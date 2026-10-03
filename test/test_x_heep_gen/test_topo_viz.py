"""Tests for the oh-my-soc topo-viz skill (semantic checks + rendering).

Run from the repo root: pytest test/test_x_heep_gen/test_topo_viz.py
"""

import pathlib
from pathlib import Path
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.append(str(REPO_ROOT))

from harness.skills.topo_viz import TopoViz  # noqa: E402

CONFIGS = {
    "obi": REPO_ROOT / "mosaic.yaml",
    "log": REPO_ROOT / "configs/mosaic_log.yaml",
    "floonoc": REPO_ROOT / "configs/mosaic_floonoc.yaml",
}


def test_check_clean_poc():
    result = TopoViz().check(CONFIGS["obi"])
    assert result.ok, result.errors


def test_check_log_bank_constraint(tmp_path):
    cfg = yaml.safe_load(CONFIGS["log"].read_text())
    cfg["soc"]["bus_opts"]["log"]["num_banks"] = 8  # < 13 masters
    p = tmp_path / "bad_log.yaml"
    p.write_text(yaml.safe_dump(cfg))
    result = TopoViz().check(p)
    assert not result.ok
    assert any("num_banks" in e for e in result.errors)


def test_check_inert_bus_opts_note(tmp_path):
    cfg = yaml.safe_load(CONFIGS["obi"].read_text())
    cfg["soc"]["bus_opts"] = {"log": {"topology": "lic"}}
    p = tmp_path / "inert.yaml"
    p.write_text(yaml.safe_dump(cfg))
    result = TopoViz().check(p)
    assert result.ok  # notes are not hard findings
    assert any("inert" in n for n in result.details["notes"])


def test_render_all_buses(tmp_path):
    for bus, cfg in CONFIGS.items():
        out = tmp_path / f"topo_{bus}.html"
        result = TopoViz().render(cfg, output=out)
        assert result.ok, result.errors
        assert result.details["bus"] == bus
        assert result.details["views"] == ["logical", "chip"]
        text = out.read_text()
        # Self-contained: the only URLs are SVG namespace names, never fetches
        refs = set(re.findall(r"https?://[^\"'\s<]+", text))
        assert refs == {"http://www.w3.org/2000/svg"}, refs
        assert "<svg" in text and "Memory map" in text


def test_render_counts_what_the_rtl_has():
    """The old digest said 13 masters and 2 banks for Block C (2*nh+1+4)."""
    result = TopoViz().render(REPO_ROOT / "configs/mosaic_blockc_4hart.yaml",
                              output=Path("/dev/null"))
    assert (result.details["crossbar_masters"], result.details["ram_banks"]) == (11, 1)


def test_render_svg_only(tmp_path):
    for view in ("logical", "chip"):
        out = tmp_path / f"topo_{view}.svg"
        result = TopoViz().render(CONFIGS["obi"], output=out, svg_only=True, view_kind=view)
        assert result.ok
        text = out.read_text()
        assert text.startswith("<svg") and "<html" not in text
