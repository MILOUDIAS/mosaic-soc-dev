/* Topology-generic MOSAIC liveness firmware.
 *
 * Every boot image is linked separately from this source. Free-running shared
 * images identify harts with mhartid; singleton images use their generated
 * fixed hart ID. All harts write a unique non-zero sentinel. Hart 0 optionally
 * wakes every non-TITAN hart through the TDU, waits for all configured harts,
 * and exits.
 * Only RV32I+Zicsr instructions and x0-x15 are used, so the image is valid for
 * RV32E controllers and as an instruction stream on the RV64 simulation tiles.
 */

#include <mosaic_memory_map.inc>

#ifndef MOSAIC_GENERIC_PRIMARY
#define MOSAIC_GENERIC_PRIMARY 0
#endif
#ifndef MOSAIC_GENERIC_NUM_HARTS
#define MOSAIC_GENERIC_NUM_HARTS 1
#endif
#ifndef MOSAIC_GENERIC_WAKE_MASK
#define MOSAIC_GENERIC_WAKE_MASK 0
#endif
#ifndef MOSAIC_GENERIC_DISPATCH_MASK
#define MOSAIC_GENERIC_DISPATCH_MASK MOSAIC_GENERIC_WAKE_MASK
#endif
#ifndef MOSAIC_GENERIC_TDU
#define MOSAIC_GENERIC_TDU 0
#endif
#ifndef MOSAIC_GENERIC_TL_WINDOW
#define MOSAIC_GENERIC_TL_WINDOW 0
#endif
#ifndef MOSAIC_GENERIC_PRIMARY_SHARED
#define MOSAIC_GENERIC_PRIMARY_SHARED 0
#endif
#ifndef MOSAIC_GENERIC_FIXED_HART
#define MOSAIC_GENERIC_FIXED_HART 0
#endif
#ifndef MOSAIC_GENERIC_TESTBENCH_BOOTSTRAP
#define MOSAIC_GENERIC_TESTBENCH_BOOTSTRAP 0
#endif

#if MOSAIC_GENERIC_TL_WINDOW
#define GENERIC_SENT_BASE MOSAIC_ASM_TL_SENTINEL_BASE
#define GENERIC_SOC_CTRL_BASE MOSAIC_ASM_TL_SOC_CTRL_BASE
#define GENERIC_TDU_BASE MOSAIC_ASM_TL_TDU_BASE
#else
#define GENERIC_SENT_BASE MOSAIC_ASM_SENTINEL_BASE
#define GENERIC_SOC_CTRL_BASE MOSAIC_ASM_SOC_CTRL_BASE
#define GENERIC_TDU_BASE MOSAIC_ASM_TDU_BASE
#endif

    .section .text.init,"ax"
    .global _start
_start:
#if MOSAIC_GENERIC_PRIMARY
#if MOSAIC_GENERIC_TESTBENCH_BOOTSTRAP
    /* This image may be shared by free-running testbench hart 0 and workers
     * without mhartid.  The reset-empty queue identifies hart 0; workers are
     * released only by targeted descriptors and arrive with a nonzero task. */
    li   t0, (GENERIC_TDU_BASE + 0x14)
    lw   t1, 0(t0)
    bnez t1, .Lworker_have_task
#endif
#if MOSAIC_GENERIC_PRIMARY_SHARED
    csrr t0, mhartid
#else
    li   t0, MOSAIC_GENERIC_FIXED_HART
#endif

    /* sentinel[mhartid] = hartid + 1 */
    slli t1, t0, 2
    li   t2, GENERIC_SENT_BASE
    add  t2, t2, t1
    addi t1, t0, 1
    sw   t1, 0(t2)

    bnez t0, .Lpark

#if MOSAIC_GENERIC_TDU && MOSAIC_GENERIC_WAKE_MASK
    /* Normal AMP images can broadcast-wake before queueing.  A worker-only
     * testbench instead arms WAKE_MASK and lets each targeted descriptor push
     * release exactly its hinted hart, because no TITAN exists to start it. */
#if MOSAIC_GENERIC_TESTBENCH_BOOTSTRAP
    li   t1, (GENERIC_TDU_BASE + 0x08)
    li   t2, MOSAIC_GENERIC_WAKE_MASK
    sw   t2, 0(t1)
#else
    li   t1, (GENERIC_TDU_BASE + 0x0c)
    li   t2, MOSAIC_GENERIC_WAKE_MASK
    sw   t2, 0(t1)
#endif

    li   t0, 1
.Lpush_next:
    li   t1, MOSAIC_GENERIC_NUM_HARTS
    bge  t0, t1, .Lpush_done
    li   t1, 1
    sll  t1, t1, t0
    li   t2, MOSAIC_GENERIC_DISPATCH_MASK
    and  t1, t1, t2
    beqz t1, .Lpush_skip
.Lpush_wait:
    li   t1, (GENERIC_TDU_BASE + 0x18)
    lw   t2, 0(t1)
    andi t2, t2, 0x20
    bnez t2, .Lpush_wait
    slli t1, t0, 11
    li   t2, 0x00010000
    or   t1, t1, t2
    li   t2, (GENERIC_TDU_BASE + 0x10)
    sw   t1, 0(t2)
.Lpush_skip:
    addi t0, t0, 1
    j    .Lpush_next
.Lpush_done:
#endif

    /* The combined HEX initializes the generated shared-control range to zero.
     * Require each exact hart-derived value, not merely nonzero memory. */
    li   t0, GENERIC_SENT_BASE
    li   t1, (GENERIC_SENT_BASE + 4 * MOSAIC_GENERIC_NUM_HARTS)
    li   t2, 1
.Lscan:
    lw   a0, 0(t0)
    bne  a0, t2, .Lscan
    addi t0, t0, 4
    addi t2, t2, 1
    bne  t0, t1, .Lscan

    li   t0, GENERIC_SOC_CTRL_BASE
    sw   zero, 4(t0)
    li   t1, 1
    sw   t1, 0(t0)

#endif

#if !MOSAIC_GENERIC_PRIMARY || MOSAIC_GENERIC_TESTBENCH_BOOTSTRAP
.Lworker:
    /* Dormant workers need no CSR support. An atomic TASK_POP descriptor
     * supplies a unique sentinel slot even when many harts share this image. */
#if MOSAIC_GENERIC_TDU
    li   t0, (GENERIC_TDU_BASE + 0x14)
.Lpop:
    lw   t1, 0(t0)
    beqz t1, .Lpop
.Lworker_have_task:
    srli t1, t1, 11
    andi t1, t1, 0x1f
    slli t2, t1, 2
    li   t0, GENERIC_SENT_BASE
    add  t0, t0, t2
    addi t1, t1, 1
    sw   t1, 0(t0)
#else
    /* Config validation only permits dormant worker roles with a TDU. */
    j .Lpark
#endif
#endif

.Lpark:
    j .Lpark
