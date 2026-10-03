"""Turn an OpenSTA `report_power` into typed metrics that carry their corner.

M2 wants "every numeric metric has a unit and source artifact" and "power
evidence without a corner is rejected". `Metric` already refuses both, so this
module's job is narrow: read the report faithfully and hand `Metric` what it
needs, never inventing the parts it would otherwise refuse over.

That has one consequence worth stating up front. The corner is parsed from the
report's own banner, not passed in by a caller who thinks they know which
directory they read. A report whose banner is missing produces no metrics and
an explicit complaint, because a caller supplying the corner from a path is
exactly how a `max_ss` number ends up labelled `nom_tt`.

WHAT THE NUMBERS MEAN. `report_power` without switching activity applies a
default toggle model, and on this design that produces ~99% clock and
sequential power: a clock-tree cost, not workload power. This module does not
decide which one it read. It reports what the file said and defers to
`workload.power_metric_status`, which is the module that can tell.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .metric import Metric, WATT

# `======================= max_ss_125C_4v50 Corner ====================`
_CORNER = re.compile(r"^=+\s*(\S+)\s+Corner\s*=+\s*$", re.M)

# `Sequential   4.922040e-02 1.267514e-05 6.536210e-06 4.923961e-02  52.3%`
_ROW = re.compile(
    r"^(?P<group>[A-Z][A-Za-z ]*?)\s+"
    r"(?P<internal>-?[\d.]+e?[-+]?\d*)\s+"
    r"(?P<switching>-?[\d.]+e?[-+]?\d*)\s+"
    r"(?P<leakage>-?[\d.]+e?[-+]?\d*)\s+"
    r"(?P<total>-?[\d.]+e?[-+]?\d*)"
    r"(?:\s+(?P<pct>[\d.]+)%)?\s*$",
    re.M,
)

COMPONENTS = ("internal", "switching", "leakage", "total")


class PowerReportError(ValueError):
    """A power report that cannot yield trustworthy metrics."""


@dataclass(frozen=True)
class PowerGroup:
    """One row: a design partition and its four power components, in watts."""

    name: str
    internal: float
    switching: float
    leakage: float
    total: float

    @property
    def slug(self) -> str:
        return self.name.strip().lower().replace(" ", "_")


@dataclass
class PowerReport:
    """A parsed `report_power`, with its corner and where it came from."""

    corner: str
    source: str
    groups: Dict[str, PowerGroup] = field(default_factory=dict)
    total: Optional[PowerGroup] = None
    pdk: Optional[str] = None

    def metrics(self) -> List[Metric]:
        """Every number in the report as a `Metric`, corner attached.

        Total-row names match LibreLane's own keys (`power__internal__total`)
        so a parsed report and a `metrics.json` can be compared without a
        translation table. Group rows take a `__group:` suffix, mirroring the
        `__corner:` convention already used across this codebase.
        """
        out: List[Metric] = []
        if self.total is not None:
            for component in COMPONENTS:
                # LibreLane names the grand total `power__total`, not
                # `power__total__total`. Matching it exactly is the point:
                # a parsed report and a metrics.json must be comparable
                # without a translation table.
                key = ("power__total" if component == "total"
                       else f"power__{component}__total")
                out.append(Metric(
                    name=key,
                    value=getattr(self.total, component),
                    unit=WATT, source=self.source,
                    pdk=self.pdk, corner=self.corner,
                ))
        for group in self.groups.values():
            for component in COMPONENTS:
                out.append(Metric(
                    name=f"power__{component}__group:{group.slug}",
                    value=getattr(group, component),
                    unit=WATT, source=self.source,
                    pdk=self.pdk, corner=self.corner,
                ))
        return out

    @property
    def clock_fraction(self) -> Optional[float]:
        """Share of total power in the clock group.

        Worth surfacing rather than burying: a value near 1.0 is the signature
        of a report taken with no switching activity, which is the specific
        way a power number here misleads.
        """
        clock = self.groups.get("clock")
        if clock is None or not self.total or not self.total.total:
            return None
        return clock.total / self.total.total


def parse_power_report(
    text: str, *, source: str, pdk: Optional[str] = None,
) -> PowerReport:
    """Parse one `report_power` block. Raises if it has no corner banner."""

    corner_match = _CORNER.search(text)
    if not corner_match:
        raise PowerReportError(
            f"{source}: no corner banner in the power report. Power varies "
            "with process, voltage and temperature, so a report that does not "
            "say which corner it was taken at cannot be labelled by the "
            "caller without guessing (M2)")
    corner = corner_match.group(1)

    report = PowerReport(corner=corner, source=source, pdk=pdk)
    for match in _ROW.finditer(text):
        name = match.group("group").strip()
        # The header spills two lines of column titles that match the row
        # shape closely enough to be worth excluding by name.
        if name in {"Group", "Power"}:
            continue
        group = PowerGroup(
            name=name,
            internal=float(match.group("internal")),
            switching=float(match.group("switching")),
            leakage=float(match.group("leakage")),
            total=float(match.group("total")),
        )
        if group.slug == "total":
            report.total = group
        else:
            report.groups[group.slug] = group

    if report.total is None and not report.groups:
        raise PowerReportError(
            f"{source}: no power rows recognised. The report format may have "
            "changed; refusing rather than reporting zero groups as a result")
    return report


def power_report_for_corner(corner_dir: Path, *, pdk: Optional[str] = None):
    """Read `power.rpt` from one corner directory of an STA step."""

    path = corner_dir / "power.rpt"
    if not path.is_file():
        return None
    return parse_power_report(path.read_text(), source=str(path), pdk=pdk)


def power_reports_for_run(sta_step: Path, *, pdk: Optional[str] = None
                          ) -> Dict[str, PowerReport]:
    """Every corner's power report under one STA step, keyed by corner.

    Keyed by the corner the REPORT declares, not by the directory name. They
    agree today; if they ever stop agreeing, the file is right and the
    directory is a label.
    """
    reports: Dict[str, PowerReport] = {}
    for child in sorted(p for p in sta_step.iterdir() if p.is_dir()):
        report = power_report_for_corner(child, pdk=pdk)
        if report is not None:
            reports[report.corner] = report
    return reports
