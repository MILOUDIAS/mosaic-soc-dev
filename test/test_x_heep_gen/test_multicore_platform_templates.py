"""Structural gates for MOSAIC per-hart platform-service generation."""

from pathlib import Path
import sys

from mako.template import Template


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util/xheep_gen"))

from mosaic_config import load_mosaic_yaml, mosaic_to_xheep_kwargs


def render(path: str, kwargs: dict) -> str:
    template = Template(filename=str(REPO_ROOT / path), strict_undefined=True)
    return template.render_unicode(**kwargs)


def kwargs_for(path: Path) -> dict:
    return mosaic_to_xheep_kwargs(load_mosaic_yaml(path))


def test_canonical_amp_renders_effective_tdu_clint_and_debug_capabilities():
    kwargs = kwargs_for(REPO_ROOT / "mosaic.yaml")

    package = render(
        "hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv.tpl", kwargs
    )
    ao = render("hw/core-v-mini-mcu/ao_peripheral_subsystem.sv.tpl", kwargs)
    top = render("hw/core-v-mini-mcu/core_v_mini_mcu.sv.tpl", kwargs)
    peripheral = render("hw/core-v-mini-mcu/peripheral_subsystem.sv.tpl", kwargs)
    system_bus = render("hw/core-v-mini-mcu/system_bus.sv.tpl", kwargs)

    assert "localparam int unsigned NUM_HARTS = 7;" in package
    assert "MEM_SIZE = 32'h00008000" in package
    assert "CLINT_START_ADDRESS" in package
    assert "TDU_START_ADDRESS" in package

    assert "mosaic_clint_i" in ao
    assert ".mtime_o       (clint_mtime_o)" in ao
    assert ".RESET_SCHED_MODE(tdu_pkg::SCHED_DYNAMIC)" in ao
    assert ".core_park_o" in ao
    assert ".core_park_i(core_park)" in top
    assert ".core_sleep_i(&core_sleep)" in top
    assert ".time_i(clint_mtime)" in top
    assert "hart_id_array[i] = 32'(i);" in top
    assert "hart_id_i + i" not in top
    assert "assign ext_debug_req_o = '0;" in top
    assert "EXT_HARTS is unsupported with an explicit MOSAIC topology" in top
    # Only cv32e20 implements debug in the canonical 7-hart topology.
    assert ".HART_DEBUG_CAPABLE(NRHARTS'(1))" in top
    assert "output logic [core_v_mini_mcu_pkg::NUM_HARTS-1:0] irq_plic_o" in peripheral
    assert "core_ext_instr_arbiter_i" in system_bus
    assert "core_ext_data_arbiter_i" in system_bus
    assert "CORE6_DATA_IDX][DEMUX_XBAR_EXT_SLAVE_IDX] = core_ext_data_resp[6]" in system_bus

    cpu = render("hw/core-v-mini-mcu/cpu_subsystem.sv.tpl", kwargs)
    assert cpu.count("tc_clk_gating hart_clock_gate_") == 7
    assert ".en_i      (hart_clock_enable_1_0)" in cpu
    assert "reset_clock_hold_1_0 <= core_run_1_0;" in cpu
    assert ".clk_i(hart_clk_1_0)" in cpu
    assert "always_ff @(posedge clk_i or negedge rst_ni)" in cpu


def test_all_titan_smp_without_tdu_still_has_per_hart_platform_services(tmp_path):
    config = tmp_path / "smp.yaml"
    config.write_text(
        """soc:
  name: smp_no_tdu
  pdk: gf180mcu
  cores:
    - {ip: cv32e20, isa: rv32emc, count: 2, role: titan}
  memory: {sram_kb: 32, boot_rom_kb: 2}
  bus: obi
  scheduler: {tdu: false, mode: static}
  peripherals: [uart, timer]
"""
    )
    kwargs = kwargs_for(config)

    package = render(
        "hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv.tpl", kwargs
    )
    ao = render("hw/core-v-mini-mcu/ao_peripheral_subsystem.sv.tpl", kwargs)
    top = render("hw/core-v-mini-mcu/core_v_mini_mcu.sv.tpl", kwargs)

    assert "CLINT_START_ADDRESS" in package
    assert "TDU_START_ADDRESS" not in package
    assert "mosaic_clint_i" in ao
    assert ") tdu_i (" not in ao
    assert "idma_xheep_wrapper" in ao
    assert "dma_subsystem #(\n" not in ao
    assert "assign core_wake = '0;" in top
    assert "irq_external[1]" in top
    assert ".HART_DEBUG_CAPABLE(NRHARTS'(3))" in top


def test_singleton_sci_uses_topology_platform_and_is_honest_about_debug(tmp_path):
    config = tmp_path / "singleton.yaml"
    config.write_text(
        """soc:
  name: singleton_serv
  pdk: gf180mcu
  cores:
    - {ip: serv, isa: rv32i, count: 1, role: titan}
  memory: {sram_kb: 8, boot_rom_kb: 1}
  bus: obi
  scheduler: {tdu: false, mode: static}
  peripherals: [uart]
"""
    )
    kwargs = kwargs_for(config)
    top = render("hw/core-v-mini-mcu/core_v_mini_mcu.sv.tpl", kwargs)

    assert "localparam NRHARTS = 1;" in top
    assert ".HART_DEBUG_CAPABLE(NRHARTS'(0))" in top
    assert ".clint_timer_irq_o" in top


def test_interrupt_capable_workers_receive_their_own_plic_context():
    capable = kwargs_for(REPO_ROOT / "configs/mosaic_hazard3.yaml")
    capable_top = render(
        "hw/core-v-mini-mcu/core_v_mini_mcu.sv.tpl", capable
    )
    assert "intr_array[1][11] = irq_external[1];" in capable_top
    assert "| irq_software[1]" in capable_top

    timer_only = kwargs_for(REPO_ROOT / "mosaic.yaml")
    timer_only_top = render(
        "hw/core-v-mini-mcu/core_v_mini_mcu.sv.tpl", timer_only
    )
    assert "intr_array[1][11] = irq_external[1];" not in timer_only_top
