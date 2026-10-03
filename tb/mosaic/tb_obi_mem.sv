// tb_obi_mem.sv — simple behavioral OBI v1.3 memory for the MOSAIC multi-core
// testbench. Single-outstanding, registered response: a request is accepted in
// one cycle and gnt + rvalid + rdata are asserted TOGETHER the next cycle. The
// SCI wrappers ack on `gnt & rvalid` (same cycle), which this satisfies; the
// one-cycle register also breaks the stb→ack→stb combinational path that a
// zero-latency slave would create through FazyRV's Wishbone FSM.
//
// Every word is preloaded with `jal x0, 0` (0x0000006F, jump-to-self) so any
// stray fetch simply spins instead of executing garbage. A tiny test program is
// overlaid at the boot address (0x180): it writes a sentinel to SENTINEL_ADDR
// and then spins, so the testbench can confirm the core fetched, decoded and
// executed real instructions through its SCI wrapper.

module tb_obi_mem #(
    parameter int unsigned DEPTH_WORDS = 2048,  // 8 KB; covers 0x180 + 0x40
    parameter logic [31:0] SENTINEL = 32'h00000055
) (
    input  logic               clk_i,
    input  logic               rst_ni,
    input  obi_pkg::obi_req_t  req_i,
    output obi_pkg::obi_resp_t resp_o
);
  localparam int unsigned AW = $clog2(DEPTH_WORDS);

  logic [31:0] mem[DEPTH_WORDS];

  // Word index from a byte address.
  function automatic int unsigned widx(input logic [31:0] addr);
    return addr[AW+1:2];
  endfunction

  initial begin
    for (int i = 0; i < DEPTH_WORDS; i++) mem[i] = 32'h0000_006F;  // jal x0, 0
    // Test program @ 0x180 (word 0x60):
    //   addi x1, x0, 0x55      ; x1 = sentinel
    //   addi x2, x0, 0x40      ; x2 = SENTINEL_ADDR
    //   sw   x1, 0(x2)         ; mem[0x40] = x1
    //   jal  x0, 0             ; spin
    mem['h60] = 32'h0550_0093;
    mem['h61] = 32'h0400_0113;
    mem['h62] = 32'h0011_2023;
    mem['h63] = 32'h0000_006F;
  end

  // Single-outstanding handshake: gnt + rvalid are asserted together one cycle
  // after a request is accepted (registered ack — breaks the stb→ack→stb
  // combinational path through FazyRV's Wishbone FSM). The read data is
  // COMBINATIONAL on the current request address, so a master that changes its
  // address each access (FazyRV) always sees the right word with no skew.
  logic resp_valid_q;

  // Accept a new request only when not currently presenting a response.
  wire  accept = req_i.req & ~resp_valid_q;

  assign resp_o.gnt    = resp_valid_q;
  assign resp_o.rvalid = resp_valid_q;
  assign resp_o.rdata  = mem[widx(req_i.addr)];

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      resp_valid_q <= 1'b0;
    end else begin
      resp_valid_q <= accept;
      if (accept && req_i.we) begin
        if (req_i.be[0]) mem[widx(req_i.addr)][7:0] <= req_i.wdata[7:0];
        if (req_i.be[1]) mem[widx(req_i.addr)][15:8] <= req_i.wdata[15:8];
        if (req_i.be[2]) mem[widx(req_i.addr)][23:16] <= req_i.wdata[23:16];
        if (req_i.be[3]) mem[widx(req_i.addr)][31:24] <= req_i.wdata[31:24];
      end
    end
  end
endmodule
