"""Does the harness carry a weaker model? Turn that into a number.

The goal this serves: good SoCs even when the driving model is small. That
claim needs a measurement, so this is a fixed set of requests, the prompt the
harness really sends, and a score computed by code.

HOW IT WORKS. `system_prompt()` is `llm._SYSTEM` with the registered cores and
peripherals filled in -- the same substitution `translate_intent` does, so the
evaluation cannot drift from what the harness asks a model. A model's reply is
scored offline: `soc_from_prompt.intent_from_json` maps the JSON exactly as the
live path does, and `SocFromPrompt.plan` applies the same fail-closed gates.
No API key lives here and no provider is called; answers arrive in a file, so
Ollama, a hosted model and a subagent are scored identically.

WHAT IS SCORED, AND WHY NOT JUST "DID IT VALIDATE". Each request states the
verdict the harness must reach. For a request naming an unregistered core the
correct verdict is a REFUSAL that names the token: a model that silently drops
it would otherwise score better than one that surfaces it, which is backwards.

A right verdict for the wrong reason does not count either. The gates fail
closed in order, so a reply that adds an impossible constraint (workers with
the TDU switched off) is refused before the unregistered core is ever reached.
`correct` therefore matches only against the harness's own verdict -- its
summary, errors and repairs -- and never against the model's `unrecognized`
list, which is reported beside it for information.

Each row also carries what the verdict cost:

  json_ok          the reply was one JSON object (a failure is exactly when
                   the live harness falls back to the deterministic grammar)
  plan_ok          the harness's gates accepted the intent
  repairs          deterministic fixes the harness had to apply
  agrees_with_grammar  the model read the request the same way the regex
                   grammar did, on the material fields only

WHAT THIS DOES NOT DO. It stops at the plan: no config is written, no RTL is
generated, no flow runs. Physical gate pass rates cost hours per candidate,
and nothing here should need approval to run. If the plan-level numbers are
good, config-level validation is the next thing to add.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Request:
    """One fixed request, and the verdict the harness must reach on it."""

    id: str
    text: str
    expect: str          # "accept" | "refuse"
    because: str = ""    # substring the harness's own verdict must contain


#: The set grows when a model finds a new way to be wrong. Each request
#: carries one axis: counts and roles, a missing orchestrator, negation, an
#: unregistered core or peripheral, a sim-only core asked for tapeout, an
#: alias, a bus, a scheduler, memory.
REQUESTS: Tuple[Request, ...] = (
    Request("one-core-uart",
            "one cv32e20 with 64 KB of sram and a uart", "accept"),
    Request("workers-tdu",
            "a cv32e20 orchestrator and three serv workers woken by the TDU, "
            "32 KB sram and a uart", "accept"),
    # 32 KB, not 16: at the established 4 KB worker-image spacing a 16 KB SRAM
    # holds two worker slots, so 16 KB would test the memory limit rather than
    # the missing-orchestrator repair this request is here for. The memory
    # limit has its own request (too-many-workers).
    Request("no-orchestrator",
            "four serv harts with 32 KB of sram and a uart",
            "accept", "orchestrator"),
    Request("unregistered-core",
            "two vexriscv cores and one serv with 32 KB sram",
            "refuse", "vexriscv"),
    Request("negation-tdu",
            "a single picorv32 with no TDU, 8 KB sram, uart and gpio",
            "accept"),
    Request("bus-log",
            "two cv32e40x on the logarithmic interconnect with 64 KB sram",
            "accept"),
    Request("peripherals-three",
            "one cv32e20 with uart, spi and a timer, and 32 KB of sram",
            "accept"),
    # A deliberate no-SRAM part is NOT an axis here: the JSON schema has no
    # way to say it. `sram_zero_by_words` exists only in the grammar, so a
    # model answering sram_kb 0 is refused and one answering null is credited
    # for dropping the request. That gap is a harness item, not a model score.
    Request("sram-large",
            "one cv32e20 with 128 KB of sram and a uart", "accept"),
    Request("roles-mixed",
            "one cv32e20 titan and two fazyrv nano workers with the TDU, "
            "16 KB sram", "accept"),
    Request("sched-dynamic",
            "a cv32e20 with three serv workers, dynamic scheduling, 32 KB sram",
            "accept"),
    Request("eight-workers",
            "eight serv workers and one cv32e20 on the floonoc NoC, "
            "64 KB sram and a uart", "accept"),
    Request("unregistered-periph",
            "one cv32e20 with ethernet and a pcie root port, 32 KB sram",
            "refuse", "pcie"),
    Request("simonly-tapeout",
            "a cva6 for tapeout with 64 KB sram and a uart",
            "refuse", "SIMULATION-ONLY"),
    Request("alias-pico",
            "two pico cores with 16 KB of sram", "accept"),
    # Added 2026-09-16 after the first 14 found three faults. These carry the
    # axes the first set missed, and four of them are fields the JSON contract
    # cannot express at all, which is what they are here to measure.
    Request("counts-x-notation",
            "2x cv32e40x and 1x serv with 32 KB sram", "accept"),
    Request("boot-rom",
            "one cv32e20, 64 KB sram, 4 KB boot rom and a uart", "accept"),
    Request("scratchpad",
            "one serv with a 2048 byte scratchpad and a uart", "accept"),
    Request("isa-stated",
            "one cv32e20 rv32imc with 32 KB sram and a uart", "accept"),
    Request("target-clock",
            "four serv workers at 20 MHz with 32 KB sram and a uart",
            "accept"),
    Request("tapeout-legal",
            "a picorv32 for tapeout with 16 KB sram", "accept"),
    Request("nothing-recognised",
            "build me a fast chip please", "refuse"),
    Request("too-many-workers",
            "one cv32e20 and 16 fazyrv nano workers with 64 KB sram",
            "refuse", "boot slot"),
    Request("periph-synonyms",
            "a cv32e20 with a serial port and general purpose io, 32 KB sram",
            "accept"),
    Request("roles-without-cores",
            "two titans and three workers with 32 KB sram", "refuse"),
    Request("big-little-words",
            "a big core and four little ones with 32 KB sram", "refuse"),
    # Block A's own profile, which the contract could not express until
    # `no_sram` existed: a bare 0 is refused as a typo, deliberately.
    # No `because`: the phrase "no on-chip RAM pool" lives in the grammar's
    # provenance, not in the verdict, so requiring it would test the grammar's
    # wording rather than what the harness decided. The test asserts the
    # substance instead -- sram_kb 0 accepted, a bare 0 still refused.
    Request("no-sram-xip",
            "a cv32e20 with no sram, xip from external flash, and a uart",
            "accept"),
)

#: Compared for agreement with the grammar. Only what changes the design:
#: provenance, names and repair notes are not interpretation. The last three
#: are deliberate: the grammar reads them and the model's JSON contract has no
#: field for them, so the disagreement measures the gap.
MATERIAL: Tuple[str, ...] = ("cores", "sram_kb", "bus", "tdu", "sched_mode",
                             "peripherals", "boot_rom_kb", "scratchpad_bytes",
                             "target_clock_mhz")


def system_prompt() -> str:
    """Exactly what `translate_intent` sends: cores, peripherals and the alias
    spellings filled in by the same substitutions it uses."""
    from harness.core import VALID_CORE_IPS, VALID_PERIPHERALS
    from harness.llm import _SYSTEM, alias_hint
    from harness.skills.soc_from_prompt import CORE_ALIASES
    return (_SYSTEM.replace("{cores}", ", ".join(sorted(VALID_CORE_IPS)))
                   .replace("{periphs}", ", ".join(sorted(VALID_PERIPHERALS)))
                   .replace("{aliases}", alias_hint(CORE_ALIASES)))


def _material(intent: Dict[str, Any]) -> Dict[str, Any]:
    groups = [(g.get("ip"), int(g.get("count", 1)), g.get("role"))
              for g in intent.get("core_groups") or []]
    return {"cores": sorted(groups),
            "sram_kb": intent.get("sram_kb"),
            "bus": intent.get("bus"),
            "tdu": bool(intent.get("tdu")),
            "sched_mode": intent.get("sched_mode"),
            "peripherals": sorted(intent.get("peripherals") or []),
            "boot_rom_kb": intent.get("boot_rom_kb"),
            "scratchpad_bytes": intent.get("scratchpad_bytes"),
            "target_clock_mhz": intent.get("target_clock_mhz")}


def _verdict(request: Request, result, intent: Optional[Dict[str, Any]],
             *, json_ok: bool, reason: str = "") -> Dict[str, Any]:
    # The haystack is the HARNESS's verdict only. Including the model's own
    # `unrecognized` list here would credit a refusal that fired on a different
    # gate entirely, which is the one thing this must not do.
    haystack = (" | ".join([result.summary or ""] + list(result.errors or [])
                           + list((intent or {}).get("repairs") or []))
                if result else reason)
    correct = (json_ok and result is not None
               and ((request.expect == "accept" and result.ok)
                    or (request.expect == "refuse" and not result.ok))
               and (not request.because
                    or request.because.lower() in haystack.lower()))
    return {
        "id": request.id,
        "json_ok": json_ok,
        "would_fall_back": not json_ok,
        "plan_ok": bool(result.ok) if result else False,
        "correct": bool(correct),
        "expected": request.expect + (f" ({request.because})"
                                      if request.because else ""),
        "summary": (result.summary if result else reason),
        "plan_errors": list(result.errors or []) if result else [reason],
        "repairs": list((intent or {}).get("repairs") or []),
        "unrecognized": list((intent or {}).get("unrecognized") or []),
        "interpretation": _material(intent or {}),
    }


def grammar_row(request: Request) -> Dict[str, Any]:
    """The no-LLM baseline: the same request through the regex grammar."""
    from harness.skills.soc_from_prompt import SocFromPrompt
    result = SocFromPrompt().plan(request.text)
    row = _verdict(request, result, (result.details or {}).get("intent") or {},
                   json_ok=True)
    row["source"] = "grammar"
    return row


def score_answer(request: Request, answer: str) -> Dict[str, Any]:
    """One model reply, scored through the harness's own mapping and gates."""
    from harness.llm import _extract_json
    from harness.skills.soc_from_prompt import SocFromPrompt, intent_from_json
    try:
        raw = _extract_json(answer)
    except Exception as e:      # noqa: BLE001 - any unparsable reply
        row = _verdict(request, None, None, json_ok=False,
                       reason=f"no JSON object in the reply: {e}")
        row["source"] = "model"
        return row
    intent = intent_from_json(raw)
    result = SocFromPrompt().plan(request.text, intent=intent)
    # `plan` repaired this very object, and an early refusal returns no
    # details, so read the interpretation off the intent itself: the row
    # should say what the model asked for whichever gate fired.
    row = _verdict(request, result, asdict(intent), json_ok=True)
    row["source"] = "model"
    base_row = grammar_row(request)
    baseline = base_row["interpretation"]
    if not baseline.get("cores") and not base_row["plan_ok"]:
        # The baseline refused before building an intent (a sim-only core asked
        # for tapeout), so there is nothing to compare with: not applicable,
        # which is not the same as disagreeing.
        row["agrees_with_grammar"] = None
        row["differs_on"] = []
    else:
        row["agrees_with_grammar"] = row["interpretation"] == baseline
        row["differs_on"] = [f for f in MATERIAL
                             if row["interpretation"].get(f) != baseline.get(f)]
    # The right verdict is not the whole story: Sonnet 5 once called the only
    # core in a one-core request a worker, the harness accepted it and added an
    # orchestrator, and so the verdict was "accept" while the SoC gained a core
    # nobody asked for.
    #
    # Agreement is a comparison with the grammar, NOT with ground truth. The
    # grammar can be the one that is wrong -- it reads "eight serv workers and
    # one cv32e20" as ten harts, making the cv32e20 a worker and then adding
    # another as orchestrator -- so read `differs_on` before blaming a model.
    row["faithful"] = bool(row["correct"]
                           and row["agrees_with_grammar"] is not False)
    return row


