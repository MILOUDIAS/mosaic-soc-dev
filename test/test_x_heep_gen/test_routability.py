"""The plateau detector, held to the five detailed-routing runs we have.

The guard has exactly one job: abort the run that spent eleven hours not
converging, and never touch the four that came out clean. Both halves matter
equally — a detector that kills healthy runs is worse than no detector, and the
first version of this module did precisely that (see
`test_a_repair_pass_restarting_is_not_a_plateau`).

These tests read the real logs where they are present and fall back to the
recorded trajectories where they are not, so the suite still means something on
a fresh clone with no `runs/` directory.
"""

import pytest

from harness.core import REPO_ROOT
from harness.physical.routability import (
    CONVERGED,
    CONVERGING,
    PLATEAUED,
    PLATEAU_MIN_IMPROVEMENT,
    PLATEAU_WINDOW,
    TOO_EARLY,
    assess,
    assess_log,
    first_plateau,
    parse_drt_passes,
    parse_drt_trajectory,
)

# Transcribed from the logs, first pass only. Kept here so the properties below
# are checkable without the multi-gigabyte run trees.
BLOCK_B_GENERATED = [30376, 14024, 13485, 2361, 1111, 545, 441, 414, 395, 249,
                     97, 55, 15, 1, 1, 1, 0]
BLOCK_C_U65 = [26579, 11986, 10827, 620, 55, 17, 15, 13, 13, 1, 0]
BLOCK_C_FAILED = [36170, 18603, 17601, 5125, 3823, 3609, 3596, 3562, 3546,
                  3546, 3320, 3247, 3059, 2952, 2926, 2908, 2906, 8552, 4695,
                  3847]
# The whole thing, all 40 iterations before the session died. Note the tail:
# 2000 -> 1650 -> 1310 -> 1310 -> 1183 -> 1049 is a 47.5% improvement over the
# last five, which is why a single check at the end calls this "converging".
BLOCK_C_FAILED_FULL = BLOCK_C_FAILED + [
    3672, 3220, 3042, 3042, 2434, 5088, 2517, 2351, 2206, 1799, 1799, 1538,
    1508, 3920, 2000, 1650, 1310, 1310, 1183, 1049]

HEALTHY = {"blockb_generated": BLOCK_B_GENERATED, "blockc_u65": BLOCK_C_U65}


def log_for(tag: str):
    hits = list((REPO_ROOT / "flow/librelane/experimental/runs").glob(
        f"{tag}/*detailedrouting/*.log"))
    return hits[0] if hits else None


# ── the two halves of the job ────────────────────────────────────────

@pytest.mark.parametrize("tag,trajectory", sorted(HEALTHY.items()))
def test_a_healthy_run_is_never_aborted(tag, trajectory):
    """Replay iteration by iteration: no prefix may ever say abort."""
    for n in range(1, len(trajectory) + 1):
        verdict = assess(trajectory[:n])
        assert not verdict.should_abort, (
            f"{tag} would have been killed at iteration {n - 1}: "
            f"{verdict.reason}")
    assert assess(trajectory).state == CONVERGED


def test_the_eleven_hour_failure_is_caught_early():
    """And caught early enough to be worth having.

    Iteration 9 is 1.15 h into a detailed routing that ran 10.66 h without
    converging — a 9.3x reduction in the cost of finding out.
    """
    first_abort = next(
        n - 1 for n in range(1, len(BLOCK_C_FAILED) + 1)
        if assess(BLOCK_C_FAILED[:n]).should_abort
    )
    assert first_abort == 9

    # The verdict AT the abort, not at the end of the log: by iteration 17 the
    # rip-up has fired and the reason becomes "rose 31.5%", which is true but
    # is not what the guard acted on.
    verdict = assess(BLOCK_C_FAILED[:first_abort + 1])
    assert verdict.state == PLATEAUED
    assert "fell by only 7.2%" in verdict.reason
    assert "3,823 -> 3,546" in verdict.reason


def test_the_margin_between_healthy_and_failed_is_wide():
    """The threshold is a judgement call; this records how much room it has.

    Worst 5-iteration improvement: 77.6% and 98.0% on the healthy runs against
    7.2% on the failure. If a future run narrows this gap, the threshold needs
    re-deriving rather than nudging.
    """
    def worst(traj):
        return min((traj[i] - traj[i + PLATEAU_WINDOW]) / traj[i]
                   for i in range(len(traj) - PLATEAU_WINDOW)
                   if traj[i])

    for trajectory in HEALTHY.values():
        assert worst(trajectory) > PLATEAU_MIN_IMPROVEMENT * 2
    assert worst(BLOCK_C_FAILED) < PLATEAU_MIN_IMPROVEMENT / 2


# ── the false positive that shipped, and must not return ─────────────

