// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// iDMA register frontend + ND midends + rw_obi backends adapted to x-heep.
// The stream count defaults to the configured x-heep DMA master-port count.
// Each stream gets an independent execution lane, a 0x200-byte programming
// window, and a one-to-one mapping onto an OBI read/write master pair. Software
// launches stream n by reading NEXT_ID[n] in programming window n.
//
// iDMA 0.6.5's rw_obi backend does not implement bus-error recovery: selecting
// ERROR_HANDLING triggers a fatal elaboration error, and its action enum has
// CONTINUE/ABORT only (no REPLAY). Unsupported ERROR_CAP values fail loudly.

`include "idma/typedef.svh"
`include "obi/typedef.svh"

module idma_xheep_wrapper #(
    parameter type reg_req_t = logic,
    parameter type reg_rsp_t = logic,
    parameter type obi_req_t = logic,
    parameter type obi_resp_t = logic,
    parameter type fifo_req_t = logic,
    parameter type fifo_resp_t = logic,
    parameter int unsigned GLOBAL_SLOT_NUM = 0,
    parameter int unsigned EXT_SLOT_NUM = 0,
    parameter int unsigned NUM_STREAMS =
        core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS,
    parameter idma_pkg::error_cap_e ERROR_CAP =
        idma_pkg::NO_ERROR_HANDLING
) (
    input logic clk_i,
    input logic rst_ni,

    input logic clk_gate_en_ni [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0],

    input  reg_req_t reg_req_i,
    output reg_rsp_t reg_rsp_o,

    output obi_req_t  [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_read_req_o,
    input  obi_resp_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_read_resp_i,
    output obi_req_t  [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_write_req_o,
    input  obi_resp_t [core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS-1:0] dma_write_resp_i,

    output fifo_req_t  [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_req_o,
    input  fifo_resp_t [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_resp_i,

    input logic [GLOBAL_SLOT_NUM-1:0] global_trigger_slot_i,
    input logic [EXT_SLOT_NUM-1:0]   ext_trigger_slot_i,
    input logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] ext_dma_stop_i,
    input logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] hw_fifo_done_i,
    input dma_reg_pkg::dma_hw2reg_t [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0]
        external_hw2reg_i,

    output logic dma_done_intr_o,
    output logic dma_window_intr_o,
    output logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] dma_ready_o,
    output logic [core_v_mini_mcu_pkg::DMA_CH_NUM-1:0] dma_done_o
);

  import idma_pkg::*;

  localparam int unsigned DataWidth   = 32;
  localparam int unsigned AddrWidth   = 32;
  localparam int unsigned UserWidth   = 1;
  localparam int unsigned AxiIdWidth  = 1;
  localparam int unsigned TFLenWidth  = 32;
  localparam int unsigned NumDim      = 3;
  localparam int unsigned RepWidth    = 32;
  localparam int unsigned StrideWidth = 32;
  localparam int unsigned StrbWidth   = DataWidth / 8;
  localparam int unsigned NumStreams  = NUM_STREAMS;
  localparam int unsigned NumRegs     = NumStreams;
  localparam int unsigned StreamWidth = cf_math_pkg::idx_width(NumStreams);
  localparam int unsigned RegWindowLsb = 9;
  localparam logic [8:0] OwnerClaimOffset   = 9'h180;
  localparam logic [8:0] OwnerReleaseOffset = 9'h184;
  localparam logic [8:0] OwnerOffset        = 9'h188;

  typedef logic [AddrWidth-1:0]   addr_t;
  typedef logic [DataWidth-1:0]   data_t;
  typedef logic [StrbWidth-1:0]   strb_t;
  typedef logic [AxiIdWidth-1:0]  id_t;
  typedef logic [TFLenWidth-1:0]  tf_len_t;
  typedef logic [RepWidth-1:0]    reps_t;
  typedef logic [StrideWidth-1:0] strides_t;
  typedef logic [31:0]            cnt_t;
  typedef logic [StreamWidth-1:0] stream_idx_t;

  `IDMA_TYPEDEF_FULL_REQ_T(idma_req_t, id_t, addr_t, tf_len_t)
  `IDMA_TYPEDEF_FULL_RSP_T(idma_rsp_t, addr_t)
  `IDMA_TYPEDEF_D_REQ_T(idma_d_req_t, reps_t, strides_t)
  `IDMA_TYPEDEF_ND_REQ_T(idma_nd_req_t, idma_req_t, idma_d_req_t)

  `OBI_TYPEDEF_MINIMAL_A_OPTIONAL(obi_a_optional_t)
  `OBI_TYPEDEF_MINIMAL_R_OPTIONAL(obi_r_optional_t)
  `OBI_TYPEDEF_TYPE_A_CHAN_T(obi_a_chan_t, addr_t, data_t, strb_t, id_t,
                             obi_a_optional_t)
  `OBI_TYPEDEF_TYPE_R_CHAN_T(obi_r_chan_t, data_t, id_t, obi_r_optional_t)
  `OBI_TYPEDEF_REQ_T(idma_obi_req_t, obi_a_chan_t)
  `OBI_TYPEDEF_RSP_T(idma_obi_rsp_t, obi_r_chan_t)

  typedef struct packed {obi_a_chan_t a_chan;} obi_read_meta_channel_t;
  typedef struct packed {obi_read_meta_channel_t obi;} read_meta_channel_t;
  typedef struct packed {obi_a_chan_t a_chan;} obi_write_meta_channel_t;
  typedef struct packed {obi_write_meta_channel_t obi;} write_meta_channel_t;

  reg_req_t [NumRegs-1:0] ctrl_req;
  reg_rsp_t [NumRegs-1:0] ctrl_rsp;
  stream_idx_t reg_stream_idx;
  logic owner_access;
  cnt_t [NumStreams-1:0] owner_token;
  assign reg_stream_idx = reg_req_i.addr[RegWindowLsb +: StreamWidth];
  assign owner_access = (reg_req_i.addr[8:0] == OwnerClaimOffset) |
                        (reg_req_i.addr[8:0] == OwnerReleaseOffset) |
                        (reg_req_i.addr[8:0] == OwnerOffset);

  // Give each stream a private descriptor bank. The generated register block
  // consumes address bits [8:0], so consecutive banks live at 0x200-byte
  // intervals. This removes the global descriptor-state race between harts.
  always_comb begin
    reg_rsp_o       = '0;
    reg_rsp_o.ready = 1'b1;
    reg_rsp_o.error = reg_req_i.valid;
    for (int unsigned s = 0; s < NumRegs; s++) begin
      ctrl_req[s]       = reg_req_i;
      ctrl_req[s].valid = reg_req_i.valid & ~owner_access &
                          (reg_stream_idx == stream_idx_t'(s));
      if (reg_stream_idx == stream_idx_t'(s)) begin
        if (owner_access) begin
          reg_rsp_o.error = 1'b0;
          reg_rsp_o.rdata = owner_token[s];
        end else begin
          reg_rsp_o = ctrl_rsp[s];
        end
      end
    end
  end

  // Atomic cooperative ownership primitive. A nonzero token claims an idle
  // stream in one MMIO write; only the matching token can release it. The
  // driver checks OWNER before programming the stream's private descriptor
  // bank, preventing multi-hart descriptor corruption without ISA atomics.
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      owner_token <= '0;
    end else if (reg_req_i.valid && reg_req_i.write) begin
      for (int unsigned s = 0; s < NumStreams; s++) begin
        if (reg_stream_idx == stream_idx_t'(s)) begin
          if ((reg_req_i.addr[8:0] == OwnerClaimOffset) &&
              (reg_req_i.wdata != '0) && (owner_token[s] == '0)) begin
            owner_token[s] <= reg_req_i.wdata;
          end else if ((reg_req_i.addr[8:0] == OwnerReleaseOffset) &&
                       (owner_token[s] == reg_req_i.wdata)) begin
            owner_token[s] <= '0;
          end
        end
      end
    end
  end

  idma_nd_req_t fe_nd_req;
  logic         fe_nd_req_valid, fe_nd_req_ready;
  stream_idx_t  stream_idx;
  cnt_t         selected_next_id;

  idma_nd_req_t [NumStreams-1:0] lane_nd_req;
  logic         [NumStreams-1:0] lane_nd_req_valid, lane_nd_req_ready;
  cnt_t         [NumStreams-1:0] next_id, completed_id;
  idma_pkg::idma_busy_t [NumStreams-1:0] be_busy;
  logic         [NumStreams-1:0] nd_busy;
  logic         [NumStreams-1:0] transfer_done;

  idma_reg32_3d #(
    .NumRegs    (NumRegs),
    .NumStreams (NumStreams),
    .reg_req_t  (reg_req_t),
    .reg_rsp_t  (reg_rsp_t),
    .dma_req_t  (idma_nd_req_t)
  ) i_reg_fe (
    .clk_i,
    .rst_ni,
    .dma_ctrl_req_i (ctrl_req),
    .dma_ctrl_rsp_o (ctrl_rsp),
    .dma_req_o      (fe_nd_req),
    .req_valid_o    (fe_nd_req_valid),
    .req_ready_i    (fe_nd_req_ready),
    .next_id_i      (selected_next_id),
    .stream_idx_o   (stream_idx),
    .done_id_i      (completed_id),
    .busy_i         (be_busy),
    .midend_busy_i  (nd_busy)
  );

  // The frontend produces one descriptor plus a stream index. Route it to the
  // selected execution lane and return that lane's backpressure and next ID.
  always_comb begin
    selected_next_id = next_id[stream_idx];
    fe_nd_req_ready   = 1'b0;
    for (int unsigned s = 0; s < NumStreams; s++) begin
      lane_nd_req[s]       = fe_nd_req;
      lane_nd_req_valid[s] = fe_nd_req_valid & (stream_idx == stream_idx_t'(s));
      if (stream_idx == stream_idx_t'(s)) begin
        fe_nd_req_ready = lane_nd_req_ready[s];
      end
    end
  end

  localparam logic [NumDim-1:0][31:0] RepWidths = '{default: RepWidth};

  for (genvar s = 0; s < NumStreams; s++) begin : gen_stream
    idma_rsp_t nd_rsp;
    logic      nd_rsp_valid, nd_rsp_ready;
    idma_req_t burst_req;
    logic      burst_req_valid, burst_req_ready;
    idma_rsp_t burst_rsp;
    logic      burst_rsp_valid, burst_rsp_ready;
    idma_obi_req_t obi_read_req, obi_write_req;
    idma_obi_rsp_t obi_read_rsp, obi_write_rsp;
    idma_pkg::idma_eh_req_t eh_req;
    logic eh_req_valid, eh_req_ready;
    obi_req_t rd_req_xheep, wr_req_xheep;
    obi_resp_t rd_resp_xheep, wr_resp_xheep;

    idma_transfer_id_gen #(
      .IdWidth (32)
    ) i_id_gen (
      .clk_i,
      .rst_ni,
      .issue_i     (lane_nd_req_valid[s] & lane_nd_req_ready[s]),
      .retire_i    (nd_rsp_valid & nd_rsp_ready),
      .next_o      (next_id[s]),
      .completed_o (completed_id[s])
    );

    idma_nd_midend #(
      .NumDim        (NumDim),
      .addr_t        (addr_t),
      .idma_req_t    (idma_req_t),
      .idma_rsp_t    (idma_rsp_t),
      .idma_nd_req_t (idma_nd_req_t),
      .RepWidths     (RepWidths)
    ) i_nd_midend (
      .clk_i,
      .rst_ni,
      .nd_req_i          (lane_nd_req[s]),
      .nd_req_valid_i    (lane_nd_req_valid[s]),
      .nd_req_ready_o    (lane_nd_req_ready[s]),
      .nd_rsp_o          (nd_rsp),
      .nd_rsp_valid_o    (nd_rsp_valid),
      .nd_rsp_ready_i    (nd_rsp_ready),
      .burst_req_o       (burst_req),
      .burst_req_valid_o (burst_req_valid),
      .burst_req_ready_i (burst_req_ready),
      .burst_rsp_i       (burst_rsp),
      .burst_rsp_valid_i (burst_rsp_valid),
      .burst_rsp_ready_o (burst_rsp_ready),
      .busy_o            (nd_busy[s])
    );
    assign nd_rsp_ready = 1'b1;

    idma_backend_rw_obi #(
      .DataWidth           (DataWidth),
      .AddrWidth           (AddrWidth),
      .UserWidth           (UserWidth),
      .AxiIdWidth          (AxiIdWidth),
      .NumAxInFlight       (3),
      .BufferDepth         (3),
      .TFLenWidth          (TFLenWidth),
      .MemSysDepth         (0),
      .RAWCouplingAvail    (1'b0),
      .MaskInvalidData     (1'b1),
      .HardwareLegalizer   (1'b1),
      .RejectZeroTransfers (1'b1),
      .ErrorCap            (ERROR_CAP),
      .idma_req_t          (idma_req_t),
      .idma_rsp_t          (idma_rsp_t),
      .idma_eh_req_t       (idma_pkg::idma_eh_req_t),
      .idma_busy_t         (idma_pkg::idma_busy_t),
      .obi_req_t           (idma_obi_req_t),
      .obi_rsp_t           (idma_obi_rsp_t),
      .read_meta_channel_t (read_meta_channel_t),
      .write_meta_channel_t(write_meta_channel_t)
    ) i_backend (
      .clk_i,
      .rst_ni,
      .testmode_i      (1'b0),
      .idma_req_i      (burst_req),
      .req_valid_i     (burst_req_valid),
      .req_ready_o     (burst_req_ready),
      .idma_rsp_o      (burst_rsp),
      .rsp_valid_o     (burst_rsp_valid),
      .rsp_ready_i     (burst_rsp_ready),
      .idma_eh_req_i   (eh_req),
      .eh_req_valid_i  (eh_req_valid),
      .eh_req_ready_o  (eh_req_ready),
      .obi_read_req_o  (obi_read_req),
      .obi_read_rsp_i  (obi_read_rsp),
      .obi_write_req_o (obi_write_req),
      .obi_write_rsp_i (obi_write_rsp),
      .busy_o          (be_busy[s])
    );
    assign eh_req       = '0;
    assign eh_req_valid = 1'b0;

    // Local structs avoid member access on packed-array elements in older
    // Older simulators do not support member access on packed-array elements.
    assign rd_resp_xheep = dma_read_resp_i[s];
    assign wr_resp_xheep = dma_write_resp_i[s];
    always_comb begin
      rd_req_xheep       = '0;
      rd_req_xheep.req   = obi_read_req.req;
      rd_req_xheep.we    = obi_read_req.a.we;
      rd_req_xheep.be    = obi_read_req.a.be;
      rd_req_xheep.addr  = obi_read_req.a.addr;
      rd_req_xheep.wdata = obi_read_req.a.wdata;

      obi_read_rsp         = '0;
      obi_read_rsp.gnt     = rd_resp_xheep.gnt;
      obi_read_rsp.rvalid  = rd_resp_xheep.rvalid;
      obi_read_rsp.r.rdata = rd_resp_xheep.rdata;

      wr_req_xheep       = '0;
      wr_req_xheep.req   = obi_write_req.req;
      wr_req_xheep.we    = obi_write_req.a.we;
      wr_req_xheep.be    = obi_write_req.a.be;
      wr_req_xheep.addr  = obi_write_req.a.addr;
      wr_req_xheep.wdata = obi_write_req.a.wdata;

      obi_write_rsp         = '0;
      obi_write_rsp.gnt     = wr_resp_xheep.gnt;
      obi_write_rsp.rvalid  = wr_resp_xheep.rvalid;
      obi_write_rsp.r.rdata = wr_resp_xheep.rdata;
    end

    assign dma_read_req_o[s]  = rd_req_xheep;
    assign dma_write_req_o[s] = wr_req_xheep;
    assign transfer_done[s]   = nd_rsp_valid & nd_rsp_ready;
    assign dma_ready_o[s]     = ~(nd_busy[s] | (|be_busy[s]));
    assign dma_done_o[s]      = transfer_done[s];
  end

  assign dma_done_intr_o   = |transfer_done;
  assign dma_window_intr_o = 1'b0;

  // Legacy x-heep exposes DMA_CH_NUM status bits. Streams occupy the first
  // NUM_STREAMS bits; channels without a physical stream remain inactive.
  for (genvar i = NumStreams; i < core_v_mini_mcu_pkg::DMA_CH_NUM; i++) begin : gen_unused_ch
    assign dma_ready_o[i] = 1'b0;
    assign dma_done_o[i]  = 1'b0;
  end

  for (genvar i = NumStreams;
       i < core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS; i++) begin : gen_unused_rw
    assign dma_read_req_o[i]  = '0;
    assign dma_write_req_o[i] = '0;
  end
  for (genvar i = 0; i < core_v_mini_mcu_pkg::DMA_CH_NUM; i++) begin : gen_fifo_tie
    assign hw_fifo_req_o[i] = '0;
  end

`ifndef SYNTHESIS
  initial begin : check_configuration
    assert (NumStreams > 0 && NumStreams <= 16)
      else $fatal(1, "iDMA NUM_STREAMS must be in [1, 16]");
    assert (NumStreams <= core_v_mini_mcu_pkg::DMA_NUM_MASTER_PORTS)
      else $fatal(1, "iDMA needs one DMA master port per stream");
    assert (NumStreams <= core_v_mini_mcu_pkg::DMA_CH_NUM)
      else $fatal(1, "iDMA streams exceed x-heep DMA status channels");
    assert (ERROR_CAP == idma_pkg::NO_ERROR_HANDLING)
      else $fatal(1,
        "iDMA 0.6.5 rw_obi has no bus-error handling; CONTINUE/ABORT/REPLAY unavailable");
  end
`endif

endmodule : idma_xheep_wrapper
