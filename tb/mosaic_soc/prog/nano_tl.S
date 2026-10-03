# nano_tl.S — NANO worker program for Berkeley RV64 tiles (rocket/boom).
# See atlas_tl.S for the CLINT-window sentinel rationale.
#if defined(MOSAIC_USE_BUILD_GENERATED_HEADERS)
#include <mosaic_memory_map.inc>
#define NANO_TL_SENTINEL_ADDR (MOSAIC_ASM_TL_SENTINEL_BASE + 8)
#define NANO_TL_PARK_REQ MOSAIC_ASM_TL_TDU_PARK_REQ
#else
#define NANO_TL_SENTINEL_ADDR 0x02000008
#define NANO_TL_PARK_REQ 0x0C000060
#endif

    .section .nano,"ax"
    .global _nano_start
_nano_start:
    li   t0, 0x4E414E00           # NANO sentinel value ("NAN\0")
    li   t1, NANO_TL_SENTINEL_ADDR  # uncached CLINT-window sentinel alias
    sw   t0, 0(t1)
    li   t0, 0x00000004           # park hart 2 after completion
    li   t1, NANO_TL_PARK_REQ     # uncached TDU window
    sw   t0, 0(t1)
1:  j    1b
