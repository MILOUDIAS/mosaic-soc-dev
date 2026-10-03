// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tb_noc_top.sv — stage-2 smoke test for the MOSAIC FlooNoC fabric:
//
//   cocotb OBI master -> xheep_obi_to_axi -> hart0 chimney -> router
//        -> mem chimney    -> xheep_axi_to_obi -> OBI memory   ([0, 0x8000))
//        -> periph chimney -> xheep_axi_to_obi -> pattern slave (above)
//
// Requires the GENERATED fabric (make mosaic-gen / mcu_gen with
// configs/mosaic_floonoc.yaml) — the stub files will not elaborate here.

module tb_noc_top (
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
  import floo_mosaic_noc_pkg::*;

  obi_pkg::obi_req_t obi_m_req, obi_mem_req, obi_periph_req;
  obi_pkg::obi_resp_t obi_m_resp, obi_mem_resp, obi_periph_resp;

  axi_axi_in_req_t hart0_req, tieoff_req;
  axi_axi_in_rsp_t hart0_rsp;
  axi_axi_out_req_t mem_req, periph_req;
  axi_axi_out_rsp_t mem_rsp, periph_rsp;

  assign obi_m_req.req = req_i;
  assign obi_m_req.we = we_i;
  assign obi_m_req.be = be_i;
  assign obi_m_req.addr = addr_i;
  assign obi_m_req.wdata = wdata_i;
  assign gnt_o = obi_m_resp.gnt;
  assign rvalid_o = obi_m_resp.rvalid;
  assign rdata_o = obi_m_resp.rdata;

  assign tieoff_req = '0;

  xheep_obi_to_axi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .axi_req_t (axi_axi_in_req_t),
      .axi_resp_t(axi_axi_in_rsp_t)
  ) obi_to_axi_i (
      .clk_i     (clk_i),
      .rst_ni    (rst_ni),
      .obi_req_i (obi_m_req),
      .obi_resp_o(obi_m_resp),
      .axi_req_o (hart0_req),
      .axi_resp_i(hart0_rsp)
  );

  floo_mosaic_noc noc_i (
      .clk_i               (clk_i),
      .rst_ni              (rst_ni),
      .test_enable_i       (1'b0),
      .hart0_axi_in_req_i  (hart0_req),
      .hart0_axi_in_rsp_o  (hart0_rsp),
      .hart1_axi_in_req_i  (tieoff_req),
      .hart1_axi_in_rsp_o  (),
      .hart2_axi_in_req_i  (tieoff_req),
      .hart2_axi_in_rsp_o  (),
      .shared_axi_in_req_i (tieoff_req),
      .shared_axi_in_rsp_o (),
      .mem_axi_out_req_o   (mem_req),
      .mem_axi_out_rsp_i   (mem_rsp),
      .periph_axi_out_req_o(periph_req),
      .periph_axi_out_rsp_i(periph_rsp)
  );

  xheep_axi_to_obi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .axi_req_t (axi_axi_out_req_t),
      .axi_resp_t(axi_axi_out_rsp_t),
      .AxiIdWidth(2)
  ) axi_to_obi_mem_i (
      .clk_i     (clk_i),
      .rst_ni    (rst_ni),
      .axi_req_i (mem_req),
      .axi_resp_o(mem_rsp),
      .obi_req_o (obi_mem_req),
      .obi_resp_i(obi_mem_resp)
  );

  xheep_axi_to_obi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .axi_req_t (axi_axi_out_req_t),
      .axi_resp_t(axi_axi_out_rsp_t),
      .AxiIdWidth(2)
  ) axi_to_obi_periph_i (
      .clk_i     (clk_i),
      .rst_ni    (rst_ni),
      .axi_req_i (periph_req),
      .axi_resp_o(periph_rsp),
      .obi_req_o (obi_periph_req),
      .obi_resp_i(obi_periph_resp)
  );

  // OBI memory behind the mem endpoint (gnt tied high, registered response)
  localparam int unsigned Words = 1024;
  logic [31:0] mem[Words];
  logic mem_rvalid_q;
  logic [31:0] mem_rdata_q;
  wire [$clog2(Words)-1:0] word = obi_mem_req.addr[$clog2(Words)+1:2];

  assign obi_mem_resp.gnt = 1'b1;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      mem_rvalid_q <= 1'b0;
      mem_rdata_q  <= '0;
    end else begin
      mem_rvalid_q <= obi_mem_req.req;
      if (obi_mem_req.req) begin
        mem_rdata_q <= mem[word];
        if (obi_mem_req.we) begin
          for (int unsigned k = 0; k < 4; k++)
          if (obi_mem_req.be[k]) mem[word][8*k+:8] <= obi_mem_req.wdata[8*k+:8];
        end
      end
    end
  end
  assign obi_mem_resp.rvalid = mem_rvalid_q;
  assign obi_mem_resp.rdata  = mem_rdata_q;

  // Pattern slave behind the periph endpoint: reads return PATTERN ^ addr
  logic periph_rvalid_q;
  logic [31:0] periph_rdata_q;
  assign obi_periph_resp.gnt = 1'b1;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      periph_rvalid_q <= 1'b0;
      periph_rdata_q  <= '0;
    end else begin
      periph_rvalid_q <= obi_periph_req.req;
      periph_rdata_q  <= 32'hCAFE0000 ^ obi_periph_req.addr;
    end
  end
  assign obi_periph_resp.rvalid = periph_rvalid_q;
  assign obi_periph_resp.rdata  = periph_rdata_q;

endmodule : tb_noc_top
