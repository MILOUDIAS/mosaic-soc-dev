#!/usr/bin/env python3
"""Pack MOSAIC AMP images into a bootable SPI-flash deployment image.

The x-heep boot ROM enables the memory-mapped flash window and jumps to byte
offset 0x180.  A compact table below that entry point describes worker images;
the TITAN cold-boot loader copies and CRC-checks them before TDU wake-up.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct
from typing import Sequence


MAGIC = 0x4D4F5341
VERSION = 1
TITAN_OFFSET = 0x180
FLASH_SIZE = 0x0100_0000
HEADER = struct.Struct("<8I")
ENTRY = struct.Struct("<4I")


class PackError(RuntimeError):
    pass


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & -alignment


def _verilog_hex(data: bytes) -> str:
    lines = ["@00000000"]
    for offset in range(0, len(data), 16):
        lines.append(" ".join(f"{byte:02X}" for byte in data[offset : offset + 16]))
    return "\n".join(lines) + "\n"


def pack(manifest_path: Path, image_paths: dict[int, Path], output: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    images = manifest.get("images")
    if not isinstance(images, list) or not images:
        raise PackError("boot manifest has no images")
    titan = next((item for item in images if 0 in item.get("harts", [])), None)
    if titan is None:
        raise PackError("boot manifest has no hart-zero TITAN image")
    titan_id = int(titan["image_id"])
    missing = sorted(int(item["image_id"]) for item in images if int(item["image_id"]) not in image_paths)
    if missing:
        raise PackError(f"missing binary paths for image IDs {missing}")

    binaries = {image_id: path.read_bytes() for image_id, path in image_paths.items()}
    if not binaries[titan_id]:
        raise PackError("TITAN binary is empty")
    cursor = _align(TITAN_OFFSET + len(binaries[titan_id]), 0x1000)
    entries: list[tuple[int, int, int, int]] = []
    deployments: list[dict] = []
    placements = {titan_id: TITAN_OFFSET}
    for item in images:
        image_id = int(item["image_id"])
        blob = binaries[image_id]
        max_size = int(item["max_size"])
        if len(blob) > max_size and image_id != titan_id:
            raise PackError(
                f"image {image_id} is {len(blob)} bytes, exceeds SRAM slot {max_size}"
            )
        if image_id != titan_id:
            placements[image_id] = cursor
            crc = binascii.crc32(blob) & 0xFFFF_FFFF
            entries.append((int(item["load_address"], 0), len(blob), cursor, crc))
            cursor = _align(cursor + len(blob), 0x1000)
        deployments.append(
            {
                "image_id": image_id,
                "harts": item["harts"],
                "execution_address": (
                    f"0x{0x4000_0000 + TITAN_OFFSET:08X}"
                    if image_id == titan_id
                    else item["load_address"]
                ),
                "sram_destination": (
                    None if image_id == titan_id else item["load_address"]
                ),
                "flash_offset": f"0x{placements[image_id]:08X}",
                "size": len(blob),
                "crc32": f"0x{binascii.crc32(blob) & 0xFFFF_FFFF:08X}",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "source": image_paths[image_id].name,
            }
        )

    entry_bytes = b"".join(ENTRY.pack(*entry) for entry in entries)
    header_size = HEADER.size + len(entry_bytes)
    if header_size > TITAN_OFFSET:
        raise PackError(f"deployment table ({header_size} bytes) overlaps TITAN entry")
    table_crc = binascii.crc32(entry_bytes) & 0xFFFF_FFFF
    topo_crc = binascii.crc32(manifest["topology_sha256"].encode("ascii")) & 0xFFFF_FFFF
    header = HEADER.pack(
        MAGIC, VERSION, header_size, len(entries), 1, topo_crc, table_crc, TITAN_OFFSET
    )
    if cursor > FLASH_SIZE:
        raise PackError(f"flash bundle is {cursor} bytes, exceeds {FLASH_SIZE}")
    flash = bytearray(b"\xFF" * cursor)
    flash[: len(header)] = header
    flash[len(header) : len(header) + len(entry_bytes)] = entry_bytes
    for image_id, offset in placements.items():
        blob = binaries[image_id]
        flash[offset : offset + len(blob)] = blob

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(flash)
    output.with_suffix(".hex").write_text(_verilog_hex(flash))
    deployment = {
        "schema_version": 1,
        "boot_mode": "spi-memio-xip-titan-load-workers",
        "boot_straps": {"boot_select": 1, "execute_from_flash": 1},
        "flash_binary": output.name,
        "flash_verilog_hex": output.with_suffix(".hex").name,
        "flash_size": len(flash),
        "topology_sha256": manifest["topology_sha256"],
        "table": {
            "offset": "0x00000000",
            "size": header_size,
            "entry_count": len(entries),
            "crc32": f"0x{table_crc:08X}",
        },
        "images": deployments,
    }
    output.with_suffix(".json").write_text(json.dumps(deployment, indent=2, sort_keys=True) + "\n")
    return deployment


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--image", action="append", default=[], metavar="ID=BIN")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    paths: dict[int, Path] = {}
    for value in args.image:
        try:
            raw_id, raw_path = value.split("=", 1)
            image_id = int(raw_id, 0)
        except ValueError as exc:
            parser.error(f"invalid --image {value!r}; expected ID=BIN")
        if image_id in paths:
            parser.error(f"duplicate image ID {image_id}")
        paths[image_id] = Path(raw_path)
    try:
        pack(args.manifest, paths, args.output)
    except (OSError, KeyError, TypeError, ValueError, PackError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
