# iDMA functional tests (cocotb + Verilator)

Verifies that MOSAIC's multi-stream iDMA wrapper actually moves data through
every configured OBI master port, at two integration levels.

The iDMA datapath under test:
`reg frontend (idma_reg32_3d) → ND midend → rw_obi backend → pulp-OBI⇄x-heep-OBI`.

## Run

```bash
tb/idma/cocotb/run.sh          # runs BOTH levels
# or individually:
make -C tb/idma/cocotb SIM=verilator TOPLEVEL=idma_tb_top        # per-block
make -C tb/idma/cocotb SIM=verilator TOPLEVEL=idma_soc_tb_top    # SoC-level
```

Needs cocotb + Verilator only (no RTL generation, no RISC-V GCC — the iDMA is
static vendored RTL and the test drives the register frontend directly).

## What each level does

The wrapper defaults to `DMA_NUM_MASTER_PORTS` independent execution streams.
Stream `n` owns a private descriptor bank at `DMA_START + n * 0x200`, selects
its execution lane through `NEXT_ID[n]`, and maps directly to OBI read/write
master port `n`. Stream zero keeps the original offsets:

```
CONF (0x00)     = 0x9000      # src_protocol=OBI, dst_protocol=OBI
DST_ADDR (0xd0) = <dst>
SRC_ADDR (0xd8) = <src>
LENGTH (0xe0)   = <bytes>
REPS_2 (0xf8)   = 1           # 1D transfer
read NEXT_ID (0x44)           # launches the transfer
```

| Level | Top | Memory | What it adds |
|-------|-----|--------|--------------|
| **per-block** | `idma_tb_top` | `tb_idma_mem` (dual-port) | iDMA read + write masters to a 2-port memory |
| **SoC-level** | `idma_soc_tb_top` | `tb_idma_xbar_mem` (1 port, arbitrated) | read/write **contend** for one memory (write priority) — models the SoC crossbar serialising the iDMA's two masters onto the shared SRAM |

Both tops run five checks: legacy 1D copy and completion IRQ, atomic multi-hart
stream ownership, 2D expansion, 3D expansion, and concurrent two-stream copies
that prove both configured read/write master ports were active. Current result:
`TESTS=5 PASS=5` at both levels.

The wrapper adds three registers to every private stream window:

| Offset | Register | Semantics |
|--------|----------|-----------|
| `0x180` | `OWNER_CLAIM` | Atomic claim-if-zero with a nonzero hart token |
| `0x184` | `OWNER_RELEASE` | Clear only when the written token owns the stream |
| `0x188` | `OWNER` | Current owner token |

The matching driver is in `sw/device/lib/drivers/idma/`. It validates ownership,
programs 1D/2D/3D descriptors, launches the correct stream, polls busy/done IDs,
and provides bounded waits.

## Error-policy limitation in vendored iDMA 0.6.5

The `rw_obi` backend supports `NO_ERROR_HANDLING` only. Its
`ERROR_HANDLING` generate branch calls `$fatal` with "only implemented for AXI
to AXI DMA", and `idma_pkg::eh_action_e` defines `CONTINUE` and `ABORT` only;
there is no `REPLAY`. The wrapper exposes `ERROR_CAP` but rejects unsupported
values during elaboration. Continue/abort/replay cannot truthfully be enabled
until upstream implements OBI error responses and recovery.

## Background: the iDMA bugs this fixed

The vendored iDMA was non-buildable; bringing it up required:
1. **Wrapper rewrite** — `idma_xheep_wrapper.sv` was written against a different
   iDMA version (wrong `IDMA_TYPEDEF_*` arity, wrong submodule param/pin names).
   It was rewritten against the **latest iDMA 0.6.5** modules: build the 1D/ND
   request types + OBI meta channels, instantiate `idma_reg32_3d` + `id_gen` +
   `idma_nd_midend` + `idma_backend_rw_obi`, and convert the backend's
   pulp-platform OBI masters to x-heep's simpler `obi_pkg` bus.
2. **Vendored the OBI package** — `hw/vendor/pulp_platform/obi` (v0.1.2), for the
   `OBI_TYPEDEF_*` macros the backend needs. Added `obi.core`.
3. **Fixed `idma.core`** — it never declared the `idma/typedef.svh` include dir
   (so the build couldn't find it) and listed `*_synth` wrappers that pull an
   un-vendored AXI backend. Now lists the exact rw_obi closure + the obi dep.

## Files

```
cocotb/test_idma.py    1D/2D/3D, ownership, IRQ, multi-stream tests
cocotb/Makefile        cocotb+Verilator build (TOPLEVEL selects the level)
cocotb/run.sh          runs both levels
idma_tb_top.sv         per-block top (iDMA + dual-port memory)
idma_soc_tb_top.sv     SoC-level top (iDMA + shared arbitrated memory)
tb_idma_mem.sv         independently multiported OBI memory
tb_idma_xbar_mem.sv    shared single-port memory arbitrating every iDMA port
test_idma_driver.c     host register-map/driver unit test
```
