// Copyright 2022 OpenHW Group
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

<%!
    from pads.pin import Input, Output, Inout
%>

<%
  dma = xheep.get_base_peripheral_domain().get_dma()
  memory_ss = xheep.memory_ss()
  user_peripheral_domain = xheep.get_user_peripheral_domain()
  dma = xheep.get_base_peripheral_domain().get_dma()
  memory_ss = xheep.memory_ss()
  dma_obi_msb = dma.get_num_master_ports() - 1
  is_mc = xheep.is_multi_core()
  nh = xheep.num_harts()
  tdu_enabled = is_mc and bool(xheep.get_extension("tdu_enabled"))
  debug_ext = xheep.get_extension("debug_enabled")
  debug_enabled = True if debug_ext is None else bool(debug_ext)
  debug_hart_mask = xheep.get_extension("debug_hart_mask")
  if debug_hart_mask is None:
      debug_hart_mask = (1 << max(1, nh)) - 1
  interrupt_hart_mask = xheep.get_extension("interrupt_hart_mask") or 0
  hart_roles = []
  for group in xheep.cpus():
      hart_roles.extend([group.role] * group.count)

  clk_module = next((p for p in xheep.get_padring().get_connected_pins() if p.name in ["clk", "ref_clk"] ), None).module
  rst_module = next((p for p in xheep.get_padring().get_connected_pins() if p.name == "rst"), None).module

%>

module mosaic_soc
  import obi_pkg::*;
  import reg_pkg::*;
  import fifo_pkg::*;
