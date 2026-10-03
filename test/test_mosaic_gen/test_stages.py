"""Cheap stages may prune a candidate; only signoff decides it.

A stage that prunes wrongly throws away a design that would have passed, and
nothing downstream ever sees it again. So these tests check the PREMISE each
rule rests on against every run on disk, not only the rule's output: a new
run that breaks a premise fails here before an optimizer trusts the rule.
"""
from __future__ import annotations

import json

import pytest

from harness.core import REPO_ROOT
from harness.physical.ppa import evaluate
from harness.physical.routability import ROUTABILITY_OBSERVATIONS
from harness.physical.stages import (logic_area_band, post_grt_metrics,
                                     screen_model, screen_post_grt,
                                     synthesised_area)

RUNS = REPO_ROOT / "flow/librelane/experimental/runs"
FINISHED = (sorted(d for d in RUNS.iterdir()
                   if (d / "final/metrics.json").is_file()
                   and (d / "resolved.json").is_file())
            if RUNS.is_dir() else [])
needs_runs = pytest.mark.skipif(not FINISHED, reason="run tree not present")


def _gf180(run):
    r = json.loads((run / "resolved.json").read_text())
    return str(r.get("PDK", "")).startswith("gf180")


# ── premises, re-checked on every run present ───────────────────────
@needs_runs
def test_slack_never_improves_after_global_routing():
    """The post-grt screen's premise: signoff slack is at or below the last
    STA before detailed routing. 22 of 22 runs when the screen was written."""
    checked = 0
    for d in FINISHED:
        step, mid = post_grt_metrics(d)
        if not mid:
            continue
        p, _ = evaluate(d)
        for key, final in (("timing__setup__ws", p.setup_ws_ns),
                           ("timing__hold__ws", p.hold_ws_ns)):
            if key in mid and final is not None:
                assert final <= mid[key] + 1e-6, (d.name, step, key)
                checked += 1
    assert checked >= 2 * 20


@needs_runs
def test_no_run_that_passed_signoff_would_have_been_pruned():
    for d in FINISHED:
        p, _ = evaluate(d)
        if p.accepted:
            assert screen_post_grt(d).keep, d.name
    for o in ROUTABILITY_OBSERVATIONS:
        if o.routed:
            assert screen_model(o.serv_harts, o.target_utilisation).keep, o.run_tag


@needs_runs
def test_pre_layout_timing_is_no_predictor():
    """Why synthesis has no timing screen: every run that shipped had negative
    slack before placement, so such a screen would have discarded all of them."""
    shipped = 0
    for d in FINISHED:
        p, _ = evaluate(d)
        pre = sorted(d.glob("*-openroad-staprepnr"))
        if p.accepted and pre:
            ws = json.loads((pre[0] / "state_out.json").read_text())[
                "metrics"]["timing__setup__ws"]
            assert ws < 0 <= p.setup_ws_ns, d.name
            shipped += 1
    assert shipped


@needs_runs
def test_synthesis_predicts_final_logic_area_within_the_band():
    checked = 0
    for d in FINISHED:
        s, (p, _) = synthesised_area(d), evaluate(d)
        if s and p.logic_um2 and _gf180(d):
            lo, hi = logic_area_band(s)
            assert lo <= p.logic_um2 <= hi, (d.name, p.logic_um2 / s)
            checked += 1
    assert checked


SAME_SYNTHESIS = ("blocka_1110_25_ndr", "blocka_1110_ndr", "blocka_25mhz",
                  "blocka_libtran", "blocka_m45", "blocka_m45_ndr",
                  "blocka_reharden", "blocka_sdc", "blocka_signoff",
                  "blocka_slew32", "blocka_slewonly")


@pytest.mark.skipif(not all((RUNS / t / "resolved.json").is_file()
                            for t in SAME_SYNTHESIS),
                    reason="run tree not present")
def test_synthesis_does_not_see_the_physical_knobs():
    """Eleven Block A runs, three clock periods, three slew margins, NDR on
    and off: one synthesised area. So synthesis ranks RTL, never knob settings."""
    resolved = [json.loads((RUNS / t / "resolved.json").read_text())
                for t in SAME_SYNTHESIS]
    assert len({synthesised_area(RUNS / t) for t in SAME_SYNTHESIS}) == 1
    assert {r["CLOCK_PERIOD"] for r in resolved} == {40, 50, 100}
    assert {r["GRT_DESIGN_REPAIR_MAX_SLEW_PCT"] for r in resolved} == {10, 32, 45}
    assert len({json.dumps(r.get("NON_DEFAULT_RULES")) for r in resolved}) == 2


# ── the rules ────────────────────────────────────────────────────────
def _run(tmp_path, steps):
    for name, metrics in steps.items():
        (tmp_path / name).mkdir()
        if metrics is not None:
            (tmp_path / name / "state_out.json").write_text(
                json.dumps({"metrics": metrics}))
    return tmp_path


def test_negative_slack_before_detailed_routing_is_pruned(tmp_path):
    run = _run(tmp_path, {
        "44-openroad-stamidpnr-3": {"timing__setup__ws": -0.5,
                                    "timing__hold__ws": 0.3},
        "45-openroad-detailedrouting": None})
    v = screen_post_grt(run)
    assert not v.keep and "setup -0.500" in v.reason


def test_zero_slack_is_kept(tmp_path):
    """The gate accepts zero slack, so the screen must not prune it."""
    run = _run(tmp_path, {
        "44-openroad-stamidpnr-3": {"timing__setup__ws": 0.0,
                                    "timing__hold__ws": 0.0},
        "45-openroad-detailedrouting": None})
    assert screen_post_grt(run).keep


def test_nothing_is_pruned_before_detailed_routing_starts(tmp_path):
    """Negative slack after CTS is not a verdict: repair after global routing
    still moves timing, and the premise was measured only right before
    detailed routing."""
    run = _run(tmp_path, {"38-openroad-stamidpnr-2": {"timing__setup__ws": -3.0}})
    assert screen_post_grt(run).keep


def test_missing_metrics_keep_the_candidate(tmp_path):
    """A screen must prove a candidate bad; not measured proves nothing."""
    run = _run(tmp_path, {"44-openroad-stamidpnr-3": None,
                          "45-openroad-detailedrouting": None})
    assert screen_post_grt(run).keep


def test_a_routing_failure_prunes_only_its_own_hart_count():
    assert not screen_model(4, 0.75).keep     # blockc_generated failed here
    assert not screen_model(4, 0.80).keep
    assert screen_model(4, 0.70).keep         # between clean 0.65 and failed 0.75
    assert screen_model(5, 0.90).keep         # never routed: the guard decides
    assert screen_model(3, 0.80).keep         # 3 harts has never failed
