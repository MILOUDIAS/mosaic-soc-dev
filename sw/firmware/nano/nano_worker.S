// Copyright 2026 MOSAIC-SoC Contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// nano_worker.S — NANO (SERV) worker firmware.
//
// Runs on NANO cores (harts 3-6, serv bit-serial). Boots dormant;
// executes only after TITAN wakes it via the TDU. On wake it POPS its task
// descriptor from the TDU task FIFO — the bus serializes reads of
// TDU_TASK_POP, so every worker dequeues a UNIQUE descriptor even though
// all NANO harts run this same program. The descriptor's core_hint selects
// the per-hart sentinel slot TITAN polls (slot = core_hint: ATLAS 1-2 ->
// 0x3004/0x3008, NANO 3-6 -> 0x300C..0x3018) and the task_id selects the
// sentinel value (SIGNAL_PROC -> ATLAS value, SENSOR_POLL -> NANO value),
// so completion reporting is correct regardless of which worker happens to
// pop which task.
//
// It then performs a sensor-polling simulation (CRC accumulation loop),
// stores the result in the scratch area (0x3100 + slot*4, OUTSIDE the
// sentinel range), and only then writes the sentinel — so TITAN's
// completion poll also bounds the workload, and the TDU CPI/energy counters
// observe real activity.
//
// ISA: RV32I only (serv COMPRESSED=0, no M-extension). No wfi — the serial
// cores' wfi behavior is unverified, so the worker parks on a jump spin.
// Boot address: 0x2000 (cores[].boot_addr in mosaic.yaml).
//
// SERV is a bit-serial core (~200 GE, ~500 cycles/insn). The workload is
// intentionally short (64 iterations) to keep simulated execution bounded.
//
// @see hw/tdu/rtl/tdu_pkg.sv — task descriptor format
//      [31:16] task_id, [15:11] core_hint, [10:8] prio
// @see sw/firmware/titan/titan_main.c — dispatch + completion poll

#if defined(MOSAIC_USE_BUILD_GENERATED_HEADERS)
#include <mosaic_memory_map.inc>
#define TDU_TASK_POP  MOSAIC_ASM_TDU_TASK_POP
#define TDU_PARK_REQ  MOSAIC_ASM_TDU_PARK_REQ
#define SENTINEL_BASE MOSAIC_ASM_SENTINEL_BASE
#define RESULT_BASE   MOSAIC_ASM_RESULT_BASE
#else
#define TDU_TASK_POP  0x200A0014
#define TDU_PARK_REQ  0x200A0060
#define SENTINEL_BASE 0x00003000
#define RESULT_BASE   0x00003100
#endif
#define ATLAS_SENTINEL   0xA71A5000
#define NANO_SENTINEL    0x4E414E00
#define TASK_SIGNAL_PROC 0x0002

    .section .nano,"ax",@progbits
    .global _nano_start
    .option norvc           // serv has no compressed instructions

_nano_start:
    // ── Pop our task descriptor (hardware-atomic dequeue) ───────────
    li      t0, TDU_TASK_POP
    lw      s0, 0(t0)              // s0 = task descriptor
    beqz    s0, 9f                 // empty pop (raw 0) -> park, never
                                   // write slot 0 (TITAN's sentinel)
    srli    s1, s0, 11
    andi    s1, s1, 0x1F           // s1 = core_hint = sentinel slot
    slli    s1, s1, 2              // s1 = slot * 4
    srli    s2, s0, 16             // s2 = task_id
    li      t1, TASK_SIGNAL_PROC
    li      s3, NANO_SENTINEL
    bne     s2, t1, 1f
    li      s3, ATLAS_SENTINEL     // signal task popped by a NANO core
1:

    // ── Sensor-polling simulation ───────────────────────────────────
    // Simulate reading a sensor register and accumulating a CRC.
    // This represents the always-on sensor-polling workload that
    // NANO cores are designed for.
    //
    // Loop: crc = crc ^ (i << (i%8)) for i = 0..63
    li      t2, 0xFFFFFFFF         // crc = 0xFFFFFFFF (standard CRC32 init)
    li      t3, 0                  // i = 0
    li      t4, 64                 // limit

crc_loop:
    andi    t5, t3, 0x7            // t5 = i % 8
    sll     t6, t3, t5             // t6 = i << (i%8)
    xor     t2, t2, t6             // crc ^= t6
    addi    t3, t3, 1              // i++
    blt     t3, t4, crc_loop       // if (i < 64) goto crc_loop

    // ── Store result in the scratch area (outside the sentinel range) ─
    li      t1, RESULT_BASE
    add     t1, t1, s1
    sw      t2, 0(t1)

    // ── Report completion: sentinel LAST, to the popped task's slot ──
    li      t1, SENTINEL_BASE
    add     t1, t1, s1
    sw      s3, 0(t1)

    // ── Park and re-arm for the next dispatch ────────────────────────
    srli    t2, s1, 2
    li      t3, 1
    sll     t3, t3, t2
    li      t4, TDU_PARK_REQ
    sw      t3, 0(t4)
9:  j       9b
