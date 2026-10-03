"""Byte-granular scratchpad (`memory.scratchpad_bytes`).

`memory.sram_kb` is integer KiB, but the size that matters for the Option C
minimal-scratchpad profile is below 1 KiB: the smallest GF180 SRAM macros are
64/128/256/512 bytes, and `gf180mcu_fd_ip_sram__sram512x8m8wm1` measures
0.209 mm² for 512 B against 0.419 mm² for a 1 KiB pair. At a 1.25 mm² budget
that is 17% of the die, so the schema must be able to say "512 B" rather than
round up.

Scope: schema and config layers. Realising a sub-KiB memory needs a scratchpad
instance outside x-heep's RAM bank pool — `Bank(size_k)` is integer kiB and a
positive power of two, so it cannot represent one. Generation therefore fails
with that reason rather than silently building a 1 KiB bank.
"""

import pathlib
import sys

import pytest

from util.xheep_gen.core_registry import FLASH_XIP_BASE, validate_soc_config

# Same path shim as the other suites: make util/xheep_gen importable.
sys.path.append(
    str(pathlib.Path(__file__).resolve().parents[2].joinpath("util/xheep_gen"))
)


def _cfg(**memory):
    mem = {"sram_kb": 0, "boot_rom_kb": 1, "scratchpad_bytes": 512}
    mem.update(memory)
    return {
        "soc": {
            "name": "scratch",
            "pdk": "gf180mcu",
            "target": "simulation",
            "cores": [
                {"ip": "cv32e20", "count": 1, "role": "titan", "isa": "rv32imc"},
                {"ip": "serv", "count": 1, "role": "atlas", "isa": "rv32i",
                 "boot_addr": FLASH_XIP_BASE + 0x10000},
                {"ip": "serv", "count": 1, "role": "atlas", "isa": "rv32i",
                 "boot_addr": FLASH_XIP_BASE + 0x11000},
            ],
            "memory": mem,
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart", "spi", "gpio"],
        }
    }


@pytest.mark.parametrize("size", [64, 128, 256, 512])
def test_realisable_macro_sizes_validate(size):
    """The four GF180 sub-KiB macro capacities."""
    assert validate_soc_config(_cfg(scratchpad_bytes=size)) == []


def test_one_kib_and_above_is_redirected_to_sram_kb():
    """The whole point of the field is sub-KiB; 1 KiB is expressible already."""
    errors = validate_soc_config(_cfg(scratchpad_bytes=1024))
    assert any("use memory.sram_kb" in e for e in errors), errors


def test_size_must_be_a_power_of_two():
    errors = validate_soc_config(_cfg(scratchpad_bytes=500))
    assert any("power of two" in e for e in errors), errors


def test_below_the_smallest_macro_is_rejected():
    errors = validate_soc_config(_cfg(scratchpad_bytes=32))
    assert any("smallest realisable macro" in e for e in errors), errors


@pytest.mark.parametrize("bad", [0, -512, "512", 512.0, True])
def test_non_positive_int_is_rejected(bad):
    errors = validate_soc_config(_cfg(scratchpad_bytes=bad))
    assert errors, f"{bad!r} was accepted"


def test_scratchpad_requires_zero_sram_kb():
    """Two separate writable memories is a configuration mistake, not a feature."""
    cfg = _cfg(sram_kb=16)
    for core in cfg["soc"]["cores"][1:]:
        core["boot_addr"] = 0x1000 if core is cfg["soc"]["cores"][1] else 0x2000
    errors = validate_soc_config(cfg)
    assert any("requires memory.sram_kb: 0" in e for e in errors), errors


def test_scratchpad_is_optional():
    """Omitting it leaves the XIP-only profile, which is still legal."""
    cfg = _cfg()
    del cfg["soc"]["memory"]["scratchpad_bytes"]
    assert validate_soc_config(cfg) == []


def test_scratchpad_coexists_with_external_ram():
    """Scratchpad for early stack, external RAM for the bulk — Option C in full."""
    cfg = _cfg()
    cfg["soc"]["memory"]["external"] = {"base": 0xF0000000, "size_kb": 64}
    assert validate_soc_config(cfg) == []


def test_generation_never_rounds_a_sub_kib_scratchpad_up():
    """512 B must never silently become a 1 KiB bank.

    Rounding would double the macro area (0.209 -> 0.419 mm2) while the config
    still said 512 B. The bank itself is now byte-exact, so the only way this
    could regress is a future shortcut in _resolve_base_config.
    """
    from memory_ss.memory_ss import MemorySS

    mem = MemorySS()
    mem.add_ram_bank_bytes(512)
    assert next(iter(mem.iter_ram_banks())).size() == 512, "scratchpad was rounded"


# ── hardware layer: sub-KiB banks are now representable ──────────────

def test_bank_carries_byte_sizes():
    """512 B is 128 words with a 7-bit address — what the RTL template needs."""
    from memory_ss.ram_bank import Bank

    b = Bank(0, 0, 1, size_bytes=512)
    assert b.size() == 512
    assert b.end_address() == 0x200
    assert b.size() // 4 == 128                 # sram_wrapper NumWords
    assert b.size().bit_length() - 1 - 2 == 7   # ram_req_addr width


