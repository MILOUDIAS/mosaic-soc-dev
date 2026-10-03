// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// fazyrv_sci.sv — Standard Core Interface wrapper for FazyRV.
//
// Wraps a FazyRV core, converting its dual Wishbone Classic master ports
// (separate I-fetch and D-access) to OBI v1.3.
//
// FazyRV fazyrv_top port map (from refs/IP_Cores_Catalog/FazyRV/rtl/fazyrv_top.sv):
//   clk_i, rst_in (active-high), tirq_i (timer IRQ), trap_o
//   wb_imem_stb_o, wb_imem_cyc_o, wb_imem_adr_o, wb_imem_dat_i, wb_imem_ack_i
//   wb_dmem_cyc_o, wb_dmem_stb_o, wb_dmem_we_o, wb_dmem_ack_i,
//   wb_dmem_be_o, wb_dmem_dat_i, wb_dmem_adr_o, wb_dmem_dat_o
//
// FazyRV has no hart_id port (mhartid is not implemented); no fetch_enable
// (starts at BOOTADR parameter); no debug interface.

module fazyrv_sci #(
    parameter int unsigned CHUNKSIZE = 8,   // 1, 2, 4, or 8
    parameter CONF_STR     = "CSR",         // MIN, INT, or CSR
    parameter RFTYPE_STR   = "BRAM_DP_BP",  // LOGIC, BRAM, BRAM_BP, BRAM_DP, BRAM_DP_BP
    parameter RVC_STR      = "NONE",        // NONE, COMB, REG, HYBR
    parameter bit MEMDLY1  = 1'b0,
    parameter logic [31:0] BOOTADR = 32'h00000180
) (
    input logic clk_i,
    input logic rst_ni,

    // Core control
    input  logic [31:0] hart_id_i,   // unused — FazyRV has no mhartid
    input  logic [31:0] boot_addr_i, // unused — BOOTADR parameter set at elab
    // fetch_enable_i: FazyRV has no native fetch-enable, so a dormant worker is
    // emulated by holding the core in reset until this is asserted (driven by
    // the per-hart wake latch in cpu_subsystem → TDU.core_wake_o).
    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Interrupts
    input  logic [31:0] irq_i,

    // Debug
    input  logic        debug_req_i, // unused — FazyRV has no debug port

    // OBI Instruction master port (read-only)
    output obi_pkg::obi_req_t  instr_req_o,
    input  obi_pkg::obi_resp_t instr_resp_i,

    // OBI Data master port (read-write)
    output obi_pkg::obi_req_t  data_req_o,
    input  obi_pkg::obi_resp_t data_resp_i
);

    // ── FazyRV core signals ──────────────────────────────────────
    logic        wb_imem_stb;
    logic        wb_imem_cyc;
    logic [31:0] wb_imem_adr;
    logic [31:0] wb_imem_dat;
    logic        wb_imem_ack;

    logic        wb_dmem_cyc;
    logic        wb_dmem_stb;
    logic        wb_dmem_we;
    logic        wb_dmem_ack;
    logic [3:0]  wb_dmem_be;
    logic [31:0] wb_dmem_dat_i;
    logic [31:0] wb_dmem_adr;
    logic [31:0] wb_dmem_dat_o;

    logic        trap_o;

    // ── Timing contract: MEMDLY1 must be 0 ───────────────────────────────
    // This wrapper presents FazyRV with exactly ONE memory model: a
    // COMBINATIONAL (0-latency) Wishbone Classic slave, synthesised by freezing
    // the core's clock until the transfer it issued has returned (below). There
    // is no second contract to select between, so MEMDLY1 must be 0.
    //
    // With MEMDLY1=1 the core's control FSM leaves IFETCH unconditionally after
    // one cycle and ignores the ack completely:
    //     fazyrv_cntrl.sv:  if (MEMDLY1 | imem_ack_i) state_n = DECODE;
    // That is only correct against a memory which ALWAYS answers in exactly one
    // cycle. A shared OBI crossbar cannot promise that — a peripheral, the boot
    // ROM, or a contended SRAM bank takes longer — so the core would run on
    // without its data. It also directly contradicts the clock-stall above,
    // which exists precisely to make latency invisible.
    //
    // Measured: MEMDLY1=1 breaks a FazyRV worker that passes with MEMDLY1=0.
    // Fail at elaboration rather than emit hardware that cannot be correct.
    if (MEMDLY1 != 1'b0) begin : gen_memdly1_unsupported
        $error("fazyrv_sci: MEMDLY1=1 is unsupported. This wrapper presents a ",
               "0-latency Wishbone view by clock-stalling, so FazyRV must be ",
               "built with MEMDLY1=0. With MEMDLY1=1 the core ignores the ack ",
               "and assumes a fixed 1-cycle memory, which the OBI crossbar ",
               "cannot guarantee.");
    end

    // ── Clock-stall adapter: registered bus → combinational view ─────────
    // FazyRV is built for COMBINATIONAL (0-latency) memory — it reads/shifts the
    // fetched word during its fetch cycle. The MOSAIC system xbar/SRAM is
    // REGISTERED (gnt one cycle, rvalid the next), so a bare connection makes
    // FazyRV read 0 during the latency cycle, decode garbage, and take an
    // illegal-instruction trap to mtvec(=0). Fix: freeze FazyRV's clock while a
    // fetch it issued has not yet returned (req asserted, no rvalid). On the cycle
    // FazyRV resumes it samples the now-valid ack+data, exactly as a 0-latency
    // memory would have presented them. This adapts to ANY latency (0, 1, or more
    // under bus contention): for a 0-latency memory (gnt+rvalid same cycle, as in
    // the tb/mosaic cocotb model) stb & ~rvalid is never true, so it never stalls
    // and is fully transparent. The negedge-latched gate is glitch-free and breaks
    // the comb path (FazyRV's stb → stall → its own clock).
    //
    // INVARIANT (why a bare `ack = rvalid` is sufficient): the two ports are
    // never outstanding at the same time. FazyRV issues instruction fetches in
    // its IFETCH state and data transfers only in ACK
    // (fazyrv_cntrl.sv: `assign dmem_stb_o = (state_r == ACK);`), which are
    // mutually exclusive FSM states. Both wait terms therefore stall the shared
    // clock for one port at a time, and a one-cycle rvalid is always observed by
    // the core on its next edge. Do NOT "harden" this with a sticky per-port
    // response latch: holding ack beyond the cycle the core is released makes
    // FazyRV count a second, phantom Wishbone transfer and breaks a working
    // worker. (Tried; it regressed the all-hart liveness gate.)
    logic fz_imem_wait, fz_dmem_wait, fz_stall, fz_stall_q;
    logic fazyrv_clk;
    assign fz_imem_wait = wb_imem_stb & wb_imem_cyc & ~instr_resp_i.rvalid;
    assign fz_dmem_wait = wb_dmem_stb & wb_dmem_cyc & ~data_resp_i.rvalid;
    assign fz_stall     = fz_imem_wait | fz_dmem_wait;
    always_ff @(negedge clk_i) begin
        if (!rst_ni) fz_stall_q <= 1'b0;
        else         fz_stall_q <= fz_stall;
    end
    assign fazyrv_clk = clk_i & ~fz_stall_q;

    // ── FazyRV core instantiation ────────────────────────────────
    fazyrv_top #(
        .CHUNKSIZE(CHUNKSIZE),
        .CONF(CONF_STR),
        .RFTYPE(RFTYPE_STR),
        .RVC(RVC_STR),
        .MEMDLY1(MEMDLY1),
        .BOOTADR(BOOTADR)
    ) i_core (
        .clk_i  (fazyrv_clk),   // clock-stalled so the registered bus looks combinational
        // FazyRV rst_in is ACTIVE-LOW ("Reset, low active" — fazyrv_pc.sv uses
        // `if (~rst_in)`), matching x-heep's active-low rst_ni. Gate it with
        // fetch_enable_i: while a worker is dormant (not yet woken) rst_in is
        // held low → the PC stays pinned at BOOTADR and the regfile never writes;
        // once woken (fetch_enable_i high) reset releases and the core fetches.
        .rst_in (rst_ni & fetch_enable_i),

        // FazyRV uses a single timer-irq input (bit [7] of the RISC-V
        // mip/mie layout). No external/software IRQ in the MIN/CSR config.
        .tirq_i (irq_i[7]),
        .trap_o (trap_o),

        // Instruction Wishbone master
        .wb_imem_stb_o(wb_imem_stb),
        .wb_imem_cyc_o(wb_imem_cyc),
        .wb_imem_adr_o(wb_imem_adr),
        .wb_imem_dat_i(wb_imem_dat),
        .wb_imem_ack_i(wb_imem_ack),

        // Data Wishbone master
        .wb_dmem_cyc_o(wb_dmem_cyc),
        .wb_dmem_stb_o(wb_dmem_stb),
        .wb_dmem_we_o (wb_dmem_we),
        .wb_dmem_ack_i(wb_dmem_ack),
        .wb_dmem_be_o (wb_dmem_be),
        .wb_dmem_dat_i(wb_dmem_dat_i),
        .wb_dmem_adr_o(wb_dmem_adr),
        .wb_dmem_dat_o(wb_dmem_dat_o)
    );

    // ── Instruction Wishbone Classic → OBI (single outstanding) ──────────
    // OBI splits acceptance (gnt) from response (rvalid): against the real
    // system xbar gnt asserts the cycle the request is accepted and rvalid the
    // NEXT cycle. FazyRV holds its Wishbone strobe until ack, so a purely
    // combinational req = stb&cyc would re-launch the SAME fetch every cycle —
    // the duplicate accepted on the rvalid cycle produces a second, lingering
    // response that desynchronises FazyRV's fetch/PC (observed: it fetched its
    // boot word then ran off to PC 0). Track one outstanding transaction: drop
    // req once accepted (gnt) until its response (rvalid) returns, and ack on
    // rvalid (= Wishbone "data valid"). Against a 0-latency memory (gnt+rvalid
    // same cycle, as in tb/mosaic) this still completes in one cycle.
    logic instr_outstanding_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                                  instr_outstanding_q <= 1'b0;
      else if (instr_resp_i.rvalid)                 instr_outstanding_q <= 1'b0;
      else if (instr_req_o.req && instr_resp_i.gnt) instr_outstanding_q <= 1'b1;
    end

    // Gate the request with fetch_enable_i: while the worker is dormant (not yet
    // woken) FazyRV still toggles its imem fetch strobe out of reset, so mask it
    // here to keep a parked core electrically quiet on the bus.
    assign instr_req_o.req      = wb_imem_stb & wb_imem_cyc & fetch_enable_i & ~instr_outstanding_q;
    assign instr_req_o.addr     = wb_imem_adr;
    assign instr_req_o.we       = 1'b0;
    assign instr_req_o.be       = 4'b1111;
    assign instr_req_o.wdata    = '0;

    // HOLD the read data after rvalid. FazyRV (RVC=NONE) reads the fetched word
    // COMBINATIONALLY and shifts it in chunk-by-chunk over the cycles FOLLOWING
    // the ack, so the word must stay stable until the next fetch — but OBI drives
    // rdata only during the 1-cycle rvalid. Without this latch FazyRV reads the
    // instruction on the rvalid cycle and zeros afterwards, so its serial PC fills
    // with 0 and the core runs off to address 0 (serv works unlatched because
    // servile registers its read data on ack; FazyRV does not).
    logic [31:0] instr_rdata_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                  instr_rdata_q <= 32'h0;
      else if (instr_resp_i.rvalid) instr_rdata_q <= instr_resp_i.rdata;
    end
    assign wb_imem_ack          = instr_resp_i.rvalid;
    assign wb_imem_dat          = instr_resp_i.rvalid ? instr_resp_i.rdata : instr_rdata_q;

    // ── Data Wishbone → OBI (single outstanding, same rationale) ─────────
    logic data_outstanding_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                                data_outstanding_q <= 1'b0;
      else if (data_resp_i.rvalid)                data_outstanding_q <= 1'b0;
      else if (data_req_o.req && data_resp_i.gnt) data_outstanding_q <= 1'b1;
    end

    assign data_req_o.req       = wb_dmem_stb & wb_dmem_cyc & fetch_enable_i & ~data_outstanding_q;
    assign data_req_o.addr      = wb_dmem_adr;
    assign data_req_o.we        = wb_dmem_we;
    assign data_req_o.be        = wb_dmem_be;
    assign data_req_o.wdata     = wb_dmem_dat_o;

    // Hold load data after rvalid for the same reason (FazyRV shifts loaded data
    // in over the cycles following the ack).
    logic [31:0] data_rdata_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                 data_rdata_q <= 32'h0;
      else if (data_resp_i.rvalid) data_rdata_q <= data_resp_i.rdata;
    end
    assign wb_dmem_ack          = data_resp_i.rvalid;
    assign wb_dmem_dat_i        = data_resp_i.rvalid ? data_resp_i.rdata : data_rdata_q;

    // FazyRV does not have a WFI/sleep output; trap indicates an exception.
    // Report "asleep" while held dormant (not yet woken) so the TDU CORE_STATUS
    // reflects which workers are still parked.
    assign core_sleep_o         = ~fetch_enable_i;

endmodule : fazyrv_sci
