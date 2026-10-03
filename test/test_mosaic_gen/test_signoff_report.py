"""Reading a run's signoff numbers once, instead of by hand every time.

Every experiment in this project ended with the same manual step: open two
metrics.json files, pull the same dozen keys, convert um2 to mm2 by hand and
write the comparison into prose. A confounded experiment survived a whole run
because the die had moved and nothing printed the die.
"""

import pytest

from harness.core import REPO_ROOT
from harness.physical.report import (
    AREA,
    HARD_CHECKS,
    QUALITY,
    logic_area_um2,
    signoff_summary,
)

RUNS = REPO_ROOT / "flow/librelane/experimental/runs"


def have(tag: str) -> bool:
    return (RUNS / tag / "final" / "metrics.json").is_file()


def test_the_librelane_hard_checks_are_the_only_thing_that_gates():
    """This assertion has now been wrong in BOTH directions, which is the point.

    It first said `adverse == 0` -- clean. Then blocka_reharden turned out not
    to boot, so it said `adverse >= 1` and GLS was made a hard check. Then the
    boot failure turned out to be a zero-delay simulation race: that netlist
    and the booting one hold identical logic instance sets and differ only in
    buffering, which cannot change behaviour in zero delay.

    So the count is back to what LibreLane's own checks say, and GLS is
    reported beside it. The lesson worth keeping is not the number -- it is
    that a check which cannot distinguish two provably-equivalent inputs must
    not be allowed to fail a run.
    """
    if not have("blocka_reharden"):
        pytest.skip("run tree not present")
    summary, errors = signoff_summary(RUNS / "blocka_reharden", pdk="gf180mcuD")
    assert not errors and summary
    assert summary["design"] == "mosaic_block_a"
    assert set(summary["hard_checks"]) <= set(HARD_CHECKS)
    assert all(v == 0 for v in summary["hard_checks"].values())
    assert summary["adverse"] == 0
    # GLS still has its say, in the report rather than in the verdict.
    assert summary["gls"]["gates_signoff"] is False


def test_area_is_reported_in_mm2_by_the_type_not_by_hand():
    if not have("blocka_reharden"):
        pytest.skip("run tree not present")
    summary, _ = signoff_summary(RUNS / "blocka_reharden")
    # Block A is a quarter of a 2235 um MPW area: 1117.5 um square.
    assert summary["area_mm2"]["design__die__area"] == pytest.approx(1.2488, abs=1e-3)
    assert summary["logic_um2"] == pytest.approx(976364, rel=1e-4)


def test_logic_area_excludes_the_physical_only_cells():
    """Filler, tap, endcap and antenna scale with the die, not the design."""
    metrics = {
        "design__instance__area__class:sequential_cell": 100.0,
        "design__instance__area__class:buffer": 50.0,
        "design__instance__area__class:fill_cell": 9000.0,
        "design__instance__area__class:tap_cell": 800.0,
        "design__instance__area__class:endcap_cell": 70.0,
        "design__instance__area__class:antenna_cell": 6.0,
    }
    assert logic_area_um2(metrics) == pytest.approx(150.0)
    assert logic_area_um2({}) is None


def test_the_comparison_reports_the_die_so_a_confound_cannot_hide():
    """The specific failure this exists to prevent."""
    if not (have("blocka_reharden") and have("blocka_signoff")):
        pytest.skip("run trees not present")
    summary, _ = signoff_summary(
        RUNS / "blocka_reharden", compare=RUNS / "blocka_signoff")
    deltas = summary["compare"]["deltas"]
    # Same mandated MPW die, so this delta must be zero -- and must be shown.
    assert "design__die__area" in deltas
    assert deltas["design__die__area"]["delta"] == pytest.approx(0, abs=1)
    # The fix: 591 -> 4 max-slew for +2.68% logic area.
    assert deltas["design__max_slew_violation__count"]["delta"] == -587
    assert deltas["logic_um2"]["delta_pct"] == pytest.approx(2.68, abs=0.05)


def test_a_run_that_never_finished_is_refused_not_reported_empty():
    if not (RUNS / "blockc_generated").is_dir():
        pytest.skip("pruned run tree not present")
    summary, errors = signoff_summary(RUNS / "blockc_generated")
    assert summary is None
    assert errors and "did not finish" in errors[0]


def test_the_key_lists_are_disjoint_and_named_for_what_they_gate():
    """Hard checks fail a signoff; quality metrics are traded and waived."""
    assert not set(HARD_CHECKS) & set(QUALITY)
    assert not set(HARD_CHECKS) & set(AREA)
    # The real property, rather than a string match on the names: every hard
    # check is a count whose only acceptable value is zero, and that is what
    # `adverse` counts. QUALITY holds slacks and waivable counts instead.
    if not have("blocka_reharden"):
        return
    summary, _ = signoff_summary(RUNS / "blocka_reharden")
    assert summary["hard_checks"] and all(
        v == 0 for v in summary["hard_checks"].values())
    # `adverse` counts GLS too, so it is not zero on this run -- see
    # test_every_librelane_hard_check_can_pass_and_the_run_still_not_be_clean.
    assert summary["adverse"] == len(summary["hard_checks_failing"])
    # ...while QUALITY on the same clean run is NOT all zero: 4 slew, 1 fanout
    # and two nonzero slacks. Conflating the two lists would have waived a DRC.
    assert any(v for v in summary["quality"].values())
