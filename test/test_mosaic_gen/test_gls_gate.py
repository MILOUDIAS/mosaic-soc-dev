"""GLS is a signoff check, and "never run" is not "passed".

On 2026-08-12 a netlist passed DRC 0, LVS 0 with no unmatched nets, XOR 0,
antenna 0, routing DRC 0, setup +20.94 ns and hold +0.0667 ns, and did not
boot. LVS proves the layout matches the NETLIST; nothing proved the netlist
matches the RTL. GLS did, and GLS was not part of the gate.
"""

import re

import pytest

from harness.core import REPO_ROOT
from harness.evidence.gls import GlsStatus, gls_for_run, parse_gls_log
from harness.flow_spec import build_specs
from harness.skills.flow_runner import FLOWS

PASS_LOG = """### run     : /x/runs/blocka_signoff
### netlist : mosaic_block_a.pnl.v (12M)
[GLS] status_valid_o asserted at 1239950000 after 12399 cycles, status_o = 0x00
### RESULT: EXIT SUCCESS — gate-level netlist booted and reported 0
### RESULT: gate-level simulation PASSED
"""

FAIL_LOG = """### run     : /x/runs/blocka_reharden
### netlist : mosaic_block_a.pnl.v (12M)
[GLS] 40000 cycles, t=4000050000, sck=0 csb=1 sd=Z
### RESULT: FAIL — watchdog at 40001 cycles without status_valid_o
### RESULT: gate-level simulation FAILED
"""


# ── the three-valued answer ──────────────────────────────────────────

def test_a_booting_netlist_passes():
    result = parse_gls_log(PASS_LOG)
    assert result.status is GlsStatus.PASS
    assert result.cycles == 12399
    assert not result.blocks_signoff


def test_a_netlist_that_does_not_boot_fails_and_says_why():
    result = parse_gls_log(FAIL_LOG)
    assert result.status is GlsStatus.FAIL
    assert result.blocks_signoff
    assert any("netlist matches the RTL" in r for r in result.reasons)


def test_never_run_is_not_passed():
    """The status that would otherwise be invisible."""
    result = parse_gls_log("nothing to see here")
    assert result.status is GlsStatus.NOT_RUN
    assert result.blocks_signoff


@pytest.mark.parametrize("status", list(GlsStatus))
def test_only_pass_clears_signoff(status):
    from harness.evidence.gls import GlsResult

    assert GlsResult(status).blocks_signoff is (status is not GlsStatus.PASS)


# ── evidence must be about THIS netlist ──────────────────────────────

def test_a_log_from_another_run_is_not_evidence_about_this_one(tmp_path):
    """tb/gls writes one log in a fixed place, so a stale result would
    otherwise be read as evidence about whichever run you asked about."""
    repo = tmp_path
    (repo / "tb" / "gls").mkdir(parents=True)
    (repo / "tb" / "gls" / "sim-gls.log").write_text(PASS_LOG)

    same = gls_for_run(tmp_path / "runs" / "blocka_signoff", repo_root=repo)
    assert same.status is GlsStatus.PASS

    other = gls_for_run(tmp_path / "runs" / "blockc_slew45", repo_root=repo)
    assert other.status is GlsStatus.NOT_RUN
    assert any("different netlist" in r for r in other.reasons)


def test_a_missing_log_says_how_to_produce_one(tmp_path):
    result = gls_for_run(tmp_path / "runs" / "x", repo_root=tmp_path)
    assert result.status is GlsStatus.NOT_RUN
    assert any("GLS_RUN=" in r for r in result.reasons)


# ── it is a declared flow, and it blocks the summary ─────────────────

def test_gls_is_a_registered_declared_flow():
    spec = build_specs(FLOWS)["gls"]
    assert "physical" in spec.scopes
    assert FLOWS["gls"]["require_exit_success"] is True


def test_the_signoff_summary_reports_gls_without_gating_on_it():
    """GLS is reported prominently and does NOT block. It gated for one day.

    It was promoted to a hard check because blocka_reharden passed every
    physical check and did not boot. The observation was real; the inference
    was not. That netlist and the booting one hold the same 36,572 logic
    instances -- identical names and functions -- and differ only in buffer,
    inverter, delay, clkbuf and fill cells, all transparent in a zero-delay
    simulation. They must behave identically; one did not.

    The oracle is what differs: UDP flops compiled -DFUNCTIONAL have no CLK->Q
    delay, which is the textbook race, and buffering changes event ordering
    without changing logic.

    A gate must be sound to be hard. This one produces false failures on
    provably-equivalent inputs, and a hard gate that does that teaches people
    to bypass gates. So the status is published -- three-valued, with reasons,
    NOT_RUN still visibly different from PASS -- and it is not counted.
    """
    from harness.physical.report import signoff_summary

    run = REPO_ROOT / "flow/librelane/experimental/runs/blocka_reharden"
    if not (run / "final" / "metrics.json").is_file():
        pytest.skip("run tree not present")
    summary, _ = signoff_summary(run)
    assert "gls" in summary
    # The judgement is still made and still visible.
    assert summary["gls"]["status"] in {"pass", "fail", "not_run"}
    assert "adverse" in summary["gls"]
    # But it is explicitly not a gate, and nothing it says reaches the count.
    assert summary["gls"]["gates_signoff"] is False
    assert not any(f.startswith("gls") for f in summary["hard_checks_failing"])
    # Every LibreLane hard check is zero on this run, so the run is clean --
    # which is now the honest answer for it.
    assert all(v == 0 for v in summary["hard_checks"].values())
    assert summary["adverse"] == 0


