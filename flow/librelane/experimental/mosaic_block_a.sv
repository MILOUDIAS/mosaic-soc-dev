// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// MOSAIC-SoC -- "Block A" (the GF180MCU reference design) macro wrapper,
// padframe interface.
// mosaic-technology: gf180mcu
//   Declared because this port list IS the GF180 pad cells' terminal set
//   (drive strength, slew, Schmitt select); another process needs another
//   padframe wrapper, not an edit of this one.
//
// Block A is 1110 x 1110 um, the die the external padframe DEF mandates. The
// shared pad ring is outside this macro and is not part of this repository, so
// this macro is the deliverable and its pin list IS the block interface.
//
// THIS MACRO DRIVES THE PAD CONTROLS, NOT JUST THE DATA. The 22 bonded I/O cells
// expand to 167 boundary terminals, because gf180mcu_fd_io pads take their
// configuration from the block: pull enables on every pad, and on the
// bidirectional ones also input/output enable, CMOS-vs-Schmitt select, slew
// select and a two-bit drive strength. The port list is GENERATED into
// padframe/mosaic_block_a_ports.svh from the padframe interface file (a YAML
// list of the pad terminals, supplied with the external padframe DEF),
// because Odb.ApplyDEFTemplate matches pin sets in strict mode and 167
// hand-typed names would fail only after a synthesis run.
//
// THE TRISTATE MOVED OUT. Earlier revisions resolved spi_flash_sd_io internally
// with four gf180mcu_fd_sc_mcu7t5v0__bufz_4 cells, because the block had nowhere
// to send an output enable. gf180mcu_fd_io__bi_t does that job now, so the cells
// are gone and the core's own spi_flash_sd_*_oe_o drive the pads directly.
//
// IE IS NOT TIED HIGH. The PDK control table marks IE=1 with OE=1 "Disallowed",
// so each bidirectional pad takes IE = ~OE. On the eleven output-only pads that
// means IE=0 and their _IN terminals stay unread; they are declared because the
// DEF declares them, and left unconnected on purpose.
//
// mosaic_soc exposes 251 ports, almost all x-heep expansion interfaces
// (ext_*, hw_fifo_*, GPIO, JTAG, DDR) this block does not bring out. They are
// terminated HERE rather than deleted from the RTL: unused outputs left
// unconnected so synthesis prunes their logic, unused inputs tied to constants.
//
// ONE THING THAT IS NOT A CONSTANT: the three power-switch acknowledges. The
// x-heep testbench models each as its own switch output delayed by 15 cycles
// (tb/testharness.sv, SWITCH_ACK_LATENCY). Tying them to a fixed level can leave
// the power manager waiting for a handshake that never completes, so they are
// looped back from the matching switch output -- an ideal switch that
// acknowledges immediately, the correct model for a block with no power gating.

module mosaic_block_a
  import obi_pkg::*;
  import reg_pkg::*;
  import fifo_pkg::*;
#(

