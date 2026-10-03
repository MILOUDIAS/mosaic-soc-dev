"""Per-technology facts, and the silent failures they replace.

The goal is any SoC on ANY PDK. Before this, five of `pdk-port`'s eight
requirements were satisfied for GF180 only by being hardcoded, and a second
PDK did not hit a "this PDK is unsupported" check -- it got GF180's numbers
back, labelled as though they had been measured.

These tests pin the two properties that matter: GF180 answers do not move, and
a technology nobody has declared is REFUSED rather than served another
process's geometry.
"""
from __future__ import annotations

import json
import pathlib
import tempfile

from pathlib import Path

import pytest

from harness.physical.floorplan import (
    SITE_HEIGHT_UM, SITE_WIDTH_UM, margin_multiples)
from harness.physical.technology import (
    DEFAULT_LIBRARY, TECHNOLOGIES, resolve, technology_gap)


# ── the registry ─────────────────────────────────────────────────────
def test_gf180_seven_track_matches_the_module_constants():
    """The constants are the DEFAULT, so the two must not drift apart."""
    tech = resolve("gf180mcu")
    assert tech is not None
    assert tech.site == (SITE_WIDTH_UM, SITE_HEIGHT_UM)


def test_one_pdk_can_hold_two_geometries():
    """The reason entries are keyed by technology and not by PDK: GF180's
    9-track library is 28.6% taller in the same PDK tree."""
    seven = resolve("gf180mcu:gf180mcu_fd_sc_mcu7t5v0")
    nine = resolve("gf180mcu:gf180mcu_fd_sc_mcu9t5v0")
    assert seven.site_height_um == 3.92 and nine.site_height_um == 5.04
    assert seven.pdk == nine.pdk


def test_the_second_pdk_is_declared_from_its_own_lef():
    """IHP sg13g2, read from SITE CoreSite in the stdcell LEF."""
    tech = resolve("ihp-sg13g2")
    assert tech is not None
    assert tech.site == (0.48, 3.78)
    assert tech.typical_corner_token == "typ_1p20V_25C"
    assert "sg13g2_stdcell.lef" in tech.source


def test_an_undeclared_technology_is_refused_by_name():
    assert resolve("sky130") is None
    gap = technology_gap("sky130")
    assert gap and "no declared site geometry" in gap
    assert "technology.py" in gap        # says where to add it


def test_a_bare_pdk_only_resolves_where_a_default_is_declared():
    """Silently picking a library is the failure this module removes."""
    for pdk in DEFAULT_LIBRARY:
        assert resolve(pdk) is not None
    assert resolve("gf180mcu:not_a_library") is None
    assert resolve(None) is None and resolve("") is None


def test_every_entry_records_where_its_numbers_came_from():
    for key, tech in TECHNOLOGIES.items():
        assert tech.source, f"{key} has no provenance"
        assert tech.site_width_um > 0 and tech.site_height_um > 0
        assert tech.key == key


# ── the site grid: the number that reaches a die ─────────────────────
def test_gf180_margin_multiples_are_unchanged():
    """Every tapeout run used these. A refactor that moves them is a bug."""
    assert margin_multiples(10.0) == {
        "BOTTOM_MARGIN_MULT": 3, "TOP_MARGIN_MULT": 3,
        "LEFT_MARGIN_MULT": 18, "RIGHT_MARGIN_MULT": 18}
    assert margin_multiples(10.0) == margin_multiples(10.0, "gf180mcu")


def test_a_different_site_gives_a_different_ring():
    """The concrete silent failure: 10 um is 18 GF180 columns and 21 IHP
    ones, and nothing used to notice."""
    gf = margin_multiples(10.0, "gf180mcu")
    ihp = margin_multiples(10.0, "ihp-sg13g2")
    assert ihp["LEFT_MARGIN_MULT"] == 21
    assert gf["LEFT_MARGIN_MULT"] != ihp["LEFT_MARGIN_MULT"]
    # taller rows, fewer of them
    nine = margin_multiples(10.0, "gf180mcu:gf180mcu_fd_sc_mcu9t5v0")
    assert nine["TOP_MARGIN_MULT"] == 2 < gf["TOP_MARGIN_MULT"]


def test_an_undeclared_technology_raises_rather_than_defaulting():
    """Falling back to GF180 here is exactly the bug: a ring rounded to
    another process's grid is a wrong number that looks like a right one."""
    with pytest.raises(ValueError) as e:
        margin_multiples(10.0, "sky130")
    assert "no declared site geometry" in str(e.value)


