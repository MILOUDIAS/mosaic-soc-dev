"""The PPA objective: what an automatic improvement loop may optimise.

Every physical decision so far ended with a person reading two tables. These
tests pin the three things that had to become code before a loop could do it:
which runs count at all (gates), when one is better than another
(comparison) -- including when the question is refused -- and which setting,
if any, the difference can be blamed on (the ledger).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from harness.core import REPO_ROOT
from harness.physical.ppa import PPA, Gate, compare, evaluate, knobs, ledger
from harness.physical.report import HARD_CHECKS

RUNS = REPO_ROOT / "flow/librelane/experimental/runs"


def have(*tags):
    return all((RUNS / t / "final/metrics.json").is_file()
               and (RUNS / t / "resolved.json").is_file() for t in tags)


def _ppa(**kw):
    base: Dict[str, Any] = dict(run="r", design="d", pdk="p",
                                gates=(Gate("g", True, ""),),
                                logic_um2=1000.0, power_w=0.10,
                                power_basis="default-activity",
                                clock_period_ns=100.0, setup_ws_ns=20.0,
                                hold_ws_ns=0.1, fmax_mhz=12.5,
                                max_fanout_violations=0.0, gls="pass",
                                energy_nj=10.0, die_mm2=1.5)
    base.update(kw)
    return PPA(**base)


CLEAN = {**{k: 0 for k in HARD_CHECKS},
         "timing__setup__ws": 20.0, "timing__hold__ws": 0.1,
         "design__max_slew_violation__count": 0,
         "design__max_cap_violation__count": 0,
         "design__instance__area__class:stdcell": 1000.0,
         # um2, as LibreLane reports it; signoff_summary converts to mm2. A
         # 1.5 here would round to a 0.0 mm2 die and refuse every comparison.
         "design__die__area": 1_500_000, "power__total": 0.1}


def _run(tmp_path, metrics, name="run", **resolved):
    d = tmp_path / name
    (d / "final").mkdir(parents=True)
    (d / "final/metrics.json").write_text(json.dumps(metrics))
    (d / "resolved.json").write_text(json.dumps(
        {"DESIGN_NAME": "d", "PDK": "p", "CLOCK_PERIOD": 100.0, **resolved}))
    return d


# ── gates ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not have("blocka_1110_ndr", "blockb_m45", "blockc_m45",
                             "blocka_m45"), reason="run tree not present")
def test_the_gates_reproduce_the_block_a_margin_decision():
    """Made by hand on 2026-09-10 from five runs and a lot of prose. The
    gates reach it on their own: every shipping configuration accepted, and
    Block A at margin 45 rejected on exactly the two metrics that decided it."""
    for tag in ("blocka_1110_ndr", "blockb_m45", "blockc_m45"):
        p, _ = evaluate(RUNS / tag)
        assert p.accepted, (tag, p.failing)
    a45, _ = evaluate(RUNS / "blocka_m45")
    assert not a45.accepted
    assert a45.failing == ["max-slew", "max-cap"]


def test_a_missing_metric_fails_its_gate(tmp_path):
    """Not measured is not passed -- the GLS NOT_RUN lesson, applied here."""
    m = dict(CLEAN)
    del m["timing__setup__ws"]
    p, _ = evaluate(_run(tmp_path, m), repo_root=tmp_path)
    assert not p.accepted and "setup-slack" in p.failing
    gate = next(g for g in p.gates if g.name == "setup-slack")
    assert gate.detail == "not measured"


def test_a_missing_hard_check_is_not_a_pass(tmp_path):
    """signoff_summary only collects keys that are present, so an empty dict
    of hard checks would otherwise read as 'all zero'."""
    m = dict(CLEAN)
    del m[HARD_CHECKS[0]]
    p, _ = evaluate(_run(tmp_path, m), repo_root=tmp_path)
    assert "signoff-hard-checks" in p.failing


def test_a_clean_run_is_accepted_and_fmax_follows_the_slack(tmp_path):
    p, _ = evaluate(_run(tmp_path, CLEAN), repo_root=tmp_path)
    assert p.accepted, p.failing
    # period 100 ns, worst setup slack +20 ns -> an 80 ns achievable period
    assert p.fmax_mhz == pytest.approx(12.5)
    # 0.1 W for one 100 ns cycle is 10 nJ
    assert p.energy_nj == pytest.approx(10.0)


def test_negative_slack_and_violations_are_rejected(tmp_path):
    for key, bad in (("timing__hold__ws", -0.01),
                     ("design__max_cap_violation__count", 1)):
        m = {**CLEAN, key: bad}
        p, _ = evaluate(_run(tmp_path, m, name=key), repo_root=tmp_path)
        assert not p.accepted, key


# ── comparison ───────────────────────────────────────────────────────
def test_better_on_every_axis_dominates():
    base = _ppa()
    cand = _ppa(logic_um2=900.0, power_w=0.09, energy_nj=9.0, fmax_mhz=13.0,
                die_mm2=1.4)
    r, why = compare(cand, base)
    assert r and r["dominates"] and not r["dominated"] and r["score"] > 0


def test_a_smaller_die_is_an_improvement_even_when_the_cells_grow():
    """What a floorplan knob produces, and what logic area alone called a
    regression. Measured on two Block B runs differing only in density (74.5%
    vs 79.6% achieved): the denser one has a 5.7% smaller die and 0.53% more
    cell area, because a tighter die needs more repair buffering. Dominance
    must call that a trade-off and the score must not be negative."""
    loose = _ppa(die_mm2=1.5916, logic_um2=1_127_031.0, energy_nj=8.653)
    dense = _ppa(die_mm2=1.5004, logic_um2=1_132_987.0, energy_nj=8.983)
    r, why = compare(dense, loose)
    assert r, why
    assert r["better_on"] == ["die_mm2"] and "logic_um2" in r["worse_on"]
    assert not r["dominates"] and r["score"] > 0
    assert r["decided_by"] == "die_mm2"


def test_naming_one_weight_does_not_silence_the_others():
    """`--weight die_mm2=3` means "weigh the die higher", not "stop counting
    everything else". Replacing the table would have made a density walk
    optimise the die alone, with the cell area and energy it pays for
    invisible -- and the energy-proxy caveat unable to fire."""
    cand = _ppa(die_mm2=1.4, logic_um2=1100.0, energy_nj=12.0)
    r, why = compare(cand, _ppa(), {"die_mm2": 3.0})
    assert r, why
    assert r["weights"] == {"die_mm2": 3.0, "logic_um2": 1.0,
                            "energy_nj": 1.0, "fmax_mhz": 1.0}
    # the costs still register: a 10% worse cell area and 20% worse energy
    assert set(r["worse_on"]) == {"logic_um2", "energy_nj"}


def test_a_mandated_die_contributes_nothing_rather_than_refusing():
    """Block A's die is fixed by the Chipathon slot, so every run of it has the
    same die area. That must be a zero-gain term, not a refusal."""
    r, why = compare(_ppa(logic_um2=900.0), _ppa())
    assert r, why
    assert r["gains"]["die_mm2"] == 0.0 and "die_mm2" not in r["worse_on"]


def test_a_rejected_run_is_never_ranked():
    """A cheaper broken design is not an improvement."""
    bad = _ppa(logic_um2=1.0, gates=(Gate("max-slew", False, "45"),))
    r, why = compare(bad, _ppa())
    assert r is None and any("only accepted runs" in w for w in why)


@pytest.mark.parametrize("field,value", [
    ("design", "other"), ("pdk", "ihp-sg13g2"), ("power_basis", "workload")])
def test_confounded_comparisons_are_refused(field, value):
    r, why = compare(_ppa(**{field: value}), _ppa())
    assert r is None and why


def test_a_proxy_that_decides_the_score_is_named():
    """The first real comparison was decided by default-toggle power."""
    cand = _ppa(logic_um2=1002.0, power_w=0.09, energy_nj=9.0, fmax_mhz=12.54)
    r, _ = compare(cand, _ppa())
    assert r["decided_by"] == "energy_nj"
    assert any("energy term decides this score" in c for c in r["caveats"])


@pytest.mark.skipif(not have("blockb_m45", "blockb_sdc"),
                    reason="run tree not present")
def test_block_b_margin_45_is_a_trade_off_not_a_win():
    """Area got worse (+0.24%) while power and fmax improved. Dominance says
    'trade-off'; the score alone would have said 'better'. And it is not a
    margin measurement at all: the antenna iteration cap and the RTL bundle
    moved with the margin, so no cause is named."""
    cand, _ = evaluate(RUNS / "blockb_m45")
    base, _ = evaluate(RUNS / "blockb_sdc")
    r, why = compare(cand, base)
    assert r, why
    assert not r["dominates"] and "logic_um2" in r["worse_on"]
    assert any("energy term decides" in c for c in r["caveats"])
    assert r["attribution"] is None
    assert set(r["moved"]) == {"GRT_DESIGN_REPAIR_MAX_SLEW_PCT",
                               "DRT_ANTENNA_REPAIR_ITERS", "rtl_bundle"}


@pytest.mark.skipif(not have("blocka_25mhz", "blocka_sdc"),
                    reason="run tree not present")
def test_a_faster_clock_is_not_scored_worse_for_drawing_more_power():
    """The one single-variable clock pair on disk. Scored on power, 100 ns
    beat 40 ns by +2.8%: power rises with the clock, so a power objective
    rewards slowing the chip down. Per cycle the two spend the same energy
    to within 0.04%, and the 40 ns run is twice as fast."""
    fast, _ = evaluate(RUNS / "blocka_25mhz")
    slow, _ = evaluate(RUNS / "blocka_sdc")
    r, why = compare(fast, slow)
    assert r, why
    assert r["attribution"] == "CLOCK_PERIOD"
    # The claim is about WHICH axis wins, not the size of a mean over however
    # many objectives there happen to be: the same clock on the same die pays
    # equal energy per cycle and runs twice as fast.
    assert r["score"] > 0 and r["decided_by"] == "fmax_mhz"
    assert "fmax_mhz" in r["better_on"]
    assert abs(r["gains"]["energy_nj"]) < 0.0004
    assert r["gains"]["die_mm2"] == 0.0


# ── which setting made the difference ───────────────────────────────
def test_checker_threads_and_machine_paths_are_not_settings():
    """KLAYOUT_DRC_THREADS moved between the Block A margin runs and changes
    nothing a run produces. VERILOG_FILES moved between every pair because it
    names the bundle's absolute path: the bundle is the setting, the path is
    not. The router's thread count stays -- nobody has shown it is inert."""
    k = knobs({"KLAYOUT_DRC_THREADS": 2, "STA_THREADS": 4, "DRT_THREADS": 8,
               "PDK_ROOT": "/pdk", "GRT_DESIGN_REPAIR_MAX_SLEW_PCT": 45,
               "VERILOG_FILES": ["/r/build/mosaic/blk_b-0123456789ab/src/a.sv"]})
    assert k == {"DRT_THREADS": 8, "GRT_DESIGN_REPAIR_MAX_SLEW_PCT": 45,
                 "rtl_bundle": "blk_b-0123456789ab"}


