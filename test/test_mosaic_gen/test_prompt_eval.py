"""The small-model evaluation: is the harness's claim measurable?

What these pin is the SCORER, not a model. A model that silently drops a core
it does not know must score worse than one that surfaces it, the prompt under
test must be the prompt the harness really sends, and an unparsable reply must
count as the grammar fallback it causes in the live path.
"""
from __future__ import annotations

import json

import pytest

from harness.prompt_eval import (MATERIAL, REQUESTS, grammar_row, load_answers,
                                 run, score_answer, summarise, system_prompt)

BY_ID = {r.id: r for r in REQUESTS}


def answer(**fields) -> str:
    return json.dumps(fields)


# ── the prompt under test is the harness's own ───────────────────────
def test_the_eval_prompt_is_what_the_harness_sends():
    from harness.core import VALID_CORE_IPS, VALID_PERIPHERALS
    from harness.llm import _SYSTEM, alias_hint
    from harness.skills.soc_from_prompt import CORE_ALIASES
    text = system_prompt()
    for placeholder in ("{cores}", "{periphs}", "{aliases}"):
        assert placeholder not in text, placeholder
    assert text == (_SYSTEM.replace("{cores}", ", ".join(sorted(VALID_CORE_IPS)))
                           .replace("{periphs}",
                                    ", ".join(sorted(VALID_PERIPHERALS)))
                           .replace("{aliases}", alias_hint(CORE_ALIASES)))
    # Rendered FROM CORE_ALIASES, so the contract cannot drift from the
    # spellings the grammar accepts: "two pico cores" was refused by a model
    # told only the canonical names.
    assert "pico" in text and "picorv32" in text
    for core in ("cv32e20", "serv", "fazyrv"):
        assert core in text
    assert "vexriscv" not in text      # the unregistered-core probe


def test_every_request_states_the_verdict_it_expects():
    assert len(REQUESTS) >= 12
    assert len({r.id for r in REQUESTS}) == len(REQUESTS)
    for r in REQUESTS:
        assert r.expect in ("accept", "refuse"), r.id
        assert r.text.strip() and not r.text.endswith(".")


# ── the scorer ───────────────────────────────────────────────────────
def test_a_reply_that_is_not_json_counts_as_a_fallback():
    """Exactly what makes the live harness drop to the regex grammar."""
    row = score_answer(BY_ID["one-core-uart"], "Sure! I'd suggest a cv32e20.")
    assert not row["json_ok"] and row["would_fall_back"]
    assert not row["correct"]


def test_silently_dropping_an_unknown_core_is_not_correct():
    """The failure mode a naive 'did it validate' score would reward: the
    model keeps the core it knows and never mentions vexriscv."""
    row = score_answer(BY_ID["unregistered-core"], answer(
        cores=[{"ip": "serv", "count": 1, "role": "titan"}], sram_kb=32,
        peripherals=[], tdu=False))
    assert row["json_ok"] and not row["correct"]


def test_surfacing_an_unknown_core_is_correct():
    row = score_answer(BY_ID["unregistered-core"], answer(
        cores=[{"ip": "serv", "count": 1}], sram_kb=32, peripherals=[],
        unrecognized=["vexriscv"]))
    assert row["json_ok"] and not row["plan_ok"]     # the harness refuses
    assert row["correct"] and "vexriscv" in row["unrecognized"]
    assert any("vexriscv" in e for e in row["plan_errors"])


def test_a_refusal_for_the_wrong_reason_is_not_correct():
    """The reply switches the TDU off for a worker topology, so the harness
    refuses on that conflict and never reaches the unregistered core. Right
    verdict, wrong reason: it must not count, and the model's own token list
    must not be what makes it count."""
    row = score_answer(BY_ID["unregistered-core"], answer(
        cores=[{"ip": "serv", "count": 1, "role": "nano"}], sram_kb=32,
        peripherals=[], tdu=False, unrecognized=["vexriscv"]))
    assert not row["plan_ok"] and not row["correct"]
    assert "vexriscv" in row["unrecognized"]          # still reported
    assert "TDU" in row["summary"]


def test_inventing_an_orchestrator_is_not_what_the_prompt_asks():
    """The harness adds the orchestrator itself and reports the repair, so a
    correct answer for four workers names no titan and still ends accepted."""
    row = score_answer(BY_ID["no-orchestrator"], answer(
        cores=[{"ip": "serv", "count": 4, "role": "nano"}], sram_kb=32,
        peripherals=["uart"], tdu=True))
    assert row["correct"], row
    assert any("orchestrator" in r.lower() for r in row["repairs"])


