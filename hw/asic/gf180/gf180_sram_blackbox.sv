// Copyright 2026 MOSAIC-SoC contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Blackbox declarations for the GF180MCU SRAM cuts.
//
// WHY THIS FILE EXISTS
// --------------------
// The PDK ships `..._blackbox.v` views, but they are only *empty* modules --
// they carry no `(* blackbox *)` attribute. Yosys therefore treats them as
// ordinary modules whose outputs nobody drives, and reports every Q bit as
// "used but has no driver": 32 errors for a 4-cut bank, enough to fail
// Checker.YosysSynthChecks and stop the flow before floorplanning.
//
// Declaring the cuts explicitly as blackboxes says what is actually true --
// the implementation arrives later, as GDS and Liberty, not as RTL. The
// alternative (slang's --ignore-unknown-modules) would silence a genuinely
// missing module just as readily, so it is the worse trade.
//
// Port lists are transcribed from the PDK blackbox views; only the address
// width differs between cuts (6/7/8/9 bits for 64/128/256/512 words).
// Power pins are omitted to match the PDK's non-USE_POWER_PINS variant.

/* verilator lint_off DECLFILENAME */

(* blackbox *)
module gf180mcu_fd_ip_sram__sram64x8m8wm1 (
    input  logic       CLK,
    input  logic       CEN,   // chip enable, active low
    input  logic       GWEN,  // global write enable, active low
    input  logic [7:0] WEN,   // per-bit write enable, active low
    input  logic [5:0] A,
    input  logic [7:0] D,
    output logic [7:0] Q
);
endmodule

(* blackbox *)
module gf180mcu_fd_ip_sram__sram128x8m8wm1 (
    input  logic       CLK,
    input  logic       CEN,
    input  logic       GWEN,
    input  logic [7:0] WEN,
    input  logic [6:0] A,
    input  logic [7:0] D,
    output logic [7:0] Q
);
endmodule

(* blackbox *)
module gf180mcu_fd_ip_sram__sram256x8m8wm1 (
    input  logic       CLK,
    input  logic       CEN,
    input  logic       GWEN,
    input  logic [7:0] WEN,
    input  logic [7:0] A,
    input  logic [7:0] D,
    output logic [7:0] Q
);
endmodule

(* blackbox *)
module gf180mcu_fd_ip_sram__sram512x8m8wm1 (
    input  logic       CLK,
    input  logic       CEN,
    input  logic       GWEN,
    input  logic [7:0] WEN,
    input  logic [8:0] A,
    input  logic [7:0] D,
    output logic [7:0] Q
);
endmodule

/* verilator lint_on DECLFILENAME */
