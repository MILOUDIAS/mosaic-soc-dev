# MOSAIC full-SoC functional simulation (Verilator)

Builds and runs the **complete** multi-core SoC — the x-heep `testharness`
wrapping the generated `core_v_mini_mcu` (cv32e20 **TITAN** + 2× fazyrv + 4× serv
+ TDU + iDMA + system_bus + all peripherals + debug) — and boots a tiny RV32IMC
program on TITAN through the real bus fabric, memory, and boot ROM.

```bash
tb/mosaic_soc/run.sh          # build + run the full-SoC sim (tb_top top)
```

## Topology-generic all-hart gate

`run_generic.sh` consumes the generated boot manifest and builds one liveness
image per boot slot. It initializes the shared control range, requires the exact
`sentinel[hart] == hart + 1` value for every configured hart, then exits through
the real `soc_ctrl` path.

```bash
MOSAIC_CFG=configs/mosaic_sim.yaml tb/mosaic_soc/run_generic.sh
MOSAIC_CFG=configs/mosaic_rocket_titan.yaml tb/mosaic_soc/run_generic.sh
MOSAIC_CFG=configs/mosaic_boom_titan.yaml tb/mosaic_soc/run_generic.sh
```

Worker-only configs are still `profile: testbench`: only hart 0 is released at
POR to perform targeted TDU dispatch. Production AMP configs continue to require
one leading TITAN. Rocket/BOOM use uncached translated control windows and remain
simulation-only.

## TDU wake-and-run demo (configs/mosaic_wake_demo.yaml) — PASSING ✅

`run.sh` targets a 3-core demo (1 cv32e20 TITAN + 1 fazyrv ATLAS + 1 serv NANO, each
with its own boot address): TITAN boots, **wakes both workers via the TDU**, each woken
worker runs its own program (`prog/{start,atlas,nano}.S`) through the shared system bus
and writes a unique sentinel, and TITAN reaches **`EXIT SUCCESS`**.

End-to-end, in the full SoC:
- **TITAN** boots from the boot ROM, writes `0xC0FFEE00`@0x3000, stores `0x6` to TDU
  WAKE_REQ (`0x200A000C`), then polls 0x3004 + 0x3008 until both workers report in.
- **ATLAS (fazyrv, hart 1)** wakes, runs atlas.S (0x1000→0x1004→…), writes
  `0xA71A5000`@0x3004.
- **NANO (serv, hart 2)** wakes, runs nano.S, writes `0x4E414E00`@0x3008.
- TITAN sees both sentinels → `soc_ctrl` EXIT_VALUE=0/EXIT_VALID=1 → **`EXIT SUCCESS`**.

Five real RTL fixes were needed to bring this up (all in `hw/`):
1. **Packed wake ports** — `cpu_subsystem` `core_wake_i`/`core_sleep_o`/`debug_req_i`
   made packed `[NUM_HARTS-1:0]` (was unpacked); removed the packed↔unpacked adaptation
   in the top. Fixes the worker wake-latch miss.
2. **Range-direction reversal** — `cpu_subsystem`'s per-hart array ports were ascending
   `[NUM_HARTS]` while the top/`system_bus` are descending `[N-1:0]`; unpacked-array
   connection maps element-by-position, so they reversed (TITAN's traffic showed on hart
   index 2). Made the ports descending. (NH=1 hid this — why single-core passed.)
3. **serv_sci OBI bridge** — was `wb_ack = gnt & rvalid`, which never fires on the real
   xbar (gnt and rvalid land on different cycles). Fixed to a single-outstanding bridge
   (ack on rvalid). serv now runs.
4. **fazyrv_sci OBI bridge** — same single-outstanding fix + a read-data hold latch.
5. **fazyrv clock-stall adapter** — FazyRV is built for *combinational* memory and reads
   the fetched word during its fetch cycle; the registered bus made it read 0 and trap.
   `fazyrv_sci` now freezes FazyRV's clock while a fetch is outstanding (negedge-latched,
   glitch-free) so the registered bus looks combinational. Latency-adaptive and
   transparent for a 0-latency memory (no cocotb regression).

### Icarus Verilog attempt (`run_icarus.sh`) — not viable, documented

