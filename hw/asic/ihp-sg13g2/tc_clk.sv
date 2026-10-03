// Copyright 2019 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

module tc_clk_and2 (
  input  logic clk0_i,
  input  logic clk1_i,
  output logic clk_o
);

  assign clk_o = clk0_i & clk1_i;

endmodule

module tc_clk_buffer (
  input  logic clk_i,
  output logic clk_o
);

  assign clk_o = clk_i;

endmodule

// Description: Behavioral model of an integrated clock-gating cell (ICG)
// ---- tc_clk_gating replaced for IHP sg13g2 ----------------------------
// Same reason as the GF180 variant: the generic behavioural latch has no
// standard cell to map to and survives synthesis as an unmapped $_DLATCH_N_.
// Bound to the library's own latch-based clock gate instead. Everything else
// in this file is verbatim from
// hw/vendor/pulp_platform/tech_cells_generic/src/rtl/tc_clk.sv.
//
// NOTE THE INTERFACE DIFFERENCE, because it is not cosmetic. GF180's
// `icgtp_1` has a dedicated test-enable pin (CLK, E, TE, Q). IHP's
// `sg13g2_lgcp_1` has only (GCLK, CLK, GATE) -- no TE. The scan behaviour
// that TE provides has to be built outside the cell: test_en_i is OR'd into
// the gate enable, which holds the clock on during scan exactly as TE does.
// That OR is real combinational logic in front of every gated clock rather
// than a free pin inside the cell, so it is a small area and timing cost
// that GF180 does not pay. Recorded here rather than discovered later.
module tc_clk_gating #(
    // Kept for interface compatibility with the generic cell. A
    // non-functional gate could legitimately be replaced by a feedthrough;
    // we always instantiate the real ICG, which is the conservative choice.
    parameter bit IS_FUNCTIONAL = 1'b1
) (
    input  logic clk_i,
    input  logic en_i,
    input  logic test_en_i,
    output logic clk_o
);

  logic gate_n;
  assign gate_n = en_i | test_en_i;

  sg13g2_lgcp_1 u_icg (
      .CLK (clk_i),
      .GATE(gate_n),
      .GCLK(clk_o)
  );

endmodule

module tc_clk_inverter (
  input  logic clk_i,
  output logic clk_o
);

  assign clk_o = ~clk_i;

endmodule

// Warning: Typical clock mux cells of a technologies std cell library ARE NOT
// GLITCH FREE!! The only difference to a regular multiplexer cell is that they
// feature balanced rise- and fall-times. In other words: SWITCHING FROM ONE
// CLOCK TO THE OTHER CAN INTRODUCE GLITCHES. ALSO, GLITCHES ON THE SELECT LINE
// DIRECTLY TRANSLATE TO GLITCHES ON THE OUTPUT CLOCK!! This cell is only
// intended to be used for quasi-static switching between clocks when one of the
// clocks is anyway inactive or if the downstream logic remains gated or in
// reset state during the transition phase. If you need dynamic switching
// between arbitrary input clocks without introducing glitches, have a look at
// the clk_mux_glitch_free cell in the pulp-platform/common_cells repository.
module tc_clk_mux2 (
  input  logic clk0_i,
  input  logic clk1_i,
  input  logic clk_sel_i,
  output logic clk_o
);

  assign clk_o = (clk_sel_i) ? clk1_i : clk0_i;

endmodule

module tc_clk_xor2 (
  input  logic clk0_i,
  input  logic clk1_i,
  output logic clk_o
);

  assign clk_o = clk0_i ^ clk1_i;

endmodule

module tc_clk_or2 (
  input logic clk0_i,
  input logic clk1_i,
  output logic clk_o
);

  assign clk_o = clk0_i | clk1_i;

endmodule

`ifndef SYNTHESIS
module tc_clk_delay #(
  parameter int unsigned Delay = 300ps
) (
  input  logic in_i,
  output logic out_o
);

// pragma translate_off
`ifndef VERILATOR
  assign #(Delay) out_o = in_i;
`endif
// pragma translate_on

endmodule
`endif
