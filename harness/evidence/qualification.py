"""The M2 exit criteria, as a record that refuses to exist without them.

Three rules, from the roadmap:

  1. every numeric metric has a unit and a source artefact;
  2. timing and power evidence without a corner is rejected;
  3. workload evidence without firmware and workload hashes is rejected.

`Metric` enforces 1 and 2 at construction and `WorkloadRun` enforces 3, so this
module does not re-implement them. It does the part neither can: it holds a
whole result together and refuses to call it qualified when the pieces do not
support the claim, which is where the interesting failures live.

WHY THIS EXISTS RATHER THAN A DICT. A qualification result travels: it gets
summarised into a dashboard, quoted in a reply to a reviewer, and pasted into a
fab form. Every one of those is a place where a number loses its corner on the
way. The measured example is in this repository. LibreLane promotes ONE corner
to the unsuffixed `power__total` key, and it is `max_ff_n40C_5v50` (5.50 V,
-40 C), the highest-power corner. Reading that key and calling it "power at 5 V"
mixes a 5.50 V measurement with a 5.00 V supply and produces a current that was
never measured at any corner. `unsuffixed_corner` below names the corner instead
of letting a reader assume a nominal one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .metric import Dimension, Metric, CORNER_DEPENDENT
from .power import PowerReport
from .workload import WorkloadRun, power_metric_status


class QualificationError(ValueError):
    """A result that does not meet the M2 exit criteria."""


@dataclass(frozen=True)
class QualificationResult:
    """Typed evidence for one design, from one run, with its provenance."""

    design: str
    run_tag: str
    metrics: Sequence[Metric]
    workload: Optional[WorkloadRun] = None
    power_status: str = "default-activity"
    power_status_reasons: Sequence[str] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.design:
            raise QualificationError("a qualification result names its design")
        if not self.run_tag:
            raise QualificationError(
                f"{self.design}: a result must name the run it came from, "
                "or it cannot be reproduced or superseded")

        # Criterion 1, the half Metric cannot check: a unit may be present and
        # still be UNKNOWN, which carries a symbol without a meaning.
        unknown = [m.name for m in self.metrics
                   if m.unit.dimension is Dimension.UNKNOWN]
        if unknown:
            raise QualificationError(
                f"{self.design}: {len(unknown)} metric(s) have an unknown "
                f"unit, so nothing can be compared or converted: "
                f"{', '.join(sorted(unknown)[:5])}")

        # Criterion 2 is enforced per-metric at construction. Re-checking here
        # would be theatre; what is NOT covered there is the aggregate claim,
        # so state which corners this result actually spans.
        if self.power_status not in {"workload", "default-activity"}:
            raise QualificationError(
                f"{self.design}: power_status must say whether the number "
                f"reflects a workload; got {self.power_status!r}")

    # ── what the result can honestly be asked ────────────────────────
    @property
    def corners(self) -> List[str]:
        return sorted({m.corner for m in self.metrics if m.corner})

    def by_name(self, name: str) -> List[Metric]:
        return [m for m in self.metrics if m.name == name]

    def at(self, name: str, corner: str) -> Optional[Metric]:
        return next((m for m in self.metrics
                     if m.name == name and m.corner == corner), None)

    def worst(self, name: str) -> Optional[Metric]:
        """The largest value across corners, which is what a budget needs."""
        candidates = self.by_name(name)
        return max(candidates, key=lambda m: m.base_value) if candidates else None

    @property
    def describes_workload_power(self) -> bool:
        return self.power_status == "workload"

    def power_caveat(self) -> str:
        """One sentence a report can quote instead of inventing one."""
        if self.describes_workload_power:
            return ""
        reasons = "; ".join(self.power_status_reasons) or "no workload was bound"
        return (
            "Power figures here are OpenSTA's default toggle model, not "
            f"workload power ({reasons}). They describe what the clock tree "
            "costs, not what running the design costs.")


def unsuffixed_corner(reports: Dict[str, PowerReport],
                      flow_total: Optional[float]) -> Optional[str]:
    """Which corner LibreLane promoted to the unsuffixed `power__total`.

    Determined by comparison rather than assumed, because the answer is not
    the nominal corner and a reader who assumes it is will misreport the
    supply voltage along with it.
    """
    if flow_total is None:
        return None
    # A RELATIVE tolerance, and not a token one. `power.rpt` prints seven
    # significant figures while `metrics.json` carries full double precision,
    # so the same measurement differs in the last digits. An absolute epsilon
    # tight enough to be meaningful rejects the true match.
    for corner, report in reports.items():
        if report.total and math.isclose(report.total.total, flow_total,
                                         rel_tol=1e-6):
            return corner
    return None


def qualify_power(
    design: str,
    run_tag: str,
    reports: Dict[str, PowerReport],
    *,
    workload: Optional[WorkloadRun] = None,
) -> QualificationResult:
    """Build a result from parsed power reports and an optional workload."""

    if not reports:
        raise QualificationError(
            f"{design}: no power reports parsed, so there is nothing to "
            "qualify. An empty result that validates is worse than an error")

    metrics: List[Metric] = []
    for report in reports.values():
        metrics.extend(report.metrics())

    status, reasons = power_metric_status(workload)

    notes: List[str] = []
    clocky = [c for c, r in reports.items()
              if (r.clock_fraction or 0) > 0.9]
    if clocky and status != "workload":
        notes.append(
            f"clock power exceeds 90% of the total at {len(clocky)} corner(s), "
            "the signature of a report taken with no switching activity")

    return QualificationResult(
        design=design, run_tag=run_tag, metrics=metrics,
        workload=workload, power_status=status,
        power_status_reasons=tuple(reasons), notes=tuple(notes),
    )
