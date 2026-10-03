// Technology pad cells for GF180MCU.
//
// WHY THIS FILE EXISTS. The delivery wrappers used to instantiate
// `gf180mcu_fd_sc_mcu7t5v0__bufz_4` directly. That made every wrapper
// process-specific, and wrappers are PER DESIGN -- so the porting cost was
// designs x PDKs rather than one file per PDK. Hardening Block A on IHP
// sg13g2 failed at Verilator lint on exactly this cell, after the whole RTL
// had elaborated. Same shape as tc_clk.sv, same fix.
//
// The drive strength is a MEASURED choice, not a default, and the measurement
// lives here with the cell it constrains:
//
//   Doubling the drive made the pads themselves ~1 ns SLOWER. These pads are
//   not driver-limited; their transition is inherited from the net feeding
//   them, and a bufz_8 presents roughly twice the input capacitance, which
//   degrades that net's slew faster than the stronger output recovers it.
//   Output slew tracks input slew, so the trade is a loss. Do not "fix" these
//   pads by upsizing again without first improving what drives them.
//
//   Still provisional against the integrator's real pad loading:
//   OUTPUT_CAP_LOAD here is 72.91 fF, and a bonded pad plus board trace will
//   exceed that. If that number rises a lot, revisit -- input net first.

// Tristate pad driver. Drives `pad_io` from `d_i` when `en_i` is high, and
// releases it to Z otherwise.
module tc_pad_tristate (
    input  logic en_i,
    input  logic d_i,
    output wire  pad_io
);

  // PORT DIRECTION IS `output`, NOT `inout`, AND THAT IS DELIBERATE.
  // The cell only DRIVES the pad; reading it back is a separate `assign` in
  // the wrapper, exactly as it was when the PDK cell was instantiated there
  // directly -- and the PDK cells declare `output Z` themselves.
  //
  // Declaring it `inout` here is also not merely redundant, it does not
  // build: yosys-slang refuses to inline a module with an inout port
  // connected to a bit-select ("cannot be inlined; see yosys-slang issue
  // #143"). Measured, on mosaic_block_a.sv:102.

  // GF180's bufz has an ACTIVE-HIGH enable: bufif0(Z, EN & I, ~EN).
  gf180mcu_fd_sc_mcu7t5v0__bufz_4 u_pad_drv (
      .EN(en_i),
      .I (d_i),
      .Z (pad_io)
  );

endmodule