def test_a_repair_pass_restarting_is_not_a_plateau():
    """The flow runs DRT more than once; each pass restarts its counter.

    Block C @65% goes 26579 -> ... -> 0 and then a repair pass STARTS at 441.
    Read as one sequence that is a 2840% rise, and the first version of this
    module duly condemned a run that finished clean.
    """
    log = (
        "[INFO DRT-0195] Start 0th optimization iteration.\n"
        + "".join(f"[INFO DRT-0199]   Number of violations = {v}.\n"
                  for v in BLOCK_C_U65)
        + "[INFO DRT-0195] Start 0th optimization iteration.\n"
        + "".join(f"[INFO DRT-0199]   Number of violations = {v}.\n"
                  for v in (441, 71, 53, 0))
    )
    passes = parse_drt_passes(log)
    assert [p[0] for p in passes] == [26579, 441]
    assert parse_drt_trajectory(log) == [441, 71, 53, 0]
    assert not assess_log(log).should_abort
    assert assess_log(log).state == CONVERGED


# ── behaviour at the edges ───────────────────────────────────────────

def test_no_verdict_before_there_is_evidence():
    assert assess([]).state == TOO_EARLY
    for n in range(1, PLATEAU_WINDOW + 1):
        assert assess([5000] * n).state == TOO_EARLY, (
            "a flat trajectory shorter than the window is not yet a plateau")


def test_a_dead_flat_trajectory_above_the_floor_is_a_plateau():
    assert assess([5000] * (PLATEAU_WINDOW + 1)).state == PLATEAUED


def test_stalling_near_zero_is_finishing_not_stuck():
    """Block B sat at 1, 1, 1 before its final 0 — a 0% improvement window."""
    assert assess([30000] + [1] * PLATEAU_WINDOW * 2).state == CONVERGING
    assert assess([30000] + [1] * PLATEAU_WINDOW * 2 + [0]).state == CONVERGED


def test_a_rise_needs_no_special_case():
    """Negative improvement is below any positive threshold."""
    verdict = assess([10000, 9000, 8000, 7000, 6000, 5000, 20000])
    assert verdict.state == PLATEAUED
    assert "rose" in verdict.reason


def test_zero_violations_wins_over_a_flat_prefix():
    assert assess([9000, 9000, 9000, 9000, 9000, 9000, 0]).state == CONVERGED


def test_only_a_plateau_licenses_a_kill():
    for state in (TOO_EARLY, CONVERGING, CONVERGED):
        assert state != PLATEAUED
    assert not assess([]).should_abort
    assert not assess(BLOCK_C_U65).should_abort
    assert assess(BLOCK_C_FAILED).should_abort


# ── assessing after the fact is a different question ─────────────────

def test_a_single_check_at_the_end_misses_the_failure():
    """Which is why `first_plateau` exists, and why `watch` is sticky.

    Block C's failed run plateaued at iteration 9, drifted for thirty more,
    and by the end its last five iterations had improved 47.5%. Ask it "how
    are you doing?" at that moment and it says "converging" — about a run that
    never converged.
    """
    tail_verdict = assess(BLOCK_C_FAILED_FULL)
    assert tail_verdict.state == CONVERGING
    assert not tail_verdict.should_abort

    assert first_plateau(BLOCK_C_FAILED_FULL) == 9


@pytest.mark.parametrize("tag,trajectory", sorted(HEALTHY.items()))
def test_no_healthy_run_has_a_plateau_anywhere_in_it(tag, trajectory):
    assert first_plateau(trajectory) is None


def test_first_plateau_is_none_when_nothing_went_wrong():
    assert first_plateau([]) is None
    assert first_plateau([100, 50, 20, 5, 0]) is None


# ── parsing, against the real logs when they are here ────────────────

@pytest.mark.parametrize("tag,passes,first,last", [
    ("blocka_signoff", 3, 19564, 0),
    ("blockb_signoff", 2, 24097, 0),
    ("blockb_generated", 2, 30376, 0),
    ("blockc_generated", 1, 36170, 1049),
    ("blockc_u65", 3, 26579, 0),
])
def test_parsing_matches_the_runs_on_disk(tag, passes, first, last):
    log = log_for(tag)
    if log is None:
        pytest.skip(f"{tag} run tree not present")
    parsed = parse_drt_passes(log.read_text())
    assert len(parsed) == passes
    assert parsed[0][0] == first
    assert parsed[-1][-1] == last


def test_every_run_on_disk_gets_the_right_verdict():
    """The whole point, stated once against whatever is actually here."""
    expected = {
        "blocka_signoff": False, "blockb_signoff": False,
        "blockb_generated": False, "blockc_u65": False,
        "blockc_generated": True,
    }
    seen = 0
    for tag, should_abort in expected.items():
        log = log_for(tag)
        if log is None:
            continue
        seen += 1
        aborted = any(
            assess(p[:n]).should_abort
            for p in parse_drt_passes(log.read_text())
            for n in range(1, len(p) + 1)
        )
        assert aborted is should_abort, f"{tag}: expected abort={should_abort}"
    if not seen:
        pytest.skip("no run trees present")
