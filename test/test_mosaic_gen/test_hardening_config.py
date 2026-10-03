"""Emitting a hardening config instead of hand-writing one.

The acceptance test for Phase 2 is the first one here: regenerating Block A's
hardening config from its SoC config must reproduce the file that was actually
hardened, key for key. Block A is a real signed-off design -- 0 DRC, LVS clean,
GDS -- so if the generator agrees with it, the generator produces configs that
are known to work rather than configs that merely parse.
"""

import subprocess
import sys

import pytest
import yaml

from harness.core import REPO_ROOT
from harness.physical.hardening import (
    DERIVED_KEYS,
    generate_hardening_config,
    wrapper_path_for,
)

BLOCK_A_CONFIG = "flow/librelane/experimental/config_blocka_signoff.yaml"

# Block A's ACHIEVED logic/core utilisation under the current template. It was
# 0.813 before the repair-margin change and is 0.835 after: the same design in the
# same mandated 1117.5 um MPW slot, holding 2.68% more cell area.
#
# This is not a cosmetic bump. Deriving at the old 0.813 against the refreshed
# calibration asks for a 1127.9 um die and is REFUSED, correctly -- the slot is
# fixed and the design no longer fits at that density. Block A has less
# headroom in its slot than it did, and the number here has to say so.
BLOCK_A_UTILISATION = 0.835
TEMPLATE = "flow/librelane/signoff_template.yaml"


def block_a_soc() -> dict:
    soc = yaml.safe_load(
        (REPO_ROOT / "configs/mosaic_tapeout_ultra.yaml").read_text())["soc"]
    # The MPW slot is an input: Block A is a quarter of a 2235 um shared area.
    return dict(soc, objectives={"target_clock_mhz": 10, "die_um": 1117.5})


# ── the acceptance test ──────────────────────────────────────────────

# Keys the generator now sets that the taped-out config did not, each with the
# reason it is a deliberate divergence rather than drift. Adding to this set is
# a decision to sign off differently from Block A, so it needs a sentence.
INTENDED_DIVERGENCE = {
    # Block A was hardened with PnR optimising `nom_tt_025C_5v00` alone while
    # STA signed off all nine corners. Setting three corners is right in
    # principle; it was also MEASURED INERT (blocka_slewonly emits a
    # byte-identical netlist), so this divergence changes nothing but is kept
    # so the intent is on the record.
    "PNR_CORNERS",
    # Post-GRT repair ran with a 10% slew margin against global-route ESTIMATED
    # parasitics that extraction then made 1.15-1.65x worse, which is why 826
    # of Block C's violations appeared only after detailed routing.
    #
    # This entry said "45% ... AND stopped Block A booting -- an open defect".
    # RETRACTED 2026-08-15: the netlists at 32 and 45 hold the same 36,572
    # logic instances and differ only in buffering, which is transparent in a
    # zero-delay simulation; Blocks B and C boot at 45. The failure was the
    # oracle. The template ships 32, chosen because it is the highest margin
    # this project can currently CLEAR on evidence, not because 45 is bad.
    "GRT_DESIGN_REPAIR_MAX_SLEW_PCT",
    # LibreLane's post-DRT antenna repair loop stops at DRT_ANTENNA_REPAIR_ITERS
    # (default 3) OR at zero violations, whichever comes first -- and Tcl's &&
    # short-circuits, so on mosaic_block_c the final check never ran. Block A
    # and Block B converged inside the default and are unaffected by a higher
    # cap; Block C hit it with one violation left. 8 gives the loop room to
    # finish. Iterations are consumed only while violations remain, so this
    # costs nothing on a design that converges -- including Block A itself.
    "DRT_ANTENNA_REPAIR_ITERS",
    # Block A was signed off against `set_max_transition 4.0 [current_design]`
    # applied at all nine corners -- the tt_025C_5v00 number, while every one of
    # its 56 max-slew violations was at ss_125C_4v50 where the pins are rated to
    # 7.0 ns. PnR still targets 4.0 (removing that target was measured and
    # DEGRADES the design past the library's own limits, see runs/blocka_libtran);
    # only signoff moved, to each pin's own liberty limit. Measured on
    # runs/blocka_sdc: byte-identical netlist, max-slew 56 -> 0.
    "SIGNOFF_SDC_FILE",
}


