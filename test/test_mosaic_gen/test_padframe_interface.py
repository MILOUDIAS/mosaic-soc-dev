"""The Block A wrapper must present exactly the pins the padframe DEF declares.

`Odb.ApplyDEFTemplate` matches pin sets in strict mode, so a single missing or
extra port fails the flow, and it fails after synthesis rather than before it.
These tests move that failure to the fastest place we can put it.

They also pin the pad policy itself, because the policy has already drifted once:
the settings table said `rst_ni` carries a pull-down while the generated wrapper
tied it to 0, since each generator held its own copy. Both now read
`padframe/pad_policy.py`, and the tests below check the outputs agree rather than
trusting that they share an import.
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys

import pytest
import yaml

from harness.core import REPO_ROOT

PADFRAME = REPO_ROOT / "flow/librelane/experimental/padframe"
DEFS = REPO_ROOT / "flow/librelane/integration/D15/project_defs/A"
WRAPPER = REPO_ROOT / "flow/librelane/experimental/mosaic_block_a.sv"

# This file tests the external padframe collateral: the padframe DEF, the
# pad policy that generates the settings, and the 167-terminal delivery wrapper
# built against them. Branches carrying only the generator and the harness do
# not have it, and there a missing integration input is not a broken generator.
#
# The import has to be guarded too, not just the tests. `pytestmark` runs at
# module scope but AFTER the imports above it, so a bare `import pad_policy`
# fails collection before any skip is consulted, and the whole file reports as
# an error rather than a skip.
#
# All three paths are checked. An earlier version tested only the DEF and the
# wrapper, and both happened to be present on a branch where pad_policy was
# not: the DEF as an untracked leftover, the wrapper as an older revision.
_COLLATERAL = {
    "pad_policy.py": (PADFRAME / "pad_policy.py").is_file(),
    "D15_A.def": (DEFS / "D15_A.def").is_file(),
    "mosaic_block_a.sv": WRAPPER.is_file(),
}
_MISSING = sorted(name for name, present in _COLLATERAL.items() if not present)

if not _MISSING:
    sys.path.insert(0, str(PADFRAME))
    import pad_policy
else:
    pad_policy = None

pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"needs the external padframe collateral, missing: {', '.join(_MISSING)}",
)


def def_pins() -> set[str]:
    text = (DEFS / "D15_A.def").read_text()
    return set(re.findall(r"^- (\S+) \+ NET", text, re.M))


def wrapper_ports() -> set[str]:
    sv = WRAPPER.read_text()
    header = sv[sv.index(") (\n"): sv.index("\n);")]
    ports: set[str] = set()
    for m in re.finditer(
        r"^\s+(?:input|output|inout)\s+(?:logic|wire )\s*(?:\[(\d+):(\d+)\])?\s*(\w+)\s*,?\s*$",
        header, re.M,
    ):
        hi, lo, name = m.groups()
        if hi is None:
            ports.add(name)
        else:
            ports |= {f"{name}[{i}]" for i in range(int(lo), int(hi) + 1)}
    return ports


def test_the_wrapper_declares_exactly_the_def_pin_set():
    """Strict mode requires identical sets, so neither difference is tolerable."""
    ports, pins = wrapper_ports(), def_pins()
    assert ports - pins == set(), {"in the wrapper, not the DEF": sorted(ports - pins)}
    assert pins - ports == set(), {"in the DEF, not the wrapper": sorted(pins - ports)}
    assert len(ports) == 167


def test_no_signal_is_driven_twice():
    """A generated tie plus a hand-written assign is the obvious way to break this."""
    sv = WRAPPER.read_text()
    drivers = collections.Counter(
        m.group(1) for m in re.finditer(r"^\s*assign\s+(\w+)", sv, re.M)
    )
    assert not {k: v for k, v in drivers.items() if v > 1}


def test_the_qspi_pads_never_sit_in_the_disallowed_state():
    """IE=1 with OE=1 is marked Disallowed in the PDK control table.

    An earlier draft tied IE high, which would have put all four QSPI pads there
    whenever they drove. IE must follow ~OE.
    """
    sv = WRAPPER.read_text()
    assert "assign spi_flash_sd_io_IE  = ~flash_sd_oe;" in sv
    assert "assign spi_flash_sd_io_OE  = flash_sd_oe;" in sv
    # and it must not be tied off anywhere
    assert not re.search(r"assign\s+spi_flash_sd_io_IE\s*=\s*\{?\d*\{?1'b1", sv)


def test_rst_ni_carries_a_pull_down_in_both_generated_outputs():
    """The drift that actually happened: table said pull-down, netlist said 0."""
    table = (PADFRAME / "pad_settings.md").read_text()
    svh = (PADFRAME / "mosaic_block_a_ports.svh").read_text()
    sv = WRAPPER.read_text()

    row = [l for l in table.splitlines() if l.startswith("| `rst_ni` | input | `PD`")]
    assert row and row[0].rstrip().endswith("| 1 |"), row

    assert "assign rst_ni_PD = 1'b1;" in svh
    assert "assign rst_ni_PD = 1'b1;" in sv
    assert "assign rst_ni_PU = 1'b0;" in sv


def test_the_policy_is_the_only_place_the_settings_live():
    """Neither generator may carry its own copy of the values."""
    for name in ("gen_pad_settings.py", "gen_wrapper_ports.py"):
        text = (PADFRAME / name).read_text()
        assert "from pad_policy import" in text, name
        assert "PDRV0" not in text.split('"""', 2)[-1], (
            f"{name} appears to redeclare a pad setting; it must come from pad_policy"
        )


