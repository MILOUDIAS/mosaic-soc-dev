"""What a second PDK must supply, and what it would silently inherit.

The stated goal is any SoC on any PDK. GF180 is the first target, not the only
one. But `pdk` is threaded through this codebase as a LABEL -- recorded in
configs, metrics, manifests and software headers -- and almost nothing branches
on it. GF180 values are baked in as though one PDK exists.

So a second PDK does not fail at a "this PDK is unsupported" check. It produces
numbers. The measured demonstration, before this module existed: taking a
shipped config, changing one key to `pdk: sky130`, and asking for a floorplan
returned the identical GF180 die with `basis="measured"`. A wrong number is
recoverable. A wrong number wearing the word "measured" is not, because that
label is the only reason anyone would trust it.

THE UNIT IS NOT THE PDK. `SITE_HEIGHT_UM = 3.92` is not a GF180 constant; it
is the 7-track library's. `gf180mcu_fd_sc_mcu9t5v0`, in the same PDK tree, is
5.04. Two cell libraries, one PDK, different geometry, and the die arithmetic
depends on it. So a requirement is keyed by TECHNOLOGY -- pdk plus cell
library -- and a check that only compares PDK names is reported as partial
rather than passed.

WHAT THIS SKILL DOES. It enumerates the requirements, says which the repository
satisfies for a given technology, and names what would be silently inherited
from GF180 if the missing ones are ignored. It ports nothing: the work of
porting is hardening a design and measuring it, which no tool can shortcut.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..core import REPO_ROOT, SkillResult

GF180 = "gf180mcu"
GF180_7T = "gf180mcu:gf180mcu_fd_sc_mcu7t5v0"


@dataclass(frozen=True)
class Requirement:
    """One thing a technology must supply before the tools can size for it."""

    key: str
    what: str
    #: Where the GF180 answer lives, so a porter has a worked example.
    gf180_example: str
    #: What happens if it is missing and nobody notices.
    if_missing: str
    #: True when the absence is SILENT -- a plausible wrong answer rather
    #: than a refusal. These are the ones that need a guard, not a document.
    silent: bool


REQUIREMENTS: tuple = (
    Requirement(
        "area-calibration",
        "at least one hardened design measured on this technology",
        "harness/physical/floorplan.py CALIBRATION, three GF180 runs",
        "die sizing has no basis; every number would come from another process",
        silent=False,  # guarded as of calibration_gap()
    ),
    Requirement(
        "site-geometry",
        "standard-cell site width and height from the techlef",
        "SITE GF018hv5v_mcu_sc7, 0.56 x 3.92 um (7-track)",
        "the ring margin is rounded to the wrong site grid",
        silent=True,
    ),
    Requirement(
        "routability-ceiling",
        "the utilisation a design of each size has been shown to route at",
        "harness/physical/routability.py, five GF180 observations",
        "a design is placed at a density this process has never routed",
        silent=True,
    ),
    Requirement(
        "cell-library",
        "the standard-cell library name and its track count",
        "gf180mcu_fd_sc_mcu7t5v0",
        "site geometry and area calibration silently mismatch the cells used",
        silent=True,
    ),
    Requirement(
        "corner-names",
        "the STA corner identifiers this PDK ships",
        "nine corners, {min,nom,max}_{ss,tt,ff}_<temp>_<volt>",
        "metrics carry corner labels that cannot be compared across PDKs",
        silent=True,
    ),
    Requirement(
        "sram-macros",
        "macro geometry, or a statement that designs run SRAM-free",
        "harness/physical/sram.py, the GF180 fd_ip_sram cuts",
        "sram_kb > 0 synthesises to flip-flops with no warning",
        silent=True,
    ),
    Requirement(
        "signoff-collateral",
        "liberty, LEF, tech LEF and DRC deck reachable by the flow",
        "flow/librelane/gf180mcu/gf180mcuD/libs.ref/",
        "the flow cannot run at all",
        silent=False,
    ),
    Requirement(
        "tapeout-matrix",
        "an entry in the qualified matrix, or refusal of target: tapeout",
        "util/xheep_gen/core_registry.py TAPEOUT_PDK, a scalar not a table",
        "a config claims tapeout-qualified on unqualified collateral",
        silent=False,  # core_registry refuses non-GF180 at target: tapeout
    ),
)


@dataclass
class TechnologyReport:
    technology: str
    pdk: str
    satisfied: List[str] = field(default_factory=list)
    missing: List[Requirement] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.missing

    #: Requirement keys that now have a guard, so their absence announces
    #: itself for THIS technology. Populated by `survey`.
    guarded: List[str] = field(default_factory=list)

    @property
    def silent_gaps(self) -> List[Requirement]:
        """Missing requirements that would NOT announce themselves.

        `Requirement.silent` records whether the absence was silent when the
        requirement was written. A guard can retire that for a given
        technology without the requirement itself becoming satisfied --
        routability on IHP is still MISSING, it just says so now instead of
        handing back a GF180 density. Both facts matter and they are
        different facts.
        """
        return [r for r in self.missing
                if r.silent and r.key not in self.guarded]


def _calibrated_pdks() -> set:
    from ..physical.floorplan import CALIBRATED_PDKS
    return set(CALIBRATED_PDKS)


def _pdk_tree(pdk: str, repo_root: Path) -> Optional[Path]:
    base = repo_root / "flow/librelane"
    for child in (base / pdk, base / f"{pdk}mcu", base / pdk.replace("mcu", "")):
        if child.is_dir():
            return child
    return None


def survey(technology: str = GF180_7T, *,
           repo_root: Optional[Path] = None) -> TechnologyReport:
    """Which requirements this technology satisfies in this repository."""

    root = repo_root or REPO_ROOT
    pdk = technology.split(":", 1)[0]
    rep = TechnologyReport(technology=technology, pdk=pdk)

    calibrated = _calibrated_pdks()
    tree = _pdk_tree(pdk, root)

    for req in REQUIREMENTS:
        ok = False
        if req.key == "area-calibration":
            ok = pdk in calibrated
        elif req.key == "signoff-collateral":
            ok = tree is not None
        elif req.key == "tapeout-matrix":
            from util.xheep_gen.core_registry import TAPEOUT_PDK
            ok = pdk == TAPEOUT_PDK
        elif req.key in ("site-geometry", "corner-names", "cell-library"):
            # These now have real per-technology storage. Declaring geometry
            # is cheap -- one SITE line out of a LEF -- and is deliberately
            # decoupled from area calibration, which is not readable from
            # anything and still refuses separately.
            from harness.physical.technology import resolve
            tech = resolve(technology)
            ok = tech is not None
            if ok and req.key == "cell-library" and ":" not in technology:
                rep.notes.append(
                    "cell-library: resolved through DEFAULT_LIBRARY because "
                    f"only the PDK was named; assuming {tech.cell_library}")
        elif req.key == "sram-macros":
            from harness.physical.technology import resolve
            tech = resolve(technology)
            ok = tech is not None and tech.sram_lef_root is not None
            if tech is not None:
                # sram_cost() raises for a technology it has no macros for,
                # so the absence is loud even while it is still absent.
                rep.guarded.append(req.key)
                if not ok:
                    rep.notes.append(
                        f"sram-macros: {tech.key} declares geometry but no "
                        "characterised macro set. sram_cost() RAISES for it "
                        "rather than returning GF180 area")
        elif req.key == "routability-ceiling":
            from harness.physical.routability import OBSERVATIONS_PDK
            ok = pdk == OBSERVATIONS_PDK
            from harness.physical.technology import resolve
            if resolve(technology) is not None:
                rep.guarded.append(req.key)
                if not ok:
                    rep.notes.append(
                        "routability-ceiling: no observation on this process. "
                        "recommended_utilisation() returns basis='unvalidated' "
                        "and the most conservative number measured elsewhere, "
                        "rather than a density this process has never routed")
        else:
            # Whatever is left still has no per-technology storage. Satisfied
            # for GF180 by being hardcoded, which is precisely the defect.
            ok = pdk in calibrated
            if ok and req.silent:
                rep.notes.append(
                    f"{req.key}: satisfied only by being hardcoded to GF180 "
                    "values; there is no per-technology storage for it")
        (rep.satisfied.append(req.key) if ok else rep.missing.append(req))

    if pdk in calibrated:
        # "All eight met" should not read as "portable". It means one process
        # has been measured, on one narrow design family, and the numbers that
        # back it are three runs.
        rep.notes.append(
            "area-calibration rests on THREE hardened runs of one design "
            "family (SERV-only, 2-4 harts, XIP). Outside that family the "
            "estimator refuses by name rather than extrapolating, and the "
            "model carries a declared bias whenever the shipped repair margin "
            "differs from the one it was calibrated at")

    if ":" not in technology:
        rep.notes.append(
            "checked by PDK name alone. Site geometry differs between cell "
            "libraries within one PDK (GF180 7-track is 3.92 um, 9-track is "
            "5.04), so a PDK-only check cannot see a library swap")
    return rep


def audit(technology: str = GF180_7T, *,
          repo_root: Optional[Path] = None) -> SkillResult:
    """Report what this technology supplies and what it would inherit."""

    rep = survey(technology, repo_root=repo_root)
    return SkillResult(
        ok=rep.ready,
        skill="pdk-port",
        summary=(f"{rep.technology}: all {len(rep.satisfied)} requirements met"
                 if rep.ready else
                 f"{rep.technology}: {len(rep.missing)} of "
                 f"{len(REQUIREMENTS)} requirements missing, "
                 f"{len(rep.silent_gaps)} of them silent"),
        details={
            "technology": rep.technology,
            "satisfied": rep.satisfied,
            "missing": [
                {"key": r.key, "what": r.what, "gf180_example": r.gf180_example,
                 "if_missing": r.if_missing, "silent": r.silent}
                for r in rep.missing
            ],
            "notes": rep.notes,
        },
        errors=[f"{r.key}: {r.if_missing}" for r in rep.silent_gaps],
    )
