// Gate-level testbench for the Chipathon Block A macro.
//
// WHY THIS EXISTS
// ---------------
// Every simulation up to now ran on RTL. This one runs on the POST-PLACE-AND-
// ROUTE netlist -- the actual gates in the GDS, with the PDK's own cell models
// -- and optionally with SDF timing back-annotated. It closes the last item the
// schematic review listed for the physical phase ("post-synthesis gate-level
// simulation once a netlist exists").
//
// It also verifies something no RTL testbench can: that synthesis, CTS, place
// and route preserved the design's behaviour. Bugs 28 and 31 were both cases of
// RTL that elaborated and simulated happily while being wrong; this is the
// complementary check, RTL-correct-but-implementation-broken.
//
// THE INTERFACE IS THE CHIP'S
// ---------------------------
// This drives only the 22 pins the MPW integrator will bond. There is no
// backdoor memory load, no hierarchical force, no probing of internal state --
// the design boots XIP from a behavioural QSPI flash and reports through
// status_valid_o/status_o, which with soc.debug: false is the only observability
// the silicon has. If this passes, the part can be brought up on a board the
// same way.
//
// Usage (see run_gls.sh):
//   +firmware=<hex>   flash image, Verilog hex from objcopy -O verilog
//   +sdf=<file>       optional SDF for timing-annotated GLS
//   +maxcycles=<n>    watchdog

`timescale 1ns / 1ps

module gls_tb;

  // 10 MHz -- the frequency the macro is hardened at (CLOCK_PERIOD 100 ns).
  // Running GLS faster than the design was closed for would be meaningless.
  localparam realtime ClkPeriod = 100.0;

  logic       clk = 1'b0;
  logic       rst_n = 1'b0;
  logic       boot_select = 1'b1;        // 1 = boot from flash
  logic       execute_from_flash = 1'b1; // 1 = XIP through the spimemio window
  logic       uart_rx = 1'b1;            // idle high; nothing drives us
  wire        uart_tx;
  wire        spi_sck;
  wire        spi_csb;
  wire [3:0]  spi_sd;
  wire        status_valid;
  wire [6:0]  status;

  // The netlist instantiates every cell with .VDD/.VNW/.VPW/.VSS, so the models
  // are compiled with USE_POWER_PINS and the top's power pins must be driven.
  supply1 VDD;
  supply0 VSS;

  string  firmware;
  string  sdf_file;
  int     maxcycles;
  longint cycles;
  // Activity capture for workload power. See the +vcd block below.
  string  vcd_file;
  int     vcd_start;
  int     vcd_cycles;
  bit     vcd_enabled = 1'b0;
  bit     vcd_running = 1'b0;

  // Module name comes from the build, not the source: all three delivery
  // wrappers expose an identical 11-port list, so one testbench covers every
  // block. Defaults to Block A so a bare `iverilog` on this file still works.
`ifndef GLS_DUT
  `define GLS_DUT mosaic_block_a
`endif
  `GLS_DUT dut (
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

  always #(ClkPeriod / 2.0) clk = ~clk;

  // Count cycles for the watchdog and for reporting how long boot took.
  always @(posedge clk) cycles <= cycles + 1;

  // Open and close the region of interest. Reported when it closes so the
  // window that produced a VCD is in the log next to the run that produced it
  // -- a power number whose ROI nobody recorded is not evidence (M2).
  always @(posedge clk) begin
    if (vcd_enabled && !vcd_running && cycles >= vcd_start) begin
      $display("[GLS] activity ROI open at cycle %0d", cycles);
      $dumpfile(vcd_file);
      // Depth 0 = the whole DUT hierarchy. report_power annotates leaf-cell
      // pins, so a shallow dump reads as "no activity found" rather than
      // erroring. A full-depth dump of this netlist annotated 119 792 pins.
      $dumpvars(0, dut);
      vcd_running <= 1'b1;
    end else if (vcd_running && cycles >= vcd_start + vcd_cycles) begin
      $dumpoff;
      vcd_running <= 1'b0;
      vcd_enabled <= 1'b0;
      $display("[GLS] activity ROI closed at cycle %0d (roi_cycles=%0d)",
               cycles, vcd_cycles);
    end
  end

  initial begin
    cycles = 0;

    if (!$value$plusargs("firmware=%s", firmware)) begin
      $display("[GLS] FATAL: +firmware=<hex> is required");
      $fatal(1);
    end
    if (!$value$plusargs("maxcycles=%d", maxcycles)) maxcycles = 2_000_000;

    // SDF is optional: without it this is a functional check of the routed
    // netlist; with it, the cell and interconnect delays of a corner are
    // back-annotated and the run also exercises setup/hold in the models.
    // "No SDF" does not mean "no delay": run_gls.sh gives the sequential UDPs
    // a nonzero clock-to-Q (GLS_SEQ_DELAY) so that buffering cannot decide
    // what a flop samples. The log header records which. Combinational cells
    // are still zero-delay either way.
    if ($value$plusargs("sdf=%s", sdf_file)) begin
      $display("[GLS] annotating SDF: %s", sdf_file);
      $sdf_annotate(sdf_file, dut);
    end else begin
      $display("[GLS] no SDF (see '### seq c2q' in the log header)");
    end

    // ---- switching-activity capture, for workload power -------------------
    // +vcd=<path> turns it on. OpenSTA's report_power annotates activity onto
    // NETS OF THE NETLIST, which is why this lives in the gate-level bench and
    // not the RTL one: an RTL trace carries names that mostly do not survive
    // synthesis, so the activity would fail to attach to almost everything
    // while still producing a plausible-looking number.
    //
    // The window is BOUNDED and that is not an optimisation. This netlist is
    // ~200k cells; dumping every net for the full ~12 400-cycle boot produces
    // a VCD in the tens of gigabytes, on a volume that has been at 96-99% all
    // week. +vcd_start/+vcd_cycles select a region of interest instead, and
    // harness/evidence/workload.py refuses to call a power number
    // workload-derived if that region does not cover the run.
    if ($value$plusargs("vcd=%s", vcd_file)) begin
      if (!$value$plusargs("vcd_start=%d", vcd_start)) vcd_start = 0;
      if (!$value$plusargs("vcd_cycles=%d", vcd_cycles)) vcd_cycles = 1000;
      $display("[GLS] activity capture: %s, cycles %0d..%0d",
               vcd_file, vcd_start, vcd_start + vcd_cycles);
      // NOTE: $dumpfile/$dumpvars are deliberately NOT called here. They fire
      // when the ROI opens, below. Arming with $dumpvars + $dumpoff would emit
      // a `$dumpon` marker, and OpenSTA's VCD reader rejects it outright:
      //   [ERROR STA-0800] ... unknown vcd command
      // Deferring instead means the file only ever contains the ROI.
      vcd_enabled = 1'b1;
    end

    // Load the flash exactly as tb_util.svh does for the RTL flow: clear, then
    // read the Verilog-hex image with its @address records.
    for (int unsigned i = 0; i < 16 * 1024 * 1024; i++) flash_i.memory[i] = 8'h00;
    $readmemh(firmware, flash_i.memory);
    $display("[GLS] flash image: %s", firmware);

    // Reset for 20 cycles, released on the FALLING edge.
    //
    // This used to deassert immediately after `@(posedge clk)`, i.e. in the
    // same timestep as the capture edge. That is the async-reset-release
    // race: this netlist's dffrnq/dffsnq flops see RN rise at the instant CLK
    // rises, and which one the simulator delivers first is decided by how
    // many delta cycles the reset tree and the clock tree each take -- that
    // is, by BUFFERING. It is the mechanism behind the 2026-08-12 "failure":
    // two netlists identical in logic, connectivity and cell function
    // disagreed, and the first flops to diverge were async-reset ones, one
    // clock-to-Q after this line. Releasing half a cycle away from the edge
    // is what real bring-up does and what removes the ambiguity.
    rst_n = 1'b0;
    repeat (20) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    $display("[GLS] reset released at %t", $time);
  end

  // Model power-up: every flop gets a defined value at time 0. The generated
  // file explains why this is modelling silicon rather than hiding a problem.
`ifndef GLS_NO_POWERUP_INIT
// The deposit list names a hierarchical scope, so it changes when the DUT is
// not the tb's direct child (a padframe model in between adds a level).
`ifndef GLS_POWERUP_INIT
  `define GLS_POWERUP_INIT "gls_powerup_init.svh"
