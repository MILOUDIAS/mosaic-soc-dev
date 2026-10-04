"""A PPA verdict for one run, a comparison between two, and the ledger of
every run on disk.

WHY THIS EXISTS. Every physical decision in this project so far -- which
repair margin ships, which die, whether an NDR stays -- ended with a person
reading two tables and writing the judgement in prose. An automatic
improvement loop cannot do that, and "improve" has no meaning until two
things are written down as code:

  1. what a run must satisfy to be considered at all -- the GATES;
  2. what makes one acceptable run better than another -- the COMPARISON.

GATES are pass/fail and never traded against area or power:
  * every LibreLane hard check in report.HARD_CHECKS at zero -- the same list
    signoff_summary uses, so "hard" is defined in exactly one place;
  * worst setup and hold slack non-negative across all corners;
  * zero max-slew and zero max-cap under per-pin liberty limits. That is the
    rule Block A's repair margin was decided on (2026-09-10): a run that
    violates what its cells are qualified to is not a cheaper design, it is a
    broken one.
A metric that is MISSING fails its gate. Not measured is not passed.

GLS and LEC are reported, not gated, for the reason report.py records:
making them blocking is a policy decision nobody has taken. Max-fanout is
reported and not gated because it is under waiver.

THE COMPARISON exists only between two ACCEPTED runs of the SAME design on
the SAME PDK with the SAME power basis. Anything else is the confound this
project keeps rediscovering, so it is refused rather than computed. Within
that it reports Pareto dominance, which needs no weights, and a weighted mean
relative improvement, whose weights are declared.

TWO AREA TERMS, BECAUSE THEY ARE DIFFERENT QUESTIONS. `die_mm2` is the silicon
a wafer is charged for and the thing a floorplan knob moves; `logic_um2` is the
cell area the design needed, which says whether the tools had to buffer their
way out of trouble. They can disagree: the denser of two Block B runs has the
smaller die and MORE cell area. With cell area alone, shrinking a die scored as
a regression, so a utilisation knob would have been walked faithfully and every
value that helped would have been rejected.

ENERGY PER CYCLE, NOT POWER. Power at a run's own clock rises with the clock,
so a power objective scores slowing the chip down as an improvement. On the
one single-variable clock pair on disk (Block A, 40 vs 100 ns) it preferred
100 ns by +2.8%, when the two spend the same energy per cycle to within 0.04%
and the 40 ns run is twice as fast. Energy per cycle (power x period) takes
the clock out and keeps what a faster clock really costs: the extra buffering
and upsizing. Between runs at one clock, energy and power rank identically.

WHICH SETTING MADE THE DIFFERENCE. A comparison ranks two design points. It
says nothing about WHY one is better unless the two runs differ in exactly
one setting -- and the wrong +2.1% margin figure was a two-variable
comparison read as a one-variable one. So every comparison lists the settings
that moved between the two runs, and names a cause (`attribution`) only when
exactly one did. A run's settings are its resolved.json minus machine paths
and checker thread counts, plus the RTL bundle those paths named: the same
identity the evidence store keys on.

THE LEDGER is every finished run under a runs directory with its verdict,
objectives and settings digest, plus every pair of runs that differ in
exactly one setting: the experiments that were actually run, as opposed to
the ones that were thought to be run. It is the history the optimizer
searches.

WHAT THIS DOES NOT DO. The energy objective is only as good as the power it
comes from: without a workload VCD that is OpenSTA's default toggle model,
mostly clock-tree and sequential cost. That is still a consistent relative
measure between runs of one design, and it is labelled on every result so it
cannot be quoted as workload power.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from harness.evidence.store import _VOLATILE_CONFIG_KEYS, bundle_from_config
from harness.physical.report import HARD_CHECKS, signoff_summary

#: (field, direction): +1 means larger is better, -1 means smaller is better.
#:
#: BOTH area terms are here, and they answer different questions. `die_mm2` is
#: the silicon a wafer is charged for, which is what a floorplan knob moves.
#: `logic_um2` is the cell area the design needed, which says whether the tools
#: had to buffer their way out of trouble. Measured on two Block B runs that
#: differ only in density (74.5% vs 79.6% achieved): the denser one has the
#: SMALLER die and 0.53% MORE cell area, because a tighter die needs more repair
#: buffering. With logic area alone, shrinking the die scored as a regression --
#: so a utilisation knob would have been walked faithfully and every value that
#: helped would have been rejected.
OBJECTIVES: Tuple[Tuple[str, int], ...] = (
    ("die_mm2", -1),
    ("logic_um2", -1),
    ("energy_nj", -1),
    ("fmax_mhz", +1),
)

#: Equal by default. A caller that cares more about one axis passes its own;
#: dominance is reported beside the score precisely so the weights never get
#: to decide a case that is not actually a trade-off.
DEFAULT_WEIGHTS: Dict[str, float] = {f: 1.0 for f, _ in OBJECTIVES}

#: (name, headline key, the keys) for settings that ALWAYS move together
#: because one decision sets them all. A floorplan is sized absolutely, so
#: `derive_floorplan` emits `DIE_AREA` and `CORE_AREA` as a pair: two keys, one
#: choice. Without this a density experiment is single-variable in every sense
#: that matters and still invisible, because both the cause and the ledger
#: counted keys rather than decisions.
SETTING_GROUPS: Tuple[Tuple[str, str, FrozenSet[str]], ...] = (
    ("floorplan", "DIE_AREA", frozenset({"DIE_AREA", "CORE_AREA"})),
)

#: Thread counts of tools that READ the layout and never write it. DRT_THREADS
#: stays a setting: the router writes the layout, and nobody here has shown
#: its result is independent of the thread count.
_CHECKER_THREADS = frozenset({
    "KLAYOUT_DRC_THREADS", "KLAYOUT_XOR_THREADS", "STA_THREADS"})


def knobs(resolved: Dict[str, Any]) -> Dict[str, Any]:
    """The settings that decide a run's result."""

    out = {k: v for k, v in resolved.items()
           if k not in _VOLATILE_CONFIG_KEYS and k not in _CHECKER_THREADS}
    # ponytail: the bundle name hashes the whole generator closure, so a flow
    # script edit moves it with the RTL untouched. That errs toward "moved",
    # the safe side. Compare the two bundles' manifests while both are on disk
    # if false alarms start costing attributions.
    out["rtl_bundle"] = bundle_from_config(resolved)
    return out


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PPA:
    run: str
    design: Optional[str]
    pdk: Optional[str]
    gates: Tuple[Gate, ...]
    logic_um2: Optional[float]
    power_w: Optional[float]
    power_basis: Optional[str]
    clock_period_ns: Optional[float]
    setup_ws_ns: Optional[float]
    hold_ws_ns: Optional[float]
    fmax_mhz: Optional[float]
    max_fanout_violations: Optional[float]
    gls: Optional[str]
    #: power_w x clock_period_ns: W x ns is nJ, per clock cycle.
    energy_nj: Optional[float] = None
    #: `design__die__area`, the silicon this design occupies. Constant for a
    #: design whose die is mandated (Block A's fixed die), where it
    #: contributes a zero-gain term rather than a refusal.
    die_mm2: Optional[float] = None
    caveats: Tuple[str, ...] = ()
    #: None when the run has no readable resolved.json. Unknown settings are
    #: not identical settings.
    knobs: Optional[Dict[str, Any]] = field(default=None, repr=False,
                                            compare=False)

    @property
    def accepted(self) -> bool:
        return all(g.passed for g in self.gates)

    @property
    def failing(self) -> List[str]:
        return [g.name for g in self.gates if not g.passed]

    @property
    def setting(self) -> Optional[str]:
        """A short digest of the settings: equal digests, identical settings."""
        if self.knobs is None:
            return None
        blob = json.dumps(self.knobs, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("knobs")
        d["setting"] = self.setting
        d["accepted"] = self.accepted
        return d


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _resolved(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "resolved.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _moved(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, List[Any]]:
    return {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b))
            if a.get(k) != b.get(k)}


