"""The pad reset reaches the logic only through a synchronizer.

Block A's wrapper fed the pad's rst_ni straight into mosaic_soc. STA
passed only because the SDC times rst_ni as an input clocked by clk_i; a board
releases reset at any phase, and the GLS bench showed boot depends on that
phase. The synchronizer lives in the generator (mosaic_soc.sv.tpl), so
every generated SoC has it, with or without a hand-written wrapper.

The check renders the template in memory, the way mcu_gen does, and reads the
result: after the rstgen instance, no connection may take the raw rst_ni.
"""

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from harness.socview import _generator, _xheep_kwargs  # noqa: E402

TEMPLATE = REPO / "hw/mosaic_soc/mosaic_soc.sv.tpl"


def _render(config: Path) -> str:
    from mako.template import Template
    kwargs = _xheep_kwargs(config, REPO)
    with _generator(REPO):
        return str(Template(filename=str(TEMPLATE)).render_unicode(
            **kwargs, strict_undefined=True))


@pytest.mark.parametrize("config", [
    "configs/mosaic_tapeout_ultra.yaml",   # Block A: single-core-debug off, XIP
    "configs/mosaic_blockc_4hart.yaml",    # TDU, dma none
    "configs/mosaic_all_cores.yaml",       # debug on, PLIC, every core family
])
def test_only_the_synchronizer_reads_the_pad_reset(config):
    rtl = _render(REPO / config)
    body = rtl[rtl.index(");", rtl.index("module mosaic_soc")):]  # after the ports
    sync = re.search(r"rstgen\s+rstgen_i\s*\((.*?)\);", body, re.S)
    assert sync, "no reset synchronizer instance"
    assert re.search(r"\.rst_ni\s*[,)]", sync.group(1)), "rstgen does not take rst_ni"
    assert "rst_no(rst_n_sync)" in sync.group(1).replace(" ", "")
    rest = body[:sync.start()] + body[sync.end():]
    raw = re.findall(r"\.rst_ni\s*(?:,|\)|\(\s*rst_ni\b)|[=&|(]\s*rst_ni\b", rest)
    assert raw == [], f"raw pad reset still reaches logic: {raw}"
    assert rest.count("rst_n_sync") >= 3