`endif
`include `GLS_POWERUP_INIT
`endif

  // ---- pass / fail -------------------------------------------------------
  // status_valid_o is the exit strobe driven from soc_ctrl; status_o[6:0] is
  // the exit value. This is precisely what a bring-up board would watch.
  always @(posedge clk) begin
    if (rst_n && status_valid) begin
      $display("[GLS] status_valid_o asserted at %t after %0d cycles, status_o = 0x%02h",
               $time, cycles, status);
      if (status == 7'd0) begin
        $display("### RESULT: EXIT SUCCESS — gate-level netlist booted and reported 0");
      end else begin
        $display("### RESULT: FAIL — exit value 0x%02h", status);
      end
      $finish;
    end
  end

  // Watchdog. A hang here is a real failure: the RTL reaches the exit register
  // in ~12 400 cycles, so anything approaching the limit means the netlist does
  // not do what the RTL did.
  always @(posedge clk) begin
    if (cycles > maxcycles) begin
      $display("[GLS] status_o = 0x%02h, status_valid_o = %b", status, status_valid);
      $display("### RESULT: FAIL — watchdog at %0d cycles without status_valid_o", cycles);
      $finish;
    end
  end

  // Progress, so a long run is visibly alive rather than apparently wedged.
  always @(posedge clk) begin
    if (cycles % 2000 == 0 && cycles > 0)
      $display("[GLS] %0d cycles, t=%t, sck=%b csb=%b sd=%h", cycles, $time,
               spi_sck, spi_csb, spi_sd);
  end

endmodule
