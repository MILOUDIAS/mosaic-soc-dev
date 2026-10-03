/* mosaic_link.ld — Linker script for MOSAIC-SoC multi-core firmware.
 *
 * Places programs at each core's boot address within SRAM (0x0..0x8000):
 *   TITAN (cv32e20) @ 0x180  — boot ROM jumps here
 *   ATLAS (fazyrv)  @ 0x1000 — BOOTADR
 *   NANO  (serv)    @ 0x2000 — RESET_PC
 */

OUTPUT_FORMAT("elf32-littleriscv", "elf32-littleriscv", "elf32-littleriscv")
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
    /* TITAN code region: 0x180 .. 0x1000 (2176 bytes max) */
    titan_rx (rx)  : ORIGIN = 0x00000180, LENGTH = 0x00000E80
    /* ATLAS code region: 0x1000 .. 0x2000 (4096 bytes max) */
    atlas_rx (rx)  : ORIGIN = 0x00001000, LENGTH = 0x00001000
    /* NANO code region: 0x2000 .. 0x3000 (4096 bytes max) */
    nano_rx (rx)   : ORIGIN = 0x00002000, LENGTH = 0x00001000
    /* Shared sentinel/result window: 0x3000 .. 0x3200.
     * Workers store here by hardcoded address (sentinels 0x3000 + slot*4,
     * results 0x3100 + slot*4) and TITAN polls it — NO linked section may
     * land here, or worker stores corrupt TITAN's globals (and vice versa).
     * Kept out of every MEMORY region so the linker can never allocate it. */
    /* TITAN data + stack: 0x3200 .. 0x8000 */
    data_rw (rwx)  : ORIGIN = 0x00003200, LENGTH = 0x00004E00
}

__stack_size = 0x400;

SECTIONS
{
    /* ── TITAN program (hart 0) ──────────────────────────────────── */
    .titan : {
        _start = .;
        KEEP(*(.text.start))
        KEEP(*(.text.init))
        *(.text.startup*)
        *(.text*)
        *(.rodata*)
        *(.data*)
    } > titan_rx

    /* ── ATLAS programs (hart 1-2) at 0x1000 ─────────────────────── */
    .atlas : {
        *(.atlas)
    } > atlas_rx

    /* ── NANO programs (hart 3-6) at 0x2000 ──────────────────────── */
    .nano : {
        *(.nano)
    } > nano_rx

    /* ── Small data + BSS + Stack in the TITAN data region ──────────
     * -fdata-sections emits per-symbol .sbss.<name>/.sdata.<name>
     * sections; the patterns must catch them or the linker orphan-places
     * them at the start of data_rw's predecessor region. */
    .sdata : {
        *(.sdata*)
    } > data_rw

    .bss (NOLOAD) : {
        __bss_start = .;
        *(.sbss*)
        *(.bss*)
        *(COMMON)
        __bss_end = .;
    } > data_rw

    .stack (NOLOAD) : ALIGN(16) {
        . += __stack_size;
        _sp = .;
    } > data_rw

    _end = .;
}
