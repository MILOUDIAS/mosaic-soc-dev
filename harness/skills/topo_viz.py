"""topo-viz — semantic config checks + topology diagrams.

Both halves read the SoCView (harness/socview.py), which is derived from the
same `XHeep` object and package template the RTL is rendered from. This module
used to keep its own digest of the YAML, and that digest drew `2*nh+1+4`
crossbar masters and 2 RAM banks for every OBI design; the generated package
says 7/9/11 masters and 1 bank for Blocks A/B/C. The fabric rules it also
re-implemented (LOG bank count, power of two, bytes per bank, butterfly master
count) live in the schema validator and the generator, so a failure there is
now reported as their finding instead of a second opinion.

Rendering is harness/diagram.py: a logical view and a chip view, in one
self-contained HTML page (inline SVG, no external resources) or as SVG.

CLI:
    python -m harness topo-viz check  mosaic.yaml
    python -m harness topo-viz render mosaic.yaml -o topology.html [--svg]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..core import REPO_ROOT, SkillResult, validate_config


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def semantic_checks(soc: Dict[str, Any], view: Dict[str, Any]) -> List[str]:
    """Checks beyond the schema and the generator. Findings starting with
    `note:` are advisory."""
    findings: List[str] = []
    bus = str(soc.get("bus", "obi")).strip().lower()
    for fabric in soc.get("bus_opts", {}) or {}:
        if fabric not in ("log", "floonoc"):
            findings.append(f"bus_opts.{fabric}: unknown fabric")
        elif fabric != bus:
            findings.append(f"note: bus_opts.{fabric} is inert (selected bus is '{bus}')")

    windows = [s for s in view["address_map"]["slaves"]
               if s["start"] is not None and s["size"]]
    ordered = sorted(windows, key=lambda w: w["start"])
    for a, b in zip(ordered, ordered[1:]):
        if a["start"] + a["size"] > b["start"]:
            findings.append(
                f"address overlap: {a['name']} [{a['start']:#010x}, "
                f"{a['start'] + a['size']:#010x}) overlaps {b['name']} "
                f"starting at {b['start']:#010x}")

    titans = [g for g in soc.get("cores", []) if g.get("role") == "titan"]
    if len(titans) > 1:
        findings.append("more than one core group has role 'titan'")
    return findings


class TopoViz:
    """Semantic checks + topology rendering for mosaic.yaml configs."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT

    def _view(self, cfg_path: Path):
        """(soc, view, errors). The view is only built for a config the
        schema accepts: the generator is not written to survive the rest."""
        soc = _load_yaml(cfg_path).get("soc")
        if not soc:
            return None, None, ["missing 'soc'"]
        errors = validate_config({"soc": soc})
        if errors:
            return soc, None, errors
        from ..socview import build_view
        try:
            return soc, build_view(cfg_path, repo_root=self.repo_root), []
        except (Exception, SystemExit) as exc:  # the generator's own verdict
            return soc, None, [f"generator: {' '.join(str(exc).split())}"]

    def check(self, cfg_path: Path) -> SkillResult:
        soc, view, errors = self._view(cfg_path)
        if soc is None:
            return SkillResult(ok=False, skill="topo-viz",
                               summary=f"{cfg_path}: missing top-level 'soc' key",
                               errors=errors)
        findings = semantic_checks(soc, view) if view else []
        hard = errors + [f for f in findings if not f.startswith("note:")]
        notes = [f for f in findings if f.startswith("note:")]
        return SkillResult(
            ok=not hard, skill="topo-viz",
            summary=(f"{cfg_path.name}: clean" if not hard else
                     f"{cfg_path.name}: {len(hard)} finding(s)"),
            details={"findings": findings, "schema_errors": errors, "notes": notes},
            errors=hard)

    def render(self, cfg_path: Path, output: Optional[Path] = None,
               svg_only: bool = False, view_kind: str = "logical",
               run_dir: Optional[Path] = None,
               design: Optional[str] = None) -> SkillResult:
        from ..diagram import chip_svg, logical_svg, standalone_html
        from ..socview import build_view
        soc, view, errors = self._view(cfg_path)
        if view is None:
            return SkillResult(ok=False, skill="topo-viz",
                               summary=f"{cfg_path.name}: cannot render", errors=errors)
        if run_dir or design:
            try:
                view = build_view(cfg_path, run_dir=run_dir, design=design,
                                  repo_root=self.repo_root)
            except ValueError as exc:  # a mistyped run, or another SoC's run
                return SkillResult(ok=False, skill="topo-viz",
                                   summary=f"{cfg_path.name}: cannot join run", errors=[str(exc)])
        if svg_only:
            doc = (chip_svg if view_kind == "chip" else logical_svg)(view)
        else:
            doc = standalone_html(view)
        out = Path(output or cfg_path.with_suffix(".svg" if svg_only else ".html"))
        out.write_text(doc)
        return SkillResult(
            ok=True, skill="topo-viz",
            summary=f"rendered {view['bus']['kind']} topology -> {out}",
            details={"output": str(out), "bus": view["bus"]["kind"],
                     "views": [view_kind] if svg_only else ["logical", "chip"],
                     "crossbar_masters": view["crossbar"]["nmaster"],
                     "ram_banks": view["memory"]["banks"],
                     "io_basis": view["io"]["basis"], "die_basis": view["die"]["basis"]})
