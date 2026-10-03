// Copyright MOSAIC-SoC Contributors
// SPDX-License-Identifier: SHL-0.51
//
// Configuration-sized Core-Local Interruptor for MOSAIC multi-hart systems.
// Provides one software interrupt and one 64-bit timer comparator per hart.
// The register layout follows the conventional RISC-V CLINT offsets so a
// normal bare-metal/RTOS port can use it without a MOSAIC-specific protocol.

`include "common_cells/assertions.svh"

module mosaic_clint #(
    parameter int unsigned NUM_HARTS = 1
) (
    input  logic                    clk_i,
    input  logic                    rst_ni,
    input  reg_pkg::reg_req_t       reg_req_i,
    output reg_pkg::reg_rsp_t       reg_rsp_o,
    output logic [NUM_HARTS-1:0]    software_irq_o,
    output logic [NUM_HARTS-1:0]    timer_irq_o,
    output logic [63:0]              mtime_o
);

  localparam logic [31:0] MsipBase     = 32'h0000;
  localparam logic [31:0] MtimecmpBase = 32'h4000;
  localparam logic [31:0] MtimeLo      = 32'hBFF8;
  localparam logic [31:0] MtimeHi      = 32'hBFFC;
  localparam int unsigned HartIdxW     = NUM_HARTS > 1 ? $clog2(NUM_HARTS) : 1;

  logic [NUM_HARTS-1:0] msip_q;
  logic [63:0] mtime_q;
  logic [63:0] mtimecmp_q [NUM_HARTS];

  logic        req_valid;
  logic        req_write;
  logic [31:0] req_addr;
  logic [31:0] req_wdata;
  logic        msip_region;
  logic        mtimecmp_region;
  logic [HartIdxW-1:0] msip_hart;
  logic [HartIdxW-1:0] mtimecmp_hart;
  logic        mtimecmp_high;
  logic [31:0] rdata;
  logic        error;

  assign req_valid = reg_req_i.valid;
  assign req_write = reg_req_i.write;
  assign req_addr  = reg_req_i.addr;
  assign req_wdata = reg_req_i.wdata;

  // MsipBase is zero, so omitting the tautological unsigned lower-bound
  // comparison keeps synthesis and lint quiet.  Reject unaligned aliases:
  // every architected CLINT word is naturally 32-bit aligned.
  assign msip_region = (req_addr < MsipBase + 4*NUM_HARTS) &&
                       (req_addr[1:0] == 2'b00);
  assign msip_hart = HartIdxW'((req_addr - MsipBase) >> 2);

  assign mtimecmp_region = (req_addr >= MtimecmpBase) &&
                           (req_addr < MtimecmpBase + 8*NUM_HARTS) &&
                           (req_addr[1:0] == 2'b00);
  assign mtimecmp_hart = HartIdxW'((req_addr - MtimecmpBase) >> 3);
  assign mtimecmp_high = req_addr[2];

  always_comb begin
    rdata = '0;
    error = 1'b0;
    if (req_valid) begin
      if (req_write && reg_req_i.wstrb != 4'hF) begin
        error = 1'b1;
      end else if (msip_region) begin
        rdata = {31'b0, msip_q[msip_hart]};
      end else if (mtimecmp_region) begin
        rdata = mtimecmp_high ? mtimecmp_q[mtimecmp_hart][63:32]
                               : mtimecmp_q[mtimecmp_hart][31:0];
      end else if (req_addr == MtimeLo) begin
        rdata = mtime_q[31:0];
      end else if (req_addr == MtimeHi) begin
        rdata = mtime_q[63:32];
      end else begin
        error = 1'b1;
      end
    end
  end

  assign reg_rsp_o = '{ready: req_valid, error: error, rdata: rdata};
  assign software_irq_o = msip_q;
  assign mtime_o = mtime_q;

  for (genvar hart = 0; hart < NUM_HARTS; hart++) begin : gen_timer_irq
    assign timer_irq_o[hart] = (mtime_q >= mtimecmp_q[hart]);
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      msip_q  <= '0;
      mtime_q <= '0;
      for (int hart = 0; hart < NUM_HARTS; hart++) begin
        mtimecmp_q[hart] <= 64'hFFFF_FFFF_FFFF_FFFF;
      end
    end else begin
      mtime_q <= mtime_q + 64'd1;
      if (req_valid && req_write && !error) begin
        if (msip_region) begin
          msip_q[msip_hart] <= req_wdata[0];
        end else if (mtimecmp_region) begin
          if (mtimecmp_high)
            mtimecmp_q[mtimecmp_hart][63:32] <= req_wdata;
          else
            mtimecmp_q[mtimecmp_hart][31:0] <= req_wdata;
        end else if (req_addr == MtimeLo) begin
          mtime_q[31:0] <= req_wdata;
        end else if (req_addr == MtimeHi) begin
          mtime_q[63:32] <= req_wdata;
        end
      end
    end
  end

  `ASSERT_INIT(NumHartsPositive, NUM_HARTS >= 1)
  `ASSERT_INIT(NumHartsSupported, NUM_HARTS <= 16)

endmodule : mosaic_clint
