// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tb_bridge_top.sv — stage-1 loopback for the MOSAIC OBI<->AXI bridges:
//
//   cocotb OBI master -> xheep_obi_to_axi -> AXI -> xheep_axi_to_obi -> OBI mem
//
// Proves the two bridges compose (R/W, byte enables, back-to-back,
// read-after-write) before the FlooNoC fabric is inserted between them
// (stage 2, tb_noc_top.sv).

`include "axi/typedef.svh"

module tb_bridge_top (
    input logic clk_i,
    input logic rst_ni,

    // OBI master port (discrete pins for cocotb)
    input  logic        req_i,
    input  logic        we_i,
    input  logic [ 3:0] be_i,
    input  logic [31:0] addr_i,
    input  logic [31:0] wdata_i,
    output logic        gnt_o,
    output logic        rvalid_o,
    output logic [31:0] rdata_o
);

  // 32-bit AXI, 2-bit IDs, 1-bit (unused) user — matches the FlooNoC config.
  typedef logic [31:0] axi_addr_t;
  typedef logic [31:0] axi_data_t;
  typedef logic [3:0] axi_strb_t;
  typedef logic [1:0] axi_id_t;
  typedef logic [0:0] axi_user_t;
  `AXI_TYPEDEF_ALL(axi, axi_addr_t, axi_id_t, axi_data_t, axi_strb_t, axi_user_t)

  obi_pkg::obi_req_t obi_m_req, obi_s_req;
  obi_pkg::obi_resp_t obi_m_resp, obi_s_resp;
  axi_req_t  axi_req;
  axi_resp_t axi_resp;

  assign obi_m_req.req = req_i;
  assign obi_m_req.we = we_i;
  assign obi_m_req.be = be_i;
  assign obi_m_req.addr = addr_i;
  assign obi_m_req.wdata = wdata_i;
  assign gnt_o = obi_m_resp.gnt;
  assign rvalid_o = obi_m_resp.rvalid;
  assign rdata_o = obi_m_resp.rdata;

  xheep_obi_to_axi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .axi_req_t (axi_req_t),
      .axi_resp_t(axi_resp_t)
  ) obi_to_axi_i (
      .clk_i     (clk_i),
      .rst_ni    (rst_ni),
      .obi_req_i (obi_m_req),
      .obi_resp_o(obi_m_resp),
      .axi_req_o (axi_req),
      .axi_resp_i(axi_resp)
  );

  xheep_axi_to_obi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .axi_req_t (axi_req_t),
      .axi_resp_t(axi_resp_t),
      .AxiIdWidth(2)
  ) axi_to_obi_i (
      .clk_i     (clk_i),
      .rst_ni    (rst_ni),
      .axi_req_i (axi_req),
      .axi_resp_o(axi_resp),
      .obi_req_o (obi_s_req),
      .obi_resp_i(obi_s_resp)
  );

  // Simple x-heep-style OBI memory: gnt tied high, rvalid/rdata registered
  // (same contract as memory_subsystem's sram banks).
  localparam int unsigned Words = 1024;
  logic [31:0] mem[Words];
  logic rvalid_q;
  logic [31:0] rdata_q;
  wire [$clog2(Words)-1:0] word = obi_s_req.addr[$clog2(Words)+1:2];

  assign obi_s_resp.gnt = 1'b1;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      rvalid_q <= 1'b0;
      rdata_q  <= '0;
    end else begin
      rvalid_q <= obi_s_req.req;
      if (obi_s_req.req) begin
        rdata_q <= mem[word];
        if (obi_s_req.we) begin
          for (int unsigned k = 0; k < 4; k++)
          if (obi_s_req.be[k]) mem[word][8*k+:8] <= obi_s_req.wdata[8*k+:8];
        end
      end
    end
  end
  assign obi_s_resp.rvalid = rvalid_q;
  assign obi_s_resp.rdata  = rdata_q;

endmodule : tb_bridge_top