S = {"GRT_DESIGN_REPAIR_MAX_SLEW_PCT": 32, "DRT_ANTENNA_REPAIR_ITERS": 3,
     "rtl_bundle": "b-1"}


def test_one_moved_setting_is_the_attribution():
    r, _ = compare(_ppa(knobs={**S, "GRT_DESIGN_REPAIR_MAX_SLEW_PCT": 45}),
                   _ppa(knobs=S))
    assert r["attribution"] == "GRT_DESIGN_REPAIR_MAX_SLEW_PCT"
    assert r["moved"] == {"GRT_DESIGN_REPAIR_MAX_SLEW_PCT": [32, 45]}


def test_a_die_and_its_core_area_are_one_decision():
    """A floorplan is sized absolutely, so DIE_AREA and CORE_AREA move together
    or not at all: two keys, one choice. Counting keys instead of decisions made
    every density experiment unattributable and invisible to the ledger."""
    loose = {"DIE_AREA": [0, 0, 1505.26, 1505.26],
             "CORE_AREA": [16, 16, 1489.26, 1489.26], "rtl_bundle": "c-1"}
    dense = {"DIE_AREA": [0, 0, 1472.39, 1472.39],
             "CORE_AREA": [16, 16, 1456.39, 1456.39], "rtl_bundle": "c-1"}
    r, why = compare(_ppa(knobs=dense, die_mm2=2.1679),
                     _ppa(knobs=loose, die_mm2=2.2658))
    assert r, why
    assert r["attribution"] == "floorplan"
    assert sorted(r["moved"]) == ["CORE_AREA", "DIE_AREA"]
    # ... and a die that moved WITHOUT its core area is not that decision
    half = {**dense, "CORE_AREA": loose["CORE_AREA"]}
    r2, _ = compare(_ppa(knobs=half), _ppa(knobs=loose))
    assert r2["attribution"] == "DIE_AREA"


