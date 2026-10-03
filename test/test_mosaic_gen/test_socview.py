"""SoCView, DEF pins and the SoC diagrams.

The view's whole claim is that it describes the RTL, not a hand-kept copy of
it. topo-viz's own digest drew 13 crossbar masters and 2 RAM banks for Block C;
the generated package says 11 and 1. So the tests compare the view against the
generated package of every bundle on disk, render the package template and
require byte equality, and build a view for the covering array's configs --
the generated design space, not the shipped examples.

DEF fixtures are verbatim excerpts of real output (a hardened run's final DEF
and the Chipathon D15 padframe DEF), because the two place pins differently and
a synthetic DEF would test neither.
"""

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from harness.diagram import chip_svg, logical_svg, standalone_html  # noqa: E402
from harness.physical.defpins import parse_def_pins  # noqa: E402
from harness.socview import build_view, localparams, render_pkg, _xheep_kwargs  # noqa: E402
from util.mosaic_gen.core_registry import CORE_SPECS  # noqa: E402

BLOCKC = REPO / "configs/mosaic_blockc_4hart.yaml"
ALLCORES = REPO / "configs/mosaic_all_cores.yaml"


def _bundles_matching_their_config():
    """Bundles whose recorded config is byte-identical to the file today."""
    out = []
    for man in sorted((REPO / "build/mosaic").glob("*/manifest.json")):
        m = json.loads(man.read_text())
        rec = m["inputs"]["mosaic_config"]
        cfg = Path(rec["path"])
        cfg = cfg if cfg.is_absolute() else REPO / cfg
        if cfg.is_file() and hashlib.sha256(cfg.read_bytes()).hexdigest() == rec["sha256"]:
            out.append((man.parent, cfg))
    return out


BUNDLES = _bundles_matching_their_config()


@pytest.mark.skipif(not BUNDLES, reason="no generated bundle on disk")
@pytest.mark.parametrize("bundle,cfg", BUNDLES, ids=[b.name for b, _ in BUNDLES])
def test_view_counts_equal_the_generated_package(bundle, cfg):
    pkg = localparams((bundle / "generated/hw/core-v-mini-mcu/include/"
                       "core_v_mini_mcu_pkg.sv").read_text())
    v = build_view(cfg)
    assert v["crossbar"]["nmaster"] == pkg["SYSTEM_XBAR_NMASTER"]
    assert v["crossbar"]["nslave"] == pkg["SYSTEM_XBAR_NSLAVE"]
    assert v["memory"]["banks"] == pkg["NUM_BANKS"]
    assert v["plic"]["sources"] == pkg["PLIC_NINT"]
    assert len(v["crossbar"]["masters"]) == pkg["SYSTEM_XBAR_NMASTER"]


@pytest.mark.skipif(not BUNDLES, reason="no generated bundle on disk")
def test_in_memory_package_is_the_generated_file():
    bundle, cfg = BUNDLES[0]
    gen = (bundle / "generated/hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv").read_text()
    assert render_pkg(_xheep_kwargs(cfg, REPO), REPO) == gen


def test_block_c_is_what_the_templates_instantiate():
    v = build_view(BLOCKC)
    assert v["crossbar"]["nmaster"] == 11 and v["memory"]["banks"] == 1
    b = v["blocks"]
    # dma "none", debug off, AO timer and fast-interrupt removed; TDU + CLINT kept
    assert (b["dma"], b["debug"], b["rv_timer_ao"], b["fast_intr_ctrl"]) == (False,) * 4
    assert b["tdu"] and b["clint"]
    assert [p["name"] for p in v["address_map"]["peripherals"]] == ["uart"]
    assert not v["plic"]["instantiated"]
    # the removed blocks still own their crossbar ports and address windows
    dma_ports = [m for m in v["crossbar"]["masters"] if m["kind"] == "dma"]
    assert dma_ports and not any(m["active"] for m in dma_ports)
    ao = {p["name"]: p["instantiated"] for p in v["address_map"]["ao_peripherals"]}
    assert ao["rv_timer_ao"] is False and ao["soc_ctrl"] is True


def test_every_core_names_its_native_bus_and_its_wrapper_agrees():
    words = {"wishbone-lite": "wishbone lite", "wishbone-classic": "wishbone classic",
             "req-gnt": "req/gnt", "mem-valid-ready": "mem_valid", "reqrsp": "reqrsp",
             "ahb-lite": "ahb-lite", "axi4": "axi4", "tilelink-c": "tilelink-c"}
    for name, spec in CORE_SPECS.items():
        assert spec.native_bus, f"{name} has no native_bus"
        if not spec.sci:
            assert spec.native_bus == "obi", name
            continue
        header = "\n".join((REPO / f"hw/sci/{spec.sci_module}.sv").read_text().splitlines()[:40])
        assert words[spec.native_bus] in header.lower(), (name, spec.native_bus)


