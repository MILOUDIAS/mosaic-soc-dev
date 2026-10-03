"""Back off to a looser die when routing will not converge.

`routability.assess` decides that a run is not going to make it. This decides
what to do next: which utilisation to try, and when to stop trying.

WHY A LADDER AND NOT A SEARCH
-----------------------------
The goal is a signoff-ready GDS, not the exact routability ceiling. When a
size has a demonstrated-clean target the first retry jumps straight to it
rather than stepping down 5 points at a time -- Block C's ceiling is somewhere
in (0.65, 0.75], and an intermediate attempt at 0.70 costs hours to maybe
learn something the plateau guard would have to catch anyway. Finding the true
ceiling is a separate experiment, run deliberately, not something to do by
accident inside a build.

Past the demonstrated range the ladder steps down by 0.05 to a floor of 0.50.
The floor is not a physical limit; it is the point at which the die has grown
so far past anything measured that a human should look at the design instead
of watching the machine try again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from harness.physical.routability import recommended_utilisation

STEP = 0.05
FLOOR = 0.50
DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class RetryDecision:
    """What to do after a run failed to converge."""

    retry: bool
    utilisation: Optional[float]
    reason: str


def retry_ladder(harts: int, start: float, *,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> List[float]:
    """The utilisations to try, in order, beginning with `start`.

    `max_attempts` counts the first attempt, so the default of 3 means one
    initial run and at most two retries.
    """
    ladder = [round(start, 4)]
    demonstrated = recommended_utilisation(harts)

    while len(ladder) < max_attempts:
        current = ladder[-1]
        nxt = _next_rung(demonstrated, current)
        if nxt is None:
            break
        ladder.append(nxt)
    return ladder


def _next_rung(demonstrated, current: float) -> Optional[float]:
    """One rung down, or None if there is nowhere useful left to go.

    Jumping to a demonstrated value is only worth a whole run if it is at
    least a full step below where we are. Without that guard, 3 harts failing
    at 80% would "retry" at 79.2% -- a 0.8-point change that costs hours and
    tests nothing.
    """
    if (demonstrated.validated
            and demonstrated.utilisation <= current - STEP + 1e-9):
        nxt = demonstrated.utilisation
    else:
        # Not "nothing is demonstrated": a demonstrated value may exist and be
        # too CLOSE to be worth a run. 4 harts are demonstrated at 74% and a
        # failure at 75% still steps to 70%, because retrying one point down
        # tests nothing.
        nxt = current - STEP
    nxt = round(nxt, 4)
    if nxt < FLOOR - 1e-9 or nxt >= current:
        return None
    return nxt


def next_utilisation(harts: int, current: float, attempts_so_far: int, *,
                     max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> RetryDecision:
    """Decide the next utilisation after `current` failed to converge."""
    if attempts_so_far >= max_attempts:
        return RetryDecision(
            False, None,
            f"{attempts_so_far} attempts is the limit; the design is not "
            "routing at any density this ladder will try. Look at the design")

    demonstrated = recommended_utilisation(harts)
    nxt = _next_rung(demonstrated, current)
    if nxt is None:
        return RetryDecision(
            False, None,
            f"no looser target left above the {FLOOR:.0%} floor "
            f"(current {current:.0%})")

    if demonstrated.validated and abs(nxt - demonstrated.utilisation) < 1e-9:
        why = (f"{demonstrated.utilisation:.0%} is demonstrated clean at "
               f"{harts} harts, so go straight there rather than stepping")
    elif demonstrated.validated and current <= demonstrated.utilisation + 1e-9:
        why = (f"stepping down {STEP:.0%}: {current:.0%} is at or below the "
               f"{demonstrated.utilisation:.0%} that has routed at {harts} "
               "harts, so this design is denser than that measurement covers")
    elif demonstrated.validated:
        # A demonstrated value exists and is too CLOSE to be worth a run: 4
        # harts demonstrated at 74% with a failure at 75% steps to 70%, because
        # retrying one point down tests nothing.
        why = (f"stepping down {STEP:.0%} from {current:.0%}; the "
               f"{demonstrated.utilisation:.0%} demonstrated at {harts} harts "
               "is too close to be worth a run")
    else:
        why = (f"stepping down {STEP:.0%} from {current:.0%}; nothing is "
               f"demonstrated at {harts} harts")
    return RetryDecision(True, nxt, why)


def describe(harts: int, start: float, *,
             max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> Tuple[str, List[float]]:
    """A human-readable plan, for logging before a long run starts."""
    ladder = retry_ladder(harts, start, max_attempts=max_attempts)
    text = " -> ".join(f"{u:.0%}" for u in ladder)
    return text, ladder
