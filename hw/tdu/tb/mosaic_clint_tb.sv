// Copyright MOSAIC-SoC Contributors
// SPDX-License-Identifier: SHL-0.51

`timescale 1ns/1ps

module mosaic_clint_tb;
  localparam int unsigned NUM_HARTS = 4;
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  reg_pkg::reg_req_t req;
  reg_pkg::reg_rsp_t rsp;
  logic [NUM_HARTS-1:0] software_irq;
  logic [NUM_HARTS-1:0] timer_irq;
  logic [63:0] mtime;
  int errors = 0;

  always #5ns clk = ~clk;

  mosaic_clint #(.NUM_HARTS(NUM_HARTS)) dut (
      .clk_i(clk),
      .rst_ni(rst_n),
      .reg_req_i(req),
      .reg_rsp_o(rsp),
      .software_irq_o(software_irq),
      .timer_irq_o(timer_irq),
      .mtime_o(mtime)
  );

  task automatic write32(input logic [31:0] addr, input logic [31:0] data);
    @(negedge clk);
    req = '{addr: addr, write: 1'b1, wdata: data, wstrb: 4'hf, valid: 1'b1};
    @(posedge clk);
    if (!rsp.ready || rsp.error) begin
      $display("[FAIL] write 0x%08x", addr);
      errors++;
    end
    @(negedge clk);
    req = '0;
  endtask

  task automatic read32(input logic [31:0] addr, output logic [31:0] data);
    @(negedge clk);
    req = '{addr: addr, write: 1'b0, wdata: '0, wstrb: '0, valid: 1'b1};
    @(posedge clk);
    data = rsp.rdata;
    if (!rsp.ready || rsp.error) begin
      $display("[FAIL] read 0x%08x", addr);
      errors++;
    end
    @(negedge clk);
    req = '0;
  endtask

  task automatic check(input logic condition, input string message);
    if (!condition) begin
      $display("[FAIL] %s", message);
      errors++;
    end else $display("[PASS] %s", message);
  endtask

  logic [31:0] value;
  initial begin
    req = '0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);
    check(mtime != 0, "architectural mtime output advances");

    write32(32'h0008, 32'h1);  // MSIP hart 2
    check(software_irq == 4'b0100, "per-hart software interrupt set");
    read32(32'h0008, value);
    check(value == 1, "MSIP readback");
    write32(32'h0008, 32'h0);
    check(software_irq == 0, "per-hart software interrupt clear");

    // Program hart 1 comparator to mtime+8 using a stable low/high sequence.
    read32(32'hBFF8, value);
    write32(32'h4008, value + 8);  // hart1 cmp low
    write32(32'h400C, 32'h0);      // hart1 cmp high
    repeat (12) @(posedge clk);
    check(timer_irq[1] == 1'b1, "per-hart timer interrupt fires");
    check((timer_irq & 4'b1101) == 0, "other hart timers remain clear");

    write32(32'h400C, 32'hffff_ffff);
    check(timer_irq[1] == 1'b0, "timer interrupt clears after comparator update");

    // Unknown and partial writes are hard errors.
    @(negedge clk);
    req = '{addr: 32'h100, write: 1'b1, wdata: 32'h1, wstrb: 4'h1, valid: 1'b1};
    @(posedge clk);
    check(rsp.ready && rsp.error, "invalid access reports error");

    if (errors == 0) begin
      $display("=== MOSAIC CLINT TB: PASS ===");
      $finish;
    end else begin
      $fatal(1, "MOSAIC CLINT TB failed with %0d errors", errors);
    end
  end

  initial begin
    #20us;
    $fatal(1, "MOSAIC CLINT TB watchdog timeout");
  end
endmodule
