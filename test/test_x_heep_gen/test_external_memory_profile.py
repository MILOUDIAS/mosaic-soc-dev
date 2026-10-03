"""External-memory profile (`memory.sram_kb: 0`) — PROTOTYPE.

Declares a SoC with no on-chip SRAM macros, executing and storing entirely in
off-chip memory on x-heep's external-slave window (`EXT_SLAVE_START_ADDRESS
0xF0000000`, 16 MB).

Motivation is area: the largest GF180 SRAM macro holds 512 bytes and costs
0.209 mm², i.e. 0.419 mm²/KB, so the previous 8 KB floor was already 3.35 mm²
— see docs/area_study_gf180_min_soc.md.

**Scope: the schema layer only.** RTL generation for this profile does not yet
work; x-heep's ``MemorySS.add_ram_banks`` requires a non-empty bank list
(``memory_ss.py:62``) and the linker sections are derived from banks. These
tests pin the validation contract so the generator work can proceed against a
stable schema.
"""

import pytest

from util.xheep_gen.core_registry import (
    EXT_SLAVE_BASE,
    EXT_SLAVE_SIZE_KB,
    validate_soc_config,
)


def _cfg(**memory):
    mem = {"sram_kb": 0, "boot_rom_kb": 1,
           "external": {"base": 0xF0000000, "size_kb": 64}}
    mem.update(memory)
    return {
        "soc": {
            "name": "nosram",
            "pdk": "gf180mcu",
            "target": "simulation",
            "cores": [
                {"ip": "cv32e20", "count": 1, "role": "titan", "isa": "rv32imc"},
                {"ip": "serv", "count": 1, "role": "atlas", "isa": "rv32i",
                 "boot_addr": 0xF0001000},
                {"ip": "serv", "count": 1, "role": "atlas", "isa": "rv32i",
                 "boot_addr": 0xF0002000},
            ],
            "memory": mem,
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart", "spi", "gpio"],
        }
    }


def test_external_slave_window_matches_xheep_header():
    assert EXT_SLAVE_BASE == 0xF0000000
    assert EXT_SLAVE_SIZE_KB * 1024 == 0x01000000


def test_no_sram_profile_validates():
    assert validate_soc_config(_cfg()) == []


def test_zero_sram_without_external_is_the_xip_only_profile():
    """Superseded rule.

    sram_kb: 0 originally *required* memory.external, on the reasoning that
    stack/.data/.bss need somewhere writable. Option C
    (docs/external_memory_boot_design.md) splits that into two legal shapes:
    with external RAM, and XIP-only where nothing writes to memory at all.
    Schema-legality is now decided by boot_addr placement, so the config below
    validates -- and is rejected later, by software generation, for the real
    reason (no stack, no C runtime).
    """
    cfg = _cfg()
    del cfg["soc"]["memory"]["external"]
    for core in cfg["soc"]["cores"][1:]:
        core["boot_addr"] = 0x40010000  # flash XIP window
    assert validate_soc_config(cfg) == []


def test_worker_boot_addr_must_be_inside_the_declared_region():
    cfg = _cfg()
    cfg["soc"]["cores"][2]["boot_addr"] = 0x00002000  # on-chip SRAM address
    errors = validate_soc_config(cfg)
    assert any("declared external region" in e for e in errors), errors


def test_titan_default_reset_vector_is_not_region_checked():
    """A production TITAN has no boot_addr — it enters the boot ROM.

    Its 0x180 placeholder must not be measured against external memory.
    """
    cfg = _cfg()
    assert "boot_addr" not in cfg["soc"]["cores"][0]
    assert validate_soc_config(cfg) == []


def test_external_base_must_be_in_the_external_slave_window():
    errors = validate_soc_config(
        _cfg(external={"base": 0x20000000, "size_kb": 64})
    )
    assert any("external-slave window" in e for e in errors), errors


def test_external_size_is_bounded():
    errors = validate_soc_config(
        _cfg(external={"base": 0xF0000000, "size_kb": 0})
    )
    assert any("size_kb" in e for e in errors), errors


def test_unknown_external_keys_are_rejected():
    errors = validate_soc_config(
        _cfg(external={"base": 0xF0000000, "size_kb": 64, "kind": "psram"})
    )
    assert any("kind" in e for e in errors), errors


