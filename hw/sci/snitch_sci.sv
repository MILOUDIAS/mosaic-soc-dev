// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// snitch_sci.sv — Standard Core Interface wrapper for the bare Snitch core.
//
// Wraps the mempool-flavor Snitch (hw/vendor/mosaic/snitch), converting its
// two native ports to split OBI v1.3:
//   - instruction refill port (inst_addr/valid → data/ready, TCDM-style: data
//     is consumed in the ready cycle) → OBI instr channel;
//   - TCDM data port (reqrsp q/p channels) → OBI data channel. Per the core's
//     port contract, WRITE transactions return nothing on the P channel — the
//     wrapper swallows OBI's write rvalid and only forwards read responses.
//
// Snitch has no debug port and (in this mempool flavor) no mip-style IRQ
// inputs — only wake_up_sync_i, which MOSAIC leaves tied (dormancy is
// emulated the SCI way: reset-hold until fetch_enable_i + request masking).
// RVM defaults OFF: mul/div would offload to the accelerator port, which is
// tied off here (workers run rv32i; wire snitch_shared_muldiv before enabling).

module snitch_sci #(
    parameter logic [31:0] BOOT_ADDR = 32'h00000180,
    parameter bit RVE = 1'b0,
    parameter bit RVM = 1'b0
) (
    input logic clk_i,
    input logic rst_ni,

    // Core control
    input  logic [31:0] hart_id_i,
    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Interrupts
    input  logic [31:0] irq_i,        // unused — no mip-style inputs on this core

    // Debug
    input  logic        debug_req_i,  // unused — Snitch has no debug port

    // OBI split master ports
    output obi_pkg::obi_req_t  instr_req_o,
    input  obi_pkg::obi_resp_t instr_resp_i,
    output obi_pkg::obi_req_t  data_req_o,
    input  obi_pkg::obi_resp_t data_resp_i
);

    import snitch_pkg::meta_id_t;

    // ── Snitch native signals ────────────────────────────────────
    logic [31:0] inst_addr;
    logic [31:0] inst_data;
    logic        inst_valid;
    logic        inst_ready;

    logic [31:0] data_qaddr;
    logic        data_qwrite;
    logic [3:0]  data_qamo;
    logic [31:0] data_qdata;
    logic [3:0]  data_qstrb;
    meta_id_t    data_qid;
    logic        data_qvalid;
    logic        data_qready;
    logic [31:0] data_pdata;
    logic        data_perror;
    meta_id_t    data_pid;
    logic        data_pvalid;
    logic        data_pready;

    snitch #(
        .BootAddr(BOOT_ADDR),
        .MTVEC   (BOOT_ADDR),
        .RVE     (RVE),
        .RVM     (RVM)
    ) i_snitch (
        .clk_i (clk_i),
        // Held in reset while the system resets OR the worker is parked
        // (fetch_enable_i low) → dormant until the TDU wake latch fires.
        .rst_ni(rst_ni & fetch_enable_i),

        .hart_id_i(hart_id_i),

        .inst_addr_o (inst_addr),
        .inst_data_i (inst_data),
        .inst_valid_o(inst_valid),
        .inst_ready_i(inst_ready),

        // Accelerator port: tied off (RVM=0 → never used)
        .acc_qaddr_o     (),
        .acc_qid_o       (),
        .acc_qdata_op_o  (),
        .acc_qdata_arga_o(),
        .acc_qdata_argb_o(),
        .acc_qdata_argc_o(),
        .acc_qvalid_o    (),
        .acc_qready_i    (1'b0),
        .acc_pdata_i     ('0),
        .acc_pid_i       ('0),
        .acc_perror_i    (1'b0),
        .acc_pvalid_i    (1'b0),
        .acc_pready_o    (),

        .data_qaddr_o (data_qaddr),
        .data_qwrite_o(data_qwrite),
        .data_qamo_o  (data_qamo),
        .data_qdata_o (data_qdata),
        .data_qstrb_o (data_qstrb),
        .data_qid_o   (data_qid),
        .data_qvalid_o(data_qvalid),
        .data_qready_i(data_qready),
        .data_pdata_i (data_pdata),
        .data_perror_i(data_perror),
        .data_pid_i   (data_pid),
        .data_pvalid_i(data_pvalid),
        .data_pready_o(data_pready),

        .wake_up_sync_i(1'b0),

        .fpu_rnd_mode_o(),
        .fpu_status_i  (fpnew_pkg::status_t'('0)),
        .core_events_o ()
    );

    // ── Instruction refill port → OBI instr channel ─────────────────────
    // Single outstanding. Snitch may abandon/redirect a fetch (branch) while
    // our OBI read is in flight — guard the handshake with an address match
    // so a stale response is never served to a different fetch address.
    logic        instr_outstanding_q;
    logic [31:0] instr_addr_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        instr_outstanding_q <= 1'b0;
        instr_addr_q        <= '0;
      end else if (instr_resp_i.rvalid) begin
        instr_outstanding_q <= 1'b0;
      end else if (instr_req_o.req && instr_resp_i.gnt) begin
        instr_outstanding_q <= 1'b1;
        instr_addr_q        <= inst_addr;
      end
    end

    assign instr_req_o.req   = inst_valid & fetch_enable_i & ~instr_outstanding_q;
    assign instr_req_o.addr  = inst_addr;
    assign instr_req_o.we    = 1'b0;
    assign instr_req_o.be    = 4'hF;
    assign instr_req_o.wdata = '0;

    assign inst_ready = instr_resp_i.rvalid & inst_valid & (inst_addr == instr_addr_q);
    assign inst_data  = instr_resp_i.rdata;

    // ── TCDM data port (reqrsp q/p) → OBI data channel ───────────────────
    // Strictly single outstanding, in-order (satisfies the core's ordering
    // requirement). Writes complete at OBI rvalid with NO p-channel response;
    // reads latch rdata at rvalid (OBI rvalid is single-cycle) and present it
    // on the p channel until the core takes it (pready).
    typedef enum logic [1:0] { D_IDLE, D_WAIT, D_RESP } dstate_e;
    dstate_e     dstate_q;
    logic        dwrite_q;
    meta_id_t    did_q;
    logic [31:0] drdata_q;

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        dstate_q <= D_IDLE;
        dwrite_q <= 1'b0;
        did_q    <= '0;
        drdata_q <= '0;
      end else begin
        unique case (dstate_q)
          D_IDLE: begin
            if (data_req_o.req && data_resp_i.gnt) begin
              dwrite_q <= data_qwrite;
              did_q    <= data_qid;
              dstate_q <= D_WAIT;
            end
          end
          D_WAIT: begin
            if (data_resp_i.rvalid) begin
              if (dwrite_q) begin
                dstate_q <= D_IDLE;   // writes: no p-channel response
              end else begin
                drdata_q <= data_resp_i.rdata;
                dstate_q <= D_RESP;
              end
            end
          end
          D_RESP: begin
            if (data_pready) dstate_q <= D_IDLE;
          end
          default: dstate_q <= D_IDLE;
        endcase
      end
    end

    assign data_req_o.req   = data_qvalid & fetch_enable_i & (dstate_q == D_IDLE);
    assign data_req_o.addr  = data_qaddr;
    assign data_req_o.we    = data_qwrite;
    assign data_req_o.be    = data_qwrite ? data_qstrb : 4'hF;
    assign data_req_o.wdata = data_qdata;

    assign data_qready = data_req_o.req & data_resp_i.gnt;

    assign data_pvalid = (dstate_q == D_RESP);
    assign data_pdata  = drdata_q;
    assign data_pid    = did_q;
    assign data_perror = 1'b0;

    // Snitch has no native sleep output: report "asleep" while parked so the
    // TDU's CORE_STATUS reflects un-woken workers.
    assign core_sleep_o = ~fetch_enable_i;

`ifndef SYNTHESIS
    // The demo firmware issues no AMOs; the OBI fabric has no AMO support.
    always_ff @(posedge clk_i) begin
      if (rst_ni && data_qvalid && (data_qamo != 4'h0))
        $display("[snitch_sci] hart %0d ERROR: unsupported AMO 0x%0h at 0x%08x",
                 hart_id_i, data_qamo, data_qaddr);
    end
`endif

endmodule : snitch_sci
