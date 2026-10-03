"""Stage-2 smoke test: OBI traffic through the generated FlooNoC fabric.

cocotb OBI master -> xheep_obi_to_axi -> hart0 chimney -> router -> mem/periph
chimney -> xheep_axi_to_obi -> OBI slaves. The mem endpoint covers
[0, 0x8000); everything above routes to the periph endpoint.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

from test_bridges import obi_req


@cocotb.test()
async def noc_loopback(dut):
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.req_i.value = 0
    dut.rst_ni.value = 0
    await ClockCycles(dut.clk_i, 5)
    dut.rst_ni.value = 1
    await ClockCycles(dut.clk_i, 10)

    # 1. Write / read-after-write through the NoC into the mem endpoint
    await obi_req(dut, 0x0000_0040, we=True, wdata=0xDEAD_BEEF, timeout=500)
    rd = await obi_req(dut, 0x0000_0040, timeout=500)
    assert rd == 0xDEAD_BEEF, f"RAW through NoC: got 0x{rd:08x}"

    # 2. Byte enables through the NoC
    await obi_req(dut, 0x0000_0040, we=True, wdata=0x0000_5500, be=0b0010, timeout=500)
    rd = await obi_req(dut, 0x0000_0040, timeout=500)
    assert rd == 0xDEAD_55EF, f"BE through NoC: got 0x{rd:08x}"

    # 3. Back-to-back traffic
    for i in range(16):
        await obi_req(dut, 0x100 + 4 * i, we=True, wdata=0xB000_0000 + i, timeout=500)
    for i in range(16):
        rd = await obi_req(dut, 0x100 + 4 * i, timeout=500)
        assert rd == 0xB000_0000 + i, f"burst word {i}: got 0x{rd:08x}"

    # 4. Periph endpoint routing: address above MEM_SIZE (0x8000)
    rd = await obi_req(dut, 0x2000_0010, timeout=500)
    assert rd == (0xCAFE0000 ^ 0x2000_0010), f"periph read: got 0x{rd:08x}"

    # 5. Alternating mem/periph traffic (route switching on one manager)
    for i in range(4):
        await obi_req(dut, 0x200 + 4 * i, we=True, wdata=i, timeout=500)
        rd = await obi_req(dut, 0x3000_0000 + 4 * i, timeout=500)
        assert rd == (0xCAFE0000 ^ (0x3000_0000 + 4 * i))
        rd = await obi_req(dut, 0x200 + 4 * i, timeout=500)
        assert rd == i

    dut._log.info("NoC loopback PASS")
