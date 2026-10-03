// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Self-checking testbench for the LOG (logarithmic interconnect) system_xbar.
// Drives the *generated* system_xbar for configs/mosaic_log.yaml (13 masters,
// 16 interleaved banks + 5 non-memory slaves) and checks:
//   T1: write/read sweep across the interleaved space (data integrity)
//   T2: two masters, different banks -> granted in the SAME cycle
//   T3: two masters, same bank -> serialized by RR arb, both complete
//   T4: peripheral (DEBUG) access completes while memory traffic streams
//   T5: unmapped address decodes to the ERROR default slave
// Every write is also an implicit WriteRespOn check (waits for rvalid).

module tb_log_xbar;
  import obi_pkg::*;
  import core_v_mini_mcu_pkg::*;

  localparam int unsigned NM = core_v_mini_mcu_pkg::SYSTEM_XBAR_NMASTER;
  localparam int unsigned NS = core_v_mini_mcu_pkg::SYSTEM_XBAR_NSLAVE;
  localparam int unsigned NB = core_v_mini_mcu_pkg::NUM_BANKS;
  localparam int unsigned NbLog2 = $clog2(NB);
  localparam int unsigned IdxW = cf_math_pkg::idx_width(NS);
  // Per-bank word addressing as memory_subsystem sees it
  localparam int unsigned BankWords = 32'(core_v_mini_mcu_pkg::RAM0_SIZE) / 4;
  localparam int unsigned BankAw = $clog2(BankWords);

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #5 clk = ~clk;

  int unsigned cycle = 0;
  always @(posedge clk) cycle <= cycle + 1;

  obi_req_t  [NM-1:0] m_req;
  obi_resp_t [NM-1:0] m_resp;
  obi_req_t  [NS-1:0] s_req;
  obi_resp_t [NS-1:0] s_resp;

  system_xbar #(
      .XBAR_NMASTER(NM),
      .XBAR_NSLAVE (NS)
  ) dut (
      .clk_i        (clk),
      .rst_ni       (rst_n),
      .addr_map_i   (core_v_mini_mcu_pkg::XBAR_ADDR_RULES),
      .default_idx_i(core_v_mini_mcu_pkg::ERROR_IDX[IdxW-1:0]),
      .master_req_i (m_req),
      .master_resp_o(m_resp),
      .slave_req_o  (s_req),
      .slave_resp_i (s_resp)
  );

  // ── Fixed-latency bank models (mimic memory_subsystem: gnt=1, rvalid 1
  //    cycle later). Word index = addr[NbLog2+2 +: BankAw], exactly the
  //    field the LOG fabric reconstructs from the LIC bank address. ──
  for (genvar b = 0; b < NB; b++) begin : gen_banks
    localparam int unsigned SlvIdx = 1 + b;  // RAM banks sit at indices 1..NB
    logic [31:0] mem[BankWords];
    logic rvalid_q = 1'b0;
    logic [31:0] rdata_q = '0;
    wire [BankAw-1:0] word = s_req[SlvIdx].addr[NbLog2+2+:BankAw];
    assign s_resp[SlvIdx].gnt = 1'b1;
    always @(posedge clk) begin
      rvalid_q <= s_req[SlvIdx].req;
      if (s_req[SlvIdx].req) begin
        rdata_q <= mem[word];
        if (s_req[SlvIdx].we) begin
          for (int unsigned k = 0; k < 4; k++)
          if (s_req[SlvIdx].be[k]) mem[word][8*k+:8] <= s_req[SlvIdx].wdata[8*k+:8];
        end
      end
    end
    assign s_resp[SlvIdx].rvalid = rvalid_q;
    assign s_resp[SlvIdx].rdata  = rdata_q;
  end

  // ── Variable-latency non-memory slaves (ERROR/DEBUG/AO/PERIPH/FLASH).
  //    gnt is stalled for a per-slave number of cycles; reads return
  //    PATTERN ^ addr so the TB can check routing + data. ──
  typedef struct packed {
    logic [31:0] idx;
    logic [31:0] pattern;
    int unsigned stall;
  } nonmem_cfg_t;

  localparam int unsigned NNonmem = 5;
  localparam nonmem_cfg_t NonmemCfg[NNonmem] = '{
      '{core_v_mini_mcu_pkg::ERROR_IDX, 32'hBADACCE5, 0},
      '{core_v_mini_mcu_pkg::DEBUG_IDX, 32'hDEB00000, 2},
      '{core_v_mini_mcu_pkg::AO_PERIPHERAL_IDX, 32'hA0000000, 1},
      '{core_v_mini_mcu_pkg::PERIPHERAL_IDX, 32'hCAFE0000, 3},
      '{core_v_mini_mcu_pkg::FLASH_MEM_IDX, 32'hF1A50000, 1}
  };

  logic error_slave_seen = 1'b0;

  for (genvar p = 0; p < NNonmem; p++) begin : gen_nonmem
    localparam int unsigned SlvIdx = 32'(NonmemCfg[p].idx);
    int unsigned stall_cnt = 0;
    logic rvalid_q = 1'b0;
    logic [31:0] rdata_q = '0;
    always @(posedge clk) begin
      rvalid_q <= s_req[SlvIdx].req && s_resp[SlvIdx].gnt;
      rdata_q  <= NonmemCfg[p].pattern ^ s_req[SlvIdx].addr;
      if (s_req[SlvIdx].req && !s_resp[SlvIdx].gnt) stall_cnt <= stall_cnt + 1;
      else stall_cnt <= 0;
    end
    assign s_resp[SlvIdx].gnt = s_req[SlvIdx].req && (stall_cnt >= NonmemCfg[p].stall);
    assign s_resp[SlvIdx].rvalid = rvalid_q;
    assign s_resp[SlvIdx].rdata = rdata_q;
  end

  always @(posedge clk) if (s_req[core_v_mini_mcu_pkg::ERROR_IDX].req) error_slave_seen <= 1'b1;

  // ── OBI master driver tasks (single outstanding, like the real cores) ──
  int unsigned gnt_cycle[NM];

  // Drive and sample ONLY at negedges (mid-window): gnt/rvalid are one-cycle
  // pulses, so sampling right after a posedge (post-NBA) would miss them, and
  // driving at a posedge races the DUT flops. All TB activity happens half a
  // cycle away from the capture edges.
  task automatic obi_req(input int unsigned m, input logic [31:0] addr, input logic we,
                         input logic [31:0] wdata, output logic [31:0] rdata);
    @(negedge clk);
    m_req[m].req   = 1'b1;
    m_req[m].addr  = addr;
    m_req[m].we    = we;
    m_req[m].be    = 4'hF;
    m_req[m].wdata = wdata;
    #1;
    while (!m_resp[m].gnt) @(negedge clk);
    gnt_cycle[m] = cycle;
    // The fabric captures the request on the next posedge; deassert req at
    // the negedge after it — mid-window, before the response can arrive.
    @(negedge clk);
    m_req[m].req = 1'b0;
    while (!m_resp[m].rvalid) @(negedge clk);
    rdata = m_resp[m].rdata;
  endtask

  task automatic obi_write(input int unsigned m, input logic [31:0] addr, input logic [31:0] data);
    logic [31:0] unused;
    obi_req(m, addr, 1'b1, data, unused);
  endtask

  task automatic obi_read(input int unsigned m, input logic [31:0] addr, output logic [31:0] data);
    obi_req(m, addr, 1'b0, 32'h0, data);
  endtask

  int errors = 0;

  task automatic check(input logic cond, input string msg);
    if (!cond) begin
      errors++;
      $display("### FAIL: %s (cycle %0d)", msg, cycle);
    end
  endtask

  // Watchdog
  initial begin
    #2_000_000;
    $display("### TIMEOUT");
    $fatal(1);
  end

  initial begin
    #1;
    $display("### params: WriteRespOn=%0d RespLat=%0d wen(lic)=? NumIn=%0d NumOut=%0d",
             dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.WriteRespOn,
             dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.RespLat,
             dut.tcdm_interconnect_i.NumIn, dut.tcdm_interconnect_i.NumOut);
  end

  // Debug trace (+trace) — sampled at negedge: mid-window, race-free
  int unsigned trace_cnt = 0;
  always @(negedge clk) begin
    if ($test$plusargs("trace") && trace_cnt < 60) begin
      trace_cnt <= trace_cnt + 1;
      $display(
          "[%0d] m0 req=%b addr=%h we=%b gnt=%b rvld=%b | tier m=%b/%b/%b p=%b/%b/%b | bank1 req=%b | err req=%b",
          cycle, m_req[0].req, m_req[0].addr, m_req[0].we, m_resp[0].gnt, m_resp[0].rvalid,
          dut.tier_req[0][1].req, dut.tier_resp[0][1].gnt, dut.tier_resp[0][1].rvalid,
          dut.tier_req[0][0].req, dut.tier_resp[0][0].gnt, dut.tier_resp[0][0].rvalid,
          s_req[1].req, s_req[core_v_mini_mcu_pkg::ERROR_IDX].req);
      $display(
          "      lic0: req=%b wen=%b gnt_o=%b vld_d=%b vld_q=%b vld_o=%b | tcdm req=%b gnt=%b vld=%b",
          dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.req_i,
          dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.wen_i,
          dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.gnt_o,
          dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.vld_d,
          dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.vld_q,
          dut.tcdm_interconnect_i.gen_lic.i_xbar.gen_inputs[0].i_addr_dec_resp_mux.vld_o,
          dut.tcdm_req[0], dut.tcdm_gnt[0], dut.tcdm_vld[0]);
    end
  end

  logic [31:0] rd, rd0, rd1;

  initial begin
    for (int m = 0; m < NM; m++) m_req[m] = '0;
    repeat (5) @(posedge clk);
    // Off the capture edge: deasserting in the same timestep as posedge
    // clk races the async-reset flops against the clock. See tb/gls/README.md.
    @(negedge clk);
    rst_n = 1'b1;
    repeat (5) @(posedge clk);

    // ── T1: write/read sweep across the interleaved space ──
    // 64 consecutive words: hits every bank 4 times (bank = word % NB).
    for (int unsigned w = 0; w < 64; w++) obi_write(0, 32'h0000_1000 + 4 * w, 32'hA5A5_0000 + w);
    for (int unsigned w = 0; w < 64; w++) begin
      obi_read(0, 32'h0000_1000 + 4 * w, rd);
      check(rd == 32'hA5A5_0000 + w, $sformatf("T1 sweep readback word %0d: got %08x", w, rd));
    end
    $display("### T1 interleaved sweep: %s", errors == 0 ? "PASS" : "FAIL");

    // ── T2: two masters, different banks, same cycle grant ──
    // word 0 -> bank 0, word 1 -> bank 1: no arbitration conflict.
    fork
      obi_write(1, 32'h0000_2000, 32'h11111111);  // word 0x800 -> bank 0
      obi_write(2, 32'h0000_2004, 32'h22222222);  // word 0x801 -> bank 1
    join
    check(gnt_cycle[1] == gnt_cycle[2], $sformatf(
          "T2 parallel banks granted same cycle (m1@%0d m2@%0d)", gnt_cycle[1], gnt_cycle[2]));
    obi_read(3, 32'h0000_2000, rd0);
    obi_read(3, 32'h0000_2004, rd1);
    check(rd0 == 32'h11111111 && rd1 == 32'h22222222, "T2 readback");
    $display("### T2 parallel-bank grant: %s", errors == 0 ? "PASS" : "FAIL");

    // ── T3: two masters, SAME bank -> serialized, both complete ──
    // words 0x900*NB.. same bank 0: addr = base + 4*NB*k keeps bank bits 0.
    fork
      obi_write(1, 32'h0000_3000, 32'h33333333);  // bank 0, word A
      obi_write(2, 32'h0000_3000 + 4 * NB, 32'h44444444);  // bank 0, word A+1
    join
    check(gnt_cycle[1] != gnt_cycle[2], "T3 same-bank accesses serialized");
    obi_read(3, 32'h0000_3000, rd0);
    obi_read(3, 32'h0000_3000 + 4 * NB, rd1);
    check(rd0 == 32'h33333333 && rd1 == 32'h44444444, "T3 readback");
    $display("### T3 same-bank RR: %s", errors == 0 ? "PASS" : "FAIL");

    // ── T4: peripheral access completes while memory traffic streams ──
    fork
      for (int unsigned w = 0; w < 32; w++) obi_write(0, 32'h0000_4000 + 4 * w, w);
      begin
        obi_read(4, core_v_mini_mcu_pkg::DEBUG_START_ADDRESS + 32'h10, rd);
        check(rd == (32'hDEB00000 ^ (core_v_mini_mcu_pkg::DEBUG_START_ADDRESS + 32'h10)), $sformatf(
              "T4 DEBUG read data: got %08x", rd));
        obi_read(4, core_v_mini_mcu_pkg::PERIPHERAL_START_ADDRESS + 32'h20, rd);
        check(rd == (32'hCAFE0000 ^ (core_v_mini_mcu_pkg::PERIPHERAL_START_ADDRESS + 32'h20)),
              $sformatf("T4 PERIPHERAL read data: got %08x", rd));
      end
    join
    obi_read(0, 32'h0000_4000 + 4 * 31, rd);
    check(rd == 31, "T4 memory stream readback");
    $display("### T4 mid-stream peripheral: %s", errors == 0 ? "PASS" : "FAIL");

    // ── T5: unmapped address -> ERROR default slave ──
    obi_read(5, 32'h5000_0000, rd);
    check(error_slave_seen, "T5 unmapped address reached ERROR slave");
    check(rd == (32'hBADACCE5 ^ 32'h5000_0000), $sformatf("T5 ERROR pattern: got %08x", rd));
    $display("### T5 unmapped->ERROR: %s", errors == 0 ? "PASS" : "FAIL");

    if (errors == 0) begin
      $display("### RESULT: ALL LOG-XBAR TESTS PASS");
      $finish;
    end else begin
      $display("### RESULT: %0d FAILURES", errors);
      $fatal(1);
    end
  end

endmodule
