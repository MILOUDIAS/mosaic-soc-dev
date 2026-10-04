# TDU test at its SoC address

The register-level testbench in `hw/tdu/tb/tdu_tb.sv` drives the Task Dispatch
Unit (TDU) with bare register offsets. This test checks the TDU where it sits in
the SoC: behind the register-bus tap of the always-on peripheral subsystem, at
`TDU_START_ADDRESS = 0x200A0000`. It reproduces that tap, injects the per-hart
sleep inputs, and observes the wake outputs and the interrupt.

## Running it

```bash
tb/tdu/soc/cocotb/run.sh        # the tap as built in the SoC
tb/tdu/soc/cocotb/run.sh bug    # also runs the tap without address subtraction, which must fail
```

It needs cocotb and Verilator only.

## What it checks

Through SoC addresses:

- the TDU is reachable: `SCHED_MODE` reads back what was written;
- the eight-deep task queue works: three `TASK_PUSH` writes, `TASK_STATUS`
  reports three, `TASK_POP` returns them in order;
- a `WAKE_REQ` write produces a wake pulse for the targeted hart;
- `CORE_STATUS` reflects the injected sleep inputs.

## Why the tap subtracts the base address

The TDU decodes register offsets (`0x00`, `0x04`, ...), not full addresses. The
tap in `ao_peripheral_subsystem.sv.tpl` therefore subtracts the window base
before it forwards a request:

```
tdu_req.addr = perconv2regdemux_req.addr - TDU_START_ADDRESS
```

Without the subtraction no register matches and every access returns zero. The
`bug` argument runs that variant to show the test detects it:

| Tap | `SCHED_MODE` readback | Task count | Wake | Result |
|---|---|---|---|---|
| full address | `0x0` | 0 | none | fail |
| base subtracted | `0x1` | 3 | bit 2 set | pass |

## Related tests

The path from a wake pulse to a core starting is tested in `tb/mosaic/cocotb`
and in the full-SoC benches under `tb/mosaic_soc/`.

## Files

| File | Purpose |
|---|---|
| `cocotb/test_tdu_soc.py` | the test |
| `cocotb/Makefile` | cocotb and Verilator build; `SUB` selects the tap variant |
| `cocotb/run.sh` | runs the test |
| `tdu_soc_tb_top.sv` | the tap, the TDU and the sleep and wake ports |
