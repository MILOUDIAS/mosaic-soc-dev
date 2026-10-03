"""Read signoff evidence out of a real LibreLane run directory.

The run layout below is ground truth taken from `mattvenn/librelane_summary`
(MIT), which is a working tool against real LibreLane runs::

    runs/<RUN_TAG>/
      final/metrics.csv                                  # Metric,Value rows
      final/metrics.json                                 # same data, when present
      final/gds/*.gds
      *-magic-drc/reports/drc_violations.magic.rpt
      *-openroad-stapostpnr/summary.rpt
      *-openroad-checkantennas/openroad-checkantennas.log
      *-yosys-synthesis/reports/stat.json

Two design rules follow from that tool's behaviour.

**The metrics file is authoritative, but its absence is not "clean".**
`librelane_summary` prints *"no DRC file, DRC clean?"* — with a question mark,
because it genuinely cannot tell. We resolve that ambiguity the other way: no
evidence is ``INFRASTRUCTURE_ERROR``, never ``PASS``.

**Unknown metric keys must still be able to fail the run.** LibreLane's metric
vocabulary changes between versions, so a hardcoded key list silently goes
blind on upgrade. `librelane_summary`'s own summary view selects every row
whose key contains ``violation`` or ``error``; we do the same as a generic
sweep *in addition to* the curated key map, so a renamed or newly added
violation counter still fails the gate instead of disappearing.

Nothing here is PDK-specific.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Curated keys: precise semantics where we are confident of the name.
DRC_METRIC_KEYS = (
    "magic__drc_error__count",
    "klayout__drc_error__count",
    "magic__illegal__overlaps",
)
LVS_METRIC_KEYS = (
    "design__lvs_error__count",
    "design__lvs_device_difference__count",
    "design__lvs_unmatched_devices__count",
    "design__lvs_unmatched_nets__count",
    "design__lvs_unmatched_pins__count",
)
ANTENNA_METRIC_KEYS = ("route__antenna_violation__count",)
WORST_SLACK_KEYS = ("timing__setup__ws", "timing__hold__ws")
TNS_KEYS = ("timing__setup__tns", "timing__hold__tns")
AREA_KEYS = (
    "design__die__area",
    "design__core__area",
    "design__instance__area",
    "design__instance__count",
)

# Generic sweep, mirroring librelane_summary's own violation view.
_ADVERSE_KEY = re.compile(r"(?i)violation|error|_vio__")
# Keys that match the sweep but are descriptive rather than counts.
_ADVERSE_EXEMPT = re.compile(r"(?i)__ws$|__tns$|_slack$")

# LibreLane decorates a base metric with two orthogonal suffixes:
#   <base>__corner:<corner>   the same measurement at one PVT corner
#   <base>__iter:<n>          the value during optimisation iteration n
# Both must be stripped before a key is classified, and both used to defeat
# the sweep. See `adverse_metrics` for what each one meant in practice.
_CORNER_SUFFIX = re.compile(r"__corner:([^:]+)$")
_ITER_SUFFIX = re.compile(r"__iter:\d+$")

_STEP_GLOBS = {
    "magic_drc": "*-magic-drc/reports/drc_violations.magic.rpt",
    "klayout_drc": "*-klayout-drc/reports/*.rpt",
    "netgen_lvs": "*-netgen-lvs/reports/lvs.rpt",
    "sta_postpnr": "*-openroad-stapostpnr/summary.rpt",
    "antenna": "*-openroad-checkantennas/openroad-checkantennas.log",
}


@dataclass
class LibreLaneRun:
    """One located LibreLane run and the evidence read out of it."""

    run_dir: Path
    metrics: Dict[str, Any] = field(default_factory=dict)
    metrics_source: Optional[str] = None
    reports: Dict[str, Path] = field(default_factory=dict)
    # Read from the run's own resolved config, never supplied by the caller:
    # a waiver is scoped to a design, so the identity it is matched against
    # must come from the artefact being judged rather than from whoever is
    # asking for a verdict.
    design_name: Optional[str] = None

    @property
    def has_metrics(self) -> bool:
        return bool(self.metrics)


def _numeric(value: Any) -> Optional[float]:
    """Coerce a metric value to a number, or None if it is not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def find_latest_run(runs_dir: Path) -> Optional[Path]:
    """Newest run directory under ``runs_dir``, or None.

    Selection is by directory mtime rather than by parsing the run tag, so it
    does not depend on a particular LibreLane tag format.
    """
    if not runs_dir.is_dir():
        return None
    candidates = [p for p in runs_dir.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_metrics(run_dir: Path) -> Tuple[Dict[str, Any], Optional[str]]:
    """Load ``final/metrics.json`` if present, else ``final/metrics.csv``."""
    final = run_dir / "final"
    as_json = final / "metrics.json"
    if as_json.is_file():
        try:
            data = json.loads(as_json.read_text(errors="replace"))
            if isinstance(data, dict):
                return data, str(as_json)
        except (OSError, json.JSONDecodeError):
            pass
    as_csv = final / "metrics.csv"
    if as_csv.is_file():
        try:
            rows: Dict[str, Any] = {}
            with as_csv.open(newline="") as fh:
                for row in csv.DictReader(fh):
                    key = (row.get("Metric") or row.get("metric") or "").strip()
                    if key:
                        rows[key] = (row.get("Value") or row.get("value") or "").strip()
            if rows:
                return rows, str(as_csv)
        except OSError:
            pass
    return {}, None


def locate_reports(run_dir: Path) -> Dict[str, Path]:
    """Resolve the per-step report files this run produced."""
    found: Dict[str, Path] = {}
    for name, pattern in _STEP_GLOBS.items():
        matches = sorted(run_dir.glob(pattern))
        if matches:
            found[name] = matches[0]
    return found


def disconnected_pins(run_dir: Path) -> Optional[List[str]]:
    """Every disconnected pin LibreLane recorded, as `pin` for the top module
    and `instance/pin` for instances, or None when the run has no table.

    Read from `NN-odb-reportdisconnectedpins/full_disconnected_pins_table.txt`,
    a rich box table grouped by module: a row whose first cell is non-empty
    starts a module, and the power and signal "Disconnected" columns list
    the pins. The metric only counts them; a waiver that names which ones it
    accepts needs the names.
    """
    tables = sorted(Path(run_dir).glob("*-odb-reportdisconnectedpins/"
                                       "full_disconnected_pins_table.txt"))
    if not tables:
        return None
    top = load_design_name(Path(run_dir))
    module, pins = None, []
    for line in tables[-1].read_text(errors="replace").splitlines():
        if not line.startswith("\u2502"):          # "│": a body row
            continue
        cells = [c.strip() for c in line.strip("\u2502").split("\u2502")]
        if len(cells) != 5:
            continue
        if cells[0]:
            module = cells[0]
        for pin in (cells[2], cells[4]):
            if pin:
                pins.append(pin if module == top else f"{module}/{pin}")
    return pins


def load_design_name(run_dir: Path) -> Optional[str]:
    """``DESIGN_NAME`` from the run's own resolved configuration."""
    for name in ("resolved.json", "config.json"):
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            value = data.get("DESIGN_NAME")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def load_run(runs_dir: Path) -> Optional[LibreLaneRun]:
    """Locate the newest run under ``runs_dir`` and read its evidence."""
    run_dir = find_latest_run(runs_dir)
    if run_dir is None:
        return None
    metrics, source = load_metrics(run_dir)
    return LibreLaneRun(
        run_dir=run_dir,
        metrics=metrics,
        metrics_source=source,
        reports=locate_reports(run_dir),
        design_name=load_design_name(run_dir),
    )


def first_metric(metrics: Dict[str, Any], keys) -> Tuple[Optional[str], Optional[float]]:
    """First present, numeric metric among ``keys``."""
    for key in keys:
        if key in metrics:
            value = _numeric(metrics[key])
            if value is not None:
                return key, value
    return None, None


def sum_metrics(metrics: Dict[str, Any], keys) -> Tuple[List[str], Optional[float]]:
    """Sum every present numeric metric among ``keys``.

    Returns ``(matched_keys, total)``; ``total`` is None when none matched, so
    a caller can tell "all zero" from "never measured".
    """
    matched: List[str] = []
    total = 0.0
    for key in keys:
        if key in metrics:
            value = _numeric(metrics[key])
            if value is not None:
                matched.append(key)
                total += value
    return (matched, total) if matched else ([], None)


def _split_key(key: str) -> Tuple[str, Optional[str], bool]:
    """Split a LibreLane metric key into ``(base, corner, is_iteration)``."""
    corner = None
    match = _CORNER_SUFFIX.search(key)
    if match:
        corner = match.group(1)
        key = key[: match.start()]
    is_iteration = bool(_ITER_SUFFIX.search(key))
    if is_iteration:
        key = _ITER_SUFFIX.sub("", key)
    return key, corner, is_iteration


def _is_adverse(base: str, value: float) -> bool:
    """Does this value of this base metric indicate a problem?

    Takes the BASE key, so that a per-corner worst-slack key is still tested
    against the slack rule. Testing the raw key instead silently exempted every
    corner-qualified slack metric, because
    ``timing__setup__ws__corner:nom_tt_025C_5v00`` does not end in ``__ws``.
    """
    if _ADVERSE_KEY.search(base) and not _ADVERSE_EXEMPT.search(base):
        return value > 0
    if base.endswith("__ws"):
        return value < 0
    return False


def adverse_metrics(metrics: Dict[str, Any]) -> List[Tuple[str, float]]:
    """Every metric that indicates a problem, including unknown keys.

    Two rules, applied to the base key after ``__corner:``/``__iter:`` are
    stripped:

    - any key matching ``violation`` / ``error`` / ``_vio__`` whose value is a
      number greater than zero;
    - any worst-slack key whose value is negative.

    The first is the version-drift safety net: a LibreLane upgrade that renames
    or adds a violation counter still fails the gate. That property is why the
    sweep is deliberately pattern-based rather than a fixed key list, and the
    two reductions below are shaped so it survives them.

    **Iteration traces are not results.** The detailed router reports
    ``route__drc_errors__iter:0..N`` as it converges — on the Block A signoff
    run: 11, 4, 3, 7, 2, 1, 1 — alongside the final ``route__drc_errors = 0``.
    A router that converges to zero is a router working correctly, so an
    ``__iter:`` key is suppressed when the final aggregate exists and is clean.
    If the aggregate is missing or is itself adverse, the iteration values are
    reported: absent a final answer, the trace is the only evidence there is.

    **Corners are collapsed to their worst.** One real violation class otherwise
    lands once per corner plus once for the aggregate, so a single finding is
    reported four times. Entries are grouped by base metric and reduced to the
    worst magnitude, tagged ``base@corner`` when a corner supplied it. A metric
    that exists *only* per-corner is still reported — nothing is dropped for
    lacking an aggregate.
    """
    values: Dict[str, float] = {}
    for key, raw in metrics.items():
        value = _numeric(raw)
        if value is not None:
            values[key] = value

    worst_by_base: Dict[str, Tuple[float, Optional[str]]] = {}
    for key, value in values.items():
        base, corner, is_iteration = _split_key(key)
        if not _is_adverse(base, value):
            continue
        if is_iteration:
            final = values.get(base)
            if final is not None and not _is_adverse(base, final):
                continue
        previous = worst_by_base.get(base)
        if (
            previous is None
            or abs(value) > abs(previous[0])
            # On a tie prefer the aggregate, so the reported name is the
            # canonical one rather than whichever corner was seen first.
            or (abs(value) == abs(previous[0]) and corner is None)
        ):
            worst_by_base[base] = (value, corner)

    return sorted(
        (f"{base}@{corner}" if corner else base, value)
        for base, (value, corner) in worst_by_base.items()
    )