def test_the_tristate_buffers_are_gone():
    """bi_t does the job now; internal bufz_4 cells would fight the pad."""
    sv = WRAPPER.read_text()
    instantiations = re.findall(r"^\s*gf180mcu_fd_sc_mcu7t5v0__bufz_4\s+\w+\s*\(", sv, re.M)
    assert not instantiations


@pytest.mark.parametrize("user,terminal,expected", [
    ("rst_ni", "PD", "1"),
    ("rst_ni", "PU", "0"),
    ("clk_i", "PD", "0"),
    ("uart_tx_o", "OE", "1"),
    ("uart_tx_o", "IE", "0"),
    ("spi_flash_sd_io[0]", "OE", "oe"),
    ("spi_flash_sd_io[0]", "IE", "~oe"),
    # 12 mA, chosen 2026-08-25. The encoding is PDRV1/PDRV0 = 00/01/10/11 for
    # 4/8/12/16 mA, so 12 mA is PDRV1=1 with PDRV0=0 and it is easy to write
    # backwards. An earlier revision proposed 8 mA (0/1); driving ~20 pF through
    # a 10 ns edge at 20 MHz needs about 10 mA, which 8 does not cover.
    ("uart_tx_o", "PDRV1", "1"),
    ("uart_tx_o", "PDRV0", "0"),
    ("spi_flash_sd_io[0]", "PDRV1", "1"),
    ("spi_flash_sd_io[0]", "PDRV0", "0"),
])
def test_pad_policy_values(user, terminal, expected):
    kind = "input" if user in ("rst_ni", "clk_i") else (
        "qspi" if user.startswith("spi_flash_sd_io") else "output")
    assert pad_policy.value(user, kind, terminal) == expected


def test_every_bi_t_pad_drives_at_12_ma():
    """One pad left at 8 mA would be invisible until the board misbehaved."""
    sv = WRAPPER.read_text()
    pdrv1 = re.findall(r"assign (\w+)_PDRV1 = (?:\{\d+\{)?1'b(\d)", sv)
    pdrv0 = re.findall(r"assign (\w+)_PDRV0 = (?:\{\d+\{)?1'b(\d)", sv)
    assert len(pdrv1) == len(pdrv0) == 6, (pdrv1, pdrv0)
    assert all(v == "1" for _, v in pdrv1), pdrv1
    assert all(v == "0" for _, v in pdrv0), pdrv0
