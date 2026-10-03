"""Improve a design's PPA with no person and no model in the loop.

A line search over the physical knobs, one setting per run. It is the
margin decision of 2026-09-10 -- five runs, ~15 h, and a two-variable error
at the end -- written down so a machine runs it, and runs it correctly.

WHY NOT SUCCESSIVE HALVING. The board suggested it, and it needs many
candidates evaluated side by side at low cost. Here a run takes 1.5-3.9 h,
the 11 GB machine holds one at a time, and the budget is a handful of runs.
The cheap stages cannot rank knob settings either (stages.py: synthesis
gives every candidate the same netlist). So the search is sequential and
every move is small.

EVERY MOVE IS ONE SETTING. A candidate is the incumbent with one knob
changed, so each run is a single-variable experiment and the ledger can name
its cause. If anything else moved -- the RTL bundle, because a generator
source was edited mid-session -- the run measures two things at once, and
the search stops rather than build on it.

THE PROCEDURE. Measure the config as it stands (the baseline). Then walk
each knob's values in the order given, least to most aggressive: a value
that improves on the incumbent becomes the incumbent and the walk goes on;
the first value that fails a gate, is pruned, or does not improve ends that
knob. "20 MHz failed timing" is taken to mean 25 MHz would too. One pass.

BETTER means what harness.physical.ppa says: every gate passed, a positive
score, and not a score decided by the default-toggle energy proxy -- the
trap the first real comparison found.

WHAT IT MAY TOUCH: the knobs in KNOBS, each through the path run_signoff.sh
already uses to derive a config. Nothing here can set an arbitrary LibreLane
key, so nothing here can switch a check off.

A SCREEN MAY END A RUN EARLY. The poll applies the post-global-routing
timing screen and kills the run's process group when it prunes: a clock that
cannot close is known at ~25 min instead of ~3 h.

RESUMABLE. Every launch and result is appended to a JSONL journal. Re-run
the same command after a crash: finished candidates are read back, a run
still going is adopted by its process group, and nothing is repeated.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Callable, Dict, FrozenSet, List, Optional, Sequence,
                    Tuple)

from harness.physical.ppa import PPA, compare, evaluate
from harness.physical.stages import screen_post_grt

#: knob -> (the run_signoff.sh variable that sets it, the LibreLane keys it
#: owns). This is the whole of what the optimizer can change.
#:
#: A knob owns a SET of keys because one knob can legitimately move more than
#: one: utilisation is sized absolutely, so `derive_floorplan` emits both
#: `DIE_AREA` and `CORE_AREA`. The single-variable rule is about the knob, not
#: the key count -- but it stays strict, because `moved` must be a subset of
#: what the knob owns, so anything else moving still stops the search.
KNOBS: Dict[str, Tuple[str, FrozenSet[str]]] = {
    "clock_period_ns": ("MOSAIC_HARDEN_CLOCK_NS", frozenset({"CLOCK_PERIOD"})),
    "repair_margin_pct": ("MOSAIC_HARDEN_MARGIN",
                          frozenset({"GRT_DESIGN_REPAIR_MAX_SLEW_PCT"})),
    "target_utilisation": ("MOSAIC_HARDEN_UTIL",
                           frozenset({"DIE_AREA", "CORE_AREA"})),
}

#: Knobs whose value is a fraction of one, not a count or a period.
_FRACTIONS = frozenset({"target_utilisation"})

#: One signoff run's footprint on disk (blockb_m45: 3.9 GB).
RUN_GB = 4.0

#: Inherited variables that would change what a launch does behind its back:
#: watch mode setsids librelane out of reach of a prune's kill, and the rest
#: would silently override the die, the manifest or the resources.
_SCRUBBED = ("MOSAIC_WATCH_ROUTING", "MOSAIC_HARDEN_UTIL", "MOSAIC_MANIFEST",
             "MOSAIC_RESOURCE_CONFIG") + tuple(v for v, _ in KNOBS.values())

Space = Sequence[Tuple[str, Sequence[float]]]


class Stop(Exception):
    """The search cannot go on; the message says why."""


def parse_knob(text: str) -> Tuple[str, List[float]]:
    """'clock_period_ns=80,60,50' -> ('clock_period_ns', [80.0, 60.0, 50.0])."""
    name, _, values = text.partition("=")
    if name not in KNOBS:
        raise ValueError(f"{name!r} is not a knob the optimizer may set; "
                         f"it may set {sorted(KNOBS)}")
    try:
        out = [float(v) for v in values.split(",") if v.strip()]
    except ValueError:
        raise ValueError(f"{text!r}: values must be numbers") from None
    if not out or any(v <= 0 for v in out):
        raise ValueError(f"{text!r}: give one or more positive values")
    if name == "repair_margin_pct" and any(v != int(v) for v in out):
        raise ValueError(f"{text!r}: a repair margin is a whole percentage")
    if name in _FRACTIONS and any(v >= 1 for v in out):
        # 75 for 0.75 is the obvious slip, and it would ask for a die 75x
        # denser than the cells rather than a quarter empty.
        raise ValueError(f"{text!r}: {name} is a fraction of one "
                         "(0.78, not 78)")
    return name, out


def plan(space: Space) -> List[Dict[str, float]]:
    """The longest possible walk: the baseline, then every value as if each
    one improved. A real search stops at the first value that does not."""
    seq, incumbent = [{}], {}
    for knob, values in space:
        for v in values:
            incumbent = {**incumbent, knob: v}
            seq.append(incumbent)
    return seq


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


@dataclass
class Run:
    """A launched run: started by this process, or adopted after a restart."""
    pgid: int
    proc: Optional[subprocess.Popen] = None

    def alive(self) -> bool:
        if self.proc is not None:
            self.proc.poll()        # reap it, or a zombie leader looks alive
        if self.pgid <= 1:
            return False            # never signal init or our own group
        try:
            os.killpg(self.pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def kill(self, grace_s: float = 60.0) -> None:
        """TERM the whole group, then KILL what is left. An OpenROAD that
        outlived its run would share 11 GB with the next one."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if not self.alive():
                return
            try:
                os.killpg(self.pgid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + grace_s
            while self.alive() and time.monotonic() < deadline:
                time.sleep(0.5)


def signoff_launcher(soc_config: Path, design: str, repo_root: Path, *,
                     log_dir: Path, resource_config: Optional[str] = None,
                     manifest: Optional[str] = None
                     ) -> Callable[[str, Dict[str, float]], Run]:
    """Start run_signoff.sh for one candidate, in a session of its own.

    `manifest` pins the RTL bundle for the whole series, and a search wants
    that: without it every run resolves the bundle from today's generator
    sources, so editing a flow script mid-search moves the RTL identity and
    the single-setting check stops everything. Pinning also makes the series
    an experiment on one knob with the RTL held fixed, which is the point.
    """
    script = repo_root / "flow/librelane/experimental/run_signoff.sh"

    def launch(tag: str, overrides: Dict[str, float]) -> Run:
        env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED}
        env.update(MOSAIC_CFG=str(soc_config),
                   MOSAIC_HARDEN_FROM_SOC=str(soc_config),
                   MOSAIC_HARDEN_DESIGN=design)
        if resource_config:
            env["MOSAIC_RESOURCE_CONFIG"] = resource_config
        if manifest:
            env["MOSAIC_MANIFEST"] = manifest
        for knob, value in overrides.items():
            env[KNOBS[knob][0]] = f"{value:g}"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f"{tag}.log", "w") as log:
            proc = subprocess.Popen(
                [str(script), tag], cwd=script.parent, env=env, stdout=log,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True)
        return Run(proc.pid, proc)

    return launch


