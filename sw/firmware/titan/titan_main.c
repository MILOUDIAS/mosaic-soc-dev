// Copyright 2026 MOSAIC-SoC Contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

/**
 * @file titan_main.c
 * @brief TITAN (cv32e20) orchestrator firmware for MOSAIC-SoC.
 *
 * Demonstrates the full TDU programming model:
 *   1. System initialization
 *   2. TDU configuration (scheduling mode, wake mask)
 *   3. Worker core wake-up via TDU
 *   4. Task dispatch to heterogeneous workers
 *   5. Completion monitoring via shared-memory sentinels
 *   6. Test exit signaling
 *
 * Architecture: cv32e20 (RV32E/M/C) — 16 registers, hardware M-extension.
 * Boot address: 0x180 (set by boot ROM / soc_ctrl).
 *
 * @see hw/tdu/rtl/tdu.sv — TDU implementation
 * @see hw/tdu/rtl/tdu_pkg.sv — register map and task descriptor format
 * @see tb/mosaic_soc/prog/start.S — original bare-metal demo (assembly)
 */

#include "tdu.h"
#ifdef MOSAIC_FLASH_XIP
#include "cold_boot.h"
#endif

/* Standalone firmware builds use the checked-in canonical generated contract.
 * Isolated build bundles define MOSAIC_USE_BUILD_GENERATED_HEADERS and place
 * their config-specific include directory on the compiler search path. */
#if defined(MOSAIC_USE_BUILD_GENERATED_HEADERS)
#include <mosaic_memory_map.h>
#include <mosaic_topology.h>
#else
#include "mosaic_legacy_config.h"
#endif

#if !MOSAIC_TDU_ENABLED
#error "titan_main.c is the TDU worker-dispatch demo and requires scheduler.tdu=true"
#endif

#if MOSAIC_NUM_HARTS > 16
#error "the current TDU firmware interface supports at most 16 harts"
#endif

/** Sentinel value written by TITAN to prove it executed. */
#define SENTINEL_TITAN_VAL  0xC0FFEE00u

/** Sentinel value written by ATLAS workers. */
#define SENTINEL_ATLAS_VAL  0xA71A5000u

/** Sentinel value written by NANO workers. */
#define SENTINEL_NANO_VAL   0x4E414E00u

/** Task ID for sensor-polling tasks (dispatched to NANO). */
#define TASK_ID_SENSOR_POLL  0x0001u

/** Task ID for signal-processing tasks (dispatched to ATLAS). */
#define TASK_ID_SIGNAL_PROC  0x0002u

/** Task ID for aggregation tasks. */
#define TASK_ID_AGGREGATE    0x0003u

/** Maximum polling iterations before timeout. */
#define POLL_TIMEOUT 1000000u

/**
 * Population count (number of set bits) for RV32I.
 *
 * RV32I has no popcount instruction, and linking __popcountsi2 would
 * require libgcc. This avoids the library dependency.
 *
 * @param x 32-bit value.
 * @return Number of set bits.
 */
static inline uint32_t popcount32(uint32_t x) {
    uint32_t count = 0u;
    while (x != 0u) {
        x &= x - 1u;
        count++;
    }
    return count;
}

/** MMIO region handle for shared sentinel memory. */
static mmio_region_t sentinel_region = {0};

/**
 * Initialize the shared sentinel memory region.
 */
static void sentinel_init(void) {
    sentinel_region = mmio_region_from_addr(MOSAIC_SENTINEL_BASE);
}

/**
 * Write a 32-bit value to a sentinel address.
 *
 * @param offset Byte offset from SENTINEL_BASE.
 * @param val    Value to write.
 */
static void sentinel_write(ptrdiff_t offset, uint32_t val) {
    mmio_region_write32(sentinel_region, offset, val);
}

/**
 * Read a 32-bit value from a sentinel address.
 *
 * @param offset Byte offset from SENTINEL_BASE.
 * @return Value read.
 */
static uint32_t sentinel_read(ptrdiff_t offset) {
    return mmio_region_read32(sentinel_region, offset);
}

// ── Forward declarations ───────────────────────────────────────────

static void system_init(void);
static void tdu_configure(void);
static void dispatch_workers(void);
static void wait_for_completion(void);
static void signal_success(void);
static void signal_failure(uint32_t code);

/**
 * Main entry point for TITAN firmware.
 *
 * Called from start.S after stack initialization.
 */
int main(void) {
    system_init();
#ifdef MOSAIC_FLASH_XIP
    const uint32_t cold_boot_status = mosaic_cold_boot_load_workers();
    if (cold_boot_status != 0u) {
        signal_failure(cold_boot_status);
    }
#ifdef MOSAIC_COLD_BOOT_SMOKE
    /* Focused gate used to prove flash-only loading without waiting for the
     * much slower XIP orchestration demo. Production run_fw leaves this off. */
    signal_success();
#endif
#endif
    tdu_configure();
    dispatch_workers();
    wait_for_completion();
    signal_success();

    /* Should never reach here. */
    while (1) {}
    return 0;
}

/**
 * Initialize system state.
 *
 * Writes TITAN sentinel, clears worker sentinels, and resets the
 * TDU energy counter.
 */
