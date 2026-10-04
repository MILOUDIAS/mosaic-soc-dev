"""Contract tests for the selectable DMA engine (``soc.dma``).

MOSAIC historically hard-wired the pulp-platform iDMA (AGENTS.md still
fixes it as the default). Area work on the minimum-area tapeout config
showed the iDMA costs 0.319 mm2 in GF180 -- 8.2% of the whole SoC -- for a
design whose workers execute in place from flash and never issue a bulk copy.
``soc.dma`` therefore selects between:

    idma   pulp-platform iDMA (default; preserves every pre-existing config)
    xheep  x-heep's own simpler DMA
    none   no DMA engine instantiated at all

The default is ``idma`` precisely so that omitting the key cannot silently
change an existing design.
"""

import pathlib
import re
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.append(str(REPO / "util" / "mosaic_gen"))

from util.mosaic_gen.core_registry import VALID_DMA, validate_soc_config

CONFIGS = sorted((REPO / "configs").glob("mosaic_*.yaml"))


def _cfg(dma=None):
    soc = {
        "name": "dma_probe",
        "pdk": "gf180mcu",
        "target": "simulation",
        "cores": [
            {"ip": "cv32e20", "count": 1, "role": "titan", "isa": "rv32imc"},
            {"ip": "serv", "count": 1, "role": "atlas", "isa": "rv32i",
             "boot_addr": 0x00004000},
        ],
        "memory": {"sram_kb": 32, "boot_rom_kb": 1},
        "bus": "obi",
        "scheduler": {"tdu": True, "mode": "dynamic"},
        "peripherals": ["uart"],
    }
    if dma is not None:
        soc["dma"] = dma
    return {"soc": soc}


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------


def test_valid_dma_set_is_exactly_the_three_documented_kinds():
    assert VALID_DMA == frozenset({"idma", "xheep", "none"})


@pytest.mark.parametrize("kind", ["idma", "none"])
def test_wired_dma_kinds_are_accepted(kind):
    assert validate_soc_config(_cfg(kind)) == []


def test_xheep_dma_is_refused_on_multicore_rather_than_silently_ignored():
    """The template picks the flavour by is_mc, so 'xheep' on a multi-core SoC
    would instantiate the iDMA anyway. Refuse it, and say why."""
    errors = validate_soc_config(_cfg("xheep"))
    assert errors, "'xheep' must not quietly resolve to the iDMA"
    joined = " ".join(errors)
    assert "xheep" in joined and "multi-core" in joined, joined


def test_xheep_dma_stays_in_the_vocabulary():
    """Kept valid as a *value* so the error explains the gap instead of
    reporting an unknown key."""
    assert "xheep" in VALID_DMA


def test_omitting_dma_is_still_valid():
    assert validate_soc_config(_cfg()) == []


def test_unknown_dma_kind_is_rejected_by_name():
    errors = validate_soc_config(_cfg("bogus"))
    assert errors, "an unknown DMA kind must not validate"
    joined = " ".join(errors)
    assert "dma" in joined and "bogus" in joined, joined


# --------------------------------------------------------------------------
# default preserves behaviour
# --------------------------------------------------------------------------


def test_dma_defaults_to_idma_on_the_dataclass():
    from mosaic_config import MosaicConfig

    assert MosaicConfig.__dataclass_fields__["dma"].default == "idma"


def test_omitting_dma_yields_idma(tmp_path):
    from mosaic_config import load_mosaic_yaml

    p = tmp_path / "no_dma_key.yaml"
    p.write_text(yaml.safe_dump(_cfg()))
    assert load_mosaic_yaml(p).dma == "idma"


@pytest.mark.parametrize("kind", ["idma", "none"])
def test_dma_round_trips_through_the_loader(tmp_path, kind):
    from mosaic_config import load_mosaic_yaml

    p = tmp_path / f"dma_{kind}.yaml"
    p.write_text(yaml.safe_dump(_cfg(kind)))
    assert load_mosaic_yaml(p).dma == kind


# --------------------------------------------------------------------------
# the knob has to reach the generated hjson, or it is decorative
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind,expected", [("idma", "yes"), ("none", "no")])
def test_dma_kind_drives_is_included(tmp_path, kind, expected):
    import hjson
    from jsonref import JsonRef

    from mosaic_config import _resolve_base_config, load_mosaic_yaml

    p = tmp_path / f"res_{kind}.yaml"
    p.write_text(yaml.safe_dump(_cfg(kind)))
    cfg = load_mosaic_yaml(p)

    base = JsonRef.replace_refs(hjson.load(open(REPO / "configs/general.hjson")))
    resolved = _resolve_base_config(base, cfg)
    assert resolved["ao_peripherals"]["dma"]["is_included"] == expected


