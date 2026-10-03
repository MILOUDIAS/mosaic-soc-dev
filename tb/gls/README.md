# Gate-level simulation — Chipathon Block A

Simulates the **post-place-and-route netlist** — the gates that are in the GDS —
with the PDK's own cell models, booting XIP from a behavioural QSPI flash and
reporting only through the **22 pins the MPW integrator bonds**. No backdoor
memory load, no hierarchical forces, no internal probes: if this passes, the
part can be brought up on a board the same way.

It is the complementary check to bugs 28 and 31, which were RTL that elaborated
and simulated happily while being wrong. This catches the opposite failure —
RTL correct, implementation broken.

## Status

| | |
|---|---|
| **Functional GLS (Icarus)** | ✅ **passes** — boots in 12 399 cycles vs the RTL's ~12 400 |
| **Sequential clock-to-Q** | ✅ 1 ns by default — the zero-delay flop race is gone |
| **Timing-annotated GLS (CVC)** | ❌ blocked by a CVC crash — see below |

```bash
./run_gls.sh                      # functional GLS on the routed netlist
GLS_NETLIST=<nl.v> ./run_gls.sh   # post-synthesis netlist instead
GLS_SEQ_DELAY=0 ./run_gls.sh      # the old zero-delay oracle, for comparison
```

```
[GLS] reset released at 1950000
[GLS] status_valid_o asserted at 1239950000 after 12399 cycles, status_o = 0x00
### RESULT: EXIT SUCCESS — gate-level netlist booted and reported 0
```

Cycle-for-cycle agreement with RTL is the result that matters: synthesis, CTS
and place-and-route preserved the behaviour.

## Sequential clock-to-Q, and what it did not fix

`-DFUNCTIONAL` strips the specify blocks, and with them the CLK→Q delay, so the
flops were UDP primitives switching in zero time. That is the textbook race:
what a flop samples depends on event ordering, and inserting a buffer changes
ordering without changing logic. So `run_gls.sh` now patches a nonzero
clock-to-Q onto every sequential UDP in a **derived copy** of the cell models —
the PDK files are read-only inputs and are never written. Combinational cells
stay zero-delay; only the clock-to-Q arc changes, because only that arc carried
the race. The log header records which oracle ran:

```
### seq c2q : 1 ns on 18 sequential UDPs
```

`harness/evidence/gls.py` reads that line, and a verdict from the zero-delay
oracle is labelled race-prone. A log with no such line predates the change and
is treated as race-prone, because it is.

If a PDK bump ever changes the cell models so the patch matches nothing,
`run_gls.sh` **refuses** rather than running a zero-delay simulation while
claiming a delay it does not have.

### What actually caused the 2026-08-12 "failure"

Not the clock-to-Q race. The bench released reset **in the same timestep as
the capture edge**:

```systemverilog
repeat (20) @(posedge clk);
rst_n = 1'b1;              // same instant as the rising edge
```

This design has `dffrnq`/`dffsnq` flops with asynchronous reset. Deasserting
`rst_n` on the edge means each of them sees `RN` rise at the instant `CLK`
rises, and which the simulator delivers first is decided by how many **delta
cycles** the reset tree and the clock tree each take. Both trees are
zero-delay combinational, so that count is exactly the number of buffer
stages. **Buffering therefore decided the verdict** — which is why two
netlists identical in logic, connectivity and cell function disagreed on
whether the part boots.

The evidence is a bounded VCD of the first 250 cycles from each run, diffed on
the 5 587 flop `Q` outputs, whose instance names are identical in both. The
first state divergence is at **t = 1 951 ns**, one clock-to-Q after reset
released at 1 950 ns, and the first five flops to diverge are `dffrnq` and
`dffsnq` — the asynchronous ones.

Releasing half a cycle away is what real bring-up does, and it settles it:

| netlist | reset on the edge | reset off the edge |
|---|---|---|
| `blocka_signoff` (slew margin 10) | ✅ 12 399 cyc | ✅ **12 400** |
| `blocka_reharden` (slew margin 45) | ❌ watchdog 40 001 | ✅ **12 400** |

Both netlists now boot, to the same cycle. **`GRT_DESIGN_REPAIR_MAX_SLEW_PCT:
45` is exonerated** — there was never a design defect here.

### Which fix was load-bearing

The reset-release fix, on its own. With `GLS_SEQ_DELAY=0` — the old zero-delay
oracle — both netlists still boot in 12 400 cycles once reset is released off
the edge. So the clock-to-Q patch did **not** fix this incident; it removes a
different and genuine race (zero-delay UDP flops racing on *data capture*),
and it is kept because that race is real, not because it was the culprit here.

For the record, the clock-to-Q sweep before the reset bug was found — one
variable, everything else held. It is a clean negative result, and it is what
ruled the c2q race out and sent the search to the VCD:

| netlist | 0 | 0.1 ns | 1 ns | 2 ns | 5 ns |
|---|---|---|---|---|---|
| `blocka_signoff` | ✅ 12 399 | — | ✅ 12 399 | — | ✅ 12 399 |
| `blocka_reharden` | ❌ 40 001 | ❌ 40 001 | ❌ 40 001 | ❌ 40 001 | ❌ 40 001 |

