/* UART bring-up firmware for the frozen Chipathon Block A part.
 *
 * WHY THIS EXISTS
 * ---------------
 * Two reasons, and the first is the stronger one.
 *
 * 1. The area work reduced BOTH OpenTitan UART FIFOs from 32 to 4 entries
 *    (hw/vendor/lowrisc/opentitan/hw/ip/uart/rtl/uart_core.sv) -- 0.066 mm2,
 *    61% of the whole UART. That is hand-modified vendored RTL sitting in a
 *    tapeout candidate, and until this test nothing exercised it. A depth-4 TX
 *    FIFO changes the SOFTWARE contract: a driver written against depth 32 that
 *    burst-writes without checking TXFULL silently drops bytes.
 *
 * 2. With soc.debug: false there is no JTAG. Observability on silicon is
 *    status_o[6:0], status_valid_o, and UART TX. UART is the only channel with
 *    real bandwidth, so it is the one that most needs to work -- and it was the
 *    one with no test.
 *
 * WHAT IT CHECKS
 * --------------
 *   Phase 1  TX FIFO depth is really 4. TX is left DISABLED so the FIFO cannot
 *            drain (tx_fifo_rready is gated by tx_enable, while wvalid is not),
 *            8 bytes are written without polling, and FIFO_STATUS.TXLVL must
 *            saturate at exactly 4. This is a positive measurement of the area
 *            change, not an inference.
 *   Phase 2  A correctly-polling driver loses nothing: a known string is sent
 *            byte by byte, waiting on !TXFULL. The testbench compares the UART
 *            DPI log against the same string.
 *   Phase 3  The RX path and the depth-4 RX FIFO, exercised through the UART's
 *            own system loopback (CTRL.SLPBK) so no external stimulus is
 *            needed: a byte is transmitted, then read back from RDATA.
 *
 * Any failure parks in .Lfail WITHOUT writing the exit register, so the run
 * ends in timeout rather than a false EXIT SUCCESS. The failing phase is left
 * in the sentinel word so the log identifies it.
 *
 * RV32I + Zicsr only, registers x0-x15, matching prog_generic/generic.S so the
 * image is valid on the RV32E-class cores too.
 */

#include <mosaic_memory_map.inc>

#ifndef MOSAIC_UART_PRIMARY
#define MOSAIC_UART_PRIMARY 0
#endif
#ifndef MOSAIC_UART_BASE
#error "MOSAIC_UART_BASE must be supplied by run_uart.sh"
#endif
#ifndef MOSAIC_UART_NCO
#error "MOSAIC_UART_NCO must be supplied by run_uart.sh"
#endif
/* Depth of both prim_fifo_sync instances in uart_core.sv. */
#ifndef MOSAIC_UART_FIFO_DEPTH
#define MOSAIC_UART_FIFO_DEPTH 4
#endif

/* OpenTitan UART register offsets (uart_reg_pkg.sv). */
#define UART_CTRL         0x0c
#define UART_STATUS       0x10
#define UART_RDATA        0x14
#define UART_WDATA        0x18
#define UART_FIFO_CTRL    0x1c
#define UART_FIFO_STATUS  0x20

#define UART_CTRL_TX      (1 << 0)
#define UART_CTRL_RX      (1 << 1)
#define UART_CTRL_SLPBK   (1 << 4)
#define UART_STATUS_TXFULL  (1 << 0)
#define UART_STATUS_TXEMPTY (1 << 2)
#define UART_STATUS_RXEMPTY (1 << 5)
#define UART_FIFO_CTRL_RXRST (1 << 0)
#define UART_FIFO_CTRL_TXRST (1 << 1)
#define UART_TXLVL_MASK   0x3f

#define UART_NCO_FIELD    (MOSAIC_UART_NCO << 16)

    .section .text.init,"ax"
    .global _start
_start:
#if !MOSAIC_UART_PRIMARY
    /* Workers take no part in this test; they are never woken. */
    j    .Lpark
