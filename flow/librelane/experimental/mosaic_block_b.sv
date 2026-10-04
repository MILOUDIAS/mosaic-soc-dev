// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// MOSAIC-SoC -- Phase-1 second-hardening wrapper (Block B experiment).
//
// THIS FILE IS mosaic_block_a.sv WITH THE MODULE RENAMED. Nothing else differs;
// `diff` reports exactly two lines. That is deliberate, and both the sameness
// and the duplication are findings.
//
// The SAMENESS: this wrapper does not depend on the SoC's topology. It
// instantiates core_v_mini_mcu and terminates its ~251 ports using package
// parameters (DMA_NUM_MASTER_PORTS, AO_SPC_NUM), never a core type or a hart
// count. Block B has three harts where Block A has two, and not one line here
// needed to change. A second design needs a new hardening config, not a new
// wrapper.
//
// The DUPLICATION: LibreLane's DESIGN_NAME must equal the top module name, so
// two designs cannot share one module. Reusing `mosaic_block_a` would have
// been the easy path and a bad one -- signoff waivers are scoped by design
// name (harness/evidence/waivers.py), so Block B would silently inherit Block
// A's accepted 591 max-slew and 1 max-fanout violations. The first thing we
// learned about the new design would have been Block A's numbers.
//
// Both observations point the same way: this file is mechanical port
// termination derived from a port list, which makes it a generator
// target rather than something to copy again.
//
// Block A is a quarter of the 2235 x 2235 um shared project area (1117.5 um
// square, 1.2488 mm2) with a 22-pin budget. The shared pad ring is outside
// this macro; this macro is the deliverable, so its pin list IS the block
// interface -- there is no chip-level adapter to tie things off in.
//
// 22 LEF pins = 20 signal ports below + VDD/VSS, which the PDN creates.
//
// core_v_mini_mcu exposes 251 ports / ~1343 pins, almost all of them x-heep
// expansion interfaces (ext_*, hw_fifo_*, GPIO, JTAG, DDR) that this block
// does not bring out. They are terminated HERE rather than deleted from the
// RTL: unused outputs are left unconnected so synthesis prunes the logic
// driving them, and unused inputs are tied to explicit constants.
//
// ONE THING THAT IS NOT A CONSTANT: the three power-switch acknowledges. The
// x-heep testbench models each as its own switch output delayed by 15 cycles
// (tb/testharness.sv, SWITCH_ACK_LATENCY). Tying them to a fixed level can
// leave the power manager waiting for a handshake that never completes, so
// they are looped back from the matching switch output -- an ideal switch
// that acknowledges immediately, which is the correct model for a block with
// no real power gating.

module mosaic_block_b
  import obi_pkg::*;
  import reg_pkg::*;
  import fifo_pkg::*;
#(