def test_bank_kib_path_is_unchanged():
    from memory_ss.ram_bank import Bank

    b = Bank(32, 0, 1)
    assert b.size() == 32 * 1024
    assert b.end_address() == 0x8000


@pytest.mark.parametrize("bad,reason", [(500, "power of two"), (2, "word")])
def test_bank_rejects_bad_byte_sizes(bad, reason):
    from memory_ss.ram_bank import Bank

    with pytest.raises(ValueError, match=reason):
        Bank(0, 0, 1, size_bytes=bad)


def test_memory_ss_adds_a_byte_sized_bank():
    from memory_ss.memory_ss import MemorySS

    mem = MemorySS()
    mem.add_ram_bank_bytes(512)
    assert mem.ram_numbanks() == 1
    assert next(iter(mem.iter_ram_banks())).size() == 512


def test_loader_accepts_sizes_bytes():
    import hjson
    import load_config

    system = load_config.load_cfg_hjson(hjson.dumps({
        "ram_banks": {"code_and_data": {"sizes_bytes": 512}},
        "bus_type": "onetoM",
        "cpu_type": "cv32e20",
        "linker_sections": [
            {"name": "code", "start": 0, "size": 256},
            {"name": "data", "start": 256, "size": 256},
        ],
    }))
    assert system.memory_ss().ram_numbanks() == 1
    assert next(iter(system.memory_ss().iter_ram_banks())).size() == 512


def test_scratchpad_profile_resolves_to_flash_code_and_scratchpad_data():
    """Option C layout: code in the flash window, data in the scratchpad."""
    import hjson
    from jsonref import JsonRef

    from core_registry import FLASH_XIP_BASE
    from mosaic_config import MemoryConfig, MosaicConfig, _resolve_base_config

    repo = pathlib.Path(__file__).resolve().parents[2]
    base = JsonRef.replace_refs(hjson.load(open(repo / "configs/general.hjson")))

    cfg = MosaicConfig()
    cfg.memory = MemoryConfig(sram_kb=0, boot_rom_kb=1, scratchpad_bytes=512)
    out = _resolve_base_config(base, cfg)

    assert out["ram_banks"] == {"code_and_data": {"sizes_bytes": 512}}
    code, data = out["linker_sections"]
    assert code["name"] == "code" and code["start"] == FLASH_XIP_BASE
    assert data["name"] == "data" and data["start"] == 0
    assert data["size"] == 512
    # Stack fits inside the scratchpad, heap is disabled.
    assert int(out["linker_script"]["stack_size"], 16) <= 512
    assert int(out["linker_script"]["heap_size"], 16) == 0


def test_out_of_bank_code_section_validates():
    """A `code` section in the flash window is legal and outranks ordering.

    MemorySS sorts sections by address, so with code at 0x40000000 and the
    scratchpad at 0x0 the sorted order is data-then-code. The positional
    code-before-data rule must not fire, because code is not on-chip at all.
    """
    import hjson
    import load_config

    system = load_config.load_cfg_hjson(hjson.dumps({
        "ram_banks": {"code_and_data": {"sizes_bytes": 512}},
        "bus_type": "onetoM",
        "cpu_type": "cv32e20",
        "linker_sections": [
            {"name": "code", "start": 0x40000000, "size": 0xFFF000},
            {"name": "data", "start": 0, "size": 512},
        ],
    }))
    mem = system.memory_ss()
    mem.build()
    mem.validate()  # must not raise
    assert [s.name for s in mem.iter_linker_sections()] == ["data", "code"]


def test_on_chip_ordering_rule_is_still_enforced():
    """Relaxing the rule for XIP must not relax it for on-chip designs."""
    from memory_ss.linker_section import LinkerSection
    from memory_ss.memory_ss import MemorySS

    mem = MemorySS()
    mem.add_ram_banks([32])
    # Both on-chip, deliberately misnamed/misordered.
    mem.add_linker_section(LinkerSection("data", 0, 0x4000))
    mem.add_linker_section(LinkerSection("code", 0x4000, 0x8000))
    mem.build()
    with pytest.raises(RuntimeError, match="first linker section should be called code"):
        mem.validate()


def test_section_in_ram_banks_helper():
    from memory_ss.linker_section import LinkerSection
    from memory_ss.memory_ss import MemorySS

    mem = MemorySS()
    mem.add_ram_banks([32])
    assert mem.section_in_ram_banks(LinkerSection("data", 0, 0x100))
    assert not mem.section_in_ram_banks(LinkerSection("code", 0x40000000, 0x40001000))


# ── software layout: Option C images and shared window ───────────────

def _mosaic_cfg(scratch=512, n_workers=2):
    from mosaic_config import HartConfig, MemoryConfig, MosaicConfig

    cfg = MosaicConfig()
    cfg.memory = MemoryConfig(sram_kb=0, boot_rom_kb=1, scratchpad_bytes=scratch)
    harts = [HartConfig(0, 0, 0, "cv32e20", "rv32imc", "titan", {})]
    for i in range(n_workers):
        harts.append(
            HartConfig(i + 1, i + 1, 0, "serv", "rv32i", "atlas",
                       {"boot_addr": 0x40010000 + i * 0x1000})
        )
    cfg.harts = harts
    cfg.total_cores = len(harts)
    return cfg


