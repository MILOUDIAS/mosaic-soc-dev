# atlas.S — ATLAS worker (fazyrv, hart 1) firmware for the wake-and-run demo.
# Linked at 0x1000 (= the core's BOOTADR / reset PC). Boots dormant; runs only once
# TITAN wakes it via the TDU. Writes its sentinel (0xA71A5000) to 0x3004, then spins.
# Base RV32I only (fazyrv RVC=NONE, no M). No stack (li/sw/j only).
#if defined(MOSAIC_USE_BUILD_GENERATED_HEADERS)
#include <mosaic_memory_map.inc>
#define ATLAS_SENTINEL_ADDR (MOSAIC_ASM_SENTINEL_BASE + 4)
#define TDU_PARK_REQ_ADDR MOSAIC_ASM_TDU_PARK_REQ
#else
#define ATLAS_SENTINEL_ADDR 0x00003004
#define TDU_PARK_REQ_ADDR 0x200A0060
#endif

    .section .atlas,"ax"
    .global _atlas_start
_atlas_start:
    li   t0, 0xA71A5000               # ATLAS sentinel
    li   t1, ATLAS_SENTINEL_ADDR      # generated ATLAS sentinel slot
    sw   t0, 0(t1)
    li   t0, 0x00000002               # park hart 1 after completion
    li   t1, TDU_PARK_REQ_ADDR        # generated TDU PARK_REQ
    sw   t0, 0(t1)
1:  j    1b