#else
    li   s0, MOSAIC_UART_BASE

    /* ---- Phase 1: prove the TX FIFO is 4 deep -------------------------- */
    /* TX disabled: the FIFO accepts writes (wvalid_i is reg2hw.wdata.qe) but
     * cannot drain (tx_fifo_rready is ANDed with tx_enable), so the level is
     * deterministic and does not race the serializer. */
    li   t0, UART_NCO_FIELD
    sw   t0, UART_CTRL(s0)
    li   t0, (UART_FIFO_CTRL_RXRST | UART_FIFO_CTRL_TXRST)
    sw   t0, UART_FIFO_CTRL(s0)

    li   a0, 1                      /* phase number, for the failure sentinel */
    li   t1, 8                      /* twice the depth, written blind */
    li   t2, 0x41                   /* 'A' */
.Lfill:
    sw   t2, UART_WDATA(s0)
    addi t2, t2, 1
    addi t1, t1, -1
    bnez t1, .Lfill

    lw   t0, UART_FIFO_STATUS(s0)
    andi t0, t0, UART_TXLVL_MASK
    li   t1, MOSAIC_UART_FIFO_DEPTH
    bne  t0, t1, .Lfail             /* not 4 -> the area change is not what we think */

    /* ---- Phase 2: a polling driver loses nothing ----------------------- */
    li   a0, 2
    li   t0, (UART_FIFO_CTRL_RXRST | UART_FIFO_CTRL_TXRST)
    sw   t0, UART_FIFO_CTRL(s0)     /* discard the phase-1 bytes */
    li   t0, (UART_NCO_FIELD | UART_CTRL_TX)
    sw   t0, UART_CTRL(s0)

    /* The message is read from .rodata, i.e. FROM FLASH -- this part has
     * sram_kb: 0, so read-only data has nowhere else to live. That is
     * deliberate: it makes this test the regression for bug 31, where SERV's
     * ext Wishbone port was tied off and any data access at or above
     * 0x4000_0000 stalled the hart forever. If that regresses, this loop hangs
     * and the run fails on the watchdog instead of quietly passing. */
    la   a1, uart_message
.Lsend:
    lbu  a2, 0(a1)
    beqz a2, .Lsend_done
.Lsend_wait:
    lw   t0, UART_STATUS(s0)
    andi t0, t0, UART_STATUS_TXFULL
    bnez t0, .Lsend_wait            /* the depth-4 contract: poll, never assume */
    sw   a2, UART_WDATA(s0)
    addi a1, a1, 1
    j    .Lsend

.Lsend_done:
    /* Drain before switching modes, or phase 3 would loop back the tail. */
.Ldrain:
    lw   t0, UART_STATUS(s0)
    andi t0, t0, UART_STATUS_TXEMPTY
    beqz t0, .Ldrain

    /* ---- Phase 3: RX path and RX FIFO, via system loopback ------------- */
    li   a0, 3
    li   t0, (UART_FIFO_CTRL_RXRST | UART_FIFO_CTRL_TXRST)
    sw   t0, UART_FIFO_CTRL(s0)
    li   t0, (UART_NCO_FIELD | UART_CTRL_TX | UART_CTRL_RX | UART_CTRL_SLPBK)
    sw   t0, UART_CTRL(s0)

    li   a3, 0x5a                   /* 'Z' */
    sw   a3, UART_WDATA(s0)
.Lrx_wait:
    lw   t0, UART_STATUS(s0)
    andi t0, t0, UART_STATUS_RXEMPTY
    bnez t0, .Lrx_wait
    lw   t0, UART_RDATA(s0)
    andi t0, t0, 0xff
    bne  t0, a3, .Lfail

    /* ---- passed ------------------------------------------------------- */
    li   t0, MOSAIC_ASM_SENTINEL_BASE
    li   t1, 1
    sw   t1, 0(t0)

    li   t0, MOSAIC_ASM_SOC_CTRL_BASE
    sw   zero, 4(t0)                /* exit_value = 0 */
    li   t1, 1
    sw   t1, 0(t0)                  /* exit_valid  -> status_valid_o */
    j    .Lpark

.Lfail:
    /* Record which phase failed and park. The exit register is deliberately
     * NOT written: the run must end in timeout, never in EXIT SUCCESS. */
    li   t0, MOSAIC_ASM_SENTINEL_BASE
    sw   a0, 0(t0)
    j    .Lpark
#endif

.Lpark:
    j    .Lpark

    .section .rodata
    .balign 4
uart_message:
    .asciz "MOSAIC BLOCK A UART OK\n"
