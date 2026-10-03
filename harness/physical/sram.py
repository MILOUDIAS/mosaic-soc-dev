"""What on-chip SRAM costs in GF180, so the refusal can be a design answer.

WHY THIS EXISTS
---------------
`estimate_logic_area` refuses any design with `memory.sram_kb > 0`, and said
only "macro placement is not modelled". That is true and useless: someone
asking for "an SoC with two cores and 64 KB of RAM" -- the most ordinary
request there is -- learns that the tool has a gap, not that their design is
impossible.

It is impossible. Measured from the PDK's own LEFs:

    macro                             size (um)        area      per KB
    gf180mcu_fd_ip_sram__sram64x8    431.86 x 232.88   100,572   1.609 mm2
    gf180mcu_fd_ip_sram__sram128x8   431.86 x 268.88   116,119   0.929 mm2
    gf180mcu_fd_ip_sram__sram256x8   431.86 x 340.88   147,212   0.589 mm2
    gf180mcu_fd_ip_sram__sram512x8   431.86 x 484.88   209,400   0.419 mm2

The best density available is 0.419 mm2 per KB. For scale, the entire Block A
die -- a quarter of a shared MPW area, two harts, UART, SPI, timers, debug --
is 1.25 mm2, and Block C's four-hart die is 2.18 mm2. So:

    4 KB  =  1.68 mm2   already larger than Block A's whole die
   16 KB  =  6.70 mm2   three times Block C's whole die
   64 KB  = 26.80 mm2

`sram_kb: 0` across every design that has ever been hardened is not an
oversight. XIP from flash is the only architecture that fits, and this module
exists so the tool says that with a number instead of shrugging.

THE SECOND REASON, WHICH IS INDEPENDENT
---------------------------------------
Nothing in `hw/` or `configs/` instantiates a PDK SRAM macro. `sram_kb > 0`
produces x-heep's generic RAM, which synthesis maps to standard cells -- so
today the cost is not even the macro cost above, it is flip-flops, which for
64 KB is 524,288 bits of DFF plus decode. Comparable, and comparably
impossible.

Both routes are therefore closed, for different reasons, and closing either
one alone would not help. Adding macro support means adding a macro
instantiation path AND finding the silicon; this module quantifies the second
so nobody starts the first without knowing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# Measured from flow/librelane/gf180mcu/gf180mcuD/libs.ref/gf180mcu_fd_ip_sram/
# lef/*.lef -- the SIZE line of each macro. Not from a datasheet: these are the
# abstracts the placer would actually have to fit.
_LEF_ROOT = "flow/librelane/gf180mcu/gf180mcuD/libs.ref/gf180mcu_fd_ip_sram/lef"

#: Which PDK the macro table below describes. Every number here is GF180
#: geometry; see harness/physical/technology.py for where a second PDK's
#: macro root would be declared.
DENSEST_PDK = "gf180mcu"


@dataclass(frozen=True)
class SramMacro:
    """One PDK SRAM abstract, with the area it actually occupies."""

    name: str
    words: int
    bits: int
    width_um: float
    height_um: float

    @property
    def area_um2(self) -> float:
        return self.width_um * self.height_um

    @property
    def kib(self) -> float:
        return self.words * self.bits / 8 / 1024

    @property
    def mm2_per_kib(self) -> float:
        return self.area_um2 / self.kib / 1e6


SRAM_MACROS: Tuple[SramMacro, ...] = (
    SramMacro("gf180mcu_fd_ip_sram__sram64x8m8wm1", 64, 8, 431.86, 232.88),
    SramMacro("gf180mcu_fd_ip_sram__sram128x8m8wm1", 128, 8, 431.86, 268.88),
    SramMacro("gf180mcu_fd_ip_sram__sram256x8m8wm1", 256, 8, 431.86, 340.88),
    SramMacro("gf180mcu_fd_ip_sram__sram512x8m8wm1", 512, 8, 431.86, 484.88),
)

# The densest macro available. Bigger macros amortise their periphery over more
# bits, so this is always the largest one.
DENSEST = max(SRAM_MACROS, key=lambda m: m.kib / m.area_um2)


@dataclass(frozen=True)
class SramCost:
    """What a given amount of SRAM would take, and in what."""

    kib: int
    macro: SramMacro
    count: int
    area_um2: float

    @property
    def area_mm2(self) -> float:
        return self.area_um2 / 1e6

    def describe(self) -> str:
        return (f"{self.kib} KB of SRAM needs {self.count} x {self.macro.name} "
                f"= {self.area_mm2:.2f} mm2 of macro at "
                f"{self.macro.mm2_per_kib:.3f} mm2/KB, the densest this PDK "
                f"offers")


def sram_cost(kib: int, macro: Optional[SramMacro] = None,
              technology: Optional[str] = None) -> Optional[SramCost]:
    """Macro area for `kib` kilobytes, using the densest macro by default.

    Returns None for zero or negative, which is the supported case: every
    design hardened so far executes in place from flash.

    `technology` is `pdk` or `pdk:cell_library`. Omitted, it means GF180 --
    the only process whose macros are characterised here. Named as anything
    else it RAISES, because this table is GF180 abstracts measured from
    GF180 LEFs: IHP sg13g2 ships RM_IHPSG13_1P_* macros with entirely
    different geometry, and returning 0.419 mm2/KB for them would be a wrong
    number with no symptom. This function had no technology parameter at all
    until 2026-09-08, which is why the gap was invisible.
    """
    if kib <= 0:
        return None
    if technology is not None:
        from harness.physical.technology import resolve, technology_gap
        tech = resolve(technology)
        if tech is None:
            raise ValueError(technology_gap(technology))
        if tech.sram_lef_root is None:
            raise ValueError(
                f"no characterised SRAM macros for {tech.key}: its collateral "
                "declares a macro set that nobody has measured here, so there "
                "is no area basis for sram_kb > 0. Measure the SIZE lines from "
                "its LEFs and add a macro table, or keep the design XIP")
        if tech.pdk != DENSEST_PDK:
            raise ValueError(
                f"the macro table is {DENSEST_PDK} geometry; {tech.key} needs "
                "its own measurements")
    macro = macro or DENSEST
    import math

    count = max(1, math.ceil(kib / macro.kib))
    return SramCost(kib=kib, macro=macro, count=count,
                    area_um2=count * macro.area_um2)


def largest_sram_that_fits(area_um2: float) -> float:
    """How much SRAM a given area could hold, in KB. Usually a hard truth.

    Note this ignores everything else the die must contain, so it is an upper
    bound on an upper bound.
    """
    if area_um2 <= 0:
        return 0.0
    return (area_um2 / DENSEST.area_um2) * DENSEST.kib
