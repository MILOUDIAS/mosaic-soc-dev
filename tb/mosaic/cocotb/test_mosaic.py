"""cocotb test for the MOSAIC multi-core cpu_subsystem + TDU wake loop.

Drives the generated multi-core SoC (serv + qerv + fazyrv via their SCI wrappers)
through cocotb_top.sv, exercising the TDU.core_wake_o → cpu_subsystem
fetch-enable path end-to-end.

HART 0 IS NOT DORMANT HERE, AND THAT IS DELIBERATE.
`configs/mosaic_sim.yaml` declares `profile: testbench` and has no TITAN. A
worker-only topology has no running hart able to issue the first TDU dispatch,
so cpu_subsystem.sv.tpl's `testbench_hart0_bootstrap` ties hart 0's
fetch_enable high (production `soc` profiles still require a TITAN). This test
predated that rule and asserted all three stayed parked, so it failed on
hart 0 by design rather than by defect.

Per-hart gating is still fully proved by the two harts that ARE dormant:

  1. Reset, run without waking → hart 0 boots (bootstrap); harts 1 and 2 must
     stay dormant (core_sleep_o asserted, sentinel never written).
  2. Wake hart 1 ONLY → it comes alive and retires its program while hart 2
     stays parked. This is the step that proves wake is per-hart, not global.
  3. Wake hart 2 → all three have retired (write 0x55 to 0x40).

Run via tb/mosaic/cocotb/run.sh (generates the RTL first), or:
    make -C tb/mosaic/cocotb SIM=verilator
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

SENTINEL = 0x55
# (label, sentinel signal, alive signal, sleep signal, wake bit)
CORES = [
    ("serv  (W=1)", "sentinel0", "alive0", "sleep0", 0),
    ("qerv  (W=4)", "sentinel1", "alive1", "sleep1", 1),
    ("fazyrv     ", "sentinel2", "alive2", "sleep2", 2),
]
# Hart 0 is released at reset by the testbench bootstrap; 1 and 2 are the
# genuinely dormant workers this test gates on.
BOOTSTRAPPED = CORES[0]
DORMANT = CORES[1:]


def _sent(dut, sig):
    return int(getattr(dut, sig).value)


async def _run_until_executed(dut, bits, max_chunks=30, chunk=2000):
    """Run, polling until every hart whose wake bit is set has written 0x55."""
    targets = [c for c in CORES if (bits >> c[4]) & 1]
    for _ in range(max_chunks):
        await ClockCycles(dut.clk_i, chunk)
        if all(_sent(dut, c[1]) == SENTINEL for c in targets):
            break


@cocotb.test()
async def multicore_wake_loop(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, units="ns").start())

    # ── Reset, wake held low: every worker must stay parked ────────────────
    dut.core_wake.value = 0b000
    dut.rst_ni.value = 0
    await ClockCycles(dut.clk_i, 20)
    dut.rst_ni.value = 1

    # Give them ample time to misbehave if gating were broken.
    await ClockCycles(dut.clk_i, 4000)

    dut._log.info("=== Phase 1: dormant out of reset (no wake) ===")
    failures = 0

    # Hart 0 is expected to be RUNNING: no TITAN in this config + the testbench
    # profile releases it so something can drive the first dispatch.
    name, sent_sig, alive_sig, sleep_sig, _ = BOOTSTRAPPED
    alive = int(getattr(dut, alive_sig).value)
    slp = int(getattr(dut, sleep_sig).value)
    sval = _sent(dut, sent_sig)
    boot_ok = alive == 1 and slp == 0
    dut._log.info(
        f"hart {name}: alive={alive} sleep={slp} sentinel=0x{sval:08x} "
        f"{'PASS (testbench bootstrap)' if boot_ok else 'FAIL (bootstrap hart did not run!)'}"
    )
    if not boot_ok:
        failures += 1

    for name, sent_sig, alive_sig, sleep_sig, _ in DORMANT:
        alive = int(getattr(dut, alive_sig).value)
        slp = int(getattr(dut, sleep_sig).value)
        sval = _sent(dut, sent_sig)
        ok = alive == 0 and slp == 1 and sval != SENTINEL
        dut._log.info(
            f"hart {name}: alive={alive} sleep={slp} sentinel=0x{sval:08x} "
            f"{'PASS (parked)' if ok else 'FAIL (ran without wake!)'}"
        )
        if not ok:
            failures += 1
    assert failures == 0, "a worker executed before being woken — gating is broken"

    # ── Phase 2: wake hart 1 only ──────────────────────────────────────────
    # The selective-wake proof lives here, on a hart that really was dormant.
    dut._log.info("=== Phase 2: wake hart 1 only (selective) ===")
    # A one-cycle pulse is enough — cpu_subsystem latches it.
    dut.core_wake.value = 0b010
    await ClockCycles(dut.clk_i, 2)
    dut.core_wake.value = 0b000
    await _run_until_executed(dut, 0b010)

    h1 = CORES[1]
    assert int(getattr(dut, h1[2]).value) == 1, "hart 1 did not wake"
    assert _sent(dut, h1[1]) == SENTINEL, "hart 1 woke but did not execute"
    assert int(getattr(dut, h1[3]).value) == 0, "hart 1 still reports sleep"
    # Hart 2 must still be parked — one wake bit must not release its neighbour.
    name, sent_sig, alive_sig, sleep_sig, _ = CORES[2]
    assert int(getattr(dut, alive_sig).value) == 0, f"{name} woke unexpectedly"
    assert _sent(dut, sent_sig) != SENTINEL, f"{name} executed unexpectedly"
    assert int(getattr(dut, sleep_sig).value) == 1, f"{name} not parked"
    dut._log.info("hart 1 ran; hart 2 still parked — per-hart wake confirmed")

    # ── Phase 3: wake the rest ─────────────────────────────────────────────
    dut._log.info("=== Phase 3: wake remaining harts ===")
    dut.core_wake.value = 0b100
    await ClockCycles(dut.clk_i, 2)
    dut.core_wake.value = 0b000
    await _run_until_executed(dut, 0b100)

    dut._log.info("=== Final state ===")
    failures = 0
    for name, sent_sig, alive_sig, sleep_sig, _ in CORES:
        alive = int(getattr(dut, alive_sig).value)
        slp = int(getattr(dut, sleep_sig).value)
        sval = _sent(dut, sent_sig)
        ok = alive == 1 and slp == 0 and sval == SENTINEL
        dut._log.info(
            f"hart {name}: alive={alive} sleep={slp} sentinel=0x{sval:08x} "
            f"{'PASS' if ok else 'FAIL'}"
        )
        if not ok:
            failures += 1

    assert failures == 0, f"{failures} core(s) failed after wake (see log above)"
    dut._log.info("ALL WORKERS: dormant→woken→executed — TDU wake loop closed")
