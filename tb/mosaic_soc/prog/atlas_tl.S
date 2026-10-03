# atlas_tl.S — ATLAS worker program for Berkeley RV64 tiles (rocket/boom).
#
# Identical to atlas.S except the sentinel store goes through the tile's
# UNCACHED CLINT-range window: 0x0200_0004 is translated by the SCI wrapper's
# TileLink->OBI bridge to the real sentinel address 0x3004, so TITAN's poll
# and the TB's write monitor see exactly the same traffic as with any other
# worker core. (A direct store to 0x3004 would be CACHED by the RV64 tile's
# D$ — the write would sit dirty in the cache and TITAN would spin forever.)
#
# rv32i encodings are valid RV64I here: li splits into lui+addi (lui
# sign-extends on RV64 — harmless, sw stores bits [31:0]); j is PC-relative.
#if defined(MOSAIC_USE_BUILD_GENERATED_HEADERS)
#include <mosaic_memory_map.inc>
#define ATLAS_TL_SENTINEL_ADDR (MOSAIC_ASM_TL_SENTINEL_BASE + 4)
#define ATLAS_TL_PARK_REQ MOSAIC_ASM_TL_TDU_PARK_REQ
#else
#define ATLAS_TL_SENTINEL_ADDR 0x02000004
#define ATLAS_TL_PARK_REQ 0x0C000060
#endif

    .section .atlas,"ax"
    .global _atlas_start
_atlas_start:
    li   t0, 0xA71A5000           # ATLAS sentinel value
    li   t1, ATLAS_TL_SENTINEL_ADDR  # uncached CLINT-window sentinel alias
    sw   t0, 0(t1)
    li   t0, 0x00000002           # park hart 1 after completion
    li   t1, ATLAS_TL_PARK_REQ    # uncached TDU window
    sw   t0, 0(t1)
1:  j    1b
