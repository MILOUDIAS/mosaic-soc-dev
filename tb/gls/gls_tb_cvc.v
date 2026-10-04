// TIMING-ANNOTATED gate-level testbench for the Block A macro (CVC).
//
// This is the companion to gls_tb.sv. That one runs under Icarus and is
// ZERO-DELAY: iverilog cannot compile the GF180 models' specify blocks
// ("sorry: ifnone with an edge-sensitive path is not supported"), so they must
// be built -DFUNCTIONAL, which removes exactly the paths SDF would annotate.
//
// CVC (OSS CVC 7.00b, IEEE 1364-2005) compiles those specify blocks, so here
// the run carries real cell and interconnect delays from the SDF, and the
// models' own setup/hold timing checks are live. That is the part STA cannot
// give you: STA proves the paths meet their constraints, this proves the design
// still FUNCTIONS when every gate has its delay and every flop is checked.
//
// Written in Verilog-2001 because CVC is not a SystemVerilog simulator -- no
// `logic`, no `string`, no `int`.
//
// POWER-UP: not modelled here. CVC's +random_2state=<seed> initialises all
// state to random 0/1, which is what silicon actually does, and is stronger
// than forcing everything to zero because a different seed is a different
// power-up state. See run_gls_cvc.sh.
//
// Plusargs:
//   +firmware=<hex>   flash image (Verilog hex, from objcopy -O verilog)
//   +sdf=<file>       SDF to back-annotate onto the DUT
//   +maxcycles=<n>    watchdog

`timescale 1ns / 1ps

module gls_tb_cvc;

  // 10 MHz: the frequency the macro is hardened at (CLOCK_PERIOD 100 ns).
  parameter CLK_HALF = 50.0;

  reg        clk = 1'b0;
  reg        rst_n = 1'b0;
  reg        boot_select = 1'b1;
  reg        execute_from_flash = 1'b1;
  reg        uart_rx = 1'b1;
  wire       uart_tx;
  wire       spi_sck;
  wire       spi_csb;
  wire [3:0] spi_sd;
  wire       status_valid;
  wire [6:0] status;

  supply1 VDD;
  supply0 VSS;

  reg [8*256:1] firmware;
  reg [8*256:1] sdf_file;
  integer       maxcycles;
  integer       cycles;
  integer       i;
  reg           have_sdf;

  mosaic_block_a dut (
      .clk_i               (clk),
      .rst_ni              (rst_n),
      .boot_select_i       (boot_select),
      .execute_from_flash_i(execute_from_flash),
      .uart_rx_i           (uart_rx),
      .uart_tx_o           (uart_tx),
      .spi_flash_sck_o     (spi_sck),
      .spi_flash_cs_o      (spi_csb),
      .spi_flash_sd_io     (spi_sd),
      .status_valid_o      (status_valid),
      .status_o            (status),
      .VDD                 (VDD),
      .VSS                 (VSS)
  );

  spiflash flash_i (
      .csb(spi_csb),
      .clk(spi_sck),
      .io0(spi_sd[0]),
      .io1(spi_sd[1]),
      .io2(spi_sd[2]),
      .io3(spi_sd[3])
  );

  always #(CLK_HALF) clk = ~clk;

  always @(posedge clk) cycles = cycles + 1;

  // SDF must be annotated before time advances.
  initial begin
    have_sdf = $value$plusargs("sdf=%s", sdf_file);
    if (have_sdf) begin
      $display("[GLS-CVC] annotating SDF");
      $sdf_annotate(sdf_file, dut);
    end else begin
      $display("[GLS-CVC] NO SDF -- this run carries cell delays but no back-annotation");
    end
  end

  initial begin
    cycles = 0;

    if (!$value$plusargs("firmware=%s", firmware)) begin
      $display("[GLS-CVC] FATAL: +firmware=<hex> required");
      $finish;
    end
    if (!$value$plusargs("maxcycles=%d", maxcycles)) maxcycles = 2000000;

    for (i = 0; i < 16*1024*1024; i = i + 1) flash_i.memory[i] = 8'h00;
    $readmemh(firmware, flash_i.memory);

    // Released on the FALLING edge. Deasserting in the same timestep as the
    // capture edge is the async-reset-release race: the dffrnq/dffsnq flops
    // see RN rise as CLK rises, and the winner is decided by the delta-cycle
    // depth of the reset tree versus the clock tree -- i.e. by buffering.
    // That is what made two equivalent netlists disagree under gls_tb.sv.
    rst_n = 1'b0;
    repeat (20) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    $display("[GLS-CVC] reset released at %t", $time);
  end

  // Pass/fail through the pins the silicon actually exposes.
  always @(posedge clk) begin
    if (rst_n === 1'b1 && status_valid === 1'b1) begin
      $display("[GLS-CVC] status_valid_o at %t after %0d cycles, status_o = 0x%02h",
               $time, cycles, status);
      if (status === 7'd0)
        $display("### RESULT: EXIT SUCCESS - timing-annotated netlist booted and reported 0");
      else
        $display("### RESULT: FAIL - exit value 0x%02h", status);
      $finish;
    end
  end

  always @(posedge clk) begin
    if (cycles > maxcycles) begin
      $display("[GLS-CVC] status_o = 0x%02h, status_valid_o = %b", status, status_valid);
      $display("### RESULT: FAIL - watchdog at %0d cycles", cycles);
      $finish;
    end
  end

  always @(posedge clk) begin
    if (cycles % 2000 == 0 && cycles > 0)
      $display("[GLS-CVC] %0d cycles, t=%t, csb=%b sd=%h", cycles, $time, spi_csb, spi_sd);
  end

endmodule
