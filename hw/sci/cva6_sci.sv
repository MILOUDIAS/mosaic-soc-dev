// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// cva6_sci.sv — Standard Core Interface wrapper for CVA6 (32-bit, sim-only).
//
// Wraps CVA6 configured by hw/vendor/mosaic/cva6/core/include/
// cv32a6_mosaic_config_pkg.sv (cv32a65x derivative: XLEN=32, M-mode only,
// WT D$, fully-uncached data side, CVXIF off, RVA off) and converts its
// single AXI4 port to a unified OBI master through the burst-capable
// xheep_axi_burst_to_obi bridge (64-bit AXI data -> 32-bit OBI).
//
// CVA6 remains EXCLUDED from the GF180 tapeout (area) — this integration is
// for simulation/architecture exploration. Debug is disabled in the config
// (DebugEn=0), so debug_req_i is accepted but tied off, like the other SCI
// cores without debug support.

module cva6_sci #(
    parameter logic [31:0] BOOT_ADDR = 32'h00000180
) (
    input logic clk_i,
    input logic rst_ni,

    // Core control
    input  logic [31:0] hart_id_i,
    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Interrupts (RISC-V mip bit layout: 3=MSIP, 7=MTIP, 11=MEIP)
    input  logic [31:0] irq_i,

    // Debug
    input  logic        debug_req_i,  // unused — DebugEn=0 in the MOSAIC config

    // OBI unified master port (all core traffic via the AXI bridge)
    output obi_pkg::obi_req_t  mem_req_o,
    input  obi_pkg::obi_resp_t mem_resp_i
);

    // The core's whole personality comes from cva6_config_pkg (the MOSAIC
    // package is the one compiled into this build).
    localparam config_pkg::cva6_cfg_t CVA6Cfg =
        build_config_pkg::build_config(cva6_config_pkg::cva6_cfg);

    // ── AXI channel typedefs — byte-identical mirrors of the parameter
    //    defaults in cva6.sv, passed explicitly so both sides agree ─────────
    typedef struct packed {
      logic [CVA6Cfg.AxiIdWidth-1:0]   id;
      logic [CVA6Cfg.AxiAddrWidth-1:0] addr;
      axi_pkg::len_t                   len;
      axi_pkg::size_t                  size;
      axi_pkg::burst_t                 burst;
      logic                            lock;
      axi_pkg::cache_t                 cache;
      axi_pkg::prot_t                  prot;
      axi_pkg::qos_t                   qos;
      axi_pkg::region_t                region;
      logic [CVA6Cfg.AxiUserWidth-1:0] user;
    } axi_ar_chan_t;
    typedef struct packed {
      logic [CVA6Cfg.AxiIdWidth-1:0]   id;
      logic [CVA6Cfg.AxiAddrWidth-1:0] addr;
      axi_pkg::len_t                   len;
      axi_pkg::size_t                  size;
      axi_pkg::burst_t                 burst;
      logic                            lock;
      axi_pkg::cache_t                 cache;
      axi_pkg::prot_t                  prot;
      axi_pkg::qos_t                   qos;
      axi_pkg::region_t                region;
      axi_pkg::atop_t                  atop;
      logic [CVA6Cfg.AxiUserWidth-1:0] user;
    } axi_aw_chan_t;
    typedef struct packed {
      logic [CVA6Cfg.AxiDataWidth-1:0]     data;
      logic [(CVA6Cfg.AxiDataWidth/8)-1:0] strb;
      logic                                last;
      logic [CVA6Cfg.AxiUserWidth-1:0]     user;
    } axi_w_chan_t;
    typedef struct packed {
      logic [CVA6Cfg.AxiIdWidth-1:0]   id;
      axi_pkg::resp_t                  resp;
      logic [CVA6Cfg.AxiUserWidth-1:0] user;
    } b_chan_t;
    typedef struct packed {
      logic [CVA6Cfg.AxiIdWidth-1:0]   id;
      logic [CVA6Cfg.AxiDataWidth-1:0] data;
      axi_pkg::resp_t                  resp;
      logic                            last;
      logic [CVA6Cfg.AxiUserWidth-1:0] user;
    } r_chan_t;
    typedef struct packed {
      axi_aw_chan_t aw;
      logic         aw_valid;
      axi_w_chan_t  w;
      logic         w_valid;
      logic         b_ready;
      axi_ar_chan_t ar;
      logic         ar_valid;
      logic         r_ready;
    } noc_req_t;
    typedef struct packed {
      logic    aw_ready;
      logic    ar_ready;
      logic    w_ready;
      logic    b_valid;
      b_chan_t b;
      logic    r_valid;
      r_chan_t r;
    } noc_resp_t;

    noc_req_t  noc_req;
    noc_resp_t noc_resp;

    // Reset-hold dormancy covers the core AND the bridge, so a parked hart
    // holds no partial AXI transaction when it wakes.
    logic core_rst_n;
    assign core_rst_n = rst_ni & fetch_enable_i;

    cva6 #(
        .CVA6Cfg      (CVA6Cfg),
        .axi_ar_chan_t(axi_ar_chan_t),
        .axi_aw_chan_t(axi_aw_chan_t),
        .axi_w_chan_t (axi_w_chan_t),
        .b_chan_t     (b_chan_t),
        .r_chan_t     (r_chan_t),
        .noc_req_t    (noc_req_t),
        .noc_resp_t   (noc_resp_t)
    ) i_cva6 (
        .clk_i        (clk_i),
        .rst_ni       (core_rst_n),
        .boot_addr_i  (BOOT_ADDR),
        .hart_id_i    (hart_id_i),
        // irq_i[0] = M-mode external, irq_i[1] = S-mode external (RVS=0 -> 0)
        .irq_i        ({1'b0, irq_i[11]}),
        .ipi_i        (irq_i[3]),
        .time_irq_i   (irq_i[7]),
        .debug_req_i  (1'b0),
        .rvfi_probes_o(),
        .cvxif_req_o  (),
        .cvxif_resp_i ('0),
        .noc_req_o    (noc_req),
        .noc_resp_i   (noc_resp)
    );

    xheep_axi_burst_to_obi #(
        .obi_req_t  (obi_pkg::obi_req_t),
        .obi_resp_t (obi_pkg::obi_resp_t),
        .axi_req_t  (noc_req_t),
        .axi_resp_t (noc_resp_t),
        .AxiIdWidth (CVA6Cfg.AxiIdWidth)
    ) i_bridge (
        .clk_i     (clk_i),
        .rst_ni    (core_rst_n),
        .axi_req_i (noc_req),
        .axi_resp_o(noc_resp),
        .obi_req_o (mem_req_o),
        .obi_resp_i(mem_resp_i)
    );

    // CVA6 has no exported sleep state here: report "asleep" while parked so
    // the TDU's CORE_STATUS reflects un-woken workers (titan role: always 1
    // -> never asleep).
    assign core_sleep_o = ~fetch_enable_i;

endmodule : cva6_sci
