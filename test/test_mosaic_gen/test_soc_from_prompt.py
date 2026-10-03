"""Table-driven tests for the deterministic soc-from-prompt grammar.

Run from the repo root: python3 -m pytest test/test_mosaic_gen/test_soc_from_prompt.py
"""

import pathlib
import sys

import pytest

directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(directory))

from harness.skills.soc_from_prompt import (  # noqa: E402
    SocFromPrompt,
    parse_prompt,
    _repair,
)

REPO_ROOT = directory


def _groups(intent):
    return [(g["ip"], g["count"], g.get("role")) for g in intent.core_groups]


# ── core + count forms ───────────────────────────────────────────────

CASES = [
    # (prompt, expected (ip, count, role) list BEFORE repair)
    (
        "an SoC with one cv32e20 controller and two picorv32 workers",
        [("cv32e20", 1, "titan"), ("picorv32", 2, "atlas")],
    ),
    ("4x serv sensor cores", [("serv", 4, "nano")]),
    ("serv x4 and 2 fazyrv", [("serv", 4, None), ("fazyrv", 2, None)]),
    (
        "a rocket worker and a boom worker",
        [("rocket", 1, "atlas"), ("boom", 1, "atlas")],
    ),
    (
        "three qerv tiny cores under an ibex orchestrator",
        [("qerv", 3, "nano"), ("ibex", 1, "titan")],
    ),
    # alias: pico -> picorv32
    ("2 pico workers", [("picorv32", 2, "atlas")]),
    # 'small' near boom must not misparse; boom named directly
    ("one boom", [("boom", 1, None)]),
]


@pytest.mark.parametrize("prompt,expected", CASES, ids=[c[0][:40] for c in CASES])
def test_core_parsing(prompt, expected):
    intent = parse_prompt(prompt)
    assert _groups(intent) == expected


# ── other slots ──────────────────────────────────────────────────────


def test_memory_kb():
    assert parse_prompt("64kb sram").sram_kb == 64
    assert parse_prompt("64 KB of ram").sram_kb == 64


def test_memory_mb():
    assert parse_prompt("1MB memory").sram_kb == 1024


def test_boot_rom_memory():
    assert parse_prompt("8 KB boot ROM").boot_rom_kb == 8


def test_bus_floonoc():
    assert parse_prompt("connect them with a noc").bus == "floonoc"


def test_bus_log():
    assert parse_prompt("use the log interconnect").bus == "log"


def test_tdu_and_mode():
    intent = parse_prompt("with a task dispatcher, power-aware scheduling")
    assert intent.tdu is True
    assert intent.sched_mode == "power-aware"


def test_peripheral_synonyms():
    intent = parse_prompt("a serial console and some leds")
    assert "uart" in intent.peripherals
    assert "gpio" in intent.peripherals


def test_negative_peripheral_intent_is_preserved():
    intent = parse_prompt("one serv worker without uart and no peripherals")
    assert intent.peripherals_explicit
    assert intent.peripherals == []


def test_distinct_fazyrv_parameters_are_bound_per_group():
    intent = parse_prompt(
        "one fazyrv chunksize 4 worker and one fazyrv chunksize 8 worker"
    )
    assert [group["chunksize"] for group in intent.core_groups] == [4, 8]


def test_per_core_boot_address_is_not_silently_defaulted():
    intent = parse_prompt("one cv32e20 controller and one serv boot address 0x3000")
    serv = next(group for group in intent.core_groups if group["ip"] == "serv")
    assert serv["boot_addr"] == 0x3000


def test_verbose_last_core_parameters_are_not_cut_off():
    intent = parse_prompt(
        "one cv32e20 controller and one fazyrv worker configured with chunksize of 8 "
        "rv32ic boot address 0x4000"
    )
    fazyrv = next(group for group in intent.core_groups if group["ip"] == "fazyrv")
    assert fazyrv["chunksize"] == 8
    assert fazyrv["isa"] == "rv32ic"
    assert fazyrv["boot_addr"] == 0x4000


def test_harmless_conjunctions_do_not_make_plan_fail_closed():
    result = SocFromPrompt(REPO_ROOT).plan(
        "build only one cv32e20 controller but include a uart"
    )
    assert result.ok, result.errors


def test_common_architecture_wording_does_not_make_plan_fail_closed():
    result = SocFromPrompt(REPO_ROOT).plan(
        "build a full-SoC heterogeneous system with one cv32e20 controller "
        "and one serv worker with a TDU"
    )
    assert result.ok, result.errors


@pytest.mark.parametrize("field", ["SRAM", "boot ROM"])
def test_zero_memory_is_rejected_not_silently_defaulted(field):
    result = SocFromPrompt(REPO_ROOT).plan(
        f"one cv32e20 controller with 0 KB {field}"
    )
    assert not result.ok


def test_preset_escape():
    intent = parse_prompt("just give me the minimal soc")
    assert intent.preset == "minimal"
    assert intent.core_groups == []