def load_answers(path: Path) -> Dict[str, str]:
    """JSONL of {"id": ..., "answer": <the model's raw reply>}."""
    out: Dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["id"]] = rec.get("answer", "")
    return out


def summarise(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows) or 1
    # Only rows whose baseline had a reading to compare with (see score_answer):
    # counting a not-applicable row as a disagreement understates the model.
    applicable = [r for r in rows if r.get("agrees_with_grammar") is not None]

    def rate(key):
        return round(sum(bool(r.get(key)) for r in rows) / n, 3)

    return {
        "n": len(rows),
        "correct": sum(bool(r["correct"]) for r in rows),
        "correct_rate": rate("correct"),
        "json_ok_rate": rate("json_ok"),
        "fallback_rate": rate("would_fall_back"),
        "plan_ok_rate": rate("plan_ok"),
        "agreement_n": len(applicable),
        "agreement_rate": (round(sum(bool(r["agrees_with_grammar"])
                                     for r in applicable) / len(applicable), 3)
                           if applicable else None),
        # The baseline has nothing to agree with but itself, so a grammar row
        # is faithful exactly when it is correct.
        "faithful": sum(bool(r.get("faithful", r["correct"])) for r in rows),
        "faithful_rate": round(sum(bool(r.get("faithful", r["correct"]))
                                   for r in rows) / n, 3),
        "repairs_total": sum(len(r["repairs"]) for r in rows),
        "wrong": [r["id"] for r in rows if not r["correct"]],
    }