parameter EXT_XBAR_NMASTER = 0,
    parameter AO_SPC_NUM = 0,
    parameter EXT_HARTS = 0,
    
    parameter AO_SPC_NUM_RND = AO_SPC_NUM == 0 ? 0 : AO_SPC_NUM - 1,
    parameter EXT_XBAR_NMASTER_RND = EXT_XBAR_NMASTER == 0 ? 1 : EXT_XBAR_NMASTER,
    parameter EXT_DOMAINS_RND = core_v_mini_mcu_pkg::EXTERNAL_DOMAINS == 0 ? 1 : core_v_mini_mcu_pkg::EXTERNAL_DOMAINS,
    parameter NEXT_INT_RND = core_v_mini_mcu_pkg::NEXT_INT == 0 ? 1 : core_v_mini_mcu_pkg::NEXT_INT,
    parameter EXT_HARTS_RND = EXT_HARTS == 0 ? 1 : EXT_HARTS
) (
    // ---- clock and reset ------------------------------------------------
    input  logic       clk_i,
    input  logic       rst_ni,

    // ---- boot configuration ---------------------------------------------
    input  logic       boot_select_i,
    input  logic       execute_from_flash_i,

    // ---- QSPI flash: execute-in-place boot -------------------------------
    output logic       spi_flash_sck_o,
    output logic       spi_flash_cs_o,
    inout  wire  [3:0] spi_flash_sd_io,

    // ---- UART -------------------------------------------------------------
    input  logic       uart_rx_i,
    output logic       uart_tx_o,

    // ---- status: the only visibility this part has, since soc.debug is
    //      false and there is no JTAG. Driven by soc_ctrl's exit register.
    output logic       status_valid_o,
    output logic [6:0] status_o
);

  // The eXtension interface: six ports that slang/verilator refuse to leave
  // unconnected. One bus, all six tied to it -- no XIF accelerator is present.
  if_xif xif_bus ();

  // --- QSPI tristate ------------------------------------------------------
  logic [3:0] flash_sd_o, flash_sd_oe, flash_sd_i;

  // Tristate drivers are instantiated EXPLICITLY. Writing the usual
  //     assign pad = oe ? d : 1'bz;
  // leaves yosys with 4 unmapped $_TBUF_ cells -- neither dfflibmap nor abc
  // maps tristates, so the flow stops at Checker.YosysUnmappedCells with
  // nothing placeable. GF180MCU does provide the cells (bufz_*/invz_*, 43
  // three-state entries in the liberty), they just have to be named.
  // DRIVE STRENGTH: bufz_4 (0.927 pF rated). bufz_8 WAS TRIED AND IS WORSE --
  // measured, not assumed:
  //
  //            worst pad slew   max-slew   max-cap   max-fanout   worst slew
  //   bufz_4       5.187 ns        591        0          1          5.19 ns
  //   bufz_8       6.190 ns        785        9          3          9.51 ns
  //
  // (The drive-strength measurement now lives with the cell, in
  //  hw/asic/<pdk>/tc_pad.sv, so it cannot drift from what it constrains.)
  // Doubling the drive made the pads themselves ~1 ns SLOWER. These pads are
  // not driver-limited; their transition is inherited from the net feeding
  // them, and a bufz_8 presents roughly twice the input capacitance, which
  // degrades that net's slew faster than the stronger output recovers it.
  // Output slew tracks input slew, so the trade is a loss. Do not "fix" these
  // pads by upsizing again without first improving what drives them.
  //
  // Still provisional against the real pad loading: OUTPUT_CAP_LOAD
  // here is 72.91 fF, and a bonded pad plus board trace will exceed that. If
  // that number rises a lot, revisit -- with the input net fixed first.
  for (genvar b = 0; b < 4; b++) begin : gen_flash_sd
    tc_pad_tristate u_pad_drv (
        .en_i  (flash_sd_oe[b]),
        .d_i   (flash_sd_o[b]),
        .pad_io(spi_flash_sd_io[b])
    );
    assign flash_sd_i[b] = spi_flash_sd_io[b];
  end

  // --- power-switch handshakes (see header) --------------------------------
  logic cpu_subsystem_powergate_switch_no_int;
  logic peripheral_subsystem_powergate_switch_no_int;
  logic [EXT_DOMAINS_RND-1:0] external_subsystem_powergate_switch_no_int;

  logic [31:0] exit_value_int;
  assign status_o = exit_value_int[6:0];

  logic jtag_tck_i_tie = '0;
  logic jtag_tms_i_tie = '0;
  logic jtag_trst_ni_tie = '0;
  logic jtag_tdi_i_tie = '0;
  logic ddr_rcv_clk_i_tie = '0;
  logic gpio_0_i_tie = '0;
  logic gpio_1_i_tie = '0;
  logic ddr_rcv_0_i_tie = '0;
  logic gpio_2_i_tie = '0;
  logic ddr_rcv_1_i_tie = '0;
  logic gpio_3_i_tie = '0;
  logic ddr_rcv_2_i_tie = '0;
  logic gpio_4_i_tie = '0;
  logic gpio_5_i_tie = '0;
  logic gpio_6_i_tie = '0;
  logic ddr_rcv_3_i_tie = '0;
  logic gpio_7_i_tie = '0;
  logic gpio_8_i_tie = '0;
  logic gpio_9_i_tie = '0;
  logic gpio_10_i_tie = '0;
  logic gpio_11_i_tie = '0;
  logic gpio_12_i_tie = '0;
  logic gpio_13_i_tie = '0;
  logic spi_flash_cs_1_i_tie = '0;
  logic spi_sck_i_tie = '0;
  logic spi_cs_0_i_tie = '0;
  logic spi_cs_1_i_tie = '0;
  logic spi_sd_0_i_tie = '0;
  logic spi_sd_1_i_tie = '0;
  logic spi_sd_2_i_tie = '0;
  logic spi_sd_3_i_tie = '0;
  logic spi_slave_sck_i_tie = '0;
  logic gpio_14_i_tie = '0;
  logic spi_slave_cs_i_tie = '0;
  logic gpio_15_i_tie = '0;
  logic spi_slave_miso_i_tie = '0;
  logic gpio_16_i_tie = '0;
  logic spi_slave_mosi_i_tie = '0;
  logic gpio_17_i_tie = '0;
  logic pdm2pcm_pdm_i_tie = '0;
  logic gpio_18_i_tie = '0;
  logic pdm2pcm_clk_i_tie = '0;
  logic gpio_19_i_tie = '0;
  logic i2s_sck_i_tie = '0;
  logic gpio_20_i_tie = '0;
  logic i2s_ws_i_tie = '0;
  logic gpio_21_i_tie = '0;
  logic i2s_sd_i_tie = '0;
  logic gpio_22_i_tie = '0;
  logic spi2_cs_0_i_tie = '0;
  logic gpio_23_i_tie = '0;
  logic spi2_cs_1_i_tie = '0;
  logic gpio_24_i_tie = '0;
  logic spi2_sck_i_tie = '0;
  logic gpio_25_i_tie = '0;
  logic spi2_sd_0_i_tie = '0;
  logic gpio_26_i_tie = '0;
  logic spi2_sd_1_i_tie = '0;
  logic gpio_27_i_tie = '0;
  logic spi2_sd_2_i_tie = '0;
  logic gpio_28_i_tie = '0;
  logic spi2_sd_3_i_tie = '0;
  logic gpio_29_i_tie = '0;
  logic i2c_scl_i_tie = '0;
  logic gpio_31_i_tie = '0;
  logic i2c_sda_i_tie = '0;
  logic gpio_30_i_tie = '0;
  logic [31:0] hart_id_i_tie = '0;
  logic [31:0] xheep_instance_id_i_tie = '0;
  reg_rsp_t pad_resp_i_tie = '0;
  obi_req_t  [EXT_XBAR_NMASTER_RND-1:0] ext_xbar_master_req_i_tie = '0;
  reg_req_t  [AO_SPC_NUM_RND:0] ext_ao_peripheral_slave_req_i_tie = '0;
  obi_resp_t ext_core_instr_resp_i_tie = '0;
  obi_resp_t ext_core_data_resp_i_tie = '0;
  obi_resp_t ext_debug_master_resp_i_tie = '0;
  obi_resp_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_read_resp_i_tie = '0;
  obi_resp_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_write_resp_i_tie = '0;
  fifo_resp_t [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_resp_i_tie = '0;
  logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] ext_dma_stop_i_tie = '0;
  logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_done_i_tie = '0;
  reg_rsp_t ext_peripheral_slave_resp_i_tie = '0;
  logic [NEXT_INT_RND-1:0] intr_vector_ext_i_tie = '0;
  logic intr_ext_peripheral_i_tie = '0;
  logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] ext_dma_slot_tx_i_tie = '0;
  logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] ext_dma_slot_rx_i_tie = '0;

  // Unused core_v_mini_mcu OUTPUTS are omitted from this instantiation rather
  // than bound to an empty reference: synthesis then prunes the logic that
  // drove them, which is the point of trimming the interface. Unused INPUTS
  // are tied to explicit constants below.
  core_v_mini_mcu i_core_v_mini_mcu (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .boot_select_i(boot_select_i),
      .execute_from_flash_i(execute_from_flash_i),
      .uart_rx_i(uart_rx_i),
      .uart_tx_o(uart_tx_o),
      .exit_valid_o(status_valid_o),
      .exit_value_o(exit_value_int),
      .spi_flash_sck_o(spi_flash_sck_o),
      .spi_flash_sck_i(1'b0),
      .spi_flash_cs_0_o(spi_flash_cs_o),
      .spi_flash_cs_0_i(1'b0),
      .spi_flash_sd_0_o(flash_sd_o[0]),
      .spi_flash_sd_0_oe_o(flash_sd_oe[0]),
      .spi_flash_sd_0_i(flash_sd_i[0]),
      .spi_flash_sd_1_o(flash_sd_o[1]),
      .spi_flash_sd_1_oe_o(flash_sd_oe[1]),
      .spi_flash_sd_1_i(flash_sd_i[1]),
      .spi_flash_sd_2_o(flash_sd_o[2]),
      .spi_flash_sd_2_oe_o(flash_sd_oe[2]),
      .spi_flash_sd_2_i(flash_sd_i[2]),
      .spi_flash_sd_3_o(flash_sd_o[3]),
      .spi_flash_sd_3_oe_o(flash_sd_oe[3]),
      .spi_flash_sd_3_i(flash_sd_i[3]),
      .cpu_subsystem_powergate_switch_no(cpu_subsystem_powergate_switch_no_int),
      .cpu_subsystem_powergate_switch_ack_ni(cpu_subsystem_powergate_switch_no_int),
      .peripheral_subsystem_powergate_switch_no(peripheral_subsystem_powergate_switch_no_int),
      .peripheral_subsystem_powergate_switch_ack_ni(peripheral_subsystem_powergate_switch_no_int),
      .external_subsystem_powergate_switch_no(external_subsystem_powergate_switch_no_int),
      .external_subsystem_powergate_switch_ack_ni(external_subsystem_powergate_switch_no_int),
      .jtag_tck_i(jtag_tck_i_tie),
      .jtag_tms_i(jtag_tms_i_tie),
      .jtag_trst_ni(jtag_trst_ni_tie),
      .jtag_tdi_i(jtag_tdi_i_tie),
      .ddr_rcv_clk_i(ddr_rcv_clk_i_tie),
      .gpio_0_i(gpio_0_i_tie),
      .gpio_1_i(gpio_1_i_tie),
      .ddr_rcv_0_i(ddr_rcv_0_i_tie),
      .gpio_2_i(gpio_2_i_tie),
      .ddr_rcv_1_i(ddr_rcv_1_i_tie),
      .gpio_3_i(gpio_3_i_tie),
      .ddr_rcv_2_i(ddr_rcv_2_i_tie),
      .gpio_4_i(gpio_4_i_tie),
      .gpio_5_i(gpio_5_i_tie),
      .gpio_6_i(gpio_6_i_tie),
      .ddr_rcv_3_i(ddr_rcv_3_i_tie),
      .gpio_7_i(gpio_7_i_tie),
      .gpio_8_i(gpio_8_i_tie),
      .gpio_9_i(gpio_9_i_tie),
      .gpio_10_i(gpio_10_i_tie),
      .gpio_11_i(gpio_11_i_tie),
      .gpio_12_i(gpio_12_i_tie),
      .gpio_13_i(gpio_13_i_tie),
      .spi_flash_cs_1_i(spi_flash_cs_1_i_tie),
      .spi_sck_i(spi_sck_i_tie),
      .spi_cs_0_i(spi_cs_0_i_tie),
      .spi_cs_1_i(spi_cs_1_i_tie),
      .spi_sd_0_i(spi_sd_0_i_tie),
      .spi_sd_1_i(spi_sd_1_i_tie),
      .spi_sd_2_i(spi_sd_2_i_tie),
      .spi_sd_3_i(spi_sd_3_i_tie),
      .spi_slave_sck_i(spi_slave_sck_i_tie),
      .gpio_14_i(gpio_14_i_tie),
      .spi_slave_cs_i(spi_slave_cs_i_tie),
      .gpio_15_i(gpio_15_i_tie),
      .spi_slave_miso_i(spi_slave_miso_i_tie),
      .gpio_16_i(gpio_16_i_tie),
      .spi_slave_mosi_i(spi_slave_mosi_i_tie),
      .gpio_17_i(gpio_17_i_tie),
      .pdm2pcm_pdm_i(pdm2pcm_pdm_i_tie),
      .gpio_18_i(gpio_18_i_tie),
      .pdm2pcm_clk_i(pdm2pcm_clk_i_tie),
      .gpio_19_i(gpio_19_i_tie),
      .i2s_sck_i(i2s_sck_i_tie),
      .gpio_20_i(gpio_20_i_tie),
      .i2s_ws_i(i2s_ws_i_tie),
      .gpio_21_i(gpio_21_i_tie),
      .i2s_sd_i(i2s_sd_i_tie),
      .gpio_22_i(gpio_22_i_tie),
      .spi2_cs_0_i(spi2_cs_0_i_tie),
      .gpio_23_i(gpio_23_i_tie),
      .spi2_cs_1_i(spi2_cs_1_i_tie),
      .gpio_24_i(gpio_24_i_tie),
      .spi2_sck_i(spi2_sck_i_tie),
      .gpio_25_i(gpio_25_i_tie),
      .spi2_sd_0_i(spi2_sd_0_i_tie),
      .gpio_26_i(gpio_26_i_tie),
      .spi2_sd_1_i(spi2_sd_1_i_tie),
      .gpio_27_i(gpio_27_i_tie),
      .spi2_sd_2_i(spi2_sd_2_i_tie),
      .gpio_28_i(gpio_28_i_tie),
      .spi2_sd_3_i(spi2_sd_3_i_tie),
      .gpio_29_i(gpio_29_i_tie),
      .i2c_scl_i(i2c_scl_i_tie),
      .gpio_31_i(gpio_31_i_tie),
      .i2c_sda_i(i2c_sda_i_tie),
      .gpio_30_i(gpio_30_i_tie),
      .hart_id_i(hart_id_i_tie),
      .xheep_instance_id_i(xheep_instance_id_i_tie),
      .pad_resp_i(pad_resp_i_tie),
      .ext_xbar_master_req_i(ext_xbar_master_req_i_tie),
      .ext_ao_peripheral_slave_req_i(ext_ao_peripheral_slave_req_i_tie),
      .ext_core_instr_resp_i(ext_core_instr_resp_i_tie),
      .ext_core_data_resp_i(ext_core_data_resp_i_tie),
      .ext_debug_master_resp_i(ext_debug_master_resp_i_tie),
      .ext_dma_read_resp_i(ext_dma_read_resp_i_tie),
      .ext_dma_write_resp_i(ext_dma_write_resp_i_tie),
      .hw_fifo_resp_i(hw_fifo_resp_i_tie),
      .ext_dma_stop_i(ext_dma_stop_i_tie),
      .hw_fifo_done_i(hw_fifo_done_i_tie),
      .ext_peripheral_slave_resp_i(ext_peripheral_slave_resp_i_tie),
      .intr_vector_ext_i(intr_vector_ext_i_tie),
      .intr_ext_peripheral_i(intr_ext_peripheral_i_tie),
      .ext_dma_slot_tx_i(ext_dma_slot_tx_i_tie),
      .ext_dma_slot_rx_i(ext_dma_slot_rx_i_tie),
      .xif_compressed_if(xif_bus),
      .xif_issue_if(xif_bus),
      .xif_commit_if(xif_bus),
      .xif_mem_if(xif_bus),
      .xif_mem_result_if(xif_bus),
      .xif_result_if(xif_bus)
  );

endmodule