def test_unrecognized_surfaced():
    intent = parse_prompt("one serv with a flux capacitor")
    assert "flux" in intent.unrecognized
    assert "capacitor" in intent.unrecognized


# ── repairs ──────────────────────────────────────────────────────────


def test_repair_adds_titan():
    intent = parse_prompt("two serv workers with tdu")
    _repair(intent)
    assert _groups(intent)[0] == ("cv32e20", 1, "titan")
    assert any("added 1x cv32e20 titan" in r for r in intent.repairs)


def test_repair_promotes_orchestrator_class_core():
    intent = parse_prompt("an ibex and four serv")
    _repair(intent)
    by_ip = {g["ip"]: g for g in intent.core_groups}
    assert by_ip["ibex"]["role"] == "titan"
    assert by_ip["serv"]["role"] == "atlas"


def test_repair_splits_multi_count_titan():
    intent = parse_prompt("2 cv32e20 and 2 serv")
    _repair(intent)
    groups = _groups(intent)
    assert ("cv32e20", 1, "titan") in groups
    assert ("cv32e20", 1, "atlas") in groups


def test_repair_worker_boot_addrs():
    intent = parse_prompt("one cv32e20 controller, two picorv32 workers, tdu")
    _repair(intent)
    workers = [g for g in intent.core_groups if g["role"] != "titan"]
    assert workers[0]["boot_addr"] == 0x1000


@pytest.mark.parametrize("core", ["rocket", "boom"])
def test_repair_assigns_berkeley_titan_translation_boot(core):
    intent = parse_prompt(f"one {core} controller and one serv worker with tdu")
    _repair(intent)
    titan = next(g for g in intent.core_groups if g["role"] == "titan")
    assert titan["ip"] == core
    assert titan["boot_addr"] == 0x180
    assert any("translated boot address 0x180" in item for item in intent.repairs)


# ── plan()/run() behavior ────────────────────────────────────────────


def test_plan_never_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # any accidental write would land here
    sfp = SocFromPrompt(REPO_ROOT)
    result = sfp.plan("one cv32e20 controller and two serv workers, tdu")
    assert result.ok
    assert list(tmp_path.iterdir()) == []


def test_plan_rejects_sim_only_tapeout():
    sfp = SocFromPrompt(REPO_ROOT)
    result = sfp.plan("a cva6 titan for tapeout")
    assert not result.ok
    assert any("SIMULATION-ONLY" in e for e in result.errors)


def test_plan_rejects_worker_topology_with_explicit_no_tdu():
    result = SocFromPrompt(REPO_ROOT).plan("one serv worker with no TDU")
    assert not result.ok
    assert "require the TDU" in result.summary


def test_plan_empty_prompt_fails():
    sfp = SocFromPrompt(REPO_ROOT)
    result = sfp.plan("make me something nice")
    assert not result.ok


def test_plan_rejects_material_unrecognized_clauses():
    result = SocFromPrompt(REPO_ROOT).plan("one serv with a flux capacitor")
    assert not result.ok
    assert "unrecognized" in result.errors[0]


def test_run_writes_config_without_execute(tmp_path):
    sfp = SocFromPrompt(REPO_ROOT)
    # write into configs/ then clean up: use name targeting tmp via author API
    result = sfp.run(
        "one cv32e20 controller and one serv worker, a uart",
        execute=False,
        name="pytest_prompt_soc",
    )
    try:
        assert result.ok, result.errors
        cfg_path = pathlib.Path(result.details["config"]["path"])
        assert cfg_path.exists()
        text = cfg_path.read_text()
        assert "cv32e20" in text and "serv" in text
        # the diagram of what was asked for is written before any RTL exists
        diagram = result.details["diagram"]
        assert diagram["ok"], diagram["errors"]
        assert pathlib.Path(diagram["path"]).read_text().count("<svg") == 2
    finally:
        for name in ("pytest_prompt_soc.yaml", "pytest_prompt_soc.diagram.html"):
            p = REPO_ROOT / "configs" / name
            if p.exists():
                p.unlink()


# --------------------------------------------------------------------------
# the Chipathon Block A part, from a prompt
# --------------------------------------------------------------------------

BLOCK_A_PROMPT = (
    "a tapeout SoC: one serv titan rv32ic compressed with CSRs, "
    "one serv atlas rv32i without CSRs boot 0x40010000, "
    "no sram, 128 byte scratchpad, 1 kb boot rom, no DMA, no debug, no PLIC, "
    "no multicore timer, no gpio, no rv timer, no fast interrupts, "
    "XIP from flash, uart only, TDU dynamic, at 20 MHz, "
    # Added 2026-09-10 with objectives.repair_margin_pct. Block A ships at 32
    # while B and C ship at 45, and the prompt surface has to reach every
    # field of the config it claims to reproduce -- the same reason the
    # clock is in this prompt.
    "repair margin 32"
)

# The same prompt without the clock, for the clock-grammar tests below.
BLOCK_A_PROMPT_NO_CLOCK = BLOCK_A_PROMPT.replace(", at 20 MHz", "")