# --------------------------------------------------------------------------
# anti-drift: the choice is explicit in every shipped config
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_every_shipped_config_states_its_dma_explicitly(path):
    """A silent default is fine for the schema and wrong for a tapeout config.

    A reviewer should be able to see what DMA a design carries without knowing
    what the loader defaults to.
    """
    soc = yaml.safe_load(path.read_text()).get("soc", {})
    assert "dma" in soc, f"{path.name} does not state soc.dma"
    assert soc["dma"] in VALID_DMA


def test_the_minimum_area_config_carries_no_dma():
    soc = yaml.safe_load(
        (REPO / "configs" / "mosaic_pico_serv_xip.yaml").read_text()
    )["soc"]
    assert soc["dma"] == "none"


# --------------------------------------------------------------------------
# the residual: "none" still leaves crossbar ports behind
# --------------------------------------------------------------------------


def test_xbar_master_count_is_computed_without_consulting_is_included():
    """Pins a known, documented limitation so it cannot be silently "fixed".

    ``mosaic_soc_pkg.sv.tpl`` derives SYSTEM_XBAR_NMASTER from
    ``dma.get_num_master_ports()`` alone. With ``soc.dma: none`` the engine is
    not instantiated but its (stubbed, 1-port) master slots still exist. Any
    change here must move ``xheep.num_bus_masters`` in the same commit -- that
    count feeds the LOG-bus bank check, and the two disagreeing is worse than
    two unused ports.
    """
    tpl = (
        REPO / "hw" / "mosaic_soc" / "include" / "mosaic_soc_pkg.sv.tpl"
    ).read_text()
    lines = [
        ln
        for ln in tpl.splitlines()
        if re.search(r"(?<!LOG_)SYSTEM_XBAR_NMASTER\s*=", ln)
    ]
    assert lines, "SYSTEM_XBAR_NMASTER assignment vanished from the template"
    for ln in lines:
        assert "get_num_master_ports()" in ln
        assert "is_included" not in ln, (
            "template now honours is_included -- update xheep.num_bus_masters "
            "to match and delete this test"
        )


def test_instantiation_guard_still_gates_on_is_included():
    """The guard in ao_peripheral_subsystem.sv.tpl is what makes "none" work."""
    tpl = (
        REPO / "hw" / "mosaic_soc" / "ao_peripheral_subsystem.sv.tpl"
    ).read_text()
    assert re.search(r"contains_peripheral\(\s*['\"]dma['\"]\s*\)", tpl)
    assert "get_dma().get_is_included()" in tpl


def test_absent_dma_never_indexes_the_ao_demux_with_a_channel_index():
    """Regression: DMA_CH0_IDX is not an AO peripheral index.

    The tie-off that answers the DMA register window when ``soc.dma: none``
    covers exactly one demux slot, DMA_IDX. DMA_CH0_IDX belongs to a different
    address space -- it is the 8-bit index into the DMA's own DMA_ADDR_RULES --
    and its value is 0, which in the AO demux is SOC_CTRL_IDX. Driving
    ``ao_peripheral_slv_rsp[DMA_CH0_IDX]`` therefore double-drives soc_ctrl's
    response with a constant, and the constant wins: every soc_ctrl read comes
    back error=1, rdata=0. It shipped in a GDS before yosys' check caught it.
    """
    tpl = (
        REPO / "hw" / "mosaic_soc" / "ao_peripheral_subsystem.sv.tpl"
    ).read_text()
    offenders = [
        ln
        for ln in tpl.splitlines()
        if re.search(r"^\s*assign\s+ao_peripheral_slv_rsp\[", ln)
        and "DMA_CH0_IDX" in ln
    ]
    assert not offenders, (
        "ao_peripheral_slv_rsp is indexed by DMA_CH0_IDX (== 0 == SOC_CTRL_IDX): "
        + "; ".join(o.strip() for o in offenders)
    )


def test_absent_dma_still_terminates_its_own_window():
    """The other half of the absent-DMA tie-off: DMA_IDX must stay driven, or the bus hangs."""
    tpl = (
        REPO / "hw" / "mosaic_soc" / "ao_peripheral_subsystem.sv.tpl"
    ).read_text()
    assert re.search(
        r"assign\s+ao_peripheral_slv_rsp\[\s*mosaic_soc_pkg::DMA_IDX\s*\]",
        tpl,
    )