def test_images_are_placed_in_the_flash_window():
    """Code XIPs; nothing is staged. The TITAN lands at the boot ROM's target."""
    from software_gen import FLASH_BASE, _layout

    images, _, _, _ = _layout(_mosaic_cfg())
    loads = [int(im["load_address"], 16) for im in images]
    assert all(FLASH_BASE <= a for a in loads), loads
    # TITAN has no boot_addr; the boot ROM's _execute_from_flash jumps to
    # FLASH_MEM + 0x180, which is also pack_flash.py's TITAN_OFFSET.
    assert FLASH_BASE + 0x180 in loads
    assert all(im.get("execute_in_place") for im in images)


def test_shared_window_sits_at_the_top_of_the_scratchpad():
    from software_gen import _layout

    _, shared_base, shared_size, data_base = _layout(_mosaic_cfg(scratch=512))
    assert shared_base + shared_size == 512, "shared window must end at the top"
    assert data_base == 0, "data/stack grow from the bottom"
    assert shared_size < 512, "a stack needs room below the shared window"


def test_result_region_stays_inside_the_scratchpad():
    """A fixed +0x100 result offset used to land past a 512 B scratchpad."""
    from software_gen import _layout, _result_offset

    _, shared_base, shared_size, _ = _layout(_mosaic_cfg(scratch=512))
    assert shared_base + _result_offset(shared_size) < 512


def test_result_offset_is_unchanged_for_on_chip_windows():
    """Every on-chip config produces a 0x200 window; the offset must stay 0x100."""
    from software_gen import _result_offset

    assert _result_offset(0x200) == 0x100


def test_scratchpad_too_small_for_its_harts_is_rejected():
    from software_gen import SoftwareGenerationError, _layout

    with pytest.raises(SoftwareGenerationError, match="cannot hold the shared-control"):
        _layout(_mosaic_cfg(scratch=64, n_workers=14))


def test_writable_bytes_covers_both_profiles():
    from mosaic_config import MemoryConfig, MosaicConfig
    from software_gen import _writable_bytes

    on_chip = MosaicConfig()
    on_chip.memory = MemoryConfig(sram_kb=16)
    assert _writable_bytes(on_chip) == 16 * 1024

    scratch = MosaicConfig()
    scratch.memory = MemoryConfig(sram_kb=0, scratchpad_bytes=512)
    assert _writable_bytes(scratch) == 512


# ── flash packing for XIP boot ───────────────────────────────────────

def test_xip_hex_addresses_are_rebased_to_flash_offsets(tmp_path):
    """`$readmemh` indexes the flash byte array, so records must be offsets.

    objcopy emits absolute 0x4000_xxxx addresses; left alone, every image
    would index past the end of a 16 MiB array and silently vanish.
    """
    sys.path.append(
        str(pathlib.Path(__file__).resolve().parents[2].joinpath("tb/mosaic_soc"))
    )
    from pack_xip_hex import rebase

    out = rebase("@40000180\nDE AD BE EF\n", "image_0.hex")
    assert out.splitlines()[0] == "@00000180"
    assert "DE AD BE EF" in out


def test_xip_packer_rejects_a_non_flash_image():
    """An image linked into RAM is not execute-in-place; catch it loudly."""
    sys.path.append(
        str(pathlib.Path(__file__).resolve().parents[2].joinpath("tb/mosaic_soc"))
    )
    from pack_xip_hex import rebase

    with pytest.raises(SystemExit, match="outside the flash window"):
        rebase("@00001000\n00 00 00 00\n", "image_1.hex")


def test_xip_packer_rejects_a_hex_without_an_address():
    sys.path.append(
        str(pathlib.Path(__file__).resolve().parents[2].joinpath("tb/mosaic_soc"))
    )
    from pack_xip_hex import rebase

    with pytest.raises(SystemExit, match="no @address record"):
        rebase("00 11 22 33\n", "image_2.hex")


def test_stack_stride_is_capped_by_the_writable_region():
    """The 0x400/0x100 defaults assume a multi-KiB SRAM.

    With a 512 B scratchpad, .hart_stacks (stride x harts) overflowed data_rw
    by exactly the size of the whole memory. The cap must bind for the
    scratchpad and must NOT bind for on-chip memories.
    """
    from software_gen import _image_linker, _layout

    images, _, _, _ = _layout(_mosaic_cfg(scratch=512))
    titan = next(im for im in images if 0 in im["harts"])

    def stride_of(sram_end, data_base):
        text = _image_linker(titan, sram_end=sram_end, data_base=data_base)
        line = next(l for l in text.splitlines() if "__mosaic_stack_stride" in l)
        return int(line.split("=")[1].strip().rstrip(";"), 16)

    assert stride_of(0x200, 0) < 0x400, "cap did not bind for a 512 B scratchpad"
    assert stride_of(0x4000, 0x3200) == 0x400, "cap must not bind on-chip"
