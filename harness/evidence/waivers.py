"""Bounded, design-scoped waivers for signoff metrics.

A waiver mechanism is the single feature most capable of quietly destroying an
evidence gate, so this one is built to make the dangerous shapes unexpressible
rather than merely discouraged.

Five rules, each enforced at load time:

**A waiver is a ceiling, not an exemption.** Every record carries
``accepted_max``. Waiving 591 max-slew violations accepts *591*; 592 fails.
A waiver therefore freezes a known defect at its measured size and cannot
absorb a regression that grows it.

**A waiver names one design.** ``design`` must equal the run's ``DESIGN_NAME``.
This is the trap the mechanism exists to avoid: the next thing we do is harden
a second configuration, and a waiver granted for Block A must not travel to it.
A waiver for a design that is not the one being evaluated is inert.

**A waiver names one metric, exactly.** No wildcards, no prefixes, no regular
expressions. ``metric`` is compared literally against the key the sweep
reports, so a waiver cannot broaden itself as LibreLane's vocabulary drifts.

**A waiver must say what it costs.** ``justification`` is mandatory and must be
substantive; a record without one does not load. This follows OpenADA's
assertion profiles, where ``non_goals`` has ``minItems: 1`` -- you cannot ship
a claim without stating what it does not prove.

**A waiver expires.** ``review_by`` is mandatory. Past that date the waiver
stops applying and the metric fails again. A waiver with no expiry is
indistinguishable from a deleted check after the person who granted it leaves.

Finally, applying a waiver never yields a silent pass: the waived records
travel in the evidence as ``waived`` and appear in the flow summary, so
"PASS with 2 waivers" is never rendered as "PASS".
"""

from __future__ import annotations

import datetime as _datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# Minimum characters of justification. Enough to reject "n/a", "known", "ok",
# and the empty string, without pretending prose length is rigour.
_MIN_JUSTIFICATION = 40

_REQUIRED = ("metric", "design", "accepted_max", "justification", "review_by",
             "recorded_by", "evidence")

# Optional, and the reason they exist is that a COUNT is not an argument.
#
# `accepted_max: 5` says five of something are tolerated. It does not say which
# five, or how bad each is, so the same ceiling that accepts five clock-buffer
# roots at fanout 16 also accepts five combinational nets at fanout 40. The
# justification names the five, but prose is not checked: when this project
# raised a ceiling 4 -> 5, the text still asserted every violator was a 16-way
# clock root and the fifth was a combinational buffer at 11. The number moved
# and the reason did not, which is the generic failure, not a one-off.
#
# `expected_violators` pins the identities. `preconditions` pins the conditions
# the argument rests on -- the ones justifications state as "NOT waived by this
# record: any of these appearing in the max-slew list" and nothing enforces.
_OPTIONAL = ("expected_violators", "preconditions")


@dataclass(frozen=True)
class Waiver:
    """One accepted, bounded, dated exception for one metric on one design."""

    metric: str
    design: str
    accepted_max: float
    justification: str
    review_by: _datetime.date
    recorded_by: str
    evidence: str
    #: Pin names this waiver claims are the violators. Empty means unpinned,
    #: which is permitted and reported rather than silently accepted.
    expected_violators: Tuple[str, ...] = ()
    #: metric -> ceiling that must hold for the argument to stand. Checked
    #: against the RAW metrics of the run, before any waiver is applied, so no
    #: waiver can satisfy another's precondition.
    preconditions: Mapping[str, float] = field(default_factory=dict)

    @property
    def pinned(self) -> bool:
        """Whether this waiver says WHICH violations it accepts."""
        return bool(self.expected_violators)

    def precondition_failures(
        self, metrics: Mapping[str, Any]
    ) -> List[str]:
        """Which stated preconditions the run does not meet."""
        out = []
        for key, ceiling in sorted(self.preconditions.items()):
            if key not in metrics:
                out.append(f"{key} is not measured in this run")
                continue
            value = metrics[key]
            if isinstance(value, (int, float)) and value > ceiling:
                out.append(f"{key} is {value:g}, above the {ceiling:g} this "
                           "waiver's argument requires")
        return out

    def expired(self, today: _datetime.date) -> bool:
        return today > self.review_by

    def as_record(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "design": self.design,
            "accepted_max": self.accepted_max,
            "review_by": self.review_by.isoformat(),
            "recorded_by": self.recorded_by,
            "evidence": self.evidence,
        }


