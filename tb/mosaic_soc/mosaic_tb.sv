// mosaic_tb.sv — diagnostic top for the MOSAIC full-SoC functional sim.
// Same boot/load flow as tb_top.sv, but with explicit visibility: it polls the
// SRAM sentinel the program writes and the soc_ctrl exit registers, and reports
// exactly what happened (executed? wrote sentinel? asserted exit?).
module mosaic_tb;
  localparam time CLK_HI = 5ns, CLK_LO = 5ns;
  localparam int RESET_WAIT = 50;

  logic clk = 1'b1, rst_n = 1'b0;
  logic boot_sel = 1'b0, exec_flash = 1'b0;
  wire exit_valid;
  logic [31:0] exit_value;
  wire jtag_tck, jtag_trst_n, jtag_tms, jtag_tdi, jtag_tdo;

  // Hierarchical handles into the SoC.
  `define RAM0 testharness_i.x_heep_system_i.core_v_mini_mcu_i.memory_subsystem_i.ram0_i.tc_ram_i.sram
  `define CMM testharness_i.x_heep_system_i.core_v_mini_mcu_i
  `define CPU `CMM.cpu_subsystem_i

  // Wake-demo tracking: did the TDU ever pulse core_wake for harts 1/2? (1-cycle pulse)
  logic woke1 = 0, woke2 = 0;
  always_ff @(posedge clk)
    if (rst_n) begin
      if (`CMM.core_wake[1]) woke1 <= 1'b1;
      if (`CMM.core_wake[2]) woke2 <= 1'b1;
    end

  // FazyRV (ATLAS) internal PC/state probe — trace pc_r evolution after wake to
  // see exactly how the serial PC collapses to 0.
  `define FZPC `CPU.cpu_1_0.i_core.i_fazyrv_core.i_fazyrv_pc
  `define FZCT `CPU.cpu_1_0.i_core.i_fazyrv_core.i_fazyrv_cntrl
  logic [31:0] fz_lastpc = 32'hFFFFFFFF;
  int unsigned fz_prints = 0;
  logic fz_trap_seen = 0;
  always_ff @(posedge clk)
    if (rst_n && woke1) begin
      if (`CPU.cpu_1_0.trap_o && !fz_trap_seen) begin
        $display("[fz] *** TRAP at pc_r=0x%08x  irq7=%0b  imem_dat=0x%08x", `FZPC.pc_r,
                 `CPU.cpu_1_0.irq_i[7], `CPU.cpu_1_0.wb_imem_dat);
        fz_trap_seen <= 1'b1;
      end
      if (`FZPC.pc_r != fz_lastpc && fz_prints < 80) begin
        $display("[fz] pc_r=0x%08x state=%0d stb=%0b ack=%0b trap=%0b irq7=%0b idat=0x%08x",
                 `FZPC.pc_r, `FZCT.state_r, `CPU.cpu_1_0.wb_imem_stb, `CPU.cpu_1_0.wb_imem_ack,
                 `CPU.cpu_1_0.trap_o, `CPU.cpu_1_0.irq_i[7], `CPU.cpu_1_0.wb_imem_dat);
        fz_lastpc <= `FZPC.pc_r;
        fz_prints <= fz_prints + 1;
      end
    end

  initial
    forever begin
      #CLK_HI clk = 1'b0;
      #CLK_LO clk = 1'b1;
    end

  initial begin
    rst_n = 1'b0;
    repeat (RESET_WAIT) @(posedge clk);
    #1 rst_n = 1'b1;
  end

  // firmware load + boot release (pure SV; reuses the DPI-free tb_util tasks)
  initial begin : load
    automatic string firmware;
    if (!$value$plusargs("firmware=%s", firmware)) begin
      $display("[mosaic_tb] no +firmware");
      $finish;
    end
    $display("[mosaic_tb] firmware = %s", firmware);
    testharness_i.load_flash_hex(firmware);
    wait (rst_n == 1'b1);
    repeat (RESET_WAIT) @(posedge clk);
    testharness_i.tb_loadHEX(firmware);
    #CLK_HI testharness_i.tb_set_exit_loop();
    $display("[mosaic_tb] %0t: programs loaded; @0x180=0x%08x @0x1000=0x%08x @0x2000=0x%08x",
             $time, `RAM0['h60], `RAM0['h400], `RAM0['h800]);
  end

  // success path
  always_ff @(posedge clk) begin
    if (exit_valid) begin
      $display("[mosaic_tb] exit_valid asserted, exit_value=%0d", exit_value);
      if (exit_value == 0) begin
        $display("EXIT SUCCESS");
        dump();
        $finish;
      end else begin
        dump();
        $fatal(1, "EXIT FAILURE: %0d", exit_value);
      end
    end
  end

  // watchdog + diagnostics
  int unsigned cyc = 0;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) cyc <= 0;
    else begin
      cyc <= cyc + 1;
      if (cyc == 60000) begin
        $display("[mosaic_tb] watchdog @%0t (no exit yet) — dumping state:", $time);
        dump();
        $fatal(1, "full-SoC watchdog expired without exit");
      end
    end
  end

  task dump;
    $display("  --- sentinels (RAM0) ---");
    $display("  TITAN @0x3000 = 0x%08x (expect 0xC0FFEE00)", `RAM0['hC00]);
    $display("  ATLAS @0x3004 = 0x%08x (expect 0xA71A5000)", `RAM0['hC01]);
    $display("  NANO  @0x3008 = 0x%08x (expect 0x4E414E00)", `RAM0['hC02]);
    $display("  --- TDU wake path ---");
    $display("  TDU core_wake pulsed: ATLAS(hart1)=%0d  NANO(hart2)=%0d", woke1, woke2);
    $display("  worker run-latch:     ATLAS=%0d  NANO=%0d", `CPU.core_run_1_0, `CPU.core_run_2_0);
    $display("  worker fetch_enable:  ATLAS=%0d  NANO=%0d", `CPU.fetch_enable_1_0,
             `CPU.fetch_enable_2_0);
    $display("  core_sleep[TITAN,ATLAS,NANO] = %0d %0d %0d", `CMM.core_sleep[0],
             `CMM.core_sleep[1], `CMM.core_sleep[2]);
  endtask

  testharness #(
      .JTAG_DPI(0),
      .USE_EXTERNAL_DEVICE_EXAMPLE(1),
      .CLK_FREQUENCY(100_000)
  ) testharness_i (
      .clk_i(clk),
      .rst_ni(rst_n),
      .boot_select_i(boot_sel),
      .execute_from_flash_i(exec_flash),
      .exit_valid_o(exit_valid),
      .exit_value_o(exit_value),
      .jtag_tck_i(jtag_tck),
      .jtag_trst_ni(jtag_trst_n),
      .jtag_tms_i(jtag_tms),
      .jtag_tdi_i(jtag_tdi),
      .jtag_tdo_o(jtag_tdo)
  );
endmodule
