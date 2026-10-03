// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tl_obi_tb.sv — self-checking unit TB for xheep_tilelink_to_obi (the
// TileLink-C -> OBI bridge used by the Rocket/BOOM SCI wrappers).
//
// An OBI memory model with pseudorandom grant delays backs three regions:
//   0x0000_0000-0x0000_7FFF : 32 KB SRAM  (code window + sentinel region)
//   0x2000_0000-0x2000_003F : soc_ctrl stub registers
//   0x200A_0000-0x200A_003F : TDU stub registers
//
// Tests:
//   T1  PutFull 4B / Get 4B round-trip through the code window
//   T2  Get 8B (both lanes)
//   T3  AcquireBlock 64B: 8 GrantData beats + GrantAck
//   T4  ReleaseData 64B writeback, then Get verifies memory
//   T5  sentinel window: PutFull @0x0200_0004 lands at SRAM 0x3004
//   T6  TDU window: PutFull/Get @0x0C00_0000 hits the TDU stub
//   T7  unmapped address -> denied Get and denied PutFull
//   T8  PutPartial 8B with high-half-only mask
//   T9  Intent -> HintAck no-op
//   T10 Release (no data) -> ReleaseAck
//   T11 sub-word Get (1B at offset 3) lane check
//   T12 multi-beat PutFull 32B (4 beats)
//   T13 soc_ctrl window: PutFull/Get @0x0200_1000 hits the AO stub