parameter EXT_XBAR_NMASTER = 0,
    parameter AO_SPC_NUM = 0,
    parameter EXT_HARTS = 0,
    
    parameter AO_SPC_NUM_RND = AO_SPC_NUM == 0 ? 0 : AO_SPC_NUM - 1,
    parameter EXT_XBAR_NMASTER_RND = EXT_XBAR_NMASTER == 0 ? 1 : EXT_XBAR_NMASTER,
    parameter EXT_DOMAINS_RND = mosaic_soc_pkg::EXTERNAL_DOMAINS == 0 ? 1 : mosaic_soc_pkg::EXTERNAL_DOMAINS,
    parameter NEXT_INT_RND = mosaic_soc_pkg::NEXT_INT == 0 ? 1 : mosaic_soc_pkg::NEXT_INT,
    parameter EXT_HARTS_RND = EXT_HARTS == 0 ? 1 : EXT_HARTS
) (
    inout  wire          VDD,
    inout  wire          VSS,
    input  logic         boot_select_i,
    output logic         boot_select_i_PD,
    output logic         boot_select_i_PU,
    input  logic         clk_i,
    output logic         clk_i_PD,
    output logic         clk_i_PU,
    input  logic         execute_from_flash_i,
    output logic         execute_from_flash_i_PD,
    output logic         execute_from_flash_i_PU,
    input  logic         rst_ni,
    output logic         rst_ni_PD,
    output logic         rst_ni_PU,
    output logic         spi_flash_cs_o_CS,
    output logic         spi_flash_cs_o_IE,
    input  logic         spi_flash_cs_o_IN,
    output logic         spi_flash_cs_o_OE,
    output logic         spi_flash_cs_o_OUT,
    output logic         spi_flash_cs_o_PD,
    output logic         spi_flash_cs_o_PDRV0,
    output logic         spi_flash_cs_o_PDRV1,
    output logic         spi_flash_cs_o_PU,
    output logic         spi_flash_cs_o_SL,
    output logic         spi_flash_sck_o_CS,
    output logic         spi_flash_sck_o_IE,
    input  logic         spi_flash_sck_o_IN,
    output logic         spi_flash_sck_o_OE,
    output logic         spi_flash_sck_o_OUT,
    output logic         spi_flash_sck_o_PD,
    output logic         spi_flash_sck_o_PDRV0,
    output logic         spi_flash_sck_o_PDRV1,
    output logic         spi_flash_sck_o_PU,
    output logic         spi_flash_sck_o_SL,
    output logic   [3:0] spi_flash_sd_io_CS,
    output logic   [3:0] spi_flash_sd_io_IE,
    input  logic   [3:0] spi_flash_sd_io_IN,
    output logic   [3:0] spi_flash_sd_io_OE,
    output logic   [3:0] spi_flash_sd_io_OUT,
    output logic   [3:0] spi_flash_sd_io_PD,
    output logic   [3:0] spi_flash_sd_io_PDRV0,
    output logic   [3:0] spi_flash_sd_io_PDRV1,
    output logic   [3:0] spi_flash_sd_io_PU,
    output logic   [3:0] spi_flash_sd_io_SL,
    output logic   [6:0] status_o_CS,
    output logic   [6:0] status_o_IE,
    input  logic   [6:0] status_o_IN,
    output logic   [6:0] status_o_OE,
    output logic   [6:0] status_o_OUT,
    output logic   [6:0] status_o_PD,
    output logic   [6:0] status_o_PDRV0,
    output logic   [6:0] status_o_PDRV1,
    output logic   [6:0] status_o_PU,
    output logic   [6:0] status_o_SL,
    output logic         status_valid_o_CS,
    output logic         status_valid_o_IE,
    input  logic         status_valid_o_IN,
    output logic         status_valid_o_OE,
    output logic         status_valid_o_OUT,
    output logic         status_valid_o_PD,
    output logic         status_valid_o_PDRV0,
    output logic         status_valid_o_PDRV1,
    output logic         status_valid_o_PU,
    output logic         status_valid_o_SL,
    input  logic         uart_rx_i,
    output logic         uart_rx_i_PD,
    output logic         uart_rx_i_PU,
    output logic         uart_tx_o_CS,
    output logic         uart_tx_o_IE,
    input  logic         uart_tx_o_IN,
    output logic         uart_tx_o_OE,
    output logic         uart_tx_o_OUT,
    output logic         uart_tx_o_PD,
    output logic         uart_tx_o_PDRV0,
    output logic         uart_tx_o_PDRV1,
    output logic         uart_tx_o_PU,
    output logic         uart_tx_o_SL
);

  // The eXtension interface: six ports that slang/verilator refuse to leave
  // unconnected. One bus, all six tied to it -- no XIF accelerator is present.
  if_xif xif_bus ();

  // --- pad data wiring -----------------------------------------------------
  //
  // A  = the macro drives the pad (pad name `_OUT`)
  // Y  = the pad drives the macro (pad name `_IN`)
  // OE = output enable, IE = input enable, and IE = ~OE keeps every
  //      bidirectional pad out of the Disallowed IE=1/OE=1 state.
  logic [3:0] flash_sd_o, flash_sd_oe, flash_sd_i;

  assign spi_flash_sd_io_OUT = flash_sd_o;
  assign spi_flash_sd_io_OE  = flash_sd_oe;
  assign spi_flash_sd_io_IE  = ~flash_sd_oe;
  assign flash_sd_i          = spi_flash_sd_io_IN;

  // The eleven output-only pads drive constantly, so their OE is 1 and IE is 0
  // (both tied in the generated block below) and their _IN terminals are never
  // read. Only the _OUT side is connected here.
  logic [31:0] exit_value_int;
  assign status_o_OUT      = exit_value_int[6:0];

  assign boot_select_i_PD = 1'b0;
  assign boot_select_i_PU = 1'b0;
  assign clk_i_PD = 1'b0;
  assign clk_i_PU = 1'b0;
  assign execute_from_flash_i_PD = 1'b0;
  assign execute_from_flash_i_PU = 1'b0;
  assign rst_ni_PD = 1'b1;
  assign rst_ni_PU = 1'b0;
  assign spi_flash_cs_o_CS = 1'b0;
  assign spi_flash_cs_o_IE = 1'b0;
  assign spi_flash_cs_o_OE = 1'b1;
  assign spi_flash_cs_o_PD = 1'b0;
  assign spi_flash_cs_o_PDRV0 = 1'b0;
  assign spi_flash_cs_o_PDRV1 = 1'b1;
  assign spi_flash_cs_o_PU = 1'b0;
  assign spi_flash_cs_o_SL = 1'b0;
  assign spi_flash_sck_o_CS = 1'b0;
  assign spi_flash_sck_o_IE = 1'b0;
  assign spi_flash_sck_o_OE = 1'b1;
  assign spi_flash_sck_o_PD = 1'b0;
  assign spi_flash_sck_o_PDRV0 = 1'b0;
  assign spi_flash_sck_o_PDRV1 = 1'b1;
  assign spi_flash_sck_o_PU = 1'b0;
  assign spi_flash_sck_o_SL = 1'b0;
  assign spi_flash_sd_io_CS = {4{1'b0}};
  assign spi_flash_sd_io_PD = {4{1'b0}};
  assign spi_flash_sd_io_PDRV0 = {4{1'b0}};
  assign spi_flash_sd_io_PDRV1 = {4{1'b1}};
  assign spi_flash_sd_io_PU = {4{1'b0}};
  assign spi_flash_sd_io_SL = {4{1'b0}};
  assign status_o_CS = {7{1'b0}};
  assign status_o_IE = {7{1'b0}};
  assign status_o_OE = {7{1'b1}};
  assign status_o_PD = {7{1'b0}};
  assign status_o_PDRV0 = {7{1'b0}};
  assign status_o_PDRV1 = {7{1'b1}};
  assign status_o_PU = {7{1'b0}};
  assign status_o_SL = {7{1'b0}};
  assign status_valid_o_CS = 1'b0;
  assign status_valid_o_IE = 1'b0;
  assign status_valid_o_OE = 1'b1;
  assign status_valid_o_PD = 1'b0;
  assign status_valid_o_PDRV0 = 1'b0;
  assign status_valid_o_PDRV1 = 1'b1;
  assign status_valid_o_PU = 1'b0;
  assign status_valid_o_SL = 1'b0;
  assign uart_rx_i_PD = 1'b0;
  assign uart_rx_i_PU = 1'b0;
  assign uart_tx_o_CS = 1'b0;
  assign uart_tx_o_IE = 1'b0;
  assign uart_tx_o_OE = 1'b1;
  assign uart_tx_o_PD = 1'b0;
  assign uart_tx_o_PDRV0 = 1'b0;
  assign uart_tx_o_PDRV1 = 1'b1;
  assign uart_tx_o_PU = 1'b0;
  assign uart_tx_o_SL = 1'b0;

  // --- power-switch handshakes (see header) --------------------------------
  logic cpu_subsystem_powergate_switch_no_int;
  logic peripheral_subsystem_powergate_switch_no_int;
  logic [EXT_DOMAINS_RND-1:0] external_subsystem_powergate_switch_no_int;


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
  obi_resp_t [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_read_resp_i_tie = '0;
  obi_resp_t [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_write_resp_i_tie = '0;
  fifo_resp_t [mosaic_soc_pkg::DMA_CH_NUM-1:0] hw_fifo_resp_i_tie = '0;
  logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] ext_dma_stop_i_tie = '0;
  logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] hw_fifo_done_i_tie = '0;
  reg_rsp_t ext_peripheral_slave_resp_i_tie = '0;
  logic [NEXT_INT_RND-1:0] intr_vector_ext_i_tie = '0;
  logic intr_ext_peripheral_i_tie = '0;
  logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] ext_dma_slot_tx_i_tie = '0;
  logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] ext_dma_slot_rx_i_tie = '0;

  // Unused mosaic_soc OUTPUTS are omitted from this instantiation rather
  // than bound to an empty reference: synthesis then prunes the logic that
  // drove them, which is the point of trimming the interface. Unused INPUTS
  // are tied to explicit constants below.
  mosaic_soc i_mosaic_soc (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .boot_select_i(boot_select_i),
      .execute_from_flash_i(execute_from_flash_i),
      .uart_rx_i(uart_rx_i),
      .uart_tx_o(uart_tx_o_OUT),
      .exit_valid_o(status_valid_o_OUT),
      .exit_value_o(exit_value_int),
      .spi_flash_sck_o(spi_flash_sck_o_OUT),
      .spi_flash_sck_i(1'b0),
      .spi_flash_cs_0_o(spi_flash_cs_o_OUT),
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
