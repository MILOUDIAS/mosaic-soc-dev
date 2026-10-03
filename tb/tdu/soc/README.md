# TDU SoC-level test (cocotb + Verilator)

Verifies the **Task Dispatch Unit at its SoC integration point**, not just in
isolation. The block-level TB (`hw/tdu/tb/tdu_tb.sv`) drives the TDU with bare
register offsets (`0x00..`); this test reproduces the **ao-peripheral reg-bus tap**
from `ao_peripheral_subsystem.sv.tpl` and accesses the TDU through its real SoC
address (`TDU_START_ADDRESS = 0x200A0000`), injecting per-hart `core_sleep` and
observing `core_wake`/`tdu_irq`.

## Run

```bash
tb/tdu/soc/cocotb/run.sh           # the (fixed) flow — PASS
tb/tdu/soc/cocotb/run.sh bug       # also runs the original buggy tap (FAIL)
```

Needs cocotb + Verilator only.

## What it checks (all PASS with the fix)

Programs the TDU through the SoC bus and verifies:
- it is **reachable at its SoC address** (`SCHED_MODE` read/write round-trips);
- the **8-deep task FIFO** works (`TASK_PUSH` ×3 → `TASK_STATUS` count = 3 → `TASK_POP` returns them in order);
- **`WAKE_REQ` produces a `core_wake` pulse** for the targeted hart;
- **`CORE_STATUS` reflects** the injected `core_sleep` inputs.

## Bug this test caught (now fixed)

**The TDU was unreachable through the SoC bus.** The tap in
`ao_peripheral_subsystem.sv.tpl` passed the **full** SoC address
(`0x200A0000 + offset`) to the TDU, but the TDU decodes by bare **offset**
(`case(req_addr) TDU_*_OFFSET=0x00..`). So no register ever matched — every TDU
access (task dispatch, wake, CPI, mode) silently returned 0.

The test demonstrates it directly:

| Tap (`SUB`) | `SCHED_MODE` readback | task count | wake | result |
|-------------|-----------------------|-----------|------|--------|
| `0` full address (original) | `0x0` | 0 | none | **FAIL** |
| `1` subtract base (fix) | `0x1` | 3 | bit 2 set | **PASS** |

**Fix:** the tap now subtracts the window base
(`tdu_req.addr = perconv2regdemux_req.addr - TDU_START_ADDRESS`) so the TDU sees
its register offsets — keeping the TDU position-independent and consistent with
the block TB.

## Wake→core loop (now closed in hardware)

This test originally surfaced that `core_wake_o` was **not wired back into the
cores**. That is now fixed:

- `cpu_subsystem` has a per-hart `core_wake_i [NUM_HARTS]` input, and
  `core_v_mini_mcu.sv` wires it from `TDU.core_wake_o` (`.core_wake_i(core_wake)`).
- Each worker core (role ≠ `titan`) boots **dormant**: a per-hart run-enable latch
  starts at 0 and is set by a `core_wake_i` pulse, gating the core's
  `fetch_enable`. TITAN boots immediately out of reset (`fetch_enable = 1`).
- The serial-core SCI wrappers (`serv_sci`, `fazyrv_sci`) — which have no native
  fetch-enable — emulate dormancy by holding the core in reset and masking their
  OBI request strobes until woken, and report `core_sleep_o = ~fetch_enable` so
  the TDU's `CORE_STATUS` reflects which workers are still parked.

The closed loop is verified end-to-end by the multi-core harness
**`tb/mosaic/cocotb`** (`test_mosaic.py`): workers stay parked with no wake,
a per-hart wake pulse releases exactly the targeted core, and all workers run
once woken. What remains is the *policy* layer — the FreeRTOS firmware on TITAN
that decides when to program `WAKE_REQ`/`TASK_PUSH` — which is future firmware
work; the hardware mechanism and wiring are now complete and tested.

## Files

```
cocotb/test_tdu_soc.py   cocotb test (drive TDU at its SoC address, check behaviour)
cocotb/Makefile          cocotb+Verilator build (SUB selects the tap mode)
cocotb/run.sh            runs the fixed flow (add `bug` to also run the broken tap)
tdu_soc_tb_top.sv        reproduces the ao-peripheral tap + TDU + sleep/wake ports
```