def test_the_run_gls_script_records_which_run_it_simulated():
    script = (REPO_ROOT / "tb/gls/run_gls.sh").read_text()
    assert '### run     : $RUN' in script, (
        "without this the log is unattributable and a stale result reads as "
        "evidence about the wrong netlist")


# ── which oracle produced the verdict ────────────────────────────────
# A FAIL is only evidence about the design if the simulation that produced it
# could not have invented it. Zero clock-to-Q with UDP flops can, so the log
# records the oracle and a race-prone FAIL says so in its own reasons.
def test_a_zero_delay_failure_carries_its_own_caveat():
    r = parse_gls_log(FAIL_LOG)
    assert r.status is GlsStatus.FAIL and r.race_prone is True
    assert any("ZERO clock-to-Q" in x for x in r.reasons)
    assert any("GLS_SEQ_DELAY=1" in x for x in r.reasons)


def test_a_race_free_failure_does_not_carry_the_caveat():
    """Once the race is excluded the caveat would be noise, and worse, it
    would keep pointing at an explanation that has been measured false."""
    r = parse_gls_log(FAIL_LOG.replace(
        "### netlist :", "### seq c2q : 1 ns on 18 sequential UDPs\n### netlist :"))
    assert r.status is GlsStatus.FAIL
    assert r.seq_delay_ns == 1.0 and r.race_prone is False
    assert not any("ZERO clock-to-Q" in x for x in r.reasons)


def test_a_pass_still_reports_which_oracle_ran():
    assert parse_gls_log(PASS_LOG).race_prone is True          # no header
    ok = parse_gls_log(PASS_LOG.replace(
        "### netlist :", "### seq c2q : 1 ns on 18\n### netlist :"))
    assert ok.status is GlsStatus.PASS and ok.race_prone is False


# ── the async-reset-release race ─────────────────────────────────────
# The 2026-08-12 "failure" was this: the bench deasserted rst_n in the same
# timestep as the capture edge, so the dffrnq/dffsnq flops saw RN rise at the
# instant CLK rose. Which arrives first is decided by how many delta cycles
# the reset tree and the clock tree take -- by BUFFERING. Two netlists
# identical in logic, connectivity and cell function disagreed on whether the
# part boots. Released half a cycle away, both boot in 12 400 cycles.
# It was in seven benches, not one, so the guard covers the class. A bench may
# move off the edge with @(negedge clk) or with a delay (#1, #RESET_DEL) --
# either is fine; landing exactly on the edge is not.
_BENCHES = [
    "tb/gls/gls_tb.sv", "tb/gls/gls_tb_cvc.v", "tb/mosaic/mosaic_multicore_tb.sv",
    "tb/log_xbar/tb_log_xbar.sv", "tb/sci/serv/tb_serv_sci.sv",
    "tb/sci/picorv32/tb_picorv32_sci.sv", "tb/sci/fazyrv/tb_fazyrv_sci.sv",
    "tb/sci/hazard3/tb_hazard3_sci.sv", "tb/mosaic_soc/mosaic_tb.sv",
]


@pytest.mark.parametrize("rel", _BENCHES)
def test_no_bench_releases_reset_on_the_capture_edge(rel):
    f = REPO_ROOT / rel
    if not f.is_file():
        pytest.skip(f"{rel} not present")
    text = f.read_text()
    for m in re.finditer(r"rst_n\s*=\s*1'b1", text):
        before = text[max(0, m.start() - 220):m.start()]
        # The offending shape: an edge wait with nothing between it and the
        # deassert. `#<delay>` immediately before the assignment also clears it.
        if "@(posedge clk)" not in before:
            continue
        gap = before.rsplit("@(posedge clk)", 1)[1]
        assert ("@(negedge" in gap or "#" in gap), (
            f"{rel}: rst_n deasserted in the same timestep as the capture "
            "edge. Async-reset flops then see RN rise as CLK rises and the "
            "winner is decided by the delta-cycle depth of the reset tree vs "
            "the clock tree -- by buffering. That is how two provably "
            "equivalent netlists produced different GLS verdicts (2026-08-12); "
            "see tb/gls/README.md")