def test_a_plain_good_answer_is_correct_and_agrees_with_the_grammar():
    row = score_answer(BY_ID["one-core-uart"], answer(
        cores=[{"ip": "cv32e20", "count": 1, "role": "titan"}], sram_kb=64,
        peripherals=["uart"], tdu=False, bus="obi"))
    assert row["correct"] and row["plan_ok"]
    assert row["interpretation"]["cores"] == [("cv32e20", 1, "titan")]
    assert row["differs_on"] in ([], ["bus"])      # bus wording is not material


def test_the_summary_counts_what_the_claim_needs():
    rows = [
        score_answer(BY_ID["one-core-uart"], answer(
            cores=[{"ip": "cv32e20", "count": 1, "role": "titan"}],
            sram_kb=64, peripherals=["uart"], tdu=False)),
        score_answer(BY_ID["one-core-uart"], "not json at all"),
    ]
    s = summarise(rows)
    assert s["n"] == 2 and s["correct"] == 1
    assert s["json_ok_rate"] == 0.5 and s["fallback_rate"] == 0.5
    assert s["wrong"] == ["one-core-uart"]


def test_answers_are_loaded_by_id(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(json.dumps({"id": "one-core-uart", "answer": "{}"}) + "\n"
                 + json.dumps({"id": "bus-log", "answer": "{}"}) + "\n")
    assert set(load_answers(p)) == {"one-core-uart", "bus-log"}


# ── the baseline ─────────────────────────────────────────────────────
def test_the_grammar_baseline_runs_and_is_reported_not_assumed():
    out = run(only=["one-core-uart", "unregistered-core", "simonly-tapeout"])
    assert out["summary"]["n"] == 3
    assert all(r["source"] == "grammar" for r in out["rows"])
    # the two probes the grammar must get right, whatever a model does
    by_id = {r["id"]: r for r in out["rows"]}
    assert by_id["one-core-uart"]["correct"]
    assert not by_id["unregistered-core"]["plan_ok"]


def test_workers_with_no_stated_tdu_get_one_rather_than_never_running():
    """Also found by this eval: "16 fazyrv nano workers" was accepted with the
    TDU off and no boot slots, so every worker was a hart nothing could start.
    Silence now gets the dispatcher and says so; an explicit "no TDU" beside
    workers is still the contradiction plan() refuses."""
    from harness.skills.soc_from_prompt import _repair, parse_prompt
    intent = parse_prompt("one cv32e20 and four serv workers with 32 KB sram")
    _repair(intent)
    assert intent.tdu is True
    assert any("wake themselves" in r for r in intent.repairs)
    workers = [g for g in intent.core_groups if g["role"] != "titan"]
    assert workers and all("boot_addr" in g for g in workers)


def test_the_grammar_reads_a_worker_list_without_inventing_a_hart():
    """Found by the model and the grammar disagreeing. "eight serv workers and
    one cv32e20" read as TEN harts: "workers" leaked across "and" onto the
    cv32e20, so the repair demoted it and added a second cv32e20 as
    orchestrator. Nine harts, exactly one of them the titan."""
    row = grammar_row(BY_ID["eight-workers"])
    assert row["correct"], row["summary"]
    cores = row["interpretation"]["cores"]
    assert sum(c[1] for c in cores) == 9, cores
    assert [c for c in cores if c[2] == "titan"] == [("cv32e20", 1, "titan")]


def test_agreement_is_not_applicable_when_the_baseline_refuses_early():
    """The grammar refuses a sim-only core for tapeout before it builds an
    intent, so it has no reading to compare with. A model that reached the
    same refusal must not be marked as disagreeing with an empty reading."""
    row = score_answer(BY_ID["simonly-tapeout"], answer(
        cores=[{"ip": "cva6", "count": 1}], sram_kb=64, peripherals=["uart"],
        tdu=None))
    assert row["correct"] and row["agrees_with_grammar"] is None
    assert row["differs_on"] == [] and row["faithful"]


@pytest.mark.parametrize("field", MATERIAL)
def test_material_fields_are_read_from_an_intent(field):
    row = grammar_row(BY_ID["workers-tdu"])
    assert field in row["interpretation"]


# ── what the pilot found, and the contract that answers it ──────────
def test_the_contract_lets_a_model_leave_the_tdu_unstated():
    """Pilot finding (Sonnet 5 medium, 2026-09-13): with `"tdu": bool` the
    model had to guess, guessed false beside worker roles, and the harness
    refused an impossible design on 2 of 6 requests. Null is the honest
    answer, and it costs the model nothing to give."""
    assert '"tdu": bool|null' in system_prompt()
    row = score_answer(BY_ID["no-orchestrator"], answer(
        cores=[{"ip": "serv", "count": 4, "role": "atlas"}], sram_kb=32,
        peripherals=["uart"], tdu=None))
    assert row["correct"], row["summary"]
    assert any("orchestrator" in r.lower() for r in row["repairs"])


def test_the_contract_asks_for_no_role_it_was_not_given():
    """Pilot v2 (2026-09-13): once "tdu" could be null, the model started
    calling the only core in a one-core request a worker, so the harness added
    an orchestrator nobody asked for. Right verdict, wrong SoC -- which is why
    `faithful` exists and why roles are the harness's to assign."""
    text = system_prompt()
    assert '"role"?' in text and "omit" in text.lower()
    row = score_answer(BY_ID["one-core-uart"], answer(
        cores=[{"ip": "cv32e20", "count": 1}], sram_kb=64,
        peripherals=["uart"], tdu=None))
    assert row["faithful"], row
    assert row["interpretation"]["cores"] == [("cv32e20", 1, "titan")]


def test_the_contract_carries_every_field_the_grammar_reads():
    """Measured gap: the grammar reads a boot ROM size, a scratchpad, an ISA
    and a clock target, and the contract had a field for none of them. A model
    dropped all four, and one run folded "a 2048 byte scratchpad" into
    `sram_kb: 2` -- a corrupted memory spec the boot-slot gate then refused."""
    text = system_prompt()
    for field in ("boot_rom_kb", "scratchpad_bytes", "target_clock_mhz",
                  "isa"):
        assert field in text, field
    row = score_answer(BY_ID["scratchpad"], answer(
        cores=[{"ip": "serv", "count": 1}], sram_kb=None,
        scratchpad_bytes=2048, peripherals=["uart"], tdu=None))
    assert row["correct"], row["summary"]
    assert row["interpretation"]["scratchpad_bytes"] == 2048


def test_the_contract_can_say_a_part_has_no_on_chip_ram():
    """Block A's shipped profile: no SRAM pool, XIP from flash. The grammar
    hears that decision in words, and a bare 0 is refused as a typo, so
    without a field of its own a model could not express the design at all."""
    assert '"no_sram"' in system_prompt()
    row = score_answer(BY_ID["no-sram-xip"], answer(
        cores=[{"ip": "cv32e20", "count": 1}], sram_kb=0, no_sram=True,
        peripherals=["uart"], tdu=None))
    assert row["correct"], row["summary"]
    assert row["interpretation"]["sram_kb"] == 0
    # ... and a zero without the flag is still the typo it always was
    bare_zero = score_answer(BY_ID["no-sram-xip"], answer(
        cores=[{"ip": "cv32e20", "count": 1}], sram_kb=0,
        peripherals=["uart"], tdu=None))
    assert not bare_zero["plan_ok"]


def test_the_contract_forbids_inventing_cores():
    """"a big core and four little ones" made the model choose a cv32e40x and
    four serv; the grammar refuses, because neither is named."""
    assert "invent cores" in system_prompt().lower()


def test_a_stated_no_tdu_still_refuses_worker_roles():
    """The nullable field must not weaken the gate: false stated beside
    workers is the impossible design it always was."""
    row = score_answer(BY_ID["no-orchestrator"], answer(
        cores=[{"ip": "serv", "count": 4, "role": "atlas"}], sram_kb=32,
        peripherals=["uart"], tdu=False))
    assert not row["plan_ok"] and "TDU" in row["summary"]


def test_one_core_and_no_tdu_is_that_core_as_the_orchestrator():
    """The grammar used to add a cv32e20 here, demoting the only core the
    request named to a worker the disabled TDU could never wake."""
    row = grammar_row(BY_ID["negation-tdu"])
    assert row["correct"], row["summary"]
    assert row["interpretation"]["cores"] == [("picorv32", 1, "titan")]
    assert not row["interpretation"]["tdu"]
