// Functional-sim override of the cve2 clock gate. Negedge-flop (glitch-free)
// instead of the vendored always_latch, whose combinational feedback does not
// converge in this build (cve2 never bootstraps its clock). See README.
module cve2_clock_gate (
    input  logic clk_i,
    input  logic en_i,
    input  logic scan_cg_en_i,
    output logic clk_o
);
  logic en_q;
  always_ff @(negedge clk_i) begin
    en_q <= en_i | scan_cg_en_i;
  end
  assign clk_o = clk_i & en_q;
endmodule
