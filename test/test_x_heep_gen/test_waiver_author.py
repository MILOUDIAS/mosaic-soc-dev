"""A waiver must still describe the chip, not merely parse.

`parse_waivers` validates shape and never opens `evidence`. Every test here is
a way a waiver stays structurally perfect while ceasing to be true, and the
specimen is real: the waiver in this repository has ceiling 1, cites
runs/blocka_sdc, passes every existing check, and the design measures 5 on the
run being submitted.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

from harness.evidence.waivers import WaiverError, parse_waivers
from harness.skills.waiver_author import audit, audit_waiver

TODAY = date(2026, 9, 6)


def _waiver(**over):
    e = {
        "metric": "design__max_fanout_violation__count",
        "design": "mosaic_block_a",
        "accepted_max": 5,
        "justification": "x" * 60,
        "review_by": "2026-12-01",
        "recorded_by": "tester",
        "evidence": "runs/good",
    }
    e.update(over)
    return parse_waivers({"waivers": [e]})[0]


def _run(root, name, metrics, design="mosaic_block_a"):
    run = root / "runs" / name
    (run / "final" / "pnl").mkdir(parents=True)
    (run / "final" / "pnl" / f"{design}.pnl.v").write_text("module x; endmodule\n")
    (run / "final" / "metrics.json").write_text(json.dumps(metrics))
    return run


def _gate_clean(**over):
    """Metrics a run needs for `evaluate` to call it accepted -- only such a
    run can supersede a waiver's evidence."""
    from harness.physical.report import HARD_CHECKS
    m = {**{k: 0 for k in HARD_CHECKS},
         "timing__setup__ws": 20.0, "timing__hold__ws": 0.1,
         "design__max_slew_violation__count": 0,
         "design__max_cap_violation__count": 0,
         "design__instance__area__class:stdcell": 1000.0,
         "design__die__area": 1_500_000, "power__total": 0.1}
    m.update(over)
    return m


def _accepted_run(root, name, metrics, design="mosaic_block_a"):
    run = _run(root, name, metrics, design=design)
    (run / "resolved.json").write_text(json.dumps(
        {"DESIGN_NAME": design, "PDK": "gf180mcu", "CLOCK_PERIOD": 100.0}))
    return run


# ── the field nothing ever opened ────────────────────────────────────
def test_an_unresolvable_evidence_path_is_caught(tmp_path):
    a = audit_waiver(_waiver(evidence="runs/never_existed"),
                     repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("does not resolve" in p for p in a.problems)


def test_a_run_without_metrics_is_caught(tmp_path):
    (tmp_path / "runs" / "good").mkdir(parents=True)
    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("metrics.json" in p for p in a.problems)


def test_a_metric_the_run_never_measured_is_caught(tmp_path):
    _run(tmp_path, "good", {"something__else": 1})
    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("does not measure" in p for p in a.problems)


# ── the ceiling must equal the measurement ───────────────────────────
def test_a_ceiling_above_the_measurement_waives_unobserved_headroom(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 2})
    a = audit_waiver(_waiver(accepted_max=5), repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("headroom nobody observed" in p for p in a.problems)


def test_a_ceiling_below_its_own_evidence_is_also_caught(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 9})
    a = audit_waiver(_waiver(accepted_max=5), repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("below what its own evidence" in p for p in a.problems)


def test_a_matching_ceiling_passes(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5})
    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert a.ok, a.problems


# ── self-consistent but obsolete ─────────────────────────────────────
def test_a_waiver_can_be_internally_perfect_and_superseded(tmp_path):
    """The specimen defect. Every internal check passes; the design moved on.

    The superseding run has to be one the design could actually ship, so it is
    built gate-clean here.
    """
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5})
    newer = _accepted_run(tmp_path, "later",
                          _gate_clean(design__max_fanout_violation__count=9))
    import os
    os.utime(newer / "final" / "metrics.json", (2 ** 31, 2 ** 31))   # finished last

    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("superseded" in p for p in a.problems)
    # and the ceiling itself was never wrong
    assert not any("headroom" in p for p in a.problems)


def test_a_run_that_fails_its_own_gates_supersedes_nothing(tmp_path):
    """Measured on the real tree: blocka_m45_ndr has 3 of this metric against
    the cited run's 4, and 28 max-slew violations with it. A lower count on a
    netlist that does not close is not progress, and acting on it would point
    the waiver at a design nobody can ship."""
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5})
    broken = _accepted_run(
        tmp_path, "later",
        _gate_clean(design__max_fanout_violation__count=3,
                    design__max_slew_violation__count=28))
    import os
    os.utime(broken / "final" / "metrics.json", (2 ** 31, 2 ** 31))

    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert not any("superseded" in p for p in a.problems), a.problems
    assert any("no other gate-passing run" in n for n in a.notes)


def test_an_older_run_supersedes_nothing(tmp_path):
    """Measured on the real tree: the audit read blocka_1110_ndr (finished
    2026-08-24) as superseding blocka_d15_rstsync (finished 2026-09-24),
    because pruning had touched the old run's directory mtime and any other
    accepted run counted, older or not."""
    import os
    cited = _run(tmp_path, "good", {"design__max_fanout_violation__count": 5})
    old = _accepted_run(tmp_path, "earlier",
                        _gate_clean(design__max_fanout_violation__count=9))
    os.utime(old / "final" / "metrics.json", (2 ** 30, 2 ** 30))
    os.utime(old, (2 ** 31, 2 ** 31))              # pruned recently
    os.utime(cited / "final" / "metrics.json", (2 ** 30 + 1, 2 ** 30 + 1))

    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert not any("superseded" in p for p in a.problems), a.problems