def answers_from_provider(api_cfg: Dict[str, Any],
                          only: Optional[Sequence[str]] = None
                          ) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Ask a configured provider every request, through the harness's own
    `translate_intent`. Returns (answers, failures).

    A provider or parse failure becomes an empty answer, which is exactly the
    fallback the live harness takes, and `score_answer` counts it as one. The
    message is returned separately, because "the model replied with prose" and
    "the gateway refused the request" are different findings and only one of
    them is about the model.

    The credential is read by `translate_intent` from the environment variable
    the config names. Nothing here reads or stores it.
    """
    from harness.core import VALID_CORE_IPS, VALID_PERIPHERALS
    from harness.llm import translate_intent
    from harness.skills.soc_from_prompt import CORE_ALIASES

    answers: Dict[str, str] = {}
    failures: Dict[str, str] = {}
    wanted = set(only) if only else None
    for request in REQUESTS:
        if wanted is not None and request.id not in wanted:
            continue
        try:
            raw = translate_intent(request.text, dict(api_cfg), VALID_CORE_IPS,
                                   VALID_PERIPHERALS, CORE_ALIASES)
            answers[request.id] = json.dumps(raw)
        except Exception as exc:    # noqa: BLE001 - any failure is a fallback
            answers[request.id] = ""
            failures[request.id] = f"{type(exc).__name__}: {exc}"
    return answers, failures


def run(answers: Optional[Path] = None,
        only: Optional[Sequence[str]] = None,
        replies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Score a model's answers, or the grammar baseline when given none."""
    requests = [r for r in REQUESTS if not only or r.id in set(only)]
    if replies is None and answers is None:
        rows = [grammar_row(r) for r in requests]
    else:
        if replies is None:
            replies = load_answers(answers)      # type: ignore[arg-type]
        rows = [score_answer(r, replies.get(r.id, "")) for r in requests]
    return {"summary": summarise(rows), "rows": rows}
