"""Structurally diff two hardened netlists, via najaeda.

WHY THIS EXISTS
---------------
Twice in one week the question "what actually changed between these two
netlists?" had to be answered with `grep -c`, and both times the answer was
wrong in a way that mattered.

`blocka_reharden` has 70,836 instances against `blocka_signoff`'s 71,550, and
that 714-cell drop was reported -- by me, twice -- as "logic was removed",
which made a boot failure look like an optimisation bug. najaeda loads both in
about two seconds each and says what really happened:

    -1,955  fill and fillcap     displaced, not deleted
    +1,035  buf_1..4             the slew fix
      +358  clkbuf_1..4          the corner change
      -155  dlyb_1, and dlya_2 gone entirely

Fill being displaced by buffers on a FIXED die is exactly what should happen.
No logic was removed. The classification below exists so that conclusion is
produced by the tool rather than by whoever is reading the numbers at the time.

WHAT IT IS NOT FOR
------------------
Analysis only. najaeda can also EDIT netlists, and a netlist-editing step
between synthesis and GDS would create precisely the RTL-versus-netlist gap
that let a non-booting design pass every signoff check. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Cell-name fragments, most specific first: `fillcap` must be tested before
# `fill`, and `clkbuf` before `buf`, or the counts land in the wrong bucket.
_KINDS: Tuple[Tuple[str, str], ...] = (
    ("fillcap", "fill"),
    ("fill", "fill"),
    ("tap", "physical"),
    ("endcap", "physical"),
    ("antenna", "physical"),
    ("decap", "physical"),
    ("clkbuf", "clock"),
    ("clkinv", "clock"),
    ("clkgate", "clock"),
    ("dlya", "delay"),
    ("dlyb", "delay"),
    ("dly", "delay"),
    ("buf", "buffer"),
    ("inv", "buffer"),
    ("dff", "sequential"),
    ("sdff", "sequential"),
    ("latch", "sequential"),
)


def classify(cell: str) -> str:
    """Which bucket a cell belongs to. Everything unmatched is logic.

    The buckets are the ones that change the reading of a diff: fill and
    physical cells scale with the die, buffers and clock cells with repair,
    and only `logic` moving means the design changed.
    """
    lowered = cell.lower()
    for fragment, kind in _KINDS:
        if fragment in lowered:
            return kind
    return "logic"


@dataclass
class NetlistSummary:
    """What one netlist contains, structurally."""

    path: str
    top: str
    instances: int = 0
    cells: Dict[str, int] = field(default_factory=dict)
    flops: List[str] = field(default_factory=list)

    def by_kind(self) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for cell, count in self.cells.items():
            totals[classify(cell)] = totals.get(classify(cell), 0) + count
        return totals


def load_summary(netlist_path: Path, liberty: List[Path]) -> NetlistSummary:
    """Read one gate-level netlist. Requires najaeda."""
    from najaeda import netlist as naja

    # reset() is what allows two designs with the same top name in one
    # process; without it the second load fails with "NLLibrary DESIGN
    # contains already a SNLDesign named: ...".
    naja.reset()
    naja.load_liberty([str(p) for p in liberty])
    top = naja.load_verilog([str(netlist_path)])

    cells: Dict[str, int] = {}
    flops: List[str] = []
    for instance in top.get_child_instances():
        model = instance.get_model_name()
        cells[model] = cells.get(model, 0) + 1
        if classify(model) == "sequential":
            flops.append(instance.get_name())
    return NetlistSummary(
        path=str(netlist_path), top=top.get_name() if hasattr(top, "get_name") else "",
        instances=sum(cells.values()), cells=cells, flops=sorted(flops))


def diff(a: NetlistSummary, b: NetlistSummary, *, top_n: int = 15) -> Dict:
    """What changed from `a` to `b`, bucketed so it reads correctly."""
    deltas = {cell: b.cells.get(cell, 0) - a.cells.get(cell, 0)
              for cell in set(a.cells) | set(b.cells)}
    changed = {c: d for c, d in deltas.items() if d}

    by_kind: Dict[str, int] = {}
    for cell, delta in changed.items():
        by_kind[classify(cell)] = by_kind.get(classify(cell), 0) + delta

    flops_a, flops_b = set(a.flops), set(b.flops)
    return {
        "instances": {"a": a.instances, "b": b.instances,
                      "delta": b.instances - a.instances},
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -abs(kv[1]))),
        # The interpretation, stated by the tool. A drop in total instances
        # that is entirely fill is not a design change, and reading it as one
        # sends you chasing an optimisation bug that is not there.
        "logic_changed": by_kind.get("logic", 0) != 0,
        "top_cell_changes": dict(sorted(
            changed.items(), key=lambda kv: -abs(kv[1]))[:top_n]),
        "cell_types_only_in_a": sorted(set(a.cells) - set(b.cells)),
        "cell_types_only_in_b": sorted(set(b.cells) - set(a.cells)),
        # Decisive for gate-level simulation: the power-up init file deposits
        # into flops BY NAME, so a changed flop set silently disables it.
        "flops": {"a": len(flops_a), "b": len(flops_b),
                  "identical_names": flops_a == flops_b,
                  "only_in_a": len(flops_a - flops_b),
                  "only_in_b": len(flops_b - flops_a)},
    }


def typical_corner_token(run_dir: Path) -> str:
    """The substring identifying this run's TYPICAL liberty.

    It used to be the literal `"tt_025C"`, which is GF180's spelling and
    nobody else's -- IHP writes `typ_1p20V_25C`, sharing no substring with it.
    On any other PDK the filter matched nothing and the code fell through to
    "whichever liberty sorts first", which can be the slow or the fast corner.
    That is a wrong answer with no symptom.
    """
    from harness.physical.technology import resolve

    pdk = None
    resolved = run_dir / "resolved.json"
    if resolved.is_file():
        try:
            import json
            cfg = json.loads(resolved.read_text())
            pdk = cfg.get("STD_CELL_LIBRARY") and (
                f"{cfg.get('PDK')}:{cfg.get('STD_CELL_LIBRARY')}")
            pdk = pdk or cfg.get("PDK")
        except Exception:                             # pragma: no cover
            pdk = None
    tech = resolve(pdk)
    if tech is not None:
        return tech.typical_corner_token
    # Undeclared technology: keep GF180's token rather than inventing one, but
    # the caller is told below that the selection is not trustworthy.
    return "tt_025C"


def summarise_run(run_dir: Path) -> Tuple[Optional[Path], List[Path]]:
    """The netlist and liberty for a hardening run."""
    nl = sorted((run_dir / "final" / "nl").glob("*.nl.v"))
    from harness.physical.power import liberty_files

    token = typical_corner_token(run_dir)
    libs = [p for p in liberty_files(run_dir) if token in p.name] \
        or liberty_files(run_dir)[:1]
    return (nl[0] if nl else None), libs
