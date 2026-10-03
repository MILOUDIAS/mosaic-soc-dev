"""What the tools actually DID differently, when the metrics moved.

Metrics say a count changed. They do not say what changed to cause it. Turning
MAX_FANOUT_CONSTRAINT from 10 to 16 took max-fanout violations 5 -> 12 and
buffer area 31,137 -> 23,594 um2; the netlists say why, in one line: 628 fewer
buf_1 and 463 more buf_4. CTS used bigger buffers and fewer of them.

THE STAGE MATTERS AND WAS WRONG. `summarise_run` globs `final/nl/*.nl.v`,
post-synthesis. `run_gls.sh` simulates `final/pnl/<design>.pnl.v`, post-PnR --
the netlist that becomes the GDS, with the repair buffers, fill and antenna
cells in it. A diff run to explain a gate-level result was comparing the stage
that had not been through placement. This module defaults to `pnl` and NAMES
the stage it compared in every result, because the two answer different
questions and neither is wrong in general.

DEGRADATION IS A FEATURE HERE. najaeda gives a structural diff and is optional;
it is absent on this machine. Rather than reporting "unavailable", the fallback
counts cell instantiations directly out of the Verilog: 2.4 s for two 12 MB
netlists, instance totals matching metrics.json exactly, and a per-cell-type
delta. Fewer facts than najaeda, clearly labelled, still enough to answer the
question that gets asked.
"""
from __future__ import annotations

import collections
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core import REPO_ROOT, SkillResult

#: A cell instantiation in a LibreLane-written Verilog netlist:
#:
#:     ` gf180mcu_fd_sc_mcu7t5v0__antenna ANTENNA_1 (.I(net1209),`
#:
#: Anchored on SHAPE, not on a vendor prefix. One leading space, a bare
#: identifier for the cell type, an instance name, then the paren; port
#: connections are indented four and begin with a dot, so they cannot match.
#:
#: An earlier version keyed on `__` and over-counted by 37, matching text like
#: `.CLK(clknet_5_10__leaf_clk_i_regs),` because generated NET names contain the
#: same separator. Keying on `gf180mcu_` instead would have been exact and
#: would have baked a PDK into a module that has no business knowing one --
#: precisely what pdk-port exists to catch. Verified against two runs: 73,158
#: and 72,020, both equal to design__instance__count in their metrics.json.
_CELL = re.compile(r"^ ([A-Za-z_][A-Za-z0-9_$]*)\s+(\S+)\s*\(", re.M)

STAGES = {
    "pnl": ("final/pnl", "*.pnl.v", "post-place-and-route; what becomes the GDS "
                                    "and what GLS simulates"),
    "nl": ("final/nl", "*.nl.v", "post-synthesis; before placement, repair "
                                 "buffers, fill and antenna cells"),
}

#: Kinds a zero-delay simulation cannot distinguish. Shared vocabulary with
#: gls-triage: if a diff is confined to these, it cannot explain a differing
#: gate-level verdict, and both skills should say so in the same terms.
TRANSPARENT = ("buf", "clkbuf", "inv", "dly", "fill", "antenna", "tie",
               "endcap", "tap")


def _kind(cell: str) -> str:
    leaf = cell.split("__")[-1].lower()
    for frag in TRANSPARENT:
        if leaf.startswith(frag):
            return "transparent"
    return "logic"


@dataclass
class NetlistDelta:
    stage: str
    a_path: Optional[str] = None
    b_path: Optional[str] = None
    identical: bool = False
    method: str = "cell-census"
    a_total: int = 0
    b_total: int = 0
    by_cell: Dict[str, int] = field(default_factory=dict)
    by_kind: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def logic_moved(self) -> bool:
        return bool(self.by_kind.get("logic"))

    @property
    def oracle_can_see(self) -> bool:
        """Whether a zero-delay simulation could tell these two apart."""
        return self.logic_moved


def _resolve(run: Path, stage: str) -> Optional[Path]:
    sub, pattern, _ = STAGES[stage]
    found = sorted((run / sub).glob(pattern)) if (run / sub).is_dir() else []
    return found[0] if found else None


def _census(path: Path) -> collections.Counter:
    return collections.Counter(m[0] for m in _CELL.findall(path.read_text()))


