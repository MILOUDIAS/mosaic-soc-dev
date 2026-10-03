// Shared single-port memory with arbitration across all iDMA stream read and
// write masters. This models every active OBI master contending for one SRAM.

module tb_idma_xbar_mem #(
    parameter int unsigned DEPTH_WORDS = 4096,
    parameter int unsigned NUM_PORTS = 1,
    parameter int unsigned PORT_W = cf_math_pkg::idx_width(NUM_PORTS)
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  obi_pkg::obi_req_t  [NUM_PORTS-1:0] rd_req_i,
    output obi_pkg::obi_resp_t [NUM_PORTS-1:0] rd_resp_o,
    input  obi_pkg::obi_req_t  [NUM_PORTS-1:0] wr_req_i,
    output obi_pkg::obi_resp_t [NUM_PORTS-1:0] wr_resp_o
);
  localparam int unsigned AW = $clog2(DEPTH_WORDS);
  logic [31:0] mem[DEPTH_WORDS];

  logic busy, sel_wr;
  logic [PORT_W-1:0] sel_port, next_port;
  logic [31:0] addr_q;
  logic accept, next_wr;

  function automatic int unsigned widx(input logic [31:0] a);
    return a[AW+1:2];
  endfunction

  // Fixed-priority arbiter is sufficient for a bounded functional test. A
  // granted request completes one cycle later; all unselected masters stall.
  always_comb begin
    accept    = 1'b0;
    next_wr   = 1'b0;
    next_port = '0;
    for (int signed p = NUM_PORTS - 1; p >= 0; p--) begin
      if (rd_req_i[p].req) begin
        accept    = 1'b1;
        next_wr   = 1'b0;
        next_port = PORT_W'(p);
      end
      if (wr_req_i[p].req) begin
        accept    = 1'b1;
        next_wr   = 1'b1;
        next_port = PORT_W'(p);
      end
    end

    rd_resp_o = '0;
    wr_resp_o = '0;
    if (busy) begin
      if (sel_wr) begin
        wr_resp_o[sel_port].gnt    = 1'b1;
        wr_resp_o[sel_port].rvalid = 1'b1;
        wr_resp_o[sel_port].rdata  = mem[widx(addr_q)];
      end else begin
        rd_resp_o[sel_port].gnt    = 1'b1;
        rd_resp_o[sel_port].rvalid = 1'b1;
        rd_resp_o[sel_port].rdata  = mem[widx(addr_q)];
      end
    end
  end

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      busy     <= 1'b0;
      sel_wr   <= 1'b0;
      sel_port <= '0;
      addr_q   <= '0;
    end else if (busy) begin
      busy <= 1'b0;
    end else if (accept) begin
      busy     <= 1'b1;
      sel_wr   <= next_wr;
      sel_port <= next_port;
      if (next_wr) begin
        addr_q <= wr_req_i[next_port].addr;
        if (wr_req_i[next_port].we) begin
          if (wr_req_i[next_port].be[0]) mem[widx(wr_req_i[next_port].addr)][7:0] <= wr_req_i[next_port].wdata[7:0];
          if (wr_req_i[next_port].be[1]) mem[widx(wr_req_i[next_port].addr)][15:8] <= wr_req_i[next_port].wdata[15:8];
          if (wr_req_i[next_port].be[2]) mem[widx(wr_req_i[next_port].addr)][23:16] <= wr_req_i[next_port].wdata[23:16];
          if (wr_req_i[next_port].be[3]) mem[widx(wr_req_i[next_port].addr)][31:24] <= wr_req_i[next_port].wdata[31:24];
        end
      end else begin
        addr_q <= rd_req_i[next_port].addr;
      end
    end
  end
endmodule