module tl_obi_tb;

  localparam int unsigned TL_AW = 32, TL_SZW = 4, TL_SRCW = 4, TL_SINKW = 4;

  logic clk = 0;
  logic rst_n = 0;
  always #5 clk = ~clk;

  // ── TL wires ─────────────────────────────────────────────────────────
  logic a_valid, a_ready;
  logic [        2:0] a_opcode;
  logic [        2:0] a_param;
  logic [ TL_SZW-1:0] a_size;
  logic [TL_SRCW-1:0] a_source;
  logic [  TL_AW-1:0] a_address;
  logic [        7:0] a_mask;
  logic [       63:0] a_data;
  logic c_valid, c_ready;
  logic [        2:0] c_opcode;
  logic [        2:0] c_param;
  logic [ TL_SZW-1:0] c_size;
  logic [TL_SRCW-1:0] c_source;
  logic [  TL_AW-1:0] c_address;
  logic [       63:0] c_data;
  logic d_valid, d_ready;
  logic [         2:0] d_opcode;
  logic [         1:0] d_param;
  logic [  TL_SZW-1:0] d_size;
  logic [ TL_SRCW-1:0] d_source;
  logic [TL_SINKW-1:0] d_sink;
  logic                d_denied;
  logic [        63:0] d_data;
  logic                d_corrupt;
  logic e_valid, e_ready;
  logic [TL_SINKW-1:0] e_sink;

  obi_pkg::obi_req_t obi_req;
  obi_pkg::obi_resp_t obi_resp;

  xheep_tilelink_to_obi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .TL_AW     (TL_AW),
      .TL_SZW    (TL_SZW),
      .TL_SRCW   (TL_SRCW),
      .TL_SINKW  (TL_SINKW)
  ) dut (
      .clk_i         (clk),
      .rst_ni        (rst_n),
      .tl_a_valid_i  (a_valid),
      .tl_a_ready_o  (a_ready),
      .tl_a_opcode_i (a_opcode),
      .tl_a_param_i  (a_param),
      .tl_a_size_i   (a_size),
      .tl_a_source_i (a_source),
      .tl_a_address_i(a_address),
      .tl_a_mask_i   (a_mask),
      .tl_a_data_i   (a_data),
      .tl_a_corrupt_i(1'b0),
      .tl_b_valid_o  (),
      .tl_b_ready_i  (1'b1),
      .tl_b_opcode_o (),
      .tl_b_param_o  (),
      .tl_b_size_o   (),
      .tl_b_source_o (),
      .tl_b_address_o(),
      .tl_c_valid_i  (c_valid),
      .tl_c_ready_o  (c_ready),
      .tl_c_opcode_i (c_opcode),
      .tl_c_param_i  (c_param),
      .tl_c_size_i   (c_size),
      .tl_c_source_i (c_source),
      .tl_c_address_i(c_address),
      .tl_c_data_i   (c_data),
      .tl_c_corrupt_i(1'b0),
      .tl_d_valid_o  (d_valid),
      .tl_d_ready_i  (d_ready),
      .tl_d_opcode_o (d_opcode),
      .tl_d_param_o  (d_param),
      .tl_d_size_o   (d_size),
      .tl_d_source_o (d_source),
      .tl_d_sink_o   (d_sink),
      .tl_d_denied_o (d_denied),
      .tl_d_data_o   (d_data),
      .tl_d_corrupt_o(d_corrupt),
      .tl_e_valid_i  (e_valid),
      .tl_e_ready_o  (e_ready),
      .tl_e_sink_i   (e_sink),
      .obi_req_o     (obi_req),
      .obi_resp_i    (obi_resp)
  );

  // ── OBI memory model: pseudorandom gnt delay, rvalid 1 cycle later ───
  logic [31:0] sram               [0:8191];  // 32 KB @ 0x0000_0000
  logic [31:0] soc_stub           [  0:15];  // soc_ctrl @ 0x2000_0000
  logic [31:0] tdu_stub           [  0:15];  // TDU stub @ 0x200A_0000
  logic [ 7:0] lfsr = 8'hA5;
  int          gnt_wait = 0;
  logic        pending_rvalid = 0;
  logic [31:0] pending_rdata = '0;

  function automatic logic [31:0] mem_read(input logic [31:0] addr);
    if (addr[31:16] == 16'h2000) return soc_stub[addr[5:2]];
    if (addr[31:16] == 16'h200A) return tdu_stub[addr[5:2]];
    return sram[addr[14:2]];
  endfunction

  task automatic mem_write(input logic [31:0] addr, input logic [3:0] be, input logic [31:0] data);
    logic [31:0] cur;
    cur = mem_read(addr);
    for (int b = 0; b < 4; b++) if (be[b]) cur[8*b+:8] = data[8*b+:8];
    if (addr[31:16] == 16'h2000) soc_stub[addr[5:2]] = cur;
    else if (addr[31:16] == 16'h200A) tdu_stub[addr[5:2]] = cur;
    else sram[addr[14:2]] = cur;
  endtask

  always_ff @(posedge clk) begin
    obi_resp.gnt    <= 1'b0;
    obi_resp.rvalid <= 1'b0;
    if (pending_rvalid) begin
      obi_resp.rvalid <= 1'b1;
      obi_resp.rdata  <= pending_rdata;
      pending_rvalid  <= 1'b0;
    end
    if (obi_req.req && !obi_resp.gnt && !pending_rvalid) begin
      if (gnt_wait == 0) begin
        lfsr           <= {lfsr[6:0], lfsr[7] ^ lfsr[5] ^ lfsr[4] ^ lfsr[3]};
        gnt_wait       <= int'(lfsr[1:0]);  // 0-3 stall cycles next time
        obi_resp.gnt   <= 1'b1;
        pending_rvalid <= 1'b1;
        if (obi_req.we) begin
          mem_write(obi_req.addr, obi_req.be, obi_req.wdata);
          pending_rdata <= '0;
        end else begin
          pending_rdata <= mem_read(obi_req.addr);
        end
      end else begin
        gnt_wait <= gnt_wait - 1;
      end
    end
  end

  // ── TL driver tasks (single outstanding, blocking) ───────────────────
  int n_checks = 0, n_fails = 0;

  task automatic check(input string what, input logic cond);
    n_checks++;
    if (!cond) begin
      n_fails++;
      $display("FAIL: %s", what);
    end else $display("pass: %s", what);
  endtask

  task automatic a_beat(input logic [2:0] op, input logic [2:0] par, input logic [TL_SZW-1:0] sz,
                        input logic [31:0] addr, input logic [7:0] mask, input logic [63:0] data);
    a_opcode <= op;
    a_param <= par;
    a_size <= sz;
    a_source <= 4'h3;
    a_address <= addr;
    a_mask <= mask;
    a_data <= data;
    a_valid <= 1'b1;
    do @(posedge clk); while (!a_ready);
    a_valid <= 1'b0;
    @(posedge clk);  // bubble: avoid same-slot deassert/reassert races
  endtask

  task automatic c_beat(input logic [2:0] op, input logic [2:0] par, input logic [TL_SZW-1:0] sz,
                        input logic [31:0] addr, input logic [63:0] data);
    c_opcode <= op;
    c_param <= par;
    c_size <= sz;
    c_source <= 4'h3;
    c_address <= addr;
    c_data <= data;
    c_valid <= 1'b1;
    do @(posedge clk); while (!c_ready);
    c_valid <= 1'b0;
    @(posedge clk);  // bubble: avoid same-slot deassert/reassert races
  endtask

  // accept one D beat: d_ready is LOW outside this task, so the DUT holds
  // d_valid until we listen (no pulse can be lost to a driver bubble)
  task automatic d_beat(output logic [2:0] op, output logic [1:0] par, output logic den,
                        output logic cor, output logic [63:0] data);
    d_ready <= 1'b1;
    // handshake = valid && ready both effective at the same edge (d_ready is
    // read back so the first edge after raising it counts only once committed)
    do @(posedge clk); while (!(d_valid && d_ready));
    op   = d_opcode;
    par  = d_param;
    den  = d_denied;
    cor  = d_corrupt;
    data = d_data;
    d_ready <= 1'b0;
    @(posedge clk);  // bubble: avoid same-slot deassert/reassert races
  endtask

  task automatic send_e;
    e_sink  <= d_sink;
    e_valid <= 1'b1;
    @(posedge clk);
    e_valid <= 1'b0;
  endtask

  logic [2:0] rop;
  logic [1:0] rpar;
  logic rden, rcor;
  logic [63:0] rdat;

  initial begin : main
    a_valid  = 0;
    c_valid  = 0;
    e_valid  = 0;
    d_ready  = 0;
    e_sink   = '0;
    obi_resp = '0;
    for (int i = 0; i < 8192; i++) sram[i] = 32'h5EED_0000 | i;
    for (int i = 0; i < 16; i++) soc_stub[i] = '0;
    for (int i = 0; i < 16; i++) tdu_stub[i] = '0;

    repeat (4) @(posedge clk);
    rst_n = 1;
    repeat (2) @(posedge clk);

    // T1: PutFull 4B then Get 4B through the code window
    a_beat(3'd0, '0, 4'd2, 32'h8000_0010, 8'h0F, 64'h0000_0000_CAFE_F00D);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T1 put ack", rop == 3'd0 && !rden);
    a_beat(3'd4, '0, 4'd2, 32'h8000_0010, 8'h0F, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T1 get data", rop == 3'd1 && !rden && rdat[31:0] == 32'hCAFE_F00D);
    check("T1 sram body", sram[13'h004] == 32'hCAFE_F00D);

    // T2: Get 8B — both lanes
    sram[13'h008] = 32'h1111_2222;  // 0x20
    sram[13'h009] = 32'h3333_4444;  // 0x24
    a_beat(3'd4, '0, 4'd3, 32'h8000_0020, 8'hFF, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T2 get64", rop == 3'd1 && rdat == 64'h3333_4444_1111_2222);

    // T3: AcquireBlock 64B @0x8000_0040 -> 8 GrantData beats + GrantAck
    for (int i = 0; i < 16; i++) sram[16+i] = 32'hB10C_0000 | i;
    a_beat(3'd6, 3'd1  /*NtoT*/, 4'd6, 32'h8000_0040, 8'hFF, '0);
    begin
      logic ok;
      ok = 1'b1;
      for (int b = 0; b < 8; b++) begin
        d_beat(rop, rpar, rden, rcor, rdat);
        if (rop != 3'd5 || rden) ok = 1'b0;
        if (rdat != {32'hB10C_0000 | (2 * b + 1), 32'hB10C_0000 | (2 * b)}) ok = 1'b0;
      end
      check("T3 acquire 8xGrantData toT", ok && rpar == 2'd0);
      send_e();
    end

    // T4: ReleaseData 64B writeback @0x8000_0040, then verify
    for (int b = 0; b < 8; b++)
    c_beat(3'd7, 3'd3  /*TtoN*/, 4'd6, 32'h8000_0040, {
           32'hFACE_0000 | (2 * b + 1), 32'hFACE_0000 | (2 * b)});
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T4 release ack", rop == 3'd6);
    check("T4 writeback", sram[13'h010] == 32'hFACE_0000 && sram[13'h01F] == 32'hFACE_000F);

    // T5: sentinel window — PutFull @0x0200_0004 lands at SRAM 0x3004
    a_beat(3'd0, '0, 4'd2, 32'h0200_0004, 8'hF0, 64'hA71A_5000_0000_0000);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T5 sentinel ack", rop == 3'd0 && !rden);
    check("T5 sentinel value", sram[13'hC01] == 32'hA71A_5000);  // 0x3004>>2

    // T6: TDU window — PutFull + Get @0x0C00_0000 -> TDU stub reg 0
    a_beat(3'd0, '0, 4'd2, 32'h0C00_0000, 8'h0F, 64'h0000_0000_0000_0006);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T6 tdu write ack", rop == 3'd0 && !rden);
    check("T6 tdu stub", tdu_stub[0] == 32'h6);
    a_beat(3'd4, '0, 4'd2, 32'h0C00_0000, 8'h0F, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T6 tdu readback", rop == 3'd1 && rdat[31:0] == 32'h6);

    // T7: unmapped -> denied
    a_beat(3'd4, '0, 4'd2, 32'h4000_0000, 8'h0F, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T7 denied get", rop == 3'd1 && rden && rcor);
    a_beat(3'd0, '0, 4'd2, 32'h4000_0000, 8'h0F, 64'hDEAD);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T7 denied put", rop == 3'd0 && rden);

    // T8: PutPartial 8B, high-half mask only
    sram[13'h030] = 32'h0BAD_0BAD;  // 0xC0
    sram[13'h031] = 32'h0BAD_0BAD;
    a_beat(3'd1, '0, 4'd3, 32'h8000_00C0, 8'hF0, 64'h9999_8888_0000_0000);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T8 partial ack", rop == 3'd0 && !rden);
    check("T8 partial data", sram[13'h030] == 32'h0BAD_0BAD && sram[13'h031] == 32'h9999_8888);

    // T9: Intent -> HintAck
    a_beat(3'd5, '0, 4'd6, 32'h8000_0000, 8'hFF, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T9 hint ack", rop == 3'd2);

    // T10: Release (no data) -> ReleaseAck
    c_beat(3'd6, 3'd3, 4'd6, 32'h8000_0040, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T10 release ack", rop == 3'd6 && !rden);

    // T11: sub-word Get — 1 byte at offset 3 (lane low, byte 3)
    sram[13'h014] = 32'hAB00_0000;  // 0x50
    a_beat(3'd4, '0, 4'd0, 32'h8000_0053, 8'h08, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T11 byte get", rop == 3'd1 && rdat[31:24] == 8'hAB);

    // T12: multi-beat PutFull 32B (4 beats) @0x8000_0100
    a_beat(3'd0, '0, 4'd5, 32'h8000_0100, 8'hFF, 64'h0101_0101_0000_0000);
    a_beat(3'd0, '0, 4'd5, 32'h8000_0100, 8'hFF, 64'h0303_0303_0202_0202);
    a_beat(3'd0, '0, 4'd5, 32'h8000_0100, 8'hFF, 64'h0505_0505_0404_0404);
    a_beat(3'd0, '0, 4'd5, 32'h8000_0100, 8'hFF, 64'h0707_0707_0606_0606);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T12 put32 ack", rop == 3'd0 && !rden);
    check("T12 put32 data",
          sram[13'h040] == 32'h0000_0000 && sram[13'h041] == 32'h0101_0101
                         && sram[13'h046] == 32'h0606_0606 && sram[13'h047] == 32'h0707_0707);

    // T13: soc_ctrl window — PutFull/Get @0x0200_1000 -> AO stub reg 0
    a_beat(3'd0, '0, 4'd2, 32'h0200_1000, 8'h0F, 64'h0000_0000_0000_0001);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T13 soc_ctrl write ack", rop == 3'd0 && !rden);
    check("T13 soc_ctrl stub", soc_stub[0] == 32'h1);
    a_beat(3'd4, '0, 4'd2, 32'h0200_1000, 8'h0F, '0);
    d_beat(rop, rpar, rden, rcor, rdat);
    check("T13 soc_ctrl readback", rop == 3'd1 && rdat[31:0] == 32'h1);

    repeat (4) @(posedge clk);
    if (n_fails == 0) begin
      $display("ALL TESTS PASSED (%0d checks)", n_checks);
      $finish;
    end else begin
      $fatal(1, "%0d/%0d CHECKS FAILED", n_fails, n_checks);
    end
  end

  initial begin : watchdog
    #200000;
    $display("TIMEOUT — TB hung");
    $fatal(1);
  end

  // +dbg: cycle-by-cycle bridge state trace
  always @(posedge clk)
    if ($test$plusargs("dbg"))
      $display(
          "[%0t] st=%0d cv=%b cr=%b av=%b ar=%b dv=%b dr=%b beats=%0d sub=%b den=%b | req=%b we=%b addr=%08x gnt=%b rv=%b",
          $time,
          dut.state_q,
          c_valid,
          c_ready,
          a_valid,
          a_ready,
          d_valid,
          d_ready,
          dut.beats_q,
          dut.sub_q,
          dut.denied_q,
          obi_req.req,
          obi_req.we,
          obi_req.addr,
          obi_resp.gnt,
          obi_resp.rvalid
      );

endmodule : tl_obi_tb
