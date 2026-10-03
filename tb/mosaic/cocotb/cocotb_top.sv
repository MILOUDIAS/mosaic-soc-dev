// cocotb_top.sv — cocotb DUT wrapper for the MOSAIC multi-core cpu_subsystem.
//
// Same structure as mosaic_multicore_tb.sv, but driven by a Python cocotb test
// instead of an SV initial block. cocotb owns clk_i + rst_ni; this wrapper
// instantiates the generated `cpu_subsystem` (serial-core config from
// configs/mosaic_sim.yaml: serv, qerv, fazyrv) with per-hart OBI memories
// (`tb_obi_mem`), and exposes each hart's sentinel word + liveness flag as
// top-level outputs the Python test can read directly.

module cocotb_top
  import obi_pkg::*;
(
    input  logic        clk_i,
    input  logic        rst_ni,
    // Per-hart wake request (driven by the cocotb test in place of the TDU).
    // All three sim cores are workers (no TITAN), so each is held dormant out of
    // reset until its bit is pulsed here — exactly the TDU.core_wake_o path.
    input  logic [ 2:0] core_wake,
    output logic [31:0] sentinel0,
    output logic [31:0] sentinel1,
    output logic [31:0] sentinel2,
    output logic        alive0,
    output logic        alive1,
    output logic        alive2,
    // core_sleep_o per hart (what the TDU samples as CORE_STATUS): 1 while the
    // worker is still parked, 0 once woken.
    output logic        sleep0,
    output logic        sleep1,
    output logic        sleep2
);
  localparam int unsigned NH = 3;
  localparam int unsigned SENT_WIDX = 'h10;  // byte 0x40 → word index 0x10

  // DESCENDING [NH-1:0] ranges to match cpu_subsystem's per-hart array ports
  // (also [NUM_HARTS-1:0]); an ascending [NH] here would reverse the element
  // mapping on connection (hart 0 -> port index NH-1).
  logic      [  31:0] hart_id                                            [NH-1:0];
  obi_req_t           ireq                                               [NH-1:0];
  obi_resp_t          irsp                                               [NH-1:0];
  obi_req_t           dreq                                               [NH-1:0];
  obi_resp_t          drsp                                               [NH-1:0];
  logic      [  31:0] irq                                                [NH-1:0];
  logic      [NH-1:0] dbg;  // packed: matches cpu_subsystem debug_req_i
  logic      [NH-1:0] slp;  // packed: matches cpu_subsystem core_sleep_o

  assign dbg = '0;
  always_comb begin
    for (int i = 0; i < NH; i++) begin
      hart_id[i] = i[31:0];
      irq[i]     = '0;
    end
  end

  cpu_subsystem #(
      .NUM_HARTS(NH),
      .BOOT_ADDR('h180)
  ) dut (
      .clk_i            (clk_i),
      .rst_ni           (rst_ni),
      .hart_id_i        (hart_id),
      .core_instr_req_o (ireq),
      .core_instr_resp_i(irsp),
      .core_data_req_o  (dreq),
      .core_data_resp_i (drsp),
      .irq_i            (irq),
      .debug_req_i      (dbg),
      .core_wake_i      (core_wake),  // already packed [2:0] — connect directly
      .core_sleep_o     (slp)
  );

  // Per-hart instruction + data memories.
  tb_obi_mem im0 (
      .clk_i,
      .rst_ni,
      .req_i (ireq[0]),
      .resp_o(irsp[0])
  );
  tb_obi_mem dm0 (
      .clk_i,
      .rst_ni,
      .req_i (dreq[0]),
      .resp_o(drsp[0])
  );
  tb_obi_mem im1 (
      .clk_i,
      .rst_ni,
      .req_i (ireq[1]),
      .resp_o(irsp[1])
  );
  tb_obi_mem dm1 (
      .clk_i,
      .rst_ni,
      .req_i (dreq[1]),
      .resp_o(drsp[1])
  );
  tb_obi_mem im2 (
      .clk_i,
      .rst_ni,
      .req_i (ireq[2]),
      .resp_o(irsp[2])
  );
  tb_obi_mem dm2 (
      .clk_i,
      .rst_ni,
      .req_i (dreq[2]),
      .resp_o(drsp[2])
  );

  // Sentinels: the store target word in each hart's data memory.
  assign sentinel0 = dm0.mem[SENT_WIDX];
  assign sentinel1 = dm1.mem[SENT_WIDX];
  assign sentinel2 = dm2.mem[SENT_WIDX];

  // Liveness: did each hart issue any bus request after reset?
  logic [31:0] cnt0, cnt1, cnt2;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      cnt0 <= 0;
      cnt1 <= 0;
      cnt2 <= 0;
    end else begin
      if (ireq[0].req || dreq[0].req) cnt0 <= cnt0 + 1;
      if (ireq[1].req || dreq[1].req) cnt1 <= cnt1 + 1;
      if (ireq[2].req || dreq[2].req) cnt2 <= cnt2 + 1;
    end
  end
  assign alive0 = (cnt0 != 0);
  assign alive1 = (cnt1 != 0);
  assign alive2 = (cnt2 != 0);

  assign sleep0 = slp[0];
  assign sleep1 = slp[1];
  assign sleep2 = slp[2];

endmodule
