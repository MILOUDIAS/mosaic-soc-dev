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
// ---- tc_clk_gating replaced for GF180MCU ------------------------------
// The generic behavioural latch has no GF180 standard cell to map to (the
// library has no latch), so it survived synthesis as an unmapped
// $_DLATCH_N_. Bound to the library's integrated clock gate instead.
// Everything else in this file is verbatim from
// hw/vendor/pulp_platform/tech_cells_generic/src/rtl/tc_clk.sv.
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

  // TE holds the clock on during scan/test regardless of E.
  gf180mcu_fd_sc_mcu7t5v0__icgtp_1 u_icg (
      .CLK(clk_i),
      .E  (en_i),
      .TE (test_en_i),
      .Q  (clk_o)
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
