// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// tdu_tb.sv — Self-checking testbench for the Task Dispatch Unit.
// Verifies: register read/write, task FIFO push/pop ordering and status,
// wake-pulse generation, CPI estimate array, active-hart-cycles counter, error paths.

`timescale 1ns/1ps

module tdu_tb;

  localparam int unsigned NUM_HARTS = 7;
  localparam time CLK_PERIOD = 10ns;

  logic clk;
  logic rst_n;

  reg_pkg::reg_req_t req;
  reg_pkg::reg_rsp_t rsp;

  logic [NUM_HARTS-1:0] core_running;
  logic [NUM_HARTS-1:0] core_sleep;
  logic [NUM_HARTS-1:0] core_wake;
  logic [NUM_HARTS-1:0] core_park;
  logic                 tdu_irq;

  int errors = 0;

  // ── DUT ─────────────────────────────────────────────────────────
  tdu #(
      .NUM_HARTS(NUM_HARTS),
      .RESET_SCHED_MODE(tdu_pkg::SCHED_DYNAMIC)
  ) dut (
      .clk_i          (clk),
      .rst_ni         (rst_n),
      .reg_req_i      (req),
      .reg_rsp_o      (rsp),
      .core_running_i (core_running),
      .core_sleep_i   (core_sleep),
      .core_wake_o    (core_wake),
      .core_park_o    (core_park),
      .tdu_irq_o      (tdu_irq)
  );

  // ── Clock ───────────────────────────────────────────────────────
  initial clk = 1'b0;
  always #(CLK_PERIOD/2) clk = ~clk;

  // ── Bus transaction tasks (drive on negedge to avoid races) ─────
  task automatic bus_write(input logic [31:0] addr, input logic [31:0] data);
    @(negedge clk);
    req.valid = 1'b1;
    req.write = 1'b1;
    req.addr  = addr;
    req.wdata = data;
    req.wstrb = 4'hF;
    do @(posedge clk); while (!rsp.ready);
    @(negedge clk);
    req.valid = 1'b0;
    req.write = 1'b0;
    req.wstrb = 4'h0;
  endtask

  task automatic bus_read(input logic [31:0] addr, output logic [31:0] data);
    @(negedge clk);
    req.valid = 1'b1;
    req.write = 1'b0;
    req.addr  = addr;
    req.wdata = 32'h0;
    req.wstrb = 4'hF;
    do @(posedge clk); while (!rsp.ready);
    data = rsp.rdata;
    @(negedge clk);
    req.valid = 1'b0;
    req.wstrb = 4'h0;
  endtask

  task automatic check(input logic [31:0] got, input logic [31:0] exp, input string msg);
    if (got !== exp) begin
      $display("[FAIL] %s: got 0x%08h, expected 0x%08h", msg, got, exp);
      errors++;
    end else begin
      $display("[PASS] %s: 0x%08h", msg, got);
    end
  endtask

  logic [31:0] rdata;

  // ── Stimulus ────────────────────────────────────────────────────
  initial begin
    req.valid    = 1'b0;
    req.write    = 1'b0;
    req.addr     = 32'h0;
    req.wdata    = 32'h0;
    req.wstrb    = 4'h0;
    core_running = '0;
    core_sleep   = '0;
    rst_n        = 1'b0;
    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    // ── 1. Configured reset SCHED_MODE is DYNAMIC (1) ──
    bus_read(32'h04, rdata);
    check(rdata, 32'h1, "configured reset SCHED_MODE");

    // ── 2. Write SCHED_MODE = DYNAMIC ──
    bus_write(32'h04, 32'h1);
    bus_read(32'h04, rdata);
    check(rdata, 32'h1, "SCHED_MODE=DYNAMIC");

    // ── 3. Write WAKE_MASK = cores 1..6 ──
    bus_write(32'h08, 32'h7E);
    bus_read(32'h08, rdata);
    check(rdata, 32'h7E, "WAKE_MASK");

    // ── 4. Task FIFO empty at reset: [4]=empty -> 0x10 ──
    bus_read(32'h18, rdata);
    check(rdata, 32'h10, "TASK_STATUS empty");

    // ── 5. Push 3 tasks (distinctive values; FIFO is transparent) ──
    bus_write(32'h10, 32'hAAAA0001);
    bus_write(32'h10, 32'hBBBB0002);
    bus_write(32'h10, 32'hCCCC0003);

    bus_read(32'h18, rdata);  // count=3, not empty/full -> 0x03
    check(rdata, 32'h03, "TASK_STATUS count=3");

    // ── 6. Pop tasks (FIFO order: A, B, C) ──
    bus_read(32'h14, rdata);
    check(rdata, 32'hAAAA0001, "POP task A");
    bus_read(32'h14, rdata);
    check(rdata, 32'hBBBB0002, "POP task B");
    bus_read(32'h14, rdata);
    check(rdata, 32'hCCCC0003, "POP task C");

    bus_read(32'h18, rdata);
    check(rdata, 32'h10, "TASK_STATUS empty again");

    // ── 7. Targeted auto-wake on push: ONLY the hinted core wakes ──
    // (bug 20 regression: broadcast wake launched the whole worker pool
    // on the first push and the losers popped an empty FIFO)
    // Descriptor core_hint is bits [15:11]. bus_write's single-accept
    // handshake pushes exactly once; core_wake is a 1-cycle pulse
    // registered on the accept edge, so it is still asserted when
    // bus_write returns (at the following negedge).
    core_sleep = 7'b1111110;           // ALL workers sleeping, mask=0x7E
    bus_write(32'h10, 32'hDDDD0804);   // push, core_hint=1
    check({{(32-NUM_HARTS){1'b0}}, core_wake}, 32'h02, "auto-wake only hinted core 1");
    bus_write(32'h10, 32'hEEEE1805);   // push, core_hint=3
    check({{(32-NUM_HARTS){1'b0}}, core_wake}, 32'h08, "auto-wake only hinted core 3");
    bus_write(32'h10, 32'hFFFF4006);   // push, core_hint=8 (out of range)
    check({{(32-NUM_HARTS){1'b0}}, core_wake}, 32'h00, "out-of-range hint wakes nobody");
    core_sleep = 7'b0000010;           // only core 1 sleeping
    bus_write(32'h10, 32'h11112807);   // push, core_hint=5 (core 5 awake)
    check({{(32-NUM_HARTS){1'b0}}, core_wake}, 32'h00, "hinted-but-awake core not pulsed");
    core_sleep = '0;
    // drain the 4 pushed tasks (FIFO order preserved)
    bus_read(32'h14, rdata);
    check(rdata, 32'hDDDD0804, "POP drains hint=1 task");
    bus_read(32'h14, rdata);
    bus_read(32'h14, rdata);
    bus_read(32'h14, rdata);
    bus_read(32'h18, rdata);
    check(rdata, 32'h10, "TASK_STATUS empty after drain");

    // ── 8. WAKE_REQ manual wake (write 1<<5) ──
    @(negedge clk);
    req.valid = 1'b1; req.write = 1'b1; req.addr = 32'h0C; req.wdata = 32'h20; req.wstrb = 4'hF;
    @(posedge clk);
    @(posedge clk);
    if (core_wake[5] !== 1'b1) begin
      $display("[FAIL] manual WAKE_REQ core 5: got %b", core_wake);
      errors++;
    end else $display("[PASS] manual WAKE_REQ core 5");
    @(negedge clk);
    req.valid = 1'b0; req.write = 1'b0; req.wstrb = 4'h0;

    // ── 9. PARK_REQ produces a targeted one-cycle park pulse ──
    @(negedge clk);
    req.valid = 1'b1; req.write = 1'b1; req.addr = 32'h60; req.wdata = 32'h08; req.wstrb = 4'hF;
    @(posedge clk);
    @(posedge clk);
    if (core_park !== 7'b0001000) begin
      $display("[FAIL] PARK_REQ core 3: got %b", core_park);
      errors++;
    end else $display("[PASS] PARK_REQ core 3");
    @(negedge clk);
    req.valid = 1'b0; req.write = 1'b0; req.wstrb = 4'h0;

    // ── 10. CORE_STATUS: fixed 16-bit running/sleeping fields ──
    core_running = 7'b0000001;
    core_sleep   = 7'b0000010;
    @(negedge clk);
    bus_read(32'h00, rdata);
    check(rdata, 32'h0002_0001, "CORE_STATUS");
    core_running = '0;
    core_sleep   = '0;

    // ── 11. CPI estimate array ──
    bus_write(32'h20 + 4*2, 32'h0000_0007);  // core 2 CPI = 7
    bus_read(32'h20 + 4*2, rdata);
    check(rdata, 32'h0000_0007, "CPI_EST[2]");
    bus_read(32'h20 + 4*0, rdata);
    check(rdata, 32'h0, "CPI_EST[0] default");

    // ── 12. Active-hart-cycles counter increments with running cores ──
    bus_write(32'h1C, 32'h0);  // clear
    bus_read(32'h1C, rdata);
    check(rdata, 32'h0, "ACTIVE_HART_CYCLES cleared");
    core_running = 7'b0000011;  // 2 cores running
    repeat (5) @(posedge clk);
    @(negedge clk);
    core_running = '0;
    bus_read(32'h1C, rdata);
    if (rdata == 32'h0) begin
      $display("[FAIL] ACTIVE_HART_CYCLES did not increment: 0x%08h", rdata);
      errors++;
    end else $display("[PASS] ACTIVE_HART_CYCLES incremented to 0x%08h", rdata);

    // ── 13. Read-only path: write to CORE_STATUS is ignored ──
    bus_write(32'h00, 32'hDEAD);
    bus_read(32'h00, rdata);
    check(rdata, 32'h0, "CORE_STATUS RO (write ignored)");

    // ── Summary ───────────────────────────────────────────────────
    repeat (4) @(posedge clk);
    if (errors == 0)
      $display("\n=== TDU TB: ALL TESTS PASSED ===");
    else
      $display("\n=== TDU TB: %0d FAILURES ===", errors);
    if (errors == 0) $finish;
    else $fatal(1, "TDU TB failed with %0d errors", errors);
  end

  // Watchdog
  initial begin
    #(20000ns);
    $display("[FAIL] Watchdog timeout");
    $fatal(1, "TDU TB watchdog timeout");
  end

endmodule