def _covering_rows(step):
    from harness.skills.tb_matrix import TbMatrix, synth_config
    rows, _ = TbMatrix()._plan_rows("render")
    return [synth_config(r) for r in rows[::step]]


@pytest.mark.parametrize("step", [
    10, pytest.param(1, marks=pytest.mark.slow)], ids=["sample", "all"])
def test_every_covering_array_config_has_a_view(tmp_path, step):
    for i, cfg in enumerate(_covering_rows(step)):
        p = tmp_path / f"m{i}.yaml"
        p.write_text(yaml.safe_dump(cfg))
        v = build_view(p)
        cores = sum(g["count"] for g in v["cores"])
        assert cores == v["num_harts"]
        assert sum(m["kind"] == "core" for m in v["crossbar"]["masters"]) == 2 * cores
        svg = logical_svg(v)
        ET.fromstring(svg)
        for g in v["cores"]:
            assert f">{g['ip']}" in svg


# ── DEF pins: verbatim excerpts ───────────────────────────────────────

HARDENED = """VERSION 5.8 ;
DESIGN mosaic_block_a ;
UNITS DISTANCE MICRONS 2000 ;
DIEAREA ( 0 0 ) ( 2220000 2220000 ) ;
PINS 4 ;
    - VDD + NET VDD + SPECIAL + DIRECTION INOUT + USE POWER
      + PORT
        + LAYER Metal5 ( -1097440 -5000 ) ( 1097440 5000 )
        + LAYER Metal5 ( -1097440 -155000 ) ( 1097440 -145000 )
        + LAYER Metal5 ( -1097440 -305000 ) ( 1097440 -295000 )
        + FIXED ( 1109920 2172500 ) N ;
    - clk_i + NET clk_i + DIRECTION INPUT + USE SIGNAL
      + PORT
        + LAYER Metal3 ( -560 -560 ) ( 560 560 )
        + PLACED ( 560 2175600 ) N ;
    - status_valid_o + NET status_valid_o + DIRECTION OUTPUT + USE SIGNAL
      + PORT
        + LAYER Metal3 ( -560 -560 ) ( 560 560 )
        + PLACED ( 560 1588720 ) N ;
    - uart_tx_o + NET uart_tx_o + DIRECTION OUTPUT + USE SIGNAL
      + PORT
        + LAYER Metal2 ( -560 -560 ) ( 560 560 )
        + PLACED ( 842800 560 ) N ;
END PINS
"""

PADFRAME = """VERSION 5.8 ;
DESIGN D15_A ;
UNITS DISTANCE MICRONS 200 ;
DIEAREA ( 0 0 ) ( 222000 222000 ) ;
PINS 2 ;
- clk_i + NET clk_i + DIRECTION INPUT + USE SIGNAL
  + LAYER Metal2 ( 0 21752 ) ( 200 21828 )
  + FIXED ( 0 0 ) N ;
- status_o_IN[3] + NET status_o_IN[3] + DIRECTION INPUT + USE SIGNAL
  + LAYER Metal2 ( 126752 221800 ) ( 126828 222000 )
  + FIXED ( 0 0 ) N ;
END PINS
"""


def test_def_pins_from_a_hardened_run(tmp_path):
    p = tmp_path / "a.def"
    p.write_text(HARDENED)
    d = parse_def_pins(p)
    assert d.die_um == (0, 0, 1110, 1110) and d.dbu == 2000
    pins = {x.name: x for x in d.pins}
    assert pins["clk_i"].side == "W" and pins["clk_i"].y_um == pytest.approx(1087.8)
    assert pins["uart_tx_o"].side == "S" and pins["uart_tx_o"].x_um == pytest.approx(421.4)
    # a power pin with straps across the die has no edge; it is not guessed
    assert pins["VDD"].side is None and pins["VDD"].shapes == 3


def test_def_pins_from_an_integrator_padframe(tmp_path):
    """FIXED (0 0) with absolute shapes, and UNITS 200 not 2000."""
    p = tmp_path / "d15.def"
    p.write_text(PADFRAME)
    d = parse_def_pins(p)
    pins = {x.name: x for x in d.pins}
    assert pins["clk_i"].side == "W" and pins["clk_i"].y_um == pytest.approx(108.95)
    assert pins["status_o_IN[3]"].side == "N"


def test_def_without_pins_is_an_error_not_an_empty_design(tmp_path):
    p = tmp_path / "bad.def"
    p.write_text("UNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 1 1 ) ;\n")
    with pytest.raises(ValueError):
        parse_def_pins(p)


D15 = REPO / "flow/librelane/integration/D15/project_defs/A"