# ── corner selection ─────────────────────────────────────────────────
def _run_with(pdk: str, lib: str) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "resolved.json").write_text(
        json.dumps({"PDK": pdk, "STD_CELL_LIBRARY": lib}))
    return d


def test_the_typical_corner_token_follows_the_technology():
    """`tt_025C` is GF180's spelling and nobody else's. IHP writes
    `typ_1p20V_25C`, which shares no substring with it -- so the old literal
    matched nothing and the code fell through to whichever liberty sorted
    first, which can be the slow or the fast corner."""
    from harness.physical.netlist import typical_corner_token
    assert typical_corner_token(
        _run_with("gf180mcu", "gf180mcu_fd_sc_mcu7t5v0")) == "tt_025C"
    assert typical_corner_token(
        _run_with("ihp-sg13g2", "sg13g2_stdcell")) == "typ_1p20V_25C"


def test_a_run_without_resolved_json_keeps_the_gf180_token():
    from harness.physical.netlist import typical_corner_token
    assert typical_corner_token(pathlib.Path(tempfile.mkdtemp())) == "tt_025C"


# ── the skill's own report ───────────────────────────────────────────
def test_declaring_a_technology_reduces_its_silent_gaps():
    """Declared geometry must show up as progress, and only for the
    technology that was declared."""
    from harness.skills.pdk_port import audit
    ihp = audit("ihp-sg13g2:sg13g2_stdcell").details
    undeclared = audit("sky130A:sky130_fd_sc_hd").details
    for key in ("site-geometry", "corner-names", "cell-library"):
        assert key in ihp["satisfied"]
        assert key not in undeclared["satisfied"]
    assert len(ihp["missing"]) < len(undeclared["missing"])


needs_pdk = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2]
         / "flow/librelane/gf180mcu").is_dir(),
    reason="GF180 PDK not cloned (make -C flow/librelane clone-pdk)")


@needs_pdk
def test_gf180_still_satisfies_every_requirement():
    from harness.skills.pdk_port import audit
    assert audit().ok


def test_declared_geometry_does_not_imply_a_measured_area():
    """The split that keeps this honest: geometry is one line out of a LEF,
    area is a multi-hour run. IHP has the first and not the second."""
    from harness.physical.floorplan import calibration_gap
    assert resolve("ihp-sg13g2") is not None
    assert calibration_gap("ihp-sg13g2") is not None


# ── the remaining gaps announce themselves ───────────────────────────
def test_sram_refuses_a_technology_it_has_no_macros_for():
    """sram_cost had NO technology parameter at all, which is why this gap
    was invisible: 0.419 mm2/KB came back for any PDK you asked about."""
    from harness.physical.sram import sram_cost
    assert sram_cost(64) is not None                       # GF180 default
    assert sram_cost(64, technology="gf180mcu") is not None
    with pytest.raises(ValueError) as declared:
        sram_cost(64, technology="ihp-sg13g2")
    assert "no characterised SRAM macros" in str(declared.value)
    with pytest.raises(ValueError) as undeclared:
        sram_cost(64, technology="sky130")
    assert "no declared site geometry" in str(undeclared.value)


def test_sram_still_returns_none_for_zero_before_any_technology_check():
    """XIP designs are the supported case and must not start raising."""
    from harness.physical.sram import sram_cost
    assert sram_cost(0, technology="ihp-sg13g2") is None


def test_routability_reports_unvalidated_rather_than_refusing():
    """Refusing would block the first hardening run on a new PDK -- which is
    the run that would produce the missing observation."""
    from harness.physical.routability import recommended_utilisation
    gf = recommended_utilisation(2, "gf180mcu")
    ihp = recommended_utilisation(2, "ihp-sg13g2")
    assert gf.basis == "demonstrated" and gf.validated
    assert ihp.basis == "unvalidated" and not ihp.validated
    assert ihp.utilisation <= gf.utilisation      # conservative, not optimistic
    assert "ihp-sg13g2" in ihp.reason and "metal stack" in ihp.reason


def test_routability_without_a_technology_is_unchanged():
    from harness.physical.routability import recommended_utilisation
    assert recommended_utilisation(2) == recommended_utilisation(2, "gf180mcu")


def test_a_declared_technology_has_no_silent_gaps_left():
    """The point of this stage. IHP is NOT ported -- five requirements are
    still missing -- but every one of them announces itself."""
    from harness.skills.pdk_port import audit
    ihp = audit("ihp-sg13g2:sg13g2_stdcell")
    assert not ihp.ok, "IHP is not ported and must not claim to be"
    assert ihp.errors == [], f"still silent: {ihp.errors}"
    assert len(ihp.details["missing"]) == 5


