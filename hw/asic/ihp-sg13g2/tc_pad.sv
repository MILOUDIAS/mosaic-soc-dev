// Technology pad cells for IHP sg13g2. See hw/asic/gf180/tc_pad.sv for why
// this is a per-PDK file rather than a cell instantiated in the wrappers.
//
// UNMEASURED. The GF180 variant's drive strength is backed by a slew
// measurement on real pad loading; nothing equivalent has been run here.
// `ebufn_4` is chosen to match GF180's _4 rather than because 4 is right for
// this process. Measure before believing it.

// Tristate pad driver. Drives `pad_io` from `d_i` when `en_i` is high, and
// releases it to Z otherwise -- the same contract as the GF180 variant.
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

  // NOTE THE POLARITY, because it is inverted from GF180's and silently
  // getting it wrong would leave the bus driven exactly when it should be
  // released. GF180's bufz is bufif0(Z, EN & I, ~EN) -- enable ACTIVE HIGH.
  // IHP's ebufn is bufif0(Z, A, TE_B) -- enable ACTIVE LOW. So the wrapper's
  // en_i has to be inverted here, which is one inverter per bidirectional pad
  // that GF180 does not pay for.
  logic te_b;
  assign te_b = ~en_i;

  sg13g2_ebufn_4 u_pad_drv (
      .A   (d_i),
      .TE_B(te_b),
      .Z   (pad_io)
  );

endmodule
