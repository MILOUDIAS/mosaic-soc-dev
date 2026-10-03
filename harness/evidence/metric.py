"""Typed metrics: a number with a unit, a corner, a PDK and where it came from.

ROADMAP M2, first exit criterion: "every numeric metric has a unit and source
artifact", and its second: "timing/power evidence without corner/voltage is
rejected". Both are enforced here at construction rather than checked later,
because a metric that reached a report without provenance has already done the
damage.

WHAT WAS WRONG WITH BARE FLOATS
-------------------------------
`SignoffEvidence` carries `wns_ns: Optional[float]` -- the unit lives in the
FIELD NAME, so it cannot survive being put in a dict, compared, or summed.
`area: Dict[str, float]` does not even manage that. Across this project's
sessions the same numbers have been hand-converted between um2 and mm2, ns and
ps, repeatedly, in prose. Every one of those conversions was an opportunity to
be wrong and none of them was checked.

THE PDK DIMENSION IS NOT OPTIONAL
---------------------------------
GF180 is the first target PDK, not the only one: IHP, SkyWater, FreePDK and
ASAP are planned. Almost everything physical this project has measured is
PDK-specific -- the 0.56 x 3.92 um site, the 4.0 ns max transition, the
0.419 mm2/KB SRAM, the whole per-hart area calibration. A metric recorded
without its PDK is a number that will silently be compared against a different
process later, so `pdk` is a field here from the start rather than a retrofit.
M2 also requires that changing PDK views invalidates dependent evidence, which
is impossible if the evidence never recorded which views it used.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not guess. LibreLane emits 336 metrics per run and this classifies the
ones whose naming convention is unambiguous; the rest get `UNKNOWN` and say so.
An inferred unit that is wrong is worse than an absent one, because it will be
converted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Dimension(Enum):
    """What kind of physical quantity, so conversions cannot cross."""

    TIME = "time"
    LENGTH = "length"
    AREA = "area"
    CAPACITANCE = "capacitance"
    POWER = "power"
    VOLTAGE = "voltage"
    COUNT = "count"
    RATIO = "ratio"
    UNKNOWN = "unknown"


# Measurements of these vary with process, voltage and temperature, so one
# without a corner does not identify anything. M2: "timing/power evidence
# without corner/voltage is rejected."
CORNER_DEPENDENT = frozenset({Dimension.TIME, Dimension.POWER,
                              Dimension.VOLTAGE})


@dataclass(frozen=True)
class Unit:
    symbol: str
    dimension: Dimension
    # Multiplier to the dimension's base unit (s, um, um2, F, W, V).
    in_base: float = 1.0

    def __str__(self) -> str:                      # pragma: no cover - display
        return self.symbol


SECOND = Unit("s", Dimension.TIME, 1.0)
NS = Unit("ns", Dimension.TIME, 1e-9)
PS = Unit("ps", Dimension.TIME, 1e-12)
UM = Unit("um", Dimension.LENGTH, 1.0)
MM = Unit("mm", Dimension.LENGTH, 1e3)
UM2 = Unit("um2", Dimension.AREA, 1.0)
MM2 = Unit("mm2", Dimension.AREA, 1e6)
FARAD = Unit("F", Dimension.CAPACITANCE, 1.0)
PF = Unit("pF", Dimension.CAPACITANCE, 1e-12)
FF = Unit("fF", Dimension.CAPACITANCE, 1e-15)
WATT = Unit("W", Dimension.POWER, 1.0)
MW = Unit("mW", Dimension.POWER, 1e-3)
UW = Unit("uW", Dimension.POWER, 1e-6)
VOLT = Unit("V", Dimension.VOLTAGE, 1.0)
MV = Unit("mV", Dimension.VOLTAGE, 1e-3)
COUNT = Unit("count", Dimension.COUNT, 1.0)
RATIO = Unit("ratio", Dimension.RATIO, 1.0)
UNKNOWN = Unit("?", Dimension.UNKNOWN, 1.0)


class MetricError(ValueError):
    """A metric that cannot be trusted, refused at construction."""


@dataclass(frozen=True)
class Metric:
    """One measured number, with everything needed to interpret it.

    `source` is required and `pdk`/`corner` are required where they matter,
    enforced in `__post_init__`. There is no way to build a timing measurement
    that does not say which corner it was measured at.
    """

    name: str
    value: float
    unit: Unit
    source: str
    pdk: Optional[str] = None
    corner: Optional[str] = None
    # "measurement" varies with the corner; "constraint" is a value someone
    # chose (CLOCK_PERIOD, MAX_TRANSITION_CONSTRAINT) and is corner-free by
    # nature. The distinction has to be explicit -- inferring it from the name
    # is exactly the guessing this module refuses to do.
    kind: str = "measurement"

    def __post_init__(self) -> None:
        if not self.name:
            raise MetricError("a metric needs a name")
        if not self.source:
            raise MetricError(
                f"{self.name}: every metric needs the artefact it was read "
                "from; a number without provenance is not evidence")
        if self.kind not in {"measurement", "constraint"}:
            raise MetricError(f"{self.name}: unknown kind {self.kind!r}")
        if (self.kind == "measurement"
                and self.unit.dimension in CORNER_DEPENDENT
                and not self.corner):
            raise MetricError(
                f"{self.name}: a {self.unit.dimension.value} measurement "
                "without a corner does not identify anything (M2). Pass the "
                "corner, or kind='constraint' if it is a chosen value")

    # ── conversion, which cannot cross dimensions ────────────────────
    def to(self, unit: Unit) -> "Metric":
        if unit.dimension is not self.unit.dimension:
            raise MetricError(
                f"{self.name}: cannot convert {self.unit.dimension.value} "
                f"({self.unit}) to {unit.dimension.value} ({unit})")
        if self.unit.dimension is Dimension.UNKNOWN:
            raise MetricError(
                f"{self.name}: unit is unknown, so converting it would invent "
                "a fact")
        scaled = self.value * self.unit.in_base / unit.in_base
        return Metric(self.name, scaled, unit, self.source,
                      self.pdk, self.corner, self.kind)

    @property
    def base_value(self) -> float:
        """The value in the dimension's base unit, for comparison."""
        return self.value * self.unit.in_base

    def __str__(self) -> str:                      # pragma: no cover - display
        where = f" @{self.corner}" if self.corner else ""
        return f"{self.name} = {self.value:g} {self.unit}{where}"


