"""Contract tests for the authoritative MOSAIC resolved configuration."""

from pathlib import Path, PurePath
import sys

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.append(str(REPO_ROOT / "util" / "xheep_gen"))

import mosaic_config
from harness.core import validate_config


def valid_soc() -> dict:
    return {
        "soc": {
            "name": "strict_soc",
            "pdk": "gf180mcu",
            "cores": [
                {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
                {
                    "ip": "picorv32", "isa": "rv32i", "count": 1,
                    "role": "atlas", "boot_addr": 0x1000,
                },
            ],
            "memory": {"sram_kb": 32, "boot_rom_kb": 2},
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart"],
        }
    }


def write_cfg(tmp_path: Path, cfg: dict) -> PurePath:
    path = tmp_path / "mosaic.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return PurePath(path)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda c: c["soc"].update({"memroy": {}}), "unknown key"),
        (lambda c: c["soc"]["memory"].update({"sram_kb": True}), "sram_kb"),
        (lambda c: c["soc"]["cores"][1].update({"chuncksize": 8}), "unknown key"),
        (lambda c: c["soc"]["cores"][1].update({"isa": "rv64imac"}), "not supported"),
        (lambda c: c["soc"].update({"name": "Not-A-Module"}), "soc.name"),
        (lambda c: c["soc"].update({"pdk": "proprietary7"}), "soc.pdk"),
    ],
)
def test_parser_and_harness_share_strict_schema(tmp_path, mutation, match):
    cfg = valid_soc()
    mutation(cfg)
    errors = validate_config(cfg)
    assert any(match in error for error in errors), errors
    with pytest.raises(RuntimeError, match=match):
        mosaic_config.parse_yaml(write_cfg(tmp_path, cfg))


