"""FazyRV memory-timing contract.

`fazyrv_sci` presents the core with exactly ONE memory model: a combinational
(0-latency) Wishbone Classic slave, synthesised by freezing the core's clock
until the transfer it issued returns. There is no second contract to select
between, so MEMDLY1 must be 0.

MEMDLY1=1 builds a core that asserts stb/cyc for exactly one cycle and ignores
the ack for flow control::

    fazyrv_cntrl.sv:  if (MEMDLY1 | imem_ack_i) state_n = DECODE;

Against a slower slave the IR load pulse (`stb_ir_i = ack`) then lands
mid-execution and overwrites the decoded instruction registers, silently
corrupting results. Measured: a `lui a1,0x20040` produced 0x80100000 in the
address register, and MEMDLY1=1 broke a FazyRV worker that passes without it.
"""

import re

import pytest

from harness.core import REPO_ROOT
from util.xheep_gen.core_registry import validate_soc_config


def _cfg(**fazyrv):
    core = {"ip": "fazyrv", "count": 1, "role": "atlas", "isa": "rv32i",
            "boot_addr": 0x1000}
    core.update(fazyrv)
    return {
        "soc": {
            "name": "fz", "pdk": "gf180mcu", "target": "simulation",
            "cores": [
                {"ip": "cv32e20", "count": 1, "role": "titan", "isa": "rv32imc"},
                core,
            ],
            "memory": {"sram_kb": 16, "boot_rom_kb": 1},
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart"],
        }
    }


def test_baseline_fazyrv_config_is_valid():
    assert validate_soc_config(_cfg()) == []


@pytest.mark.parametrize("value", [True, 1])
def test_memdly1_is_rejected(value):
    errors = validate_soc_config(_cfg(memdly1=value))
    assert any("memdly1 is unsupported" in e for e in errors), errors


@pytest.mark.parametrize("value", [False, 0])
def test_memdly1_explicitly_zero_is_accepted(value):
    assert validate_soc_config(_cfg(memdly1=value)) == []


def test_wrapper_guards_memdly1_at_elaboration():
    """Defence in depth: the RTL must refuse it too.

    The schema gate is the fast path, but a hand-written instantiation of
    fazyrv_sci bypasses it entirely.
    """
    src = (REPO_ROOT / "hw/sci/fazyrv_sci.sv").read_text()
    assert "MEMDLY1 != 1'b0" in src, "wrapper lost its MEMDLY1 elaboration guard"
    assert "$error" in src


def test_wrapper_records_the_mutual_exclusion_invariant():
    """The simple `ack = rvalid` is only correct because the two ports are
    never outstanding together (IFETCH vs ACK are exclusive FSM states).

    A sticky per-port response latch looks like a hardening but makes FazyRV
    count a phantom second Wishbone transfer, and regressed the all-hart
    liveness gate when tried. Keep the reasoning in the file.
    """
    src = (REPO_ROOT / "hw/sci/fazyrv_sci.sv").read_text()
    assert re.search(r"never outstanding at the same time", src), (
        "wrapper lost the invariant that justifies ack = rvalid"
    )
