// idma_stub.sv — DMA-disabled stub of idma_xheep_wrapper, for the Icarus
// (event-driven) full-SoC wake-and-run demo. The pulp iDMA uses package-function
// param defaults (cf_math_pkg::idx_width) and other constructs Icarus can't parse,
// and the demo doesn't use DMA — so we replace the wrapper with this stub (same
// ports) and exclude the real iDMA sources from the Icarus filelist. All outputs
// tied off; the DMA register region is simply acked (never accessed by the demo).
module idma_xheep_wrapper #(
    parameter type reg_req_t = logic,
    parameter type reg_rsp_t = logic,
    parameter type obi_req_t = logic,
    parameter type obi_resp_t = logic,
    parameter type fifo_req_t = logic,
    parameter type fifo_resp_t = logic,
    parameter int unsigned GLOBAL_SLOT_NUM = 0,
    parameter int unsigned EXT_SLOT_NUM = 0
) (
    input logic clk_i,
    input logic rst_ni,
    input logic clk_gate_en_ni[core_v_mini_mcu_pkg::DMA_CH_NUM-1:0],
    input reg_req_t reg_req_i,
    output reg_rsp_t reg_rsp_o,
    output obi_req_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_read_req_o,
    input obi_resp_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_read_resp_i,
    output obi_req_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_write_req_o,
    input obi_resp_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_write_resp_i,
    output fifo_req_t [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_req_o,
    input fifo_resp_t [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_resp_i,
    input logic [GLOBAL_SLOT_NUM-1:0] global_trigger_slot_i,
    input logic [EXT_SLOT_NUM-1:0] ext_trigger_slot_i,
    input logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] ext_dma_stop_i,
    input logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_done_i,
    input dma_reg_pkg::dma_hw2reg_t [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] external_hw2reg_i,
    output logic dma_done_intr_o,
    output logic dma_window_intr_o,
    output logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] dma_ready_o,
    output logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] dma_done_o
);
  // Register region: ack any access (never targeted by the demo), return 0.
  always_comb begin
    reg_rsp_o = '0;
    reg_rsp_o.ready = 1'b1;
  end
  // No DMA bus activity, no FIFO traffic, no interrupts.
  assign dma_read_req_o    = '0;
  assign dma_write_req_o   = '0;
  assign hw_fifo_req_o     = '0;
  assign dma_done_intr_o   = 1'b0;
  assign dma_window_intr_o = 1'b0;
  assign dma_ready_o       = '1;  // idle/ready
  assign dma_done_o        = '0;
endmodule
