"""The gate-level evidence path reported a passing run as never having run.

Two independent defects, both found by grounding a triage skill in the real
artifacts rather than in the code's intent:

  1. run_gls.sh printed the run identity to stdout while `tee` OVERWROTE
     sim-gls.log with vvp's output alone. gls_for_run greps that log for the
     run tag, so every completed run returned NOT_RUN -- including runs that
     had just printed EXIT SUCCESS.

  2. flow_runner parsed `exit_success` only for flows whose name contains
     "tb-". The `gls` flow does not, yet declares require_exit_success, so the
     gate compared `None is True` and a passing gate-level run reported FAIL.

Together they meant the harness could not report a GLS pass at all, by two
different routes. Both are cheap to reintroduce, hence these tests.
"""
from __future__ import annotations

import re

import pytest

from harness.core import REPO_ROOT
from harness.evidence.gls import GlsStatus, parse_gls_log

RUN_GLS = REPO_ROOT / "tb/gls/run_gls.sh"
FLOW_RUNNER = REPO_ROOT / "harness/skills/flow_runner.py"


# ── defect 1: the identity never reached the log ─────────────────────
def test_the_identity_header_is_written_to_the_log_not_only_stdout():
    text = RUN_GLS.read_text()
    assert 'tee "$HERE/sim-gls.log"' in text, (
        "the header block must seed the log; without it the run tag never "
        "reaches the file gls_for_run greps")


def test_the_simulation_output_appends_rather_than_overwrites():
    """`tee` without -a would discard the identity the header just wrote."""
    text = RUN_GLS.read_text()
    assert 'tee -a "$HERE/sim-gls.log"' in text
    assert re.search(r'vvp .*\n.*tee -a "\$HERE/sim-gls\.log"', text), (
        "the vvp pipeline must append to the seeded log")


def test_a_seeded_log_carries_both_identity_and_verdict():
    """What the fixed script produces must satisfy BOTH checks at once."""
    seeded = (
        "### run     : /abs/flow/librelane/integration/runs/blocka_padframe_def\n"
        "### design  : mosaic_block_a\n"
        "### netlist : mosaic_block_a.pnl.v (12M)\n"
        "[GLS] zero-delay run (no SDF)\n"
        "[GLS] status_valid_o asserted after 12399 cycles, status_o = 0x00\n"
        "### RESULT: EXIT SUCCESS - gate-level netlist booted and reported 0\n"
    )
    result = parse_gls_log(seeded)
    assert result.status is GlsStatus.PASS
    assert result.cycles == 12399
    assert result.netlist == "mosaic_block_a.pnl.v"
    assert "blocka_padframe_def" in seeded, (
        "the identity a caller greps for must survive in the same text")


def test_a_log_without_the_header_still_parses_but_cannot_be_attributed():
    """The pre-fix shape: a real verdict that names no run.

    Kept as a test because this is what every archived log looks like, and the
    triage path must treat it as unattributable rather than as a failure.
    """
    bare = ("[GLS] zero-delay run (no SDF)\n"
            "### RESULT: EXIT SUCCESS - gate-level netlist booted and reported 0\n")
    assert parse_gls_log(bare).status is GlsStatus.PASS
    assert "blocka" not in bare


# ── defect 2: a gated flow whose gate was never fed ──────────────────
def test_exit_success_is_parsed_for_any_flow_that_gates_on_it():
    text = FLOW_RUNNER.read_text()
    assert 'or spec.get("require_exit_success")' in text, (
        'exit_success was parsed only for "tb-" flows; a flow declaring '
        "require_exit_success and not matching that prefix could never pass")


def test_the_gls_flow_is_one_of_those_flows():
    """Guards the specific case: `gls` gates on the marker and is not a tb- flow."""
    from harness.skills.flow_runner import FLOWS
    spec = FLOWS["gls"]
    assert spec.get("require_exit_success") is True
    assert "tb-" not in "gls", "if this ever changes, the fix above is moot"


