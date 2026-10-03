// MOSAIC-SoC core adapter — generic GF180 pad buses ⇄ x_heep_system pins.
//
// chip_top.sv drives this with generic pad buses (input_*, bidir_*). This
// adapter maps them onto x_heep_system's physical pins following the order in
// configs/pad_cfg.py, and instantiates the generated MOSAIC SoC.
//
// ─────────────────────────────────────────────────────────────────────────
// AUTHORING STEP (the only remaining piece of the GF180 flow bring-up):
//   1. `make mosaic-gen` to emit hw/system/x_heep_system.sv + the SoC RTL.
//   2. Instantiate x_heep_system (or the x-heep pin-level top) below.
//   3. Bind each pad bus bit to the matching x_heep_system pin, in the SAME
//      order pad_cfg.py lists them, so the bit indices here line up with the
//      PAD_{N,S,E,W} instance-name lists in slots/slot_mosaic.yaml:
//        input_in[k]   -> the k-th pure-input pin  (boot_select, exec_from_flash,
//                         jtag_tck/tms/trst/tdi, uart_rx, ddr_rcv_clk)
//        bidir_in[k]   -> x-heep <pin>_i        (pad → core)
//        bidir_out[k]  -> x-heep <pin>_o        (core → pad)
//        bidir_oe[k]   -> x-heep <pin>_oe_o     (output enable)
//      gpio_* pads that pad_cfg.py muxes with spi_slave_*/ddr_* take the
//      muxed pin per the priority attribute.
//   4. Drive bidir_cs/sl (slew/drive-strength) and pull configs as the design
//      needs; defaults below are safe (push-pull, no pull, input buffer on).
// ─────────────────────────────────────────────────────────────────────────

`default_nettype none

module mosaic_soc_core #(
    parameter NUM_INPUT_PADS  = 8,
    parameter NUM_BIDIR_PADS  = 50,
    parameter NUM_ANALOG_PADS = 0
) (
    `ifdef USE_POWER_PINS
    inout wire VDD,
    inout wire VSS,
    `endif

    input  wire clk,
    input  wire rst_n,

    // Input pads (pad → core), with per-pad pull controls (core → pad)
    input  wire [NUM_INPUT_PADS-1:0] input_in,
    output wire [NUM_INPUT_PADS-1:0] input_pu,
    output wire [NUM_INPUT_PADS-1:0] input_pd,

    // Bidirectional pads
    input  wire [NUM_BIDIR_PADS-1:0] bidir_in,
    output wire [NUM_BIDIR_PADS-1:0] bidir_out,
    output wire [NUM_BIDIR_PADS-1:0] bidir_oe,
    output wire [NUM_BIDIR_PADS-1:0] bidir_cs,
    output wire [NUM_BIDIR_PADS-1:0] bidir_sl,
    output wire [NUM_BIDIR_PADS-1:0] bidir_ie,
    output wire [NUM_BIDIR_PADS-1:0] bidir_pu,
    output wire [NUM_BIDIR_PADS-1:0] bidir_pd,

    inout  wire [NUM_ANALOG_PADS-1:0] analog
);

  // Safe pad-control defaults: push-pull (CS=1), slow slew (SL=0),
  // input buffers enabled (IE=1), no pulls (PU=PD=0).
  assign bidir_cs = {NUM_BIDIR_PADS{1'b1}};
  assign bidir_sl = {NUM_BIDIR_PADS{1'b0}};
  assign bidir_ie = {NUM_BIDIR_PADS{1'b1}};
  assign bidir_pu = {NUM_BIDIR_PADS{1'b0}};
  assign bidir_pd = {NUM_BIDIR_PADS{1'b0}};
  assign input_pu = {NUM_INPUT_PADS{1'b0}};
  assign input_pd = {NUM_INPUT_PADS{1'b0}};

  // TODO(authoring step): instantiate x_heep_system and bind pads to pins.
  // Until then, drive outputs to a safe (tristate) state so this module
  // elaborates standalone.
  assign bidir_out = {NUM_BIDIR_PADS{1'b0}};
  assign bidir_oe  = {NUM_BIDIR_PADS{1'b0}};  // all pads input-only until bound

  // x_heep_system i_soc (
  //   .clk_i (clk), .rst_ni(rst_n), .hart_id_i(32'd0),
  //   ... bind the ~95 pad_cfg.py pins here ...
  // );

  // Silence unused-signal lint until the SoC is bound.
  wire _unused = &{1'b0, clk, rst_n, input_in, bidir_in, analog};

endmodule

`default_nettype wire