#(
    parameter EXT_XBAR_NMASTER = 0,
    parameter AO_SPC_NUM = 0,
    parameter EXT_HARTS = 0,
    //do not touch these parameters
    parameter AO_SPC_NUM_RND = AO_SPC_NUM == 0 ? 0 : AO_SPC_NUM - 1,
    parameter EXT_XBAR_NMASTER_RND = EXT_XBAR_NMASTER == 0 ? 1 : EXT_XBAR_NMASTER,
    parameter EXT_DOMAINS_RND = mosaic_soc_pkg::EXTERNAL_DOMAINS == 0 ? 1 : mosaic_soc_pkg::EXTERNAL_DOMAINS,
    parameter NEXT_INT_RND = mosaic_soc_pkg::NEXT_INT == 0 ? 1 : mosaic_soc_pkg::NEXT_INT,
    parameter EXT_HARTS_RND = EXT_HARTS == 0 ? 1 : EXT_HARTS
) (

    % if clk_module != "mosaic_soc":
      input logic clk_i,
    % endif
    % if rst_module != "mosaic_soc":
      input logic rst_ni,
    % endif
    % for pin in xheep.get_padring().get_connected_pins():
      % if pin.module == "mosaic_soc":
        % if isinstance(pin, (Input, Inout)):
          input logic ${pin.rtl_name()}i,
        % endif
        % if isinstance(pin, (Output, Inout)):
          output logic ${pin.rtl_name()}o,
        % endif
        % if isinstance(pin, Inout):
          output logic ${pin.rtl_name()}oe_o,
        % endif
      % endif
    % endfor
    
    // IDs
    input logic [31:0] hart_id_i,
    input logic [31:0] xheep_instance_id_i,

    // eXtension interface
    if_xif.cpu_compressed xif_compressed_if,
    if_xif.cpu_issue      xif_issue_if,
    if_xif.cpu_commit     xif_commit_if,
    if_xif.cpu_mem        xif_mem_if,
    if_xif.cpu_mem_result xif_mem_result_if,
    if_xif.cpu_result     xif_result_if,

    output reg_req_t pad_req_o,
    input  reg_rsp_t pad_resp_i,

    input  obi_req_t  [EXT_XBAR_NMASTER_RND-1:0] ext_xbar_master_req_i,
    output obi_resp_t [EXT_XBAR_NMASTER_RND-1:0] ext_xbar_master_resp_o,

    input reg_req_t  [AO_SPC_NUM_RND:0] ext_ao_peripheral_slave_req_i,
    output reg_rsp_t [AO_SPC_NUM_RND:0] ext_ao_peripheral_slave_resp_o,

    // External slave ports
    output obi_req_t  ext_core_instr_req_o,
    input  obi_resp_t ext_core_instr_resp_i,
    output obi_req_t  ext_core_data_req_o,
    input  obi_resp_t ext_core_data_resp_i,
    output obi_req_t  ext_debug_master_req_o,
    input  obi_resp_t ext_debug_master_resp_i,
    output obi_req_t  [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_read_req_o,
    input  obi_resp_t [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_read_resp_i,
    output obi_req_t  [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_write_req_o,
    input  obi_resp_t [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_write_resp_i,
% if not is_mc:
    output obi_req_t  [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_addr_req_o,
    input  obi_resp_t [mosaic_soc_pkg::DMA_NUM_MASTER_PORTS-1:0] ext_dma_addr_resp_i,
% endif

    output fifo_req_t [mosaic_soc_pkg::DMA_CH_NUM-1:0] hw_fifo_req_o,
    input fifo_resp_t [mosaic_soc_pkg::DMA_CH_NUM-1:0] hw_fifo_resp_i,

    input logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] ext_dma_stop_i,
    input logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] hw_fifo_done_i,

    output reg_req_t ext_peripheral_slave_req_o,
    input  reg_rsp_t ext_peripheral_slave_resp_i,

    output logic  [EXT_HARTS_RND-1:0] ext_debug_req_o,
    output logic  ext_debug_reset_no,

    // PLIC external interrupts
    input logic [NEXT_INT_RND-1:0] intr_vector_ext_i,
    // FIC external interrupt
    input logic intr_ext_peripheral_i,

    //power manager exposed to top level
    //signals are unrolled to easy EDA tools
    output logic cpu_subsystem_powergate_switch_no,
    input  logic cpu_subsystem_powergate_switch_ack_ni,
    output logic peripheral_subsystem_powergate_switch_no,
    input  logic peripheral_subsystem_powergate_switch_ack_ni,
    output logic [EXT_DOMAINS_RND-1:0] external_subsystem_powergate_switch_no,
    input  logic [EXT_DOMAINS_RND-1:0] external_subsystem_powergate_switch_ack_ni,
    output logic [EXT_DOMAINS_RND-1:0] external_subsystem_powergate_iso_no,
    output logic [EXT_DOMAINS_RND-1:0] external_subsystem_rst_no,
    output logic ext_cpu_subsystem_rst_no,
    output logic [EXT_DOMAINS_RND-1:0] external_ram_banks_set_retentive_no,
    output logic [EXT_DOMAINS_RND-1:0] external_subsystem_clkgate_en_no,

    output logic [31:0] exit_value_o,

    // External SPC interface
    input logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] ext_dma_slot_tx_i,
    input logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] ext_dma_slot_rx_i,
    output logic [mosaic_soc_pkg::DMA_CH_NUM-1:0] dma_done_o
);

  import mosaic_soc_pkg::*;
  import cv32e40p_apu_core_pkg::*;
  import power_manager_pkg::*;

  localparam NUM_BYTES = mosaic_soc_pkg::MEM_SIZE;
  localparam DM_HALTADDRESS = mosaic_soc_pkg::DEBUG_START_ADDRESS + 32'h00000800; //debug rom code (section .text in linker) starts at 0x800

  localparam JTAG_IDCODE = 32'h10001c05;
% if is_mc:
  localparam NRHARTS = ${nh};
% else:
  localparam NRHARTS = EXT_HARTS + 1; // external harts + single hart mosaic_soc
% endif
  localparam BOOT_ADDR = mosaic_soc_pkg::BOOTROM_START_ADDRESS;

  // Log top level parameter values
`ifndef SYNTHESIS
  initial begin
    $display("[X-HEEP]: NUM_BYTES = %dKB", NUM_BYTES / 1024);
  end
`endif

  // masters signals
% if is_mc:
  obi_req_t  core_instr_req [NRHARTS-1:0];
  obi_resp_t core_instr_resp [NRHARTS-1:0];
  obi_req_t  core_data_req [NRHARTS-1:0];
  obi_resp_t core_data_resp [NRHARTS-1:0];
  // Per-core hart IDs (core 0 gets hart_id_i, others get sequential IDs)
  logic [31:0] hart_id_array [NRHARTS-1:0];
  always_comb begin
    // MOSAIC platform-service indices and generated software use local,
    // contiguous hart IDs. xheep_instance_id_i remains the separate SoC
    // instance namespace; an external hart_id_i base must not skew CLINT,
    // PLIC, TDU, debug masks, or mhartid-visible software indices.
    for (int i = 0; i < NRHARTS; i++) begin
      hart_id_array[i] = 32'(i);
    end
  end
% else:
  obi_req_t core_instr_req;
  obi_resp_t core_instr_resp;
  obi_req_t core_data_req;
  obi_resp_t core_data_resp;
% endif
  obi_req_t debug_master_req;
  obi_resp_t debug_master_resp;
  obi_req_t [${dma_obi_msb}:0]dma_read_req;
  obi_resp_t [${dma_obi_msb}:0]dma_read_resp;
  obi_req_t [${dma_obi_msb}:0]dma_write_req;
  obi_resp_t [${dma_obi_msb}:0]dma_write_resp;
% if not is_mc:
  obi_req_t [${dma_obi_msb}:0]dma_addr_req;
  obi_resp_t [${dma_obi_msb}:0]dma_addr_resp;
% endif

  // ram signals
  obi_req_t [mosaic_soc_pkg::NUM_BANKS-1:0] ram_slave_req;
  obi_resp_t [mosaic_soc_pkg::NUM_BANKS-1:0] ram_slave_resp;

  // w25q128jw controller signals
  logic w25q128jw_controller_intr;

  // debug signals
  obi_req_t debug_slave_req;
  obi_resp_t debug_slave_resp;

  // peripherals signals
  obi_req_t ao_peripheral_slave_req;
  obi_resp_t ao_peripheral_slave_resp;
  obi_req_t peripheral_slave_req;
  obi_resp_t peripheral_slave_resp;

  // signals to debug unit
  logic debug_core_req;
  logic debug_reset_n;
  logic [NRHARTS-1:0] debug_req;
  // core
% if is_mc:
  logic [NRHARTS-1:0] core_sleep;
  logic [NRHARTS-1:0] core_running;
  logic [NRHARTS-1:0] core_wake;
  logic [NRHARTS-1:0] core_park;
  logic [NRHARTS-1:0] clint_timer_irq;
  logic [NRHARTS-1:0] clint_software_irq;
  logic [63:0]         clint_mtime;
  logic                tdu_irq;
  // A core is "running" when it is not sleeping (WFI/halted).
  // The TDU uses this to track active cores for energy accounting
  // and dynamic scheduling decisions.
  assign core_running = ~core_sleep;
% if not tdu_enabled:
  // All-TITAN SMP configurations may intentionally omit the TDU.  There are
  // no dormant workers in that profile, so keep the dispatch controls idle.
  assign core_wake = '0;
  assign core_park = '0;
  assign tdu_irq   = 1'b0;
% endif

  // cpu_subsystem's 1-bit-per-hart control ports (debug_req_i/core_wake_i/
  // core_sleep_o) are PACKED [NUM_HARTS-1:0], matching these packed SoC nets, so
  // they connect straight through — no packed-to-unpacked adaptation. (A previous
  // unpacked-array port convention needed a boundary conversion whose per-index
  // cycle-based evaluation order dropped a worker's core_wake[h] pulse.)
% else:
  logic core_sleep;
% endif

  // irq signals
  logic irq_ack;
  logic [4:0] irq_id_out;
% if is_mc:
  logic [NRHARTS-1:0] irq_software;
  logic [NRHARTS-1:0] irq_external;
% else:
  logic irq_software;
  logic irq_external;
% endif
  logic [15:0] irq_fast;

  // Memory Map SPI Region
  obi_req_t flash_mem_slave_req;
  obi_resp_t flash_mem_slave_resp;

  // rv_timer
  logic [3:0] rv_timer_intr;

  // interrupt array
  logic [31:0] intr;
  logic [15:0] fast_intr;
% if is_mc:
  // Per-hart interrupt vectors are derived from the configured roles, never
  // from an implicit "hart zero" convention. TITANs receive the platform
  // vector. Workers receive a timer plus a TDU software-interrupt pulse; SCI
  // capability validation rejects cores that cannot support the requested
  // interrupt policy.
  logic [31:0] intr_array [NRHARTS-1:0];
  always_comb begin
% for hart, role in enumerate(hart_roles):
% if role == "titan":
    intr_array[${hart}] = intr;
    intr_array[${hart}][11] = irq_external[${hart}];
    intr_array[${hart}][7] = clint_timer_irq[${hart}];
    intr_array[${hart}][3] = clint_software_irq[${hart}]
                               | irq_software[${hart}]
                               ;
% if tdu_enabled:
    intr_array[${hart}][31] = intr[31] | tdu_irq;
% endif
% else:
    intr_array[${hart}] = '0;
    intr_array[${hart}][7] = clint_timer_irq[${hart}];
    intr_array[${hart}][3] = clint_software_irq[${hart}]
% if (interrupt_hart_mask >> hart) & 1:
                               | irq_software[${hart}]
% endif
% if tdu_enabled:
                               | core_wake[${hart}]
% endif
                               ;
% if (interrupt_hart_mask >> hart) & 1:
    intr_array[${hart}][11] = irq_external[${hart}];
% endif
% endif
% endfor
  end
% endif

  //Power manager signals
  power_manager_out_t cpu_subsystem_pwr_ctrl_out;
  power_manager_out_t peripheral_subsystem_pwr_ctrl_out;
  power_manager_out_t memory_subsystem_pwr_ctrl_out[mosaic_soc_pkg::NUM_BANKS-1:0];
  power_manager_out_t external_subsystem_pwr_ctrl_out[EXT_DOMAINS_RND-1:0];

  power_manager_in_t  cpu_subsystem_pwr_ctrl_in;
  power_manager_in_t  peripheral_subsystem_pwr_ctrl_in;
  power_manager_in_t  memory_subsystem_pwr_ctrl_in[mosaic_soc_pkg::NUM_BANKS-1:0];
  power_manager_in_t  external_subsystem_pwr_ctrl_in[EXT_DOMAINS_RND-1:0];

  // The pad reset is asynchronous to clk_i. A board button or supervisor can
  // release it at any phase, and a release inside a flop's recovery/removal
  // window lets harts and peripherals leave reset on different edges -- the
  // GLS bench showed boot depends on that phase. Assert asynchronously,
  // release on a clock edge: x-heep's own rstgen, which mosaic_system uses and
  // the MOSAIC delivery wrappers bypass. Every reset below derives from
  // rst_n_sync, including the power manager's per-domain resets.
  logic rst_n_sync;
  rstgen rstgen_i (
      .clk_i,
      .rst_ni,
      .test_mode_i(1'b0),
      .rst_no(rst_n_sync),
      .init_no()
  );

  logic cpu_subsystem_rst_n;
  logic cpu_subsystem_powergate_iso_n;

  logic peripheral_subsystem_rst_n;
  logic peripheral_subsystem_powergate_iso_n;
  logic peripheral_subsystem_clkgate_en_n;

  logic [mosaic_soc_pkg::NUM_BANKS-1:0] memory_subsystem_banks_powergate_switch_n;
  logic [mosaic_soc_pkg::NUM_BANKS-1:0] memory_subsystem_banks_powergate_switch_ack_n;
  logic [mosaic_soc_pkg::NUM_BANKS-1:0] memory_subsystem_banks_set_retentive_n;
  logic [mosaic_soc_pkg::NUM_BANKS-1:0] memory_subsystem_banks_powergate_iso_n;
  logic [mosaic_soc_pkg::NUM_BANKS-1:0] memory_subsystem_clkgate_en_n;

  //pwrgate exposed outside for UPF sim flow and switch cells
  assign cpu_subsystem_powergate_switch_no    = cpu_subsystem_pwr_ctrl_out.pwrgate_en_n;
  assign cpu_subsystem_pwr_ctrl_in.pwrgate_ack_n = cpu_subsystem_powergate_switch_ack_ni;
  //isogate exposed outside for UPF sim flow and switch cells
  assign cpu_subsystem_powergate_iso_n                 = cpu_subsystem_pwr_ctrl_out.isogate_en_n;
  assign cpu_subsystem_rst_n                  = cpu_subsystem_pwr_ctrl_out.rst_n;

  //pwrgate exposed both outside for UPF sim flow
  assign peripheral_subsystem_powergate_switch_no = peripheral_subsystem_pwr_ctrl_out.pwrgate_en_n;
  assign peripheral_subsystem_pwr_ctrl_in.pwrgate_ack_n  = peripheral_subsystem_powergate_switch_ack_ni;
  //isogate exposed outside for UPF sim flow and switch cells
  assign peripheral_subsystem_powergate_iso_n = peripheral_subsystem_pwr_ctrl_out.isogate_en_n;
  assign peripheral_subsystem_rst_n           = peripheral_subsystem_pwr_ctrl_out.rst_n;
  assign peripheral_subsystem_clkgate_en_n    = peripheral_subsystem_pwr_ctrl_out.clkgate_en_n;

  % for bank in memory_ss.iter_ram_banks():
    assign memory_subsystem_banks_powergate_switch_n[${bank.name()}] = memory_subsystem_pwr_ctrl_out[${bank.name()}].pwrgate_en_n;
    assign memory_subsystem_pwr_ctrl_in[${bank.name()}].pwrgate_ack_n = memory_subsystem_banks_powergate_switch_ack_n[${bank.name()}];
    //isogate exposed outside for UPF sim flow and switch cells
    assign memory_subsystem_banks_powergate_iso_n[${bank.name()}] = memory_subsystem_pwr_ctrl_out[${bank.name()}].isogate_en_n;
    assign memory_subsystem_banks_set_retentive_n[${bank.name()}] = memory_subsystem_pwr_ctrl_out[${bank.name()}].retentive_en_n;
    assign memory_subsystem_clkgate_en_n[${bank.name()}] = memory_subsystem_pwr_ctrl_out[${bank.name()}].clkgate_en_n;
  % endfor

  for (genvar i = 0; i < EXT_DOMAINS_RND; i = i + 1) begin : gen_external_subsystem_pwr_gating
    assign external_subsystem_powergate_switch_no[i]        = external_subsystem_pwr_ctrl_out[i].pwrgate_en_n;
    assign external_subsystem_powergate_iso_no[i] = external_subsystem_pwr_ctrl_out[i].isogate_en_n;
    assign external_subsystem_rst_no[i] = external_subsystem_pwr_ctrl_out[i].rst_n;
    assign external_ram_banks_set_retentive_no[i]           = external_subsystem_pwr_ctrl_out[i].retentive_en_n;
    assign external_subsystem_clkgate_en_no[i] = external_subsystem_pwr_ctrl_out[i].clkgate_en_n;
    assign external_subsystem_pwr_ctrl_in[i].pwrgate_ack_n = external_subsystem_powergate_switch_ack_ni[i];
  end

  // DMA
  logic dma_done_intr;
  logic dma_window_intr;

  // SPI
  logic spi_flash_intr, spi_intr, spi_rx_valid, spi_tx_ready;

/* verilator lint_off UNDRIVEN */
  // GPIO
  logic [31:8] gpio_in;
  logic [31:8] gpio_out;
  logic [31:8] gpio_oe;

  // GPIO_AO
  logic [7:0] gpio_ao_in;
  logic [7:0] gpio_ao_out;
  logic [7:0] gpio_ao_oe;
  logic [7:0] gpio_ao_intr;
/* verilator lint_on UNDRIVEN */

  // I2s
  logic i2s_rx_valid;

  assign intr = {
% if is_mc:
    irq_fast, 4'b0, irq_external[0], 3'b0, rv_timer_intr[0], 3'b0, irq_software[0], 3'b0
% else:
    irq_fast, 4'b0, irq_external, 3'b0, rv_timer_intr[0], 3'b0, irq_software, 3'b0
% endif
  };

  assign fast_intr = {
    intr_ext_peripheral_i,
    dma_window_intr,
    gpio_ao_intr,
    spi_flash_intr,
    spi_intr,
    dma_done_intr,
    rv_timer_intr[3],
    rv_timer_intr[2],
    rv_timer_intr[1]
  };

  cpu_subsystem #(
      .BOOT_ADDR(BOOT_ADDR),
% if is_mc:
      .DM_HALTADDRESS(DM_HALTADDRESS),
      .NUM_HARTS(NRHARTS)
% else:
      .DM_HALTADDRESS(DM_HALTADDRESS)
% endif
  ) cpu_subsystem_i (
      // Clock and Reset
      .clk_i,
      .rst_ni(cpu_subsystem_rst_n && debug_reset_n),
% if is_mc:
      .hart_id_i(hart_id_array),  // per-core unique hart IDs
      .core_instr_req_o(core_instr_req),
      .core_instr_resp_i(core_instr_resp),
      .core_data_req_o(core_data_req),
      .core_data_resp_i(core_data_resp),
      .irq_i(intr_array),  // per-hart interrupt vectors
      .time_i(clint_mtime),
      .debug_req_i(debug_req),
      .core_wake_i(core_wake),  // TDU/orchestrator wake → releases dormant workers
      .core_park_i(core_park),  // worker completion → re-arms dormancy for next task
      .core_sleep_o(core_sleep)
    );
% else:
      .hart_id_i,
      .core_instr_req_o(core_instr_req),
      .core_instr_resp_i(core_instr_resp),
      .core_data_req_o(core_data_req),
      .core_data_resp_i(core_data_resp),
      .xif_compressed_if,
      .xif_issue_if,
      .xif_commit_if,
      .xif_mem_if,
      .xif_mem_result_if,
      .xif_result_if,
      .irq_i(intr),
      .irq_ack_o(irq_ack),
      .irq_id_o(irq_id_out),
      .debug_req_i(debug_core_req),
      .core_sleep_o(core_sleep)
  );
