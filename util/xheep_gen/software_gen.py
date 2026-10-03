#!/usr/bin/env python3
"""Generate software-facing artifacts from a resolved MOSAIC topology.

The RTL and firmware must consume the same :class:`MosaicConfig`.  This module
is intentionally independent from ``mcu_gen.py`` so command-line flows, tests,
and build-bundle code can all call :func:`generate_software_artifacts` without
rendering RTL first.

Generated files are deterministic and contain no host-specific absolute paths:

* ``include/mosaic_topology.h`` -- per-hart role, ISA, boot and capabilities
* ``include/mosaic_memory_map.h`` -- SRAM, TDU, CLINT and selected peripherals
* ``include/mosaic_memory_map.inc`` -- assembler-safe address definitions
* ``include/mosaic_runtime.h`` -- per-image hart/stack startup contract
* ``startup/image_<n>_crt0.S`` -- opt-in startup when hart identity is available
* ``make/mosaic_isa.mk`` -- per-group and per-hart ``-march``/``-mabi`` data
* ``linker/mosaic_link.ld`` -- compatibility multi-image linker layout
* ``linker/image_<n>.ld`` -- one linker script per distinct boot image slot
* ``linker/titan_flash.ld`` -- production-only XIP TITAN linker for cold boot
* ``boot_images.json`` -- machine-readable boot/loading contract

The compatibility linker preserves the historical ``.atlas`` and ``.nano``
sections for the first image of each role.  New software should use the
generated ``.mosaic.image.<n>`` section names from ``boot_images.json``.
"""

from __future__ import annotations

import argparse
import binascii
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePath
import sys
from typing import Any, Dict, Iterable, Mapping, Sequence, TYPE_CHECKING

try:
    from .core_registry import (
        CORE_SPECS, expanded_user_peripherals, resolved_capabilities,
    )
except ImportError:  # Direct execution from util/xheep_gen.
    from core_registry import CORE_SPECS, expanded_user_peripherals, resolved_capabilities

if TYPE_CHECKING:
    try:
        from .mosaic_config import HartConfig, MosaicConfig
    except ImportError:
        from mosaic_config import HartConfig, MosaicConfig


SCHEMA_VERSION = 1
DEFAULT_BOOT_ADDRESS = 0x0000_0180
SRAM_BASE = 0x0000_0000
DEBUG_BASE = 0x1000_0000
DEBUG_SIZE = 0x0010_0000
AO_PERIPHERAL_BASE = 0x2000_0000
AO_PERIPHERAL_SIZE = 0x0010_0000
USER_PERIPHERAL_BASE = 0x3000_0000
USER_PERIPHERAL_SIZE = 0x0010_0000
FLASH_BASE = 0x4000_0000
FLASH_SIZE = 0x0100_0000
EXT_SLAVE_BASE = 0xF000_0000
EXT_SLAVE_SIZE = 0x0100_0000

SOC_CTRL_OFFSET = 0x0000_0000
BOOT_ROM_OFFSET = 0x0001_0000
TDU_OFFSET = 0x000A_0000
TDU_SIZE = 0x0000_1000
CLINT_OFFSET = 0x000B_0000
CLINT_SIZE = 0x0001_0000

USER_PERIPHERALS: Mapping[str, tuple[str, int, int]] = {
    "rv_plic": ("PLIC", 0x0000_0000, 0x0001_0000),
    "spi_host": ("SPI", 0x0001_0000, 0x0001_0000),
    "gpio": ("GPIO", 0x0002_0000, 0x0001_0000),
    "i2c": ("I2C", 0x0003_0000, 0x0001_0000),
    "rv_timer": ("TIMER", 0x0004_0000, 0x0001_0000),
    "uart": ("UART", 0x0008_0000, 0x0001_0000),
    "serial_link": ("SERIAL_LINK", 0x0009_0000, 0x0001_0000),
    "serial_link_reg": ("SERIAL_LINK_REG", 0x000A_0000, 0x0001_0000),
    "serial_link_receiver_fifo": (
        "SERIAL_LINK_RECEIVER_FIFO",
        0x000B_0000,
        0x0001_0000,
    ),
}

# These assignments are an ABI between generated firmware and the registry.
# New registry capabilities must be assigned deliberately instead of silently
# renumbering existing bits.
CAPABILITY_BITS: Mapping[str, int] = {
    "split_obi": 1 << 0,
    "unified_obi": 1 << 1,
    "debug": 1 << 2,
    "interrupts": 1 << 3,
    "xif": 1 << 4,
    "cached": 1 << 5,
    "timer_interrupt": 1 << 6,
    "mhartid": 1 << 7,
}

ROLE_VALUES = {"titan": 0, "atlas": 1, "nano": 2}
SCHED_VALUES = {"static": 0, "dynamic": 1, "power-aware": 2}


class SoftwareGenerationError(RuntimeError):
    """The resolved hardware topology cannot produce a safe software layout."""



