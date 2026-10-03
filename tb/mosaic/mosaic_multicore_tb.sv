// mosaic_multicore_tb.sv — self-checking Verilator testbench for the generated
// MOSAIC multi-core cpu_subsystem (serial-core config from configs/mosaic_sim.yaml:
// SERV + QERV + FazyRV). It wraps the real generated DUT, gives each hart its own
// instruction + data OBI memory preloaded with a tiny program, releases reset, and
// checks two things per hart:
//   (1) LIVENESS  — the core comes out of reset and issues bus requests through
//                   its SCI wrapper (fetch path works);
//   (2) EXECUTION — the core runs the program and writes the sentinel 0x55 to
//                   address 0x40 (fetch + decode + ALU + store all work).
//
// SERV/QERV are unified-bus (cpu_subsystem ties their instr port off and routes
// everything onto the data port), so their data memory carries both fetch and
// store. FazyRV has split I/D ports. Either way the sentinel lands in the data
// memory, so the check is uniform.

`timescale 1ns / 1ps

module mosaic_multicore_tb
  import obi_pkg::*;
;
  localparam int unsigned NH = 3;  // must match configs/mosaic_sim.yaml
  localparam logic [31:0] SENTINEL = 32'h0000_0055;
  localparam int unsigned SENT_WIDX = 'h10;  // byte 0x40 → word index 0x10

  logic               clk;
  logic               rst_n;

  // DESCENDING [NH-1:0] ranges to match cpu_subsystem's per-hart array ports
  // ([NUM_HARTS-1:0]); ascending [NH] would reverse the element mapping.
  logic      [  31:0] hart_id                                               [NH-1:0];
  obi_req_t           instr_req                                             [NH-1:0];
  obi_resp_t          instr_rsp                                             [NH-1:0];
  obi_req_t           data_req                                              [NH-1:0];
  obi_resp_t          data_rsp                                              [NH-1:0];
  logic      [  31:0] irq                                                   [NH-1:0];
  logic      [NH-1:0] dbg_req;  // packed: matches cpu_subsystem debug_req_i
  logic      [NH-1:0] wake;  // packed per-hart wake (stands in for the TDU)
  logic      [NH-1:0] sleep;  // packed: matches cpu_subsystem core_sleep_o

  // 100 MHz clock
  initial clk = 1'b0;
  always #5 clk = ~clk;

  initial begin
    for (int i = 0; i < NH; i++) begin
      hart_id[i] = i[31:0];
      irq[i]     = '0;
      dbg_req[i] = 1'b0;
      wake[i]    = 1'b0;  // all workers start dormant; woken in the stimulus block
    end
  end

  // ── DUT: the generated multi-core CPU subsystem ──────────────────
  cpu_subsystem #(
      .NUM_HARTS(NH),
      .BOOT_ADDR('h180)
  ) dut (
      .clk_i            (clk),
      .rst_ni           (rst_n),
      .hart_id_i        (hart_id),
      .core_instr_req_o (instr_req),
      .core_instr_resp_i(instr_rsp),
      .core_data_req_o  (data_req),
      .core_data_resp_i (data_rsp),
      .irq_i            (irq),
      .debug_req_i      (dbg_req),
      .core_wake_i      (wake),
      .core_sleep_o     (sleep)
  );

  // ── Per-hart memories + liveness counters ────────────────────────
  int unsigned instr_cnt[NH];
  int unsigned data_cnt [NH];

  for (genvar h = 0; h < NH; h++) begin : gen_harts
    tb_obi_mem imem (
        .clk_i (clk),
        .rst_ni(rst_n),
        .req_i (instr_req[h]),
        .resp_o(instr_rsp[h])
    );
    tb_obi_mem dmem (
        .clk_i (clk),
        .rst_ni(rst_n),
        .req_i (data_req[h]),
        .resp_o(data_rsp[h])
    );
    always_ff @(posedge clk) begin
      if (!rst_n) begin
        instr_cnt[h] <= 0;
        data_cnt[h]  <= 0;
      end else begin
        if (instr_req[h].req) instr_cnt[h] <= instr_cnt[h] + 1;
        if (data_req[h].req) data_cnt[h] <= data_cnt[h] + 1;
      end
    end
  end

  // Constant-index hierarchical taps into each data memory's sentinel word.
  logic [31:0] sval[NH];
  assign sval[0] = gen_harts[0].dmem.mem[SENT_WIDX];
  assign sval[1] = gen_harts[1].dmem.mem[SENT_WIDX];
  assign sval[2] = gen_harts[2].dmem.mem[SENT_WIDX];

  // ── Stimulus + self-check ────────────────────────────────────────
  int unsigned errors;
  int unsigned n_alive;
  int unsigned n_exec;
  string       names   [NH];
  initial begin
    errors = 0;
    n_alive = 0;
    n_exec = 0;
    names[0] = "serv  (W=1)";
    names[1] = "qerv  (W=4)";
    names[2] = "fazyrv     ";
    rst_n = 1'b0;
    repeat (20) @(posedge clk);
    // Off the capture edge: deasserting in the same timestep as posedge
    // clk races the async-reset flops against the clock. See tb/gls/README.md.
    @(negedge clk);
    rst_n = 1'b1;

    // Workers boot dormant (no TITAN in this config). Pulse wake for every hart
    // — the TDU's core_wake_o path — to release them. cpu_subsystem latches it,
    // so a short pulse suffices.
    repeat (5) @(posedge clk);
    for (int i = 0; i < NH; i++) wake[i] = 1'b1;
    @(posedge clk);
    for (int i = 0; i < NH; i++) wake[i] = 1'b0;

    // Bit-serial SERV needs ~hundreds of cycles per instruction; give the
    // slowest core ample time to reach the store.
    repeat (300000) @(posedge clk);

    $display("\n=== MOSAIC multi-core harness (NH=%0d: serv, qerv, fazyrv) ===", NH);
    for (int i = 0; i < NH; i++) begin
      automatic bit alive = (instr_cnt[i] > 0) || (data_cnt[i] > 0);
      automatic bit executed = (sval[i] == SENTINEL);
      if (alive) n_alive++;
      if (executed) n_exec++;
      $display("hart %0d %s : instr_req=%0d data_req=%0d  alive=%0b  sentinel=0x%08x  executed=%0b",
               i, names[i], instr_cnt[i], data_cnt[i], alive, sval[i], executed);
      if (!alive) begin
        errors++;  // a dead core is a hard integration failure
        $display("   [FAIL] no bus activity — core did not come out of reset");
      end else if (executed) begin
        $display("   [PASS] alive and retired the test program");
      end else begin
        $display(
            "   [WARN] alive (fetching) but did not retire the program — see tb/mosaic/README.md");
      end
    end

    $display("\n--- summary ---");
    $display("integration : %0d/%0d cores alive (issued bus requests)", n_alive, NH);
    $display("execution   : %0d/%0d cores retired the test program", n_exec, NH);
    if (errors == 0 && n_exec == NH) begin
      $display("=== MOSAIC multi-core TB: PASS — all cores alive + executed ===\n");
      $finish;
    end else if (errors == 0) begin
      $fatal(1,
             "MOSAIC multi-core TB: %0d core(s) alive but did not execute the program",
             NH - n_exec);
    end else begin
      $fatal(1, "MOSAIC multi-core TB: %0d dead core(s)", errors);
    end
  end

  // Watchdog
  initial begin
    repeat (2_000_000) @(posedge clk);
    $fatal(1, "watchdog timeout — a core hung");
  end
endmodule