class WaiverError(ValueError):
    """A waiver file that does not load. Never degraded to "no waivers"."""


def _as_date(value: Any, where: str) -> _datetime.date:
    if isinstance(value, _datetime.date) and not isinstance(value, _datetime.datetime):
        return value
    if isinstance(value, _datetime.datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return _datetime.date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise WaiverError(f"{where}: review_by must be an ISO date (YYYY-MM-DD)")


def parse_waivers(data: Any, *, source: str = "<memory>") -> List[Waiver]:
    """Validate a waiver document into records, or raise.

    Raising rather than skipping is deliberate: a malformed waiver file must
    stop the gate, not silently reduce to an empty waiver set, which would
    turn a typo into an unexplained FAIL and a deleted key into a silent
    tightening nobody notices.
    """
    if data is None:
        return []
    if not isinstance(data, dict):
        raise WaiverError(f"{source}: waiver document must be a mapping")
    raw = data.get("waivers", [])
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise WaiverError(f"{source}: 'waivers' must be a list")

    waivers: List[Waiver] = []
    seen: set = set()
    for index, entry in enumerate(raw):
        where = f"{source}: waivers[{index}]"
        if not isinstance(entry, dict):
            raise WaiverError(f"{where}: must be a mapping")
        unknown = set(entry) - set(_REQUIRED) - set(_OPTIONAL)
        if unknown:
            raise WaiverError(f"{where}: unknown key(s) {sorted(unknown)}")
        missing = [key for key in _REQUIRED if key not in entry]
        if missing:
            raise WaiverError(f"{where}: missing required key(s) {missing}")

        metric = entry["metric"]
        if not isinstance(metric, str) or not metric.strip():
            raise WaiverError(f"{where}: metric must be a non-empty string")
        if any(char in metric for char in "*?["):
            raise WaiverError(
                f"{where}: metric {metric!r} looks like a pattern; waivers name "
                "exactly one metric so they cannot broaden themselves"
            )
        design = entry["design"]
        if not isinstance(design, str) or not design.strip():
            raise WaiverError(f"{where}: design must be a non-empty string")

        accepted = entry["accepted_max"]
        if isinstance(accepted, bool) or not isinstance(accepted, (int, float)):
            raise WaiverError(f"{where}: accepted_max must be a number")
        if accepted < 0:
            raise WaiverError(f"{where}: accepted_max must not be negative")

        justification = entry["justification"]
        if not isinstance(justification, str):
            raise WaiverError(f"{where}: justification must be a string")
        if len(justification.strip()) < _MIN_JUSTIFICATION:
            raise WaiverError(
                f"{where}: justification must be at least {_MIN_JUSTIFICATION} "
                "characters -- a waiver records why a known defect is accepted, "
                "and an unexplained waiver is a deleted check"
            )
        for field_name in ("recorded_by", "evidence"):
            value = entry[field_name]
            if not isinstance(value, str) or not value.strip():
                raise WaiverError(f"{where}: {field_name} must be a non-empty string")

        key = (metric.strip(), design.strip())
        if key in seen:
            raise WaiverError(
                f"{where}: duplicate waiver for {key[0]!r} on {key[1]!r}; "
                "two ceilings for one metric is ambiguous"
            )
        seen.add(key)

        violators = entry.get("expected_violators", ())
        if isinstance(violators, str) or not isinstance(violators, (list, tuple)):
            raise WaiverError(
                f"{where}: expected_violators must be a list of pin names")
        if any(not isinstance(v, str) or not v.strip() for v in violators):
            raise WaiverError(
                f"{where}: every expected_violator must be a non-empty string")
        if len(set(violators)) != len(violators):
            raise WaiverError(
                f"{where}: expected_violators contains duplicates, so it "
                "cannot be compared against a run's violator set")
        if violators and len(violators) > accepted:
            raise WaiverError(
                f"{where}: names {len(violators)} expected violators but the "
                f"ceiling is {accepted:g}. The list may be shorter than the "
                "ceiling; it must never be longer, or the record accepts "
                "violations it also declares unacceptable")

        preconditions = entry.get("preconditions", {}) or {}
        if not isinstance(preconditions, dict):
            raise WaiverError(
                f"{where}: preconditions must be a mapping of metric to ceiling")
        for key, ceiling in preconditions.items():
            if not isinstance(key, str) or not key.strip():
                raise WaiverError(
                    f"{where}: precondition keys must be metric names")
            if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)):
                raise WaiverError(
                    f"{where}: precondition {key!r} must map to a number")
        if metric.strip() in preconditions:
            raise WaiverError(
                f"{where}: {metric.strip()!r} is both the waived metric and "
                "one of its own preconditions, which would make the waiver "
                "justify itself")

        waivers.append(Waiver(
            expected_violators=tuple(violators),
            preconditions={k: float(v) for k, v in preconditions.items()},
            metric=metric.strip(),
            design=design.strip(),
            accepted_max=float(accepted),
            justification=justification.strip(),
            review_by=_as_date(entry["review_by"], where),
            recorded_by=entry["recorded_by"].strip(),
            evidence=entry["evidence"].strip(),
        ))
    return waivers