def test_the_ledger_records_a_grouped_experiment(tmp_path):
    """The density pair must appear as an experiment, or step 2's history has a
    blind spot exactly where the floorplan knob operates."""
    _run(tmp_path, CLEAN, "loose", DIE_AREA=[0, 0, 1505.26, 1505.26],
         CORE_AREA=[16, 16, 1489.26, 1489.26])
    _run(tmp_path, CLEAN, "dense", DIE_AREA=[0, 0, 1472.39, 1472.39],
         CORE_AREA=[16, 16, 1456.39, 1456.39])
    runs, exps = ledger(tmp_path, repo_root=tmp_path)
    assert len(exps) == 1, exps
    assert exps[0]["setting"] == "floorplan"
    assert exps[0]["keys"] == ["CORE_AREA", "DIE_AREA"]
    assert exps[0]["values"][0] == [0, 0, 1472.39, 1472.39]


def test_two_moved_settings_are_attributed_to_neither():
    """The +2.1% error: a two-variable comparison read as a one-variable one.
    The two points are still ranked; the difference is explained by nothing."""
    cand = _ppa(knobs={**S, "GRT_DESIGN_REPAIR_MAX_SLEW_PCT": 45,
                       "DRT_ANTENNA_REPAIR_ITERS": 8})
    r, _ = compare(cand, _ppa(knobs=S))
    assert r is not None and r["attribution"] is None
    assert any("2 settings moved" in c for c in r["caveats"])


