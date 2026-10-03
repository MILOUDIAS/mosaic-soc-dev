// Copyright 2026 MOSAIC-SoC Contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

/**
 * @file tdu.c
 * @brief MOSAIC-SoC Task Dispatch Unit driver implementation.
 *
 * All register accesses use the mmio_region_t abstraction from x-heep,
 * matching the coding conventions of existing drivers (soc_ctrl, rv_timer).
 */

#include "tdu.h"

#include <stddef.h>
#include <stdint.h>

#if defined(MOSAIC_USE_BUILD_GENERATED_HEADERS)
#include <mosaic_topology.h>
#else
#include "mosaic_legacy_config.h"
#endif

/** MMIO region handle for the TDU register space. */
static mmio_region_t tdu_region = {0};

/**
 * Get or initialize the TDU MMIO region handle.
 *
 * Uses lazy initialization: the first call creates the region from
 * TDU_BASE, subsequent calls return the cached handle.
 */
static mmio_region_t tdu_get_region(void) {
    if (tdu_region.base == NULL) {
        tdu_region = mmio_region_from_addr(TDU_BASE);
    }
    return tdu_region;
}

// ── Scheduling mode ────────────────────────────────────────────────

void tdu_set_sched_mode(uint32_t mode) {
    mmio_region_t tdu = tdu_get_region();
    mmio_region_write32(tdu, (ptrdiff_t)TDU_SCHED_MODE_REG_OFFSET,
                        mode & 0x3u);
}

uint32_t tdu_get_sched_mode(void) {
    mmio_region_t tdu = tdu_get_region();
    return mmio_region_read32(tdu,
                              (ptrdiff_t)TDU_SCHED_MODE_REG_OFFSET) & 0x3u;
}

// ── Wake mask ──────────────────────────────────────────────────────

void tdu_set_wake_mask(uint32_t mask) {
    mmio_region_t tdu = tdu_get_region();
    mmio_region_write32(tdu, (ptrdiff_t)TDU_WAKE_MASK_REG_OFFSET, mask);
}

uint32_t tdu_get_wake_mask(void) {
    mmio_region_t tdu = tdu_get_region();
    return mmio_region_read32(tdu, (ptrdiff_t)TDU_WAKE_MASK_REG_OFFSET);
}

// ── Wake pulses ────────────────────────────────────────────────────

void tdu_wake_harts(uint32_t hart_mask) {
    // W1S register: writing 1 generates a 1-cycle pulse on core_wake_o.
    // The RTL (tdu.sv:285-287) uses req_wdata as the pulse source.
    mmio_region_t tdu = tdu_get_region();
    mmio_region_write32(tdu, (ptrdiff_t)TDU_WAKE_REQ_REG_OFFSET, hart_mask);
}

void tdu_park_harts(uint32_t hart_mask) {
    mmio_region_t tdu = tdu_get_region();
    mmio_region_write32(tdu, (ptrdiff_t)TDU_PARK_REQ_REG_OFFSET, hart_mask);
}

// ── Core status ────────────────────────────────────────────────────

tdu_core_status_t tdu_get_core_status(void) {
    mmio_region_t tdu = tdu_get_region();
    uint32_t raw = mmio_region_read32(tdu,
                                      (ptrdiff_t)TDU_CORE_STATUS_REG_OFFSET);
    tdu_core_status_t status;
    status.running  = (uint16_t)(raw & 0xFFFFu);
    status.sleeping = (uint16_t)((raw >> 16) & 0xFFFFu);
    return status;
}

// ── Task queue ─────────────────────────────────────────────────────

int tdu_task_push(const tdu_task_t *task) {
    if (task == NULL || task->core_hint >= MOSAIC_NUM_HARTS) return -1;
    tdu_task_status_t st = tdu_get_task_status();
    if (st.full) return -1;

    mmio_region_t tdu = tdu_get_region();
    uint32_t desc = tdu_task_pack(task->task_id, task->core_hint, task->prio);
    mmio_region_write32(tdu, (ptrdiff_t)TDU_TASK_PUSH_REG_OFFSET, desc);
    return 0;
}

int tdu_task_pop(tdu_task_t *task) {
    if (task == NULL) return -1;
    tdu_task_status_t st = tdu_get_task_status();
    if (st.empty) return -1;

    mmio_region_t tdu = tdu_get_region();
    uint32_t raw = mmio_region_read32(tdu,
                                      (ptrdiff_t)TDU_TASK_POP_REG_OFFSET);
    task->task_id   = (uint16_t)((raw >> 16) & 0xFFFFu);
    task->core_hint = (uint8_t)((raw >> 11) & 0x1Fu);
    task->prio      = (uint8_t)((raw >> 8)  & 0x7u);
    return 0;
}

tdu_task_status_t tdu_get_task_status(void) {
    mmio_region_t tdu = tdu_get_region();
    uint32_t raw = mmio_region_read32(tdu,
                                      (ptrdiff_t)TDU_TASK_STATUS_REG_OFFSET);
    tdu_task_status_t status;
    status.full  = (uint8_t)((raw >> 5) & 1u);
    status.empty = (uint8_t)((raw >> 4) & 1u);
    status.count = (uint8_t)(raw & 0xFu);
    return status;
}

// ── CPI estimates ──────────────────────────────────────────────────

void tdu_set_cpi_estimate(uint32_t hart, uint32_t cpi) {
    mmio_region_t tdu = tdu_get_region();
    ptrdiff_t offset = (ptrdiff_t)(TDU_CPI_EST_BASE_OFFSET + hart * 4u);
    mmio_region_write32(tdu, offset, cpi);
}

uint32_t tdu_get_cpi_estimate(uint32_t hart) {
    mmio_region_t tdu = tdu_get_region();
    ptrdiff_t offset = (ptrdiff_t)(TDU_CPI_EST_BASE_OFFSET + hart * 4u);
    return mmio_region_read32(tdu, offset);
}

// ── Active-hart-cycles counter (workload proxy, NOT energy) ────────

uint32_t tdu_get_active_hart_cycles(void) {
    mmio_region_t tdu = tdu_get_region();
    return mmio_region_read32(tdu,
                              (ptrdiff_t)TDU_ACTIVE_HART_CYCLES_REG_OFFSET);
}

void tdu_clear_active_hart_cycles(void) {
    // Writing any value clears the counter (tdu.sv:258).
    mmio_region_t tdu = tdu_get_region();
    mmio_region_write32(tdu, (ptrdiff_t)TDU_ACTIVE_HART_CYCLES_REG_OFFSET, 0u);
}

// ── Convenience: dispatch task + wake ──────────────────────────────

int tdu_dispatch_task(const tdu_task_t *task) {
    if (task == NULL || task->core_hint >= MOSAIC_NUM_HARTS) return -1;
    if (tdu_task_push(task) != 0) return -1;

    // Wake the target core if it's in the wake mask and currently sleeping.
    // The TDU hardware auto-wakes the HINTED core on task push (tdu.sv:
    // wake_task_pulse = push ? (wake_mask & core_sleep & 1<<core_hint) : 0).
    // However, for explicit control, we also issue a direct WAKE_REQ.
    tdu_core_status_t st = tdu_get_core_status();
    uint32_t wake = (1u << task->core_hint) & st.sleeping;
    if (wake) {
        tdu_wake_harts(wake);
    }
    return 0;
}
