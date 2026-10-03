"""Stage-1 loopback test for the MOSAIC OBI<->AXI bridges.

cocotb OBI master -> xheep_obi_to_axi -> AXI -> xheep_axi_to_obi -> OBI memory.
Checks writes, reads, byte enables, back-to-back traffic, read-after-write.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles


async def obi_req(dut, addr, we=False, wdata=0, be=0xF, timeout=200):
    """One OBI transaction through the discrete master pins."""
    dut.req_i.value = 1
    dut.we_i.value = 1 if we else 0
    dut.be_i.value = be
    dut.addr_i.value = addr
    dut.wdata_i.value = wdata
    for _ in range(timeout):
        await RisingEdge(dut.clk_i)
        if dut.gnt_o.value:
            break
    else:
        raise AssertionError(f"no gnt for addr 0x{addr:08x}")
    dut.req_i.value = 0
    for _ in range(timeout):
        await RisingEdge(dut.clk_i)
        if dut.rvalid_o.value:
            return int(dut.rdata_o.value)
    raise AssertionError(f"no rvalid for addr 0x{addr:08x}")


@cocotb.test()
async def bridge_loopback(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.req_i.value = 0
    dut.rst_ni.value = 0
    await ClockCycles(dut.clk_i, 5)
    dut.rst_ni.value = 1
    await ClockCycles(dut.clk_i, 5)

    # 1. Write / read-after-write
    await obi_req(dut, 0x0000_0040, we=True, wdata=0xDEAD_BEEF)
    rd = await obi_req(dut, 0x0000_0040)
    assert rd == 0xDEAD_BEEF, f"RAW: got 0x{rd:08x}"

    # 2. Byte enables: overwrite only byte 1
    await obi_req(dut, 0x0000_0040, we=True, wdata=0x0000_5500, be=0b0010)
    rd = await obi_req(dut, 0x0000_0040)
    assert rd == 0xDEAD_55EF, f"BE: got 0x{rd:08x}"

    # 3. Back-to-back writes then reads over a small region
    for i in range(16):
        await obi_req(dut, 0x100 + 4 * i, we=True, wdata=0xA000_0000 + i)
    for i in range(16):
        rd = await obi_req(dut, 0x100 + 4 * i)
        assert rd == 0xA000_0000 + i, f"burst word {i}: got 0x{rd:08x}"

    # 4. Interleaved read/write pattern
    for i in range(8):
        await obi_req(dut, 0x200 + 4 * i, we=True, wdata=i)
        rd = await obi_req(dut, 0x200 + 4 * i)
        assert rd == i

    dut._log.info("bridge loopback PASS")