def _attributable(moved: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(cause, headline key) when `moved` is ONE decision, else None.

    One key is one decision. So is a whole SETTING_GROUP: a die and its core
    area move together or not at all, and calling that two variables would
    refuse to attribute the one thing a floorplan knob does.
    """
    if len(moved) == 1:
        key = next(iter(moved))
        return key, key
    for name, headline, keys in SETTING_GROUPS:
        if set(moved) == keys:
            return name, headline
    return None


def evaluate(run_dir: Path, *, pdk: Optional[str] = None,
             repo_root: Optional[Path] = None
             ) -> Tuple[Optional[PPA], List[str]]:
    """Gate a finished run and read its three objectives."""

    run_dir = Path(run_dir)
    summary, errors = signoff_summary(run_dir, pdk=pdk, repo_root=repo_root)
    if summary is None:
        return None, errors
    resolved = _resolved(run_dir)
    hard = summary.get("hard_checks") or {}
    quality = summary.get("quality") or {}

    gates: List[Gate] = []
    missing = [k for k in HARD_CHECKS if k not in hard]
    failing = summary.get("hard_checks_failing") or []
    gates.append(Gate(
        "signoff-hard-checks", not missing and not failing,
        ("all zero" if not missing and not failing else
         f"failing {failing}" if failing else f"not measured: {missing}")))

    for name, key in (("setup-slack", "timing__setup__ws"),
                      ("hold-slack", "timing__hold__ws")):
        v = _num(quality.get(key))
        gates.append(Gate(
            name, v is not None and v >= 0,
            "not measured" if v is None else f"worst {v:+.3f} ns"))

    for name, key in (("max-slew", "design__max_slew_violation__count"),
                      ("max-cap", "design__max_cap_violation__count")):
        v = _num(quality.get(key))
        gates.append(Gate(
            name, v == 0,
            "not measured" if v is None else f"{v:g} violations"))

    period = _num(resolved.get("CLOCK_PERIOD"))
    setup = _num(quality.get("timing__setup__ws"))
    fmax = (1000.0 / (period - setup)
            if period is not None and setup is not None and period - setup > 0
            else None)

    power = _num((summary.get("power_watts") or {}).get("power__total"))
    basis = summary.get("power_basis")

    caveats = []
    if power is not None and basis != "workload":
        caveats.append(
            "power is OpenSTA's default toggle model, not a workload: mostly "
            "clock-tree and sequential cost. Consistent between runs of one "
            "design, not quotable as what the chip will draw")
    if fmax is not None:
        caveats.append(
            "fmax is estimated as 1000 / (CLOCK_PERIOD - worst setup slack): "
            "the worst corner, a single clock domain, first order")

    return PPA(
        run=str(run_dir),
        design=summary.get("design"),
        pdk=resolved.get("PDK") or pdk,
        gates=tuple(gates),
        logic_um2=_num(summary.get("logic_um2")),
        power_w=power,
        power_basis=basis,
        clock_period_ns=period,
        setup_ws_ns=setup,
        hold_ws_ns=_num(quality.get("timing__hold__ws")),
        fmax_mhz=fmax,
        max_fanout_violations=_num(
            quality.get("design__max_fanout_violation__count")),
        gls=(summary.get("gls") or {}).get("status"),
        energy_nj=(power * period
                   if power is not None and period is not None else None),
        die_mm2=_num((summary.get("area_mm2") or {}).get("design__die__area")),
        caveats=tuple(caveats),
        knobs=knobs(resolved) if resolved else None,
    ), []


def compare(candidate: PPA, baseline: PPA,
            weights: Optional[Dict[str, float]] = None
            ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Is `candidate` better than `baseline`? Refuses when the question is
    not well posed rather than returning a number that looks like an answer."""

    refusals: List[str] = []
    for label, p in (("candidate", candidate), ("baseline", baseline)):
        if not p.accepted:
            refusals.append(
                f"{label} {p.run} fails {p.failing}; only accepted runs are "
                "compared -- a cheaper broken design is not an improvement")
    if candidate.design != baseline.design:
        refusals.append(f"different designs ({candidate.design} vs "
                        f"{baseline.design}) are not a comparison")
    if candidate.pdk != baseline.pdk:
        refusals.append(f"different PDKs ({candidate.pdk} vs {baseline.pdk})")
    if candidate.power_basis != baseline.power_basis:
        refusals.append(f"power measured on different bases "
                        f"({candidate.power_basis} vs {baseline.power_basis})")
    for f, _ in OBJECTIVES:
        if not getattr(candidate, f) or not getattr(baseline, f):
            refusals.append(f"{f} not measured on both runs")
    if refusals:
        return None, refusals

    # Merged onto the defaults, never replacing them: a caller that says
    # `die_mm2=3` means "weigh the die higher", not "stop counting cell area,
    # energy and fmax". Replacing the table would silently drop every objective
    # the caller did not name -- and `--weight die_mm2=3` on a density walk
    # would then optimise the die alone, with the trade-off it is paying for
    # invisible.
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    gains = {f: sign * (getattr(candidate, f) - getattr(baseline, f))
             / getattr(baseline, f)
             for f, sign in OBJECTIVES}
    better = [f for f, g in gains.items() if g > 0]
    worse = [f for f, g in gains.items() if g < 0]
    total = sum(w.get(f, 0.0) for f, _ in OBJECTIVES)
    score = (sum(w.get(f, 0.0) * gains[f] for f, _ in OBJECTIVES) / total
             if total else 0.0)
    contrib = {f: abs(w.get(f, 0.0) * gains[f]) for f, _ in OBJECTIVES}
    decided_by = max(contrib, key=lambda f: contrib[f])
    # Measured on the first real comparison (Block B, margin 45 vs 32): +3.1%
    # overall, of which the power term alone was +9.3% -- and that power is
    # the default-toggle proxy. An automatic loop would have optimised the
    # least trustworthy axis without knowing it. Say so, on the result.
    caveats: List[str] = []
    if candidate.power_basis != "workload":
        if decided_by == "energy_nj":
            caveats.append(
                "the energy term decides this score and it comes from a "
                "default-toggle power proxy, not a workload. Re-weight it or "
                "measure a workload before an automatic loop acts on it")
        else:
            caveats.append("energy comes from a default-toggle power proxy, "
                           "not a workload")

    moved: Optional[Dict[str, List[Any]]] = None
    if candidate.knobs is None or baseline.knobs is None:
        caveats.append("a run without a readable resolved.json: nothing says "
                       "what differs between them, so no cause is named")
    else:
        moved = _moved(baseline.knobs, candidate.knobs)
        if not moved:
            caveats.append("identical settings: a repeat run, so the "
                           "difference is the flow's run-to-run noise")
        elif len(moved) > 1:
            caveats.append(
                f"{len(moved)} settings moved ({', '.join(moved)}): two design "
                "points, not a measurement of any one setting. Attribute the "
                "difference to none of them")
    return {
        "caveats": caveats,
        "dominates": bool(better) and not worse,
        "dominated": bool(worse) and not better,
        "better_on": better,
        "worse_on": worse,
        "gains": {f: round(g, 6) for f, g in gains.items()},
        "score": round(score, 6),
        "decided_by": decided_by,
        "weights": w,
        "moved": moved,
        "attribution": ((_attributable(moved) or (None,))[0]
                        if moved else None),
    }, []


def ledger(runs_root: Path, *, design: Optional[str] = None,
           pdk: Optional[str] = None, repo_root: Optional[Path] = None
           ) -> Tuple[List[PPA], List[Dict[str, Any]]]:
    """Every finished run under `runs_root`, and every single-variable pair.

    An experiment's `gains` are its second run relative to its first, and are
    None when either run is rejected -- the verdicts still say which one
    failed, which is a finding in itself (a setting that breaks a gate).
    """

    runs: List[PPA] = []
    for d in sorted(Path(runs_root).iterdir()):
        if not (d / "final" / "metrics.json").is_file():
            continue            # unfinished: no verdict to record
        p, _ = evaluate(d, pdk=pdk, repo_root=repo_root)
        if p is not None and (design is None or p.design == design):
            runs.append(p)

    experiments: List[Dict[str, Any]] = []
    for i, a in enumerate(runs):
        for b in runs[i + 1:]:
            if (a.knobs is None or b.knobs is None
                    or (a.design, a.pdk) != (b.design, b.pdk)):
                continue
            moved = _moved(a.knobs, b.knobs)
            named = _attributable(moved) if moved else None
            if named is None:
                continue
            cause, headline = named
            result, _ = compare(b, a)
            experiments.append({
                "setting": cause, "values": moved[headline],
                "keys": sorted(moved), "runs": [a.run, b.run],
                "accepted": [a.accepted, b.accepted],
                "gains": result["gains"] if result else None})
    return runs, experiments