def load_waivers(path: Optional[Path]) -> List[Waiver]:
    """Read and validate a waiver YAML file. A missing path means no waivers."""
    if path is None or not Path(path).is_file():
        return []
    import yaml

    text = Path(path).read_text(errors="replace")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise WaiverError(f"{path}: not valid YAML: {error}") from error
    return parse_waivers(data, source=str(path))


#: Metrics a count cannot waive: the run records WHICH pins, and a waiver for
#: them must name the same set. A disconnected-pin count is a hard check -- it
#: stands for "a port nothing drives or reads" -- so eleven pad readbacks and
#: eleven undriven outputs must not look alike to the gate.
IDENTITY_REQUIRED = frozenset({"design__disconnected_pin__count"})


def apply_waivers(
    findings: Sequence[Tuple[str, float]],
    waivers: Sequence[Waiver],
    *,
    design: Optional[str],
    today: Optional[_datetime.date] = None,
    identities: Optional[Mapping[str, Optional[Sequence[str]]]] = None,
    metrics: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Tuple[str, float]], List[Dict[str, Any]], List[str]]:
    """Split findings into (still failing, waived, notes).

    A finding is waived only when a waiver matches its metric *and* the design
    under evaluation, has not expired, and the observed value is within the
    accepted ceiling. Anything else leaves the finding failing, with a note
    explaining which condition was not met -- a waiver that does not apply is
    reported, never silently ignored.

    With `metrics`, a waiver whose preconditions the run does not meet does
    not apply. With `identities` (metric -> the violators the run recorded), a
    pinned waiver applies only to exactly its named set. A metric in
    IDENTITY_REQUIRED is never waived by count alone.
    """
    today = today or _datetime.date.today()
    remaining: List[Tuple[str, float]] = []
    waived: List[Dict[str, Any]] = []
    notes: List[str] = []

    by_metric = {w.metric: w for w in waivers if design and w.design == design}
    for key, value in findings:
        waiver = by_metric.get(key)
        if waiver is None:
            remaining.append((key, value))
            continue
        if waiver.expired(today):
            remaining.append((key, value))
            notes.append(
                f"waiver for {key} expired on {waiver.review_by.isoformat()} "
                "and no longer applies"
            )
            continue
        if value > waiver.accepted_max:
            remaining.append((key, value))
            notes.append(
                f"{key}={value:g} exceeds its waived ceiling of "
                f"{waiver.accepted_max:g}"
            )
            continue
        if metrics is not None:
            failed = waiver.precondition_failures(metrics)
            if failed:
                remaining.append((key, value))
                notes.append(f"waiver for {key} does not apply: " + "; ".join(failed))
                continue
        seen = (identities or {}).get(key)
        if key in IDENTITY_REQUIRED and (seen is None or not waiver.pinned):
            remaining.append((key, value))
            notes.append(f"waiver for {key} does not apply: it can only accept named "
                         "violators, and " + ("the run records none" if seen is None
                                              else "the waiver names none"))
            continue
        if waiver.pinned and seen is not None and set(seen) != set(waiver.expected_violators):
            remaining.append((key, value))
            extra = sorted(set(seen) - set(waiver.expected_violators))
            gone = sorted(set(waiver.expected_violators) - set(seen))
            notes.append(f"waiver for {key} does not apply: violators differ"
                         + (f"; not named: {extra}" if extra else "")
                         + (f"; named but absent: {gone}" if gone else ""))
            continue
        record = waiver.as_record()
        record["observed"] = value
        waived.append(record)

    inert = [w for w in waivers if design and w.design != design]
    if inert:
        notes.append(
            f"{len(inert)} waiver(s) not applied: recorded for a different "
            f"design than {design!r}"
        )
    return remaining, waived, notes