def test_amp_topology_requires_one_leading_titan_and_tdu():
    no_tdu = valid_soc()
    no_tdu["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    assert any("requires scheduler.tdu=true" in e for e in validate_config(no_tdu))

    two_titans = valid_soc()
    two_titans["soc"]["cores"][0]["count"] = 2
    assert any("exactly one leading TITAN hart" in e for e in validate_config(two_titans))

    wrong_order = valid_soc()
    wrong_order["soc"]["cores"].extend(
        [{"ip": "serv", "isa": "rv32i", "count": 1, "role": "nano"}]
    )
    wrong_order["soc"]["cores"][1]["role"] = "nano"
    wrong_order["soc"]["cores"][2]["role"] = "atlas"
    assert any("ATLAS before NANO" in e for e in validate_config(wrong_order))


def test_all_titan_smp_may_disable_tdu():
    cfg = valid_soc()
    cfg["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 4, "role": "titan"}
    ]
    cfg["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    assert validate_config(cfg) == []


def test_platform_hart_limit_is_schema_gate_even_without_tdu():
    cfg = valid_soc()
    cfg["soc"]["cores"][1]["count"] = 16
    assert any("at most 16 harts" in e for e in validate_config(cfg))

    cfg["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 17, "role": "titan"}
    ]
    cfg["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    assert any("at most 16 harts" in e for e in validate_config(cfg))


def test_testbench_profile_is_required_for_worker_only_topology():
    cfg = valid_soc()
    cfg["soc"]["cores"] = [
        {"ip": "serv", "isa": "rv32i", "count": 2, "role": "nano"}
    ]
    assert any("leading TITAN" in e for e in validate_config(cfg))
    cfg["soc"]["profile"] = "testbench"
    assert validate_config(cfg) == []


def test_soc_workers_require_explicit_sram_boot_images():
    cfg = valid_soc()
    del cfg["soc"]["cores"][1]["boot_addr"]
    assert any("boot_addr is required" in e for e in validate_config(cfg))

    cfg["soc"]["profile"] = "testbench"
    assert validate_config(cfg) == []


def test_boot_contract_is_schema_validated_not_a_late_software_failure():
    outside = valid_soc()
    outside["soc"]["cores"][1]["boot_addr"] = 0x8000
    assert any("must select SRAM" in e for e in validate_config(outside))

    no_layout_room = valid_soc()
    no_layout_room["soc"]["memory"]["sram_kb"] = 8
    no_layout_room["soc"]["cores"][1]["boot_addr"] = 0x1800
    assert any("do not fit SRAM" in e for e in validate_config(no_layout_room))

    tiny_slot = valid_soc()
    tiny_slot["soc"]["cores"].append(
        {
            "ip": "serv",
            "isa": "rv32i",
            "count": 1,
            "role": "nano",
            "boot_addr": 0x1004,
        }
    )
    assert any(
        "boot image slot at 0x00001000 is only 4 bytes" in error
        for error in validate_config(tiny_slot)
    )


def test_production_titan_uses_the_boot_rom_reset_vector():
    cfg = valid_soc()
    cfg["soc"]["cores"][0]["boot_addr"] = 0x1000
    assert any("reset vector must remain the boot ROM" in e for e in validate_config(cfg))


def test_soc_shared_boot_image_must_have_one_abi():
    cfg = valid_soc()
    cfg["soc"]["cores"][1]["boot_addr"] = 0x180
    assert any("mixes incompatible ABIs" in e for e in validate_config(cfg))

    cfg["soc"]["profile"] = "testbench"
    assert validate_config(cfg) == []


def test_rocket_and_boom_require_explicit_translated_boot_window():
    cfg = valid_soc()
    cfg["soc"]["cores"][1] = {
        "ip": "rocket", "isa": "rv64imc", "count": 1, "role": "atlas"
    }
    assert any("translated code window" in e for e in validate_config(cfg))


@pytest.mark.parametrize("ip", ["rocket", "boom"])
def test_singleton_berkeley_core_may_be_testbench_titan(ip):
    cfg = valid_soc()
    cfg["soc"]["profile"] = "testbench"
    cfg["soc"]["target"] = "simulation"
    cfg["soc"]["cores"][0] = {
        "ip": ip,
        "isa": "rv64imc",
        "count": 1,
        "role": "titan",
        "boot_addr": 0x180,
    }
    assert validate_config(cfg) == []

    cfg["soc"]["cores"][0]["count"] = 2
    assert any("only as one leading" in e for e in validate_config(cfg))

    cfg["soc"]["cores"][0]["count"] = 1
    cfg["soc"]["cores"] = [cfg["soc"]["cores"][1], cfg["soc"]["cores"][0]]
    assert any("only as one leading" in e for e in validate_config(cfg))


def test_multihart_testbench_workers_require_tdu():
    cfg = valid_soc()
    cfg["soc"]["profile"] = "testbench"
    cfg["soc"]["cores"] = [
        {"ip": "serv", "isa": "rv32i", "count": 2, "role": "nano"}
    ]
    cfg["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    assert any("release dormant workers" in e for e in validate_config(cfg))

    titan_and_worker = valid_soc()
    titan_and_worker["soc"]["profile"] = "testbench"
    titan_and_worker["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    assert any(
        "release dormant workers" in error
        for error in validate_config(titan_and_worker)
    )


def test_repeated_ip_hart_mapping_is_aggregated(tmp_path):
    cfg = valid_soc()
    cfg["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
        {
            "ip": "picorv32", "isa": "rv32i", "count": 1,
            "role": "atlas", "boot_addr": 0x1000,
        },
        {
            "ip": "picorv32", "isa": "rv32i", "count": 2,
            "role": "nano", "boot_addr": 0x2000,
        },
    ]
    resolved = mosaic_config.parse_yaml(write_cfg(tmp_path, cfg))
    assert resolved.hart_id_map["picorv32"] == [1, 2, 3]
    assert [(h.hart_id, h.role) for h in resolved.harts] == [
        (0, "titan"), (1, "atlas"), (2, "nano"), (3, "nano")
    ]


def test_resolved_xheep_uses_yaml_memory_rom_peripherals_and_metadata(
    tmp_path, monkeypatch
):
    cfg = valid_soc()
    cfg["soc"]["name"] = "resolved_soc"
    cfg["soc"]["pdk"] = "sky130"
    cfg["soc"]["memory"] = {"sram_kb": 64, "boot_rom_kb": 1}
    cfg["soc"]["peripherals"] = ["uart"]
    monkeypatch.chdir(REPO_ROOT)
    parsed = mosaic_config.parse_yaml(write_cfg(tmp_path, cfg))
    kwargs = mosaic_config.mosaic_to_xheep_kwargs(parsed)
    xheep = kwargs["xheep"]

    assert xheep.memory_ss().ram_size_address() == 64 * 1024
    assert xheep.memory_ss().ram_numbanks() == 2
    bootrom = next(
        p for p in xheep.get_base_peripheral_domain().get_peripherals()
        if p.get_name() == "bootrom"
    )
    assert bootrom.get_length() == 1024

    # PLIC/timer are mandatory multicore services; UART is requested.  Fixed
    # general.hjson options such as GPIO/I2C/SPI/I2S must not leak through.
    names = {p.get_name() for p in xheep.get_user_peripheral_domain().get_peripherals()}
    assert names == {"rv_plic", "rv_timer", "uart"}
    assert xheep.get_extension("soc_name") == "resolved_soc"
    assert xheep.get_extension("pdk") == "sky130"
    assert xheep.get_extension("tdu_enabled") is True
    assert xheep.get_extension("sched_mode") == "dynamic"
    assert xheep.get_extension("sched_mode_value") == 1
    assert [h.hart_id for h in xheep.get_extension("resolved_harts")] == [0, 1]


def test_cv32e20_native_parameters_are_derived_from_declared_isa(tmp_path):
    cfg = valid_soc()
    parsed = mosaic_config.parse_yaml(write_cfg(tmp_path, cfg))
    xheep = mosaic_config.mosaic_to_xheep_kwargs(parsed)["xheep"]
    params = xheep.cpus()[0].cpu.params
    assert params["rv32e"] is True
    assert params["rv32m"] == "RV32MFast"


@pytest.mark.parametrize(
    "parameter,value",
    [("rv32e", False), ("rv32m", "RV32MNone")],
)
def test_cv32e20_isa_parameter_mismatch_is_rejected(parameter, value):
    cfg = valid_soc()
    cfg["soc"]["cores"][0][parameter] = value
    assert any(parameter in error and "disagree" in error for error in validate_config(cfg))
