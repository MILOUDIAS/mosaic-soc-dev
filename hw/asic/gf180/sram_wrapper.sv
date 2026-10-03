// Copyright 2026 MOSAIC-SoC contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// GF180MCU macro-backed sram_wrapper.
//
// Replaces the flip-flop inferred memory of hw/simulation/sram_wrapper.sv with
// real gf180mcu_fd_ip_sram cuts. Selected by the `mosaic_gf180_sram` FuseSoC
// flag; every other target keeps the generic wrapper.
//
// GEOMETRY -- why four cuts per bank
// ---------------------------------
// Every GF180 SRAM macro is 8 BITS WIDE. A 32-bit bank is therefore always
// four cuts side by side, one per byte lane, sharing address and clock. That
// also makes byte enables free: each lane's WEN is driven from its own be_i
// bit, so no read-modify-write is needed.
//
// Available cuts and what a 32-bit bank made from them costs (areas measured
// from the PDK LEF SIZE, gf180mcuD):
//
//   cut          per-cut mm2   x4 = 32-bit bank      capacity
//   sram64x8        0.1006          0.4023 mm2         256 B
//   sram128x8       0.1161          0.4645 mm2         512 B
//   sram256x8       0.1472          0.5888 mm2       1'024 B
//   sram512x8       0.2094          0.8376 mm2       2'048 B
//
// Pick the cut by DEPTH, not by name: a 512 B bank wants four sram128x8
// (0.4645 mm2), NOT four sram512x8 -- the latter is the same 512 B of useful
// storage for 0.8376 mm2 because three quarters of every cut goes unaddressed.
//
// TIMING -- read this before trusting a frequency
// -----------------------------------------------
// The vendor model specifies Tcyc = 55.6 ns, i.e. roughly 18 MHz, far below the
// 100 MHz the simulation testbench assumes. A design that binds these macros
// must be closed at the macro's cycle time or clocked down. This wrapper does
// not and cannot fix that; it is a geometry binding, not a timing solution.
//
// INTERFACE
// ---------
// The cuts are all active-low: CEN enables the chip, GWEN selects write when
// low, and WEN is a per-bit active-low write mask. Q is registered inside the
// macro, so read data lands the cycle after the request -- the same contract
// the generic wrapper provides.

module sram_wrapper #(
    parameter int unsigned NumWords  = 32'd128,  // Number of words in the bank
    parameter int unsigned DataWidth = 32'd32,   // Data signal width
    // DEPENDENT PARAMETER, DO NOT OVERWRITE!
    parameter int unsigned AddrWidth = (NumWords > 32'd1) ? $clog2(NumWords) : 32'd1
) (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    // input ports
    input  logic                 req_i,
    input  logic                 we_i,
    input  logic [AddrWidth-1:0] addr_i,
    input  logic [         31:0] wdata_i,
    input  logic [          3:0] be_i,
    // power manager handshake -- the GF180 cuts have no power-gate or
    // retention pin, so the acknowledge is looped straight back. Keeping the
    // ports means memory_subsystem binds this wrapper unchanged.
    input  logic                 pwrgate_ni,
    output logic                 pwrgate_ack_no,
    input  logic                 set_retentive_ni,
    // output ports
    output logic [         31:0] rdata_o
);

  assign pwrgate_ack_no = pwrgate_ni;

  if (DataWidth != 32) begin : gen_width_check
    $error("gf180 sram_wrapper: only DataWidth 32 is bound (got %0d).", DataWidth);
  end

  if (NumWords != 64 && NumWords != 128 && NumWords != 256 && NumWords != 512) begin : gen_depth_check
    $error(
        "gf180 sram_wrapper: NumWords %0d has no matching GF180 cut. ",
        "Available depths are 64, 128, 256 and 512 words.", NumWords
    );
  end

  // Active-low macro controls, shared by all four byte lanes.
  logic cen_n, gwen_n;
  assign cen_n  = ~req_i;
  assign gwen_n = ~we_i;

  // rst_ni and set_retentive_ni have no macro equivalent: the cuts have no
  // reset port and no retention mode. Absorbed here so the ports do not read
  // as accidentally forgotten.
  logic unused_ok;
  assign unused_ok = ^{rst_ni, set_retentive_ni};

  for (genvar b = 0; b < 4; b++) begin : gen_byte_lane
    // be_i[b] high means "write this byte", and WEN is active low.
    logic [7:0] wen_n;
    assign wen_n = {8{~be_i[b]}};

    if (NumWords == 64) begin : gen_cut64
      gf180mcu_fd_ip_sram__sram64x8m8wm1 u_cut (
          .CLK(clk_i),
          .CEN(cen_n),
          .GWEN(gwen_n),
          .WEN(wen_n),
          .A(addr_i[5:0]),
          .D(wdata_i[8*b+:8]),
          .Q(rdata_o[8*b+:8])
      );
    end else if (NumWords == 128) begin : gen_cut128
      gf180mcu_fd_ip_sram__sram128x8m8wm1 u_cut (
          .CLK(clk_i),
          .CEN(cen_n),
          .GWEN(gwen_n),
          .WEN(wen_n),
          .A(addr_i[6:0]),
          .D(wdata_i[8*b+:8]),
          .Q(rdata_o[8*b+:8])
      );
    end else if (NumWords == 256) begin : gen_cut256
      gf180mcu_fd_ip_sram__sram256x8m8wm1 u_cut (
          .CLK(clk_i),
          .CEN(cen_n),
          .GWEN(gwen_n),
          .WEN(wen_n),
          .A(addr_i[7:0]),
          .D(wdata_i[8*b+:8]),
          .Q(rdata_o[8*b+:8])
      );
    end else begin : gen_cut512
      gf180mcu_fd_ip_sram__sram512x8m8wm1 u_cut (
          .CLK(clk_i),
          .CEN(cen_n),
          .GWEN(gwen_n),
          .WEN(wen_n),
          .A(addr_i[8:0]),
          .D(wdata_i[8*b+:8]),
          .Q(rdata_o[8*b+:8])
      );
    end
  end

endmodule  // sram_wrapper
