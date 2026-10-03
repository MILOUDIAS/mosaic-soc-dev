"""Tests for the 2026-07 Berkeley core additions (rocket, boom — RV64,
SIM-ONLY): registry membership, SCI classification, and config parsing of the
shipped bring-up / combined yamls.

Run from the repo root: python3 -m pytest test/test_x_heep_gen/test_berkeley_cores.py
"""

import pathlib
import sys

import pytest

# Same path shim as the other suites: make util/xheep_gen importable.
directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.append(str(directory.joinpath("util/xheep_gen")))

import mosaic_config
from cpu.cpu import CPU
from pathlib import PurePath

REPO_ROOT = directory

BERKELEY_CORES = ["rocket", "boom"]


@pytest.mark.parametrize("name", BERKELEY_CORES)
def test_berkeley_core_in_available_cpus(name):
    assert name in CPU.AVAILABLE_CPUS
    assert CPU(name).get_name() == name  # constructor accepts it


@pytest.mark.parametrize("name", BERKELEY_CORES)
def test_berkeley_core_is_sci(name):
    # Both integrate through hw/sci/<core>_sci.sv (TileLink->OBI bridged).
    assert name in mosaic_config.SCI_CORES


@pytest.mark.parametrize(
    "yaml_name,expected_ips",
    [
        ("mosaic_rocket.yaml", {"cv32e20", "rocket"}),
        ("mosaic_boom.yaml", {"cv32e20", "boom"}),
        ("mosaic_berkeley.yaml", {"cv32e20", "rocket", "boom"}),
    ],
)
def test_shipped_config_parses(yaml_name, expected_ips, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    cfg = mosaic_config.load_mosaic_yaml(
        PurePath(str(REPO_ROOT / "configs" / yaml_name))
    )
    ips = {g.ip for g in cfg.cpu_groups}
    assert ips == expected_ips
    # 3-hart wake-demo shape: 1 titan + 2 dormant workers with distinct boots.
    assert sum(g.count for g in cfg.cpu_groups) == 3
    roles = [g.role for g in cfg.cpu_groups for _ in range(g.count)]
    assert roles.count("titan") == 1
    kwargs = mosaic_config.mosaic_to_xheep_kwargs(cfg)
    assert kwargs is not None


def test_berkeley_config_hart_order(monkeypatch):
    # The combined demo relies on hart 0=titan(cv32e20), 1=rocket, 2=boom.
    monkeypatch.chdir(REPO_ROOT)
    cfg = mosaic_config.load_mosaic_yaml(
        PurePath(str(REPO_ROOT / "configs" / "mosaic_berkeley.yaml"))
    )
    order = [(g.ip, g.role, g.hart_id_base) for g in cfg.cpu_groups]
    assert order == [
        ("cv32e20", "titan", 0),
        ("rocket", "atlas", 1),
        ("boom", "nano", 2),
    ]


def test_berkeley_worker_boot_addrs(monkeypatch):
    # boot_addr params must reach group.params (the SCI wrapper aliases them
    # into the tile's DRAM window: 0x8000_0000 | boot_addr).
    monkeypatch.chdir(REPO_ROOT)
    cfg = mosaic_config.load_mosaic_yaml(
        PurePath(str(REPO_ROOT / "configs" / "mosaic_berkeley.yaml"))
    )
    by_role = {g.role: g for g in cfg.cpu_groups}
    assert int(str(by_role["atlas"].params["boot_addr"]), 0) == 0x1000
    assert int(str(by_role["nano"].params["boot_addr"]), 0) == 0x2000


@pytest.mark.parametrize(
    "yaml_name,controller",
    [("mosaic_rocket_titan.yaml", "rocket"), ("mosaic_boom_titan.yaml", "boom")],
)
def test_berkeley_titan_generic_regression_configs(yaml_name, controller, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    cfg = mosaic_config.load_mosaic_yaml(PurePath(str(REPO_ROOT / "configs" / yaml_name)))
    assert cfg.profile == "testbench"
    assert cfg.target == "simulation"
    assert [(g.ip, g.role, g.hart_id_base) for g in cfg.cpu_groups] == [
        (controller, "titan", 0),
        ("serv", "nano", 1),
    ]
    assert int(str(cfg.cpu_groups[0].params["boot_addr"]), 0) == 0x180


def test_verilator_sram_preload_blocks_reset_release():
    tb = (REPO_ROOT / "tb" / "tb_top.sv").read_text()
    preload = tb.index("testharness_i.tb_loadHEX(firmware);")
    preload_done = tb.index("verilator_preload_done = 1'b1;")
    reset_wait = tb.index("wait (verilator_preload_done == 1'b1);")
    reset_release = tb.index("#RESET_DEL rst_n = 1'b1;")
    assert preload < preload_done < reset_wait < reset_release
