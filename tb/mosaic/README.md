# CPU subsystem testbench

This bench builds the generated multi-core `cpu_subsystem` on its own, without
the rest of the SoC, and runs each wrapped core against a private OBI memory. It
answers one question quickly: do the cores come out of reset, fetch through
their wrappers and execute?

The configuration is `configs/mosaic_sim.yaml`: one SERV, one QERV and one
FazyRV, all workers. The test program is assembled by hand, so the Verilator
bench needs no RISC-V compiler.

## Running it

```bash
tb/mosaic/run.sh          # SystemVerilog bench: wakes every hart, checks execution
tb/mosaic/cocotb/run.sh   # cocotb bench: dormant, selective wake, wake all
```

`run.sh` generates the RTL for `configs/mosaic_sim.yaml`, builds the model, runs
it, and then regenerates the default configuration so the rendered files are
left as they were.

## What the SystemVerilog bench checks

Each hart has its own instruction and data memory (`tb_obi_mem.sv`), loaded at
the boot address `0x180` with:

```
addi x1, x0, 0x55      ; the sentinel value
addi x2, x0, 0x40
sw   x1, 0(x2)         ; mem[0x40] = 0x55
jal  x0, 0             ; spin
```

Every other word is `jal x0, 0`, so a stray fetch spins in place. All three
cores are workers and therefore dormant after reset. The bench pulses the wake
input of every hart (the path the TDU drives in the SoC) and checks, per hart:

- **liveness:** the core issues bus requests through its wrapper;
- **execution:** the core writes `0x55` to address `0x40`.

A pass prints:

```
=== MOSAIC multi-core TB: PASS — all cores alive + executed ===
```

## What the cocotb bench checks

`cocotb/test_mosaic.py` tests the wake path in three phases:

1. **No wake.** Every worker stays parked: no bus request, `core_sleep_o` high,
   no sentinel.
2. **Wake hart 0 only.** Exactly one core runs. The other two stay parked, which
   shows that wake is per hart.
3. **Wake the rest.** All three run and `core_sleep_o` falls.

## The memory model

`tb_obi_mem.sv` registers the grant and the response-valid signal one cycle
after a request, and keeps the read data combinational on the current address.
The registered handshake breaks a combinational loop through FazyRV's bus state
machine. The combinational data avoids a skew for cores that change their
address on every access. Both properties are needed for all three cores to run
against one model.

## Scope

cv32e20 is not in this bench. The generated `cpu_subsystem` leaves that core's
extension interface ports unconnected, and a stand-alone top level cannot
elaborate unconnected interface ports. cv32e20 is covered by the full-SoC
benches in `tb/mosaic_soc/`.

## Files

| File | Purpose |
|---|---|
| `run.sh` | generate, build, run, restore |
| `mosaic_multicore_tb.sv` | the SystemVerilog testbench |
| `tb_obi_mem.sv` | the OBI memory model |
| `cocotb/` | the cocotb wake test and its top level |
