// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// xheep_axi_burst_to_obi.sv — burst-capable AXI4 subordinate -> x-heep OBI
// master bridge (64-bit AXI data, 32-bit OBI).
//
// Companion to the single-beat xheep_axi_to_obi.sv, written for CVA6's cache
// subsystem: INCR bursts of any length (cacheline refills) and narrow
// single-beat accesses, on a 64-bit AXI data bus, are walked into sequential
// 32-bit OBI transactions (one or two OBI beats per AXI beat, selected by the
// nonzero strobe halves / the addressed lane). One AXI transaction in flight;
// AR/AW alternate priority. CVA6's axi_shim only ever emits BURST_INCR, and
// with RVA=0 no ATOPs — both are asserted below.
//
// OBI has no error channel: responses are always RESP_OKAY (same policy as
// the single-beat bridge).

module xheep_axi_burst_to_obi #(
    parameter type obi_req_t  = logic,  // x-heep obi_pkg::obi_req_t
    parameter type obi_resp_t = logic,  // x-heep obi_pkg::obi_resp_t
    parameter type axi_req_t  = logic,
    parameter type axi_resp_t = logic,
    parameter int unsigned AxiIdWidth = 4
) (
    input  logic      clk_i,
    input  logic      rst_ni,

    input  axi_req_t  axi_req_i,
    output axi_resp_t axi_resp_o,

    output obi_req_t  obi_req_o,
    input  obi_resp_t obi_resp_i
);

  typedef enum logic [2:0] {
    IDLE,
    RD_OBI,   // issue the OBI read(s) of the current AXI beat
    RD_SEND,  // drive R for the completed beat until r_ready
    WR_GETW,  // wait for / accept one W beat
    WR_OBI,   // issue the OBI write(s) of the current W beat
    SEND_B    // drive B until b_ready
  } state_e;

  state_e state_q, state_d;

  logic [31:0]           beat_addr_q;   // AXI address of the current beat
  logic [7:0]            len_q;         // beats remaining after this one
  logic [2:0]            size_q;
  logic [AxiIdWidth-1:0] id_q;
  logic [63:0]           wdata_q, rdata_q;
  logic [7:0]            wstrb_q;
  logic                  wlast_q;
  logic                  sub_q;         // current 32-bit lane (0 = low, 1 = high)
  logic                  granted_q;     // OBI request accepted, awaiting rvalid
  logic                  prefer_read_q; // alternate AR/AW priority

  // A 64-bit (size==3) beat spans both 32-bit lanes; narrower beats live in
  // the lane addressed by bit [2].
  logic two_lanes;
  assign two_lanes = (size_q == 3'd3);

  // Current OBI word address: 8-byte-aligned base plus the lane offset.
  logic [31:0] lane_addr;
  assign lane_addr = two_lanes ? ({beat_addr_q[31:3], 3'b000} | (sub_q ? 32'h4 : 32'h0))
                               : {beat_addr_q[31:2], 2'b00};
  logic lane_sel;  // which 64-bit lane this OBI access maps to
  assign lane_sel = lane_addr[2];

  // Write sub-beats: one OBI write per nonzero strobe half.
  logic [3:0] strb_lo, strb_hi;
  assign strb_lo = wstrb_q[3:0];
  assign strb_hi = wstrb_q[7:4];

  // The write lane currently being issued: low half first if nonzero.
  logic wr_lane;
  assign wr_lane = (sub_q == 1'b0) ? ~|strb_lo : 1'b1;

  always_comb begin
    state_d = state_q;

    axi_resp_o        = '0;
    axi_resp_o.b.id   = id_q;
    axi_resp_o.b.resp = axi_pkg::RESP_OKAY;
    axi_resp_o.r.id   = id_q;
    axi_resp_o.r.data = rdata_q;
    axi_resp_o.r.resp = axi_pkg::RESP_OKAY;
    axi_resp_o.r.last = (len_q == 8'd0);

    obi_req_o       = '0;
    obi_req_o.addr  = '0;
    obi_req_o.we    = 1'b0;
    obi_req_o.be    = 4'hF;
    obi_req_o.wdata = '0;

    unique case (state_q)
      IDLE: begin
        if (axi_req_i.aw_valid && (!axi_req_i.ar_valid || !prefer_read_q)) begin
          axi_resp_o.aw_ready = 1'b1;
          state_d = WR_GETW;
        end else if (axi_req_i.ar_valid) begin
          axi_resp_o.ar_ready = 1'b1;
          state_d = RD_OBI;
        end
      end

      // ── read path ────────────────────────────────────────────────
      RD_OBI: begin
        obi_req_o.req  = ~granted_q;
        obi_req_o.addr = lane_addr;
        if (obi_resp_i.rvalid) begin
          if (two_lanes && !sub_q) state_d = RD_OBI;   // second lane next
          else                     state_d = RD_SEND;
        end
      end

      RD_SEND: begin
        axi_resp_o.r_valid = 1'b1;
        if (axi_req_i.r_ready) state_d = (len_q == 8'd0) ? IDLE : RD_OBI;
      end

      // ── write path ───────────────────────────────────────────────
      WR_GETW: begin
        axi_resp_o.w_ready = 1'b1;
        if (axi_req_i.w_valid) begin
          // A beat with no active strobes needs no OBI access at all.
          if (axi_req_i.w.strb == '0)
            state_d = axi_req_i.w.last ? SEND_B : WR_GETW;
          else
            state_d = WR_OBI;
        end
      end

      WR_OBI: begin
        obi_req_o.req   = ~granted_q;
        obi_req_o.we    = 1'b1;
        obi_req_o.addr  = {beat_addr_q[31:3], 3'b000} | (wr_lane ? 32'h4 : 32'h0);
        obi_req_o.be    = wr_lane ? strb_hi : strb_lo;
        obi_req_o.wdata = wr_lane ? wdata_q[63:32] : wdata_q[31:0];
        if (obi_resp_i.rvalid) begin
          // More work if the high half is nonzero and we just did the low one.
          if (!wr_lane && |strb_hi) state_d = WR_OBI;
          else                      state_d = wlast_q ? SEND_B : WR_GETW;
        end
      end

      SEND_B: begin
        axi_resp_o.b_valid = 1'b1;
        if (axi_req_i.b_ready) state_d = IDLE;
      end

      default: state_d = IDLE;
    endcase
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q       <= IDLE;
      beat_addr_q   <= '0;
      len_q         <= '0;
      size_q        <= '0;
      id_q          <= '0;
      wdata_q       <= '0;
      wstrb_q       <= '0;
      wlast_q       <= 1'b0;
      rdata_q       <= '0;
      sub_q         <= 1'b0;
      granted_q     <= 1'b0;
      prefer_read_q <= 1'b0;
    end else begin
      state_q <= state_d;

      if (state_q == IDLE) begin
        if (axi_resp_o.aw_ready && axi_req_i.aw_valid) begin
          beat_addr_q   <= axi_req_i.aw.addr[31:0];
          len_q         <= axi_req_i.aw.len;
          size_q        <= axi_req_i.aw.size;
          id_q          <= axi_req_i.aw.id;
          sub_q         <= 1'b0;
          prefer_read_q <= 1'b1;
        end else if (axi_resp_o.ar_ready && axi_req_i.ar_valid) begin
          beat_addr_q   <= axi_req_i.ar.addr[31:0];
          len_q         <= axi_req_i.ar.len;
          size_q        <= axi_req_i.ar.size;
          id_q          <= axi_req_i.ar.id;
          sub_q         <= 1'b0;
          prefer_read_q <= 1'b0;
        end
      end

      if (state_q == WR_GETW && axi_req_i.w_valid) begin
        wdata_q <= axi_req_i.w.data;
        wstrb_q <= axi_req_i.w.strb;
        wlast_q <= axi_req_i.w.last;
        sub_q   <= 1'b0;
      end

      if (state_q == RD_OBI || state_q == WR_OBI) begin
        if (obi_req_o.req && obi_resp_i.gnt) granted_q <= 1'b1;
        if (obi_resp_i.rvalid) begin
          granted_q <= 1'b0;
          if (state_q == RD_OBI) begin
            // Deposit into the addressed 64-bit lane.
            if (lane_sel) rdata_q[63:32] <= obi_resp_i.rdata;
            else          rdata_q[31:0]  <= obi_resp_i.rdata;
            if (two_lanes && !sub_q) sub_q <= 1'b1;
          end else begin
            if (!wr_lane && |strb_hi) sub_q <= 1'b1;
          end
        end
      end

      // Advance to the next beat of the burst (INCR) once R is accepted /
      // the next W beat is awaited.
      if (state_q == RD_SEND && axi_req_i.r_ready && len_q != 8'd0) begin
        beat_addr_q <= beat_addr_q + (32'h1 << size_q);
        len_q       <= len_q - 8'd1;
        sub_q       <= 1'b0;
      end
      if (state_q == WR_OBI && obi_resp_i.rvalid
          && !(!wr_lane && |strb_hi) && !wlast_q) begin
        beat_addr_q <= beat_addr_q + (32'h1 << size_q);
        len_q       <= len_q - 8'd1;
      end
      // A fully-strobeless W beat consumes no OBI access but still advances
      // the burst position.
      if (state_q == WR_GETW && axi_req_i.w_valid && axi_req_i.w.strb == '0
          && !axi_req_i.w.last) begin
        beat_addr_q <= beat_addr_q + (32'h1 << size_q);
        len_q       <= len_q - 8'd1;
      end
    end
  end

`ifndef SYNTHESIS
  // pragma translate_off
  incr_only_ar :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (axi_req_i.ar_valid |-> (axi_req_i.ar.len == '0
                               || axi_req_i.ar.burst == axi_pkg::BURST_INCR)))
  else $fatal(1, "xheep_axi_burst_to_obi: only INCR read bursts are supported");
  incr_only_aw :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (axi_req_i.aw_valid |-> (axi_req_i.aw.len == '0
                               || axi_req_i.aw.burst == axi_pkg::BURST_INCR)))
  else $fatal(1, "xheep_axi_burst_to_obi: only INCR write bursts are supported");
  no_atop :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (axi_req_i.aw_valid |-> axi_req_i.aw.atop == '0))
  else $fatal(1, "xheep_axi_burst_to_obi: ATOPs are not supported (RVA must be 0)");
  max_size_ar :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (axi_req_i.ar_valid |-> axi_req_i.ar.size <= 3'd3))
  else $fatal(1, "xheep_axi_burst_to_obi: read size > 8 bytes unsupported");
  max_size_aw :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (axi_req_i.aw_valid |-> axi_req_i.aw.size <= 3'd3))
  else $fatal(1, "xheep_axi_burst_to_obi: write size > 8 bytes unsupported");
  // pragma translate_on
`endif

endmodule : xheep_axi_burst_to_obi
