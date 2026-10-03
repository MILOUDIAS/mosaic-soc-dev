"""kepler-formal: does the netlist compute what the RTL said?

Fixtures are REAL tool output, captured from kepler-formal 2026-07-14 on a
deliberately equivalent and a deliberately inequivalent pair. Both runs exited
0 -- which is the single most important fact about integrating this tool.
"""

import pytest

from harness.evidence.lec import LecStatus, lec_for_run, parse_lec_log
from harness.flow_spec import build_specs
from harness.skills.flow_runner import FLOWS

PROVEN = """[info] Verification: sec
2026-08-12 18:48:15,249 [naja] [info] SEC checked-output coverage: 100.00% (1/1 covered/existing outputs).
2026-08-12 18:48:15,249 [naja] [info] No difference was found. SEC proved equivalence at k = 0.
"""

DISPROVEN = """[info] Verification: sec
2026-08-12 18:48:34,280 [naja] [info] SEC checked-output coverage: 100.00% (1/1 covered/existing outputs).
2026-08-12 18:48:34,280 [naja] [info] Difference was found. SEC found a counterexample at k = 0.
Counterexample reaches the first bad frame at cycle 0.
"""

PARTIAL = """[info] Verification: sec
[naja] [info] SEC checked-output coverage: 61.50% (123/200 covered/existing outputs).
[naja] [info] No difference was found. SEC proved equivalence at k = 4.
"""

LOAD_FAILED = """[critical] Netlist loading failed: SystemVerilog compilation failed:
"""


def test_a_full_coverage_proof_is_the_only_pass():
    result = parse_lec_log(PROVEN)
    assert result.status is LecStatus.PROVEN
    assert result.coverage_pct == 100.0 and result.k == 0
    assert not result.blocks_signoff


def test_a_counterexample_is_disproven_despite_a_zero_exit():
    """kepler-formal exits 0 here. Gating on $? would call this a pass."""
    result = parse_lec_log(DISPROVEN)
    assert result.status is LecStatus.DISPROVEN
    assert result.blocks_signoff
    assert any("exits 0" in r for r in result.reasons)


def test_a_partial_proof_is_not_a_proof_of_the_design():
    """61.5% of outputs proved is a statement about 61.5% of the outputs."""
    result = parse_lec_log(PARTIAL)
    assert result.status is LecStatus.INCONCLUSIVE
    assert result.outputs_checked == 123 and result.outputs_total == 200
    assert any("not about the design" in r for r in result.reasons)
    assert result.blocks_signoff


def test_a_design_that_would_not_load_has_not_been_agreed_with():
    result = parse_lec_log(LOAD_FAILED)
    assert result.status is LecStatus.INCONCLUSIVE
    assert any("failed to load" in r for r in result.reasons)


def test_a_timeout_is_not_a_pass():
    """Formal runs time out. Silence must not read as agreement."""
    result = parse_lec_log("[info] Verification: sec\nParsing...\n")
    assert result.status is LecStatus.INCONCLUSIVE
    assert any("timeout is not a pass" in r for r in result.reasons)


@pytest.mark.parametrize("status", list(LecStatus))
def test_only_proven_clears(status):
    from harness.evidence.lec import LecResult

    assert LecResult(status).blocks_signoff is (status is not LecStatus.PROVEN)


def test_evidence_is_matched_to_the_run(tmp_path):
    (tmp_path / "build" / "lec").mkdir(parents=True)
    (tmp_path / "build" / "lec" / "blocka_signoff.log").write_text(PROVEN)

    same = lec_for_run(tmp_path / "runs" / "blocka_signoff", repo_root=tmp_path)
    assert same.status is LecStatus.PROVEN
    other = lec_for_run(tmp_path / "runs" / "blockc_slew45", repo_root=tmp_path)
    assert other.status is LecStatus.NOT_RUN
    assert any("LEC_RUN=" in r for r in other.reasons)


def test_lec_is_a_declared_flow_that_needs_approval():
    """Hours of formal on a 70k-cell design is a real cost."""
    spec = build_specs(FLOWS)["lec"]
    assert spec.approval is True
    assert spec.cost.value == "hours"
    assert "physical" in spec.scopes


def test_the_summary_reports_lec_without_blocking_on_it_yet():
    """It is reported now and blocks once it is shown to converge here."""
    from harness.core import REPO_ROOT
    from harness.physical.report import signoff_summary

    run = REPO_ROOT / "flow/librelane/experimental/runs/blocka_reharden"
    if not (run / "final" / "metrics.json").is_file():
        pytest.skip("run tree not present")
    summary, _ = signoff_summary(run)
    assert summary["lec"]["status"] == "not_run"
    assert not any(f.startswith("lec:") for f in summary["hard_checks_failing"])