# --------------------------------------------------------------------------
# soc.debug / soc.plic -- the other two "pure area when unused" blocks
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["debug", "plic"])
@pytest.mark.parametrize("value", [True, False])
def test_debug_and_plic_accept_booleans(key, value):
    cfg = _cfg()
    cfg["soc"][key] = value
    assert validate_soc_config(cfg) == []


@pytest.mark.parametrize("key", ["debug", "plic"])
def test_debug_and_plic_reject_non_booleans(key):
    cfg = _cfg()
    cfg["soc"][key] = "false"          # the classic YAML quoting mistake
    errors = validate_soc_config(cfg)
    assert errors and key in " ".join(errors), errors


@pytest.mark.parametrize("key", ["debug", "plic"])
def test_debug_and_plic_default_to_present(key):
    from mosaic_config import MosaicConfig

    assert MosaicConfig.__dataclass_fields__[key].default is True


@pytest.mark.parametrize("key,value", [("debug", False), ("plic", False)])
def test_debug_and_plic_round_trip(tmp_path, key, value):
    from mosaic_config import load_mosaic_yaml

    cfg = _cfg()
    cfg["soc"][key] = value
    p = tmp_path / f"{key}.yaml"
    p.write_text(yaml.safe_dump(cfg))
    assert getattr(load_mosaic_yaml(p), key) is value


def test_plic_false_drops_rv_plic_from_the_mandatory_set():
    """rv_plic is otherwise force-added by MANDATORY_USER_PERIPHERALS."""
    from core_registry import expanded_user_peripherals

    assert "rv_plic" in expanded_user_peripherals(["uart"])
    assert "rv_plic" not in expanded_user_peripherals(["uart"], plic=False)


def test_peripheral_subsystem_template_ties_off_an_absent_plic():
    """Removing the PLIC is safe only because the template has a full else
    branch; without it the design would have floating interrupt nets."""
    tpl = (REPO / "hw" / "mosaic_soc" / "peripheral_subsystem.sv.tpl").read_text()
    assert "contains_peripheral('rv_plic')" in tpl
    for tie in ("assign msip_o = '0;", "assign irq_plic_o = '0;",
                "assign plic_tl_d2h = '0;"):
        assert tie in tpl, f"missing PLIC tie-off: {tie}"


def test_mosaic_soc_template_ties_off_an_absent_debug_subsystem():
    tpl = (REPO / "hw" / "mosaic_soc" / "mosaic_soc.sv.tpl").read_text()
    assert "% if debug_enabled:" in tpl
    for tie in ("assign debug_req         = '0;",
                "assign debug_reset_n     = 1'b1;",
                "assign debug_slave_resp  = '0;",
                "assign debug_master_req  = '0;"):
        assert tie in tpl, f"missing debug tie-off: {tie}"


def test_debug_reset_is_released_when_the_debug_module_is_absent():
    """debug_reset_n gates the whole system bus reset. Tying it to 0 -- or
    leaving it undriven -- would hold the SoC in reset forever."""
    tpl = (REPO / "hw" / "mosaic_soc" / "mosaic_soc.sv.tpl").read_text()
    assert "assign debug_reset_n     = 1'b1;" in tpl
    # the synchronized pad reset (test_reset_sync.py), still gated by debug
    assert "rst_n_sync && debug_reset_n" in tpl


def test_the_minimum_area_config_drops_debug_and_plic():
    soc = yaml.safe_load(
        (REPO / "configs" / "mosaic_pico_serv_xip.yaml").read_text()
    )["soc"]
    assert soc["debug"] is False and soc["plic"] is False


# --------------------------------------------------------------------------
# soc.spi_mode -- the largest single block in the minimum-area SoC
# --------------------------------------------------------------------------


def test_valid_spi_modes():
    from core_registry import VALID_SPI_MODE

    assert VALID_SPI_MODE == frozenset({"full", "xip_only"})


@pytest.mark.parametrize("mode", ["full", "xip_only"])
def test_spi_mode_accepted(mode):
    cfg = _cfg()
    cfg["soc"]["spi_mode"] = mode
    assert validate_soc_config(cfg) == []


def test_unknown_spi_mode_rejected():
    cfg = _cfg()
    cfg["soc"]["spi_mode"] = "write_too"
    errors = validate_soc_config(cfg)
    assert errors and "spi_mode" in " ".join(errors), errors


def test_spi_mode_defaults_to_full():
    from mosaic_config import MosaicConfig

    assert MosaicConfig.__dataclass_fields__["spi_mode"].default == "full"