% endif

% if debug_enabled:
  debug_subsystem #(
      .NRHARTS    (NRHARTS),
      .JTAG_IDCODE(JTAG_IDCODE),
      .SPI_SLAVE(${has_spi_slave}),
      .HART_DEBUG_CAPABLE(NRHARTS'(${debug_hart_mask}))
  ) debug_subsystem_i (
      .clk_i,
      .rst_ni(rst_n_sync),
      .jtag_tck_i,
      .jtag_tms_i,
      .jtag_trst_ni,
      .jtag_tdi_i,
      .jtag_tdo_o,
      .spi_slave_sck_i(spi_slave_sck_i),
      .spi_slave_cs_i(spi_slave_cs_i),
      .spi_slave_miso_o(spi_slave_miso_o),
      .spi_slave_miso_oe_o(spi_slave_miso_oe_o),
      .spi_slave_mosi_i(spi_slave_mosi_i),
      .debug_core_req_o(debug_req),
      .debug_ndmreset_no(debug_reset_n),
      .debug_slave_req_i(debug_slave_req),
      .debug_slave_resp_o(debug_slave_resp),
      .debug_master_req_o(debug_master_req),
      .debug_master_resp_i(debug_master_resp)
  );
% else:
  // soc.debug: false -- JTAG DTM and the RISC-V debug module are omitted.
  // The design keeps its debug crossbar master and slave slots so the bus
  // index map is identical to a debug-enabled build; both are tied off here.
  // Consequences: no halt/step/resume, no external memory access over JTAG,
  // and no debug-driven ndmreset. Bring-up must go through the boot ROM.
  assign debug_req         = '0;
  assign debug_reset_n     = 1'b1;
  assign debug_slave_resp  = '0;
  assign debug_master_req  = '0;
  assign jtag_tdo_o        = 1'b0;
  assign spi_slave_miso_o  = 1'b0;
  assign spi_slave_miso_oe_o = 1'b0;

  // Inputs the debug subsystem would have consumed.
  logic unused_debug_inputs;
  assign unused_debug_inputs = ^{jtag_tck_i, jtag_tms_i, jtag_trst_ni,
                                 jtag_tdi_i, spi_slave_sck_i, spi_slave_cs_i,
                                 spi_slave_mosi_i, debug_slave_req,
                                 debug_master_resp};
