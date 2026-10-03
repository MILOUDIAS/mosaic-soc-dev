"""WP0 truth items: names and semantics must match what is actually built.

Both fixes here come from `general_multicore_soc_generator_roadmap.md` §14
("Immediate truth and correctness issues"), which asks that a result never
claim more than the evidence supports.
"""

import re

from harness.core import REPO_ROOT


# ── §14.3: the physical flag must not claim qualification ────────────

def test_physical_flag_names_what_it_proves():
    src = (REPO_ROOT / "harness/agent.py").read_text()
    assert "physical_drc_lvs_ok" in src
    # The old name implied a qualified physical result.
    assert not re.search(r"\bphysical_ok\b", src.replace("`physical_ok`", "")), (
        "physical_ok survives outside the explanatory comment"
    )


def test_physical_scope_message_states_the_evidence():
    src = (REPO_ROOT / "harness/agent.py").read_text()
    assert "DRC and LVS evidence passed" in src


# ── §14.2: the TDU counter is activity, not energy ───────────────────

def test_tdu_counter_is_named_for_activity_not_energy():
    for path in (
        "hw/tdu/rtl/tdu.sv",
        "hw/tdu/rtl/tdu_pkg.sv",
        "sw/firmware/common/mosaic_hw.h",
        "sw/firmware/common/tdu.c",
        "sw/firmware/common/tdu.h",
        "util/xheep_gen/software_gen.py",
        "harness/skills/doc_gen.py",
    ):
        src = (REPO_ROOT / path).read_text()
        assert "ENERGY_COUNTER" not in src, f"{path} still exposes ENERGY_COUNTER"
        assert "energy_counter" not in src, f"{path} still uses energy_counter"


def test_tdu_register_offset_is_unchanged_by_the_rename():
    """A rename must not silently move the register and break firmware."""
    pkg = (REPO_ROOT / "hw/tdu/rtl/tdu_pkg.sv").read_text()
    hw_h = (REPO_ROOT / "sw/firmware/common/mosaic_hw.h").read_text()
    assert "TDU_ACTIVE_HART_CYCLES_OFFSET = 32'h1C" in pkg
    assert "#define TDU_ACTIVE_HART_CYCLES_REG_OFFSET 0x1Cu" in hw_h


def test_tdu_counter_saturates_rather_than_wraps():
    """The old comment claimed saturation while the RTL did plain addition.

    A wrapped counter can report a huge workload as a small one; a saturated
    one is at least an honest lower bound.
    """
    src = (REPO_ROOT / "hw/tdu/rtl/tdu.sv").read_text()
    assert "active_hart_cycles_sum" in src, "no 33-bit sum for carry detection"
    assert re.search(r"active_hart_cycles_sum\[32\]", src), (
        "carry out of bit 31 is not tested, so the counter still wraps"
    )
    assert "{32{1'b1}}" in src, "no saturation value"


def test_tdu_documents_that_it_is_not_energy():
    """The unit is Σ(active harts × cycles) and weights every hart equally."""
    src = (REPO_ROOT / "hw/tdu/rtl/tdu.sv").read_text()
    assert "NOT energy" in src
    header = (REPO_ROOT / "sw/firmware/common/tdu.h").read_text()
    assert "NOT energy" in header