def test_identical_settings_are_a_repeat():
    r, _ = compare(_ppa(knobs=S, logic_um2=1001.0), _ppa(knobs=S))
    assert r["moved"] == {} and r["attribution"] is None
    assert any("repeat" in c for c in r["caveats"])


def test_unknown_settings_are_not_identical_settings():
    r, _ = compare(_ppa(knobs=None), _ppa(knobs=S))
    assert r["moved"] is None and r["attribution"] is None


# ── the ledger ───────────────────────────────────────────────────────
def test_the_ledger_pairs_runs_that_differ_in_exactly_one_setting(tmp_path):
    _run(tmp_path, CLEAN, "base")
    _run(tmp_path, CLEAN, "one", GRT_DESIGN_REPAIR_MAX_SLEW_PCT=45)
    _run(tmp_path, CLEAN, "two", DRT_ANTENNA_REPAIR_ITERS=8,
         CLOCK_PERIOD=50.0, KLAYOUT_DRC_THREADS=2)
    (tmp_path / "unfinished").mkdir()
    (tmp_path / "unfinished/resolved.json").write_text("{}")
    runs, exps = ledger(tmp_path, repo_root=tmp_path)
    assert sorted(Path(r.run).name for r in runs) == ["base", "one", "two"]
    assert len(exps) == 1, exps
    assert exps[0]["setting"] == "GRT_DESIGN_REPAIR_MAX_SLEW_PCT"
    assert exps[0]["values"] == [None, 45]
    assert exps[0]["accepted"] == [True, True] and exps[0]["gains"]


@pytest.mark.skipif(not have("blockc_ant8", "blockc_sdc", "blocka_1110_ndr",
                             "blocka_m45_ndr"), reason="run tree not present")
def test_the_ledger_finds_only_the_experiments_actually_run():
    """The Block C SDC pair is a real experiment: the signoff SDC alone turned
    a max-slew rejection into an accepted run -- the per-corner liberty limit
    finding, measured cleanly. The Block A margin pair the shipping decision
    rests on is not one: the cap margin moved with the slew margin, among
    others. The shipped run passes its gates either way; the causal story
    does not survive."""
    runs, exps = ledger(RUNS)
    pairs = {tuple(Path(x).name for x in e["runs"]): e for e in exps}
    sdc = pairs[("blockc_ant8", "blockc_sdc")]
    assert sdc["setting"] == "SIGNOFF_SDC_FILE"
    assert sdc["accepted"] == [False, True]
    assert ("blocka_1110_ndr", "blocka_m45_ndr") not in pairs
    a32, a45 = (next(r for r in runs if Path(r.run).name == t)
                for t in ("blocka_1110_ndr", "blocka_m45_ndr"))
    moved = {k for k in set(a32.knobs) | set(a45.knobs)
             if a32.knobs.get(k) != a45.knobs.get(k)}
    assert {"GRT_DESIGN_REPAIR_MAX_SLEW_PCT",
            "GRT_DESIGN_REPAIR_MAX_CAP_PCT"} <= moved