% endif

  system_bus #(
      .NUM_BANKS(mosaic_soc_pkg::NUM_BANKS),
      .EXT_XBAR_NMASTER(EXT_XBAR_NMASTER)
  ) system_bus_i (
      .clk_i,
      .rst_ni(rst_n_sync && debug_reset_n),
% if is_mc:
      .core_instr_req_i(core_instr_req),
      .core_instr_resp_o(core_instr_resp),
      .core_data_req_i(core_data_req),
      .core_data_resp_o(core_data_resp),
% else:
      .core_instr_req_i(core_instr_req),
      .core_instr_resp_o(core_instr_resp),
      .core_data_req_i(core_data_req),
      .core_data_resp_o(core_data_resp),
% endif
      .debug_master_req_i(debug_master_req),
      .debug_master_resp_o(debug_master_resp),
      .dma_read_req_i(dma_read_req),
      .dma_read_resp_o(dma_read_resp),
      .dma_write_req_i(dma_write_req),
      .dma_write_resp_o(dma_write_resp),
% if not is_mc:
      .dma_addr_req_i(dma_addr_req),
      .dma_addr_resp_o(dma_addr_resp),
% endif
      .ext_xbar_master_req_i(ext_xbar_master_req_i),
      .ext_xbar_master_resp_o(ext_xbar_master_resp_o),
      .ram_req_o(ram_slave_req),
      .ram_resp_i(ram_slave_resp),
      .debug_slave_req_o(debug_slave_req),
      .debug_slave_resp_i(debug_slave_resp),
      .ao_peripheral_slave_req_o(ao_peripheral_slave_req),
      .ao_peripheral_slave_resp_i(ao_peripheral_slave_resp),
      .peripheral_slave_req_o(peripheral_slave_req),
      .peripheral_slave_resp_i(peripheral_slave_resp),
      .flash_mem_slave_req_o(flash_mem_slave_req),
      .flash_mem_slave_resp_i(flash_mem_slave_resp),
      .ext_core_instr_req_o(ext_core_instr_req_o),
      .ext_core_instr_resp_i(ext_core_instr_resp_i),
      .ext_core_data_req_o(ext_core_data_req_o),
      .ext_core_data_resp_i(ext_core_data_resp_i),
      .ext_debug_master_req_o(ext_debug_master_req_o),
      .ext_debug_master_resp_i(ext_debug_master_resp_i),
      .ext_dma_read_req_o(ext_dma_read_req_o),
      .ext_dma_read_resp_i(ext_dma_read_resp_i),
      .ext_dma_write_req_o(ext_dma_write_req_o),
      .ext_dma_write_resp_i(ext_dma_write_resp_i)
% if not is_mc:
      ,
      .ext_dma_addr_req_o(ext_dma_addr_req_o),
      .ext_dma_addr_resp_i(ext_dma_addr_resp_i)
% endif
  );

  memory_subsystem #(
      .NUM_BANKS(mosaic_soc_pkg::NUM_BANKS)
  ) memory_subsystem_i (
      .clk_i,
      .rst_ni(rst_n_sync && debug_reset_n),
      .clk_gate_en_ni(memory_subsystem_clkgate_en_n),
      .ram_req_i(ram_slave_req),
      .ram_resp_o(ram_slave_resp),
      .pwrgate_ni(memory_subsystem_banks_powergate_switch_n),
      .pwrgate_ack_no(memory_subsystem_banks_powergate_switch_ack_n),
      .set_retentive_ni(memory_subsystem_banks_set_retentive_n)
  );

  ao_peripheral_subsystem #(
      .AO_SPC_NUM(AO_SPC_NUM)
  ) ao_peripheral_subsystem_i (
      .clk_i,
      .rst_ni(rst_n_sync && debug_reset_n),
      .slave_req_i(ao_peripheral_slave_req),
      .slave_resp_o(ao_peripheral_slave_resp),
      .spc2ao_req_i(ext_ao_peripheral_slave_req_i),
      .ao2spc_resp_o(ext_ao_peripheral_slave_resp_o),
      .xheep_instance_id_i,
      .boot_select_i,
      .execute_from_flash_i,
      .exit_valid_o,
      .exit_value_o,
      .spimemio_req_i(flash_mem_slave_req),
      .spimemio_resp_o(flash_mem_slave_resp),
      .w25q128jw_controller_intr_o(w25q128jw_controller_intr),
      .spi_flash_sck_o,
      .spi_flash_sck_en_o(spi_flash_sck_oe_o),
      .spi_flash_csb_o({spi_flash_cs_1_o,spi_flash_cs_0_o}),
      .spi_flash_csb_en_o({spi_flash_cs_1_oe_o, spi_flash_cs_0_oe_o}),
      .spi_flash_sd_o({spi_flash_sd_3_o,spi_flash_sd_2_o, spi_flash_sd_1_o, spi_flash_sd_0_o}),
      .spi_flash_sd_en_o({spi_flash_sd_3_oe_o,spi_flash_sd_2_oe_o, spi_flash_sd_1_oe_o, spi_flash_sd_0_oe_o}),
      .spi_flash_sd_i({spi_flash_sd_3_i,spi_flash_sd_2_i, spi_flash_sd_1_i, spi_flash_sd_0_i}),
      .intr_i(intr),
      .intr_vector_ext_i,
% if is_mc:
      // The legacy power manager controls one aggregate CPU domain.  It may
      // gate that domain only when every hart is asleep; using hart 0 alone
      // could reset active workers.
      .core_sleep_i(&core_sleep),
% else:
      .core_sleep_i(core_sleep),
% endif
      .cpu_subsystem_pwr_ctrl_o(cpu_subsystem_pwr_ctrl_out),
      .peripheral_subsystem_pwr_ctrl_o(peripheral_subsystem_pwr_ctrl_out),
      .memory_subsystem_pwr_ctrl_o(memory_subsystem_pwr_ctrl_out),
      .external_subsystem_pwr_ctrl_o(external_subsystem_pwr_ctrl_out),
      .cpu_subsystem_pwr_ctrl_i(cpu_subsystem_pwr_ctrl_in),
      .peripheral_subsystem_pwr_ctrl_i(peripheral_subsystem_pwr_ctrl_in),
      .memory_subsystem_pwr_ctrl_i(memory_subsystem_pwr_ctrl_in),
      .external_subsystem_pwr_ctrl_i(external_subsystem_pwr_ctrl_in),
      .rv_timer_0_intr_o(rv_timer_intr[0]),
      .rv_timer_1_intr_o(rv_timer_intr[1]),
      .dma_read_req_o(dma_read_req),
      .dma_read_resp_i(dma_read_resp),
      .dma_write_req_o(dma_write_req),
      .dma_write_resp_i(dma_write_resp),
% if not is_mc:
      .dma_addr_req_o(dma_addr_req),
      .dma_addr_resp_i(dma_addr_resp),
% endif
      .dma_done_intr_o(dma_done_intr),
      .dma_window_intr_o(dma_window_intr),
      .hw_fifo_req_o,
      .hw_fifo_resp_i,
      .spi_flash_intr_event_o(spi_flash_intr),
      .pad_req_o,
      .pad_resp_i,
      .fast_intr_i(fast_intr),
      .fast_intr_o(irq_fast),
      .cio_gpio_i(gpio_ao_in),
      .cio_gpio_o(gpio_ao_out),
      .cio_gpio_en_o(gpio_ao_oe),
      .intr_gpio_o(gpio_ao_intr),
      .spi_rx_valid_i(spi_rx_valid),
      .spi_tx_ready_i(spi_tx_ready),
      .i2s_rx_valid_i(i2s_rx_valid),
      .ext_peripheral_slave_req_o,
      .ext_peripheral_slave_resp_i,
      .ext_dma_slot_tx_i,
      .ext_dma_slot_rx_i,
      .ext_dma_stop_i,
      .hw_fifo_done_i,
      .dma_done_o
% if is_mc:
      ,
      .clint_timer_irq_o(clint_timer_irq),
      .clint_software_irq_o(clint_software_irq),
      .clint_mtime_o(clint_mtime)
% endif
% if tdu_enabled:
      ,
      // MOSAIC TDU
      .tdu_core_running_i(core_running),
      .tdu_core_sleep_i(core_sleep),
      .tdu_core_wake_o(core_wake),
      .tdu_core_park_o(core_park),
      .tdu_irq_o(tdu_irq)
% endif
  );

  peripheral_subsystem peripheral_subsystem_i (
      .clk_i,
      .rst_ni(peripheral_subsystem_rst_n && debug_reset_n),
      .clk_gate_en_ni(peripheral_subsystem_clkgate_en_n),
      .slave_req_i(peripheral_slave_req),
      .slave_resp_o(peripheral_slave_resp),
      .intr_vector_ext_i,
      .irq_plic_o(irq_external),
      .msip_o(irq_software),
      .w25q128jw_controller_intr_i(w25q128jw_controller_intr),
      .cio_gpio_i(gpio_in),
      .cio_gpio_o(gpio_out),
      .cio_gpio_en_o(gpio_oe),
      .cio_scl_i(i2c_scl_i),
      .cio_scl_o(i2c_scl_o),
      .cio_scl_en_o(i2c_scl_oe_o),
      .cio_sda_i(i2c_sda_i),
      .cio_sda_o(i2c_sda_o),
      .cio_sda_en_o(i2c_sda_oe_o),
      .spi_sck_o,
      .spi_sck_en_o(spi_sck_oe_o),
      .spi_csb_o({spi_cs_1_o,spi_cs_0_o}),
      .spi_csb_en_o({spi_cs_1_oe_o, spi_cs_0_oe_o}),
      .spi_sd_o({spi_sd_3_o,spi_sd_2_o, spi_sd_1_o, spi_sd_0_o}),
      .spi_sd_en_o({spi_sd_3_oe_o,spi_sd_2_oe_o, spi_sd_1_oe_o, spi_sd_0_oe_o}),
      .spi_sd_i({spi_sd_3_i,spi_sd_2_i, spi_sd_1_i, spi_sd_0_i}),
      .spi_intr_event_o(spi_intr),
      .spi_rx_valid_o(spi_rx_valid),
      .spi_tx_ready_o(spi_tx_ready),
      .spi2_sck_o,
      .spi2_sck_en_o(spi2_sck_oe_o),
      .spi2_csb_o({spi2_cs_1_o, spi2_cs_0_o}),
      .spi2_csb_en_o({spi2_cs_1_oe_o, spi2_cs_0_oe_o}),
      .spi2_sd_o({spi2_sd_3_o, spi2_sd_2_o, spi2_sd_1_o, spi2_sd_0_o}),
      .spi2_sd_en_o({spi2_sd_3_oe_o, spi2_sd_2_oe_o, spi2_sd_1_oe_o, spi2_sd_0_oe_o}),
      .spi2_sd_i({spi2_sd_3_i, spi2_sd_2_i, spi2_sd_1_i, spi2_sd_0_i}),
      .rv_timer_2_intr_o(rv_timer_intr[2]),
      .rv_timer_3_intr_o(rv_timer_intr[3]),
      .pdm2pcm_clk_o(pdm2pcm_clk_o),
      .pdm2pcm_clk_en_o(pdm2pcm_clk_oe_o),
      .pdm2pcm_pdm_i(pdm2pcm_pdm_i),
      .i2s_sck_o(i2s_sck_o),
      .i2s_sck_oe_o(i2s_sck_oe_o),
      .i2s_sck_i(i2s_sck_i),
      .i2s_ws_o(i2s_ws_o),
      .i2s_ws_oe_o(i2s_ws_oe_o),
      .i2s_ws_i(i2s_ws_i),
      .i2s_sd_o(i2s_sd_o),
      .i2s_sd_oe_o(i2s_sd_oe_o),
      .i2s_sd_i(i2s_sd_i),
      .i2s_rx_valid_o(i2s_rx_valid),
      .ddr_rcv_clk_i,  
      .ddr_snd_clk_o,
      .ddr_rcv_0_i,
      .ddr_rcv_1_i,
      .ddr_rcv_2_i,
      .ddr_rcv_3_i,
      .ddr_snd_0_o,
      .ddr_snd_1_o,
      .ddr_snd_2_o,
      .ddr_snd_3_o,
      .uart_rx_i,
      .uart_tx_o
  );

  // Debug_req assign