Tried Icarus 13.0 (event-driven, to dodge Verilator's evaluation-order quirks).
**Icarus cannot compile the x-heep SoC.** Its SystemVerilog frontend rejects ≥3 idiom
classes that pervade the vendored IP, two of them in **non-excludable foundational RTL**:

1. **Package-function parameter defaults** — `parameter int X = cf_math_pkg::idx_width(N)`
   is a syntax error in Icarus. Used by the pulp iDMA (stubbable) **and** by the pulp
   common_cells `addr_decode.sv:65` / `lzc.sv:25` that the **system crossbar**
   (`system_xbar.sv`) is built from — cannot be excluded.
2. **Named struct-pattern parameters** — `parameter t X = '{field: val, ...}` is a syntax
   error (positional `'{val, ...}` parses fine). Used by the **cve2 TITAN core**
   (`cve2_cs_registers.sv`) and `cv32e40x_pkg.sv` (reached via the `if_xif` interface).
3. **Unpacked-array parameters** — `sorry: unpacked array parameters not supported yet`,
   in OpenTitan reg packages (`spi_device`/`ams`/`dlc`).

Everything Icarus could *not* parse that the demo doesn't actually instantiate is excluded
by `run_icarus.sh` (iDMA→`idma_stub.sv`, `hw/ip_examples/`, `spi_device`, the cv32e40x
*core* keeping only `if_xif.sv`). But the non-excludable failures (the crossbar's
common_cells + the cve2 core itself) would require shimming foundational functional RTL,
and the serv/fazyrv workers + debug + reg/OBI interfaces were never even reached by the
parser. This is an open-ended port of x-heep to a simulator it was never designed for.
**Questa/VCS** is the right event-driven tool (full SV support) but is not installed here.
The `run_icarus.sh` harness + `*_stub.sv` files are kept as a precise record of what Icarus
would need. Note these are **simulator** limitations — the RTL is correct (the full SoC
elaborates clean, the single-core functional sim passes, the TDU wake mechanism is proven),
and the project deliverable (DRC/LVS-clean GDSII) does not depend on this sim.

(The single-core full-SoC functional sim — TITAN boots + runs a program → EXIT
SUCCESS — passes; that milestone, and the obi_fifo deadlock fix it required, are
described below. Run it with `MOSAIC_CFG=mosaic.yaml` + a single-TITAN program.)

## Single-core full-SoC functional sim — PASSING ✅

The full multi-core SoC **boots and executes a program end-to-end** in Verilator:
TITAN (cv32e20) runs the boot ROM, jumps to the program at `0x180`, writes the
sentinel `0xC0FFEE00` to `0x200` and the loop result `16` to `0x204` through the
real bus + SRAM, then writes `soc_ctrl` EXIT_VALUE=0/EXIT_VALID=1 → **`EXIT
SUCCESS`**. Confirmed on both the official `tb_top` harness (`run.sh`) and the
`mosaic_tb.sv` diagnostic top. First end-to-end functional sim of the whole MOSAIC
SoC.

Two root-cause issues had to be fixed to get here — both surfaced because the full
multi-core SoC had **never** been simulated before, so these paths were never hit:

1. **iDMA-adjacent `obi_fifo` deadlock (REAL fix, in the RTL).** The AO-peripheral
   `obi_fifo` producer FSM read its own **output port** `producer_resp_o.rvalid`
   in the same `always_comb` that drives `gnt`. When a response and the next
   request coincide, Verilator evaluates the FSM with a *stale* `rvalid`, so `gnt`
   never re-asserts and the bus deadlocks after the **first** instruction fetch
   (boot ROM word 0 returns, word 1 never granted). Fixed in
   `hw/ip/obi_fifo/obi_fifo.sv` to read the internal `fifo_resp_pop`
   (`== producer_resp_o.rvalid`) — functionally identical for synthesis, but it
   breaks the output-port read-back that confused Verilator. This is a genuine SoC
   fix, not a sim hack.

2. **cve2 clock-gate bootstrap (sim-only override).** The vendored
   `cve2_clock_gate` is an `always_latch`; its combinational feedback
   (`clk_o → core_busy → clock_en → en_i`) does not converge in this Verilator
   build, so cve2 never bootstraps its gated clock and TITAN never fetches. The
   sim substitutes a negedge-flop clock gate (`cve2_clock_gate.sv` here, swapped in
   via `gen_filelist.py`) — glitch-free, registered (so Verilator converges), and
   still honours the enable. Functional sims routinely replace power-gating clock
   gates; the real latch gate is correct for synthesis/event-driven sim.

The serial worker cores (serv/qerv/fazyrv) are additionally exercised by `tb/mosaic`
(the cocotb wake-loop test). Next firmware work: wake the workers from TITAN via
the TDU and run real per-core programs.

## Files

```
prog/start.S, link.ld   tiny RV32IMC program (entry @0x180 = boot_address);
                        writes a sentinel + soc_ctrl EXIT_VALUE/VALID. Built with
                        compile(rv32imc/ilp32) + ld (the installed toolchain has
                        no rv32imc multilib, so libc/BSP can't be linked).
gen_filelist.py         assembles the Verilator filelist from the FuseSoC sim .vc:
                        remaps build-copies -> live hw/ + tb/, drops the stale
                        idma copies + pulp obi_pkg (collision), shadows tb_util.svh
                        with a DPI-export-free copy (dodges a Verilator tb_loadHEX
                        codegen bug), keeps uartdpi/jtag DPI C, makes CFLAGS absolute.
tb_util.svh             DPI-export-free shadow of tb/tb_util.svh (see above).
run.sh                  gen RTL -> build program -> verilator --binary (tb_top) -> run.
mosaic_tb.sv            diagnostic top: dumps SRAM sentinel, TITAN fetch/write
                        addresses, and the reset/clock-gate state.
```

## Notes / gotchas found while bringing this up

- Toolchain (`riscv32-unknown-elf-gcc` 16.1) defaults to `ilp32d` and has **no
  multilib** — compile with explicit `-march=rv32imc -mabi=ilp32` and link with
  `ld` directly (the gcc driver fails multilib selection at link).
- x-heep's `sw/cmake/riscv.cmake` sets `-march` but **not `-mabi`**, so the CMake
  app build fails the bare-compiler test on this toolchain (pass
  `COMPILER_FLAGS=-mabi=ilp32`, though the multilib link still blocks libc apps).
- `boot_address` reset value is **0x180** (not 0) — programs must be linked there.
- Verilator 5.047 mis-generates the DPI-**export** of `tb_loadHEX` (64 KB array
  local → `this->` in a free function) — hence the DPI-free `tb_util.svh` shadow.
```
