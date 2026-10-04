# iDMA tests

These tests check that the MOSAIC iDMA wrapper (`idma_mosaic_wrapper`) moves
data through every OBI master port it has, at two levels of integration.

The datapath under test is the register frontend (`idma_reg32_3d`), the
N-dimensional midend, the read/write OBI backend, and the conversion from the
backend's OBI types to X-HEEP's.

## Running them

```bash
tb/idma/cocotb/run.sh                                           # both levels
make -C tb/idma/cocotb SIM=verilator TOPLEVEL=idma_tb_top       # block level
make -C tb/idma/cocotb SIM=verilator TOPLEVEL=idma_soc_tb_top   # SoC level
```

They need cocotb and Verilator only. The iDMA RTL is vendored and static, and
the test drives the register frontend directly, so no RTL generation and no
RISC-V compiler are involved.

## The two levels

| Level | Top module | Memory | What it adds |
|---|---|---|---|
| block | `idma_tb_top` | `tb_idma_mem`, one port per master | read and write masters on independent ports |
| SoC | `idma_soc_tb_top` | `tb_idma_xbar_mem`, one arbitrated port | the read and write masters contend for one memory, as they do behind the SoC crossbar |

Both run five checks: a one-dimensional copy with its completion interrupt,
atomic ownership of a stream by a hart, a two-dimensional transfer, a
three-dimensional transfer, and two concurrent streams that show every
configured read and write master port was active.

## Register interface

The wrapper has `DMA_NUM_MASTER_PORTS` independent streams. Stream `n` has its
own register window at `DMA_START + n * 0x200` and drives OBI read and write
master port `n`. A one-dimensional copy on stream 0:

```
CONF     (0x00) = 0x9000      # source and destination protocol: OBI
DST_ADDR (0xd0) = <destination>
SRC_ADDR (0xd8) = <source>
LENGTH   (0xe0) = <bytes>
REPS_2   (0xf8) = 1           # one-dimensional
read NEXT_ID (0x44)           # starts the transfer
```

The wrapper adds three registers to every stream window, so that several harts
can share the engine:

| Offset | Register | Meaning |
|---|---|---|
| `0x180` | `OWNER_CLAIM` | claim the stream with a nonzero hart token; succeeds only if it is free |
| `0x184` | `OWNER_RELEASE` | release the stream; takes effect only for the owning token |
| `0x188` | `OWNER` | the current owner token |

The matching software driver is in `sw/device/lib/drivers/idma/`.
`test_idma_driver.c` is a host-side unit test of the driver against the register
map.

## Error handling

The vendored iDMA (a commit shortly after release 0.6.5, recorded in
`hw/vendor/mosaic/idma/UPSTREAM`) implements error handling only for its AXI
backend. Its OBI backend supports `NO_ERROR_HANDLING` only, and the wrapper
rejects any other setting at elaboration. Continue, abort and replay on a bus
error are therefore not available.

## Files

| File | Purpose |
|---|---|
| `cocotb/test_idma.py` | the five checks |
| `cocotb/Makefile` | cocotb and Verilator build; `TOPLEVEL` selects the level |
| `cocotb/run.sh` | runs both levels |
| `idma_tb_top.sv` | block-level top |
| `idma_soc_tb_top.sv` | SoC-level top |
| `tb_idma_mem.sv` | memory with one port per master |
| `tb_idma_xbar_mem.sv` | single-port memory that arbitrates all iDMA ports |
| `test_idma_driver.c` | host-side driver test |
