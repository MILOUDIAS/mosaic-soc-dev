#!/usr/bin/env python3
"""Print a compact, stable summary of a generated MOSAIC build manifest."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def display_path(path: Path) -> str:
    """Prefer a repo-relative path while remaining useful from any cwd."""

    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as error:
        raise SystemExit(f"missing generated file: {path}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {path}: {error}") from error


def require_file(path: Path) -> Path:
    """Fail early if a manifest points at an incomplete generated tree."""

    if not path.is_file():
        raise SystemExit(f"missing generated file: {path}")
    return path


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <manifest.json>", file=sys.stderr)
        return 2

    manifest_path = Path(sys.argv[1])
    manifest = load_json(manifest_path)
    generated_root = Path(manifest["generated_root"])
    boot_contract_path = generated_root / "sw" / "boot_images.json"
    boot_contract = load_json(boot_contract_path)

    print("MOSAIC build summary")
    print(f"manifest: {display_path(manifest_path)}")
    print(f"build key: {manifest_path.parent.name}")
    print(f"generated root: {display_path(generated_root)}")
    print(f"harts: {len(boot_contract['harts'])}")
    for hart in sorted(boot_contract["harts"], key=lambda item: item["hart_id"]):
        print(
            f"  hart {hart['hart_id']}: {hart['ip']:<8} "
            f"role={hart['role']:<6} isa={hart['isa']:<8} "
            f"boot={hart['boot_address']} image={hart['image_id']}"
        )

    rtl_root = generated_root / "hw" / "core-v-mini-mcu"
    rtl_package = require_file(rtl_root / "include" / "core_v_mini_mcu_pkg.sv")
    cpu_rtl = require_file(rtl_root / "cpu_subsystem.sv")
    print(f"RTL package: {display_path(rtl_package)}")
    print(f"CPU RTL: {display_path(cpu_rtl)}")
    print(f"boot contract: {display_path(require_file(boot_contract_path))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
