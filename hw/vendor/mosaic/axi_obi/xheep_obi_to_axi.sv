// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// xheep_obi_to_axi.sv — x-heep OBI master -> AXI4 manager bridge.
//
// Ported from pulp-platform axi_obi's obi_to_axi.sv (Solderpad 0.51,
// refs/IP_Interconnect_Catalog/axi_obi) with the pulp obi_pkg machinery
// removed: x-heep defines its own obi_pkg (obi_req_t {req,we,be,addr,wdata} /
// obi_resp_t {gnt,rvalid,rdata}) that collides with pulp's, so — like
// hw/vendor/mosaic/idma/idma_xheep_wrapper.sv — this module takes the x-heep
// structs as type parameters and never imports pulp obi_pkg. Simplifications
// vs the reference:
//   - no atomics (x-heep OBI has no atop signals; aw.atop is tied to '0)
//   - no data-width conversion (AXI DataWidth == OBI DataWidth == 32)
//   - single outstanding transaction (every MOSAIC OBI source behind this
//     bridge is single-outstanding through the varlat demux chain)
//
// AXI mapping: read -> AR (len=0, size=2); write -> AW + W (decoupled
// acceptance, single beat, wstrb=be); gnt when the request channel(s)
// complete; rvalid on R (rdata=r.data) or B.

module xheep_obi_to_axi #(
    parameter type obi_req_t  = logic,  // x-heep obi_pkg::obi_req_t
    parameter type obi_resp_t = logic,  // x-heep obi_pkg::obi_resp_t
    parameter type axi_req_t  = logic,
    parameter type axi_resp_t = logic
) (
    input  logic      clk_i,
    input  logic      rst_ni,

    input  obi_req_t  obi_req_i,
    output obi_resp_t obi_resp_o,

    output axi_req_t  axi_req_o,
    input  axi_resp_t axi_resp_i
);

  // One in-flight transaction: set on OBI grant, cleared on the response.
  logic busy_q, we_q;
  // Decoupled AW/W acceptance bookkeeping (reference: obi_to_axi.sv:222-261).
  logic aw_sent_q, w_sent_q;
  logic aw_sent_d, w_sent_d;
  logic gnt, resp_done;

  always_comb begin
    axi_req_o = '0;

    // Request channel payloads (single beat, 32-bit)
    axi_req_o.aw.addr  = obi_req_i.addr;
    axi_req_o.aw.len   = '0;
    axi_req_o.aw.size  = 3'd2;
    axi_req_o.aw.burst = axi_pkg::BURST_INCR;
    axi_req_o.aw.cache = 4'b0010;  // modifiable, non-cacheable
    axi_req_o.aw.prot  = 3'b100;

    axi_req_o.w.data = obi_req_i.wdata;
    axi_req_o.w.strb = obi_req_i.be;
    axi_req_o.w.last = 1'b1;

    axi_req_o.ar.addr  = obi_req_i.addr;
    axi_req_o.ar.len   = '0;
    axi_req_o.ar.size  = 3'd2;
    axi_req_o.ar.burst = axi_pkg::BURST_INCR;
    axi_req_o.ar.cache = 4'b0010;
    axi_req_o.ar.prot  = 3'b100;

    // Request-channel handshake control
    gnt       = 1'b0;
    aw_sent_d = aw_sent_q;
    w_sent_d  = w_sent_q;

    if (obi_req_i.req && !busy_q) begin
      if (!obi_req_i.we) begin
        axi_req_o.ar_valid = 1'b1;
        gnt = axi_resp_i.ar_ready;
      end else begin
        unique case ({aw_sent_q, w_sent_q})
          2'b00: begin
            axi_req_o.aw_valid = 1'b1;
            axi_req_o.w_valid  = 1'b1;
            unique case ({axi_resp_i.aw_ready, axi_resp_i.w_ready})
              2'b01:   w_sent_d = 1'b1;
              2'b10:   aw_sent_d = 1'b1;
              2'b11:   gnt = 1'b1;
              default: ;
            endcase
          end
          2'b10: begin  // AW accepted earlier, W still pending
            axi_req_o.w_valid = 1'b1;
            if (axi_resp_i.w_ready) begin
              aw_sent_d = 1'b0;
              gnt       = 1'b1;
            end
          end
          2'b01: begin  // W accepted earlier, AW still pending
            axi_req_o.aw_valid = 1'b1;
            if (axi_resp_i.aw_ready) begin
              w_sent_d = 1'b0;
              gnt      = 1'b1;
            end
          end
          default: begin
            aw_sent_d = 1'b0;
            w_sent_d  = 1'b0;
          end
        endcase
      end
    end

    // Response channel: accept exactly the channel of the in-flight txn
    axi_req_o.b_ready = busy_q & we_q;
    axi_req_o.r_ready = busy_q & ~we_q;
  end

  assign resp_done = (axi_resp_i.b_valid & axi_req_o.b_ready) |
                     (axi_resp_i.r_valid & axi_req_o.r_ready & axi_resp_i.r.last);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q    <= 1'b0;
      we_q      <= 1'b0;
      aw_sent_q <= 1'b0;
      w_sent_q  <= 1'b0;
    end else begin
      aw_sent_q <= aw_sent_d;
      w_sent_q  <= w_sent_d;
      if (gnt) begin
        busy_q <= 1'b1;
        we_q   <= obi_req_i.we;
      end else if (resp_done) begin
        busy_q <= 1'b0;
      end
    end
  end

  assign obi_resp_o.gnt    = gnt;
  assign obi_resp_o.rvalid = resp_done;
  assign obi_resp_o.rdata  = axi_resp_i.r.data;

endmodule : xheep_obi_to_axi
