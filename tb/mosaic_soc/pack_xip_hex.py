#!/usr/bin/env python3
"""Pack per-image Verilog hex files into one flash image for XIP boot.

Under Option C (docs/external_memory_boot_design.md) every hart executes in
place from the memory-mapped SPI-flash window, so images are linked at flash
ADDRESSES (0x4000_0180, 0x4001_0000, ...) and nothing is staged into RAM.

The testbench loads flash with

    $readmemh(file, gen_USE_EXTERNAL_DEVICE_EXAMPLE.flash_boot_i.memory)

which treats the ``@`` directives as indices into a 16 MiB BYTE array, i.e.
offsets from the start of flash. `objcopy -O verilog` emits absolute addresses,
so every record has to be rebased by FLASH_BASE. Rebasing is the whole job:
without it every image would land at 0x4000_xxxx in a 0x100_0000-entry array
and be dropped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FLASH_BASE = 0x4000_0000
FLASH_SIZE = 0x0100_0000

_ADDR = re.compile(r"^@([0-9A-Fa-f]+)\s*$")


def rebase(hex_text: str, image_name: str) -> str:
    """Rewrite absolute @addresses as flash-relative offsets."""
    out: list[str] = []
    seen_addr = False
    for line in hex_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _ADDR.match(stripped)
        if match:
            address = int(match.group(1), 16)
            if not FLASH_BASE <= address < FLASH_BASE + FLASH_SIZE:
                raise SystemExit(
                    f"{image_name}: address 0x{address:08X} is outside the flash "
                    f"window [0x{FLASH_BASE:08X}, 0x{FLASH_BASE + FLASH_SIZE:08X}). "
                    "An XIP image must be linked into flash."
                )
            out.append(f"@{address - FLASH_BASE:08X}")
            seen_addr = True
        else:
            out.append(stripped)
    if not seen_addr:
        raise SystemExit(f"{image_name}: no @address record; nothing to place")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("hexes", nargs="+", type=Path)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text())
    images = manifest.get("images", [])
    if not images:
        raise SystemExit("boot manifest has no images")
    if not all(image.get("execute_in_place") for image in images):
        raise SystemExit(
            "pack_xip_hex is only valid when every image is execute_in_place; "
            "staged images belong in pack_flash.py"
        )

    chunks = [rebase(path.read_text(), path.name) for path in sorted(args.hexes)]
    args.output.write_text("\n".join(chunks) + "\n")

    for image in images:
        load = int(image["load_address"], 0)
        print(
            f"    image {image['image_id']}: flash offset "
            f"0x{load - FLASH_BASE:08X} (XIP at {image['load_address']}), "
            f"harts {image['harts']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