def test_generated_block_a_equals_the_config_that_was_hardened():
    """Every key, against the config behind a signed-off GDS.

    Divergence is allowed only where it is listed and justified. The point of
    this test is that the generator emits configs KNOWN to work, so "it
    differs but I meant it" has to be written down, not discovered later.
    """
    text, errors = generate_hardening_config(
        block_a_soc(), "mosaic_block_a",
        repo_root=REPO_ROOT, target_utilisation=BLOCK_A_UTILISATION,
    )
    assert not errors, errors
    generated = yaml.safe_load(text)
    hardened = yaml.safe_load((REPO_ROOT / BLOCK_A_CONFIG).read_text())

    assert set(generated) - set(hardened) <= INTENDED_DIVERGENCE, {
        "unexplained new keys":
            sorted(set(generated) - set(hardened) - INTENDED_DIVERGENCE),
    }
    assert not set(hardened) - set(generated), {
        "dropped by the generator": sorted(set(hardened) - set(generated)),
    }
    differing = {k: (generated[k], hardened[k])
                 for k in generated
                 if k not in INTENDED_DIVERGENCE and generated[k] != hardened[k]}
    assert not differing, f"generated config diverged: {differing}"


def test_the_corner_set_covers_setup_hold_and_the_typical_case():
    """Pinned so the corner set cannot silently revert -- NOT because it fixes
    anything.

    PNR_CORNERS unset means OpenROAD optimises `DEFAULT_CORNER` only --
    `corners = self.config["PNR_CORNERS"] or [self.config["DEFAULT_CORNER"]]`
    -- while signoff STA checks all nine. That code path is real, and it means
    mid-PnR STA never looks at `ss`.

    What it does NOT mean, corrected 2026-08-13: that this caused the waived
    max-slew violations. It was credited with a 19% reduction from a confounded
    comparison in which the die also moved. The clean test -- blocka_slewonly,
    one differing key -- produced a BYTE-IDENTICAL netlist, so the corner set
    changed nothing measurable. This test asserts the setting is present and
    coherent; it does not assert it helps, because it does not.
    """
    template = yaml.safe_load(
        (REPO_ROOT / "flow/librelane/signoff_template.yaml").read_text())
    corners = template.get("PNR_CORNERS")
    assert corners, "PNR_CORNERS unset means single-corner PnR; see the template"

    # Worst slew and worst setup live in slow silicon, hot, on a low rail.
    assert any("ss" in c for c in corners), corners
    # And a fast corner, or fixing slew with buffers breaks hold instead.
    assert any("ff" in c for c in corners), corners

    # Every corner must be one STA will actually sign off.
    hardened = yaml.safe_load((REPO_ROOT / BLOCK_A_CONFIG).read_text())
    known = set(hardened.get("STA_CORNERS") or [])
    if known:
        assert set(corners) <= known, sorted(set(corners) - known)


def test_the_generated_config_is_valid_yaml_and_declares_a_flow():
    text, _ = generate_hardening_config(
        block_a_soc(), "mosaic_block_a", repo_root=REPO_ROOT,
        target_utilisation=BLOCK_A_UTILISATION)
    data = yaml.safe_load(text)
    assert data["meta"]["flow"] == "Classic"
    assert data["DESIGN_NAME"] == "mosaic_block_a"


# ── the template must not carry design-specific keys ─────────────────

def test_the_template_holds_no_derived_key():
    """A template that sets DESIGN_NAME wins or loses by YAML ordering."""
    template = yaml.safe_load((REPO_ROOT / TEMPLATE).read_text())
    assert not (set(DERIVED_KEYS) & set(template)), (
        f"template sets derived key(s): {sorted(set(DERIVED_KEYS) & set(template))}"
    )


