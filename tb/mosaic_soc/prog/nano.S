# nano.S — NANO worker (serv, hart 2) firmware for the wake-and-run demo.
# Linked at 0x2000 (= the core's RESET_PC). Boots dormant; runs only once TITAN
# wakes it via the TDU. Writes its sentinel (0x4E414E00) to 0x3008, then spins.
# Base RV32I only (serv COMPRESSED=0, no M). No stack (li/sw/j only).
#if defined(MOSAIC_USE_BUILD_GENERATED_HEADERS)
#include <mosaic_memory_map.inc>
#define NANO_SENTINEL_ADDR (MOSAIC_ASM_SENTINEL_BASE + 8)
#define TDU_PARK_REQ_ADDR MOSAIC_ASM_TDU_PARK_REQ
#else
#define NANO_SENTINEL_ADDR 0x00003008
#define TDU_PARK_REQ_ADDR 0x200A0060
#endif

    .section .nano,"ax"
    .global _nano_start
_nano_start:
    li   t0, 0x4E414E00               # NANO sentinel ("NANE")
    li   t1, NANO_SENTINEL_ADDR       # generated NANO sentinel slot
    sw   t0, 0(t1)
    li   t0, 0x00000004               # park hart 2 after completion
    li   t1, TDU_PARK_REQ_ADDR        # generated TDU PARK_REQ
    sw   t0, 0(t1)
1:  j    1b
