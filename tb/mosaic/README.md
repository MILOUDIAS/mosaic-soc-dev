# MOSAIC multi-core simulation harness

A self-checking **Verilator** testbench that builds the **real generated
multi-core `cpu_subsystem`** and runs the SCI-wrapped cores against per-hart OBI
memories — to answer "does the PoC multi-core SoC actually come alive and run?".

The test program is hand-assembled RV32I, so this harness needs neither cocotb
nor a RISC-V toolchain — only Verilator. (Both `cocotb` 2.1 and
`riscv32-unknown-elf-gcc` 16.1 are in fact available in the oss-cad-suite, and
can be used for a richer cocotb-driven or compiled-program flow; see the bottom.)

## What it does

1. Generates the SoC RTL for `configs/mosaic_sim.yaml` (SERV + QERV + FazyRV).
2. Verilates the generated `cpu_subsystem` + `tb_obi_mem` + the testbench.
3. Gives each hart its own instruction + data OBI memory (`tb_obi_mem.sv`)
   preloaded at the boot address `0x180` with a tiny program:
   ```
   addi x1, x0, 0x55      ; x1 = sentinel
   addi x2, x0, 0x40      ; x2 = 0x40
   sw   x1, 0(x2)         ; mem[0x40] = 0x55
   jal  x0, 0             ; spin
   ```
   (every other word is `jal x0,0`, so a stray fetch just spins).
4. Releases reset. All three sim cores are **workers** (no TITAN in this config),
   so they boot **dormant** and only run once a per-hart wake pulse is applied
   (`core_wake_i` — the TDU's `core_wake_o` path). The TB pulses wake for every
   hart, then checks, per hart:
   - **integration / liveness** — the core issues bus requests through its SCI
     wrapper (it came out of reset and the fetch path works);
   - **execution** — the core writes the sentinel `0x55` to `0x40` (fetch +
     decode + ALU + store all work).
5. Restores the default PoC config (`mosaic.yaml`) so the tree is left as found.

## Run

```bash
tb/mosaic/run.sh         # pure-SV Verilator TB (wakes all harts, checks execute)
tb/mosaic/cocotb/run.sh  # cocotb TB: full dormant → selective-wake → all-wake loop
```

Files: `tb_obi_mem.sv` (OBI memory model), `mosaic_multicore_tb.sv` (SV testbench),
`cocotb/` (cocotb wake-loop test + DUT wrapper), `run.sh` (generate → verilate →
run → restore), `configs/mosaic_sim.yaml` (DUT config).

## The TDU wake loop (cocotb test)

`cocotb/test_mosaic.py` exercises the closed `TDU.core_wake_o → cpu_subsystem →
core` loop in three phases:

1. **No wake** — every worker stays parked: `alive=0`, `core_sleep_o=1`, sentinel
   never written. Proves the dormancy gating actually holds the cores.
2. **Wake hart 0 only** — exactly one core comes alive and retires its program;
   the other two stay parked. Proves wake is **per-hart**, not global.
3. **Wake the rest** — all three run and write the sentinel; `core_sleep_o`
   deasserts. The loop the TDU SoC test flagged as open is now closed.

## Current results

```
hart 0 serv  (W=1) : alive=1  executed=1   [PASS]
hart 1 qerv  (W=4) : alive=1  executed=1   [PASS]
hart 2 fazyrv      : alive=1  executed=1   [PASS]
integration : 3/3 cores alive
execution   : 3/3 cores retired the test program
=== MOSAIC multi-core TB: PASS — all cores alive + executed ===
```

All three SCI-wrapped core types boot, fetch, run the ALU ops, and complete the
store through their wrappers on the per-hart OBI fabric — the multi-core
integration works. The probe (removed) confirmed FazyRV's PC walking
`0x180→0x184→0x188(sw)→0x18c(loop)` and issuing the data write of `0x55` to `0x40`.

## Bugs this harness caught (now fixed)

1. **FazyRV reset polarity inverted (RTL bug)** — `hw/sci/fazyrv_sci.sv` connected
   `.rst_in(~rst_ni)`, but FazyRV's `rst_in` is **active-low** (`fazyrv_pc.sv`:
   `if (~rst_in)`), like x-heep's `rst_ni`. The inversion held FazyRV in reset
   during normal operation: the PC stayed pinned at `BOOTADR` (endless re-fetch of
   `0x180`) and the regfile write-enable was gated off (`ram_we & rst_in`), so no
   instruction ever retired. Fixed to `.rst_in(rst_ni)`. This is a real PoC bug —
   any FazyRV (ATLAS tier) instance would never execute.
2. **`CpuType` enum overflow** — `core_v_mini_mcu_pkg.sv.tpl` emitted
   `localparam cpu_type_e CpuType = <first-core-name>`, but `serv`/`qerv`/
   `fazyrv`/`ibex` aren't members of `cpu_type_e`. Any config whose first core is
   an SCI core failed to compile. Fixed by falling back to a valid enum value.
3. **FazyRV `CONF=CSR` + `RFTYPE=LOGIC` invalid combo** — the template defaulted
   `rftype` to `LOGIC` while the wrapper defaulted `conf` to `CSR`, which trips a
   FazyRV elaboration assertion ("use the BRAM implementation for CSR support").
   This affected the PoC too. Fixed by defaulting `rftype` to `BRAM_DP_BP`.
4. **`TDU.core_wake_o` not wired to the cores (open scheduling loop)** — the TDU
   produced a correct per-hart wake (verified by `tb/tdu/soc`) but nothing
   consumed it: `cpu_subsystem` had no wake input and tied `fetch_enable=1`, so
   every worker ran unconditionally out of reset. Fixed by adding a per-hart
   `core_wake_i` to `cpu_subsystem`, wiring it from `TDU.core_wake_o` in
   `core_v_mini_mcu.sv`, and gating each worker's `fetch_enable` (and, in the
   serial SCI wrappers, the core reset + OBI request strobes) behind a wake latch.
   This harness's cocotb test now demonstrates the closed loop directly.

## A note on the OBI memory model

`tb_obi_mem.sv` registers the ack (gnt + rvalid one cycle after a request is
accepted) but keeps **combinational read data** on the current request address.
The registered ack breaks the `stb→ack→stb` combinational path through FazyRV's
Wishbone FSM; the combinational read data avoids an address/data skew for masters
that change their address every access (FazyRV does; SERV holds it). Both are
needed for all three cores to run from one memory model.

## Scope: why cv32e20 (TITAN) isn't in this harness

The generated `cpu_subsystem` leaves cv32e20's CV-X-IF SystemVerilog interfaces
unconnected (`.xif_compressed_if()` …). A standalone Verilator top can't resolve
dangling `interface` ports, so cv32e20 can only be elaborated through x-heep's
full `core_v_mini_mcu` + `testharness` (which also needs a boot ROM and a
RISC-V GCC toolchain — neither is installed here). cv32e20 is a silicon-proven
industry core; the novel MOSAIC integration risk lives in the SCI wrappers and
the per-hart OBI fabric, which this harness exercises directly.

To run the full PoC (incl. cv32e20) once a RISC-V toolchain is available, use
x-heep's flow: `make mosaic-gen` then `make verilate` + an application built with
`make app`, driven through `tb/testharness.sv`.
