# MOSAIC AMP cold boot and deployment

Production multi-core firmware is packaged as `build/mosaic_flash.bin` (raw
programmer input), `build/mosaic_flash.hex` (simulation flash-model input), and
`build/mosaic_flash.json` (auditable placement and hashes). These are generated
from the same `boot_images.json` topology contract as the RTL.

Cold boot uses the existing x-heep silicon path:

1. Strap `boot_select=1` and `execute_from_flash=1`.
2. The immutable boot ROM enables the SPI memory-mapped window and jumps to
   `0x4000_0180`.
3. TITAN executes in place, validates the deployment magic, version, table CRC,
   and generated topology fingerprint.
4. TITAN copies every worker payload to its configured SRAM boot address and
   verifies its CRC32 before configuring or waking the TDU.

`mosaic_flash.json` also records SHA-256 for each binary so a host programmer or
release pipeline can authenticate the exact deployment artifact. Runtime CRC32
detects transfer/corruption errors; it is not a cryptographic secure-boot root.

`tb/mosaic_soc/run_fw.sh` exercises this path without hierarchical SRAM preload:
the testbench initializes only the external SPI flash model. An exit success
therefore covers boot-ROM handoff, TITAN XIP, worker copy/verification, TDU wake,
and execution by all configured workers.

## Generic per-image startup

Each generated `linker/image_<n>.ld` reserves a stack for every hart in that
image and exports the hart list, stack stride/base/end, and per-ordinal stack-top
symbols. `include/mosaic_runtime.h` mirrors the mapping.

For C applications, `startup/image_<n>_crt0.S` is emitted when the startup is
truthful: singleton images use their constant hart ID, while shared images
require every participating core to expose `mhartid`. The image's lowest hart
clears BSS exactly once after load, releases secondaries through an
initialized-data handoff word, and calls `mosaic_main(hart_id)`. Park/reset/wake
cycles reuse that initialized image state, which avoids clearing shared BSS
while another core is running. The code uses no atomic extension and only the
RV32E register subset.

Shared FazyRV, SERV/QERV, PicoRV32, Rocket, or BOOM images do not expose a
reliable per-instance `mhartid`, so their manifest records
`startup_source: null`; no misleading generic crt0 is emitted. Those images
must provide a topology-specific entry point (the checked-in production
workers use the TDU descriptor's `core_hint`) while still benefiting from the
generated linker and stack contract.
