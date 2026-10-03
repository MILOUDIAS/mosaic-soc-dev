// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// serv_sci.sv — Standard Core Interface wrapper for SERV (via servile).
//
// Wraps SERV+servile, converting its unified Wishbone Lite master port
// (I+D arbitrated by servile internally) to OBI v1.3.
//
// servile port map (from refs/IP_Cores_Catalog/serv/servile/servile.v):
//   i_clk, i_rst (active-high), i_timer_irq
//   o_wb_mem_adr/dat/sel/we/stb, i_wb_mem_rdt/ack  (unified I+D bus)
//   o_wb_ext_adr/dat/sel/we/stb, i_wb_ext_rdt/ack  (extension bus)
//   o_rf_waddr/wdata/wen, o_rf_raddr, i_rf_rdata, o_rf_ren  (register file)
//
// SERV has no hart_id port, no debug interface, no fetch_enable (starts at
// reset_pc), and no WFI/sleep output. The register file is provided by
// serv_rf_ram (a simple dual-port RAM).

module serv_sci #(
    parameter int unsigned W = 1,               // 1=SERV, 4=QERV
    parameter bit WITH_CSR = 1'b1,
    parameter bit COMPRESSED = 1'b0,
    parameter bit MDU = 1'b0,
    parameter bit PRE_REGISTER = 1'b0,
    parameter logic [31:0] RESET_PC = 32'h00000180
) (
    input logic clk_i,
    input logic rst_ni,

    // Core control
    input  logic [31:0] hart_id_i,    // unused — SERV has no mhartid
    // fetch_enable_i: SERV has no native fetch-enable, so we emulate a dormant
    // worker by holding the core in reset until this is asserted (driven by the
    // per-hart wake latch in cpu_subsystem → TDU.core_wake_o). Closes the loop.
    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Interrupts
    input  logic [31:0] irq_i,

    // Debug
    input  logic        debug_req_i,  // unused — SERV has no debug port

    // OBI unified master port (read-write, arbitrated I+D)
    output obi_pkg::obi_req_t  mem_req_o,
    input  obi_pkg::obi_resp_t mem_resp_i
);

    // ── servile Wishbone Lite signals ────────────────────────────
    //
    // servile exposes TWO Wishbone ports, and both must be served (bug 31).
    // servile.v feeds servile_mux with the DATA bus only, and the mux splits
    // it on the top two address bits:
    //
    //     servile_mux.v:  wire ext = (i_wb_cpu_adr[31:30] != 2'b00);
    //
    // so data accesses below 0x4000_0000 leave on the "mem" port while
    // anything at or above it leaves on the "ext" port. Instruction fetch does
    // NOT go through the mux -- it reaches the mem port through servile's
    // arbiter -- which is why executing from flash always worked while loading
    // from it did not: this wrapper used to tie i_wb_ext_ack to 1'b0, so a data
    // access to the flash XIP window at 0x4000_0000 asserted a strobe on a port
    // that could never acknowledge, and the hart stalled forever with no bus
    // request ever issued.
    //
    // In MOSAIC's map that dead region is not obscure: FLASH_MEM (XIP) is at
    // 0x4000_0000 and EXT_SLAVE at 0xF000_0000. On a part with sram_kb: 0 all
    // read-only data lives in flash, so this took out every string, table and
    // constant a worker might load.
    //
    // Both ports are therefore arbitrated onto the single OBI master below.
    logic [31:0] wb_mem_adr, wb_ext_adr;
    logic [31:0] wb_mem_dat_o, wb_ext_dat_o;  // write data from core
    logic [31:0] wb_rdt;                      // read data to core (shared)
    logic [3:0]  wb_mem_sel, wb_ext_sel;
    logic        wb_mem_we, wb_ext_we;
    logic        wb_mem_stb, wb_ext_stb;
    logic        wb_mem_ack, wb_ext_ack;

    // ── servile RF signals ────────────────────────────────────────
    localparam int unsigned RfWidth = W * 2;
    localparam int unsigned RfRegs  = 32 + (WITH_CSR ? 4 : 0);
    localparam int unsigned RfDepth = RfRegs * 32 / RfWidth;
    localparam int unsigned RfL2d   = $clog2(RfDepth);

    logic [RfL2d-1:0]   rf_waddr;
    logic [RfWidth-1:0] rf_wdata;
    logic               rf_wen;
    logic [RfL2d-1:0]   rf_raddr;
    logic               rf_ren;
    logic [RfWidth-1:0] rf_rdata;

    // ── Register file (serv_rf_ram) ──────────────────────────────
    serv_rf_ram #(
        .width  (RfWidth),
        .csr_regs(WITH_CSR ? 4 : 0)
    ) rf_ram_i (
        .i_clk   (clk_i),
        .i_waddr (rf_waddr),
        .i_wdata (rf_wdata),
        .i_wen   (rf_wen),
        .i_raddr (rf_raddr),
        .i_ren   (rf_ren),
        .o_rdata (rf_rdata)
    );

    // ── servile instantiation ────────────────────────────────────
    servile #(
        .width     (W),
        .reset_pc  (RESET_PC),
        .with_c    (COMPRESSED),
        .with_csr  (WITH_CSR),
        .with_mdu  (MDU),
        .pre_register(PRE_REGISTER)
    ) i_servile (
        .i_clk       (clk_i),
        // Active-high reset. Held asserted while the system is in reset OR the
        // core has not yet been woken (fetch_enable_i low) → dormant worker.
        .i_rst       (~rst_ni | ~fetch_enable_i),

        // SERV only has a timer IRQ (no external/software in standard mode)
        .i_timer_irq (irq_i[7]),

        // Wishbone Lite memory bus: instruction fetch (via servile's arbiter)
        // plus data accesses below 0x4000_0000.
        .o_wb_mem_adr(wb_mem_adr),
        .o_wb_mem_dat(wb_mem_dat_o),
        .o_wb_mem_sel(wb_mem_sel),
        .o_wb_mem_we (wb_mem_we),
        .o_wb_mem_stb(wb_mem_stb),
        .i_wb_mem_rdt(wb_rdt),
        .i_wb_mem_ack(wb_mem_ack),

        // Extension bus: data accesses at or above 0x4000_0000 -- the flash
        // XIP window and the external-slave window. Arbitrated onto the same
        // OBI master below; tying its ack off is bug 31.
        .o_wb_ext_adr(wb_ext_adr),
        .o_wb_ext_dat(wb_ext_dat_o),
        .o_wb_ext_sel(wb_ext_sel),
        .o_wb_ext_we (wb_ext_we),
        .o_wb_ext_stb(wb_ext_stb),
        .i_wb_ext_rdt(wb_rdt),
        .i_wb_ext_ack(wb_ext_ack),

        // Register file interface
        .o_rf_waddr  (rf_waddr),
        .o_rf_wdata  (rf_wdata),
        .o_rf_wen    (rf_wen),
        .o_rf_raddr  (rf_raddr),
        .i_rf_rdata  (rf_rdata),
        .o_rf_ren    (rf_ren)
    );

    // ── Wishbone Lite → OBI conversion (single outstanding) ─────────────
    // OBI separates acceptance (gnt) from response (rvalid). Against the real
    // system xbar gnt asserts when the request is accepted and rvalid the NEXT
    // cycle (or later under contention), so the previous "ack = gnt & rvalid"
    // almost never fired (the two rarely coincide) and SERV stalled. Track one
    // outstanding transaction — drop req once accepted (gnt) until its response
    // (rvalid) returns — and ack the Wishbone bus on rvalid (= data valid).
    // servile registers its read data on ack, so no read-data hold is needed.
    // Against a 0-latency memory (gnt+rvalid same cycle, tb/mosaic) this still
    // completes in one cycle.
    logic mem_outstanding_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                              mem_outstanding_q <= 1'b0;
      else if (mem_resp_i.rvalid)               mem_outstanding_q <= 1'b0;
      else if (mem_req_o.req && mem_resp_i.gnt) mem_outstanding_q <= 1'b1;
    end

    // Port arbitration. The mem and ext strobes CAN be asserted together (an
    // instruction fetch on mem while a data access sits on ext), so exactly one
    // is placed on OBI at a time and the response is routed back to whichever
    // owns the outstanding transaction.
    //
    // ext wins when both ask. Data accesses above 0x4000_0000 are comparatively
    // rare and always bounded, whereas the instruction stream requests
    // continuously -- giving mem priority would let a fetch-hungry core starve
    // its own load indefinitely, which is the failure this fix exists to remove.
    logic ext_owns_q;
    wire  pick_ext = wb_ext_stb;

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                              ext_owns_q <= 1'b0;
      else if (mem_req_o.req && mem_resp_i.gnt) ext_owns_q <= pick_ext;
    end

    // Gate with fetch_enable_i so a dormant worker stays bus-silent (SERV is
    // held in reset while parked, but mask the strobe too for defense in depth).
    assign mem_req_o.req       = (wb_mem_stb | wb_ext_stb) & fetch_enable_i
                                 & ~mem_outstanding_q;
    assign mem_req_o.addr      = pick_ext ? wb_ext_adr   : wb_mem_adr;
    assign mem_req_o.we        = pick_ext ? wb_ext_we    : wb_mem_we;
    assign mem_req_o.be        = pick_ext ? wb_ext_sel   : wb_mem_sel;
    assign mem_req_o.wdata     = pick_ext ? wb_ext_dat_o : wb_mem_dat_o;

    // Acknowledge only the port that issued the outstanding request, or a
    // fetch would consume a load's response.
    assign wb_mem_ack          = mem_resp_i.rvalid & ~ext_owns_q;
    assign wb_ext_ack          = mem_resp_i.rvalid &  ext_owns_q;
    assign wb_rdt              = mem_resp_i.rdata;

    // SERV has no native WFI/sleep output. Report "asleep" while the core is
    // held dormant (not yet woken) so the TDU's CORE_STATUS reflects which
    // workers are still parked — drives the wake-scheduling loop.
    assign core_sleep_o        = ~fetch_enable_i;

endmodule : serv_sci