def _key(overrides: Dict[str, float]) -> str:
    return json.dumps(overrides, sort_keys=True)


def _read(journal: Path) -> List[Dict[str, Any]]:
    if not journal.is_file():
        return []
    return [json.loads(line) for line in journal.read_text().splitlines()
            if line.strip()]


def launches_recorded(journal: Path) -> int:
    """How many runs this journal has already launched.

    A resumed search spends only what is left, and a caller pricing the disk a
    run costs needs that number: counting the whole plan again refused a
    one-run continuation for wanting 20 GB when it needed 4.
    """
    return sum(r.get("event") == "launch" for r in _read(journal))


def _routing_plateau(run_dir: Path) -> Optional[str]:
    """Detailed routing that has stopped converging, by the rule the routing
    guard already uses: violations must fall 25% over five iterations while
    above the floor.

    Block C spent ELEVEN HOURS not converging once, and the guard would have
    called it at 1.15 h. It is opt-in for a hand-launched run, where a false
    positive costs an afternoon. Here it is on: these runs are unattended, the
    verdict is sticky (`first_plateau` replays prefixes), and the alternative
    is a search that spends its whole budget on one doomed candidate.
    """
    from harness.physical.routability import (assess, first_plateau,
                                              parse_drt_passes)
    logs = sorted(Path(run_dir).glob("*detailedrouting/*.log"))
    if not logs:
        return None
    passes = parse_drt_passes(logs[-1].read_text())
    current = passes[-1] if passes else []
    if not current:
        return None
    verdict = assess(current)
    at = first_plateau(current)
    if not (verdict.should_abort or at is not None):
        return None
    return ("detailed routing plateaued"
            + (f" at iteration {at}" if at is not None else "")
            + f": {verdict.reason}")


