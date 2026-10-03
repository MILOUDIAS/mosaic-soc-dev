"""On-chip SRAM in GF180, and why every hardened design has none.

The point of this module is that "macro placement is not modelled" was a true
statement that answered the wrong question. A user asking for 64 KB of RAM is
not blocked by a gap in our tooling; they are asking for 26.8 mm2 of macro in
a project whose largest die is 2.18 mm2. The refusal has to say so.

The macro dimensions are checked against the PDK's own LEF files where the PDK
is present, so this cannot drift from the abstracts a placer would actually
have to fit.
"""

import re

import pytest

from harness.core import REPO_ROOT
from harness.physical.sram import (
    DENSEST,
    SRAM_MACROS,
    largest_sram_that_fits,
    sram_cost,
)

LEF_ROOT = (REPO_ROOT / "flow/librelane/gf180mcu/gf180mcuD/libs.ref"
            / "gf180mcu_fd_ip_sram/lef")


# ── the numbers come from the PDK, not from a docstring ──────────────

@pytest.mark.parametrize("macro", SRAM_MACROS, ids=lambda m: m.name)
def test_every_macro_matches_the_pdk_lef(macro):
    lef = LEF_ROOT / f"{macro.name}.lef"
    if not lef.is_file():
        pytest.skip("GF180 PDK not cloned")
    size = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", lef.read_text())
    assert size, f"no SIZE line in {lef}"
    width, height = float(size.group(1)), float(size.group(2))
    assert (macro.width_um, macro.height_um) == pytest.approx((width, height))


def test_the_densest_macro_is_the_largest_one():
    """Periphery amortises over bits, so this should always hold."""
    assert DENSEST.words == max(m.words for m in SRAM_MACROS)
    assert all(DENSEST.mm2_per_kib <= m.mm2_per_kib for m in SRAM_MACROS)


def test_density_is_the_measured_0_419_mm2_per_kb():
    assert DENSEST.mm2_per_kib == pytest.approx(0.419, abs=0.002)


# ── the fact that makes SRAM impossible here ─────────────────────────

def test_four_kilobytes_already_exceeds_the_taped_out_die():
    """Block A's whole die -- 2 harts, UART, SPI, timers, debug -- is 1.25 mm2."""
    assert sram_cost(4).area_mm2 > 1.25


def test_the_shipped_sram_configs_would_need_more_silicon_than_exists():
    """Several configs carry sram_kb 16-32. They can simulate, never harden."""
    assert sram_cost(16).area_mm2 == pytest.approx(6.70, abs=0.05)
    assert sram_cost(32).area_mm2 == pytest.approx(13.40, abs=0.05)
    assert sram_cost(64).area_mm2 == pytest.approx(26.80, abs=0.10)


def test_cost_counts_whole_macros():
    """A partial macro is not purchasable; 1 KB still costs two 512-byte parts."""
    assert sram_cost(1).count == 2
    assert sram_cost(64).count == 128
    assert sram_cost(64).area_um2 == pytest.approx(128 * DENSEST.area_um2)


def test_zero_sram_is_the_supported_case_not_an_error():
    assert sram_cost(0) is None
    assert sram_cost(-1) is None


def test_largest_that_fits_is_brutal():
    """Block C's ENTIRE die, given over to nothing but RAM, holds ~5 KB."""
    block_c_die_um2 = 1477.7 ** 2
    assert largest_sram_that_fits(block_c_die_um2) < 6
    assert largest_sram_that_fits(0) == 0


# ── the refusal has to carry the number ──────────────────────────────

def test_the_refusal_quantifies_instead_of_shrugging():
    from harness.physical import estimate_logic_area

    estimate = estimate_logic_area(
        {"cores": [{"ip": "serv", "count": 3}], "memory": {"sram_kb": 64}})
    assert estimate.basis == "unsupported"
    reason = estimate.reason
    # The number, the macro, and the comparison that makes it meaningful.
    assert "26.80 mm2" in reason
    assert "sram512x8" in reason
    assert "1.25" in reason and "2.18" in reason
    # And the second, independent reason.
    assert "flip-flops" in reason


def test_the_refusal_still_fires_for_every_nonzero_size():
    from harness.physical import derive_floorplan, estimate_logic_area

    for kb in (1, 4, 16, 32, 64):
        soc = {"cores": [{"ip": "serv", "count": 3}], "memory": {"sram_kb": kb}}
        assert estimate_logic_area(soc).basis == "unsupported"
        floorplan, errors = derive_floorplan(soc)
        assert floorplan is None and errors
