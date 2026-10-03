"""SoC-level cocotb test for the Task Dispatch Unit (TDU).

Accesses the TDU through its real SoC address (TDU_START_ADDRESS = 0x200A0000)
via the reproduced ao-peripheral reg-bus tap, and checks the behaviours that
matter at the SoC integration level:
  * the TDU is reachable + its registers read/write at the SoC address,
  * the 8-deep task FIFO (push / status count / pop) works,
  * WAKE_REQ generates a core_wake pulse for the targeted hart,
  * CORE_STATUS reflects the injected per-hart core_sleep inputs.

Register offsets (tdu_pkg): CORE_STATUS=0x00, SCHED_MODE=0x04, WAKE_MASK=0x08,
WAKE_REQ=0x0C, TASK_PUSH=0x10, TASK_POP=0x14, TASK_STATUS=0x18,
    ACTIVE_HART_CYCLES=0x1C, CPI_EST_BASE=0x20, PARK_REQ=0x60.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

TDU_BASE = 0x200A0000  # AO_PERIPHERAL_START (0x20000000) + 0xA0000
CORE_STATUS = TDU_BASE + 0x00
SCHED_MODE = TDU_BASE + 0x04
WAKE_MASK = TDU_BASE + 0x08
WAKE_REQ = TDU_BASE + 0x0C
TASK_PUSH = TDU_BASE + 0x10
TASK_POP = TDU_BASE + 0x14
TASK_STATUS = TDU_BASE + 0x18
PARK_REQ = TDU_BASE + 0x60

SCHED_DYNAMIC = 1


def task_desc(task_id, core_hint=0, prio=0):
    return ((task_id & 0xFFFF) << 16) | ((core_hint & 0x1F) << 11) | ((prio & 0x7) << 8)


async def reg_write(dut, addr, data):
    dut.reg_addr_i.value = addr
    dut.reg_wdata_i.value = data
    dut.reg_write_i.value = 1
    dut.reg_valid_i.value = 1
    await RisingEdge(dut.clk_i)
    for _ in range(64):
        if int(dut.reg_ready_o.value) == 1:
            break
        await RisingEdge(dut.clk_i)
    dut.reg_valid_i.value = 0
    dut.reg_write_i.value = 0
    await RisingEdge(dut.clk_i)


async def reg_read(dut, addr):
    dut.reg_addr_i.value = addr
    dut.reg_write_i.value = 0
    dut.reg_valid_i.value = 1
    await RisingEdge(dut.clk_i)
    val = 0
    for _ in range(64):
        if int(dut.reg_ready_o.value) == 1:
            val = int(dut.reg_rdata_o.value)
            break
        await RisingEdge(dut.clk_i)
    dut.reg_valid_i.value = 0
    await RisingEdge(dut.clk_i)
    return val


@cocotb.test()
async def tdu_soc_level(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, units="ns").start())
    dut.reg_valid_i.value = 0
    dut.reg_write_i.value = 0
    dut.reg_addr_i.value = 0
    dut.reg_wdata_i.value = 0
    dut.core_sleep_i.value = 0
    dut.rst_ni.value = 0
    await ClockCycles(dut.clk_i, 10)
    dut.rst_ni.value = 1
    await ClockCycles(dut.clk_i, 5)

    fails = 0

    # 1) Reachable at the SoC address: SCHED_MODE is RW.
    await reg_write(dut, SCHED_MODE, SCHED_DYNAMIC)
    m = await reg_read(dut, SCHED_MODE)
    dut._log.info(f"SCHED_MODE readback = 0x{m:08x} (expected 0x{SCHED_DYNAMIC:08x})")
    if m != SCHED_DYNAMIC:
        fails += 1
        dut._log.error("TDU not reachable at its SoC address (SCHED_MODE mismatch)")

    # 2) Task FIFO: push 3, check count, pop them back in order.
    tasks = [task_desc(0xA1, 1, 0), task_desc(0xB2, 2, 1), task_desc(0xC3, 3, 2)]
    for t in tasks:
        await reg_write(dut, TASK_PUSH, t)
    st = await reg_read(dut, TASK_STATUS)
    cnt = st & 0xFF
    dut._log.info(f"TASK_STATUS count = {cnt} (expected {len(tasks)})")
    if cnt != len(tasks):
        fails += 1
        dut._log.error("task FIFO count wrong")
    for i, t in enumerate(tasks):
        got = await reg_read(dut, TASK_POP)
        dut._log.info(f"POP[{i}] = 0x{got:08x} (expected 0x{t:08x})")
        if got != t:
            fails += 1

    # 3) WAKE_REQ → core_wake pulse for the targeted hart.
    await reg_write(dut, WAKE_MASK, 0x7E)  # enable harts 1..6
    seen_wake = 0
    dut.reg_addr_i.value = WAKE_REQ
    dut.reg_wdata_i.value = 1 << 2  # wake hart 2
    dut.reg_write_i.value = 1
    dut.reg_valid_i.value = 1
    for _ in range(6):
        await RisingEdge(dut.clk_i)
        seen_wake |= int(dut.core_wake_o.value)
        if int(dut.reg_ready_o.value) == 1:
            dut.reg_valid_i.value = 0
            dut.reg_write_i.value = 0
    dut.reg_valid_i.value = 0
    dut.reg_write_i.value = 0
    dut._log.info(f"core_wake_o observed bits = 0x{seen_wake:02x} (expected bit 2 set)")
    if not (seen_wake & (1 << 2)):
        fails += 1
        dut._log.error("no wake pulse on hart 2")

    # 4) PARK_REQ -> targeted park pulse for a completed worker.
    seen_park = 0
    dut.reg_addr_i.value = PARK_REQ
    dut.reg_wdata_i.value = 1 << 3
    dut.reg_write_i.value = 1
    dut.reg_valid_i.value = 1
    for _ in range(6):
        await RisingEdge(dut.clk_i)
        seen_park |= int(dut.core_park_o.value)
        if int(dut.reg_ready_o.value) == 1:
            dut.reg_valid_i.value = 0
            dut.reg_write_i.value = 0
    dut.reg_valid_i.value = 0
    dut.reg_write_i.value = 0
    if seen_park != (1 << 3):
        fails += 1
        dut._log.error(f"wrong park pulse: got 0x{seen_park:x}, expected hart 3")

    # 5) CORE_STATUS reflects injected core_sleep.
    dut.core_sleep_i.value = 0b0001010  # harts 1 and 3 asleep
    await ClockCycles(dut.clk_i, 3)
    cs = await reg_read(dut, CORE_STATUS)
    dut._log.info(f"CORE_STATUS = 0x{cs:08x} (injected sleep = 0x0a)")
    # Integration top mirrors running=~sleep, so low 16 bits are 0x75.
    if cs != 0x000A0075:
        fails += 1
        dut._log.error(
            f"CORE_STATUS ABI mismatch: got 0x{cs:08x}, expected 0x000a0075"
        )

    dut._log.info(f"=== TDU SoC-level test: {fails} failure(s) ===")
    assert fails == 0, f"{fails} SoC-level TDU check(s) failed"
    dut._log.info("TDU SoC-level test: PASS")