static void system_init(void) {
    sentinel_init();

    /* Write TITAN sentinel to prove we're alive. */
    sentinel_write(0x00u, SENTINEL_TITAN_VAL);

    /* Clear every configured worker sentinel (in case of warm restart). */
    for (uint32_t hart = 0; hart < MOSAIC_NUM_HARTS; hart++) {
        if ((MOSAIC_WORKER_HART_MASK & (1u << hart)) != 0u) {
            sentinel_write((ptrdiff_t)(hart * 4u), 0u);
        }
    }

    /* Clear energy counter for measurement. */
    tdu_clear_active_hart_cycles();
}

/**
 * Configure TDU scheduling parameters.
 *
 * Sets the generated scheduling mode and pre-loads role-appropriate CPI
 * estimates. dispatch_workers() changes the wake mask per descriptor so only
 * the intended hart can consume it, then restores the full worker mask.
 */
static void tdu_configure(void) {
    /* The scheduler reset/default and firmware now come from the same YAML. */
    tdu_set_sched_mode(MOSAIC_SCHED_MODE);

    /* Pre-load CPI estimates for the workers (used by DYNAMIC scheduler). */
    for (uint32_t hart = 0; hart < MOSAIC_NUM_HARTS; hart++) {
        const uint32_t bit = 1u << hart;
        if ((MOSAIC_ATLAS_HART_MASK & bit) != 0u) {
            tdu_set_cpi_estimate(hart, 4u);
        } else if ((MOSAIC_NANO_HART_MASK & bit) != 0u) {
            tdu_set_cpi_estimate(hart, 32u);
        }
    }
}

/**
 * Dispatch tasks to worker cores via the TDU.
 *
 * Signal-processing tasks go to the generated ATLAS mask; sensor-polling
 * tasks go to the generated NANO mask.
 *
 * Hand off one descriptor at a time. Before each push, only its hinted hart
 * is enabled in WAKE_MASK; the hardware atomically enqueues and wakes that
 * hart. TITAN waits until the descriptor has been popped before publishing
 * the next one. This guarantees that a worker executes (and later parks)
 * its own hinted descriptor, while previously handed-off workers continue
 * computing concurrently. It also scales safely beyond the 8-entry FIFO.
 */
static void dispatch_workers(void) {
    tdu_task_t task;

    for (uint32_t hart = 0; hart < MOSAIC_NUM_HARTS; hart++) {
        const uint32_t bit = 1u << hart;
        uint32_t handoff_timeout = POLL_TIMEOUT;
        if ((MOSAIC_ATLAS_HART_MASK & bit) != 0u) {
            task.task_id = TASK_ID_SIGNAL_PROC;
            task.prio = 0u;
        } else if ((MOSAIC_NANO_HART_MASK & bit) != 0u) {
            task.task_id = TASK_ID_SENSOR_POLL;
            task.prio = 1u;
        } else {
            continue;
        }
        task.core_hint = (uint8_t)hart;
        tdu_set_wake_mask(bit);
        if (tdu_task_push(&task) != 0) {
            signal_failure(0xD1500000u | hart);
        }

        /* Only this target was released, so an empty queue proves that it
         * accepted its descriptor. Do not wake the next hart before then. */
        while (!tdu_get_task_status().empty && handoff_timeout > 0u) {
            handoff_timeout--;
        }
        if (handoff_timeout == 0u) {
            signal_failure(0xD1510000u | hart);
        }
    }

    /* Leave the configured worker set armed for later policy iterations. */
    tdu_set_wake_mask(MOSAIC_WORKER_HART_MASK);
}

/**
 * Poll sentinel addresses until all workers have reported completion.
 *
 * Worker programs write their sentinel value to shared memory after
 * completing their task. This function polls until all sentinels are
 * set or a timeout occurs.
 */
static void wait_for_completion(void) {
    uint32_t timeout = POLL_TIMEOUT;
    uint32_t done_mask = 0u;

    while (done_mask != MOSAIC_WORKER_HART_MASK && timeout > 0u) {
        done_mask = 0u;
        for (uint32_t hart = 0; hart < MOSAIC_NUM_HARTS; hart++) {
            const uint32_t bit = 1u << hart;
            uint32_t expected;
            if ((MOSAIC_ATLAS_HART_MASK & bit) != 0u) {
                expected = SENTINEL_ATLAS_VAL;
            } else if ((MOSAIC_NANO_HART_MASK & bit) != 0u) {
                expected = SENTINEL_NANO_VAL;
            } else {
                continue;
            }
            if (sentinel_read((ptrdiff_t)(hart * 4u)) == expected) {
                done_mask |= bit;
            }
        }
        timeout--;
    }

    /* Check for timeout. */
    if (done_mask != MOSAIC_WORKER_HART_MASK) {
        signal_failure(
            (popcount32(done_mask) << 16) |
            popcount32(MOSAIC_WORKER_HART_MASK)
        );
    }

    /* Read final energy counter for reporting. */
    (void)tdu_get_active_hart_cycles();
}

/**
 * Signal test success to the SoC testbench.
 */
static void signal_success(void) {
    soc_ctrl_exit(0u);
}

/**
 * Signal test failure with an error code.
 *
 * @code Packed error: (completed_workers << 16) | expected_workers.
 */
static void signal_failure(uint32_t code) {
    soc_ctrl_exit(code);
}
