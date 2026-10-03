/*****************************************************************************\
|                      Copyright (C) 2021-2025 Luke Wren                      |
|                     SPDX-License-Identifier: Apache-2.0                     |
\*****************************************************************************/

// Implement a RISC-V JTAG DTM tunnelled through a Xilinx BSCANE2 primitive.
//
// Xilinx allows up to four custom DRs to be added to the FPGA TAP controller.
// A JTAG-DTM only needs two: DTMCS and DMI.
//
// With the correct config, OpenOCD can treat the FPGA TAP as a JTAG DTM and
// access RISC-V debug directly. This allows you to debug internal RISC-V
// cores with the same JTAG interface you use to load the FPGA.
//
// CHAIN_DTMCS and CHAIN_DMI select which JTAG IR values are used to access
// these DRs. Values 1 through 4 correspond to Xilinx USER1 through USER4
// instructions, which have IR values 0x02, 0x03, 0x22, 0x23.

`default_nettype none

module hazard3_xilinx7_jtag_dtm #(
    parameter SEL_DTMCS       = 3,
    parameter SEL_DMI         = 4,
    parameter DTMCS_IDLE_HINT = 3'd4,
    parameter W_PADDR         = 9,
    parameter ABITS           = W_PADDR - 2 // do not modify
) (
    // This is synchronous to TCK and asserted for one TCK cycle only
    output wire               dmihardreset_req,

    // Bus clock + reset for Debug Module Interface
    input  wire               clk_dmi,
    input  wire               rst_n_dmi,

    // Debug Module Interface (APB)
    output wire               dmi_psel,
    output wire               dmi_penable,
    output wire               dmi_pwrite,
    output wire [W_PADDR-1:0] dmi_paddr,
    output wire [31:0]        dmi_pwdata,
    input  wire [31:0]        dmi_prdata,
    input  wire               dmi_pready,
    input  wire               dmi_pslverr
);

// Signals to/from the Xilinx TAP

wire jtck_unbuf;
wire jtck;
wire jtdo2;
wire jtdo1;
wire jtdi;
wire jshift;
wire jupdate;
wire jcapture;
wire jrst;
wire jrst_n = !jrst;
wire jce2;
wire jce1;

BSCANE2 #(
   .JTAG_CHAIN (SEL_DTMCS)  // Value for USER command.
) bscan_dtmcs (
   .CAPTURE (jcapture),     // CAPTURE output from TAP controller.
   .DRCK    (/* unused */), // Gated TCK output. When SEL is asserted, DRCK toggles when CAPTURE or SHIFT are asserted.
   .RESET   (jrst),         // Reset output for TAP controller.
   .RUNTEST (/* unused */), // Output asserted when TAP controller is in Run Test/Idle state.
   .SEL     (jce1),         // USER instruction active output.
   .SHIFT   (jshift),       // SHIFT output from TAP controller.
   .TCK     (jtck_unbuf),   // Test Clock output. Fabric connection to TAP Clock pin.
   .TDI     (jtdi),         // Test Data Input (TDI) output from TAP controller.
   .TMS     (/* unused */), // Test Mode Select output. Fabric connection to TAP.
   .UPDATE  (jupdate),      // UPDATE output from TAP controller
   .TDO     (jtdo1)         // Test Data Output (TDO) input for USER function.
);

BSCANE2 #(
   .JTAG_CHAIN (SEL_DMI)
) bscan_dmi (
   .CAPTURE (/* unused */),
   .DRCK    (/* unused */),
   .RESET   (/* unused */),
   .RUNTEST (/* unused */),
   .SEL     (jce2),
   .SHIFT   (/* unused */),
   .TCK     (/* unused */),
   .TDI     (/* unused */),
   .TMS     (/* unused */),
   .UPDATE  (/* unused */),
   .TDO     (jtdo2)
);

BUFG bufg_jtck (
    .I (jtck_unbuf),
    .O (jtck)
);

localparam W_DR_SHIFT = ABITS + 32 + 2;

wire                  core_dr_wen = jupdate;
wire                  core_dr_ren = jcapture;
wire                  core_dr_sel_dmi_ndtmcs = !jce1;
wire                  dr_shift_en = jshift;
wire [W_DR_SHIFT-1:0] core_dr_wdata;
wire [W_DR_SHIFT-1:0] core_dr_rdata;

reg [W_DR_SHIFT-1:0] dr_shift;
assign core_dr_wdata = dr_shift;

always @ (posedge jtck or negedge jrst_n) begin
    if (!jrst_n) begin
        dr_shift <= {W_DR_SHIFT{1'b0}};
    end else if (core_dr_ren) begin
        dr_shift <= core_dr_rdata;
    end else if (dr_shift_en) begin
        dr_shift <= {jtdi, dr_shift[W_DR_SHIFT-1:1]};
        if (!core_dr_sel_dmi_ndtmcs)
            dr_shift[31] <= jtdi;
    end
end

// We have only a single shifter for the two DRs, so these are tied together:
assign jtdo1 = dr_shift[0];
assign jtdo2 = dr_shift[0];

// The actual DTM is in here:

hazard3_jtag_dtm_core #(
    .DTMCS_IDLE_HINT (DTMCS_IDLE_HINT),
    .W_ADDR          (ABITS)
) inst_hazard3_jtag_dtm_core (
    .tck               (jtck),
    .trst_n            (jrst_n),

    .clk_dmi           (clk_dmi),
    .rst_n_dmi         (rst_n_dmi),

    .dr_wen            (core_dr_wen),
    .dr_ren            (core_dr_ren),
    .dr_sel_dmi_ndtmcs (core_dr_sel_dmi_ndtmcs),
    .dr_wdata          (core_dr_wdata),
    .dr_rdata          (core_dr_rdata),

    .dmihardreset_req  (dmihardreset_req),

    .dmi_psel          (dmi_psel),
    .dmi_penable       (dmi_penable),
    .dmi_pwrite        (dmi_pwrite),
    .dmi_paddr         (dmi_paddr[W_PADDR-1:2]),
    .dmi_pwdata        (dmi_pwdata),
    .dmi_prdata        (dmi_prdata),
    .dmi_pready        (dmi_pready),
    .dmi_pslverr       (dmi_pslverr)
);

assign dmi_paddr[1:0] = 2'b00;

endmodule

`ifndef YOSYS
`default_nettype wire
`endif
