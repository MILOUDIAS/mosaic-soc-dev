// Copyright 2026 MOSAIC-SoC Contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

/**
 * @file tdu.h
 * @brief MOSAIC-SoC Task Dispatch Unit driver.
 *
 * Provides a clean C API for the TDU hardware registers. The TDU is a small
 * (<100 GE) memory-mapped block that assists the TITAN core with:
 *   - Task queue management (8-deep FIFO)
 *   - Per-core wake pulses
 *   - CPI estimate tracking
 *   - Energy accumulation
 *   - Scheduling mode control
 *
 * @see hw/tdu/rtl/tdu.sv
 * @see hw/tdu/rtl/tdu_pkg.sv
 */

#ifndef TDU_H
#define TDU_H

#include "mosaic_hw.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Task descriptor for TDU dispatch.
 *
 * Packed into a 32-bit word by tdu_task_push() using tdu_task_pack().
 */
typedef struct {
    uint16_t task_id;    /**< Software-defined task identifier. */
    uint8_t  core_hint;  /**< Target hart index (0..NUM_HARTS-1). */
    uint8_t  prio;       /**< Task priority (0=highest). */
} tdu_task_t;

/**
 * Core status snapshot from TDU_CORE_STATUS register.
 */
typedef struct {
    uint16_t running;    /**< Bitmask: core i is currently executing. */
    uint16_t sleeping;   /**< Bitmask: core i is in WFI/sleep. */
} tdu_core_status_t;

/**
 * Task queue status from TDU_TASK_STATUS register.
 */
typedef struct {
    uint8_t count;       /**< Number of tasks in queue (0..8). */
    uint8_t full;        /**< Queue is full. */
    uint8_t empty;       /**< Queue is empty. */
} tdu_task_status_t;

// ── Core API ───────────────────────────────────────────────────────

/**
 * Set the TDU scheduling mode.
 *
 * @param mode Scheduling mode (TDU_SCHED_STATIC, DYNAMIC, or POWER_AWARE).
 */
void tdu_set_sched_mode(uint32_t mode);

/**
 * Read the current TDU scheduling mode.
 *
 * @return Current scheduling mode.
 */
uint32_t tdu_get_sched_mode(void);

/**
 * Set the wake mask (bitmask of harts that auto-wake on task push).
 *
 * @param mask Bitmask of hart indices to enable for auto-wake.
 */
void tdu_set_wake_mask(uint32_t mask);

/**
 * Read the current wake mask.
 *
 * @return Current wake mask.
 */
uint32_t tdu_get_wake_mask(void);

/**
 * Send a 1-cycle wake pulse to specified harts.
 *
 * @param hart_mask Bitmask of harts to wake (W1S register).
 */
void tdu_wake_harts(uint32_t hart_mask);

/**
 * Re-park completed harts so a later TDU wake restarts their worker image.
 * Workers normally call this only for their own hart bit after committing all
 * result and completion stores.
 */
void tdu_park_harts(uint32_t hart_mask);

/**
 * Read core status (running + sleeping bitmasks).
 *
 * @return Current core status.
 */
tdu_core_status_t tdu_get_core_status(void);

/**
 * Push a task descriptor into the 8-deep FIFO.
 *
 * This status-then-write convenience API is for the single TITAN orchestrator
 * required by production AMP profiles. It is not a multi-producer SMP lock;
 * multiple TITANs must serialize producers in software or use a dedicated
 * atomic mailbox protocol.
 *
 * @param task Task descriptor to enqueue.
 * @return 0 on success, -1 if queue is full.
 */
int tdu_task_push(const tdu_task_t *task);

/**
 * Pop a task descriptor from the FIFO.
 *
 * @param task Output: dequeued task descriptor.
 * @return 0 on success, -1 if queue is empty.
 */
int tdu_task_pop(tdu_task_t *task);

/**
 * Read task queue status (count, full, empty).
 *
 * @return Current task queue status.
 */
tdu_task_status_t tdu_get_task_status(void);

/**
 * Write a CPI estimate for a specific hart.
 *
 * @param hart Hart index (0..NUM_HARTS-1).
 * @param cpi  Cycles-per-instruction estimate.
 */
void tdu_set_cpi_estimate(uint32_t hart, uint32_t cpi);

/**
 * Read the CPI estimate for a specific hart.
 *
 * @param hart Hart index (0..NUM_HARTS-1).
 * @return CPI estimate for the given hart.
 */
uint32_t tdu_get_cpi_estimate(uint32_t hart);

/**
 * Read the active-hart-cycles counter.
 *
 * Sum over time of the number of running harts. This is a WORKLOAD
 * proxy, NOT energy: every hart is weighted equally, so a cycle of a
 * bit-serial SERV counts the same as a cycle of a BOOM. Saturates at
 * UINT32_MAX rather than wrapping.
 *
 * @return Current active-hart-cycles value (saturating).
 */
uint32_t tdu_get_active_hart_cycles(void);

/**
 * Clear the active-hart-cycles counter.
 */
void tdu_clear_active_hart_cycles(void);

/**
 * Push a task and wake the target core.
 *
 * Convenience function that combines task_push + wake. Uses the core_hint
 * from the task descriptor to select which hart to wake.
 *
 * @param task Task descriptor with core_hint set.
 * @return 0 on success, -1 if queue is full.
 */
int tdu_dispatch_task(const tdu_task_t *task);

#ifdef __cplusplus
}
#endif

#endif  // TDU_H
