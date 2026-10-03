"""Cheap stages that may prune a candidate before signoff decides it.

Signoff costs 1.5-3.9 h a run here. An optimizer that sends every candidate
through it spends most of its budget confirming what a cheaper look would
have shown. A cheap stage is only worth having if it is RIGHT when it prunes:
a wrongly pruned design is never seen again. So each rule below rests on a
measurement over the 22 runs on disk (2026-09-11), and test_stages re-checks
each rule's PREMISE against every run present, not only the rule's output.

A screen keeps a candidate when data is missing -- the opposite of a gate. A
gate must prove a run good before accepting it; a screen must prove a
candidate bad before discarding it, and "not measured yet" proves nothing.

  stage       cost        prunes
  ----------  ----------  -------------------------------------------------
  model       seconds     a target utilisation at or above one that failed
                          to route at the same hart count on the same PDK
  synthesis   2-4 min     nothing that differs between physical knobs
  post-grt    18-33 min   negative setup or hold slack before detailed routing
  signoff     1.5-3.9 h   decides: the gates in harness.physical.ppa

WHAT WAS MEASURED, AND WHAT IT RULED OUT

  * Synthesis does not see the physical knobs. Eleven Block A runs spanning
    three clock periods (40/50/100 ns), three slew repair margins (10/32/45)
    and the NDR on and off all synthesised to 854,954 um2; only
    MAX_FANOUT_CONSTRAINT moved it. So
    synthesis runs once per RTL, not once per candidate, and cannot tell two
    knob settings apart. What it does give, for ANY design, is the final
    logic area to within SYNTH_TO_LOGIC of the synthesised area -- and the
    area model refuses everything but SERV-only designs. That makes it the
    cheap area check for STRUCTURE candidates, not for physical ones.

  * Pre-layout timing predicts nothing. Setup slack before placement was
    -146 to -195 ns on runs that closed at +0.09 to +21 ns: before repair,
    unbuffered high-fanout nets make every delay absurd. A timing screen at
    synthesis would have pruned every run that shipped.

  * After global routing, slack only gets worse. Final setup and hold slack
    were at or below the last STA before detailed routing on all 22 runs
    (setup by 0.01-6.86 ns). Nothing resizes after detailed routing, and real
    parasitics only add delay. Negative slack there is a failed gate later.

  * Slew and cap counts at that same point do NOT carry over: blockc_sdc had
    19 slew violations there and 0 at signoff (accepted), blocka_signoff 0
    there and 591 at signoff. They are checked against different limits. No
    screen uses them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from harness.physical.routability import (OBSERVATIONS_PDK,
                                          ROUTABILITY_OBSERVATIONS)

#: Final logic area / synthesised area: the lowest and highest ratio over the
#: 22 gf180mcu runs on disk (1.0879 and 1.1771), rounded outward. Measured on
#: one process only; another PDK's cells and repair behave differently.
SYNTH_TO_LOGIC: Tuple[float, float] = (1.087, 1.178)


@dataclass(frozen=True)
class Screen:
    stage: str
    keep: bool
    reason: str


def screen_model(harts: int, target_utilisation: float,
                 technology: Optional[str] = None) -> Screen:
    """Prune a target at or above one that has already failed to route.

    Only at the SAME hart count: the ceiling falls as designs grow, but a
    single failure does not say where it sits for a size nobody has routed.
    That case is kept and left to the routing guard, which watches the run.
    """
    if technology is not None:
        from harness.physical.technology import resolve
        tech = resolve(technology)
        if tech is None or tech.pdk != OBSERVATIONS_PDK:
            return Screen("model", True,
                          f"nothing has been routed on {technology}")
    failed = [o.target_utilisation for o in ROUTABILITY_OBSERVATIONS
              if not o.routed and o.serv_harts == harts]
    if failed and target_utilisation >= min(failed):
        return Screen("model", False,
                      f"{harts} harts failed to route at a {min(failed):.0%} "
                      f"target and {target_utilisation:.0%} is no less dense")
    return Screen("model", True,
                  f"no routing failure recorded at or below "
                  f"{target_utilisation:.0%} for {harts} harts")


def logic_area_band(synth_um2: float) -> Tuple[float, float]:
    """Where the final logic area will land, from the synthesised area."""
    lo, hi = SYNTH_TO_LOGIC
    return synth_um2 * lo, synth_um2 * hi


def _steps(run_dir: Path) -> List[Tuple[int, Path]]:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return []
    out = []
    for d in run_dir.iterdir():
        m = re.match(r"(\d+)-", d.name)
        if m and d.is_dir():
            out.append((int(m.group(1)), d))
    return sorted(out)


def _metrics(step: Path) -> Optional[Dict[str, Any]]:
    try:
        state = json.loads((step / "state_out.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return state.get("metrics") or {}


def synthesised_area(run_dir: Path) -> Optional[float]:
    for _, d in _steps(run_dir):
        if d.name.endswith("-yosys-synthesis"):
            v = (_metrics(d) or {}).get("design__instance__area")
            return float(v) if isinstance(v, (int, float)) else None
    return None


def post_grt_metrics(run_dir: Path
                     ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """(step, metrics) at the last STA before detailed routing.

    (None, None) until detailed routing has started. An earlier STA is no
    substitute: design repair after global routing still moves timing, and
    the premise was measured at this point and no other.
    """
    steps = _steps(run_dir)
    drt = next((n for n, d in steps
                if d.name.endswith("-openroad-detailedrouting")), None)
    if drt is None:
        return None, None
    sta = [d for n, d in steps if n < drt and "-openroad-stamidpnr" in d.name]
    if not sta:
        return None, None
    return sta[-1].name, _metrics(sta[-1])


def screen_post_grt(run_dir: Path) -> Screen:
    """Prune on negative setup or hold slack before detailed routing."""
    step, m = post_grt_metrics(run_dir)
    if step is None:
        return Screen("post-grt", True,
                      "detailed routing has not started: nothing to screen yet")
    if m is None:
        return Screen("post-grt", True, f"{step} has no metrics")
    bad = [f"{label} {v:+.3f} ns"
           for label, key in (("setup", "timing__setup__ws"),
                              ("hold", "timing__hold__ws"))
           for v in [m.get(key)]
           if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0]
    if bad:
        return Screen("post-grt", False,
                      f"{', '.join(bad)} at {step}. Slack has never improved "
                      "after this point (22 of 22 runs), so the timing gate "
                      "would fail at signoff")
    return Screen("post-grt", True,
                  f"setup and hold slack non-negative at {step}")