def _watch(run: Any, run_dir: Path, poll_s: float,
           sleep: Callable[[float], None],
           repo_root: Optional[Path]) -> Dict[str, str]:
    while run.alive():
        screen = screen_post_grt(run_dir)
        if not screen.keep:
            run.kill()
            return {"status": "pruned", "reason": screen.reason}
        stalled = _routing_plateau(run_dir)
        if stalled:
            run.kill()
            return {"status": "pruned", "reason": stalled}
        sleep(poll_s)
    ppa, errors = evaluate(run_dir, repo_root=repo_root)
    if ppa is None:
        return {"status": "failed", "reason": "; ".join(errors) or "no metrics"}
    return {"status": "accepted" if ppa.accepted else "rejected",
            "reason": ", ".join(ppa.failing)}


def _improves(cmp: Dict[str, Any], ppa: PPA) -> bool:
    # ponytail: any positive score counts. The run-to-run noise floor is
    # unmeasured -- no repeat run exists -- so once one does, require the
    # score to clear it.
    proxy = ppa.power_basis != "workload" and cmp["decided_by"] == "energy_nj"
    return cmp["score"] > 0 and not proxy


def optimize(space: Space, *, journal: Path, runs_root: Path, max_runs: int,
             launch: Callable[[str, Dict[str, float]], Any],
             poll_s: float = 300.0,
             sleep: Callable[[float], None] = time.sleep,
             repo_root: Optional[Path] = None,
             weights: Optional[Dict[str, float]] = None,
             prescreen: Optional[Callable[[Dict[str, float]],
                                          Optional[str]]] = None
             ) -> Dict[str, Any]:
    """Run the line search. Returns the incumbent and why the search ended.

    `weights` is passed to `compare`, and a density walk needs it. Equal
    weights average a die saving against three other terms: the measured
    Block B density pair gives up 5.7% of its die for slightly worse cell area
    and energy, which lands at +0.28% overall -- a real gain thin enough that
    noise in another axis would read as a regression and end the knob. Say what
    the search is for.

    `prescreen` refuses a candidate before it costs anything. The cheap stages
    exist for this (`stages.screen_model` prices a density against the routing
    failures already recorded) and the search was not consulting them: a
    utilisation at or above one that already failed to route would have been
    launched and spent 2-4 h re-proving it. A refusal ends that knob, on the
    same monotonicity the rest of the walk assumes -- denser only gets harder.
    """

    records = _read(journal)
    prefix = f"opt_{journal.stem}"
    launches = sum(r["event"] == "launch" for r in records)

    def find(event: str, key: str) -> Optional[Dict[str, Any]]:
        return next((r for r in records
                     if r["event"] == event and r["key"] == key), None)

    def record(entry: Dict[str, Any]) -> None:
        # When each launch and result happened. The journal is an append-only
        # log, not the content-addressed store, so a stamp inside a record
        # costs nothing and is the only way to say how long a candidate took
        # without reading run-directory mtimes.
        from datetime import datetime, timezone
        entry.setdefault("at", datetime.now(timezone.utc)
                         .isoformat(timespec="seconds"))
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        records.append(entry)

    def measure(overrides: Dict[str, float]
                ) -> Tuple[Dict[str, Any], Optional[PPA]]:
        nonlocal launches
        key = _key(overrides)
        result = find("result", key)
        if result is None:
            launched = find("launch", key)
            if launched is not None:
                # Adopted after a restart. A process group recorded before a
                # reboot is not this run, whatever now holds that number.
                same_boot = launched.get("boot") == _boot_id()
                run: Any = Run(launched["pgid"] if same_boot else 0)
            else:
                if launches >= max_runs:
                    raise Stop(f"budget spent: {max_runs} run(s) launched")
                tag = f"{prefix}_{launches:02d}"
                run = launch(tag, overrides)
                launches += 1
                launched = {"event": "launch", "key": key, "tag": tag,
                            "overrides": overrides, "pgid": run.pgid,
                            "boot": _boot_id()}
                record(launched)
            result = {"event": "result", "key": key, "tag": launched["tag"],
                      **_watch(run, runs_root / launched["tag"], poll_s,
                               sleep, repo_root)}
            record(result)
        if result["status"] == "failed":
            raise Stop(f"{result['tag']} ({overrides or 'the baseline'}) "
                       f"produced no result: {result['reason']}")
        ppa = None
        if result["status"] != "pruned":
            ppa, errors = evaluate(runs_root / result["tag"],
                                   repo_root=repo_root)
            if ppa is None:
                raise Stop(f"{result['tag']} no longer has metrics: {errors}")
        return result, ppa

    history: List[Dict[str, Any]] = []
    incumbent: Dict[str, float] = {}
    inc: Optional[PPA] = None
    try:
        result, inc = measure({})
        if result["status"] != "accepted":
            raise Stop(f"the baseline is {result['status']} "
                       f"({result['reason']}); a search needs an accepted "
                       "starting point")
        assert inc is not None          # accepted, so it was measured
        for knob, values in space:
            keys = KNOBS[knob][1]
            for value in values:
                # Skipping a value the incumbent already runs at is only
                # meaningful when the knob's one key holds the knob's own
                # value. Utilisation's keys hold a derived die rectangle, so
                # there is nothing to compare against and the value is run.
                if (len(keys) == 1
                        and (inc.knobs or {}).get(next(iter(keys))) == value):
                    continue            # the incumbent already runs at it
                cand = {**incumbent, knob: value}
                refused = prescreen(cand) if prescreen else None
                if refused:
                    history.append({"overrides": cand, "tag": None,
                                    "status": "screened", "reason": refused,
                                    "kept": False})
                    break       # a bolder value is refused for the same reason
                result, ppa = measure(cand)
                step: Dict[str, Any] = {
                    "overrides": cand, "tag": result["tag"],
                    "status": result["status"], "reason": result["reason"],
                    "kept": False}
                history.append(step)
                if ppa is None or not ppa.accepted:
                    break   # pruned or rejected: a bolder value will be too
                cmp, refusals = compare(ppa, inc, weights)
                if cmp is None:
                    raise Stop(f"{result['tag']} cannot be compared with the "
                               f"incumbent: {refusals}")
                moved = cmp["moved"]
                if moved is None or not moved or not set(moved) <= keys:
                    if moved is None:
                        why = "moved unknown settings"
                    elif not moved:
                        why = (f"changed nothing, so the {knob} override never "
                               "reached the config")
                    else:
                        why = (f"moved {sorted(moved)}, and {knob} owns only "
                               f"{sorted(keys)}")
                    raise Stop(f"{result['tag']} {why}: the run measures "
                               "something other than this knob, so nothing is "
                               "built on it")
                step.update(score=cmp["score"], decided_by=cmp["decided_by"])
                if not _improves(cmp, ppa):
                    break
                step["kept"] = True
                incumbent, inc = cand, ppa
        ended = "every knob walked"
    except Stop as e:
        ended = str(e)
    return {"incumbent": incumbent,
            "incumbent_run": inc.run if inc is not None else None,
            "ended": ended, "launches": launches, "history": history}
