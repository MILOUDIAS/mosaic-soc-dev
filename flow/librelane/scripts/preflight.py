#!/usr/bin/env python3
"""Fail-closed validation for a MOSAIC GF180 physical source bundle.

RTL generation is intentionally broader than the qualified physical flow.
LibreLane may run only from an explicit, content-addressed bundle containing
the resolved MOSAIC manifest, flattened SoC RTL, a bound chip adapter, and the
four SRAM views.  This prevents a stale ``design.v`` or the checked-in adapter
placeholder from being mistaken for a tapeout input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Dict, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from util.xheep_gen.core_registry import target_capability_errors  # noqa: E402
from util.xheep_gen.build_manifest import SCHEMA_VERSION as BUILD_SCHEMA_VERSION  # noqa: E402


class PreflightError(RuntimeError):
    """A physical bundle is absent, malformed, stale, or unsupported."""


REQUIRED_ARTIFACTS = (
    "manifest",
    "flattened_rtl",
    "sram_gds",
    "sram_lef",
    "sram_lib",
    "sram_verilog",
)

MIN_FLATTENED_RTL_BYTES = 64 * 1024
MIN_BOUND_ADAPTER_BYTES = 512


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise PreflightError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PreflightError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{label} must contain a JSON object: {path}")
    return value


def _artifact(bundle: Path, name: str, raw: Any) -> Path:
    if not isinstance(raw, dict):
        raise PreflightError(f"artifacts.{name} must be an object with path and sha256")
    relative = raw.get("path")
    expected = raw.get("sha256")
    if not isinstance(relative, str) or not relative:
        raise PreflightError(f"artifacts.{name}.path must be a non-empty relative path")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise PreflightError(f"artifacts.{name}.sha256 must be 64 lowercase hex digits")
    path = (bundle / relative).resolve()
    try:
        path.relative_to(bundle)
    except ValueError as exc:
        raise PreflightError(f"artifacts.{name}.path escapes the bundle: {relative}") from exc
    if not path.is_file() or path.stat().st_size == 0:
        raise PreflightError(f"artifacts.{name} is missing or empty: {path}")
    actual = _sha256(path)
    if actual != expected:
        raise PreflightError(
            f"artifacts.{name} hash mismatch: expected {expected}, got {actual}"
        )
    return path


def _without_sv_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def _validate_gds(path: Path) -> None:
    """Perform a strict structural walk of the GDSII record stream."""

    data = path.read_bytes()
    offset = 0
    record_types: set[int] = set()
    structure_names: set[str] = set()
    while offset < len(data):
        if offset + 4 > len(data):
            raise PreflightError("artifacts.sram_gds has a truncated GDSII record")
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 4 or length & 1 or offset + length > len(data):
            raise PreflightError("artifacts.sram_gds has an invalid GDSII record length")
        record_type = data[offset + 2]
        record_types.add(record_type)
        if record_type == 0x06:  # STRNAME
            structure_names.add(
                data[offset + 4 : offset + length]
                .rstrip(b"\0")
                .decode("ascii", errors="ignore")
                .lower()
            )
        offset += length
    # HEADER, BGNLIB, LIBNAME, UNITS, ENDLIB and one named structure.
    required = {
        0x00,  # HEADER
        0x01,  # BGNLIB
        0x02,  # LIBNAME
        0x03,  # UNITS
        0x04,  # ENDLIB
        0x05,  # BGNSTR
        0x06,  # STRNAME
        0x07,  # ENDSTR
        0x08,  # BOUNDARY
        0x0D,  # LAYER
        0x0E,  # DATATYPE
        0x10,  # XY
        0x11,  # ENDEL
    }
    if (
        offset != len(data)
        or not required.issubset(record_types)
        or "mosaic_sram" not in structure_names
    ):
        raise PreflightError(
            "artifacts.sram_gds is not a complete GDSII mosaic_sram library "
            "with boundary geometry"
        )


def _validate_physical_attestation(
    manifest: Mapping[str, Any], paths: Mapping[str, Path]
) -> None:
    """Bind every physical input hash back into the resolved build manifest."""

    physical = manifest.get("physical_attestation")
    if not isinstance(physical, Mapping):
        raise PreflightError(
            "MOSAIC manifest has no physical_attestation; flattened RTL and SRAM "
            "views are not bound to this build"
        )
    expected_key = manifest.get("build_key")
    if physical.get("build_key") != expected_key:
        raise PreflightError("physical_attestation.build_key does not match manifest")
    for name in (
        "flattened_rtl",
        "sram_gds",
        "sram_lef",
        "sram_lib",
        "sram_verilog",
    ):
        expected = physical.get(f"{name}_sha256")
        actual = _sha256(paths[name])
        if expected != actual:
            raise PreflightError(
                f"physical_attestation does not bind artifacts.{name}: "
                f"expected {expected!r}, got {actual}"
            )
    if "bound_core_rtl" in paths:
        expected = physical.get("bound_core_rtl_sha256")
        actual = _sha256(paths["bound_core_rtl"])
        if expected != actual:
            raise PreflightError(
                "physical_attestation does not bind artifacts.bound_core_rtl"
            )


def validate_bundle(bundle_path: Path, mode: str) -> Dict[str, Path]:
    bundle = bundle_path.resolve()
    if not bundle.is_dir():
        raise PreflightError(
            "PHYSICAL_BUNDLE must name an existing content-addressed bundle directory"
        )
    descriptor_path = bundle / "physical_bundle.json"
    descriptor = _load_json(descriptor_path, "physical bundle descriptor")
    if descriptor.get("schema_version") != 1:
        raise PreflightError("physical_bundle.json schema_version must be 1")
    artifacts = descriptor.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PreflightError("physical_bundle.json requires an artifacts object")

    required = list(REQUIRED_ARTIFACTS)
    if mode == "chip":
        required.append("bound_core_rtl")
    unknown = sorted(set(artifacts) - set(REQUIRED_ARTIFACTS) - {"bound_core_rtl"})
    if unknown:
        raise PreflightError(f"unknown physical bundle artifacts: {', '.join(unknown)}")
    paths = {name: _artifact(bundle, name, artifacts.get(name)) for name in required}

    manifest = _load_json(paths["manifest"], "MOSAIC build manifest")
    if manifest.get("schema_version") != BUILD_SCHEMA_VERSION:
        raise PreflightError(
            f"MOSAIC manifest schema_version must be {BUILD_SCHEMA_VERSION}"
        )
    build_key = manifest.get("build_key")
    if not isinstance(build_key, str) or not re.fullmatch(
        r"[a-zA-Z0-9_.-]+-[0-9a-f]{12}", build_key
    ):
        raise PreflightError("MOSAIC manifest has no valid content-addressed build_key")
    if descriptor.get("build_key") != build_key:
        raise PreflightError("physical_bundle.json build_key does not match manifest")
    resolved = manifest.get("resolved")
    if not isinstance(resolved, dict):
        raise PreflightError("MOSAIC build manifest has no resolved object")
    memory = resolved.get("memory", {})
    groups = resolved.get("cores", [])
    public_cores = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            public = {
                key: group[key]
                for key in ("ip", "isa", "count", "role")
                if key in group
            }
            params = group.get("params", {})
            if isinstance(params, dict):
                public.update(params)
            public_cores.append(public)
    soc = {
        "target": resolved.get("target"),
        "profile": resolved.get("profile"),
        "pdk": resolved.get("pdk"),
        "bus": resolved.get("bus"),
        "memory": {
            "sram_kb": memory.get("declared_sram_kb") if isinstance(memory, dict) else None,
            "boot_rom_kb": memory.get("declared_boot_rom_kb") if isinstance(memory, dict) else None,
            "scratchpad_bytes": (
                memory.get("declared_scratchpad_bytes") if isinstance(memory, dict) else None
            ),
        },
        "cores": public_cores,
        "scheduler": resolved.get("scheduler", {}),
        "peripherals": resolved.get("declared_peripherals", []),
    }
    # The selectable platform blocks (soc.dma/debug/plic/spi_mode/...). The
    # qualified Block A design is defined as much by what it REMOVES as by its
    # core list, so these have to reach target_capability_errors. A manifest
    # predating resolved.platform has no claim to make about them and is
    # rejected below rather than silently passing on defaults.
    platform = resolved.get("platform")
    if not isinstance(platform, dict):
        raise PreflightError(
            "MOSAIC build manifest has no resolved.platform object: it predates "
            "selectable platform blocks and cannot attest which were built in. "
            "Regenerate the manifest with the current generator."
        )
    soc.update(platform)
    capability_errors = target_capability_errors(soc)
    if resolved.get("target") != "tapeout" or capability_errors:
        details = "; ".join(capability_errors) or "resolved.target is not 'tapeout'"
        raise PreflightError(f"manifest is outside the qualified physical matrix: {details}")

    flat_text = paths["flattened_rtl"].read_text(errors="ignore")
    flat_code = _without_sv_comments(flat_text)
    if paths["flattened_rtl"].stat().st_size < MIN_FLATTENED_RTL_BYTES:
        raise PreflightError(
            "flattened_rtl is too small to be a MOSAIC SoC closure; placeholder forbidden"
        )
    if not re.search(r"\bmodule\s+(?:core_v_mini_mcu|x_heep_system)\b", flat_code):
        raise PreflightError(
            "flattened_rtl does not define core_v_mini_mcu or x_heep_system"
        )

    if mode == "chip":
        bound_text = paths["bound_core_rtl"].read_text(errors="ignore")
        bound_code = _without_sv_comments(bound_text)
        if paths["bound_core_rtl"].stat().st_size < MIN_BOUND_ADAPTER_BYTES:
            raise PreflightError("bound_core_rtl is a placeholder-sized adapter")
        if not re.search(r"\bmodule\s+mosaic_soc_core\b", bound_code):
            raise PreflightError("bound_core_rtl does not define mosaic_soc_core")
        if not re.search(r"\bx_heep_system\b[\s\S]*?\b[A-Za-z_]\w*\s*\(", bound_code):
            raise PreflightError(
                "bound_core_rtl does not instantiate x_heep_system; the placeholder is forbidden"
            )
        if "TODO(authoring step)" in bound_text:
            raise PreflightError("bound_core_rtl still contains the authoring placeholder")

    view_checks = {
        "sram_lef": r"\bMACRO\s+mosaic_sram\b",
        "sram_lib": r"\bcell\s*\(\s*mosaic_sram\s*\)",
        "sram_verilog": r"\bmodule\s+mosaic_sram\b",
    }
    for name, pattern in view_checks.items():
        text = paths[name].read_text(errors="ignore")
        if not re.search(pattern, text, flags=re.IGNORECASE):
            raise PreflightError(f"artifacts.{name} does not define mosaic_sram")

    _validate_gds(paths["sram_gds"])
    _validate_physical_attestation(manifest, paths)

    return paths


def _emit_shell(paths: Mapping[str, Path]) -> str:
    names = {
        "manifest": "MOSAIC_BUILD_MANIFEST",
        "flattened_rtl": "MOSAIC_FLATTENED_SOC_RTL",
        "bound_core_rtl": "MOSAIC_BOUND_SOC_RTL",
        "sram_gds": "MOSAIC_SRAM_GDS",
        "sram_lef": "MOSAIC_SRAM_LEF",
        "sram_lib": "MOSAIC_SRAM_LIB",
        "sram_verilog": "MOSAIC_SRAM_VERILOG",
    }
    return "\n".join(
        f"export {names[key]}={shlex.quote(str(path))}"
        for key, path in paths.items()
        if key in names
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--mode", choices=("chip", "classic"), default="chip")
    parser.add_argument("--emit-shell", action="store_true")
    args = parser.parse_args()
    try:
        paths = validate_bundle(args.bundle, args.mode)
    except PreflightError as exc:
        print(f"ERROR: physical preflight failed: {exc}", file=sys.stderr)
        return 2
    if args.emit_shell:
        print(_emit_shell(paths))
    else:
        print(f"physical preflight PASS ({args.mode}): {args.bundle.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
