// Padframe model for GLS of the 167-terminal Block A macro.
//
// The macro used to drive its own pads through internal tristates, so its 22
// boundary ports were the chip's pins and the testbench bound them directly.
// Against an external padframe DEF the pads live OUTSIDE the macro, and the macro
// exposes the pad CONTROL terminals instead: OUT/OE/IN plus CS/SL/IE/PU/PD/
// PDRV0/PDRV1 per bidirectional pad. That is 167 terminals, none of which the
// existing testbench knows how to bind.
//
// This module is the missing half: it plays the role of the padring so the
// same testbench, firmware and flash model keep working. It presents the old
// 13-port face outward and speaks the control protocol inward.
//
// What it deliberately does NOT model: drive strength (PDRV1/PDRV0), slew (SL)
// and input type (CS). Those change edge shape and threshold, and this is a
// ZERO-DELAY functional simulation -- it cannot observe them. They are checked
// by inspection against pad_settings.md, not here. The bits this DOES model are
// the ones that change function: OE, IE and the pulls.
`timescale 1ns / 1ps

module mosaic_block_a_padwrap (
    input  wire       clk_i,
    input  wire       rst_ni,
    input  wire       boot_select_i,
    input  wire       execute_from_flash_i,
    input  wire       uart_rx_i,
    output wire       uart_tx_o,
    output wire       spi_flash_sck_o,
    output wire       spi_flash_cs_o,
    inout  wire [3:0] spi_flash_sd_io,
    output wire       status_valid_o,
    output wire [6:0] status_o,
    inout  wire       VDD,
    inout  wire       VSS
);

  // ── output-only pads ───────────────────────────────────────────────────
  // OE is tied high and IE low inside the macro, so the pad drives OUT and its
  // receiver is off. The _IN terminals are therefore left unconnected here,
  // which is exactly why signoff reports 11 non-critical disconnected pins.
  wire uart_tx_OUT, sck_OUT, cs_OUT, status_valid_OUT;
  wire [6:0] status_OUT;
  assign uart_tx_o      = uart_tx_OUT;
  assign spi_flash_sck_o = sck_OUT;
  assign spi_flash_cs_o  = cs_OUT;
  assign status_valid_o  = status_valid_OUT;
  assign status_o        = status_OUT;

  // ── the four bidirectional QSPI pads ───────────────────────────────────
  // This is the behaviour that moved out of the macro. gf180mcu_fd_io__bi_t
  // drives A onto the pin when OE is high and presents the pin on Y when IE is
  // high; the macro sets IE = ~OE because IE=1 with OE=1 is Disallowed.
  wire [3:0] sd_OUT, sd_OE, sd_IE;
  assign spi_flash_sd_io[0] = sd_OE[0] ? sd_OUT[0] : 1'bz;
  assign spi_flash_sd_io[1] = sd_OE[1] ? sd_OUT[1] : 1'bz;
  assign spi_flash_sd_io[2] = sd_OE[2] ? sd_OUT[2] : 1'bz;
  assign spi_flash_sd_io[3] = sd_OE[3] ? sd_OUT[3] : 1'bz;
  // The receiver only presents the pin while IE is asserted. Feeding the pin
  // through unconditionally would hide a wrong IE, which is one of the two
  // things this simulation can actually catch.
  wire [3:0] sd_IN;
  assign sd_IN[0] = sd_IE[0] ? spi_flash_sd_io[0] : 1'bx;
  assign sd_IN[1] = sd_IE[1] ? spi_flash_sd_io[1] : 1'bx;
  assign sd_IN[2] = sd_IE[2] ? spi_flash_sd_io[2] : 1'bx;
  assign sd_IN[3] = sd_IE[3] ? spi_flash_sd_io[3] : 1'bx;

  // ── input pads with pulls ──────────────────────────────────────────────
  // Only rst_ni carries one (a pull-down). Model it weakly so a driven
  // testbench still wins, which is what a real pull does.
  wire rst_ni_PD, rst_ni_PU;
  wire clk_PD, clk_PU, boot_PD, boot_PU, xfl_PD, xfl_PU, urx_PD, urx_PU;
  wire rst_ni_padded;
  assign (weak0, weak1) rst_ni_padded = rst_ni_PD ? 1'b0 : (rst_ni_PU ? 1'b1 : 1'bz);
  assign rst_ni_padded = rst_ni;

  // Control terminals we accept but cannot simulate. Named so a waveform shows
  // what the macro asked the padring for.
  wire uart_tx_CS, uart_tx_IE, uart_tx_OE, uart_tx_PD, uart_tx_PU,
       uart_tx_PDRV0, uart_tx_PDRV1, uart_tx_SL;
  wire sck_CS, sck_IE, sck_OE, sck_PD, sck_PU, sck_PDRV0, sck_PDRV1, sck_SL;
  wire cs_CS, cs_IE, cs_OE, cs_PD, cs_PU, cs_PDRV0, cs_PDRV1, cs_SL;
  wire sv_CS, sv_IE, sv_OE, sv_PD, sv_PU, sv_PDRV0, sv_PDRV1, sv_SL;
  wire [6:0] st_CS, st_IE, st_OE, st_PD, st_PU, st_PDRV0, st_PDRV1, st_SL;
  wire [3:0] sd_CS, sd_PD, sd_PU, sd_PDRV0, sd_PDRV1, sd_SL;

  mosaic_block_a dut (
      .VDD(VDD), .VSS(VSS),
      .clk_i(clk_i), .clk_i_PD(clk_PD), .clk_i_PU(clk_PU),
      .rst_ni(rst_ni_padded), .rst_ni_PD(rst_ni_PD), .rst_ni_PU(rst_ni_PU),
      .boot_select_i(boot_select_i), .boot_select_i_PD(boot_PD), .boot_select_i_PU(boot_PU),
      .execute_from_flash_i(execute_from_flash_i),
      .execute_from_flash_i_PD(xfl_PD), .execute_from_flash_i_PU(xfl_PU),
      .uart_rx_i(uart_rx_i), .uart_rx_i_PD(urx_PD), .uart_rx_i_PU(urx_PU),

      .uart_tx_o_OUT(uart_tx_OUT), .uart_tx_o_IN(1'b0),
      .uart_tx_o_OE(uart_tx_OE), .uart_tx_o_IE(uart_tx_IE), .uart_tx_o_CS(uart_tx_CS),
      .uart_tx_o_SL(uart_tx_SL), .uart_tx_o_PU(uart_tx_PU), .uart_tx_o_PD(uart_tx_PD),
      .uart_tx_o_PDRV0(uart_tx_PDRV0), .uart_tx_o_PDRV1(uart_tx_PDRV1),

      .spi_flash_sck_o_OUT(sck_OUT), .spi_flash_sck_o_IN(1'b0),
      .spi_flash_sck_o_OE(sck_OE), .spi_flash_sck_o_IE(sck_IE), .spi_flash_sck_o_CS(sck_CS),
      .spi_flash_sck_o_SL(sck_SL), .spi_flash_sck_o_PU(sck_PU), .spi_flash_sck_o_PD(sck_PD),
      .spi_flash_sck_o_PDRV0(sck_PDRV0), .spi_flash_sck_o_PDRV1(sck_PDRV1),

      .spi_flash_cs_o_OUT(cs_OUT), .spi_flash_cs_o_IN(1'b0),
      .spi_flash_cs_o_OE(cs_OE), .spi_flash_cs_o_IE(cs_IE), .spi_flash_cs_o_CS(cs_CS),
      .spi_flash_cs_o_SL(cs_SL), .spi_flash_cs_o_PU(cs_PU), .spi_flash_cs_o_PD(cs_PD),
      .spi_flash_cs_o_PDRV0(cs_PDRV0), .spi_flash_cs_o_PDRV1(cs_PDRV1),

      .spi_flash_sd_io_OUT(sd_OUT), .spi_flash_sd_io_IN(sd_IN),
      .spi_flash_sd_io_OE(sd_OE), .spi_flash_sd_io_IE(sd_IE), .spi_flash_sd_io_CS(sd_CS),
      .spi_flash_sd_io_SL(sd_SL), .spi_flash_sd_io_PU(sd_PU), .spi_flash_sd_io_PD(sd_PD),
      .spi_flash_sd_io_PDRV0(sd_PDRV0), .spi_flash_sd_io_PDRV1(sd_PDRV1),

      .status_valid_o_OUT(status_valid_OUT), .status_valid_o_IN(1'b0),
      .status_valid_o_OE(sv_OE), .status_valid_o_IE(sv_IE), .status_valid_o_CS(sv_CS),
      .status_valid_o_SL(sv_SL), .status_valid_o_PU(sv_PU), .status_valid_o_PD(sv_PD),
      .status_valid_o_PDRV0(sv_PDRV0), .status_valid_o_PDRV1(sv_PDRV1),

      .status_o_OUT(status_OUT), .status_o_IN(7'b0),
      .status_o_OE(st_OE), .status_o_IE(st_IE), .status_o_CS(st_CS),
      .status_o_SL(st_SL), .status_o_PU(st_PU), .status_o_PD(st_PD),
      .status_o_PDRV0(st_PDRV0), .status_o_PDRV1(st_PDRV1)
  );

  // The pad settings are constants the macro asserts. Check them once, after
  // reset, rather than trusting the table: this is the only place the SILICON
  // states its own pad configuration.
  initial begin
    #1000;
    if (uart_tx_OE !== 1'b1 || uart_tx_IE !== 1'b0)
      $display("[PADWRAP] FAIL uart_tx_o OE=%b IE=%b, expected 1/0", uart_tx_OE, uart_tx_IE);
    if (rst_ni_PD !== 1'b1 || rst_ni_PU !== 1'b0)
      $display("[PADWRAP] FAIL rst_ni PD=%b PU=%b, expected 1/0", rst_ni_PD, rst_ni_PU);
    if (sd_PDRV1 !== 4'b1111 || sd_PDRV0 !== 4'b0000)
      $display("[PADWRAP] FAIL qspi PDRV1=%b PDRV0=%b, expected 1111/0000 (12 mA)",
               sd_PDRV1, sd_PDRV0);
    if ((sd_IE & sd_OE) !== 4'b0000)
      $display("[PADWRAP] FAIL qspi IE&OE=%b -- the PDK marks IE=1,OE=1 Disallowed",
               sd_IE & sd_OE);
    $display("[PADWRAP] pad controls: uart OE/IE=%b/%b  rst PD/PU=%b/%b  qspi PDRV=%b%b  IE&OE=%b",
             uart_tx_OE, uart_tx_IE, rst_ni_PD, rst_ni_PU,
             sd_PDRV1[0], sd_PDRV0[0], sd_IE & sd_OE);
  end

endmodule