def test_a_template_carrying_a_derived_key_is_refused(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text((REPO_ROOT / TEMPLATE).read_text() + "\nDESIGN_NAME: sneaky\n")
    text, errors = generate_hardening_config(
        block_a_soc(), "mosaic_block_a", repo_root=tmp_path,
        template=bad.name, target_utilisation=BLOCK_A_UTILISATION)
    assert text is None
    assert errors and "must not be in the shared template" in errors[0]


def test_the_template_keeps_the_comments_that_explain_it():
    """A YAML round-trip would drop them, and they are the expensive part."""
    raw = (REPO_ROOT / TEMPLATE).read_text()
    assert "PDN-0186" in raw or "PDN" in raw
    assert raw.count("#") > 40, "template lost its rationale comments"


# ── what must be refused ─────────────────────────────────────────────

def test_no_clock_objective_is_an_error_not_a_default():
    """A clock nobody chose is how an unjustified number reaches a datasheet."""
    soc = block_a_soc()
    soc.pop("objectives")
    text, errors = generate_hardening_config(
        soc, "mosaic_block_a", repo_root=REPO_ROOT, target_utilisation=BLOCK_A_UTILISATION)
    assert text is None
    assert errors and "no clock period" in errors[0]


def test_an_explicit_override_supplies_the_clock_without_an_objective():
    soc = block_a_soc()
    soc.pop("objectives")
    text, errors = generate_hardening_config(
        soc, "mosaic_block_a", repo_root=REPO_ROOT,
        target_utilisation=BLOCK_A_UTILISATION, clock_period_override=40.0)
    assert not errors
    assert yaml.safe_load(text)["CLOCK_PERIOD"] == 40.0


@pytest.mark.parametrize("name", ["", "not a module", "has-dashes", "3leading"])
def test_an_invalid_design_name_is_refused(name):
    text, errors = generate_hardening_config(
        block_a_soc(), name, repo_root=REPO_ROOT, target_utilisation=BLOCK_A_UTILISATION)
    assert text is None and errors


def test_an_uncalibrated_design_cannot_be_hardened():
    soc = yaml.safe_load(
        (REPO_ROOT / "configs/mosaic_picorv32.yaml").read_text())["soc"]
    soc = dict(soc, objectives={"target_clock_mhz": 10})
    text, errors = generate_hardening_config(
        soc, "mosaic_picorv32", repo_root=REPO_ROOT)
    assert text is None
    assert errors and "SERV-only" in errors[0]


def test_a_design_that_overflows_its_slot_is_refused():
    """Three harts in Block A's slot: caught here, not at DPL-0036."""
    soc = yaml.safe_load(
        (REPO_ROOT / "configs/mosaic_blockb_3hart.yaml").read_text())["soc"]
    soc = dict(soc, objectives={"target_clock_mhz": 10, "die_um": 1117.5})
    text, errors = generate_hardening_config(
        soc, "mosaic_block_b", repo_root=REPO_ROOT)
    assert text is None
    assert errors and "does not fit the slot" in errors[0]


# ── provenance travels with the numbers ──────────────────────────────

def test_the_derived_block_records_where_its_numbers_came_from():
    text, _ = generate_hardening_config(
        block_a_soc(), "mosaic_block_a", repo_root=REPO_ROOT,
        target_utilisation=BLOCK_A_UTILISATION)
    derived = text.split("DERIVED PER DESIGN")[1]
    assert "measured" in derived
    # The run the number came from, which is the RE-HARDENED one since the
    # calibration refresh. Asserting the tag rather than just "a citation
    # exists" is the point: if the calibration silently reverted to a pre-fix
    # run, the emitted config would cite it and this would catch it.
    assert "blocka_1110_ndr" in derived
    assert "do not hand-edit" in text
    assert "A request, not a result" in derived


def test_the_wrapper_path_follows_the_design_name():
    assert wrapper_path_for("mosaic_block_b").endswith(
        "experimental/mosaic_block_b.sv")
    assert (REPO_ROOT / wrapper_path_for("mosaic_block_a")).is_file()
    assert (REPO_ROOT / wrapper_path_for("mosaic_block_b")).is_file()


# ── the CLI ──────────────────────────────────────────────────────────

def test_cli_emits_a_config(tmp_path):
    source = tmp_path / "soc.yaml"
    source.write_text(yaml.safe_dump({"soc": block_a_soc()}, sort_keys=False))
    out = tmp_path / "hardening.yaml"
    result = subprocess.run(
        [sys.executable, "-m", "harness", "physical-intent", "harden",
         "--config", str(source), "--design", "mosaic_block_a",
         "--utilisation", str(BLOCK_A_UTILISATION), "--output", str(out)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert yaml.safe_load(out.read_text())["DESIGN_NAME"] == "mosaic_block_a"


def test_cli_reports_a_refusal_as_a_failure(tmp_path):
    """A design the AREA MODEL refuses, not one the validator refuses.

    This fixture used to be a bare `{ip: picorv32, count: 2}` with no role,
    isa or boot_addr, and it reached the SERV-only refusal only because the
    CLI parsed the config without validating it. It now validates first, so
    the fixture has to be a genuinely legal SoC that is merely outside the
    calibration — otherwise this asserts the wrong refusal.
    """
    source = tmp_path / "soc.yaml"
    source.write_text(yaml.safe_dump({"soc": {
        "name": "probe", "target": "rtl", "bus": "obi",
        "memory": {"sram_kb": 0},
        "scheduler": {"tdu": True},
        "cores": [
            {"ip": "cv32e20", "isa": "rv32imc", "count": 1, "role": "titan"},
            {"ip": "picorv32", "isa": "rv32i", "count": 2, "role": "atlas",
             "boot_addr": 0x40010000},
        ],
    }}))
    result = subprocess.run(
        [sys.executable, "-m", "harness", "physical-intent", "floorplan",
         "--config", str(source)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "SERV-only" in (result.stdout + result.stderr)
