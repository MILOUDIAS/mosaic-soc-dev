"""Tests for the mosaic.yaml bus seam: bus string -> BusType mapping,
bus_opts parsing, and the LOG-bus memory/validation gates.

Run from the repo root: .venv/bin/python -m pytest test/test_x_heep_gen/test_bus_types.py
"""

import pathlib
import sys

import pytest
import yaml

# Same path shim as test_peripherals.py: make util/xheep_gen importable.
directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.append(str(directory.joinpath("util/xheep_gen")))

import mosaic_config
from bus_type import BusType
from pathlib import PurePath

REPO_ROOT = directory
POC_YAML = REPO_ROOT / "mosaic.yaml"


def make_cfg(tmp_path, bus, bus_opts=None, sram_kb=None):
    """Clone the PoC mosaic.yaml with a different bus/bus_opts/memory."""
    cfg = yaml.safe_load(POC_YAML.read_text())
    # These tests exercise the RTL generator design space, not the narrower
    # GF180 tapeout qualification matrix carried by the canonical PoC.
    cfg["soc"]["target"] = "rtl"
    cfg["soc"]["bus"] = bus
    if bus_opts is not None:
        cfg["soc"]["bus_opts"] = bus_opts
    if sram_kb is not None:
        cfg["soc"]["memory"]["sram_kb"] = sram_kb
    path = tmp_path / f"mosaic_{bus}.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return PurePath(str(path))


def build_kwargs(path, monkeypatch):
    """Full parse + XHeep build/validate, from the repo root."""
    monkeypatch.chdir(REPO_ROOT)
    cfg = mosaic_config.load_mosaic_yaml(path)
    return cfg, mosaic_config.mosaic_to_xheep_kwargs(cfg)


def test_obi_maps_to_ntom(tmp_path, monkeypatch):
    _, kw = build_kwargs(make_cfg(tmp_path, "obi"), monkeypatch)
    assert kw["xheep"].bus_type() == BusType.NtoM
    # mosaic.yaml is authoritative: the PoC requests one 32 KiB bank rather
    # than inheriting general.hjson's former 2 x 32 KiB layout.
    assert kw["xheep"].memory_ss().ram_numbanks() == 1
    assert kw["xheep"].memory_ss().ram_size_address() == 32 * 1024
    assert not kw["xheep"].memory_ss().has_il_ram()


def test_obi_memory_size_follows_yaml(tmp_path, monkeypatch):
    _, kw = build_kwargs(make_cfg(tmp_path, "obi", sram_kb=64), monkeypatch)
    assert kw["xheep"].memory_ss().ram_numbanks() == 2
    assert kw["xheep"].memory_ss().ram_size_address() == 64 * 1024


def test_floonoc_maps_to_floonoc(tmp_path, monkeypatch):
    _, kw = build_kwargs(make_cfg(tmp_path, "floonoc"), monkeypatch)
    assert kw["xheep"].bus_type() == BusType.FLOONOC


def test_log_auto_banks(tmp_path, monkeypatch):
    """PoC topology: 7 harts + debug + 2x2 iDMA = 19 masters -> 32 il banks."""
    _, kw = build_kwargs(make_cfg(tmp_path, "log"), monkeypatch)
    x = kw["xheep"]
    assert x.bus_type() == BusType.LOG
    assert x.num_bus_masters() == 19
    assert x.memory_ss().ram_numbanks() == 32
    assert x.memory_ss().ram_numbanks_il() == 32
    assert x.memory_ss().ram_size_address() == 32 * 1024


def test_log_explicit_banks_too_small(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match=r"num_banks >= .*bus masters"):
        build_kwargs(
            make_cfg(tmp_path, "log", bus_opts={"log": {"num_banks": 8}}),
            monkeypatch,
        )


def test_log_bfly_is_rejected_by_public_schema(tmp_path):
    with pytest.raises(RuntimeError, match="only 'lic'"):
        mosaic_config.load_mosaic_yaml(
            make_cfg(tmp_path, "log", bus_opts={"log": {"topology": "bfly4"}})
        )


def test_log_schema_rejects_auto_bank_overflow(tmp_path):
    path = make_cfg(tmp_path, "log")
    raw = yaml.safe_load(pathlib.Path(path).read_text())
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 15, "role": "titan"}
    ]
    raw["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    pathlib.Path(path).write_text(yaml.safe_dump(raw))
    with pytest.raises(RuntimeError, match="32-bank backend"):
        mosaic_config.load_mosaic_yaml(path)


def test_log_schema_rejects_too_little_sram_per_auto_bank(tmp_path):
    path = make_cfg(tmp_path, "log", sram_kb=8)
    raw = yaml.safe_load(pathlib.Path(path).read_text())
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 2, "role": "titan"}
    ]
    raw["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    pathlib.Path(path).write_text(yaml.safe_dump(raw))
    with pytest.raises(RuntimeError, match="at least 1 KiB per bank"):
        mosaic_config.load_mosaic_yaml(path)


def test_axi_hard_error(tmp_path):
    with pytest.raises(RuntimeError, match="never a distinct fabric"):
        mosaic_config.load_mosaic_yaml(make_cfg(tmp_path, "axi"))


def test_unknown_bus_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="unsupported bus type"):
        mosaic_config.load_mosaic_yaml(make_cfg(tmp_path, "wishbone"))


def test_bus_opts_typo_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="unknown option"):
        mosaic_config.load_mosaic_yaml(
            make_cfg(tmp_path, "log", bus_opts={"log": {"topolgy": "lic"}})
        )


def test_bus_opts_bad_topology_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="topology"):
        mosaic_config.load_mosaic_yaml(
            make_cfg(tmp_path, "log", bus_opts={"log": {"topology": "clos9"}})
        )


def test_bus_opts_defaults_registered_as_extension(tmp_path, monkeypatch):
    _, kw = build_kwargs(make_cfg(tmp_path, "log"), monkeypatch)
    opts = kw["xheep"].get_extension("bus_opts")
    assert opts["log"]["topology"] == "lic"
    assert opts["floonoc"]["route_algo"] == "ID"
