"""Tests for the 2026-07 core additions (picorv32, snitch, cva6): registry
membership, SCI classification, and config parsing of the shipped bring-up /
combined yamls.

Run from the repo root: python3 -m pytest test/test_x_heep_gen/test_new_cores.py
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

NEW_CORES = ["picorv32", "snitch", "cva6"]


@pytest.mark.parametrize("name", NEW_CORES)
def test_new_core_in_available_cpus(name):
    assert name in CPU.AVAILABLE_CPUS
    assert CPU(name).get_name() == name  # constructor accepts it


@pytest.mark.parametrize("name", NEW_CORES)
def test_new_core_is_sci(name):
    # All three integrate through hw/sci/<core>_sci.sv wrappers.
    assert name in mosaic_config.SCI_CORES


@pytest.mark.parametrize(
    "yaml_name,expected_ips",
    [
        ("mosaic_picorv32.yaml", {"cv32e20", "picorv32"}),
        ("mosaic_snitch.yaml", {"cv32e20", "snitch"}),
        ("mosaic_cva6.yaml", {"cva6", "fazyrv", "serv"}),
        ("mosaic_new_cores.yaml", {"cva6", "snitch", "picorv32"}),
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


def test_combined_config_hart_order(monkeypatch):
    # The combined demo relies on hart 0=titan(cva6), 1=atlas, 2=nano.
    monkeypatch.chdir(REPO_ROOT)
    cfg = mosaic_config.load_mosaic_yaml(
        PurePath(str(REPO_ROOT / "configs" / "mosaic_new_cores.yaml"))
    )
    order = [(g.ip, g.role, g.hart_id_base) for g in cfg.cpu_groups]
    assert order == [
        ("cva6", "titan", 0),
        ("snitch", "atlas", 1),
        ("picorv32", "nano", 2),
    ]