def test_spi_mode_round_trips(tmp_path):
    from mosaic_config import load_mosaic_yaml

    cfg = _cfg()
    cfg["soc"]["spi_mode"] = "xip_only"
    p = tmp_path / "spi.yaml"
    p.write_text(yaml.safe_dump(cfg))
    assert load_mosaic_yaml(p).spi_mode == "xip_only"


def test_xip_only_keeps_the_reader_and_drops_the_host():
    """The whole point: obi_spimemio survives, spi_host does not."""
    tpl = (REPO / "hw" / "vendor" / "xheep" / "spi" / "rtl" / "spi_subsystem.sv.tpl").read_text()
    assert 'spi_xip_only = (_spi_mode == "xip_only")' in tpl
    assert "% if not spi_xip_only:" in tpl, "spi_host must be gated"
    # obi_spimemio must NOT sit inside any conditional -- it is the boot path.
    body = tpl.split("obi_spimemio obi_spimemio_i")[0]
    assert body.count("% if") == body.count("% endif"), (
        "obi_spimemio is inside an unclosed conditional -- it must always be built"
    )


def test_xip_only_ties_off_every_signal_the_host_drove():
    tpl = (REPO / "hw" / "vendor" / "xheep" / "spi" / "rtl" / "spi_subsystem.sv.tpl").read_text()
    for sig in ("ot_spi_sck", "ot_spi_sck_en", "ot_spi_csb", "ot_spi_csb_en",
                "ot_spi_sd_out", "ot_spi_sd_en", "ot_spi_intr_error",
                "ot_spi_intr_event", "ot_spi_rx_valid", "ot_spi_tx_ready",
                "external_spi_host_hw2reg_status"):
        assert f"assign {sig}" in tpl, f"no tie-off for {sig} in xip_only mode"


def test_chip_selects_are_tied_deselected_not_zero():
    """csb is active low. Tying it to '0 would permanently SELECT a device."""
    tpl = (REPO / "hw" / "vendor" / "xheep" / "spi" / "rtl" / "spi_subsystem.sv.tpl").read_text()
    assert "assign ot_spi_csb       = '1;" in tpl


def test_the_tapeout_config_uses_every_area_lever():
    soc = yaml.safe_load(
        (REPO / "configs" / "mosaic_tapeout_min.yaml").read_text()
    )["soc"]
    assert soc["dma"] == "none"
    assert soc["debug"] is False and soc["plic"] is False
    assert soc["spi_mode"] == "xip_only"
    assert soc["peripherals"] == ["uart"]
    assert all(c["ip"] == "serv" for c in soc["cores"])


def test_gf180_sram_wrapper_picks_the_cut_by_depth_not_by_name():
    """A 512 B bank wants 4x sram128x8 (0.4645 mm2), not 4x sram512x8
    (0.8376 mm2 for the same usable 512 B)."""
    sv = (REPO / "hw" / "asic" / "gf180" / "sram_wrapper.sv").read_text()
    for depth in (64, 128, 256, 512):
        assert f"sram{depth}x8m8wm1" in sv, f"no cut bound for NumWords {depth}"
    assert "NumWords == 128" in sv and "gen_cut128" in sv
    # four cuts per bank, one per byte lane
    assert "b < 4" in sv and "wdata_i[8*b+:8]" in sv


def test_gf180_sram_byte_enables_are_active_low():
    sv = (REPO / "hw" / "asic" / "gf180" / "sram_wrapper.sv").read_text()
    assert "assign wen_n = {8{~be_i[b]}};" in sv
    assert "assign cen_n  = ~req_i;" in sv
    assert "assign gwen_n = ~we_i;" in sv


def test_absent_ao_rv_timer_drives_both_halves_of_its_tl_pair():
    """Regression: an absent always-on timer left half its TL-UL pair undriven.

    ``rv_timer_tl_h2d`` is driven by the reg_to_tlul bridge, which lives inside
    the branch that ``soc.ao_rv_timer: false`` removes. Absorbing it into an
    unused signal instead of tying it off left 107 bits used-but-undriven --
    the exact signature that exposed two earlier tie-off defects, so it must not be allowed
    to become background noise.
    """
    tpl = (
        REPO / "hw" / "mosaic_soc" / "ao_peripheral_subsystem.sv.tpl"
    ).read_text()
    else_branch = tpl.split("% if ao_rv_timer:", 1)[1].split("% endif", 1)[0]
    assert "% else:" in else_branch, "ao_rv_timer branch lost its else"
    off = else_branch.split("% else:", 1)[1]
    for net in ("rv_timer_tl_h2d", "rv_timer_tl_d2h"):
        assert re.search(rf"assign\s+{net}\s*=", off), f"{net} left undriven"
