// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// picorv32_sci.sv — Standard Core Interface wrapper for PicoRV32.
//
// Wraps YosysHQ PicoRV32 (hw/vendor/mosaic/picorv32/picorv32.v), converting
// its native unified memory port (mem_valid/mem_instr/mem_ready) to OBI v1.3.
//
// picorv32 native-port contract (from upstream README):
//   - mem_valid is held asserted until the cycle mem_ready is high;
//   - mem_addr/mem_wdata/mem_wstrb are stable while mem_valid;
//   - mem_rdata is sampled in the mem_ready cycle (LATCHED_MEM_RDATA=0), which
//     matches OBI's single-cycle rvalid+rdata → no read-data hold latch needed;
//   - mem_wstrb==0 encodes a read (fetch or load), nonzero a store.
//
// PicoRV32 has no hart_id port, no debug interface, and no fetch-enable: like
// SERV we emulate a dormant worker by holding the core in reset until
// fetch_enable_i asserts (driven by the per-hart TDU wake latch) and masking
// the bus request. Its custom (non-mip/mie) IRQ scheme is left disabled
// (ENABLE_IRQ=0) — workers in the wake demo are polled, not interrupted.

module picorv32_sci #(
    parameter bit ENABLE_COUNTERS   = 1'b0,
    parameter bit BARREL_SHIFTER    = 1'b0,
    parameter bit COMPRESSED_ISA    = 1'b0,
    parameter bit ENABLE_MUL        = 1'b0,
    parameter bit ENABLE_DIV        = 1'b0,
    parameter logic [31:0] PROGADDR_RESET = 32'h00000180
) (
    input logic clk_i,
    input logic rst_ni,

    // Core control
    input  logic [31:0] hart_id_i,    // unused — PicoRV32 has no mhartid
    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Interrupts
    input  logic [31:0] irq_i,        // unused — ENABLE_IRQ=0 (custom scheme)

    // Debug
    input  logic        debug_req_i,  // unused — PicoRV32 has no debug port

    // OBI unified master port (read-write, I+D on one channel)
    output obi_pkg::obi_req_t  mem_req_o,
    input  obi_pkg::obi_resp_t mem_resp_i
);

    // ── PicoRV32 native memory port ──────────────────────────────
    logic        mem_valid;
    logic        mem_instr;
    logic        mem_ready;
    logic [31:0] mem_addr;
    logic [31:0] mem_wdata;
    logic [3:0]  mem_wstrb;
    logic [31:0] mem_rdata;
    logic        trap;

    picorv32 #(
        .ENABLE_COUNTERS  (ENABLE_COUNTERS),
        .ENABLE_COUNTERS64(1'b0),
        .ENABLE_REGS_16_31(1'b1),          // full RV32I register file
        .BARREL_SHIFTER   (BARREL_SHIFTER),
        .COMPRESSED_ISA   (COMPRESSED_ISA),
        .ENABLE_MUL       (ENABLE_MUL),
        .ENABLE_DIV       (ENABLE_DIV),
        .ENABLE_IRQ       (1'b0),
        .ENABLE_TRACE     (1'b0),
        .REGS_INIT_ZERO   (1'b1),          // X-clean regfile for lint/4-state sim
        .PROGADDR_RESET   (PROGADDR_RESET),
        // STACKADDR left at its no-init default: the demo programs set sp.
        .STACKADDR        (32'hffff_ffff)
    ) i_picorv32 (
        .clk      (clk_i),
        // Active-low reset. Held asserted while the system is in reset OR the
        // core has not yet been woken (fetch_enable_i low) → dormant worker.
        .resetn   (rst_ni & fetch_enable_i),
        .trap     (trap),

        .mem_valid(mem_valid),
        .mem_instr(mem_instr),
        .mem_ready(mem_ready),
        .mem_addr (mem_addr),
        .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata),

        // Look-ahead interface: unused (native port only)
        .mem_la_read (),
        .mem_la_write(),
        .mem_la_addr (),
        .mem_la_wdata(),
        .mem_la_wstrb(),

        // PCPI coprocessor interface: disabled (ENABLE_PCPI=0 default)
        .pcpi_valid(),
        .pcpi_insn (),
        .pcpi_rs1  (),
        .pcpi_rs2  (),
        .pcpi_wr   (1'b0),
        .pcpi_rd   ('0),
        .pcpi_wait (1'b0),
        .pcpi_ready(1'b0),

        // Custom IRQ scheme: disabled
        .irq('0),
        .eoi()
    );

    // ── native port → OBI conversion (single outstanding) ───────────────
    // OBI separates acceptance (gnt) from response (rvalid): drop req once
    // accepted until the response returns (same tracker as serv_sci).
    // PicoRV32 holds mem_valid until mem_ready and samples mem_rdata in that
    // cycle, so mem_ready = rvalid and mem_rdata = resp.rdata pass through.
    logic mem_outstanding_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                              mem_outstanding_q <= 1'b0;
      else if (mem_resp_i.rvalid)               mem_outstanding_q <= 1'b0;
      else if (mem_req_o.req && mem_resp_i.gnt) mem_outstanding_q <= 1'b1;
    end

    // Gate with fetch_enable_i so a dormant worker stays bus-silent (the core
    // is held in reset while parked, but mask the request too for defense in
    // depth — same policy as serv_sci/fazyrv_sci).
    assign mem_req_o.req   = mem_valid & fetch_enable_i & ~mem_outstanding_q;
    assign mem_req_o.addr  = mem_addr;
    assign mem_req_o.we    = |mem_wstrb;
    assign mem_req_o.be    = |mem_wstrb ? mem_wstrb : 4'hF;
    assign mem_req_o.wdata = mem_wdata;

    assign mem_ready = mem_resp_i.rvalid;
    assign mem_rdata = mem_resp_i.rdata;

    // PicoRV32 has no native WFI/sleep output: report "asleep" while parked so
    // the TDU's CORE_STATUS reflects un-woken workers.
    assign core_sleep_o = ~fetch_enable_i;

`ifndef SYNTHESIS
    // A trapped PicoRV32 (illegal instruction / misaligned access) halts
    // silently — surface it in sim so a wedged worker is diagnosable.
    always_ff @(posedge clk_i) begin
      if (rst_ni && trap) $display("[picorv32_sci] hart %0d TRAP (pc wedged)", hart_id_i);
    end
`endif

endmodule : picorv32_sci
