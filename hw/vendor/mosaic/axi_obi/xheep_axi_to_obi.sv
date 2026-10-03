// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// xheep_axi_to_obi.sv — AXI4 subordinate -> x-heep OBI master bridge.
//
// Ported from pulp-platform axi_obi's axi_to_obi.sv (Solderpad 0.51,
// refs/IP_Interconnect_Catalog/axi_obi) specialized to NumBanks=1 (AXI
// DataWidth == OBI DataWidth == 32) and with the pulp obi_pkg machinery
// removed (x-heep obi structs as type parameters — see xheep_obi_to_axi.sv).
//
// Only single-beat bursts are supported (len == 0): in MOSAIC every AXI
// manager reaching this bridge is one of our own xheep_obi_to_axi instances
// (via the FlooNoC fabric), which never issues bursts. One transaction in
// flight; simultaneous AR and AW pend fairly (alternating priority).

module xheep_axi_to_obi #(
    parameter type obi_req_t  = logic,  // x-heep obi_pkg::obi_req_t
    parameter type obi_resp_t = logic,  // x-heep obi_pkg::obi_resp_t
    parameter type axi_req_t  = logic,
    parameter type axi_resp_t = logic,
    parameter int unsigned AxiIdWidth = 2
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
    WAIT_W,     // AW accepted, waiting for the W beat
    OBI_WRITE,  // OBI write request until gnt, then wait rvalid
    OBI_READ,   // OBI read request until gnt, then wait rvalid
    SEND_B,     // drive B until b_ready
    SEND_R      // drive R until r_ready
  } state_e;

  state_e state_q, state_d;

  logic [31:0] addr_q, wdata_q, rdata_q;
  logic [3:0] be_q;
  logic [AxiIdWidth-1:0] id_q;
  logic granted_q;     // OBI request accepted, waiting for rvalid
  logic prefer_read_q; // alternate AR/AW priority when both are pending

  always_comb begin
    state_d = state_q;

    axi_resp_o          = '0;
    axi_resp_o.b.id     = id_q;
    axi_resp_o.b.resp   = axi_pkg::RESP_OKAY;
    axi_resp_o.r.id     = id_q;
    axi_resp_o.r.data   = rdata_q;
    axi_resp_o.r.resp   = axi_pkg::RESP_OKAY;
    axi_resp_o.r.last   = 1'b1;

    obi_req_o       = '0;
    obi_req_o.addr  = addr_q;
    obi_req_o.we    = (state_q == OBI_WRITE);
    obi_req_o.be    = (state_q == OBI_WRITE) ? be_q : 4'hF;
    obi_req_o.wdata = wdata_q;

    unique case (state_q)
      IDLE: begin
        // Accept one request; alternate priority so neither channel starves.
        if (axi_req_i.aw_valid && (!axi_req_i.ar_valid || !prefer_read_q)) begin
          axi_resp_o.aw_ready = 1'b1;
          state_d = WAIT_W;
        end else if (axi_req_i.ar_valid) begin
          axi_resp_o.ar_ready = 1'b1;
          state_d = OBI_READ;
        end
      end

      WAIT_W: begin
        axi_resp_o.w_ready = 1'b1;
        if (axi_req_i.w_valid) state_d = OBI_WRITE;
      end

      OBI_WRITE, OBI_READ: begin
        obi_req_o.req = ~granted_q;
        if (obi_resp_i.rvalid) state_d = (state_q == OBI_WRITE) ? SEND_B : SEND_R;
      end

      SEND_B: begin
        axi_resp_o.b_valid = 1'b1;
        if (axi_req_i.b_ready) state_d = IDLE;
      end

      SEND_R: begin
        axi_resp_o.r_valid = 1'b1;
        if (axi_req_i.r_ready) state_d = IDLE;
      end

      default: state_d = IDLE;
    endcase
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q       <= IDLE;
      addr_q        <= '0;
      wdata_q       <= '0;
      rdata_q       <= '0;
      be_q          <= '0;
      id_q          <= '0;
      granted_q     <= 1'b0;
      prefer_read_q <= 1'b0;
    end else begin
      state_q <= state_d;
      if (state_q == IDLE) begin
        if (axi_resp_o.aw_ready && axi_req_i.aw_valid) begin
          addr_q        <= axi_req_i.aw.addr;
          id_q          <= axi_req_i.aw.id;
          prefer_read_q <= 1'b1;
        end else if (axi_resp_o.ar_ready && axi_req_i.ar_valid) begin
          addr_q        <= axi_req_i.ar.addr;
          id_q          <= axi_req_i.ar.id;
          prefer_read_q <= 1'b0;
        end
      end
      if (state_q == WAIT_W && axi_req_i.w_valid) begin
        wdata_q <= axi_req_i.w.data;
        be_q    <= axi_req_i.w.strb;
      end
      if ((state_q == OBI_WRITE || state_q == OBI_READ)) begin
        if (obi_req_o.req && obi_resp_i.gnt) granted_q <= 1'b1;
        if (obi_resp_i.rvalid) begin
          granted_q <= 1'b0;
          rdata_q   <= obi_resp_i.rdata;
        end
      end
    end
  end

`ifndef SYNTHESIS
  // pragma translate_off
  single_beat_ar :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (axi_req_i.ar_valid |-> axi_req_i.ar.len == '0))
  else $fatal(1, "xheep_axi_to_obi supports only single-beat reads (ar.len == 0)");
  single_beat_aw :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (axi_req_i.aw_valid |-> axi_req_i.aw.len == '0))
  else $fatal(1, "xheep_axi_to_obi supports only single-beat writes (aw.len == 0)");
  // pragma translate_on
`endif

endmodule : xheep_axi_to_obi
