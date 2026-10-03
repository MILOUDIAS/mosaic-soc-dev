// Copyright 2026 MOSAIC-SoC Contributors
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// start.S — TITAN startup code for MOSAIC-SoC firmware.
//
// Placed at 0x180 (boot ROM jumps here). Initializes the stack pointer
// and jumps to main(). This is the true entry point for the TITAN core.

    .section .text.start, "ax", @progbits
    .global _start
    .type _start, @function

_start:
#ifdef MOSAIC_FLASH_XIP
    .option push
    .option norelax
1:  auipc   gp, %pcrel_hi(__global_pointer$)
    addi    gp, gp, %pcrel_lo(1b)
    .option pop

    // Initialize stack pointer (top of SRAM)
    la      sp, _sp

    // TITAN executes in place from SPI flash. Initialize its SRAM state.
    la      t0, __data_load_start
    la      t1, __data_start
    la      t2, __data_end
2:  bgeu    t1, t2, 3f
    lbu     a0, 0(t0)
    sb      a0, 0(t1)
    addi    t0, t0, 1
    addi    t1, t1, 1
    j       2b
3:  la      t1, __bss_start
    la      t2, __bss_end
4:  bgeu    t1, t2, 5f
    sb      zero, 0(t1)
    addi    t1, t1, 1
    j       4b
5:
#endif

    // Jump to main()
    jal     ra, main

    // Should never return; spin if it does
6:  wfi
    j       6b

    .size _start, .-_start
