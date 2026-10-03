// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// tdu.sv — Task Dispatch Unit.
//
// A small (<100 GE) memory-mapped hardware block that assists the TITAN core
// (running FreeRTOS) with task dispatch to the heterogeneous ATLAS/NANO
// cores. It provides:
//   - an 8-deep task descriptor FIFO (push from TITAN, pop by TITAN/hw)
//   - per-core wake pulses (a sleeping ATLAS/NANO core is woken when a task
//     is enqueued with its core_hint, or by explicit software request)
//   - a per-core CPI estimate array (software-updated, read by the scheduler)
//   - a core status mirror (running/sleep) sampled from each SCI wrapper
//   - an active-hart-cycles accumulator (workload proxy, NOT energy)
//   - a scheduling-mode register (static / dynamic / power-aware)
//
// Placement policy intentionally runs on TITAN: the TDU records the selected
// mode and supplies CPI/activity telemetry, while every descriptor carries the
// final concrete hart chosen by software.  The hardware never silently
// rewrites core_hint.
//
// The module exposes a register-interface slave (reg_req_t/reg_rsp_t) so it
// drops into the always-on (AO) peripheral reg bus alongside the other x-heep
// peripherals. The register decode is hand-coded (no regtool dependency).

`include "common_cells/assertions.svh"

module tdu #(
    parameter int unsigned NUM_HARTS = 7,
    parameter tdu_pkg::sched_mode_e RESET_SCHED_MODE = tdu_pkg::SCHED_STATIC
) (
    input  logic clk_i,
    input  logic rst_ni,

    // Register bus slave (AO peripheral reg bus)
    input  reg_pkg::reg_req_t  reg_req_i,
    output reg_pkg::reg_rsp_t  reg_rsp_o,

    // Per-core status (sampled from each SCI wrapper / cpu_subsystem)
    input  logic [NUM_HARTS-1:0] core_running_i,  // 1 = core currently executing
    input  logic [NUM_HARTS-1:0] core_sleep_i,    // 1 = core in WFI/sleep

    // Per-core wake pulses (1-cycle, edge-triggered to each core's irq/wake)
    output logic [NUM_HARTS-1:0] core_wake_o,

    // Per-core park pulses.  Software writes PARK_REQ after a worker has
    // committed its completion/result stores.  The cpu subsystem clears the
    // corresponding run latch so the worker can be dispatched repeatedly.
    output logic [NUM_HARTS-1:0] core_park_o,

    // Event interrupt to the TITAN core (asserted on task enqueue)
    output logic                 tdu_irq_o
);

  import reg_pkg::*;
  import tdu_pkg::*;

  localparam int unsigned NumHartsW = $clog2(NUM_HARTS+1);
  localparam int unsigned CpiWords  = NUM_HARTS;
  localparam int unsigned CountW    = $clog2(TASK_QUEUE_DEPTH+1);
  localparam logic [CountW-1:0]    CountFull  = CountW'(TASK_QUEUE_DEPTH);
  localparam logic [NumHartsW-1:0] CpiWordsW  = NumHartsW'(CpiWords);

  // ── Register storage ────────────────────────────────────────────
  sched_mode_e          sched_mode_q, sched_mode_d;
  logic [NUM_HARTS-1:0] wake_mask_q, wake_mask_d;
  logic [31:0]          active_hart_cycles_q;
  logic                 active_hart_cycles_clear;
  logic [32:0]          active_hart_cycles_sum;
  logic [31:0]          cpi_est_q [CpiWords];
  logic [31:0]          cpi_est_d [CpiWords];
  logic                 cpi_we [CpiWords];

  // ── Task FIFO (8-deep circular) ─────────────────────────────────
  task_desc_t task_mem [TASK_QUEUE_DEPTH-1:0];
  logic [$clog2(TASK_QUEUE_DEPTH)-1:0] wr_ptr_q, rd_ptr_q;
  logic [$clog2(TASK_QUEUE_DEPTH+1)-1:0] count_q, count_d;
  logic       full, empty;
  logic       push, pop;
  task_desc_t push_data;
  task_desc_t pop_data;

  assign full  = (count_q == CountFull);
  assign empty = (count_q == 0);

  // 33-bit sum so the carry out of bit 31 is visible and the accumulator can
  // saturate instead of wrapping (see the always_ff below).
  assign active_hart_cycles_sum =
      {1'b0, active_hart_cycles_q} + 33'($countones(core_running_i));

  // ── Bus request decode ──────────────────────────────────────────
  // The reg bus delivers word-aligned addresses in reg_req_i.addr. Only
  // word accesses are supported; sub-word writes are treated as errors.
  logic        req_valid;
  logic        req_write;
  logic [31:0] req_addr;
  logic [31:0] req_wdata;
  logic        addr_in_range;
  logic        cpi_region;
  logic [31:0] cpi_off;
  logic [NumHartsW-1:0] cpi_idx;

  assign req_valid = reg_req_i.valid;
  assign req_write = reg_req_i.write;
  assign req_addr  = reg_req_i.addr;
  assign req_wdata = reg_req_i.wdata;

  // CPI estimate array region: [TDU_CPI_EST_BASE_OFFSET .. +4*NUM_HARTS)
  assign cpi_region = (req_addr[31:0] >= TDU_CPI_EST_BASE_OFFSET) &&
                      (req_addr[31:0] <  TDU_CPI_EST_BASE_OFFSET + 4*CpiWords);
  assign cpi_off = (req_addr - TDU_CPI_EST_BASE_OFFSET) >> 2;
  assign cpi_idx = cpi_off[NumHartsW-1:0];

  // ── Read data mux ───────────────────────────────────────────────
  logic [31:0] rdata_d;
  logic        error_d;
  logic        ready_d;

  always_comb begin
    // Defaults
    rdata_d        = 32'h0;
    error_d        = 1'b0;
    ready_d        = 1'b0;
    sched_mode_d   = sched_mode_q;
    wake_mask_d    = wake_mask_q;
    active_hart_cycles_clear = 1'b0;
    push           = 1'b0;
    pop            = 1'b0;
    push_data      = task_desc_t'('0);
    for (int i = 0; i < CpiWords; i++) cpi_est_d[i] = cpi_est_q[i];
    for (int i = 0; i < CpiWords; i++) cpi_we[i]    = 1'b0;

    if (req_valid) begin
      ready_d = 1'b1;
      if (req_write && reg_req_i.wstrb != 4'hF) begin
        error_d = 1'b1;
      end else if (cpi_region) begin
        // Per-core CPI estimate array (RW)
        if (req_write) begin
          if (cpi_idx < CpiWordsW) begin
            cpi_we[cpi_idx]  = 1'b1;
            cpi_est_d[cpi_idx] = req_wdata;
          end else begin
            error_d = 1'b1;
          end
        end else begin
          if (cpi_idx < CpiWordsW) begin
            rdata_d = cpi_est_q[cpi_idx];
          end else begin
            error_d = 1'b1;
          end
        end
      end else begin
        unique case (req_addr)
          TDU_CORE_STATUS_OFFSET: begin
            if (req_write) error_d = 1'b1;  // RO
            // Stable software ABI: running in [15:0], sleeping in [31:16],
            // independent of the generated hart count.
            else begin
              rdata_d[NUM_HARTS-1:0] = core_running_i;
              rdata_d[16 +: NUM_HARTS] = core_sleep_i;
            end
          end

          TDU_SCHED_MODE_OFFSET: begin
            if (req_write) begin
              if (req_wdata[1:0] <= SCHED_POWER_AWARE)
                sched_mode_d = sched_mode_e'(req_wdata[1:0]);
              else
                error_d = 1'b1;
            end else begin
              rdata_d = {30'h0, sched_mode_q};
            end
          end

          TDU_WAKE_MASK_OFFSET: begin
            if (req_write)
              wake_mask_d = req_wdata[NUM_HARTS-1:0];
            else
              rdata_d = {{(32-NUM_HARTS){1'b0}}, wake_mask_q};
          end

          TDU_WAKE_REQ_OFFSET: begin
            // Write-1-to-set a one-cycle wake pulse on selected cores.
            // Reads return 0.
            if (req_write) begin
              // handled in wake pulse logic below via wake_req_pulse
            end
            rdata_d = 32'h0;
          end

          TDU_PARK_REQ_OFFSET: begin
            // Write-1-to-set a one-cycle park pulse on selected cores.
            // Reads return zero.  The pulse is generated below from the
            // accepted request, just like WAKE_REQ.
            rdata_d = 32'h0;
          end

          TDU_TASK_PUSH_OFFSET: begin
            if (req_write) begin
              if (!full) begin
                push      = 1'b1;
                push_data = task_desc_t'(req_wdata);
              end else begin
                error_d = 1'b1;  // queue full
              end
            end else begin
              rdata_d = 32'h0;  // WO
            end
          end

          TDU_TASK_POP_OFFSET: begin
            if (!req_write) begin
              if (!empty) begin
                pop      = 1'b1;
                rdata_d  = pop_data;
              end else begin
                rdata_d  = 32'h0;  // empty
              end
            end else begin
              rdata_d = 32'h0;  // RO
            end
          end

          TDU_TASK_STATUS_OFFSET: begin
            if (req_write) error_d = 1'b1;  // RO
            // [5]=full, [4]=empty, [3:0]=count
            else rdata_d = {26'h0, full, empty, count_q[3:0]};
          end

          TDU_ACTIVE_HART_CYCLES_OFFSET: begin
            if (req_write) begin
              // Write clears the counter (read-to-clear alternative)
              active_hart_cycles_clear = 1'b1;
            end else begin
              rdata_d = active_hart_cycles_q;
            end
          end

          default: error_d = 1'b1;
        endcase
      end
    end
  end

  assign reg_rsp_o = '{error: error_d, ready: ready_d, rdata: rdata_d};

  // ── Task FIFO sequential logic ──────────────────────────────────
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      wr_ptr_q <= '0;
      rd_ptr_q <= '0;
      count_q  <= '0;
    end else begin
      // Pointer updates: push-only, pop-only, or simultaneous (no change to count)
      if (push && !pop) begin
        task_mem[wr_ptr_q] <= push_data;
        wr_ptr_q <= wr_ptr_q + 1;
        count_q  <= count_q + 1;
      end else if (pop && !push) begin
        rd_ptr_q <= rd_ptr_q + 1;
        count_q  <= count_q - 1;
      end else if (push && pop) begin
        // Simultaneous push and pop: advance both, count unchanged.
        // Push writes to wr_ptr; pop reads from rd_ptr. With a 1-cycle
        // read window this is safe as long as the FIFO is neither full
        // (push blocked) nor empty (pop blocked) — both guarded above.
        task_mem[wr_ptr_q] <= push_data;
        wr_ptr_q <= wr_ptr_q + 1;
        rd_ptr_q <= rd_ptr_q + 1;
      end
    end
  end

  assign pop_data = task_mem[rd_ptr_q];

  // ── Register updates ────────────────────────────────────────────
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      sched_mode_q     <= RESET_SCHED_MODE;
      wake_mask_q      <= '0;
      active_hart_cycles_q <= '0;
    end else begin
      sched_mode_q     <= sched_mode_d;
      wake_mask_q      <= wake_mask_d;
      // ACTIVE-HART-CYCLES accumulator: increment by the number of running
      // harts each cycle, i.e. sum over time of the active-hart count.
      //
      // This is a WORKLOAD proxy, NOT energy. It weights every hart equally,
      // so one cycle of a bit-serial SERV and one cycle of a BOOM contribute
      // the same 1. Converting it to joules would need per-core, per-state
      // characterised weights that this design does not have; reporting it as
      // energy would be a fabricated number.
      //
      // SATURATES at 2^32-1 rather than wrapping. The previous comment claimed
      // saturation while the implementation did plain 32-bit addition, so a
      // long run silently wrapped and a huge workload could read as a small
      // one. A saturated value is honest ("at least this much"); a wrapped one
      // is wrong. A write to the register clears it.
      if (active_hart_cycles_clear) begin
        active_hart_cycles_q <= '0;
      end else begin
        active_hart_cycles_q <= active_hart_cycles_sum[32]
                              ? {32{1'b1}}
                              : active_hart_cycles_sum[31:0];
      end
    end
  end

  // CPI estimate array registers
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      for (int i = 0; i < CpiWords; i++) cpi_est_q[i] <= '0;
    end else begin
      for (int i = 0; i < CpiWords; i++) begin
        if (cpi_we[i]) cpi_est_q[i] <= cpi_est_d[i];
      end
    end
  end

  // ── Wake pulse generation ───────────────────────────────────────
  // A core is woken (1-cycle pulse) when:
  //   (a) a task is pushed whose core_hint targets it AND the core is in
  //       WAKE_MASK and currently sleeping, or
  //   (b) software writes to WAKE_REQ for that core.
  //
  // The auto-wake is TARGETED by the pushed descriptor's core_hint (a
  // one-hot decode; an out-of-range hint shifts out to zero and wakes
  // nobody). Broadcasting to every masked sleeping core instead — the
  // original behavior — launches the whole worker pool on the first push:
  // the losers race to TASK_POP before their descriptors are queued, pop
  // an empty FIFO, and never return to dormancy (bug 20). Targeted wake
  // also gives the driver a race-free invariant: each wake follows its
  // own push, so worker pops can never outrun pushes.
  logic [NUM_HARTS-1:0] wake_req_pulse;
  logic [NUM_HARTS-1:0] wake_task_pulse;
  logic [NUM_HARTS-1:0] park_req_pulse;
  logic [NUM_HARTS-1:0] hint_onehot;

  assign wake_req_pulse  = (req_valid && req_write &&
                           (req_addr == TDU_WAKE_REQ_OFFSET))
                           ? req_wdata[NUM_HARTS-1:0] : '0;
  assign park_req_pulse  = (req_valid && req_write &&
                           (req_addr == TDU_PARK_REQ_OFFSET))
                           ? req_wdata[NUM_HARTS-1:0] : '0;

  assign hint_onehot     = NUM_HARTS'(1) << push_data.core_hint;
  assign wake_task_pulse = (push) ? (wake_mask_q & core_sleep_i & hint_onehot)
                                  : '0;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      core_wake_o <= '0;
      core_park_o <= '0;
    end else begin
      core_wake_o <= wake_req_pulse | wake_task_pulse;
      core_park_o <= park_req_pulse;
    end
  end

  // ── Event interrupt to TITAN ────────────────────────────────────
  // Assert a 1-cycle interrupt pulse whenever a task is successfully
  // enqueued, so the TITAN scheduler can react (or the wake pulses alone
  // can drive ATLAS/NANO entry).
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)
      tdu_irq_o <= 1'b0;
    else
      tdu_irq_o <= push;
  end

  // ── Assertions ──────────────────────────────────────────────────
  `ASSERT_INIT(NumHartsPositive, NUM_HARTS >= 1)
  `ASSERT_INIT(NumHartsFitStatus, NUM_HARTS <= 16)
  `ASSERT_INIT(ResetSchedModeValid, RESET_SCHED_MODE <= SCHED_POWER_AWARE)

endmodule : tdu
