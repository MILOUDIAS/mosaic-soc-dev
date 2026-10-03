// Copyright 2026 MOSAIC-SoC Contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

/**
 * @file mosaic_hw.h
 * @brief MOSAIC-SoC hardware register definitions.
 *
 * Self-contained header for bare-metal firmware. Does NOT depend on generated
 * x-heep headers. Addresses match the PoC memory map (mosaic.yaml).
 *
 * @see hw/tdu/rtl/tdu_pkg.sv — TDU register offsets
 * @see hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv.tpl — SoC address map
 */

#ifndef MOSAIC_HW_H
#define MOSAIC_HW_H

#include <stddef.h>
#include <stdint.h>

#include "mmio.h"

#ifdef __cplusplus
extern "C" {
#endif

// ── SoC Control (always-on peripheral domain, base + 0x00) ─────────

/** Base address of the SoC control registers. */
#define SOC_CTRL_BASE       0x20000000u

/** Offset of the exit-valid register (write 1 to signal test completion). */
#define SOC_CTRL_EXIT_VALID_REG_OFFSET 0x00u

/** Offset of the exit-value register (test result code). */
#define SOC_CTRL_EXIT_VALUE_REG_OFFSET 0x04u

// ── TDU (always-on peripheral domain, base + 0xA000) ───────────────

/** Base address of the Task Dispatch Unit. */
#define TDU_BASE            0x200A0000u

/** Core status register offset (RO). [31:16]=sleep, [15:0]=running. */
#define TDU_CORE_STATUS_REG_OFFSET   0x00u

/** Scheduling mode register offset (RW). [1:0] = mode. */
#define TDU_SCHED_MODE_REG_OFFSET    0x04u

/** Wake mask register offset (RW). [N-1:0] = per-hart mask. */
#define TDU_WAKE_MASK_REG_OFFSET     0x08u

/** Wake request register offset (W1S). [N-1:0] = wake pulse. */
#define TDU_WAKE_REQ_REG_OFFSET      0x0Cu

/** Task push register offset (WO). Write task descriptor to enqueue. */
#define TDU_TASK_PUSH_REG_OFFSET     0x10u

/** Task pop register offset (RO). Read to dequeue. */
#define TDU_TASK_POP_REG_OFFSET      0x14u

/** Task status register offset (RO). [5]=full, [4]=empty, [3:0]=count. */
#define TDU_TASK_STATUS_REG_OFFSET   0x18u

/** Energy counter register offset (RO/RC). Write clears. */
#define TDU_ACTIVE_HART_CYCLES_REG_OFFSET 0x1Cu

/** CPI estimate array base offset (RW). One 32-bit word per hart. */
#define TDU_CPI_EST_BASE_OFFSET      0x20u

/** Worker completion/park request (write-one pulse per hart). */
#define TDU_PARK_REQ_REG_OFFSET      0x60u

// ── TDU scheduling modes ───────────────────────────────────────────

/** Static scheduling: TITAN assigns tasks to fixed cores. */
#define TDU_SCHED_STATIC      0u

/** Dynamic software policy: TITAN may place tasks from CPI telemetry. */
#define TDU_SCHED_DYNAMIC     1u

/** Power-aware software policy: TITAN may use the energy proxy. */
#define TDU_SCHED_POWER_AWARE 2u

// ── TDU task descriptor format (32-bit packed) ─────────────────────
// [31:16] task_id, [15:11] core_hint, [10:8] prio, [7:0] reserved

/**
 * Pack a task descriptor into a 32-bit word for TDU_TASK_PUSH.
 *
 * @param task_id   Software-defined task identifier (16 bits).
 * @param core_hint Target hart index (5 bits, 0..NUM_HARTS-1).
 * @param prio      Task priority (3 bits, 0=highest).
 * @return Packed 32-bit task descriptor.
 */
static inline uint32_t tdu_task_pack(uint16_t task_id, uint8_t core_hint,
                                     uint8_t prio) {
    return ((uint32_t)task_id   << 16) |
           ((uint32_t)core_hint << 11) |
           ((uint32_t)prio      << 8);
}

// ── Memory map constants ───────────────────────────────────────────

/** Base address of SRAM (0x00000000). */
#define SRAM_BASE           0x00000000u

/** Total SRAM size in bytes (32 KB). */
#define SRAM_SIZE           0x8000u

/** End address of SRAM. */
#define SRAM_END            (SRAM_BASE + SRAM_SIZE)

/** TITAN boot address (boot ROM jumps here). */
#define TITAN_BOOT_ADDR     0x00000180u

/** ATLAS (FazyRV) boot address (BOOTADR). */
#define ATLAS_BOOT_ADDR     0x00001000u

/** NANO (SERV) boot address (RESET_PC). */
#define NANO_BOOT_ADDR      0x00002000u

/** Base address of shared sentinel region. */
#define SENTINEL_BASE       0x00003000u

/** TITAN sentinel address. */
#define SENTINEL_TITAN_ADDR (SENTINEL_BASE + 0x00u)

/** ATLAS sentinel address. */
#define SENTINEL_ATLAS_ADDR (SENTINEL_BASE + 0x04u)

/** NANO sentinel address. */
#define SENTINEL_NANO_ADDR  (SENTINEL_BASE + 0x08u)

// ── Helper: signal test completion ─────────────────────────────────

/**
 * Signal test completion to the SoC testbench.
 *
 * Writes exit_val to EXIT_VALUE and sets EXIT_VALID=1, causing the
 * testbench to print "EXIT SUCCESS" (if exit_val==0) or report failure.
 *
 * @param exit_val Test result code (0 = success).
 */
static inline void soc_ctrl_exit(uint32_t exit_val) {
    mmio_region_t ctrl = mmio_region_from_addr(SOC_CTRL_BASE);
    mmio_region_write32(ctrl, (ptrdiff_t)SOC_CTRL_EXIT_VALUE_REG_OFFSET,
                        exit_val);
    mmio_region_write32(ctrl, (ptrdiff_t)SOC_CTRL_EXIT_VALID_REG_OFFSET, 1u);
}

#ifdef __cplusplus
}
#endif

#endif  // MOSAIC_HW_H
