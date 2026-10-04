"""The workload rules, and the power number they disqualify.

Every signoff run in this project reports power, and none of it is workload
power: LibreLane calls `report_power` with no activity input, so OpenSTA uses
its default toggle model. The tell is in any run's power.rpt -- combinational
switching is ~0.5% of total against ~99.5% for clock and sequential.

The rule is that failed oracles, incomplete ROIs and insufficient activity coverage
cannot produce valid power evidence. Today all three would have to pass and
one of them (activity) cannot pass at all, because nothing in the simulation
build emits a trace. These tests hold that line rather than pretending
otherwise.
"""

import pytest

from harness.evidence.workload import (
    ActivityCapture,
    ActivityKind,
    WorkloadError,
    WorkloadRun,
    parse_sim_log,
    power_metric_status,
)


def run(**kw) -> WorkloadRun:
    base = dict(
        workload="wake-demo",
        design="mosaic_block_a",
        config_digest="c" * 64,
        firmware_digest="f" * 64,
        oracle_passed=True,
        roi_cycles=12_399,
        max_cycles=300_000,
        activity=ActivityCapture(ActivityKind.VCD, "sim.vcd", "a" * 64, 12_399),
    )
    base.update(kw)
    return WorkloadRun(**base)


# ── no workload evidence without the hashes ──────────────────────────

def test_a_run_without_a_firmware_digest_is_refused():
    """Two runs of one workload differ if the firmware moved."""
    with pytest.raises(WorkloadError, match="firmware digest"):
        run(firmware_digest="")


def test_a_run_must_name_its_workload():
    with pytest.raises(WorkloadError, match="name its workload"):
        run(workload="")


# ── three separate reasons power evidence is invalid ─────────────────

def test_a_fully_evidenced_run_supports_power():
    assert run().supports_power_evidence
    assert run().power_evidence_problems() == []


def test_a_failed_oracle_disqualifies_power():
    problems = run(oracle_passed=False).power_evidence_problems()
    assert any("oracle failed" in p for p in problems)
    assert not run(oracle_passed=False).supports_power_evidence


def test_a_missing_region_of_interest_disqualifies_power():
    problems = run(roi_cycles=None).power_evidence_problems()
    assert any("region of interest" in p for p in problems)


def test_a_truncated_run_is_an_incomplete_roi():
    """Hitting +maxcycles means it stopped, not that it finished."""
    problems = run(roi_cycles=300_000, max_cycles=300_000).power_evidence_problems()
    assert any("truncated" in p for p in problems)


def test_no_activity_capture_disqualifies_power():
    problems = run(activity=None).power_evidence_problems()
    assert any("no switching activity" in p for p in problems)
    assert any("default toggle model" in p for p in problems)


def test_partial_activity_coverage_disqualifies_power():
    short = ActivityCapture(ActivityKind.VCD, "s.vcd", "a" * 64, 500)
    problems = run(activity=short).power_evidence_problems()
    assert any("covers 500 cycles" in p for p in problems)


def test_every_reason_is_reported_not_just_the_first():
    """A run with three problems should say three things."""
    problems = run(oracle_passed=False, roi_cycles=None,
                   activity=None).power_evidence_problems()
    assert len(problems) == 3


# ── how a power number may be described ──────────────────────────────

def test_power_with_no_workload_is_default_activity():
    status, reasons = power_metric_status(None)
    assert status == "default-activity"
    assert reasons and "default toggle model" in reasons[0]


def test_power_from_a_complete_run_is_workload_power():
    status, reasons = power_metric_status(run())
    assert status == "workload" and reasons == []


def test_there_is_no_third_status():
    """Either the number reflects a workload or it does not."""
    for candidate in (None, run(), run(activity=None)):
        status, _ = power_metric_status(candidate)
        assert status in {"workload", "default-activity"}


# ── reading a real testbench log ─────────────────────────────────────

def test_the_oracle_is_the_marker_not_the_exit_status():
    """A simulator can exit 0 having printed a failure."""
    passed, cycles, bound = parse_sim_log(
        "hart0 alive\n+maxcycles=300000\ncycles= 12399\nEXIT SUCCESS\n")
    assert passed and cycles == 12399 and bound == 300000

    failed, _, _ = parse_sim_log("hart0 alive\nTB FAIL reason=timeout\n")
    assert failed is False


