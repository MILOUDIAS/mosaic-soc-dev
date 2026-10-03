"""What the tools did differently, at the stage that was actually simulated.

Two real cases in this tree: two runs differing in exactly one config key, and
two independent runs that produced byte-identical netlists. Both are covered
here, because "identical" has to be said convincingly and not as an empty table.
"""
from __future__ import annotations

import json

import pytest

from harness.core import REPO_ROOT
from harness.skills.netlist_diff import (
    STAGES, TRANSPARENT, _CELL, _kind, audit, compare,
)

A = REPO_ROOT / "flow/librelane/integration/runs/blocka_padframe_def"
B = REPO_ROOT / "flow/librelane/experimental/runs/blocka_fanout16"
SAME = REPO_ROOT / "flow/librelane/integration/runs/blocka_integration"

needs_runs = pytest.mark.skipif(
    not (A / "final/pnl").is_dir(), reason="needs hardened runs")


# ── the census must agree with the flow's own count ──────────────────
@needs_runs
@pytest.mark.parametrize("run", [A, B])
def test_the_cell_census_matches_the_flows_instance_count(run):
    """The regex is only worth having if it agrees with metrics.json exactly."""
    nl = sorted((run / "final/pnl").glob("*.pnl.v"))[0]
    counted = sum(1 for _ in _CELL.finditer(nl.read_text()))
    expected = json.loads((run / "final/metrics.json").read_text())[
        "design__instance__count"]
    assert counted == expected


def test_the_regex_is_pdk_neutral():
    """Keying on a vendor prefix would bake a PDK into a module that must not
    know one -- the defect pdk-port exists to catch."""
    assert "gf180" not in _CELL.pattern
    assert "sky130" not in _CELL.pattern


def test_port_connections_are_not_counted_as_instances():
    """The bug this regex replaced: net names contain the same separator."""
    text = (" gf180mcu_fd_sc_mcu7t5v0__antenna ANTENNA_1 (.I(net1209),\n"
            "    .CLK(clknet_5_10__leaf_clk_i_regs),\n"
            "    .VDD(VDD));\n")
    found = _CELL.findall(text)
    assert len(found) == 1
    assert found[0][0].endswith("__antenna")


# ── the stage that gets compared ─────────────────────────────────────
def test_pnl_is_the_default_stage():
    """post-PnR is what becomes the GDS and what GLS simulates."""
    assert "pnl" in STAGES
    assert "place-and-route" in STAGES["pnl"][2]


def test_the_stage_is_named_in_every_result():
    @needs_runs
    def _():
        pass
    if not (A / "final/pnl").is_dir():
        pytest.skip("needs hardened runs")
    d = audit(str(A), str(B)).details
    assert d["stage"] == "pnl"
    assert d["stage_means"]


def test_an_unknown_stage_is_refused():
    with pytest.raises(ValueError, match="stage must be one of"):
        compare(A, B, stage="gds")


@needs_runs
def test_the_two_stages_are_different_netlists():
    """summarise_run globs nl/ while GLS simulates pnl/; they must not be
    conflated, which is why the stage is explicit."""
    pnl = compare(A, B, stage="pnl")
    nl = compare(A, B, stage="nl")
    if nl.a_path is None:
        pytest.skip("no post-synthesis netlist archived")
    assert pnl.a_path != nl.a_path


# ── the two real cases ───────────────────────────────────────────────
@needs_runs
def test_one_config_key_shows_up_as_buffer_resizing():
    """MAX_FANOUT_CONSTRAINT 10 -> 16: fewer, bigger buffers."""
    d = compare(A, B)
    assert not d.identical
    assert d.by_kind.get("transparent", 0) < 0, "buffering should have shrunk"
    moves = d.by_cell
    assert any("buf" in c.lower() for c in moves), moves


@needs_runs
def test_byte_identical_runs_are_reported_as_identical_not_as_zeros():
    if not (SAME / "final/pnl").is_dir():
        pytest.skip("needs the reproduction run")
    d = compare(A, SAME)
    assert d.identical
    assert d.method == "md5"
    assert any("byte-identical" in n for n in d.notes)
    assert d.a_total == d.b_total


# ── the bridge to gls-triage ─────────────────────────────────────────
def test_a_transparent_only_difference_cannot_explain_a_gls_verdict():
    from harness.skills.netlist_diff import NetlistDelta
    d = NetlistDelta(stage="pnl", by_kind={"transparent": -500})
    assert not d.oracle_can_see


def test_a_logic_difference_can():
    from harness.skills.netlist_diff import NetlistDelta
    d = NetlistDelta(stage="pnl", by_kind={"transparent": -500, "logic": 3})
    assert d.oracle_can_see


@pytest.mark.parametrize("cell,kind", [
    ("gf180mcu_fd_sc_mcu7t5v0__buf_1", "transparent"),
    ("gf180mcu_fd_sc_mcu7t5v0__clkbuf_4", "transparent"),
    ("gf180mcu_fd_sc_mcu7t5v0__fill_2", "transparent"),
    ("gf180mcu_fd_sc_mcu7t5v0__antenna", "transparent"),
    ("gf180mcu_fd_sc_mcu7t5v0__nand3_1", "logic"),
    ("gf180mcu_fd_sc_mcu7t5v0__dffq_1", "logic"),
])
def test_transparency_classification(cell, kind):
    assert _kind(cell) == kind


def test_the_transparent_set_matches_what_gls_triage_says_it_is_blind_to():
    """Both skills must use one vocabulary or they will contradict each other."""
    from harness.skills.gls_triage import TRANSPARENT_KINDS
    assert "buf" in TRANSPARENT and "buffer" in TRANSPARENT_KINDS
    assert "fill" in TRANSPARENT and "fill" in TRANSPARENT_KINDS


# ── degradation ──────────────────────────────────────────────────────
@needs_runs
def test_the_fallback_says_what_it_cannot_do():
    d = compare(A, B)
    if d.method == "najaeda":
        pytest.skip("najaeda present; fallback not exercised")
    assert any("cannot tell a rewire from a resize" in n for n in d.notes)


def test_a_missing_netlist_is_reported_not_guessed(tmp_path):
    d = compare(tmp_path / "a", tmp_path / "b")
    assert d.a_path is None
    assert d.notes and "no pnl netlist" in d.notes[0]


def test_the_skill_is_declared_read_only():
    from harness.flow_spec import Effect
    from harness.skill_policy import SKILL_SPECS
    spec = SKILL_SPECS["netlist-diff"]
    assert spec.effect is Effect.READ
    assert spec.evidence is False
