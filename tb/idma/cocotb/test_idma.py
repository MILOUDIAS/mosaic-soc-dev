"""Functional regressions for the multi-stream MOSAIC iDMA wrapper.

Every test runs against both the independent-port block memory and the shared,
arbitrated SoC memory. Coverage includes the legacy stream-0 ABI, atomic stream
ownership, 2D/3D expansion, concurrent streams, all configured OBI ports,
completion pulses, and the aggregate completion interrupt.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

STREAM_STRIDE = 0x200
CONF = 0x00
NEXT_ID_BASE = 0x44
DONE_ID_BASE = 0x84
DST_ADDR = 0xD0
SRC_ADDR = 0xD8
LENGTH = 0xE0
DST_STRIDE_2 = 0xE8
SRC_STRIDE_2 = 0xF0
REPS_2 = 0xF8
DST_STRIDE_3 = 0x100
SRC_STRIDE_3 = 0x108
REPS_3 = 0x110
OWNER_CLAIM = 0x180
OWNER_RELEASE = 0x184
OWNER = 0x188

CONF_OBI = (1 << 12) | (1 << 15)


def reg(stream, offset):
    return stream * STREAM_STRIDE + offset


async def reg_write(dut, addr, data):
    dut.reg_addr_i.value = addr
    dut.reg_wdata_i.value = data
    dut.reg_write_i.value = 1
    dut.reg_valid_i.value = 1
    for _ in range(64):
        await RisingEdge(dut.clk_i)
        if int(dut.reg_ready_o.value):
            break
    else:
        raise AssertionError(f"register write 0x{addr:x} timed out")
    dut.reg_valid_i.value = 0
    dut.reg_write_i.value = 0
    await RisingEdge(dut.clk_i)


async def reg_read(dut, addr):
    dut.reg_addr_i.value = addr
    dut.reg_write_i.value = 0
    dut.reg_valid_i.value = 1
    for _ in range(64):
        await RisingEdge(dut.clk_i)
        if int(dut.reg_ready_o.value):
            value = int(dut.reg_rdata_o.value)
            break
    else:
        raise AssertionError(f"register read 0x{addr:x} timed out")
    dut.reg_valid_i.value = 0
    await RisingEdge(dut.clk_i)
    return value


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.reg_valid_i.value = 0
    dut.reg_write_i.value = 0
    dut.reg_addr_i.value = 0
    dut.reg_wdata_i.value = 0
    dut.rst_ni.value = 0
    await ClockCycles(dut.clk_i, 10)
    dut.rst_ni.value = 1
    await ClockCycles(dut.clk_i, 5)


def mem_write(dut, addr, value):
    dut.i_mem.mem[addr >> 2].value = value


def mem_read(dut, addr):
    return int(dut.i_mem.mem[addr >> 2].value)


async def claim(dut, stream, token):
    await reg_write(dut, reg(stream, OWNER_CLAIM), token)
    return (await reg_read(dut, reg(stream, OWNER))) == token


async def program(dut, stream, src, dst, length, dimensions=1,
                  reps_2=0, src_stride_2=0, dst_stride_2=0,
                  reps_3=1, src_stride_3=0, dst_stride_3=0):
    conf = CONF_OBI | ({1: 0, 2: 1, 3: 2}[dimensions] << 10)
    await reg_write(dut, reg(stream, CONF), conf)
    await reg_write(dut, reg(stream, DST_ADDR), dst)
    await reg_write(dut, reg(stream, SRC_ADDR), src)
    await reg_write(dut, reg(stream, LENGTH), length)
    await reg_write(dut, reg(stream, DST_STRIDE_2), dst_stride_2)
    await reg_write(dut, reg(stream, SRC_STRIDE_2), src_stride_2)
    await reg_write(dut, reg(stream, REPS_2), reps_2)
    await reg_write(dut, reg(stream, DST_STRIDE_3), dst_stride_3)
    await reg_write(dut, reg(stream, SRC_STRIDE_3), src_stride_3)
    await reg_write(dut, reg(stream, REPS_3), reps_3)
    return await reg_read(dut, reg(stream, NEXT_ID_BASE + 4 * stream))


async def wait_streams(dut, mask, timeout=6000):
    irq_seen = False
    for _ in range(timeout):
        await RisingEdge(dut.clk_i)
        irq_seen |= bool(int(dut.dma_done_intr_o.value))
        if int(dut.stream_done_o.value) & mask == mask:
            return irq_seen
    raise AssertionError(
        f"iDMA completion timeout: got 0x{int(dut.stream_done_o.value):x}, expected 0x{mask:x}"
    )


@cocotb.test()
async def legacy_stream_zero_copy_and_interrupt(dut):
    await reset(dut)
    src, dst = 0x100, 0x800
    pattern = [0x11111111, 0x22222222, 0x33333333, 0x44444444]
    for i, word in enumerate(pattern):
        mem_write(dut, src + 4 * i, word)
        mem_write(dut, dst + 4 * i, 0)

    assert await claim(dut, 0, 0x101)
    await program(dut, 0, src, dst, 4 * len(pattern))
    assert await wait_streams(dut, 0x1), "aggregate completion IRQ was not observed"
    assert [mem_read(dut, dst + 4 * i) for i in range(4)] == pattern
    assert int(dut.rd_seen_o.value) & 0x1
    assert int(dut.wr_seen_o.value) & 0x1


@cocotb.test()
async def atomic_stream_ownership(dut):
    await reset(dut)
    token_a, token_b = 0xA11CE, 0xB0B
    assert await claim(dut, 0, token_a)
    assert not await claim(dut, 0, token_b), "second hart stole an owned stream"
    await reg_write(dut, reg(0, OWNER_RELEASE), token_b)
    assert await reg_read(dut, reg(0, OWNER)) == token_a
    await reg_write(dut, reg(0, OWNER_RELEASE), token_a)
    assert await reg_read(dut, reg(0, OWNER)) == 0


@cocotb.test()
async def two_dimensional_copy(dut):
    await reset(dut)
    src, dst = 0x200, 0x900
    rows = [
        [0xA0000001, 0xA0000002],
        [0xB0000001, 0xB0000002],
        [0xC0000001, 0xC0000002],
    ]
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            mem_write(dut, src + row * 0x10 + col * 4, value)
            mem_write(dut, dst + row * 0x20 + col * 4, 0)

    assert await claim(dut, 0, 0x202)
    await program(dut, 0, src, dst, 8, dimensions=2, reps_2=3,
                  src_stride_2=0x10, dst_stride_2=0x20)
    await wait_streams(dut, 0x1)
    for row, values in enumerate(rows):
        assert [mem_read(dut, dst + row * 0x20 + col * 4)
                for col in range(2)] == values


@cocotb.test()
async def three_dimensional_copy(dut):
    await reset(dut)
    src, dst = 0x300, 0xA00
    pattern = [0xD0000001, 0xD0000002, 0xD0000003, 0xD0000004]
    for i, value in enumerate(pattern):
        mem_write(dut, src + i * 4, value)
        mem_write(dut, dst + i * 4, 0)

    assert await claim(dut, 0, 0x303)
    await program(dut, 0, src, dst, 4, dimensions=3,
                  reps_2=2, src_stride_2=4, dst_stride_2=4,
                  reps_3=2, src_stride_3=4, dst_stride_3=4)
    await wait_streams(dut, 0x1)
    assert [mem_read(dut, dst + i * 4) for i in range(4)] == pattern


@cocotb.test()
async def concurrent_streams_use_all_master_ports(dut):
    await reset(dut)
    src0, dst0 = 0x400, 0xB00
    src1, dst1 = 0x600, 0xD00
    pattern0 = [0x10000000 + i for i in range(16)]
    pattern1 = [0x20000000 + i for i in range(16)]
    for i in range(16):
        mem_write(dut, src0 + 4 * i, pattern0[i])
        mem_write(dut, src1 + 4 * i, pattern1[i])
        mem_write(dut, dst0 + 4 * i, 0)
        mem_write(dut, dst1 + 4 * i, 0)

    assert await claim(dut, 0, 0x400)
    assert await claim(dut, 1, 0x600)
    await program(dut, 0, src0, dst0, 64)
    await program(dut, 1, src1, dst1, 64)
    assert await wait_streams(dut, 0x3)

    assert [mem_read(dut, dst0 + 4 * i) for i in range(16)] == pattern0
    assert [mem_read(dut, dst1 + 4 * i) for i in range(16)] == pattern1
    assert int(dut.rd_seen_o.value) & 0x3 == 0x3, "not all read ports were active"
    assert int(dut.wr_seen_o.value) & 0x3 == 0x3, "not all write ports were active"
