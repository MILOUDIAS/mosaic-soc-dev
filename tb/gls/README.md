# Gate-level simulation

This testbench simulates the netlist produced by place and route, the gates
that are in the layout, with the GF180MCU PDK's own cell models. The netlist
boots by executing in place from a behavioural QSPI flash model and reports
through the pins of the block. No memory is preloaded, no signal is forced and
no internal signal is probed. If it passes, the block can be brought up on a
board the same way.

It complements RTL simulation: it catches a design whose RTL is correct and
whose implementation is not.

## Running it

It needs a completed hardening run (for `final/pnl/<design>.pnl.v`) and the PDK
clone under `flow/librelane/gf180mcu/`. Neither is in version control.

```bash
GLS_RUN=flow/librelane/experimental/runs/<tag> tb/gls/run_gls.sh
GLS_RUN=flow/librelane/experimental/runs/<tag> ./mosaic flow-runner run gls
./mosaic gls-triage flow/librelane/experimental/runs/<tag>
```

A pass ends with:

```
### RESULT: EXIT SUCCESS — gate-level netlist booted and reported 0
```

| Variable | Meaning |
|---|---|
| `GLS_RUN` | the run directory; the design name is read from its `resolved.json` |
| `GLS_DESIGN` | override the design (top module) name |
| `GLS_NETLIST` | a different netlist, for example the post-synthesis one |
| `GLS_FIRMWARE` | a different flash image |
| `GLS_SEQ_DELAY` | clock-to-Q delay of the flip-flop models in ns; default 1, and 0 restores zero delay |
| `GLS_MAXCYCLES` | watchdog, default 2,000,000 cycles |
| `GLS_PADWRAP` | a padframe model placed between the testbench and the block |
| `GLS_TOP` | the module the testbench instantiates when a padframe model is used |
| `GLS_POWERUP_INIT` | a power-up deposit list other than the default |
| `GLS_VCD`, `GLS_VCD_START`, `GLS_VCD_CYCLES` | capture switching activity for a window of cycles |

The firmware is the liveness image built by
`MOSAIC_CFG=<config> tb/mosaic_soc/run_generic.sh`.

## What this simulation is, and is not

**It is functional, not timing-annotated.** The GF180MCU cell models use
`ifnone` on edge-sensitive specify paths. IEEE 1364-2005 permits `ifnone` only
for state-dependent simple paths, and Icarus Verilog rejects the construct:

```
sorry: ifnone with an edge-sensitive path is not supported
```

The models are therefore compiled with `-DFUNCTIONAL`, which removes the specify
blocks and with them every path that SDF could annotate. `run_gls.sh --sdf`
refuses to run rather than simulate without delays and call the result
timing-annotated. Timing is established by static timing analysis at nine
corners.

**Flip-flops have a nonzero clock-to-Q delay.** With `-DFUNCTIONAL` the
flip-flops are primitives that switch in zero time, which makes the value a
flip-flop samples depend on event ordering, and inserting a buffer changes the
ordering. `run_gls.sh` adds a clock-to-Q delay to every sequential primitive in
a derived copy of the cell models; the PDK files are never written. The log
header records it:

```
### seq c2q : 1 ns on 18 sequential UDPs
```

`harness/evidence/gls.py` reads that line and labels a verdict from the
zero-delay model as race-prone. If a PDK update changes the models so that the
patch matches nothing, the runner refuses to run.

**Reset is released away from the clock edge.** The design has flip-flops with
asynchronous reset. If a testbench deasserts reset in the same time step as a
rising clock edge, the simulator's ordering of the two events depends on the
number of buffer stages in the reset tree and the clock tree. Two netlists that
are identical in logic and differ only in buffering then disagree on whether the
block boots. `gls_tb.sv` releases reset on the falling edge, half a cycle from
the capture edge, as a board would.

**It cannot see buffers.** Two netlists that differ only in buffer, clock, fill,
tie or antenna cells are the same netlist to this simulation. `gls-triage` and
`netlist-diff` use that fact: a difference confined to those cells cannot
explain a differing verdict.

## Power-up state

Most flip-flops in these designs have no reset. In a gate-level simulation they
start as unknown, the unknown value propagates, and the netlist never fetches
its first instruction. That looks like a slow simulation, not a broken one. RTL
simulation in Verilator hides the problem by starting everything at zero.

Silicon powers up to a definite value, so `gen_powerup_init.py` writes a list
that deposits a value on each such flip-flop. The deposit holds only until the
flip-flop's first clock edge.

```bash
tb/gls/gen_powerup_init.py <netlist.v> <output.svh>
```

Each design needs its own list, regenerated after every re-harden, because the
flip-flop names come from the netlist. No list is tracked: the post-layout
netlists are not in the repository, so the list is generated from the run's
netlist with `gen_powerup_init.py` and selected with `GLS_POWERUP_INIT`. Block
A behind its padframe model needs a list of its own, because the padframe
wrapper adds a level of hierarchy to every flip-flop name.

## The padframe model

Block A, hardened against an external padframe DEF, has no pad cells inside it.
It exposes the control terminals of each pad instead. `mosaic_block_a_padwrap.sv`
plays the part of the padframe so that the same testbench, firmware and flash
model apply. It models output enable, input enable and the pulls, and prints
`[PADWRAP] FAIL ...` when a pad control is wrong; `gls-triage` reports that as
`PASS_WITH_UNCHECKED_ASSERTIONS`. It does not model drive strength, slew or
input type, which a functional simulation cannot observe.

## The second simulator

`run_gls_cvc.sh` targets OSS CVC, which compiles specify blocks and supports SDF
annotation and random two-state initialisation. `mk_cells_cvc.py` writes a copy
of the cell models with the non-compliant `ifnone` keywords removed, and
`mk_spiflash_v2001.py` writes a Verilog-2001 flash model. The scripts are
complete, but CVC crashes while compiling a design of this size, with and
without specify data, so timing-annotated gate-level simulation is not
available. That flow also needs the `final/sdf/` files of a local run.

## Files

| File | Purpose |
|---|---|
| `gls_tb.sv` | the Icarus testbench |
| `run_gls.sh` | the functional gate-level runner |
| `gen_powerup_init.py` | writes a power-up deposit list from a netlist |
| `gls_powerup_init*.svh` | deposit lists generated from a run's netlist by `gen_powerup_init.py`; not tracked |
| `mosaic_block_a_padwrap.sv` | padframe model for Block A |
| `gls_tb_cvc.v`, `run_gls_cvc.sh` | the CVC testbench and runner |
| `mk_cells_cvc.py` | writes standards-compliant cell models for CVC |
| `mk_spiflash_v2001.py`, `spiflash_v2001.v` | Verilog-2001 flash model |