def test_a_log_with_no_markers_yields_nothing_rather_than_guessing():
    passed, cycles, bound = parse_sim_log("")
    assert passed is False and cycles is None and bound is None


# ── the shipped runs are labelled honestly ───────────────────────────

def test_the_signoff_report_labels_power_as_default_activity():
    from harness.core import REPO_ROOT
    from harness.physical.report import signoff_summary

    run_dir = REPO_ROOT / "flow/librelane/experimental/runs/blocka_reharden"
    if not (run_dir / "final" / "metrics.json").is_file():
        pytest.skip("run tree not present")
    summary, _ = signoff_summary(run_dir)
    assert summary["power_watts"], "power is reported"
    assert summary["power_basis"] == "default-activity"
    assert summary["power_caveat"]


# ── the shapes the benches really print ──────────────────────────────
#
# Verbatim from flow/librelane/experimental/runs/blocka_1110_ndr/gls.log, the
# first run that ever captured switching activity. Until 2026-09-21 the ROI
# pattern was `\bcycles=`, which matches NOTHING any bench in this repo emits:
# the gate-level bench prints `(roi_cycles=N)` when its window closes and
# `after N cycles` on completion, and the full-SoC benches print no count at
# all. So parse_sim_log returned (True, None, None) on every real artefact and
# every workload was refused for "no region of interest" -- right outcome,
# wrong reason.
_REAL_GLS_LOG = """\
### deposits: gls_powerup_init.svh
### compiled (1 warnings)
### plusargs : +firmware=generic.hex +maxcycles=2000000 +vcd=/x/a.vcd \
+vcd_start=8000 +vcd_cycles=500
[GLS] activity ROI open at cycle 8000
[GLS] activity ROI closed at cycle 8500 (roi_cycles=500)
[GLS] status_valid_o asserted at 1240050000 after 12400 cycles, status_o = 0x00
### RESULT: EXIT SUCCESS — gate-level netlist booted and reported 0
"""


def test_the_roi_comes_from_the_window_the_bench_reports():
    passed, roi, bound = parse_sim_log(_REAL_GLS_LOG)
    assert passed is True
    assert roi == 500, "the ROI is the captured window, not the 12400-cycle boot"
    assert bound == 2000000


def test_the_bound_is_never_mistaken_for_the_region_of_interest():
    """`+maxcycles=` in the same log must not become the ROI: a truncated run
    would then look like a complete one covering the whole bound."""
    passed, roi, bound = parse_sim_log(
        "+maxcycles=300000\nEXIT SUCCESS\n")
    assert passed is True and bound == 300000
    assert roi is None, "no window was reported, so there is no ROI to claim"


def test_a_captured_window_makes_a_run_support_power_evidence():
    """The end-to-end rule on the real artefact: oracle passed, a window
    reported, and activity covering that window."""
    from harness.evidence.workload import (ActivityCapture, ActivityKind,
                                           WorkloadRun, power_metric_status)
    passed, roi, bound = parse_sim_log(_REAL_GLS_LOG)
    run = WorkloadRun(
        workload="generic-liveness", design="mosaic_block_a",
        config_digest="c", firmware_digest="f", oracle_passed=passed,
        roi_cycles=roi, max_cycles=bound,
        activity=ActivityCapture(kind=ActivityKind.VCD, path="/x/a.vcd",
                                 digest="d", cycles_covered=roi))
    assert run.supports_power_evidence, run.power_evidence_problems()
    assert power_metric_status(run)[0] == "workload"

    # ... and a capture shorter than the window it claims does not
    short = WorkloadRun(
        workload="generic-liveness", design="mosaic_block_a",
        config_digest="c", firmware_digest="f", oracle_passed=passed,
        roi_cycles=roi, max_cycles=bound,
        activity=ActivityCapture(kind=ActivityKind.VCD, path="/x/a.vcd",
                                 digest="d", cycles_covered=roi - 1))
    assert not short.supports_power_evidence
    assert power_metric_status(short)[0] == "default-activity"
