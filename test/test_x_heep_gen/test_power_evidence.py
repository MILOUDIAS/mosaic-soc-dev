"""M2: power reports become typed metrics, or they are refused.

The failure this guards against is not a parse error. It is a number that
survives into a report with the wrong corner attached, which is how
`power__total` measured at 5.50 V became "143 mW at 5 V, about 28.6 mA" in a
draft reply. The true figures are 26.0 mA at max_ff_n40C_5v50 and 22.8 mA at
the nominal corner; 28.6 mA was never measured anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.core import REPO_ROOT
from harness.evidence.metric import Dimension, MetricError, Metric, WATT, UNKNOWN
from harness.evidence.power import (
    PowerReportError, parse_power_report, power_reports_for_run,
)
from harness.evidence.qualification import (
    QualificationError, QualificationResult, qualify_power, unsuffixed_corner,
)
from harness.evidence.workload import WorkloadRun

RUN = REPO_ROOT / "flow/librelane/integration/runs/blocka_padframe_def"
STA = RUN / "56-openroad-stapostpnr"

REPORT = """
===========================================================================
 report_power
============================================================================
======================= max_ss_125C_4v50 Corner ===================================

Group                    Internal    Switching      Leakage        Total
                            Power        Power        Power        Power (Watts)
------------------------------------------------------------------------
Sequential           4.922040e-02 1.267514e-05 6.536210e-06 4.923961e-02  52.3%
Combinational        1.398365e-04 3.415108e-04 1.832114e-05 4.996685e-04   0.5%
Clock                2.305767e-02 2.129056e-02 1.882041e-05 4.436705e-02  47.1%
Macro                0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Pad                  0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
------------------------------------------------------------------------
Total                7.241797e-02 2.164475e-02 4.366954e-05 9.410639e-02 100.0%
"""


# ── parsing ──────────────────────────────────────────────────────────
def test_the_corner_comes_from_the_report_not_the_caller():
    r = parse_power_report(REPORT, source="power.rpt")
    assert r.corner == "max_ss_125C_4v50"
    assert all(m.corner == "max_ss_125C_4v50" for m in r.metrics())


def test_a_report_without_a_corner_banner_is_refused():
    """The caller must not be able to supply a corner it inferred from a path."""
    stripped = "\n".join(l for l in REPORT.splitlines() if "Corner" not in l)
    with pytest.raises(PowerReportError, match="no corner banner"):
        parse_power_report(stripped, source="power.rpt")


def test_groups_and_total_are_separated():
    r = parse_power_report(REPORT, source="power.rpt")
    assert set(r.groups) == {"sequential", "combinational", "clock", "macro", "pad"}
    assert r.total is not None
    assert r.total.total == pytest.approx(9.410639e-02)
    assert "total" not in r.groups


def test_the_grand_total_uses_librelanes_key():
    """`power__total`, not `power__total__total`, so the two are comparable."""
    names = {m.name for m in parse_power_report(REPORT, source="p.rpt").metrics()}
    assert "power__total" in names
    assert "power__total__total" not in names
    for component in ("internal", "switching", "leakage"):
        assert f"power__{component}__total" in names


def test_a_report_with_no_recognisable_rows_is_refused():
    text = "=== max_ss_125C_4v50 Corner ===\nnothing here\n"
    with pytest.raises(PowerReportError, match="no power rows"):
        parse_power_report(text, source="p.rpt")


def test_clock_fraction_flags_a_default_toggle_report():
    r = parse_power_report(REPORT, source="p.rpt")
    assert r.clock_fraction == pytest.approx(0.471, abs=0.01)


# ── the M2 criteria ──────────────────────────────────────────────────
def test_a_power_metric_without_a_corner_cannot_be_built():
    """Criterion 2, enforced by Metric itself."""
    with pytest.raises(MetricError, match="without a corner"):
        Metric("power__total", 0.14, WATT, source="p.rpt")


def test_a_metric_with_an_unknown_unit_fails_qualification():
    """Criterion 1: a unit that carries no meaning is not a unit."""
    bad = Metric("mystery", 1.0, UNKNOWN, source="p.rpt", corner="tt")
    with pytest.raises(QualificationError, match="unknown unit"):
        QualificationResult(design="d", run_tag="r", metrics=[bad])


def test_workload_evidence_without_a_firmware_digest_is_refused():
    """Criterion 3, enforced by WorkloadRun."""
    from harness.evidence.workload import WorkloadError
    with pytest.raises(WorkloadError, match="firmware digest"):
        WorkloadRun(workload="wake", design="d", config_digest="c",
                    firmware_digest="")


def test_a_result_must_name_its_run():
    m = Metric("power__total", 0.1, WATT, source="p.rpt", corner="tt")
    with pytest.raises(QualificationError, match="name the run"):
        QualificationResult(design="d", run_tag="", metrics=[m])


def test_an_empty_report_set_is_refused_rather_than_qualified():
    with pytest.raises(QualificationError, match="nothing to"):
        qualify_power("d", "r", {})


# ── the corner-mixing trap, on the real run ──────────────────────────
@pytest.mark.skipif(not STA.is_dir(), reason="needs the hardened run")
def test_all_nine_corners_parse_from_the_real_run():
    reports = power_reports_for_run(STA)
    assert len(reports) == 9
    assert all(r.total is not None for r in reports.values())


@pytest.mark.skipif(not STA.is_dir(), reason="needs the hardened run")
def test_librelane_promotes_the_fast_corner_to_the_unsuffixed_key():
    """The measured fact behind this module's docstring.

    `power__total` is NOT the nominal corner. Reading it and calling it "power
    at 5 V" mixes a 5.50 V measurement with a 5.00 V supply.
    """
    reports = power_reports_for_run(STA)
    flow = json.loads((RUN / "final/metrics.json").read_text())
    assert unsuffixed_corner(reports, flow["power__total"]) == "max_ff_n40C_5v50"


@pytest.mark.skipif(not STA.is_dir(), reason="needs the hardened run")
def test_the_worst_power_corner_is_reported_with_its_corner():
    q = qualify_power("mosaic_block_a", "blocka_padframe_def",
                      power_reports_for_run(STA))
    worst = q.worst("power__total")
    assert worst.corner == "max_ff_n40C_5v50"
    assert worst.value == pytest.approx(0.143, abs=0.001)
    # and the nominal corner is materially lower, which is the whole point
    nominal = q.at("power__total", "nom_tt_025C_5v00")
    assert nominal.value < worst.value * 0.85


@pytest.mark.skipif(not STA.is_dir(), reason="needs the hardened run")
def test_power_without_a_workload_is_labelled_default_activity():
    q = qualify_power("mosaic_block_a", "blocka_padframe_def",
                      power_reports_for_run(STA))
    assert not q.describes_workload_power
    assert q.power_status == "default-activity"
    assert "not workload power" in q.power_caveat()


def test_a_bound_workload_that_lacks_activity_still_refuses_the_claim():
    """A workload alone is not enough: without a VCD it is still the toggle model."""
    run = WorkloadRun(workload="wake", design="mosaic_block_a",
                      config_digest="c", firmware_digest="d" * 64,
                      oracle_passed=True, roi_cycles=1000, max_cycles=2_000_000)
    r = parse_power_report(REPORT, source="p.rpt")
    q = qualify_power("mosaic_block_a", "r", {"max_ss_125C_4v50": r},
                      workload=run)
    assert q.power_status == "default-activity"
    assert any("switching activity" in x for x in q.power_status_reasons)
