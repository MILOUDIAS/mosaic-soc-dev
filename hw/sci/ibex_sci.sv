// Copyright MOSAIC-SoC
// SPDX-License-Identifier: Apache-2.0
//
// ibex_sci.sv — Standard Core Interface wrapper for the lowRISC Ibex core.
//
// Wraps ibex_top, converting its dual simple req/gnt/rvalid memory interfaces
// (separate I-fetch and D-access) to OBI v1.3 struct ports. Ibex's native
// protocol is field-for-field compatible with OBI, so the conversion is a set
// of direct assignments (no FSM / handshake translation, unlike the Wishbone
// SERV/FazyRV wrappers).
//
// Ibex port reference: refs/IP_Cores_Catalog/ibex/rtl/ibex_top.sv. The req/gnt
// mapping mirrors x-heep's cve2_xif_wrapper.sv (cve2 is the OpenHW fork of this
// same core). PoC defaults: PMPEnable=0, ICache=0, SecureIbex=0, RegFileFF —
// chosen to fit the GF180MCU area budget (no cache SRAM, no lockstep).
//
// Watch-outs handled below:
//   * fetch_enable_i / mcounteren_writable_i are ibex_mubi_t (multi-bit safe
//     encoded), NOT plain logic — a 1-bit assign would leave the core never
//     fetching. We map the boolean fetch_enable to IbexMuBiOn/Off.
//   * irq_fast_i is [14:0] (15 bits) — narrower than cve2's [31:16].
//   * Many typed side-band ports (icache ram_cfg, scrambling, lockstep shadow,
//     alerts, crash dump) are tied off / left open for the PoC config.

module ibex_sci
  import ibex_pkg::*;