def test_an_undeclared_technology_still_has_silent_gaps():
    """The guard is earned per technology, not granted globally."""
    from harness.skills.pdk_port import audit
    sky = audit("sky130A:sky130_fd_sc_hd")
    assert not sky.ok and sky.errors, "an undeclared PDK must still warn loudly"


def test_missing_and_silent_are_different_facts():
    """routability on IHP is still MISSING; it just says so now."""
    from harness.skills.pdk_port import audit
    d = audit("ihp-sg13g2:sg13g2_stdcell").details
    keys = {m["key"] for m in d["missing"]}
    assert "routability-ceiling" in keys and "sram-macros" in keys


# ── bootstrapping a technology nobody has measured ───────────────────
# NOTE: derive_floorplan and generate_hardening_config take the INNER `soc`
# mapping, not the whole file. Handing them {"soc": ...} does not raise -- it
# coerces to defaults, so `pdk` silently reads back as gf180mcu.
IHP_SOC = {
    "name": "probe", "pdk": "ihp-sg13g2", "target": "simulation",
    "cores": [{"ip": "serv", "count": 1, "role": "titan", "isa": "rv32ic"}],
    "memory": {"sram_kb": 0, "scratchpad_bytes": 512, "boot_rom_kb": 1},
}


def test_sizing_a_die_on_an_uncalibrated_technology_is_refused():
    """No mandated die: the model is being asked to CHOOSE, and it cannot."""
    from harness.physical.floorplan import derive_floorplan
    fp, errors = derive_floorplan(dict(IHP_SOC))
    assert fp is None
    assert any("no area calibration" in e for e in errors)


def test_a_mandated_die_bootstraps_instead_of_refusing():
    """The chicken-and-egg the probe hit: calibration needs a hardening run,
    and the run needed calibration. When the die is MANDATED the model is not
    choosing it -- it is only checking fit -- and "I cannot check" is a
    warning, not a reason to emit nothing."""
    from harness.physical.floorplan import derive_floorplan
    soc = dict(IHP_SOC, objectives={"target_clock_mhz": 10, "die_um": 700})
    fp, errors = derive_floorplan(soc)
    assert not errors and fp is not None
    assert fp.die_side_um == 700.0
    assert fp.basis == "hand-sized"
    assert fp.logic_um2 is None, "an unmeasured area must not be a number"
    assert any("NO FIT CHECK" in w for w in fp.warnings)
    assert any("no area calibration" in w for w in fp.warnings)


def test_a_hand_sized_floorplan_is_reported_not_formatted_as_measured():
    """hardening.py formats logic_um2 with :,.0f, which would crash on None
    -- and printing a number there would be worse than crashing."""
    from harness.physical.floorplan import derive_floorplan
    from harness.physical.hardening import render_derived_block
    soc = dict(IHP_SOC, objectives={"target_clock_mhz": 10, "die_um": 700})
    fp, _ = derive_floorplan(soc)
    block = render_derived_block("probe", fp, 100.0)
    assert "NOT ESTIMATED" in block and "hand-sized" in block


def test_gf180_floorplans_are_unaffected_by_the_bootstrap_path():
    from harness.physical.floorplan import derive_floorplan
    import yaml
    from harness.core import REPO_ROOT
    soc = yaml.safe_load(
        (REPO_ROOT / "configs/mosaic_tapeout_ultra.yaml").read_text())["soc"]
    fp, errors = derive_floorplan(soc)
    assert not errors and fp is not None
    assert fp.basis != "hand-sized" and fp.logic_um2 is not None


# ── the wrong-process hardening config ───────────────────────────────
def test_a_hardening_config_is_refused_for_another_process():
    """The severe one. Every layer above accepted `pdk: ihp-sg13g2`, this step
    emitted a GF180 config naming no PDK at all, and run_signoff.sh hardens
    with a hardcoded --pdk gf180mcuD -- a run that succeeds and measures the
    wrong process."""
    from harness.core import REPO_ROOT
    from harness.physical.hardening import generate_hardening_config
    soc = dict(IHP_SOC, objectives={"target_clock_mhz": 10, "die_um": 700})
    text, errors = generate_hardening_config(
        soc, "probe", repo_root=REPO_ROOT)
    assert text is None and errors
    joined = " ".join(errors)
    assert "PNR_CORNERS" in joined and "gf180mcuD" in joined
    assert "--template" in joined, "a refusal must say how to proceed"


