"""A triage skill must report what its oracle can see before what it found.

Shaped by a real incident. A GLS failure here was tracked across four netlists
and localised to a missing `dlya_2` cell. It was a cell SWAP: instance _28773_
was dlya_2 in every booting netlist and clkbuf_1 in the failing one, same nets,
both non-inverting buffers, a no-op in zero delay. The real cause was a race
between zero-delay evaluation and UDP flops.

A skill that named a cell there would have been confidently wrong, so these
tests are mostly about what it REFUSES to conclude.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.core import REPO_ROOT
from harness.skills.gls_triage import (
    ORACLE, TRANSPARENT_KINDS, _attributable, _check_padwrap, audit,
    oracle_model, triage,
)

PASS_LOG = (
    "### run     : /abs/runs/blocka_padframe_def\n"
    "### netlist : mosaic_block_a.pnl.v (12M)\n"
    "[GLS] zero-delay run (no SDF)\n"
    "[PADWRAP] pad controls: uart OE/IE=1/0  rst PD/PU=1/0  qspi PDRV=10  IE&OE=0000\n"
    "[GLS] status_valid_o asserted after 12399 cycles, status_o = 0x00\n"
    "### RESULT: EXIT SUCCESS - gate-level netlist booted and reported 0\n"
)


def _log(tmp_path, text, name="blocka_padframe_def"):
    run = tmp_path / name
    run.mkdir(parents=True, exist_ok=True)
    (run / "sim-gls.log").write_text(text)
    return run


# ── the oracle is reported first, always ─────────────────────────────
def test_the_oracle_is_reported_even_on_a_pass(tmp_path):
    """If an oracle produces false failures, its passes are weak too."""
    run = _log(tmp_path, PASS_LOG)
    d = audit(str(run)).details
    assert d["oracle"]["flop_model"].startswith("UDP")
    assert "refused by design" in d["oracle"]["sdf"]
    assert d["oracle"]["timing_authority"].startswith("STA")


# ── which oracle produced the log ────────────────────────────────────
def test_the_oracle_reported_is_the_one_the_log_names(tmp_path):
    """It was a constant saying "zero". A log from the race-free oracle would
    have been described as race-prone, which is the claim the P0 removes."""
    race_free = PASS_LOG.replace(
        "### netlist :", "### seq c2q : 1 ns on 18 sequential UDPs\n### netlist :")
    d = audit(str(_log(tmp_path / "rf", race_free))).details
    assert d["oracle"]["race_prone"] is False
    assert "clock-to-Q 1 ns" in d["oracle"]["delay_mode"]

    d0 = audit(str(_log(tmp_path / "zd", PASS_LOG))).details
    assert d0["oracle"]["race_prone"] is True
    assert d0["oracle"]["delay_mode"] == "zero"


def test_a_log_predating_the_header_is_treated_as_race_prone(tmp_path):
    """Silence is not the fix. Every log written before run_gls.sh recorded
    the oracle came from the zero-delay one."""
    t = triage(_log(tmp_path / "old", PASS_LOG), repo_root=tmp_path)
    assert t.gls.seq_delay_ns is None and t.gls.race_prone is True
    assert any("predates" in n for n in t.notes)


def test_oracle_model_marks_only_the_sequential_arc(tmp_path):
    """Combinational cells stay zero-delay in both modes -- the race lived on
    clock-to-Q, and overstating the fix would be its own error."""
    assert "combinational zero" in oracle_model(1.0)["delay_mode"]
    assert oracle_model(0.0)["race_prone"] is True
    assert "GLS_SEQ_DELAY=1" in oracle_model(0.0)["caveat"]


def test_the_oracle_names_what_it_cannot_see():
    assert set(ORACLE["blind_to"]) == TRANSPARENT_KINDS
    assert "buffer" in ORACLE["blind_to"] and "clock" in ORACLE["blind_to"]


# ── the dlya_2 refusal ───────────────────────────────────────────────
def test_a_buffer_only_difference_cannot_explain_a_behavioural_one():
    """The incident, as a unit test."""
    refusal = _attributable({"by_kind": {"buffer": 1, "clock": 1, "logic": 0}})
    assert refusal is not None
    assert "zero-delay simulation cannot distinguish" in refusal
    assert "dlya_2" in refusal


def test_a_logic_difference_is_attributable():
    assert _attributable({"by_kind": {"logic": 12, "buffer": 400}}) is None


def test_a_buffer_only_difference_still_does_not_attribute_under_seq_delay():
    """The race is excluded, but buffers are logically transparent at ANY
    delay. The refusal has to survive the fix, only with a different reason."""
    refusal = _attributable({"by_kind": {"buffer": 1, "clock": 1}}, 1.0)
    assert refusal is not None
    assert "logically transparent at any delay" in refusal
    assert "connectivity" in refusal
    # ...and it must NOT still blame the race it no longer has.
    assert "artefact of the oracle" not in refusal


def test_identical_netlists_indict_the_simulator():
    refusal = _attributable({"by_kind": {"buffer": 0, "logic": 0}})
    assert refusal and "the simulator, not the design" in refusal


def test_no_control_means_undecidable_not_design_suspect(tmp_path):
    """A failing run alone cannot separate a design fault from an artefact."""
    run = _log(tmp_path, PASS_LOG.replace(
        "### RESULT: EXIT SUCCESS - gate-level netlist booted and reported 0",
        "### RESULT: gate-level simulation FAILED"))
    t = triage(run, repo_root=tmp_path)
    assert t.verdict == "UNDECIDABLE_NO_CONTROL"
    assert any("cannot separate a design fault" in r for r in t.refusals)


# ── attribution before verdict ───────────────────────────────────────
def test_a_log_about_another_run_is_refused_not_reported(tmp_path):
    run = _log(tmp_path, PASS_LOG.replace("blocka_padframe_def", "some_other_run"))
    t = triage(run, repo_root=tmp_path)
    assert t.verdict == "WRONG_RUN"
    assert any("evidence about" in r for r in t.refusals)


def test_a_log_with_no_header_is_noted_as_unattributable(tmp_path):
    """Every archived log predating the header fix is in this state."""
    bare = "\n".join(l for l in PASS_LOG.splitlines() if not l.startswith("### run"))
    run = _log(tmp_path, bare)
    t = triage(run, repo_root=tmp_path)
    assert any("cannot be attributed" in n for n in t.notes)
    assert t.verdict == "PASS"          # still a real verdict, just unattributed


# ── the assertions nothing was reading ───────────────────────────────
def test_padwrap_failures_are_surfaced():
    checks = _check_padwrap(
        "[PADWRAP] FAIL rst_ni PD=0 PU=0, expected 1/0\n"
        "[PADWRAP] FAIL qspi IE&OE=1111 -- the PDK marks IE=1,OE=1 Disallowed\n")
    assert len(checks["failures"]) == 2
    assert "Disallowed" in checks["failures"][1]


def test_a_pass_with_failed_pad_checks_is_not_a_clean_pass(tmp_path):
    """Before this, a wrong pad configuration read as a clean PASS."""
    run = _log(tmp_path, PASS_LOG + "[PADWRAP] FAIL rst_ni PD=0 PU=0, expected 1/0\n")
    t = triage(run, repo_root=tmp_path)
    assert t.verdict == "PASS_WITH_UNCHECKED_ASSERTIONS"
    assert any("pad self-check failed" in f for f in t.findings)


def test_the_reported_pad_state_is_carried_through(tmp_path):
    run = _log(tmp_path, PASS_LOG)
    t = triage(run, repo_root=tmp_path)
    assert any("read back from the gates" in n for n in t.notes)


# ── degradation ──────────────────────────────────────────────────────
def test_a_missing_log_refuses_rather_than_guesses(tmp_path):
    t = triage(tmp_path / "nothing_here", repo_root=tmp_path)
    assert t.verdict == "NO_LOG"
    assert any("GLS_RUN" in r for r in t.refusals)


def test_it_runs_against_the_real_repository_log():
    r = audit()
    assert r.details["oracle"]
    assert r.details["verdict"]


def test_the_skill_is_declared_read_only():
    from harness.flow_spec import Effect
    from harness.skill_policy import SKILL_SPECS
    spec = SKILL_SPECS["gls-triage"]
    assert spec.effect is Effect.READ
    assert spec.evidence is False


# ── the patch that makes the oracle race-free ────────────────────────
# run_gls.sh rewrites every sequential UDP INSTANCE in a derived copy of the
# cell models. If a PDK bump ever changes the instantiation shape the sed
# becomes a silent no-op: the run still says "1 ns", the race is back, and
# nothing looks wrong. run_gls.sh refuses at runtime; this catches it earlier.
_CELLS = (REPO_ROOT / "flow/librelane/gf180mcu/gf180mcuD/libs.ref"
          / "gf180mcu_fd_sc_mcu7t5v0/verilog/gf180mcu_fd_sc_mcu7t5v0.v")
_UDP_RE = re.compile(r"^([ \t]*[A-Za-z_][A-Za-z0-9_]*__udp_[a-z0-9_]+)\(", re.M)


@pytest.mark.skipif(not _CELLS.is_file(), reason="GF180 cell models not staged")
def test_the_sequential_delay_patch_matches_every_udp_instance():
    text = _CELLS.read_text()
    sites = _UDP_RE.findall(text)
    assert sites, ("no sequential UDP instantiations matched in the GF180 cell "
                   "models. run_gls.sh's clock-to-Q patch would be a no-op and "
                   "GLS would silently go back to racing")
    # Every match must be a flop or a latch: delaying a combinational UDP
    # would change the design, not just the race.
    assert all(s.endswith(("_ff", "_latch")) for s in sites), sorted(set(sites))
    patched = _UDP_RE.sub(r"\1 #1 (", text)
    assert patched.count("__udp_") - text.count("__udp_") == 0
    assert len(re.findall(r"__udp_[a-z0-9_]+ #1 \(", patched)) == len(sites)


@pytest.mark.skipif(not _CELLS.is_file(), reason="GF180 cell models not staged")
def test_run_gls_refuses_rather_than_claiming_a_delay_it_lacks():
    """The guard is the point: a no-op patch must fail, not pass quietly."""
    sh = (REPO_ROOT / "tb/gls/run_gls.sh").read_text()
    assert "GLS_SEQ_DELAY" in sh
    assert 'refusing to run a simulation that claims a nonzero' in sh
    assert "### seq c2q" in sh