def _result_offset(shared_size: int) -> int:
    """Offset of the result region inside the shared-control window.

    The upper half, clamped to the historical 0x100. Every on-chip config
    produces a 0x200 window, so this is byte-identical there; the
    minimal-scratchpad profile produces 0x40 and would otherwise place results
    outside the scratchpad entirely.
    """
    return min(0x100, shared_size // 2)


def _writable_bytes(cfg: "MosaicConfig") -> int:
    """Size of the on-chip WRITABLE region, in bytes.

    Normally that is the SRAM bank pool (``memory.sram_kb``). Under the
    minimal-scratchpad profile there is no bank pool: `sram_kb` is 0 and the
    only on-chip writable memory is the sub-KiB scratchpad, which is sized in
    bytes precisely because integer KiB cannot express it
    (docs/external_memory_boot_design.md). Everything that used to compute
    ``cfg.memory.sram_kb * 1024`` goes through here so the two profiles cannot
    drift apart.
    """
    if cfg.memory.sram_kb:
        return cfg.memory.sram_kb * 1024
    return int(getattr(cfg.memory, "scratchpad_bytes", 0) or 0)

def _read_make_fragment(path: str | Path) -> Dict[str, str]:
    """Read the simple ``NAME := value`` assignments emitted by this module."""

    values: Dict[str, str] = {}
    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":=" not in line:
            continue
        name, value = line.split(":=", 1)
        values[name.strip()] = value.strip()
    return values


def production_demo_contract_errors(
    boot_manifest: Mapping[str, Any], isa_make: Mapping[str, str]
) -> list[str]:
    """Return reasons the bundled production demo cannot run this topology.

    This validates only ``sw/firmware``'s concrete three-program demo. A
    failure does *not* invalidate the generated BSP, per-image linkers, or boot
    manifest, which remain the contract for topology-specific applications.
    """

    errors: list[str] = []
    soc = boot_manifest.get("soc", {})
    if soc.get("profile") != "soc":
        errors.append("production demo requires soc.profile='soc'")
    scheduler = boot_manifest.get("scheduler", {})
    if scheduler.get("tdu") is not True:
        errors.append("scheduler.tdu must be enabled")

    def group_ids(role: str) -> list[int]:
        raw = isa_make.get(f"MOSAIC_{role.upper()}_GROUPS", "")
        try:
            return [int(item, 10) for item in raw.split()]
        except ValueError:
            errors.append(f"{role.upper()} group list is malformed: {raw!r}")
            return []

    role_groups = {role: group_ids(role) for role in ROLE_VALUES}
    if len(role_groups["titan"]) != 1:
        errors.append(
            "production demo requires exactly one TITAN group; "
            f"found {len(role_groups['titan'])}"
        )
    for role in ("atlas", "nano"):
        if not role_groups[role]:
            errors.append(
                f"production demo requires at least one {role.upper()} group"
            )

    try:
        declared_group_count = int(isa_make.get("MOSAIC_NUM_GROUPS", ""), 10)
    except ValueError:
        declared_group_count = -1
        errors.append("MOSAIC_NUM_GROUPS is missing or malformed")
    listed_groups = [group for groups in role_groups.values() for group in groups]
    if declared_group_count >= 0 and sorted(listed_groups) != list(
        range(declared_group_count)
    ):
        errors.append(
            "role group lists do not cover every generated core group exactly once"
        )

    role_images: Dict[str, set[int]] = {}
    role_isas: Dict[str, set[str]] = {}
    role_abis: Dict[str, set[str]] = {}
    for role, groups in role_groups.items():
        images: set[int] = set()
        isas: set[str] = set()
        abis: set[str] = set()
        for group in groups:
            prefix = f"MOSAIC_GROUP_{group}"
            recorded_role = isa_make.get(f"{prefix}_ROLE")
            if recorded_role != role:
                errors.append(
                    f"group {group} is listed as {role.upper()} but records role "
                    f"{recorded_role!r}"
                )
            try:
                images.add(int(isa_make[f"{prefix}_IMAGE"], 10))
            except (KeyError, ValueError):
                errors.append(f"group {group} has no valid boot image mapping")
            isa = isa_make.get(f"{prefix}_MARCH")
            abi = isa_make.get(f"{prefix}_MABI")
            if isa:
                isas.add(isa)
            else:
                errors.append(f"group {group} has no generated ISA")
            if abi:
                abis.add(abi)
            else:
                errors.append(f"group {group} has no generated ABI")
        role_images[role] = images
        role_isas[role] = isas
        role_abis[role] = abis

    if role_groups["titan"] and len(role_images["titan"]) != 1:
        errors.append("TITAN group must map to exactly one boot image")
    for role in ("atlas", "nano"):
        if role_groups[role] and len(role_images[role]) != 1:
            errors.append(
                f"all {role.upper()} groups must share exactly one boot image"
            )
        if role_groups[role] and len(role_isas[role]) != 1:
            errors.append(f"all {role.upper()} groups must share one compatible ISA")
        if role_groups[role] and len(role_abis[role]) != 1:
            errors.append(f"all {role.upper()} groups must share one compatible ABI")

    selected_images = {
        next(iter(role_images[role]))
        for role in ROLE_VALUES
        if len(role_images[role]) == 1
    }
    if all(len(role_images[role]) == 1 for role in ROLE_VALUES) and len(
        selected_images
    ) != 3:
        errors.append("TITAN, ATLAS, and NANO must use three distinct boot images")

    harts = boot_manifest.get("harts", [])
    if not isinstance(harts, list):
        errors.append("boot manifest harts entry is malformed")
        harts = []
    titan_harts = [hart for hart in harts if hart.get("role") == "titan"]
    if len(titan_harts) != 1:
        errors.append(
            "production demo requires exactly one TITAN hart; "
            f"found {len(titan_harts)}"
        )

    unsupported_berkeley = {"rocket", "boom"}
    for hart in harts:
        hart_id = hart.get("hart_id", "?")
        role = hart.get("role")
        ip = hart.get("ip")
        isa = hart.get("isa", "")
        abi = hart.get("abi")
        xlen = hart.get("xlen")
        group = hart.get("group_index")
        image = hart.get("image_id")

        if role not in ROLE_VALUES:
            errors.append(f"hart {hart_id} has unsupported role {role!r}")
            continue
        if group not in role_groups[role]:
            errors.append(
                f"hart {hart_id} group {group!r} disagrees with generated role lists"
            )
        if group in role_groups[role]:
            prefix = f"MOSAIC_GROUP_{group}"
            expected_isa = isa_make.get(f"{prefix}_MARCH")
            expected_abi = isa_make.get(f"{prefix}_MABI")
            expected_image = isa_make.get(f"{prefix}_IMAGE")
            if isa != expected_isa or abi != expected_abi:
                errors.append(
                    f"hart {hart_id} ISA/ABI disagrees with mosaic_isa.mk group {group}"
                )
            if expected_image is not None and str(image) != expected_image:
                errors.append(
                    f"hart {hart_id} boot image disagrees with mosaic_isa.mk group {group}"
                )

        if ip in unsupported_berkeley:
            errors.append(
                f"hart {hart_id} uses {ip}, whose demo firmware requires the "
                "TileLink uncached-window worker program"
            )
        if xlen != 32 or not isinstance(isa, str) or not isa.startswith("rv32"):
            errors.append(
                f"hart {hart_id} uses {isa!r}; production demo supports RV32 only"
            )
        if role != "titan" and (
            not isinstance(isa, str) or not isa.startswith("rv32i") or abi != "ilp32"
        ):
            errors.append(
                f"{role.upper()} hart {hart_id} uses {isa}/{abi}; worker images "
                "require a full RV32I register file with ilp32 ABI"
            )

    manifest_images = boot_manifest.get("images", [])
    try:
        make_image_count = int(isa_make.get("MOSAIC_NUM_IMAGES", ""), 10)
    except ValueError:
        make_image_count = -1
        errors.append("MOSAIC_NUM_IMAGES is missing or malformed")
    if isinstance(manifest_images, list) and make_image_count >= 0:
        if len(manifest_images) != make_image_count:
            errors.append("boot_images.json and mosaic_isa.mk image counts disagree")
        manifest_image_ids = {image.get("image_id") for image in manifest_images}
        if selected_images and not selected_images.issubset(manifest_image_ids):
            errors.append("role boot-image mapping references an absent image")

    # Keep diagnostics stable and concise when one root cause is observed by
    # both the manifest and make-fragment consistency checks.
    return list(dict.fromkeys(errors))


def validate_production_demo_files(
    boot_manifest_path: str | Path, isa_makefile_path: str | Path
) -> None:
    """Raise with an actionable diagnostic if the fixed demo is inapplicable."""

    with Path(boot_manifest_path).open() as stream:
        boot_manifest = json.load(stream)
    isa_make = _read_make_fragment(isa_makefile_path)
    errors = production_demo_contract_errors(boot_manifest, isa_make)
    if errors:
        detail = "\n  - ".join(errors)
        raise SoftwareGenerationError(
            "production sw/firmware demo is not applicable to this topology:\n"
            f"  - {detail}\n"
            "Generated BSP headers, per-image linkers, and boot_images.json "
            "remain valid; provide topology-specific application images instead."
        )


@dataclass(frozen=True)
class SoftwareArtifacts:
    """Paths produced by :func:`generate_software_artifacts`."""

    root: Path
    topology_header: Path
    memory_map_header: Path
    assembler_memory_map: Path
    isa_makefile: Path
    linker_script: Path
    boot_manifest: Path
    deployment_header: Path
    runtime_header: Path
    titan_flash_linker: Path | None
    image_linker_scripts: tuple[Path, ...]
    image_startup_sources: tuple[Path, ...]

    def as_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "root": str(self.root),
            "topology_header": str(self.topology_header),
            "memory_map_header": str(self.memory_map_header),
            "assembler_memory_map": str(self.assembler_memory_map),
            "isa_makefile": str(self.isa_makefile),
            "linker_script": str(self.linker_script),
            "boot_manifest": str(self.boot_manifest),
            "deployment_header": str(self.deployment_header),
            "runtime_header": str(self.runtime_header),
            "titan_flash_linker": (
                str(self.titan_flash_linker) if self.titan_flash_linker else None
            ),
        }
        result["image_linker_scripts"] = [
            str(path) for path in self.image_linker_scripts
        ]
        result["image_startup_sources"] = [
            str(path) for path in self.image_startup_sources
        ]
        return result


def _as_int(value: Any, *, field: str) -> int:
    try:
        parsed = value if type(value) is int else int(value, 0)
    except (TypeError, ValueError) as exc:
        raise SoftwareGenerationError(f"{field} is not an integer address: {value!r}") from exc
    if not 0 <= parsed <= 0xFFFF_FFFF:
        raise SoftwareGenerationError(f"{field} is outside the 32-bit address range")
    return parsed


def _boot_address(hart: "HartConfig") -> int:
    return _as_int(
        hart.params.get("boot_addr", DEFAULT_BOOT_ADDRESS),
        field=f"hart {hart.hart_id} boot_addr",
    )


def shared_control_base(boot_addresses: Iterable[int]) -> int:
    """Return the shared sentinel/result base used by software and TL bridges."""

    addresses = list(boot_addresses)
    if not addresses:
        raise SoftwareGenerationError("shared-control layout requires a boot address")
    if any(address < 0 or address > 0xFFFF_FFFF for address in addresses):
        raise SoftwareGenerationError("shared-control layout has an invalid boot address")
    return (max(addresses) + 0x1000 + 0xFF) & ~0xFF


def _abi_for_isa(isa: str) -> str:
    if isa.startswith("rv32e"):
        return "ilp32e"
    if isa.startswith("rv32"):
        return "ilp32"
    if isa.startswith("rv64"):
        return "lp64"
    raise SoftwareGenerationError(f"unsupported ISA spelling {isa!r}")


def _xlen_for_isa(isa: str) -> int:
    if isa.startswith("rv32"):
        return 32
    if isa.startswith("rv64"):
        return 64
    raise SoftwareGenerationError(f"unsupported ISA spelling {isa!r}")


def _capability_mask(ip: str, params: Mapping[str, Any]) -> int:
    try:
        capabilities = resolved_capabilities(ip, params)
    except KeyError as exc:
        raise SoftwareGenerationError(f"hart uses unregistered core {ip!r}") from exc
    unknown = set(capabilities) - set(CAPABILITY_BITS)
    if unknown:
        raise SoftwareGenerationError(
            f"core {ip!r} has software-unmapped capabilities: {sorted(unknown)}"
        )
    return sum(CAPABILITY_BITS[name] for name in capabilities)