#(
    parameter bit          RV32E          = 1'b0,
    parameter rv32m_e      RV32M          = RV32MFast,
    parameter rv32b_e      RV32B          = RV32BNone,
    parameter rv32zc_e     RV32ZC         = RV32ZcaZcbZcmp,
    parameter regfile_e    RegFile        = RegFileFF,
    parameter bit          PMPEnable      = 1'b0,
    parameter int unsigned PMPNumRegions  = 4,
    parameter bit          ICache         = 1'b0,
    parameter bit          SecureIbex     = 1'b0,
    parameter int unsigned MHPMCounterNum = 0
) (
    input logic clk_i,
    input logic rst_ni,

    // Core control
    input  logic [31:0] hart_id_i,
    input  logic [31:0] boot_addr_i,
    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Interrupts (RISC-V mip/mie layout, packed into a 32-bit vector)
    input  logic [31:0] irq_i,

    // Debug
    input  logic        debug_req_i,

    // OBI Instruction master port (read-only)
    output obi_pkg::obi_req_t  instr_req_o,
    input  obi_pkg::obi_resp_t instr_resp_i,

    // OBI Data master port (read-write)
    output obi_pkg::obi_req_t  data_req_o,
    input  obi_pkg::obi_resp_t data_resp_i
);

  // ── Ibex native memory-interface signals ─────────────────────────
  logic        instr_req;
  logic        instr_gnt;
  logic        instr_rvalid;
  logic [31:0] instr_addr;
  logic [31:0] instr_rdata;

  logic        data_req;
  logic        data_gnt;
  logic        data_rvalid;
  logic        data_we;
  logic [ 3:0] data_be;
  logic [31:0] data_addr;
  logic [31:0] data_wdata;
  logic [31:0] data_rdata;
  logic        ibex_core_sleep;

  // The native signal only reports architectural sleep.  A MOSAIC worker
  // held behind fetch_enable_i is dormant as well, including immediately
  // after reset and after a TDU PARK_REQ.
  assign core_sleep_o = ~fetch_enable_i | ibex_core_sleep;

  // ── Ibex core ────────────────────────────────────────────────────
  ibex_top #(
      .PMPEnable      (PMPEnable),
      .PMPNumRegions  (PMPNumRegions),
      .MHPMCounterNum (MHPMCounterNum),
      .RV32E          (RV32E),
      .RV32M          (RV32M),
      .RV32B          (RV32B),
      .RV32ZC         (RV32ZC),
      .RegFile        (RegFile),
      .ICache         (ICache),
      .SecureIbex     (SecureIbex)
  ) i_core (
      .clk_i (clk_i),
      // Reset-hold while parked makes TDU PARK/WAKE repeatable rather than
      // resuming after the worker firmware's terminal spin loop.
      .rst_ni(rst_ni & fetch_enable_i),

      .test_en_i (1'b0),
      // ICache tag/data RAM config — unused (ICache=0); tie off / leave open
      .ram_cfg_icache_tag_i     ('0),
      .ram_cfg_rsp_icache_tag_o (),
      .ram_cfg_icache_data_i    ('0),
      .ram_cfg_rsp_icache_data_o(),

      .hart_id_i  (hart_id_i),
      .boot_addr_i(boot_addr_i),

      // Instruction memory interface
      .instr_req_o       (instr_req),
      .instr_gnt_i       (instr_gnt),
      .instr_rvalid_i    (instr_rvalid),
      .instr_addr_o      (instr_addr),
      .instr_rdata_i     (instr_rdata),
      .instr_rdata_intg_i('0),
      .instr_err_i       (1'b0),

      // Data memory interface
      .data_req_o       (data_req),
      .data_gnt_i       (data_gnt),
      .data_rvalid_i    (data_rvalid),
      .data_we_o        (data_we),
      .data_be_o        (data_be),
      .data_addr_o      (data_addr),
      .data_wdata_o     (data_wdata),
      .data_wdata_intg_o(),
      .data_rdata_i     (data_rdata),
      .data_rdata_intg_i('0),
      .data_err_i       (1'b0),

      // Interrupts — map the packed vector onto Ibex's discrete inputs.
      // NOTE irq_fast_i is 15 bits ([30:16]), one narrower than cve2.
      .irq_software_i(irq_i[3]),
      .irq_timer_i   (irq_i[7]),
      .irq_external_i(irq_i[11]),
      .irq_fast_i    (irq_i[30:16]),
      .irq_nm_i      (1'b0),

      // Scrambling interface — unused (SecureIbex=0 / ICacheScramble=0)
      .scramble_key_valid_i(1'b0),
      .scramble_key_i      ('0),
      .scramble_nonce_i    ('0),
      .scramble_req_o      (),

      // Debug
      .debug_req_i        (debug_req_i),
      .crash_dump_o       (),
      .double_fault_seen_o(),

      // CPU control — fetch_enable / mcounteren are multi-bit-encoded
      .fetch_enable_i        (fetch_enable_i ? ibex_pkg::IbexMuBiOn : ibex_pkg::IbexMuBiOff),
      .mcounteren_writable_i (ibex_pkg::IbexMuBiOff),
      .alert_minor_o         (),
      .alert_major_internal_o(),
      .alert_major_bus_o     (),
      .core_sleep_o          (ibex_core_sleep),

      // DFT
      .scan_rst_ni(rst_ni & fetch_enable_i),

      // Lockstep / shadow outputs — unused (SecureIbex=0)
      .lockstep_cmp_en_o       (),
      .data_req_shadow_o       (),
      .data_we_shadow_o        (),
      .data_be_shadow_o        (),
      .data_addr_shadow_o      (),
      .data_wdata_shadow_o     (),
      .data_wdata_intg_shadow_o(),
      .instr_req_shadow_o      (),
      .instr_addr_shadow_o     ()
  );

  // ── Instruction req/gnt → OBI (read-only) ────────────────────────
  assign instr_req_o.req   = instr_req;
  assign instr_req_o.addr  = instr_addr;
  assign instr_req_o.we    = 1'b0;
  assign instr_req_o.be    = 4'b1111;
  assign instr_req_o.wdata = '0;

  assign instr_gnt         = instr_resp_i.gnt;
  assign instr_rvalid      = instr_resp_i.rvalid;
  assign instr_rdata       = instr_resp_i.rdata;

  // ── Data req/gnt → OBI (read-write) ──────────────────────────────
  assign data_req_o.req    = data_req;
  assign data_req_o.addr   = data_addr;
  assign data_req_o.we     = data_we;
  assign data_req_o.be     = data_be;
  assign data_req_o.wdata  = data_wdata;

  assign data_gnt          = data_resp_i.gnt;
  assign data_rvalid       = data_resp_i.rvalid;
  assign data_rdata        = data_resp_i.rdata;

endmodule : ibex_sci