@pytest.mark.skipif(not (D15 / "D15_A.def").is_file(), reason="D15 collateral not on disk")
def test_padframe_pin_sides_agree_with_the_integrators_pad_map():
    d = parse_def_pins(D15 / "D15_A.def")
    assert len(d.pins) == 167
    sides = {p.side for p in d.pins}
    slots = {p["slot"][0] for p in yaml.safe_load((D15 / "D15_A_pad_map.yaml").read_text())["pads"]}
    assert sides == slots == {"N", "W"}


# ── diagrams ──────────────────────────────────────────────────────────


def _no_external_urls(text):
    return re.findall(r"https?://[^\"'\s<]+", text) == \
        ["http://www.w3.org/2000/svg"] * text.count('xmlns="http://www.w3.org/2000/svg"')


def test_diagrams_are_deterministic_self_contained_svg():
    v = build_view(ALLCORES)
    for draw in (logical_svg, chip_svg):
        a, b = draw(v), draw(build_view(ALLCORES))
        assert a == b
        ET.fromstring(a)
        assert _no_external_urls(a)
    page = standalone_html(v)
    assert _no_external_urls(page)
    logical = logical_svg(v)
    for g in v["cores"]:
        assert f">{g['ip']}" in logical
        if g["sci_module"]:
            assert f">{g['sci_module']}<" in logical and f">{g['native_bus']}<" in logical
    for p in v["address_map"]["peripherals"]:
        assert f">{p['name']}  " in logical


def test_chip_view_says_when_io_and_die_are_unknown():
    v = build_view(ALLCORES)
    assert v["io"]["pins"] == [] and "not assigned" in v["io"]["basis"]
    assert "rv_plic" not in v["io"]["interfaces_needing_pins"]
    assert v["die"]["die_um"] is None
    svg = chip_svg(v)
    assert "not assigned" in svg and "die size not estimated" in svg


RUN_C = REPO / "flow/librelane/experimental/runs/opt_blockc_density_03"


@pytest.mark.skipif(not (RUN_C / "final/def/mosaic_block_c.def").is_file(),
                    reason="Block C run tree not on disk")
def test_chip_view_places_every_signal_pin_of_a_run():
    v = build_view(BLOCKC, run_dir=RUN_C)
    assert v["io"]["placed"] and v["die"]["die_um"][2] == pytest.approx(1412.76)
    svg = chip_svg(v)
    for p in v["io"]["pins"]:
        if p["side"]:
            assert f"<title>{p['name']} " in svg


# ── regressions found by review (2026-09-24) ──────────────────────────


def test_def_statements_may_break_lines_anywhere_and_carry_mask(tmp_path):
    """`+ NET` on its own line was dropped; `MASK n` lost the geometry."""
    p = tmp_path / "wrap.def"
    p.write_text("UNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 100000 100000 ) ;\n"
                 "PINS 2 ;\n- a\n  + NET a + DIRECTION INPUT\n  + LAYER Metal2 MASK 1 ( 0 0 ) ( 200 200 )\n"
                 "  + PLACED ( 50000 99800 ) N ;\n"
                 "- b + NET b + DIRECTION OUTPUT + LAYER Metal2 SPACING 140 ( 0 0 ) ( 200 200 )\n"
                 "  + PLACED ( 99800 50000 ) N ;\nEND PINS\n")
    pins = {x.name: x for x in parse_def_pins(p).pins}
    assert pins["a"].side == "N" and pins["b"].side == "E"


def _fake_run(tmp_path, soc="mosaic_blockb_3hart", design="mosaic_block_b", die_area=None):
    run = tmp_path / "runs" / "r"
    (run / "final" / "def").mkdir(parents=True)
    resolved = {"DESIGN_NAME": design,
                "VERILOG_FILES": [f"/x/build/mosaic/{soc}-0123456789ab/generated/top.sv"]}
    if die_area:
        resolved["DIE_AREA"] = die_area
    (run / "resolved.json").write_text(json.dumps(resolved))
    (run / "final" / "def" / f"{design}.def").write_text(HARDENED.replace("mosaic_block_a", design))
    return run


def test_a_runs_die_is_its_own_not_a_later_derivation(tmp_path):
    """A relative-sized run has no DIE_AREA; its DEF has the die it was built at."""
    v = build_view(REPO / "configs/mosaic_blockb_3hart.yaml", run_dir=_fake_run(tmp_path))
    assert v["die"]["die_um"] == [0, 0, 1110, 1110] and "final DEF" in v["die"]["basis"]


def test_a_mistyped_run_or_another_socs_run_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a LibreLane run"):
        build_view(BLOCKC, run_dir=tmp_path / "nope")
    with pytest.raises(ValueError, match="hardened SoC 'mosaic_blockb_3hart'"):
        build_view(BLOCKC, run_dir=_fake_run(tmp_path))