# ── preconditions: prose becomes data ────────────────────────────────
def test_a_broken_precondition_fails_the_audit(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5,
                            "design__max_slew_violation__count": 3})
    a = audit_waiver(
        _waiver(preconditions={"design__max_slew_violation__count": 0}),
        repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("precondition broken" in p for p in a.problems)


def test_a_held_precondition_passes(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5,
                            "design__max_slew_violation__count": 0})
    a = audit_waiver(
        _waiver(preconditions={"design__max_slew_violation__count": 0}),
        repo_root=tmp_path, today=TODAY)
    assert a.ok, a.problems


def test_no_preconditions_is_a_note_not_an_error(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5})
    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert a.ok
    assert any("prose that no later run can break" in n for n in a.notes)


def test_a_waiver_cannot_be_its_own_precondition():
    """Otherwise the record justifies itself."""
    with pytest.raises(WaiverError, match="justify itself"):
        _waiver(preconditions={"design__max_fanout_violation__count": 0})


# ── a count is not an argument ───────────────────────────────────────
def test_more_declared_violators_than_the_ceiling_is_refused():
    with pytest.raises(WaiverError, match="must never be longer"):
        _waiver(accepted_max=2, expected_violators=["a/Z", "b/Z", "c/Z"])


def test_duplicate_violators_are_refused():
    with pytest.raises(WaiverError, match="duplicates"):
        _waiver(expected_violators=["a/Z", "a/Z"])


def test_an_unpinned_waiver_says_so(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5})
    a = audit_waiver(_waiver(), repo_root=tmp_path, today=TODAY)
    assert any("unpinned" in n for n in a.notes)


# ── expiry ───────────────────────────────────────────────────────────
def test_an_expired_waiver_is_caught(tmp_path):
    _run(tmp_path, "good", {"design__max_fanout_violation__count": 5})
    a = audit_waiver(_waiver(review_by="2026-01-01"),
                     repo_root=tmp_path, today=TODAY)
    assert not a.ok
    assert any("expired" in p for p in a.problems)


# ── backward compatibility and the real file ─────────────────────────
def test_existing_waivers_still_parse_without_the_new_fields():
    w = _waiver()
    assert w.expected_violators == ()
    assert w.preconditions == {}
    assert w.pinned is False


def test_the_repositorys_own_waiver_is_audited():
    """Runs against the real file. Asserts the audit RUNS and reports, not a
    particular verdict, since which runs exist varies by branch."""
    result = audit()
    assert result.skill == "waiver-author"
    assert "waivers" in result.details
    for entry in result.details["waivers"]:
        assert "problems" in entry and "notes" in entry
        assert entry["ok"] == (not entry["problems"])


def test_the_skill_is_declared_read_not_write():
    """It audits a decision about silicon; it does not make one."""
    from harness.skill_policy import SKILL_SPECS
    from harness.flow_spec import Effect
    spec = SKILL_SPECS["waiver-author"]
    assert spec.effect is Effect.READ
    assert spec.evidence is False
    assert spec.approval is False


# The section format OpenSTA really prints, copied from
# runs/blocka_1110_ndr/56-openroad-stapostpnr/nom_tt_025C_5v00/sta.log. The
# blank line after the header is the point: reading to the first blank line
# yielded an EMPTY violator list for every real log, so `expected_violators`
# checked the identities against nothing and reported them absent.
_REAL_STA_SECTION = """\
report_check_types -max_slew -max_cap -max_fanout -violators
============================================================================
======================= nom_tt_025C_5v00 Corner ===================================

max fanout

Pin                                   Limit Fanout  Slack
---------------------------------------------------------
clkbuf_0_i_core_v_mini_mcu.cpu_subsystem_i.cpu_0_0.clk_i/Z     10     16     -6 (VIOLATED)
clkbuf_0_i_core_v_mini_mcu.memory_subsystem_i.clk_cg/Z     10     16     -6 (VIOLATED)


===========================================================================
"""


def _sta_run(tmp_path, section=_REAL_STA_SECTION):
    corner = tmp_path / "56-openroad-stapostpnr" / "nom_tt_025C_5v00"
    corner.mkdir(parents=True)
    (corner / "sta.log").write_text(section)
    # a non-directory entry beside the corners, as real runs have
    (tmp_path / "56-openroad-stapostpnr" / "COMMANDS").write_text("x\n")
    return tmp_path


def test_violators_are_read_from_a_real_sta_section(tmp_path):
    from harness.skills.waiver_author import _violators_in
    pins = _violators_in(_sta_run(tmp_path),
                         "design__max_fanout_violation__count")
    assert pins == [
        "clkbuf_0_i_core_v_mini_mcu.cpu_subsystem_i.cpu_0_0.clk_i/Z",
        "clkbuf_0_i_core_v_mini_mcu.memory_subsystem_i.clk_cg/Z",
    ]


def test_an_unparseable_report_is_unverifiable_not_empty(tmp_path):
    """None means "cannot check"; [] means "checked, found none". Conflating
    them is what made a waiver's pinned identities look absent."""
    from harness.skills.waiver_author import _violators_in
    bare = tmp_path / "56-openroad-stapostpnr" / "nom_tt_025C_5v00"
    bare.mkdir(parents=True)
    (bare / "sta.log").write_text("no check-types report here\n")
    assert _violators_in(tmp_path,
                         "design__max_fanout_violation__count") is None
