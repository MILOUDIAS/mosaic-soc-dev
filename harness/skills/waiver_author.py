"""Check a waiver against the run it cites, which nothing has ever done.

`parse_waivers` validates a waiver's SHAPE: keys present, justification long
enough, review_by a date, accepted_max a number. It never opens `evidence`.
Across the whole tree `Waiver.evidence` is only echoed back by `as_record()`,
so the field that names the measurement has always been an unread string.

That is what lets a structurally perfect waiver stop being true. The record on
this branch is the specimen: ceiling 1, evidence `runs/blocka_sdc`, every
structural check passing, and the design it describes measures 4 and 5 on the
runs that came after. Its cited run is a 1117.5 um macro -- the die size the
integrator rejected -- and its justification still says "the same single net
has been measured in every re-harden since", which three later runs falsify.

WHAT THIS CHECKS, and why each one exists rather than being invented:

  evidence resolves            the path is never opened today
  metric is present in it      a waiver can name a metric the run never measured
  ceiling == measured          a ceiling above the measurement waives headroom
                               nobody observed
  violators match              a COUNT is not an argument: ceiling 5 accepts
                               five clock roots at 16 and equally five
                               combinational nets at 40
  preconditions hold           justifications state conditions their argument
                               rests on, in prose, and nothing enforces them
  not expired                  review_by is a date nobody rereads
  still current                a waiver can be self-consistent and obsolete,
                               because the design moved to a newer run

WHAT IT DOES NOT DO. It does not write waivers. Authoring one is a decision
about silicon and belongs to a person; this reports whether the decision still
matches the measurement. Under `harness/skill_policy`, a skill that WROTE a
waiver would write evidence and require approval at any cost. This one reads.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..core import REPO_ROOT, SkillResult
from ..evidence.waivers import Waiver, load_waivers

RUN_ROOTS = (
    "flow/librelane/integration/runs",
    "flow/librelane/experimental/runs",
)


@dataclass
class WaiverAudit:
    """One waiver, checked against the world."""

    metric: str
    design: str
    evidence: str
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    measured: Optional[float] = None
    accepted_max: Optional[float] = None

    @property
    def ok(self) -> bool:
        return not self.problems


def _metrics_of(run: Path) -> Optional[Dict[str, Any]]:
    path = run / "final" / "metrics.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _finished_at(run: Path) -> float:
    """When the run wrote its final metrics. The directory's own mtime moves
    whenever anything under it is pruned or added (a GLS log, a deleted
    binary), so blocka_1110_ndr, finished 2026-08-24 and pruned 2026-09-21,
    ranked above a run finished 2026-09-24."""
    return (run / "final" / "metrics.json").stat().st_mtime


def _runs_for(design: str, repo_root: Path,
              near: Optional[Path] = None) -> List[Path]:
    """Every run directory that measured this design, newest first.

    Searches the known run roots AND the directory holding the cited evidence
    run. The second is what makes the superseded check work at all when runs
    live somewhere the roots do not name: a waiver's nearest comparison set is
    its own siblings, and hardcoding two paths quietly disabled the check
    everywhere else.
    """

    roots = [repo_root / r for r in RUN_ROOTS]
    if near is not None:
        roots.append(near.parent)

    found = []
    seen = set()
    for base in roots:
        if not base.is_dir() or base.resolve() in seen:
            continue
        seen.add(base.resolve())
        for run in base.iterdir():
            if not run.is_dir():
                continue
            metrics = _metrics_of(run)
            if metrics is None:
                continue
            # A run is "for" a design when its saved views carry that name.
            if (run / "final" / "nl" / f"{design}.nl.v").is_file() or \
               (run / "final" / "pnl" / f"{design}.pnl.v").is_file():
                found.append(run)
    return sorted(found, key=_finished_at, reverse=True)


def _violators_in(run: Path, metric: str) -> Optional[List[str]]:
    """Pin names behind a violation count, from the STA report.

    Returns None when the report cannot be found, which is reported as
    unverifiable rather than as agreement.
    """
    if metric == "design__disconnected_pin__count":
        # Not an STA quantity: LibreLane's own table names these, and it is
        # the source both gates read (librelane.disconnected_pins).
        from harness.evidence.librelane import disconnected_pins
        return disconnected_pins(run)
    kind = ("max fanout" if "fanout" in metric else
            "max slew" if "slew" in metric else
            "max capacitance" if "cap" in metric else None)
    if kind is None:
        return None
    steps = sorted(run.glob("*-openroad-stapostpnr"))
    if not steps:
        return None
    for corner in sorted(steps[-1].iterdir()):
        log = corner / "sta.log"
        if not log.is_file():
            continue
        try:
            text = log.read_text()
        except OSError:
            continue
        if kind not in text:
            continue
        # Read to the END of the section, not to the first blank line. OpenSTA
        # prints `max fanout\n\nPin ... Limit Fanout Slack`, so a blank line
        # lands immediately after the header and splitting on it yielded an
        # EMPTY section for every real log -- which read as "the declared
        # violators are absent" and verified the identities against nothing.
        # Measured on runs/blocka_1110_ndr, whose four violators were reported
        # missing while sitting in the log.
        pins = []
        for line in text.split(kind, 1)[1].splitlines():
            stripped = line.strip()
            if stripped.startswith("=") or stripped in (
                    "max slew", "max capacitance", "max fanout"):
                break                   # next section, or the corner footer
            if "VIOLATED" in line and line.split():
                pins.append(line.split()[0])
        return pins
    return None


def audit_waiver(waiver: Waiver, *, repo_root: Path,
                 today: Optional[date] = None) -> WaiverAudit:
    """Check one waiver against its evidence run and the newest run."""

    today = today or date.fromisoformat("2026-09-06")
    a = WaiverAudit(metric=waiver.metric, design=waiver.design,
                    evidence=waiver.evidence, accepted_max=waiver.accepted_max)

    if waiver.expired(today):
        a.problems.append(
            f"expired: review_by was {waiver.review_by.isoformat()}")

    run = repo_root / waiver.evidence
    if not run.is_dir():
        a.problems.append(
            f"evidence path does not resolve: {waiver.evidence}. Nothing has "
            "ever opened this field, so it can name a run that never existed "
            "or has been deleted")
        return a

    metrics = _metrics_of(run)
    if metrics is None:
        a.problems.append(f"no final/metrics.json under {waiver.evidence}")
        return a

    if waiver.metric not in metrics:
        a.problems.append(
            f"the cited run does not measure {waiver.metric}")
        return a

    a.measured = metrics[waiver.metric]
    if a.measured != waiver.accepted_max:
        verb = ("waives headroom nobody observed"
                if waiver.accepted_max > a.measured
                else "is below what its own evidence run measures")
        a.problems.append(
            f"ceiling {waiver.accepted_max:g} against a measured "
            f"{a.measured:g} in the cited run: it {verb}")

    # Preconditions: checked against the RAW metrics, before any waiver is
    # applied, so one waiver can never satisfy another's precondition.
    if waiver.preconditions:
        for failure in waiver.precondition_failures(metrics):
            a.problems.append(f"precondition broken: {failure}")
    else:
        a.notes.append(
            "declares no preconditions, so the conditions its justification "
            "rests on are prose that no later run can break")

    # Identity, not just count.
    observed = _violators_in(run, waiver.metric)
    if waiver.expected_violators:
        if observed is None:
            a.notes.append("violator identities could not be read from the "
                           "STA report, so the pinned list is unverified here")
        else:
            missing = sorted(set(waiver.expected_violators) - set(observed))
            extra = sorted(set(observed) - set(waiver.expected_violators))
            if missing:
                a.problems.append(f"declared violators absent from the run: {missing}")
            if extra:
                a.problems.append(
                    f"the run violates on nets this waiver does not name: "
                    f"{extra}. A count alone would have accepted these")
    else:
        a.notes.append(
            f"unpinned: accepts any {waiver.accepted_max:g} violations of this "
            "metric, not the specific ones its justification describes")

    # Obsolescence: self-consistent and superseded are different failures.
    #
    # Only a run that passes its OWN gates can supersede. blocka_m45_ndr
    # measures 3 of this metric against the cited run's 4 and carries 28
    # max-slew violations, so it read as "the design has moved past" a run that
    # actually closes -- a lower count on a netlist that fails is not progress,
    # and acting on it would point the waiver at a design nobody can ship.
    from harness.physical.ppa import evaluate as _evaluate
    candidates = []
    for other in _runs_for(waiver.design, repo_root, near=run):
        if other.resolve() == run.resolve():
            continue
        # Only a LATER run can supersede; an older accepted run is the past.
        if _finished_at(other) <= _finished_at(run):
            continue
        measured, _ = _evaluate(other, repo_root=repo_root)
        if measured is not None and measured.accepted:
            candidates.append(other)
    if candidates:
        latest = candidates[0]
        latest_value = (_metrics_of(latest) or {}).get(waiver.metric)
        if latest_value is not None and latest_value != a.measured:
            a.problems.append(
                f"superseded: {latest.name} measures {latest_value:g} for this "
                f"metric where the cited {run.name} measures {a.measured:g}, "
                "and it passes its gates. The waiver is consistent with a run "
                "the design has moved past")
    else:
        a.notes.append("no other gate-passing run of this design to compare "
                       "against")

    return a


def audit(waivers_path: Optional[Path] = None, *,
          repo_root: Optional[Path] = None,
          today: Optional[date] = None) -> SkillResult:
    """Audit every waiver in the file against the runs it cites."""

    root = repo_root or REPO_ROOT
    path = waivers_path or (root / "flow/librelane/signoff_waivers.yaml")
    if not path.is_file():
        return SkillResult(ok=True, skill="waiver-author",
                           summary=f"no waiver file at {path}", details={})

    waivers: Sequence[Waiver] = load_waivers(path)
    audits = [audit_waiver(w, repo_root=root, today=today) for w in waivers]
    bad = [a for a in audits if not a.ok]

    return SkillResult(
        ok=not bad,
        skill="waiver-author",
        summary=(f"{len(audits)} waiver(s) audited, all consistent with their "
                 f"evidence" if not bad else
                 f"{len(bad)} of {len(audits)} waiver(s) no longer match their "
                 f"evidence"),
        details={
            "waivers": [
                {"metric": a.metric, "design": a.design, "evidence": a.evidence,
                 "accepted_max": a.accepted_max, "measured": a.measured,
                 "ok": a.ok, "problems": a.problems, "notes": a.notes}
                for a in audits
            ],
        },
        errors=[f"{a.metric} / {a.design}: {p}" for a in bad for p in a.problems],
    )
