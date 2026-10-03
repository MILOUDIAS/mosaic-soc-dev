// Multiported behavioral memory for the iDMA per-block test. Every stream has
// an independent read/write pair backed by the same array.

module tb_idma_mem #(
    parameter int unsigned DEPTH_WORDS = 4096,
    parameter int unsigned NUM_PORTS = 1
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  obi_pkg::obi_req_t  [NUM_PORTS-1:0] rd_req_i,
    output obi_pkg::obi_resp_t [NUM_PORTS-1:0] rd_resp_o,
    input  obi_pkg::obi_req_t  [NUM_PORTS-1:0] wr_req_i,
    output obi_pkg::obi_resp_t [NUM_PORTS-1:0] wr_resp_o
);
  localparam int unsigned AW = $clog2(DEPTH_WORDS);
  typedef logic [31:0] addr_t;
  logic [31:0] mem[DEPTH_WORDS];
  logic [NUM_PORTS-1:0] rd_v, wr_v;
  addr_t [NUM_PORTS-1:0] rd_addr_q, wr_addr_q;

  function automatic int unsigned widx(input logic [31:0] a);
    return a[AW+1:2];
  endfunction

  for (genvar p = 0; p < NUM_PORTS; p++) begin : gen_response
    assign rd_resp_o[p].gnt    = rd_v[p];
    assign rd_resp_o[p].rvalid = rd_v[p];
    assign rd_resp_o[p].rdata  = mem[widx(rd_addr_q[p])];
    assign wr_resp_o[p].gnt    = wr_v[p];
    assign wr_resp_o[p].rvalid = wr_v[p];
    assign wr_resp_o[p].rdata  = mem[widx(wr_addr_q[p])];
  end

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      rd_v <= '0;
      wr_v <= '0;
    end else begin
      for (int unsigned p = 0; p < NUM_PORTS; p++) begin
        rd_v[p] <= rd_req_i[p].req & ~rd_v[p];
        wr_v[p] <= wr_req_i[p].req & ~wr_v[p];
        if (rd_req_i[p].req & ~rd_v[p]) begin
          rd_addr_q[p] <= rd_req_i[p].addr;
        end
        if (wr_req_i[p].req & ~wr_v[p]) begin
          wr_addr_q[p] <= wr_req_i[p].addr;
          if (wr_req_i[p].we) begin
            if (wr_req_i[p].be[0]) mem[widx(wr_req_i[p].addr)][7:0] <= wr_req_i[p].wdata[7:0];
            if (wr_req_i[p].be[1]) mem[widx(wr_req_i[p].addr)][15:8] <= wr_req_i[p].wdata[15:8];
            if (wr_req_i[p].be[2]) mem[widx(wr_req_i[p].addr)][23:16] <= wr_req_i[p].wdata[23:16];
            if (wr_req_i[p].be[3]) mem[widx(wr_req_i[p].addr)][31:24] <= wr_req_i[p].wdata[31:24];
          end
        end
      end
    end
  end
endmodule