% if is_mc:
  // All debug_req go to multi-core cpu_subsystem (handles them per-hart internally)
  assign debug_core_req = debug_req[0];
  // Explicit MOSAIC topology owns every debug hart. The legacy EXT_HARTS hook
  // cannot be combined without extending the generated PLIC/CLINT/software
  // index space, so expose an honest idle output and fail a bad simulation.
  assign ext_debug_req_o = '0;
`ifndef SYNTHESIS
  initial assert (EXT_HARTS == 0)
    else $fatal(1, "EXT_HARTS is unsupported with an explicit MOSAIC topology");
`endif
% else:
  if (NRHARTS == 1) begin : gen_single_hart_debug
    assign debug_core_req = debug_req;
    assign ext_debug_req_o  = 1'b0;
  end else begin : gen_multi_hart_debug
    always @(*) begin
      for (int i = 0; i < NRHARTS; i++) begin
        if (i == 0) debug_core_req = debug_req[i];
        else ext_debug_req_o[i-1] = debug_req[i];
      end
    end
  end
% endif

  assign ext_cpu_subsystem_rst_no = cpu_subsystem_rst_n;
  assign ext_debug_reset_no = debug_reset_n;

  assign pdm2pcm_pdm_o = 0;
  assign pdm2pcm_pdm_oe_o = 0;

  % for pin in xheep.get_padring().get_connected_pins():
    % if pin.module == "mosaic_soc" and "gpio_" in pin.name:
      <% gpio_number = int(pin.name.split('_')[-1]) %>
      % if gpio_number < 8: # THE NUMBER OF GPIOS ON THE ALWAYS ON PERIPHERAL DOMAIN GPIO
        assign gpio_ao_in[${gpio_number}] = gpio_${gpio_number}_i;
        assign gpio_${gpio_number}_o      = gpio_ao_out[${gpio_number}];
        assign gpio_${gpio_number}_oe_o   = gpio_ao_oe[${gpio_number}];
      % else:
        assign gpio_in[${gpio_number}]  = gpio_${gpio_number}_i;
        assign gpio_${gpio_number}_o    = gpio_out[${gpio_number}];
        assign gpio_${gpio_number}_oe_o = gpio_oe[${gpio_number}];
      %endif
    % endif
  % endfor


endmodule  // mosaic_soc