The structural comparison that made the failure impossible to attribute to the
netlist, and so forced the search onto the bench:

| check | result |
|---|---|
| logic instance names | identical, 0 only in either |
| input-pin drivers, buffer chains collapsed | **0 mismatches across 76 910 pins** |
| cell functions | 73 type differences, every one a drive-strength resize or `clkinv`↔`inv` |
| `clkinv_1` vs `inv_1` models | byte-identical (`not MGM_BG_0( ZN, I )`) |
| clock cone from `clk_i` | same 5 591 sequential endpoints |
| reset cone from `rst_ni` | same 2 474 sequential endpoints |
| `assign` statements, non-PDK macros | none in either |

## Why there are two flows

**Icarus cannot do timing annotation on this PDK.** The GF180 cell models use
`ifnone` on edge-sensitive specify paths, which iverilog rejects:

```
sorry: ifnone with an edge-sensitive path is not supported
```

so they must be compiled `-DFUNCTIONAL`, which strips the specify blocks — and
with them the very paths SDF would annotate. `run_gls.sh --sdf` therefore
**refuses** rather than running zero-delay and calling it timing-annotated.

**CVC** (OSS CVC 7.00b, IEEE 1364-2005) does compile specify blocks, has SDF
annotation and `+min/typ/maxdelays`, and offers `+random_2state=<seed>` — a
better power-up model than Icarus allows. `run_gls_cvc.sh` is complete and
correct as far as it goes, but CVC **segfaults** compiling this design (peak RSS
210 MB of 7.3 GB available, so a bug at scale, not memory). The same patched
library compiles and simulates a small design in 0.1 s.

**Re-tested 2026-08-13 on `blocka_slew32`, and the earlier diagnosis was too
narrow.** This section used to blame the full specify library. It is not that:

| variant | result |
|---|---|
| baseline (`+define+USE_POWER_PINS`, `+maxdelays`, `+sdfverbose`) | segfault |
| without `+sdfverbose` | segfault |
| plain `nl` netlist, no power pins, testbench patched | segfault |
| **`+nospecify +notimingchecks`** | **segfault** |

It crashes with no specify data at all, so the trigger is design size outright,
not timing annotation. That rules out every workaround available here and
leaves the conclusion below unchanged — but pointed at the right cause, so
nobody re-runs this bisect.

Timing coverage is therefore STA's, at nine corners. Closing this gap needs a
commercial simulator or a newer CVC.

## The PDK defect

The models are **not standard-compliant**:

```verilog
ifnone
 (posedge A1 => (ZN:A1)) = (1.0,1.0);
```

IEEE 1364-2005 §14.2.6 permits `ifnone` only as the default for *state-dependent
simple* paths, never edge-sensitive ones. Two independent simulators reject it
and both are right:

| | |
|---|---|
| iverilog | `sorry: ifnone with an edge-sensitive path is not supported` |
| CVC | `ERROR [1012] ifnone path illegal - has edge or is state dependent` |

`mk_cells_cvc.py` removes the 120 illegal keywords and keeps the paths (225
legal state-dependent blocks are untouched), so they survive as SDF annotation
targets instead of losing their delays. Worth reporting upstream.

## Power-up

**4 081 of the design's 5 587 flops are plain `dffq_1` with no reset.** At time
zero they are X, and X-propagation stalls the netlist — the first attempt sat at
126 000 cycles with the QSPI pins stuck at `x`. Verilator hides this by
zero-initialising, which is why no RTL run ever showed it.

Real silicon powers up to a definite 0 or 1, so:

- **Icarus:** `gen_powerup_init.py` emits a `$deposit` per flop. The deposit
  holds only until each flop's first clock edge.
- **CVC:** `+random_2state=<seed>` — random 0/1 for all state, and a different
  seed is a different power-up state. Stronger, and worth sweeping on a design
  with this much unreset state.

## Files

| | |
|---|---|
| `gls_tb.sv` | Icarus testbench (SystemVerilog) |
| `run_gls.sh` | functional GLS runner |
| `gls_tb_cvc.v` | CVC testbench (Verilog-2001 — CVC is not a SystemVerilog simulator) |
| `run_gls_cvc.sh` | timing-annotated runner (blocked on the CVC crash) |
| `gen_powerup_init.py` | → `gls_powerup_init.svh` (generated, gitignored) |
| `mk_cells_cvc.py` | → `gf180mcu_cells_cvc.v`, standards-compliant cell copy (generated, gitignored) |
| `mk_spiflash_v2001.py` | → `spiflash_v2001.v`, Verilog-2001 flash model |

Generated artifacts are reproducible from the scripts and are not committed; run
the generators after a re-harden, since the flop list comes from the netlist.

Note that `final/sdf/` is gitignored (185 MB across nine corners), so the CVC
flow needs a local signoff run present — not just the committed deliverable.