@pytest.mark.parametrize("sram_kb", [16, 32, 64, 512])
def test_on_chip_profiles_are_unaffected(sram_kb):
    """The existing SRAM path must keep validating exactly as before.

    8 KB is excluded deliberately: two worker slots at 0x1000/0x2000 plus
    shared control and stack do not fit, which the boot-image fit check
    already reports and which is orthogonal to this profile.
    """
    cfg = _cfg(sram_kb=sram_kb, external=None)
    del cfg["soc"]["memory"]["external"]
    cfg["soc"]["cores"][1]["boot_addr"] = 0x1000
    cfg["soc"]["cores"][2]["boot_addr"] = 0x2000
    assert validate_soc_config(cfg) == []


# ── generator: how far the external-memory profile now gets ──────────

def test_zero_banks_pass_memory_ss_validation():
    """MemorySS accepts zero banks; the linker sections carry the addresses."""
    from util.xheep_gen.memory_ss.memory_ss import MemorySS
    from util.xheep_gen.memory_ss.linker_section import LinkerSection

    mem = MemorySS()
    mem.add_linker_section(LinkerSection("code", 0xF0000000, 0xF0008000))
    mem.add_linker_section(LinkerSection("data", 0xF0008000, 0xF0010000))
    mem.validate()  # must not raise
    assert mem.ram_numbanks() == 0


def test_nonzero_bank_bounds_are_still_enforced():
    """Relaxing zero must not relax the 1..16 window for real bank pools.

    No section name: passing one routes `banks` into the `subsections`
    parameter of add_linker_section_for_banks (a pre-existing bug in that
    vendored path, unreachable because no shipped config sets `auto_section`).
    """
    from util.xheep_gen.memory_ss.memory_ss import MemorySS

    mem = MemorySS()
    mem.add_ram_banks([32] * 20)
    with pytest.raises(RuntimeError, match="number of banks"):
        mem.validate(max_banks=16)


def test_software_generation_reports_the_external_memory_gap():
    """The remaining blocker must name itself, not surface as empty SRAM."""
    import types

    from util.xheep_gen import software_gen

    cfg = types.SimpleNamespace(
        memory=types.SimpleNamespace(sram_kb=0, external={"size_kb": 64}), harts=[]
    )
    with pytest.raises(software_gen.SoftwareGenerationError) as excinfo:
        software_gen._layout(cfg)
    message = str(excinfo.value)
    assert "external-memory profile" in message
    assert "XIP" in message


def test_software_generation_reports_the_xip_only_wall():
    """XIP-only fails for a different reason and must say so.

    Not "external RAM is uninitialised" but "there is no writable memory at
    all" -- no stack means no C runtime for any hart, and the TDU liveness
    protocol has no shared-control window to write sentinels into.
    """
    import types

    from util.xheep_gen import software_gen

    cfg = types.SimpleNamespace(
        memory=types.SimpleNamespace(sram_kb=0, external=None), harts=[]
    )
    with pytest.raises(software_gen.SoftwareGenerationError) as excinfo:
        software_gen._layout(cfg)
    message = str(excinfo.value)
    assert "XIP-only profile" in message
    assert "NO WRITABLE MEMORY" in message
    assert "CPI_EST" in message  # names the concrete alternative


def test_worker_may_boot_from_the_flash_xip_window():
    from util.xheep_gen.core_registry import FLASH_XIP_BASE

    cfg = _cfg()
    del cfg["soc"]["memory"]["external"]
    cfg["soc"]["cores"][1]["boot_addr"] = FLASH_XIP_BASE + 0x10000
    cfg["soc"]["cores"][2]["boot_addr"] = FLASH_XIP_BASE + 0x11000
    assert validate_soc_config(cfg) == []


def test_boot_addr_outside_flash_and_external_is_rejected():
    cfg = _cfg()
    del cfg["soc"]["memory"]["external"]
    cfg["soc"]["cores"][1]["boot_addr"] = 0x40010000
    cfg["soc"]["cores"][2]["boot_addr"] = 0x00002000  # on-chip SRAM: none exists
    errors = validate_soc_config(cfg)
    assert any("flash XIP window" in e for e in errors), errors


def test_software_generation_still_lays_out_on_chip_profiles():
    """The new guard must only fire for sram_kb == 0."""
    import types

    from util.xheep_gen import software_gen

    cfg = types.SimpleNamespace(memory=types.SimpleNamespace(sram_kb=16), harts=[])
    with pytest.raises(software_gen.SoftwareGenerationError) as excinfo:
        software_gen._layout(cfg)
    # Reaches the real layout and fails on "no harts", not on the profile guard.
    assert "external-memory profile" not in str(excinfo.value)