def _mask_words(hart_ids: Iterable[int], num_harts: int) -> list[int]:
    words = [0] * max(1, (num_harts + 31) // 32)
    for hart_id in hart_ids:
        words[hart_id // 32] |= 1 << (hart_id % 32)
    return words


def _hex32(value: int) -> str:
    return f"0x{value:08X}"


def _macro_string(value: str) -> str:
    return json.dumps(value)


def _write_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        return
    path.write_text(text)


def _topology_header(cfg: "MosaicConfig") -> str:
    num_harts = len(cfg.harts)
    role_ids = {
        role: [hart.hart_id for hart in cfg.harts if hart.role == role]
        for role in ROLE_VALUES
    }
    role_masks = {
        role: _mask_words(harts, num_harts) for role, harts in role_ids.items()
    }
    worker_ids = [hart.hart_id for hart in cfg.harts if hart.role != "titan"]
    worker_mask = _mask_words(worker_ids, num_harts)
    all_mask = _mask_words(range(num_harts), num_harts)
    mask_words = len(all_mask)

    lines = [
        "// Generated by util/xheep_gen/software_gen.py. Do not edit.",
        "// Source of truth: resolved MosaicConfig/HartConfig.",
        "#ifndef MOSAIC_GENERATED_TOPOLOGY_H_",
        "#define MOSAIC_GENERATED_TOPOLOGY_H_",
        "",
        "#include <stdint.h>",
        "",
        f"#define MOSAIC_SOC_NAME {_macro_string(cfg.soc_name)}",
        f"#define MOSAIC_PDK {_macro_string(cfg.pdk)}",
        f"#define MOSAIC_IMPLEMENTATION_TARGET {_macro_string(cfg.target)}",
        f"#define MOSAIC_NUM_HARTS {num_harts}u",
        f"#define MOSAIC_HART_MASK_WORDS {mask_words}u",
        f"#define MOSAIC_NUM_TITAN_HARTS {len(role_ids['titan'])}u",
        f"#define MOSAIC_NUM_ATLAS_HARTS {len(role_ids['atlas'])}u",
        f"#define MOSAIC_NUM_NANO_HARTS {len(role_ids['nano'])}u",
        f"#define MOSAIC_NUM_WORKER_HARTS {len(worker_ids)}u",
        f"#define MOSAIC_TDU_ENABLED {1 if cfg.scheduler.tdu else 0}u",
        "#define MOSAIC_SCHED_STATIC 0u",
        "#define MOSAIC_SCHED_DYNAMIC 1u",
        "#define MOSAIC_SCHED_POWER_AWARE 2u",
        f"#define MOSAIC_SCHED_MODE {SCHED_VALUES[cfg.scheduler.mode]}u",
        "",
        "#define MOSAIC_ROLE_TITAN 0u",
        "#define MOSAIC_ROLE_ATLAS 1u",
        "#define MOSAIC_ROLE_NANO 2u",
        "",
    ]

    for name, bit in CAPABILITY_BITS.items():
        lines.append(f"#define MOSAIC_CAP_{name.upper()} {_hex32(bit)}u")
    lines.append("")

    mask_sets = {
        "ALL": all_mask,
        "TITAN": role_masks["titan"],
        "ATLAS": role_masks["atlas"],
        "NANO": role_masks["nano"],
        "WORKER": worker_mask,
    }
    for name, words in mask_sets.items():
        for index, word in enumerate(words):
            lines.append(f"#define MOSAIC_{name}_HART_MASK_WORD_{index} {_hex32(word)}u")
        # TDU and current firmware masks are 32-bit.  The full topology remains
        # available through *_MASK_WORD_n for generators with more than 32 harts.
        lines.append(f"#define MOSAIC_{name}_HART_MASK MOSAIC_{name}_HART_MASK_WORD_0")
    lines.append("")

    for role in ROLE_VALUES:
        first = role_ids[role][0] if role_ids[role] else num_harts
        lines.append(f"#define MOSAIC_FIRST_{role.upper()}_HART {first}u")
    lines.append("")

    for hart in cfg.harts:
        prefix = f"MOSAIC_HART_{hart.hart_id}"
        lines.extend(
            [
                f"#define {prefix}_ID {hart.hart_id}u",
                f"#define {prefix}_IP {_macro_string(hart.ip)}",
                f"#define {prefix}_ISA {_macro_string(hart.isa)}",
                f"#define {prefix}_ABI {_macro_string(_abi_for_isa(hart.isa))}",
                f"#define {prefix}_XLEN {_xlen_for_isa(hart.isa)}u",
                f"#define {prefix}_ROLE MOSAIC_ROLE_{hart.role.upper()}",
                f"#define {prefix}_BOOT_ADDR {_hex32(_boot_address(hart))}u",
                f"#define {prefix}_CAPABILITIES {_hex32(_capability_mask(hart.ip, hart.params))}u",
                "",
            ]
        )

    lines.extend(
        [
            "#ifndef __ASSEMBLER__",
            "typedef struct mosaic_hart_config {",
            "    uint32_t hart_id;",
            "    uint32_t role;",
            "    uint32_t boot_address;",
            "    uint32_t capabilities;",
            "    uint32_t xlen;",
            "    const char *ip;",
            "    const char *isa;",
            "    const char *abi;",
            "} mosaic_hart_config_t;",
            "",
            "static const mosaic_hart_config_t mosaic_hart_config[MOSAIC_NUM_HARTS] = {",
        ]
    )
    for hart in cfg.harts:
        prefix = f"MOSAIC_HART_{hart.hart_id}"
        lines.append(
            "    { "
            f"{prefix}_ID, {prefix}_ROLE, {prefix}_BOOT_ADDR, "
            f"{prefix}_CAPABILITIES, {prefix}_XLEN, {prefix}_IP, "
            f"{prefix}_ISA, {prefix}_ABI "
            "},"
        )
    lines.extend(
        [
            "};",
            "#endif  // __ASSEMBLER__",
            "",
            "#endif  // MOSAIC_GENERATED_TOPOLOGY_H_",
            "",
        ]
    )
    return "\n".join(lines)


def _memory_map_header(cfg: "MosaicConfig", shared_base: int, shared_size: int) -> str:
    sram_size = _writable_bytes(cfg)
    selected = expanded_user_peripherals(
        cfg.peripherals,
        multicore=cfg.total_cores > 1,
        plic=cfg.plic,
        multicore_timer=getattr(cfg, "multicore_timer", True),
    )
    lines = [
        "// Generated by util/xheep_gen/software_gen.py. Do not edit.",
        "#ifndef MOSAIC_GENERATED_MEMORY_MAP_H_",
        "#define MOSAIC_GENERATED_MEMORY_MAP_H_",
        "",
        f"#define MOSAIC_SRAM_BASE {_hex32(SRAM_BASE)}u",
        f"#define MOSAIC_SRAM_SIZE {_hex32(sram_size)}u",
        f"#define MOSAIC_SRAM_END {_hex32(SRAM_BASE + sram_size)}u",
        f"#define MOSAIC_DEBUG_BASE {_hex32(DEBUG_BASE)}u",
        f"#define MOSAIC_DEBUG_SIZE {_hex32(DEBUG_SIZE)}u",
        f"#define MOSAIC_AO_PERIPHERAL_BASE {_hex32(AO_PERIPHERAL_BASE)}u",
        f"#define MOSAIC_AO_PERIPHERAL_SIZE {_hex32(AO_PERIPHERAL_SIZE)}u",
        f"#define MOSAIC_USER_PERIPHERAL_BASE {_hex32(USER_PERIPHERAL_BASE)}u",
        f"#define MOSAIC_USER_PERIPHERAL_SIZE {_hex32(USER_PERIPHERAL_SIZE)}u",
        f"#define MOSAIC_FLASH_BASE {_hex32(FLASH_BASE)}u",
        f"#define MOSAIC_FLASH_SIZE {_hex32(FLASH_SIZE)}u",
        f"#define MOSAIC_EXT_SLAVE_BASE {_hex32(EXT_SLAVE_BASE)}u",
        f"#define MOSAIC_EXT_SLAVE_SIZE {_hex32(EXT_SLAVE_SIZE)}u",
        "",
        f"#define MOSAIC_SOC_CTRL_BASE {_hex32(AO_PERIPHERAL_BASE + SOC_CTRL_OFFSET)}u",
        "#define MOSAIC_SOC_CTRL_EXIT_VALID_OFFSET 0x00000000u",
        "#define MOSAIC_SOC_CTRL_EXIT_VALUE_OFFSET 0x00000004u",
        f"#define MOSAIC_BOOT_ROM_BASE {_hex32(AO_PERIPHERAL_BASE + BOOT_ROM_OFFSET)}u",
        f"#define MOSAIC_BOOT_ROM_SIZE {_hex32(cfg.memory.boot_rom_kb * 1024)}u",
        f"#define MOSAIC_HAS_TDU {1 if cfg.scheduler.tdu else 0}u",
        f"#define MOSAIC_TDU_BASE {_hex32(AO_PERIPHERAL_BASE + TDU_OFFSET)}u",
        f"#define MOSAIC_TDU_SIZE {_hex32(TDU_SIZE)}u",
        "#define MOSAIC_TDU_TASK_QUEUE_DEPTH 8u",
        "#define MOSAIC_TDU_CORE_STATUS_OFFSET 0x00000000u",
        "#define MOSAIC_TDU_SCHED_MODE_OFFSET 0x00000004u",
        "#define MOSAIC_TDU_WAKE_MASK_OFFSET 0x00000008u",
        "#define MOSAIC_TDU_WAKE_REQ_OFFSET 0x0000000Cu",
        "#define MOSAIC_TDU_TASK_PUSH_OFFSET 0x00000010u",
        "#define MOSAIC_TDU_TASK_POP_OFFSET 0x00000014u",
        "#define MOSAIC_TDU_TASK_STATUS_OFFSET 0x00000018u",
        "#define MOSAIC_TDU_ACTIVE_HART_CYCLES_OFFSET 0x0000001Cu",
        "#define MOSAIC_TDU_CPI_EST_BASE_OFFSET 0x00000020u",
        "#define MOSAIC_TDU_PARK_REQ_OFFSET 0x00000060u",
        # Every MosaicConfig uses the explicit-topology RTL path, including a
        # singleton. That path always instantiates the per-hart CLINT.
        "#define MOSAIC_HAS_CLINT 1u",
        f"#define MOSAIC_CLINT_BASE {_hex32(AO_PERIPHERAL_BASE + CLINT_OFFSET)}u",
        f"#define MOSAIC_CLINT_SIZE {_hex32(CLINT_SIZE)}u",
        "#define MOSAIC_CLINT_MSIP_OFFSET(hart) (0x00000000u + 4u * (hart))",
        "#define MOSAIC_CLINT_MTIMECMP_OFFSET(hart) (0x00004000u + 8u * (hart))",
        "#define MOSAIC_CLINT_MTIME_LO_OFFSET 0x0000BFF8u",
        "#define MOSAIC_CLINT_MTIME_HI_OFFSET 0x0000BFFCu",
        "",
        f"#define MOSAIC_SHARED_CONTROL_BASE {_hex32(shared_base)}u",
        f"#define MOSAIC_SHARED_CONTROL_SIZE {_hex32(shared_size)}u",
        "#define MOSAIC_SENTINEL_BASE MOSAIC_SHARED_CONTROL_BASE",
        # Results occupy the upper half of the shared window. The offset was a
        # hardcoded 0x100, which is exactly half of the 0x200 window every
        # on-chip config produces -- but the minimal-scratchpad profile has a
        # 0x40 window, where a fixed 0x100 would place results PAST the end of
        # a 512 B scratchpad. Clamping to the midpoint keeps on-chip byte
        # identical and keeps the scratchpad in bounds.
        f"#define MOSAIC_RESULT_BASE (MOSAIC_SHARED_CONTROL_BASE + "
        f"{_hex32(_result_offset(shared_size))}u)",
        "",
    ]
    for concrete, (macro, offset, size) in USER_PERIPHERALS.items():
        included = concrete in selected
        lines.append(f"#define MOSAIC_HAS_{macro} {1 if included else 0}u")
        lines.append(
            f"#define MOSAIC_{macro}_BASE {_hex32(USER_PERIPHERAL_BASE + offset)}u"
        )
        lines.append(f"#define MOSAIC_{macro}_SIZE {_hex32(size)}u")
        if concrete == "rv_plic":
            # The OpenTitan PLIC template allocates one 0x100-byte register
            # context per generated target, starting at offset 0x200.  The
            # checked-in x-heep HAL names only target zero; these formulas are
            # the topology-sized BSP contract for every hart context.
            lines.extend(
                [
                    f"#define MOSAIC_PLIC_NUM_TARGETS {cfg.total_cores}u",
                    "#define MOSAIC_PLIC_NUM_SOURCES 64u",
                    "#define MOSAIC_PLIC_TARGET_BASE_OFFSET 0x00000200u",
                    "#define MOSAIC_PLIC_TARGET_STRIDE 0x00000100u",
                    "#define MOSAIC_PLIC_IE0_OFFSET(hart) "
                    "(MOSAIC_PLIC_TARGET_BASE_OFFSET + "
                    "MOSAIC_PLIC_TARGET_STRIDE * (hart))",
                    "#define MOSAIC_PLIC_IE1_OFFSET(hart) "
                    "(MOSAIC_PLIC_IE0_OFFSET(hart) + 0x4u)",
                    "#define MOSAIC_PLIC_THRESHOLD_OFFSET(hart) "
                    "(MOSAIC_PLIC_IE0_OFFSET(hart) + 0x8u)",
                    "#define MOSAIC_PLIC_CLAIM_COMPLETE_OFFSET(hart) "
                    "(MOSAIC_PLIC_IE0_OFFSET(hart) + 0xCu)",
                    "#define MOSAIC_PLIC_MSIP_OFFSET(hart) "
                    "(MOSAIC_PLIC_IE0_OFFSET(hart) + 0x10u)",
                ]
            )
    lines.extend(["", "#endif  // MOSAIC_GENERATED_MEMORY_MAP_H_", ""])
    return "\n".join(lines)


def _assembler_memory_map(cfg: "MosaicConfig", shared_base: int, shared_size: int) -> str:
    """Preprocessor include for ``.S`` sources (no C integer suffixes)."""

    worker_mask = sum(
        1 << hart.hart_id for hart in cfg.harts if hart.role != "titan"
    )
    return "\n".join(
        [
            "// Generated by util/xheep_gen/software_gen.py. Do not edit.",
            "#ifndef MOSAIC_GENERATED_MEMORY_MAP_INC_",
            "#define MOSAIC_GENERATED_MEMORY_MAP_INC_",
            f"#define MOSAIC_ASM_NUM_HARTS {cfg.total_cores}",
            f"#define MOSAIC_ASM_WORKER_HART_MASK {_hex32(worker_mask)}",
            f"#define MOSAIC_ASM_SRAM_BASE {_hex32(SRAM_BASE)}",
            f"#define MOSAIC_ASM_SRAM_SIZE {_hex32(_writable_bytes(cfg))}",
            f"#define MOSAIC_ASM_SOC_CTRL_BASE {_hex32(AO_PERIPHERAL_BASE + SOC_CTRL_OFFSET)}",
            f"#define MOSAIC_ASM_TDU_BASE {_hex32(AO_PERIPHERAL_BASE + TDU_OFFSET)}",
            "#define MOSAIC_ASM_TDU_WAKE_REQ "
            f"{_hex32(AO_PERIPHERAL_BASE + TDU_OFFSET + 0x0C)}",
            "#define MOSAIC_ASM_TDU_TASK_POP "
            f"{_hex32(AO_PERIPHERAL_BASE + TDU_OFFSET + 0x14)}",
            "#define MOSAIC_ASM_TDU_PARK_REQ "
            f"{_hex32(AO_PERIPHERAL_BASE + TDU_OFFSET + 0x60)}",
            f"#define MOSAIC_ASM_CLINT_BASE {_hex32(AO_PERIPHERAL_BASE + CLINT_OFFSET)}",
            "#define MOSAIC_ASM_CLINT_MSIP(hart) "
            f"({_hex32(AO_PERIPHERAL_BASE + CLINT_OFFSET)} + 4 * (hart))",
            "#define MOSAIC_ASM_CLINT_MTIMECMP(hart) "
            f"({_hex32(AO_PERIPHERAL_BASE + CLINT_OFFSET + 0x4000)} + 8 * (hart))",
            f"#define MOSAIC_ASM_SENTINEL_BASE {_hex32(shared_base)}",
            f"#define MOSAIC_ASM_RESULT_BASE "
            f"{_hex32(shared_base + _result_offset(shared_size))}",
            "// Uncached TileLink windows used by Rocket/BOOM SCI wrappers.",
            "#define MOSAIC_ASM_TL_SENTINEL_BASE 0x02000000",
            "#define MOSAIC_ASM_TL_SOC_CTRL_BASE 0x02001000",
            "#define MOSAIC_ASM_TL_TDU_BASE 0x0C000000",
            "#define MOSAIC_ASM_TL_TDU_WAKE_REQ 0x0C00000C",
            "#define MOSAIC_ASM_TL_TDU_TASK_POP 0x0C000014",
            "#define MOSAIC_ASM_TL_TDU_PARK_REQ 0x0C000060",
            "#endif  // MOSAIC_GENERATED_MEMORY_MAP_INC_",
            "",
        ]
    )


def _isa_makefile(cfg: "MosaicConfig", images: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Generated by util/xheep_gen/software_gen.py. Do not edit.",
        f"MOSAIC_SOC_NAME := {cfg.soc_name}",
        f"MOSAIC_NUM_HARTS := {cfg.total_cores}",
        f"MOSAIC_NUM_GROUPS := {len(cfg.cpu_groups)}",
        f"MOSAIC_NUM_IMAGES := {len(images)}",
        "MOSAIC_TITAN_GROUPS := "
        + " ".join(
            str(index)
            for index, group in enumerate(cfg.cpu_groups)
            if group.role == "titan"
        ),
        "MOSAIC_ATLAS_GROUPS := "
        + " ".join(
            str(index)
            for index, group in enumerate(cfg.cpu_groups)
            if group.role == "atlas"
        ),
        "MOSAIC_NANO_GROUPS := "
        + " ".join(
            str(index)
            for index, group in enumerate(cfg.cpu_groups)
            if group.role == "nano"
        ),
        "",
    ]
    for index, group in enumerate(cfg.cpu_groups):
        harts = list(range(group.hart_id_base, group.hart_id_base + group.count))
        image_id = next(
            image["image_id"]
            for image in images
            if group.hart_id_base in image["harts"]
        )
        lines.extend(
            [
                f"MOSAIC_GROUP_{index}_IP := {group.ip}",
                f"MOSAIC_GROUP_{index}_ROLE := {group.role}",
                f"MOSAIC_GROUP_{index}_HARTS := {' '.join(str(h) for h in harts)}",
                f"MOSAIC_GROUP_{index}_MARCH := {group.isa}",
                f"MOSAIC_GROUP_{index}_MABI := {_abi_for_isa(group.isa)}",
                f"MOSAIC_GROUP_{index}_BOOT_ADDR := "
                f"{_hex32(_boot_address(cfg.harts[group.hart_id_base]))}",
                f"MOSAIC_GROUP_{index}_IMAGE := {image_id}",
                f"MOSAIC_GROUP_{index}_CFLAGS := "
                f"-march=$(MOSAIC_GROUP_{index}_MARCH) "
                f"-mabi=$(MOSAIC_GROUP_{index}_MABI)",
                "",
            ]
        )
    for hart in cfg.harts:
        image_id = next(
            image["image_id"] for image in images if hart.hart_id in image["harts"]
        )
        lines.extend(
            [
                f"MOSAIC_HART_{hart.hart_id}_IP := {hart.ip}",
                f"MOSAIC_HART_{hart.hart_id}_ROLE := {hart.role}",
                f"MOSAIC_HART_{hart.hart_id}_MARCH := {hart.isa}",
                f"MOSAIC_HART_{hart.hart_id}_MABI := {_abi_for_isa(hart.isa)}",
                f"MOSAIC_HART_{hart.hart_id}_BOOT_ADDR := {_hex32(_boot_address(hart))}",
                f"MOSAIC_HART_{hart.hart_id}_IMAGE := {image_id}",
                "",
            ]
        )
    for image in images:
        image_id = image["image_id"]
        lines.extend(
            [
                f"MOSAIC_IMAGE_{image_id}_HARTS := {' '.join(str(h) for h in image['harts'])}",
                f"MOSAIC_IMAGE_{image_id}_BOOT_ADDR := {image['load_address']}",
                f"MOSAIC_IMAGE_{image_id}_SECTION := {image['section']}",
                f"MOSAIC_IMAGE_{image_id}_LINKER := linker/image_{image_id}.ld",
                "",
            ]
        )
    return "\n".join(lines)


def _layout_scratchpad(
    cfg: "MosaicConfig", scratch_bytes: int
) -> tuple[list[Dict[str, Any]], int, int, int]:
    """Image layout for the minimal-scratchpad profile (Option C).

    Code executes in place from the SPI-flash window, so images are placed by
    FLASH address and never staged anywhere. The on-chip scratchpad carries
    only writable data: the shared control/sentinel window at its top, and
    stack/.data/.bss below it.

    The TITAN has no explicit boot_addr -- a production TITAN must reset into
    the boot ROM -- so its image goes at the flash entry point the boot ROM
    already jumps to: FLASH_BASE + 0x180, the same TITAN_OFFSET pack_flash.py
    uses.
    """
    flash_end = FLASH_BASE + FLASH_SIZE

    by_address: Dict[int, list["HartConfig"]] = {}
    for hart in cfg.harts:
        address = _boot_address(hart)
        # A TITAN reports the platform default (0x180); read it as an offset
        # into the flash window, which is where its image actually is.
        if address < FLASH_BASE:
            address = FLASH_BASE + address
        if address < FLASH_BASE or address >= flash_end or address & 0x3:
            raise SoftwareGenerationError(
                f"hart {hart.hart_id} boot address {_hex32(address)} is outside/"
                f"unaligned against the flash XIP window "
                f"[{_hex32(FLASH_BASE)}, {_hex32(flash_end)})"
            )
        by_address.setdefault(address, []).append(hart)

    addresses = sorted(by_address)
    if not addresses:
        raise SoftwareGenerationError("software generation requires at least one hart")

    # Shared control lives at the TOP of the scratchpad; everything below it is
    # stack and initialised data. 64-byte granularity rather than the on-chip
    # 256-byte convention, because the whole scratchpad may be only 512 B.
    shared_size = max(0x40, ((cfg.total_cores * 8 + 0x3F) // 0x40) * 0x40)
    if shared_size >= scratch_bytes:
        raise SoftwareGenerationError(
            f"scratchpad of {scratch_bytes} B cannot hold the shared-control "
            f"window for {cfg.total_cores} harts ({shared_size} B) and leave "
            "room for a stack; use a larger memory.scratchpad_bytes"
        )
    shared_base = scratch_bytes - shared_size
    data_base = SRAM_BASE

    images: list[Dict[str, Any]] = []
    for image_id, address in enumerate(addresses):
        harts = by_address[address]
        end = addresses[image_id + 1] if image_id + 1 < len(addresses) else flash_end
        if end <= address:
            raise SoftwareGenerationError(f"empty boot image region at {_hex32(address)}")
        roles = sorted({hart.role for hart in harts}, key=ROLE_VALUES.get)
        images.append(
            {
                "image_id": image_id,
                "name": f"image_{image_id}",
                "path": f"images/image_{image_id}.bin",
                "elf": f"images/image_{image_id}.elf",
                "linker_script": f"linker/image_{image_id}.ld",
                "section": f".mosaic.image.{image_id}",
                "load_address": _hex32(address),
                "end_address": _hex32(end),
                "max_size": end - address,
                "harts": [hart.hart_id for hart in harts],
                "roles": roles,
                "isas": sorted({hart.isa for hart in harts}),
                "abis": sorted({_abi_for_isa(hart.isa) for hart in harts}),
                "xlens": sorted({_xlen_for_isa(hart.isa) for hart in harts}),
                "shared": len(harts) > 1,
                # Code is resident in flash, not copied anywhere.
                "execute_in_place": True,
            }
        )
    return images, shared_base, shared_size, data_base


def _layout(cfg: "MosaicConfig") -> tuple[list[Dict[str, Any]], int, int, int]:
    # The external-memory profile has no on-chip SRAM to lay images out in.
    # Hardware generation already supports it (zero RAM banks), but the software
    # side needs a boot-sequence design first: external RAM is UNINITIALISED at
    # reset, so worker images cannot simply be placed there the way SRAM images
    # are pre-loaded — something must copy them from flash into external RAM,
    # and the external memory controller must be brought up before any hart is
    # woken. Fail with that explanation rather than a confusing empty-SRAM
    # window. See docs/area_study_gf180_min_soc.md §6.2 and §8.5.
    scratchpad_bytes = getattr(cfg.memory, "scratchpad_bytes", None)
    if (
        cfg.memory.sram_kb == 0
        and not cfg.memory.external
        and not scratchpad_bytes
    ):
        raise SoftwareGenerationError(
            "XIP-only profile (memory.sram_kb: 0 with no memory.external) is not "
            "supported by software generation. RTL generation works and the "
            "images could be placed in the flash window, but this profile has "
            "NO WRITABLE MEMORY AT ALL: no stack, so no C runtime for any hart "
            "including the TITAN, and no shared-control window for the TDU "
            "liveness protocol, which currently writes per-hart sentinels to "
            "SRAM. Both would have to be replaced (assembly-only firmware; "
            "liveness via a peripheral register such as the TDU's per-hart "
            "CPI_EST array) before this profile can run anything. Prefer the "
            "minimal-scratchpad profile. See docs/external_memory_boot_design.md."
        )
    if cfg.memory.sram_kb == 0 and scratchpad_bytes:
        return _layout_scratchpad(cfg, int(scratchpad_bytes))

    if cfg.memory.sram_kb == 0:
        raise SoftwareGenerationError(
            "external-memory profile (memory.sram_kb: 0 with memory.external) is "
            "not yet supported by software generation. RTL generation works, and "
            "under Option C worker CODE needs no staging because it XIPs from "
            "flash — but the linker scripts must place .text in the flash window "
            "with .data/.bss/stack in the external region, and the memory "
            "controller must be brought up before any hart is woken. "
            "See docs/external_memory_boot_design.md."
        )

    sram_end = SRAM_BASE + _writable_bytes(cfg)
    by_address: Dict[int, list["HartConfig"]] = {}
    for hart in cfg.harts:
        address = _boot_address(hart)
        if address < SRAM_BASE or address >= sram_end or address & 0x3:
            raise SoftwareGenerationError(
                f"hart {hart.hart_id} boot address {_hex32(address)} is outside/aligned "
                f"against SRAM [{_hex32(SRAM_BASE)}, {_hex32(sram_end)})"
            )
        by_address.setdefault(address, []).append(hart)

    addresses = sorted(by_address)
    if not addresses:
        raise SoftwareGenerationError("software generation requires at least one hart")

    # Keep at least 4 KiB for the last program, then reserve a small uncached
    # shared control/result window.  This reproduces the historical 0x3000 and
    # 0x3200 boundaries for the PoC while scaling down for small SRAM configs.
    shared_base = shared_control_base(addresses)
    shared_size = max(0x200, ((cfg.total_cores * 8 + 0xFF) // 0x100) * 0x100)
    data_base = shared_base + shared_size
    if data_base + 0x400 > sram_end:
        raise SoftwareGenerationError(
            "boot images plus shared-control and minimum stack do not fit SRAM: "
            f"need through {_hex32(data_base + 0x400)}, SRAM ends at {_hex32(sram_end)}"
        )

    images: list[Dict[str, Any]] = []
    for image_id, address in enumerate(addresses):
        harts = by_address[address]
        end = addresses[image_id + 1] if image_id + 1 < len(addresses) else shared_base
        if end <= address:
            raise SoftwareGenerationError(f"empty boot image region at {_hex32(address)}")
        roles = sorted({hart.role for hart in harts}, key=ROLE_VALUES.get)
        isas = sorted({hart.isa for hart in harts})
        abis = sorted({_abi_for_isa(hart.isa) for hart in harts})
        xlens = sorted({_xlen_for_isa(hart.isa) for hart in harts})
        images.append(
            {
                "image_id": image_id,
                "name": f"image_{image_id}",
                "path": f"images/image_{image_id}.bin",
                "elf": f"images/image_{image_id}.elf",
                "linker_script": f"linker/image_{image_id}.ld",
                "section": f".mosaic.image.{image_id}",
                "load_address": _hex32(address),
                "end_address": _hex32(end),
                "max_size": end - address,
                "harts": [hart.hart_id for hart in harts],
                "roles": roles,
                "isas": isas,
                "abis": abis,
                "xlens": xlens,
                "shared": len(harts) > 1,
            }
        )
    return images, shared_base, shared_size, data_base


def _compatibility_linker(
    cfg: "MosaicConfig",
    images: Sequence[Mapping[str, Any]],
    shared_base: int,
    shared_size: int,
    data_base: int,
) -> str:
    sram_end = SRAM_BASE + _writable_bytes(cfg)
    primary_xlen = _xlen_for_isa(cfg.harts[0].isa)
    fmt = "elf32-littleriscv" if primary_xlen == 32 else "elf64-littleriscv"
    lines = [
        "/* Generated by util/xheep_gen/software_gen.py. Do not edit. */",
        f'OUTPUT_FORMAT("{fmt}", "{fmt}", "{fmt}")',
        "OUTPUT_ARCH(riscv)",
        "ENTRY(_start)",
        "",
        "MEMORY",
        "{",
    ]
    for image in images:
        start = int(image["load_address"], 0)
        lines.append(
            f"    image_{image['image_id']}_rx (rx) : ORIGIN = {_hex32(start)}, "
            f"LENGTH = {_hex32(image['max_size'])}"
        )
    lines.extend(
        [
            f"    shared_rw (rw) : ORIGIN = {_hex32(shared_base)}, LENGTH = {_hex32(shared_size)}",
            f"    data_rw (rwx) : ORIGIN = {_hex32(data_base)}, "
            f"LENGTH = {_hex32(sram_end - data_base)}",
            "}",
            "",
            "__stack_size = 0x400;",
            "",
            "SECTIONS",
            "{",
        ]
    )

    first_role_image: Dict[str, int] = {}
    for image in images:
        for role in image["roles"]:
            first_role_image.setdefault(role, image["image_id"])

    for image in images:
        image_id = image["image_id"]
        lines.append(f"    .mosaic_image_{image_id} : {{")
        if 0 in image["harts"]:
            lines.extend(
                [
                    "        _start = .;",
                    "        KEEP(*(.text.start))",
                    "        KEEP(*(.text.init))",
                    "        *(.text.startup*)",
                    "        *(.text*)",
                    "        *(.rodata*)",
                    "        *(.data*)",
                ]
            )
        if first_role_image.get("atlas") == image_id:
            lines.append("        *(.atlas)")
        if first_role_image.get("nano") == image_id:
            lines.append("        *(.nano)")
        lines.append(f"        *({image['section']}*)")
        lines.append(f"    }} > image_{image_id}_rx")
        lines.append("")

    lines.extend(
        [
            "    .mosaic_shared (NOLOAD) : {",
            "        __mosaic_shared_start = .;",
            "        . += LENGTH(shared_rw);",
            "        __mosaic_shared_end = .;",
            "    } > shared_rw",
            "",
            "    .sdata : { *(.sdata*) } > data_rw",
            "    .bss (NOLOAD) : {",
            "        __bss_start = .;",
            "        *(.sbss*)",
            "        *(.bss*)",
            "        *(COMMON)",
            "        __bss_end = .;",
            "    } > data_rw",
            "    .stack (NOLOAD) : ALIGN(16) {",
            "        . += __stack_size;",
            "        _sp = .;",
            "    } > data_rw",
            "    _end = .;",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def _image_linker(
    image: Mapping[str, Any], *, sram_end: int, data_base: int
) -> str:
    if len(image["xlens"]) == 1:
        xlen = image["xlens"][0]
    else:
        # A shared assembly image can span RV32/RV64 only when deliberately
        # written for both.  Use the smallest ELF class and retain both XLENs
        # in the manifest so the image builder must make that choice explicit.
        xlen = min(image["xlens"])
    fmt = "elf32-littleriscv" if xlen == 32 else "elf64-littleriscv"
    start = int(image["load_address"], 0)
    primary = 0 in image["harts"]
    stack_stride = 0x400 if primary else 0x100
    # The default strides assume a multi-KiB SRAM. The minimal-scratchpad
    # profile has a few hundred bytes total, so .hart_stacks (stride x harts)
    # would overflow data_rw. Cap the stride to what the writable region can
    # actually hold, keeping 16-byte alignment. On-chip configs have far more
    # room than the default stride needs, so this never binds for them.
    writable_bytes = sram_end - data_base
    hart_count = max(1, len(image["harts"]))
    affordable = max(0x20, (writable_bytes // hart_count) & ~0xF)
    if affordable < stack_stride:
        stack_stride = affordable
    lines = [
        "/* Generated by util/xheep_gen/software_gen.py. Do not edit. */",
        f'OUTPUT_FORMAT("{fmt}", "{fmt}", "{fmt}")',
        "OUTPUT_ARCH(riscv)",
        "ENTRY(_start)",
        "MEMORY",
        "{",
        f"    program_rwx (rwx) : ORIGIN = {_hex32(start)}, "
        f"LENGTH = {_hex32(image['max_size'])}",
    ]
    if primary:
        lines.append(
            f"    data_rw (rwx) : ORIGIN = {_hex32(data_base)}, "
            f"LENGTH = {_hex32(sram_end - data_base)}"
        )
    lines.extend(
        [
            "}",
            "SECTIONS",
            "{",
            "    .text : {",
            "        _start = .;",
            "        KEEP(*(.text.start))",
            "        KEEP(*(.text.init))",
            "        *(.text*)",
            "        *(.rodata*)",
        ]
    )
    if "atlas" in image["roles"]:
        lines.append("        *(.atlas)")
    if "nano" in image["roles"]:
        lines.append("        *(.nano)")
    lines.extend(
        [
            f"        *({image['section']}*)",
            "    } > program_rwx",
        ]
    )
    data_region = "data_rw" if primary else "program_rwx"
    lines.extend(
        [
            f"    .data : {{ *(.sdata*) *(.data*) }} > {data_region}",
            "    .bss (NOLOAD) : {",
            "        . = ALIGN(4);",
            "        __bss_start = .;",
            "        *(.sbss*) *(.bss*) *(COMMON)",
            "        . = ALIGN(4);",
            "        __bss_end = .;",
            f"    }} > {data_region}",
            f"    __mosaic_image_hart_count = {len(image['harts'])};",
            f"    __mosaic_stack_stride = {_hex32(stack_stride)};",
        ]
    )
    for ordinal, hart_id in enumerate(image["harts"]):
        lines.append(f"    __mosaic_image_hart_{ordinal} = {hart_id};")
    lines.extend(
        [
            "    .hart_stacks (NOLOAD) : ALIGN(16) {",
            "        __mosaic_stack_base = .;",
            f"        . += {_hex32(stack_stride * len(image['harts']))};",
            "        __mosaic_stack_end = .;",
            f"    }} > {data_region}",
        ]
    )
    for ordinal, _hart_id in enumerate(image["harts"]):
        lines.append(
            f"    __mosaic_stack_top_{ordinal} = __mosaic_stack_base + "
            f"{_hex32(stack_stride * (ordinal + 1))};"
        )
    if primary:
        lines.append("    _sp = __mosaic_stack_top_0;")
    lines.extend(["    _end = .;", "}", ""])
    return "\n".join(lines)


def _runtime_header(images: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "// Generated by util/xheep_gen/software_gen.py. Do not edit.",
        "#ifndef MOSAIC_GENERATED_RUNTIME_H_",
        "#define MOSAIC_GENERATED_RUNTIME_H_",
        "",
        "#include <stdint.h>",
        "",
        f"#define MOSAIC_RUNTIME_NUM_IMAGES {len(images)}u",
    ]
    for image in images:
        image_id = image["image_id"]
        stride = 0x400 if 0 in image["harts"] else 0x100
        lines.extend(
            [
                f"#define MOSAIC_IMAGE_{image_id}_HART_COUNT {len(image['harts'])}u",
                f"#define MOSAIC_IMAGE_{image_id}_STACK_STRIDE {_hex32(stride)}u",
                f"#define MOSAIC_IMAGE_{image_id}_INIT_HART {image['harts'][0]}u",
                f"#define MOSAIC_IMAGE_{image_id}_HAS_GENERATED_CRT0 "
                f"{int(bool(image.get('startup_source')))}u",
            ]
        )
        for ordinal, hart_id in enumerate(image["harts"]):
            lines.append(f"#define MOSAIC_IMAGE_{image_id}_HART_{ordinal} {hart_id}u")
    lines.extend(
        [
            "",
            "/* Every per-image linker exports these absolute symbols. A generated",
            " * crt0 is supplied for singleton images and shared images whose cores",
            " * expose mhartid. INIT_HART owns one-time BSS initialization; parked",
            " * cores reuse that image state on later wake/reset cycles. */",
            "extern unsigned char __mosaic_stack_base[];",
            "extern unsigned char __mosaic_stack_end[];",
            "extern unsigned char __mosaic_stack_stride[];",
            "extern unsigned char __mosaic_image_hart_count[];",
            "",
            "#endif  // MOSAIC_GENERATED_RUNTIME_H_",
            "",
        ]
    )
    return "\n".join(lines)


def _generic_crt0_identity(
    cfg: "MosaicConfig", image: Mapping[str, Any]
) -> str | None:
    """Return ``constant``/``mhartid`` when a generic startup is truthful."""

    if len(image["harts"]) == 1:
        return "constant"
    by_id = {hart.hart_id: hart for hart in cfg.harts}
    if all(
        "mhartid" in resolved_capabilities(by_id[hart_id].ip, by_id[hart_id].params)
        for hart_id in image["harts"]
    ):
        return "mhartid"
    return None


def _image_crt0(image: Mapping[str, Any], *, use_mhartid: bool) -> str:
    """Emit an opt-in per-image crt0 without requiring atomics.

    BSS belongs to the loaded image, not to an individual parked core. The
    lowest listed hart therefore clears it exactly once after the image is
    loaded and publishes a persistent release word. Repeated per-core resets
    reuse the initialized image state instead of racing a new BSS clear
    against secondaries that observed an old release. Singleton images use a
    constant hart ID; shared images are emitted only when every core exposes a
    real ``mhartid`` value. The generated code uses only RV32E registers.
    """

    image_id = image["image_id"]
    init_hart = image["harts"][0]
    if len(image["harts"]) > 1 and not use_mhartid:
        raise SoftwareGenerationError(
            f"image {image_id} shares harts {image['harts']} without mhartid"
        )
    hart_id_setup = (
        "    .word 0xF1402573  // csrr a0, mhartid (strict rv32i encoding)"
        if use_mhartid
        else f"    li a0, {init_hart}"
    )
    lines = [
        "// Generated by util/xheep_gen/software_gen.py. Do not edit.",
        f"// One-time startup contract for image {image_id}, harts {image['harts']}.",
        ".section .data.mosaic_runtime, \"aw\", @progbits",
        ".balign 4",
        ".global __mosaic_init_release",
        "__mosaic_init_release:",
        "    .word 0",
        "",
        ".section .text.start, \"ax\", @progbits",
        ".global _start",
        ".type _start, @function",
        ".weak mosaic_main",
        "_start:",
        hart_id_setup,
    ]
    for ordinal, hart_id in enumerate(image["harts"]):
        lines.extend([f"    li t0, {hart_id}", f"    beq a0, t0, .Lstack_{ordinal}"])
    lines.append("    j .Linvalid_hart")
    for ordinal, _hart_id in enumerate(image["harts"]):
        lines.extend(
            [
                f".Lstack_{ordinal}:",
                f"    la sp, __mosaic_stack_top_{ordinal}",
                "    j .Lstack_ready",
            ]
        )
    lines.extend(
        [
            ".Lstack_ready:",
            f"    li t0, {init_hart}",
            "    bne a0, t0, .Lsecondary_wait",
            "    la t0, __mosaic_init_release",
            "    lw t1, 0(t0)",
            "    bnez t1, .Lalready_initialized",
            "    la t0, __bss_start",
            "    la t2, __bss_end",
            ".Lbss_loop:",
            "    bgeu t0, t2, .Lbss_done",
            "    sw zero, 0(t0)",
            "    addi t0, t0, 4",
            "    j .Lbss_loop",
            ".Lbss_done:",
            "    fence rw, rw",
            "    la t0, __mosaic_init_release",
            "    li t1, 1",
            "    sw t1, 0(t0)",
            "    j .Lenter",
            ".Lalready_initialized:",
            "    fence r, rw",
            "    j .Lenter",
            ".Lsecondary_wait:",
            "    la t0, __mosaic_init_release",
            ".Lwait_loop:",
            "    lw t1, 0(t0)",
            "    beqz t1, .Lwait_loop",
            "    fence r, rw",
            ".Lenter:",
            "    call mosaic_main",
            ".Linvalid_hart:",
            "    wfi",
            "    j .Linvalid_hart",
            ".size _start, .-_start",
            "",
        ]
    )
    return "\n".join(lines)


def _titan_flash_linker(
    cfg: "MosaicConfig",
    images: Sequence[Mapping[str, Any]],
    *,
    data_base: int,
) -> str:
    """Link the hart-zero program for true SPI-flash execute-in-place boot.

    The immutable x-heep boot ROM enables ``spimemio`` and jumps to
    ``FLASH_BASE + 0x180`` when both boot straps are asserted.  Code and
    read-only data therefore live in flash, while initialized data, BSS and the
    stack live above MOSAIC's shared-control window in SRAM.  ``start.S`` uses
    the symbols below to initialize RAM before entering C.
    """

    titan = next((image for image in images if 0 in image["harts"]), None)
    if titan is None:
        raise SoftwareGenerationError("cold boot requires a hart-zero TITAN image")
    hart_zero = cfg.harts[0]
    if hart_zero.role != "titan":
        raise SoftwareGenerationError("cold boot requires hart zero to be a TITAN")
    if "boot_addr" in hart_zero.params:
        raise SoftwareGenerationError(
            "cold-boot TITAN boot_addr is fixed by the boot ROM SPI-XIP contract; "
            "remove the explicit boot_addr"
        )
    sram_end = SRAM_BASE + _writable_bytes(cfg)
    if data_base >= sram_end:
        raise SoftwareGenerationError("cold-boot TITAN has no SRAM data region")
    xlen = _xlen_for_isa(cfg.harts[0].isa)
    fmt = "elf32-littleriscv" if xlen == 32 else "elf64-littleriscv"
    flash_entry = FLASH_BASE + DEFAULT_BOOT_ADDRESS
    return "\n".join(
        [
            "/* Generated by util/xheep_gen/software_gen.py. Do not edit. */",
            f'OUTPUT_FORMAT("{fmt}", "{fmt}", "{fmt}")',
            "OUTPUT_ARCH(riscv)",
            "ENTRY(_start)",
            "MEMORY",
            "{",
            f"    flash_rx (rx) : ORIGIN = {_hex32(flash_entry)}, "
            f"LENGTH = {_hex32(FLASH_SIZE - DEFAULT_BOOT_ADDRESS)}",
            f"    data_rw (rwx) : ORIGIN = {_hex32(data_base)}, "
            f"LENGTH = {_hex32(sram_end - data_base)}",
            "}",
            "SECTIONS",
            "{",
            "    .text : {",
            "        _start = .;",
            "        KEEP(*(.text.start))",
            "        KEEP(*(.text.init))",
            "        *(.text.startup*)",
            "        *(.text*)",
            "        *(.rodata*)",
            "        *(.srodata*)",
            "        . = ALIGN(4);",
            "    } > flash_rx",
            "    .data : {",
            "        . = ALIGN(4);",
            "        __data_start = .;",
            "        *(.sdata*)",
            "        *(.data*)",
            "        . = ALIGN(4);",
            "        __data_end = .;",
            "    } > data_rw AT > flash_rx",
            "    __data_load_start = LOADADDR(.data);",
            "    .bss (NOLOAD) : {",
            "        . = ALIGN(4);",
            "        __bss_start = .;",
            "        *(.sbss*)",
            "        *(.bss*)",
            "        *(COMMON)",
            "        . = ALIGN(4);",
            "        __bss_end = .;",
            "    } > data_rw",
            "    __global_pointer$ = MIN(__data_start + 0x800, "
            "MAX(__data_start + 0x800, __bss_end - 0x800));",
            f"    __mosaic_image_hart_count = {len(titan['harts'])};",
            "    __mosaic_stack_stride = 0x400;",
            *[
                f"    __mosaic_image_hart_{ordinal} = {hart_id};"
                for ordinal, hart_id in enumerate(titan["harts"])
            ],
            "    .hart_stacks (NOLOAD) : ALIGN(16) {",
            "        __mosaic_stack_base = .;",
            f"        . += {_hex32(0x400 * len(titan['harts']))};",
            "        __mosaic_stack_end = .;",
            "    } > data_rw",
            *[
                f"    __mosaic_stack_top_{ordinal} = __mosaic_stack_base + "
                f"{_hex32(0x400 * (ordinal + 1))};"
                for ordinal, _hart_id in enumerate(titan["harts"])
            ],
            "    _sp = __mosaic_stack_top_0;",
            "    _end = .;",
            "    ASSERT(_end <= ORIGIN(data_rw) + LENGTH(data_rw), "
            '"TITAN RAM data/stack overflow")',
            "}",
            "",
        ]
    )


def _boot_manifest(
    cfg: "MosaicConfig",
    images: Sequence[Mapping[str, Any]],
    shared_base: int,
    shared_size: int,
) -> Dict[str, Any]:
    harts = []
    for hart in cfg.harts:
        image_id = next(
            image["image_id"] for image in images if hart.hart_id in image["harts"]
        )
        harts.append(
            {
                "hart_id": hart.hart_id,
                "group_index": hart.group_index,
                "group_instance": hart.group_instance,
                "ip": hart.ip,
                "role": hart.role,
                "isa": hart.isa,
                "abi": _abi_for_isa(hart.isa),
                "xlen": _xlen_for_isa(hart.isa),
                "boot_address": _hex32(_boot_address(hart)),
                "capabilities": sorted(resolved_capabilities(hart.ip, hart.params)),
                "capability_mask": _hex32(_capability_mask(hart.ip, hart.params)),
                "image_id": image_id,
            }
        )
    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "soc": {
            "name": cfg.soc_name,
            "pdk": cfg.pdk,
            "profile": cfg.profile,
            "target": cfg.target,
            "bus": cfg.bus,
        },
        "scheduler": {
            "tdu": cfg.scheduler.tdu,
            "mode": cfg.scheduler.mode,
        },
        "boot_policy": {
            # Publish the POR exception so verification does not have to infer
            # which non-TITAN hart, if any, is deliberately released.
            "testbench_hart0_bootstrap": (
                cfg.profile == "testbench"
                and not any(hart.role == "titan" for hart in cfg.harts)
            ),
        },
        "memory": {
            "sram_base": _hex32(SRAM_BASE),
            "sram_size": _writable_bytes(cfg),
            "boot_rom_base": _hex32(AO_PERIPHERAL_BASE + BOOT_ROM_OFFSET),
            "boot_rom_size": cfg.memory.boot_rom_kb * 1024,
            "shared_control_base": _hex32(shared_base),
            "shared_control_size": shared_size,
        },
        "harts": harts,
        "images": list(images),
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["topology_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return manifest


def _deployment_header(manifest: Mapping[str, Any]) -> str:
    topology_crc = binascii.crc32(
        str(manifest["topology_sha256"]).encode("ascii")
    ) & 0xFFFF_FFFF
    return "\n".join(
        [
            "// Generated by util/xheep_gen/software_gen.py. Do not edit.",
            "#ifndef MOSAIC_GENERATED_DEPLOYMENT_H_",
            "#define MOSAIC_GENERATED_DEPLOYMENT_H_",
            "",
            "#define MOSAIC_DEPLOYMENT_MAGIC 0x4D4F5341u",
            "#define MOSAIC_DEPLOYMENT_VERSION 1u",
            "#define MOSAIC_DEPLOYMENT_TITAN_FLASH_OFFSET 0x00000180u",
            f"#define MOSAIC_DEPLOYMENT_TOPOLOGY_CRC32 0x{topology_crc:08X}u",
            "",
            "#endif  // MOSAIC_GENERATED_DEPLOYMENT_H_",
            "",
        ]
    )


def generate_software_artifacts(
    cfg: "MosaicConfig", output_dir: str | Path
) -> SoftwareArtifacts:
    """Generate a complete software contract below ``output_dir``.

    Args:
        cfg: Fully parsed/resolved MOSAIC configuration. ``cfg.harts`` must be
            populated by ``mosaic_config.parse_yaml``.
        output_dir: Root of the generated software tree.

    Returns:
        A :class:`SoftwareArtifacts` path bundle.
    """

    if cfg.total_cores != len(cfg.harts) or not cfg.harts:
        raise SoftwareGenerationError(
            "MosaicConfig must contain a non-empty resolved per-hart topology"
        )
    expected_harts = list(range(cfg.total_cores))
    actual_harts = [hart.hart_id for hart in cfg.harts]
    if actual_harts != expected_harts:
        raise SoftwareGenerationError(
            f"hart IDs must be contiguous from zero, got {actual_harts}"
        )

    root = Path(output_dir)
    topology_header = root / "include" / "mosaic_topology.h"
    memory_map_header = root / "include" / "mosaic_memory_map.h"
    assembler_memory_map = root / "include" / "mosaic_memory_map.inc"
    isa_makefile = root / "make" / "mosaic_isa.mk"
    linker_script = root / "linker" / "mosaic_link.ld"
    boot_manifest = root / "boot_images.json"
    deployment_header = root / "include" / "mosaic_deployment.h"
    runtime_header = root / "include" / "mosaic_runtime.h"
    titan_flash_linker = root / "linker" / "titan_flash.ld"

    images, shared_base, shared_size, data_base = _layout(cfg)
    for image in images:
        identity = _generic_crt0_identity(cfg, image)
        image["startup_identity"] = identity
        image["startup_source"] = (
            f"startup/image_{image['image_id']}_crt0.S" if identity else None
        )
    _write_if_changed(topology_header, _topology_header(cfg))
    _write_if_changed(runtime_header, _runtime_header(images))
    _write_if_changed(
        memory_map_header, _memory_map_header(cfg, shared_base, shared_size)
    )
    _write_if_changed(
        assembler_memory_map, _assembler_memory_map(cfg, shared_base, shared_size)
    )
    _write_if_changed(isa_makefile, _isa_makefile(cfg, images))
    _write_if_changed(
        linker_script,
        _compatibility_linker(cfg, images, shared_base, shared_size, data_base),
    )
    image_linkers = []
    image_startups = []
    sram_end = SRAM_BASE + _writable_bytes(cfg)
    for image in images:
        path = root / image["linker_script"]
        _write_if_changed(
            path, _image_linker(image, sram_end=sram_end, data_base=data_base)
        )
        image_linkers.append(path)
        if image["startup_source"]:
            startup = root / image["startup_source"]
            _write_if_changed(
                startup,
                _image_crt0(
                    image, use_mhartid=image["startup_identity"] == "mhartid"
                ),
            )
            image_startups.append(startup)
    has_production_titan = cfg.profile == "soc" and cfg.harts[0].role == "titan"
    if has_production_titan:
        _write_if_changed(
            titan_flash_linker,
            _titan_flash_linker(cfg, images, data_base=data_base),
        )
    elif titan_flash_linker.exists():
        titan_flash_linker.unlink()
    expected_image_linkers = {path.resolve() for path in image_linkers}
    for stale in (root / "linker").glob("image_*.ld"):
        if stale.resolve() not in expected_image_linkers:
            stale.unlink()
    expected_startups = {path.resolve() for path in image_startups}
    for stale in (root / "startup").glob("image_*_crt0.S"):
        if stale.resolve() not in expected_startups:
            stale.unlink()
    manifest = _boot_manifest(cfg, images, shared_base, shared_size)
    _write_if_changed(deployment_header, _deployment_header(manifest))
    _write_if_changed(boot_manifest, json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return SoftwareArtifacts(
        root=root,
        topology_header=topology_header,
        memory_map_header=memory_map_header,
        assembler_memory_map=assembler_memory_map,
        isa_makefile=isa_makefile,
        linker_script=linker_script,
        boot_manifest=boot_manifest,
        deployment_header=deployment_header,
        runtime_header=runtime_header,
        titan_flash_linker=(titan_flash_linker if has_production_titan else None),
        image_linker_scripts=tuple(image_linkers),
        image_startup_sources=tuple(image_startups),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="resolved mosaic YAML input")
    parser.add_argument("--output", help="generated software directory")
    parser.add_argument(
        "--validate-production-demo",
        metavar="BOOT_IMAGES_JSON",
        help="validate that fixed sw/firmware demo sources apply to a generated BSP",
    )
    parser.add_argument(
        "--isa-makefile",
        metavar="MOSAIC_ISA_MK",
        help="mosaic_isa.mk paired with --validate-production-demo",
    )
    args = parser.parse_args(argv)

    if args.validate_production_demo:
        if args.config or args.output:
            parser.error(
                "--validate-production-demo cannot be combined with --config/--output"
            )
        if not args.isa_makefile:
            parser.error("--validate-production-demo requires --isa-makefile")
        try:
            validate_production_demo_files(
                args.validate_production_demo, args.isa_makefile
            )
        except (OSError, json.JSONDecodeError, SoftwareGenerationError) as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0

    if not args.config or not args.output:
        parser.error("generation requires both --config and --output")

    try:
        from .mosaic_config import load_mosaic_yaml
    except ImportError:
        from mosaic_config import load_mosaic_yaml

    cfg = load_mosaic_yaml(PurePath(args.config))
    artifacts = generate_software_artifacts(cfg, args.output)
    print(json.dumps(artifacts.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
