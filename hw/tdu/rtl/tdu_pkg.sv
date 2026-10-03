// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// tdu_pkg.sv — Task Dispatch Unit package.
//
// Defines the register map, scheduling modes and task descriptor format for
// the MOSAIC-SoC Task Dispatch Unit (TDU). The TDU is a small (<100 GE)
// memory-mapped hardware block that assists the TITAN core (running FreeRTOS)
// with task dispatch to the heterogeneous ATLAS/NANO cores.

package tdu_pkg;

  // ── Scheduling modes ────────────────────────────────────────────
  typedef enum logic [1:0] {
    SCHED_STATIC      = 2'd0,  // software uses the descriptor's fixed hint
    SCHED_DYNAMIC     = 2'd1,  // software policy may use the CPI telemetry
    SCHED_POWER_AWARE = 2'd2   // software policy may use energy telemetry
  } sched_mode_e;

  // ── Register byte offsets (32-bit word-addressed) ───────────────
  // CORE_STATUS ABI: running[15:0], sleeping[31:16].
  localparam logic [31:0] TDU_CORE_STATUS_OFFSET   = 32'h00;  // RO
  localparam logic [31:0] TDU_SCHED_MODE_OFFSET    = 32'h04;  // RW
  localparam logic [31:0] TDU_WAKE_MASK_OFFSET     = 32'h08;  // RW
  localparam logic [31:0] TDU_WAKE_REQ_OFFSET      = 32'h0C;  // W1S pulse
  localparam logic [31:0] TDU_TASK_PUSH_OFFSET     = 32'h10;  // WO enqueue
  localparam logic [31:0] TDU_TASK_POP_OFFSET      = 32'h14;  // RO dequeue
  localparam logic [31:0] TDU_TASK_STATUS_OFFSET   = 32'h18;  // RO
  localparam logic [31:0] TDU_ACTIVE_HART_CYCLES_OFFSET = 32'h1C; // RO/RC
  localparam logic [31:0] TDU_CPI_EST_BASE_OFFSET  = 32'h20;  // RW array
  // W1S pulse used by a worker after completing its current descriptor.  The
  // cpu subsystem consumes the pulse to re-park/reset that hart; a later wake
  // therefore starts the worker from its boot entry again.  0x60 is beyond
  // the largest supported CPI array (16 harts -> 0x20..0x5c).
  localparam logic [31:0] TDU_PARK_REQ_OFFSET       = 32'h60;

  // Total address span of the CPI estimate array (one 32-bit word per hart).
  // The array is mapped at TDU_CPI_EST_BASE_OFFSET .. +4*NUM_HARTS.

  // ── Task descriptor (32-bit) ────────────────────────────────────
  // [31:16] task_id   — software-defined task identifier
  // [15:11] core_hint — suggested target hart (0..NUM_HARTS-1)
  // [10:8]  prio      — task priority (0=highest)
  // [7:0]   reserved
  localparam int unsigned TASK_ID_BITS   = 16;
  localparam int unsigned TASK_HINT_BITS = 5;
  localparam int unsigned TASK_PRIO_BITS = 3;

  typedef struct packed {
    logic [TASK_ID_BITS-1:0]   task_id;
    logic [TASK_HINT_BITS-1:0] core_hint;
    logic [TASK_PRIO_BITS-1:0] prio;
    logic [7:0]                reserved;
  } task_desc_t;

  // ── Task queue ──────────────────────────────────────────────────
  localparam int unsigned TASK_QUEUE_DEPTH = 8;

endpackage