def test_gf180_hardening_configs_still_generate():
    import yaml
    from harness.core import REPO_ROOT
    from harness.physical.hardening import generate_hardening_config
    soc = yaml.safe_load(
        (REPO_ROOT / "configs/mosaic_tapeout_ultra.yaml").read_text())["soc"]
    text, errors = generate_hardening_config(
        soc, "mosaic_block_a", repo_root=REPO_ROOT)
    assert text and not errors


def test_a_template_declares_its_own_process():
    """The guard used to assume every template was GF180's, which refused the
    IHP template for an IHP design. A template says what it is for, in a
    COMMENT -- the rendered file goes to LibreLane, which has no key for this
    and should not be handed one it does not know."""
    from harness.core import REPO_ROOT
    from harness.physical.hardening import (
        TEMPLATE_TECHNOLOGY_MARKER, generate_hardening_config)
    for rel, expect in (("flow/librelane/signoff_template.yaml", "gf180mcu"),
                        ("flow/librelane/signoff_template_ihp.yaml",
                         "ihp-sg13g2")):
        head = (REPO_ROOT / rel).read_text().splitlines()[0]
        assert head.startswith(TEMPLATE_TECHNOLOGY_MARKER)
        assert head.split(":", 1)[1].strip() == expect

    soc = dict(IHP_SOC, objectives={"target_clock_mhz": 10, "die_um": 700})
    text, errors = generate_hardening_config(
        soc, "probe", repo_root=REPO_ROOT,
        template="flow/librelane/signoff_template_ihp.yaml")
    assert text and not errors, errors
    assert "nom_typ_1p20V_25C" in text
    assert "tt_025C" not in text, "GF180 corner names must not leak in"


def test_the_ihp_template_does_not_inherit_gf180_numbers():
    """Copying the GF180 template would move its numbers and leave the
    reasoning that justified them behind."""
    from harness.core import REPO_ROOT
    t = (REPO_ROOT / "flow/librelane/signoff_template_ihp.yaml").read_text()
    # The prose SHOULD discuss GF180 -- explaining what was not inherited is
    # the point. What must not appear is a GF180 VALUE.
    import re, yaml
    keys = yaml.safe_load(t)
    assert "MAX_TRANSITION_CONSTRAINT" not in keys
    assert "GRT_DESIGN_REPAIR_MAX_SLEW_PCT" not in keys
    settings = "\n".join(l for l in t.splitlines()
                         if l.strip() and not l.lstrip().startswith("#"))
    for gf180_value in ("tt_025C", "ss_125C", "ff_n40C", "5v00", "4v50"):
        assert gf180_value not in settings, (
            f"{gf180_value} is a GF180 corner value and must not be a setting "
            "in the IHP template")


# ── the RTL's technology cells ───────────────────────────────────────
# Found by hardening Block A on IHP and reading what broke, in order.
def test_the_clock_gate_is_selected_by_pdk():
    """The first IHP run died at Verilator lint with
        Can't resolve module reference: 'gf180mcu_fd_sc_mcu7t5v0__icgtp_1'
    after the whole RTL had elaborated. hw/asic/ already had per-PDK
    directories; gen_filelist just always picked GF180's."""
    import sys
    from harness.core import REPO_ROOT
    sys.path.insert(0, str(REPO_ROOT / "flow/librelane/scripts"))
    import gen_filelist as g
    assert any(c.endswith("gf180/tc_clk.sv") for c in g.tech_cells("gf180mcu"))
    assert any(c.endswith("ihp-sg13g2/tc_clk.sv")
               for c in g.tech_cells("ihp-sg13g2"))
    for cells in g.TECH_CELLS_BY_PDK.values():
        for rel in cells:
            assert (REPO_ROOT / rel).is_file(), f"{rel} registered but absent"


def test_an_unregistered_pdk_is_refused_not_given_gf180s_cell():
    """sky130 has an hw/asic/ directory and no tc_clk.sv -- exactly the case
    that would otherwise fail the same confusing way, late and misattributed."""
    import sys
    import pytest as _pytest
    from harness.core import REPO_ROOT
    sys.path.insert(0, str(REPO_ROOT / "flow/librelane/scripts"))
    import gen_filelist as g
    with _pytest.raises(SystemExit) as e:
        g.tech_cells("sky130")
    assert "no technology cells" in str(e.value)
    assert "hw/asic/<pdk>/tc_clk.sv" in str(e.value)


