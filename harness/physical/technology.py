"""Per-technology facts, so GF180's are not everyone's.

WHY THIS EXISTS. `pdk-port` reports GF180 as satisfying all eight of its
requirements and then says of five of them: *"satisfied only by being hardcoded
to GF180 values; there is no per-technology storage for it."* This module is
that storage. It was written against TWO technologies rather than one, because
an interface written against a single PDK is a guess about the second.

THE UNIT IS TECHNOLOGY, NOT PDK. `SITE_HEIGHT_UM = 3.92` is not a GF180 fact;
it is the 7-track library's. `gf180mcu_fd_sc_mcu9t5v0` sits in the same PDK
tree at 5.04 um, and the die arithmetic depends on which one is in use. So
entries are keyed `pdk:cell_library`, and a bare PDK name resolves through
`DEFAULT_LIBRARY` rather than guessing.

DECLARED IS NOT CALIBRATED, AND THE DIFFERENCE IS THE POINT. Site geometry is
a fact you can read out of a tech LEF in a minute. Area calibration takes a
multi-hour hardening run and cannot be read from anything. IHP sg13g2 is
declared here with real measured geometry and is still refused by
`floorplan.calibration_gap`, which is the correct pair of answers: the tools
now know how big its rows are and still refuse to size a die they have never
measured.

WHAT THIS DOES NOT DO. It ports nothing. Adding an entry makes the silent
failures loud; it does not make a design harden. That work is a hardening run
and a measurement, and no table shortcuts it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Technology:
    """The process-specific facts the physical tools branch on."""

    key: str                       # "<pdk>:<cell_library>"
    pdk: str
    cell_library: str

    #: Standard-cell site, from the library's own LEF. Both axes matter:
    #: LibreLane's margin multipliers are per-axis site counts.
    site_width_um: float
    site_height_um: float

    #: Substring that identifies the TYPICAL corner's liberty file. Corner
    #: naming is not standardised across PDKs -- GF180 writes `tt_025C`,
    #: IHP writes `typ_1p20V_25C` -- so this cannot be a shared literal.
    typical_corner_token: str

    #: Where this PDK's SRAM abstracts live, relative to the repo, or None
    #: when no macro set has been characterised for it here.
    sram_lef_root: Optional[str]

    #: Where these numbers came from, so a reader can re-derive them.
    source: str

    @property
    def site(self) -> Tuple[float, float]:
        return (self.site_width_um, self.site_height_um)


#: Every technology whose geometry has been read from its own collateral.
TECHNOLOGIES: Dict[str, Technology] = {
    "gf180mcu:gf180mcu_fd_sc_mcu7t5v0": Technology(
        key="gf180mcu:gf180mcu_fd_sc_mcu7t5v0",
        pdk="gf180mcu", cell_library="gf180mcu_fd_sc_mcu7t5v0",
        site_width_um=0.56, site_height_um=3.92,
        typical_corner_token="tt_025C",
        sram_lef_root=(
            "flow/librelane/gf180mcu/gf180mcuD/libs.ref/"
            "gf180mcu_fd_ip_sram/lef"),
        source="SITE GF018hv5v_mcu_sc7 in the 7-track library LEF; the "
               "technology every MOSAIC tapeout run has used",
    ),
    # Same PDK, different geometry -- the case that makes "keyed by PDK" wrong.
    # Declared from the library's SITE line; nothing has been hardened on it.
    "gf180mcu:gf180mcu_fd_sc_mcu9t5v0": Technology(
        key="gf180mcu:gf180mcu_fd_sc_mcu9t5v0",
        pdk="gf180mcu", cell_library="gf180mcu_fd_sc_mcu9t5v0",
        site_width_um=0.56, site_height_um=5.04,
        typical_corner_token="tt_025C",
        sram_lef_root=(
            "flow/librelane/gf180mcu/gf180mcuD/libs.ref/"
            "gf180mcu_fd_ip_sram/lef"),
        source="9-track library SITE height, 5.04 um; same PDK as the 7-track "
               "entry and 28.6% taller rows",
    ),
    # The second PDK. Geometry and corner naming read from the IHP Open PDK
    # tree; NOT area-calibrated, and floorplan.calibration_gap still refuses
    # it. Both facts are true at once and that is the intended state.
    "ihp-sg13g2:sg13g2_stdcell": Technology(
        key="ihp-sg13g2:sg13g2_stdcell",
        pdk="ihp-sg13g2", cell_library="sg13g2_stdcell",
        site_width_um=0.48, site_height_um=3.78,
        typical_corner_token="typ_1p20V_25C",
        sram_lef_root=None,     # RM_IHPSG13_1P_* exist; none measured here
        source="SITE CoreSite, SIZE 0.48 BY 3.78, in "
               "ihp-sg13g2/libs.ref/sg13g2_stdcell/lef/sg13g2_stdcell.lef",
    ),
}

#: Which library a bare PDK name means. Only for PDKs where one library is the
#: established choice; anything else must be named in full, because picking
#: silently is the failure this module exists to remove.
DEFAULT_LIBRARY: Dict[str, str] = {
    "gf180mcu": "gf180mcu_fd_sc_mcu7t5v0",
    "ihp-sg13g2": "sg13g2_stdcell",
}


def resolve(technology: Optional[str]) -> Optional[Technology]:
    """Look up `pdk` or `pdk:cell_library`. None when it is not declared."""

    if not technology:
        return None
    if technology in TECHNOLOGIES:
        return TECHNOLOGIES[technology]
    if ":" in technology:
        return None
    library = DEFAULT_LIBRARY.get(technology)
    if library is None:
        return None
    return TECHNOLOGIES.get(f"{technology}:{library}")


def technology_gap(technology: Optional[str]) -> Optional[str]:
    """Why this technology's geometry is unknown, or None if it is declared.

    Separate from `floorplan.calibration_gap`, and both can fire: geometry is
    readable from a LEF, area is not readable from anything.
    """

    if resolve(technology) is not None:
        return None
    known = ", ".join(sorted(TECHNOLOGIES))
    return (
        f"no declared site geometry for technology {technology!r}. Rounding a "
        "core ring or selecting a timing corner would silently use another "
        f"process's numbers. Declared: {known}. Add an entry to "
        "harness/physical/technology.py with the SITE line from its LEF"
    )