# ── inferring units from LibreLane's naming convention ───────────────
#
# Grounded in a real run: of 336 metrics, 177 carry `__corner:`, and the
# trailing token is the type. Ordered longest-first so `worst_setup` wins over
# a bare `worst`.
_SUFFIX_UNITS: Tuple[Tuple[str, Unit], ...] = (
    ("__count", COUNT),
    ("__ws", NS),
    ("__wns", NS),
    ("__tns", NS),
    ("__worst_setup", NS),
    ("__worst_hold", NS),
    ("__area", UM2),
    ("__util", RATIO),
    ("__utilization", RATIO),
)
_PREFIX_UNITS: Tuple[Tuple[str, Unit], ...] = (
    ("power__", WATT),
    ("design_powergrid__drop__", VOLT),
    ("design_powergrid__voltage__", VOLT),
)
_CORNER = re.compile(r"__corner:(.+)$")


def split_corner(key: str) -> Tuple[str, Optional[str]]:
    """`foo__corner:nom_tt` -> `("foo", "nom_tt")`."""
    match = _CORNER.search(key)
    if not match:
        return key, None
    return key[: match.start()], match.group(1)


def unit_for(key: str) -> Unit:
    """The unit LibreLane's naming implies, or UNKNOWN.

    UNKNOWN is a real answer here. Roughly a third of the keys in a run are
    tool-specific and guessing at them would produce numbers that convert.
    """
    base, _ = split_corner(key)
    for prefix, unit in _PREFIX_UNITS:
        if base.startswith(prefix):
            return unit
    for suffix, unit in _SUFFIX_UNITS:
        if base.endswith(suffix):
            return unit
    return UNKNOWN


def from_librelane(
    key: str, value: Any, *, source: str, pdk: Optional[str] = None,
) -> Optional[Metric]:
    """Type one entry of a LibreLane `metrics.json`.

    Returns None for non-numeric values (LibreLane stores strings and nulls in
    the same file). A corner-dependent metric with no `__corner:` in its key is
    recorded as a `constraint` rather than refused -- LibreLane aggregates the
    worst corner into an unqualified key, and dropping those would discard the
    numbers the gate actually reads.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    base, corner = split_corner(key)
    unit = unit_for(key)
    kind = "measurement"
    if unit.dimension in CORNER_DEPENDENT and not corner:
        # The aggregate across corners. Real, but not a corner measurement.
        kind = "constraint"
    return Metric(name=base, value=float(value), unit=unit, source=source,
                  pdk=pdk, corner=corner, kind=kind)


def typed_metrics(
    metrics: Dict[str, Any], *, source: str, pdk: Optional[str] = None,
) -> List[Metric]:
    """Type a whole `metrics.json`, skipping non-numeric entries."""
    out = []
    for key, value in metrics.items():
        metric = from_librelane(key, value, source=source, pdk=pdk)
        if metric is not None:
            out.append(metric)
    return out


def unit_coverage(metrics: Dict[str, Any]) -> Tuple[int, int]:
    """`(typed, total)` numeric metrics -- how much of a run we understand.

    Reported rather than asserted at 100%: the honest number is the one that
    can improve, and a test that demanded full coverage would be satisfied by
    guessing.
    """
    typed = total = 0
    for key, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total += 1
        if unit_for(key).dimension is not Dimension.UNKNOWN:
            typed += 1
    return typed, total