@pytest.mark.parametrize("flow_name,gated", [
    ("gls", True),
    ("tb-soc-wake", False),
])
def test_every_gated_flow_would_reach_the_parser(flow_name, gated):
    from harness.skills.flow_runner import FLOWS
    spec = FLOWS.get(flow_name)
    if spec is None:
        pytest.skip(f"{flow_name} not registered")
    reaches = "tb-" in flow_name or bool(spec.get("require_exit_success"))
    if spec.get("require_exit_success"):
        assert reaches, f"{flow_name} gates on a marker it never parses"


def test_no_flow_gates_on_a_marker_it_cannot_receive():
    """The general invariant, so the next such flow fails here."""
    from harness.skills.flow_runner import FLOWS
    broken = [
        name for name, spec in FLOWS.items()
        if spec.get("require_exit_success")
        and "tb-" not in name
        and 'or spec.get("require_exit_success")' not in FLOW_RUNNER.read_text()
    ]
    assert not broken, f"these gate on exit_success but never parse it: {broken}"


def test_two_designs_can_both_hold_a_verdict(tmp_path):
    """The shared log holds only the LAST gate-level run, so one design's
    verdict used to erase every other's: simulating Block C on 2026-09-18 reset
    every Block A run to NOT_RUN, and the tapeout part's passing GLS became
    unreadable. A per-run copy cannot be overwritten by another design.
    """
    from harness.evidence.gls import GlsStatus, gls_for_run

    def log_for(tag, marker="EXIT SUCCESS"):
        return (f"### run     : /x/runs/{tag}\n"
                f"### netlist : {tag}.pnl.v\n"
                f"[GLS] status_valid_o asserted at 1 after 12400 cycles\n"
                f"### RESULT: {marker}\n")

    a, c = tmp_path / "runs/blocka", tmp_path / "runs/blockc"
    for d in (a, c):
        d.mkdir(parents=True)
    (a / "gls.log").write_text(log_for("blocka"))
    (c / "gls.log").write_text(log_for("blockc"))

    # the shared log names only the most recent design
    (tmp_path / "tb/gls").mkdir(parents=True)
    (tmp_path / "tb/gls/sim-gls.log").write_text(log_for("blockc"))

    assert gls_for_run(a, repo_root=tmp_path).status is GlsStatus.PASS
    assert gls_for_run(c, repo_root=tmp_path).status is GlsStatus.PASS


def test_a_run_with_no_copy_still_reads_the_shared_log(tmp_path):
    """Runs simulated before the per-run copy existed keep their evidence."""
    from harness.evidence.gls import GlsStatus, gls_for_run

    old = tmp_path / "runs/blocka"
    old.mkdir(parents=True)
    (tmp_path / "tb/gls").mkdir(parents=True)
    (tmp_path / "tb/gls/sim-gls.log").write_text(
        "### run     : /x/runs/blocka\n### RESULT: EXIT SUCCESS\n")
    assert gls_for_run(old, repo_root=tmp_path).status is GlsStatus.PASS


def test_a_log_naming_another_netlist_is_never_this_run_s_evidence(tmp_path):
    """Both sources are tag-checked. Reading a log about a different netlist is
    the mistake that once made a re-hardened netlist look fine."""
    from harness.evidence.gls import GlsStatus, gls_for_run

    run = tmp_path / "runs/blocka"
    run.mkdir(parents=True)
    (run / "gls.log").write_text(
        "### run     : /x/runs/blockc\n### RESULT: EXIT SUCCESS\n")
    (tmp_path / "tb/gls").mkdir(parents=True)
    (tmp_path / "tb/gls/sim-gls.log").write_text(
        "### run     : /x/runs/blockc\n### RESULT: EXIT SUCCESS\n")
    result = gls_for_run(run, repo_root=tmp_path)
    assert result.status is GlsStatus.NOT_RUN
    assert "different netlist" in " ".join(result.reasons)