def _digest(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(run_a: Path, run_b: Path, *, stage: str = "pnl") -> NetlistDelta:
    """Compare two runs' netlists at one stage."""

    if stage not in STAGES:
        raise ValueError(f"stage must be one of {sorted(STAGES)}, got {stage!r}")

    d = NetlistDelta(stage=stage)
    a = _resolve(run_a, stage)
    b = _resolve(run_b, stage)
    if a is None or b is None:
        missing = [str(r) for r, p in ((run_a, a), (run_b, b)) if p is None]
        d.notes.append(
            f"no {stage} netlist under {missing}. STAGES[{stage!r}] looks in "
            f"{STAGES[stage][0]}/{STAGES[stage][1]}")
        return d
    d.a_path, d.b_path = str(a), str(b)

    # Byte identity first: two independent runs of one design produce this,
    # and saying "identical" convincingly is worth more than a table of zeros.
    if _digest(a) == _digest(b):
        d.identical = True
        d.method = "md5"
        d.a_total = d.b_total = sum(_census(a).values())
        d.notes.append(
            "byte-identical netlists. The runs differ in nothing the tools "
            "wrote, so any behavioural difference between them is not in the "
            "design")
        return d

    try:
        from ..physical.netlist import diff, load_summary, summarise_run
        import najaeda  # noqa: F401
        _, libs_a = summarise_run(run_a)
        _, libs_b = summarise_run(run_b)
        structural = diff(load_summary(a, libs_a), load_summary(b, libs_b))
        d.method = "najaeda"
        d.a_total = structural["instances"]["a"]
        d.b_total = structural["instances"]["b"]
        d.by_kind = dict(structural.get("by_kind") or {})
        d.notes.append("structural diff via najaeda")
        return d
    except ImportError:
        d.notes.append(
            "najaeda absent: counted cell instantiations from the Verilog "
            "instead. This gives per-cell-type deltas but no connectivity, so "
            "it cannot tell a rewire from a resize")

    ca, cb = _census(a), _census(b)
    d.a_total, d.b_total = sum(ca.values()), sum(cb.values())
    moved = {c: cb[c] - ca[c] for c in set(ca) | set(cb) if cb[c] != ca[c]}
    d.by_cell = dict(sorted(moved.items(), key=lambda kv: -abs(kv[1])))
    kinds: collections.Counter = collections.Counter()
    for cell, delta in moved.items():
        kinds[_kind(cell)] += delta
    d.by_kind = dict(kinds)
    return d


def audit(run_a: str, run_b: str, *, stage: str = "pnl",
          repo_root: Optional[Path] = None) -> SkillResult:
    """Compare two hardening runs and say what the tools did differently."""

    root = repo_root or REPO_ROOT
    a, b = Path(run_a), Path(run_b)
    if not a.is_absolute():
        a = root / a
    if not b.is_absolute():
        b = root / b

    try:
        d = compare(a, b, stage=stage)
    except ValueError as exc:
        return SkillResult(ok=False, skill="netlist-diff",
                           summary=str(exc), errors=[str(exc)])

    if d.a_path is None:
        return SkillResult(ok=False, skill="netlist-diff",
                           summary=f"no {stage} netlist to compare",
                           details={"stage": stage}, errors=d.notes)

    if d.identical:
        summary = f"identical at {stage} ({d.a_total:,} instances)"
    else:
        summary = (f"{stage}: {d.a_total:,} -> {d.b_total:,} instances "
                   f"({d.b_total - d.a_total:+,}), "
                   f"{'logic moved' if d.logic_moved else 'buffering only'}")

    return SkillResult(
        ok=True,
        skill="netlist-diff",
        summary=summary,
        details={
            "stage": stage,
            "stage_means": STAGES[stage][2],
            "method": d.method,
            "identical": d.identical,
            "instances": {"a": d.a_total, "b": d.b_total,
                          "delta": d.b_total - d.a_total},
            "by_kind": d.by_kind,
            "largest_moves": dict(list(d.by_cell.items())[:12]),
            # The bridge to gls-triage: a difference confined to transparent
            # cells cannot explain a differing zero-delay verdict.
            "explains_a_gls_difference": d.oracle_can_see,
            "notes": d.notes,
        },
    )
