// tdu_soc_tb_top.sv — SoC-level test top for the Task Dispatch Unit.
//
// The block-level TB (hw/tdu/tb/tdu_tb.sv) drives the TDU with bare register
// offsets (0x00..). This top instead reproduces the TDU's *SoC integration* —
// the address-window reg-bus tap from ao_peripheral_subsystem.sv.tpl — and lets
// a cocotb test access the TDU through its real SoC address
// (TDU_START_ADDRESS = 0x200A0000), inject core sleep status, and observe the
// wake pulses + IRQ. This catches integration bugs the block TB can't (address
// decode, status/wake wiring) — see tb/tdu/soc/README.md.
//
// TAP_SUBTRACT_BASE selects the addressing the tap presents to the TDU:
//   0 = pass the full SoC address through (as the original template did)
//   1 = subtract TDU_START_ADDRESS so the TDU sees its register offsets

module tdu_soc_tb_top #(
    parameter bit TAP_SUBTRACT_BASE = 1'b1,
    parameter int unsigned NUM_HARTS = 7
) (
    input  logic                                      clk_i,
    input  logic                                      rst_ni,
    // flat register bus (cocotb drives this with full SoC addresses)
    input  logic [                              31:0] reg_addr_i,
    input  logic [                              31:0] reg_wdata_i,
    input  logic                                      reg_write_i,
    input  logic                                      reg_valid_i,
    output logic                                      reg_ready_o,
    output logic [                              31:0] reg_rdata_o,
    // per-hart core status injection + observation
    input  logic [NUM_HARTS-1:0] core_sleep_i,
    output logic [NUM_HARTS-1:0] core_wake_o,
    output logic [NUM_HARTS-1:0] core_park_o,
    output logic                                      tdu_irq_o
);
  // Keep this integration test independent of whichever configuration last
  // rendered core_v_mini_mcu_pkg.sv into the source tree.
  localparam logic [31:0] TDU_START_ADDRESS = 32'h200A_0000;
  localparam logic [31:0] TDU_END_ADDRESS   = TDU_START_ADDRESS + 32'h1000;

  reg_pkg::reg_req_t soc_req;
  reg_pkg::reg_rsp_t soc_rsp;
  always_comb begin
    soc_req       = '0;
    soc_req.addr  = reg_addr_i;
    soc_req.wdata = reg_wdata_i;
    soc_req.write = reg_write_i;
    soc_req.wstrb = 4'hF;
    soc_req.valid = reg_valid_i;
  end
  assign reg_ready_o = soc_rsp.ready;
  assign reg_rdata_o = soc_rsp.rdata;

  // ── Reproduce the AO reg-bus TDU tap (ao_peripheral_subsystem.sv.tpl) ──
  logic              tdu_select;
  reg_pkg::reg_req_t tdu_req;
  reg_pkg::reg_rsp_t tdu_rsp;

  assign tdu_select = (soc_req.addr >= TDU_START_ADDRESS) && (soc_req.addr < TDU_END_ADDRESS);
  assign tdu_req.valid = soc_req.valid & tdu_select;
  assign tdu_req.write = soc_req.write;
  assign tdu_req.wstrb = soc_req.wstrb;
  assign tdu_req.addr = TAP_SUBTRACT_BASE ? (soc_req.addr - TDU_START_ADDRESS) : soc_req.addr;
  assign tdu_req.wdata = soc_req.wdata;

  // When not addressing the TDU, behave like a benign always-ready slave.
  assign soc_rsp = tdu_select ? tdu_rsp : '{ready: 1'b1, error: 1'b0, rdata: 32'h0};

  // core_running is the inverse of core_sleep (as wired in core_v_mini_mcu.sv).
  logic [NUM_HARTS-1:0] core_running;
  assign core_running = ~core_sleep_i;

  tdu #(
      .NUM_HARTS(NUM_HARTS)
  ) tdu_i (
      .clk_i,
      .rst_ni,
      .reg_req_i     (tdu_req),
      .reg_rsp_o     (tdu_rsp),
      .core_running_i(core_running),
      .core_sleep_i  (core_sleep_i),
      .core_wake_o   (core_wake_o),
      .core_park_o   (core_park_o),
      .tdu_irq_o     (tdu_irq_o)
  );
endmodule