def test_the_ihp_clock_gate_builds_test_enable_outside_the_cell():
    """GF180's icgtp_1 has a TE pin; IHP's sg13g2_lgcp_1 has only
    (GCLK, CLK, GATE). The scan behaviour TE provides has to be OR'd in
    outside the cell, which is real logic GF180 does not pay for."""
    from harness.core import REPO_ROOT
    src = (REPO_ROOT / "hw/asic/ihp-sg13g2/tc_clk.sv").read_text()
    assert "sg13g2_lgcp_1" in src
    assert "en_i | test_en_i" in src
    # The prose SHOULD compare against GF180 -- explaining the missing TE pin
    # is the point of the file. What must not appear is a GF180 CELL.
    code = "\n".join(l for l in src.splitlines()
                      if not l.lstrip().startswith("//"))
    assert "gf180mcu_fd_" not in code
    for mod in ("tc_clk_gating", "tc_clk_and2", "tc_clk_buffer",
                "tc_clk_inverter"):
        assert f"module {mod}" in src, f"{mod} missing from the IHP variant"


def test_the_rtl_hardcodes_only_the_technology_cells_we_know_about():
    """The porting surface, pinned. Two cells: the ICG (now per-PDK) and a
    tristate pad driver in each delivery wrapper (still GF180-only). If a
    third appears, this fails and someone has to decide where it belongs."""
    import re
    from harness.core import REPO_ROOT
    found = set()
    for pat in ("hw/**/*.sv", "flow/librelane/experimental/*.sv"):
        for f in REPO_ROOT.glob(pat):
            found |= set(re.findall(r"gf180mcu_fd_(?:sc|io)_\w+", _code(f.read_text())))
    assert found == {"gf180mcu_fd_sc_mcu7t5v0__icgtp_1",
                     "gf180mcu_fd_sc_mcu7t5v0__bufz_4"}, sorted(found)


def _code(src):
    """SystemVerilog without its comments: a cell NAMED in prose is not a cell
    the RTL instantiates."""
    import re
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)


def test_the_delivery_wrappers_are_process_neutral():
    """The wrappers are PER DESIGN, so a process-specific cell in them costs
    designs x PDKs. Both technology cells now live in hw/asic/<pdk>/ and the
    wrappers instantiate the neutral names.

    One kind of wrapper is process-specific by nature: a padframe interface,
    whose port list is the integrator's pad cells' terminals (Block A's D15
    wrapper drives GF180 pad drive strength, slew and Schmitt select). It must
    say so with a `mosaic-technology:` marker, and it still instantiates no
    technology cell itself -- the pads are the integrator's."""
    import re
    from harness.core import REPO_ROOT
    for name in ("a", "b", "c"):
        src = (REPO_ROOT / f"flow/librelane/experimental/mosaic_block_{name}.sv"
               ).read_text()
        code = _code(src)
        assert "gf180mcu_fd_" not in code, f"block {name} instantiates a GF180 cell"
        if re.search(r"^// mosaic-technology: \S+", src, re.M):
            continue  # a declared padframe interface: its pads are external
        assert "gf180mcu_fd_" not in src, f"block {name} still names a GF180 cell"
        assert "tc_pad_tristate" in src


def test_the_tristate_enable_polarity_is_inverted_for_ihp():
    """GF180's bufz is bufif0(Z, EN & I, ~EN) -- enable ACTIVE HIGH. IHP's
    ebufn is bufif0(Z, A, TE_B) -- ACTIVE LOW. Getting this wrong would drive
    the bus exactly when it should be released, which is a bus fight, not a
    lint error."""
    from harness.core import REPO_ROOT
    gf = (REPO_ROOT / "hw/asic/gf180/tc_pad.sv").read_text()
    ihp = (REPO_ROOT / "hw/asic/ihp-sg13g2/tc_pad.sv").read_text()
    assert ".EN(en_i)" in gf and "~en_i" not in gf
    assert "~en_i" in ihp and "sg13g2_ebufn_4" in ihp
    for src in (gf, ihp):
        assert "module tc_pad_tristate" in src


def test_the_pad_cell_port_is_output_not_inout():
    """yosys-slang refuses to inline a module with an `inout` port connected
    to a bit-select ("cannot be inlined; see yosys-slang issue #143"),
    measured on mosaic_block_a.sv:102. It is also the wrong direction on its
    own terms: the cell only DRIVES the pad, and the read-back is a separate
    assign in the wrapper -- which is why the PDK cells declare `output Z`."""
    from harness.core import REPO_ROOT
    for rel in ("hw/asic/gf180/tc_pad.sv", "hw/asic/ihp-sg13g2/tc_pad.sv"):
        src = (REPO_ROOT / rel).read_text()
        assert "output wire  pad_io" in src, rel
        code = "\n".join(l for l in src.splitlines()
                          if not l.lstrip().startswith("//"))
        assert "inout" not in code, f"{rel} declares an inout port"