@pytest.mark.parametrize("phrasing", [
    "at 25 MHz", "25MHz", "with a 25 MHz clock",
    "clocked at 25 MHz", "clock at 25 mhz",
])
def test_the_grammar_can_state_a_target_clock(tmp_path, phrasing):
    """The frequency is design intent, so the prompt has to be able to say it.

    Before this the grammar could describe the whole SoC but not the speed it
    was meant to run at, so a clock could only be set by hand-editing a
    LibreLane hardening config.
    """
    import yaml

    result = SocFromPrompt(repo_root=tmp_path).run(
        BLOCK_A_PROMPT_NO_CLOCK + ", " + phrasing, name="probe")
    assert result.ok, result.errors
    soc = yaml.safe_load(open(result.details["config"]["path"]))["soc"]
    assert soc["objectives"]["target_clock_mhz"] == 25.0


def test_a_megahertz_clock_does_not_also_request_a_timer(tmp_path):
    """"clock" means the TIMER PERIPHERAL in this grammar -- except next to MHz.

    "with a 25 MHz clock" used to set the frequency AND add a timer, and on a
    tapeout config that is rejected outright: "soc.target 'tapeout' requires
    peripherals uart". The frequency rule now swallows an adjacent "clock" into
    its consumed span so the peripheral scan never sees it.
    """
    import yaml

    result = SocFromPrompt(repo_root=tmp_path).run(
        BLOCK_A_PROMPT_NO_CLOCK + ", with a 25 MHz clock", name="probe")
    assert result.ok, result.errors
    soc = yaml.safe_load(open(result.details["config"]["path"]))["soc"]
    assert soc["peripherals"] == ["uart"], "a MHz figure must not add a timer"
    assert soc["objectives"]["target_clock_mhz"] == 25.0
    # And the bare word still does mean a timer, on a config that allows one.
    plain = SocFromPrompt(repo_root=tmp_path).run(
        "an SoC with one serv titan and a clock", name="probe2")
    assert plain.ok, plain.errors
    plain_soc = yaml.safe_load(open(plain.details["config"]["path"]))["soc"]
    assert "timer" in plain_soc["peripherals"]



def test_prompt_reproduces_the_frozen_tapeout_config(tmp_path):
    """The headline claim: the frozen Block A part is reachable from a prompt.

    Not "something like it" -- the same config, field for field. Before the
    platform-knob grammar existed a prompt could not say "no DMA" at all, so
    the part that is actually being taped out was the one design the prompt
    path could not express.
    """
    import yaml
    from harness.skills.soc_from_prompt import SocFromPrompt

    result = SocFromPrompt(repo_root=tmp_path).run(
        BLOCK_A_PROMPT, name="mosaic_tapeout_ultra"
    )
    assert result.ok, result.errors

    generated = yaml.safe_load(
        open(result.details["config"]["path"])
    )["soc"]
    frozen = yaml.safe_load(
        (REPO_ROOT / "configs" / "mosaic_tapeout_ultra.yaml").read_text()
    )["soc"]

    differing = {
        key: (generated.get(key, "<absent>"), frozen.get(key, "<absent>"))
        for key in set(generated) | set(frozen)
        if generated.get(key, "<absent>") != frozen.get(key, "<absent>")
    }
    assert not differing, f"prompt output diverged from the frozen config: {differing}"


def test_prompt_cannot_talk_its_way_past_the_tapeout_gate(tmp_path):
    """`tapeout` in a prompt is a CLAIM; the capability gate decides.

    Same Block A prompt with the debug module left in. The design is otherwise
    identical, so nothing but the gate stops it being labelled tapeout-ready.
    """
    from harness.skills.soc_from_prompt import SocFromPrompt

    result = SocFromPrompt(repo_root=tmp_path).run(
        BLOCK_A_PROMPT.replace("no debug, ", ""), name="probe"
    )
    assert not result.ok
    assert any("soc.debug" in e for e in result.errors), result.errors


def test_xip_does_not_bring_back_the_spi_host(tmp_path):
    """"XIP from flash" must not add a `spi` peripheral.

    The flash interface at xip_only is the always-on subsystem reduced to its
    reader; a `spi` user peripheral is the full OpenTitan host, 0.717 mm2 of it
    -- exactly what the prompt asked to remove.
    """
    from harness.skills.soc_from_prompt import parse_prompt

    intent = parse_prompt(BLOCK_A_PROMPT)
    assert intent.peripherals == ["uart"], intent.peripherals
    assert intent.platform["spi_mode"] == "xip_only"


def test_platform_denials_do_not_leak_into_peripherals():
    """"no rv timer" must not contribute a `timer` peripheral."""
    from harness.skills.soc_from_prompt import parse_prompt

    intent = parse_prompt("one serv titan, no rv timer, no multicore timer, uart")
    assert "timer" not in intent.peripherals, intent.peripherals


def test_no_sram_is_a_profile_not_an_error():
    """sram_kb 0 is the external-memory-only part, not a typo."""
    from harness.skills.soc_from_prompt import parse_prompt

    assert parse_prompt("one serv titan, no sram, uart").sram_kb == 0
