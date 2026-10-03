"""Where a LibreLane run is, from its files alone.

A multi-hour hardening run has no progress API. What it has is `flow.log`,
which LibreLane writes one line per step start ("Running 'Step.Id' at
'<run>/NN-name'…", with a unicode ellipsis) and ends with "Flow complete." on
success. A FAILED run leaves no marker there: LibreLane prints the error to
its console only (run_signoff.sh tees that to /tmp/ll_<tag>.log). So a run
without "Flow complete." is either still going or stopped, and only activity
tells the two apart. Detailed routing can go hours without a flow.log line, so
activity is the newest mtime of flow.log OR anything in the current step's
directory, never flow.log alone.

The states, in the order they are decided:
  complete  "Flow complete." is in flow.log
  aborted   the routing guard left `.plateau_abort` (run_signoff.sh)
  running   something in the run changed within `stale_after_s`
  stopped   none of the above: failed, killed, or the machine went down.
            Never read as a pass.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

_RUNNING = re.compile(r"^Running '([^']+)' at '([^']+)'", re.M)
STALE_AFTER_S = 20 * 60


def _newest_mtime(path: Path) -> float:
    try:
        newest = path.stat().st_mtime
    except OSError:
        return 0.0
    if path.is_dir():
        for child in path.iterdir():
            try:
                newest = max(newest, child.stat().st_mtime)
            except OSError:
                pass
    return newest


def expected_steps(run_dir: Path) -> Optional[int]:
    """Step count of the newest COMPLETE sibling run: the flow's length on
    this machine, measured rather than assumed."""
    best = None
    for log in run_dir.parent.glob("*/flow.log"):
        if log.parent == run_dir:
            continue
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue
        if "Flow complete." in text:
            mtime = log.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, len(_RUNNING.findall(text)))
    return best[1] if best else None


def run_progress(run_dir: Path, *, now: Optional[float] = None,
                 stale_after_s: float = STALE_AFTER_S) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    now = time.time() if now is None else now
    log = run_dir / "flow.log"
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return {"run": run_dir.name, "state": "not_started", "step": None,
                "reason": "no flow.log yet"}
    steps = _RUNNING.findall(text)
    step_id, step_path = steps[-1] if steps else (None, None)
    step_dir = run_dir / Path(step_path).name if step_path else None
    number = None
    if step_dir is not None:
        m = re.match(r"(\d+)-", step_dir.name)
        number = int(m.group(1)) if m else None
    activity = max(_newest_mtime(log), _newest_mtime(step_dir) if step_dir else 0.0)
    idle = max(0.0, now - activity)

    if "Flow complete." in text:
        state, reason = "complete", "Flow complete."
    elif (run_dir / ".plateau_abort").exists():
        state, reason = "aborted", "routing guard: detailed routing plateaued"
    elif idle <= stale_after_s:
        state, reason = "running", f"active {idle / 60:.0f} min ago"
    else:
        state, reason = "stopped", (
            f"no activity for {idle / 3600:.1f} h and no 'Flow complete.': "
            "failed or killed; the console log (/tmp/ll_<tag>.log) has the error")

    out: Dict[str, Any] = {
        "run": run_dir.name, "state": state, "reason": reason,
        "step": step_id, "step_dir": step_dir.name if step_dir else None,
        "step_number": number, "steps_started": len(steps),
        "expected_steps": expected_steps(run_dir), "idle_s": round(idle),
    }
    drt = sorted(run_dir.glob("*detailedrouting/*.log"))
    if drt:
        from .routability import assess, first_plateau, parse_drt_passes
        passes = parse_drt_passes(drt[-1].read_text(errors="replace"))
        current = passes[-1] if passes else []
        verdict = assess(current)
        out["routing"] = {"passes": len(passes), "trajectory": current,
                          "state": verdict.state, "reason": verdict.reason,
                          "first_plateau_iteration": first_plateau(current)}
    return out
