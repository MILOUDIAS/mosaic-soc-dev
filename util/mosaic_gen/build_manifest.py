#!/usr/bin/env python3

"""MOSAIC build identity, resolved manifest, and FuseSoC staging helpers.

The Mako templates historically rendered beside their ``.tpl`` sources.  That
made two configurations overwrite each other and left FuseSoC consuming a
mixture of fresh and stale files.  This module gives every MOSAIC input tuple a
stable ``<soc-name>-<content-hash>`` bundle under ``build/mosaic`` and records
the resolved generator/build state in ``manifest.json``.

The staging helper creates a lightweight union tree for FuseSoC: repository
sources are symlinked read-only and only generated logical paths are overlaid
with files from the bundle.  Each FuseSoC invocation supplies its own run root,
so there is no process-global ``/tmp/mosaic_fusesoc_root`` race.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import yaml

try:
    from core_registry import CORE_SPECS
except ModuleNotFoundError:  # package import (util.mosaic_gen.build_manifest)
    from .core_registry import CORE_SPECS

SCHEMA_VERSION = 2
DEFAULT_OUTPUT_ROOT = "build/mosaic"

# Files whose contents can change the generated RTL/software contract or the
# FuseSoC graph selected by the manifest.  ``refs`` and build/test products are
# deliberately absent: they are not legal generator inputs.
GENERATOR_SOURCE_ROOTS = ("hw", "tb", "util", "configs", "sw", "flow", "scripts")
GENERATOR_SOURCE_FILES = (
    "core-v-mini-mcu.core",
    "waiver_v5.core",
)

def _resolve(path: str | os.PathLike[str], repo_root: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-._")
    return slug or "mosaic_soc"


def _ignored_source_path(path: Path) -> bool:
    """Return true for volatile build/cache products below source roots."""

    ignored_exact = {
        "__pycache__",
        ".pytest_cache",
        "build",
        ".git",
        ".venv",
        "final",
        "final_classic",
        "gf180mcu",
        "img",
        "runs",
    }
    if any(part in ignored_exact for part in path.parts):
        return True
    # `final_` catches the SAME BUG as the `.resolved_`/`.filelist_` rule
    # below, in the directory that rule missed. run_signoff.sh passes
    # `--save-views-to "$HERE/final_${TAG}"`, so every run drops a full set of
    # views -- GDS, DEF, ODB, netlists, metrics -- into
    # flow/librelane/experimental/final_<tag>/. `flow` is a closure root and
    # the exact names "final" and "final_classic" were ignored, but
    # "final_blocka_m45" was not. So each signoff run moved the hash of the
    # very RTL bundle it had just hardened, and the NEXT run aborted with
    # "no MOSAIC manifest. Generate the RTL first" -- costing a ~30 min regen
    # every time, blamed on a missing input rather than on the previous run.
    # Measured 2026-09-10: three consecutive re-hardens, three regenerations.
    if any(part.startswith(("obj_", "sim_build", "final_")) for part in path.parts):
        return True
    # The signoff runner writes its resolved config and its resolved file list
    # beside the config template -- they have to live there for the template's
    # `dir::` references to keep resolving. They are run OUTPUTS, not generator
    # sources, and `flow` is a closure root, so counting them meant a hardening
    # run changed the identity of the very RTL bundle it was hardening. The
    # next run then computed a hash for which no manifest existed and aborted
    # with "no MOSAIC manifest. Generate the RTL first" -- the flow poisoning
    # its own input, reported as a missing input.
    if path.name.startswith((".resolved_", ".filelist_", ".generated_")):
        return True
    # The same argument, for the hardening INPUTS rather than the outputs.
    #
    # `flow/librelane/signoff_template.yaml` is a LibreLane config: it sets
    # PnR corners, repair margins and PDN geometry. Nothing in mcu-gen or
    # FuseSoC reads it -- its only consumers are harness/physical/hardening.py
    # and, for the waivers, harness/skills/flow_runner.py. It cannot change one
    # line of generated RTL.
    #
    # But it sits under `flow`, so editing it moved the RTL bundle hash, and
    # the next hardening run aborted with "no MOSAIC manifest" and demanded a
    # multi-hour regen that would reproduce byte-identical RTL. Measured: the
    # bundles for blocka_reharden and blocka_slewonly have different hashes and
    # `diff -rq` of their generated/ trees is empty.
    #
    # That is not a nuisance, it is a correctness hazard. Editing the template
    # is exactly what a flow experiment DOES, so the cost of changing one PnR
    # setting was a full regen -- which is how one bisect died outright and how
    # a later untimed regen filled the disk and left a corrupt bundle. Pressure
    # to skip the regen and reuse a stale bundle is pressure to harden RTL you
    # did not verify.
    #
    # Deliberately narrow: the waivers, and `signoff_template*.yaml` so that a
    # variant template for a one-variable experiment is covered too -- that is
    # the case this exists for. Everything else under `flow` stays in the
    # closure, because `flow/librelane/**/*.sv` holds the delivery wrappers,
    # which ARE synthesised. Extend this only for a file you have confirmed no
    # RTL generator reads.
    if path.as_posix() == "flow/librelane/signoff_waivers.yaml" or (
        path.parent.as_posix() == "flow/librelane"
        and path.name.startswith("signoff_template")
        and path.suffix == ".yaml"
    ):
        return True
    if path.as_posix() == "tb/mosaic_soc/soc.f" or path.name == "results.xml":
        return True
    return path.suffix in {
        ".pyc",
        ".pyo",
        ".o",
        ".a",
        ".so",
        ".elf",
        ".log",
        ".vcd",
        ".fst",
        ".hex",
        ".bin",
        ".mem",
    }


def _generator_source_files(repo_root: Path) -> List[Path]:
    """Enumerate the repository files materialized into a FuseSoC snapshot."""

    candidates: set[Path] = set()
    for relative in GENERATOR_SOURCE_ROOTS:
        source = repo_root / relative
        if source.is_dir():
            for path in source.rglob("*"):
                relative_path = path.relative_to(repo_root)
                if _ignored_source_path(relative_path):
                    continue
                if path.is_file():
                    candidates.add(path)
    for relative in GENERATOR_SOURCE_FILES:
        source = repo_root / relative
        if source.is_file():
            candidates.add(source)
    return sorted(candidates, key=lambda item: str(item))


def _generator_source_record(repo_root: Path) -> Dict[str, str]:
    """Hash the complete local source closure used by generated builds.

    A YAML-only key is not content addressed: changing a Mako template, CPU
    model, SCI wrapper, platform RTL, or firmware while retaining the config
    would reuse the same bundle name.  Hash relative paths and file contents so
    the identity changes for both tracked and untracked source edits.
    """

    candidates = _generator_source_files(repo_root)

    # Unit tests may use a synthetic repo root.  The implementation itself is
    # still an identity input in that case.
    if not candidates:
        candidates = [Path(__file__).resolve()]

    digest = hashlib.sha256()
    count = 0
    for path in candidates:
        try:
            display = str(path.relative_to(repo_root))
        except ValueError:
            display = str(path)
        digest.update(display.encode())
        digest.update(b"\0")
        digest.update(_sha256(path).encode())
        digest.update(b"\0")
        count += 1
    return {
        "path": "<generator-source-closure>",
        "absolute_path": str(repo_root),
        "sha256": digest.hexdigest(),
        "file_count": str(count),
    }


def input_records(
    config_path: str | os.PathLike[str],
    base_config: str | os.PathLike[str],
    pads_cfg: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
) -> Dict[str, Dict[str, str]]:
    """Return normalized input paths and hashes used for the build identity."""

    root = Path(repo_root).resolve()
    records: Dict[str, Dict[str, str]] = {}
    for label, raw in (
        ("mosaic_config", config_path),
        ("base_config", base_config),
        ("pads_config", pads_cfg),
    ):
        path = _resolve(raw, root)
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
        try:
            display = str(path.relative_to(root))
        except ValueError:
            display = str(path)
        records[label] = {
            "path": display,
            "absolute_path": str(path),
            "sha256": _sha256(path),
        }
    records["generator_sources"] = _generator_source_record(root)
    return records


def compute_identity(
    config_path: str | os.PathLike[str],
    base_config: str | os.PathLike[str],
    pads_cfg: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
) -> Tuple[str, str, Dict[str, Dict[str, str]]]:
    """Return ``(soc_name, build_key, inputs)`` for a MOSAIC build."""

    records = input_records(config_path, base_config, pads_cfg, repo_root)
    with Path(records["mosaic_config"]["absolute_path"]).open() as stream:
        raw = yaml.safe_load(stream) or {}
    soc_name = str((raw.get("soc") or {}).get("name", "mosaic_soc"))

    digest = hashlib.sha256()
    digest.update(f"mosaic-build-schema:{SCHEMA_VERSION}\0".encode())
    for label in sorted(records):
        digest.update(label.encode())
        digest.update(b"\0")
        digest.update(records[label]["sha256"].encode())
        digest.update(b"\0")
    short_hash = digest.hexdigest()[:12]
    return soc_name, f"{_slug(soc_name)}-{short_hash}", records


def bundle_paths(
    config_path: str | os.PathLike[str],
    base_config: str | os.PathLike[str],
    pads_cfg: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
    output_root: str | os.PathLike[str] = DEFAULT_OUTPUT_ROOT,
) -> Dict[str, Any]:
    """Compute deterministic bundle paths without creating them."""

    root = Path(repo_root).resolve()
    soc_name, key, inputs = compute_identity(
        config_path, base_config, pads_cfg, root
    )
    out = Path(output_root)
    if not out.is_absolute():
        out = root / out
    bundle = out.resolve() / key
    return {
        "soc_name": soc_name,
        "key": key,
        "inputs": inputs,
        "bundle": bundle,
        "generated": bundle / "generated",
        "manifest": bundle / "manifest.json",
    }


def verify_pinned_identity(
    pinned: Mapping[str, Any],
    config_path: str | os.PathLike[str],
    base_config: str | os.PathLike[str],
    pads_cfg: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
) -> None:
    """Abort if any build-identity input drifted during generation.

    Rendering reads a large source closure over time. A manifest must never be
    written under the initial bundle path while advertising a recomputed key
    from a different source/config snapshot. The stage command independently
    revalidates the same hashes before building.
    """

    soc_name, key, inputs = compute_identity(
        config_path, base_config, pads_cfg, repo_root
    )
    if (
        key != pinned.get("key")
        or soc_name != pinned.get("soc_name")
        or inputs != pinned.get("inputs")
    ):
        raise RuntimeError(
            "MOSAIC config/source inputs changed during generation; generated "
            "outputs were discarded without a manifest. Rerun mcu_gen on a "
            "stable source tree."
        )


def logical_output_path(template: Path, repo_root: Path) -> Path:
    """Map a template to its repository-logical generated path."""

    template = template.resolve()
    try:
        relative = template.relative_to(repo_root.resolve())
    except ValueError:
        # External templates keep a collision-resistant prefix while retaining
        # their basename for human readability.
        prefix = hashlib.sha256(str(template.parent).encode()).hexdigest()[:8]
        relative = Path("external") / prefix / template.name
    text = str(relative)
    return Path(text[:-4] if text.endswith(".tpl") else text)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace ``path`` so concurrent readers never see partial data."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _selected_core_names(xheep: Any) -> List[str]:
    names: List[str] = []
    for group in xheep.cpus():
        name = str(group.name)
        if name not in names:
            names.append(name)
    return names


def selected_flags(core_names: Iterable[str]) -> List[str]:
    flags = ["mosaic_configured"]
    for name in core_names:
        spec = CORE_SPECS.get(name)
        if spec is None:
            raise ValueError(
                f"No FuseSoC selection flag is registered for core '{name}'"
            )
        flag = spec.fusesoc_flag
        if flag not in flags:
            flags.append(flag)
    return flags


def resolved_manifest(
    *,
    kwargs: Mapping[str, Any],
    config_path: str | os.PathLike[str],
    base_config: str | os.PathLike[str],
    pads_cfg: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
    generated_files: Sequence[Mapping[str, str]],
    pinned_identity: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the JSON-serializable manifest from resolved generator kwargs."""

    root = Path(repo_root).resolve()
    if pinned_identity is not None:
        verify_pinned_identity(
            pinned_identity, config_path, base_config, pads_cfg, root
        )
        soc_name = str(pinned_identity["soc_name"])
        key = str(pinned_identity["key"])
        inputs = dict(pinned_identity["inputs"])
        paths = {
            "bundle": Path(pinned_identity["bundle"]),
            "generated": Path(pinned_identity["generated"]),
        }
    else:
        soc_name, key, inputs = compute_identity(
            config_path, base_config, pads_cfg, root
        )
        out = Path(output_root)
        if not out.is_absolute():
            out = root / out
        bundle = out.resolve() / key
        paths = {"bundle": bundle, "generated": bundle / "generated"}
    xheep = kwargs["xheep"]
    mosaic_cfg = kwargs["mosaic_cfg"]

    core_names = _selected_core_names(xheep)
    groups: List[Dict[str, Any]] = []
    for group in xheep.cpus():
        groups.append(
            {
                "ip": group.name,
                "role": group.role,
                "isa": group.isa,
                "count": group.count,
                "hart_id_base": group.hart_id_base,
                "hart_ids": group.hart_ids(),
                "params": dict(group.params),
            }
        )

    user_peripherals = []
    if xheep.are_user_peripherals_configured():
        user_peripherals = [
            peripheral.get_name()
            for peripheral in xheep.get_user_peripheral_domain().get_peripherals()
        ]
    base_peripherals = []
    if xheep.are_base_peripherals_configured():
        base_peripherals = [
            peripheral.get_name()
            for peripheral in xheep.get_base_peripheral_domain().get_peripherals()
        ]

    flags = selected_flags(core_names)
    dependencies = [CORE_SPECS[name].fusesoc_dependency for name in core_names]
    dependencies.extend(["mosaic:ip:idma", "pulp-platform.org::obi"])
    if mosaic_cfg.bus == "floonoc":
        flags.append("mosaic_floonoc")
        dependencies.extend(["mosaic:ip:axi_obi", "mosaic:ip:floonoc_fabric"])

    generated = [dict(item) for item in generated_files]
    for item in generated:
        output = Path(item["path"])
        if not output.is_file():
            raise FileNotFoundError(f"generated output not found: {output}")
        item["sha256"] = _sha256(output)
    invalid = [
        item["logical_path"]
        for item in generated
        if Path(item["logical_path"]).parts
        and Path(item["logical_path"]).parts[0] in {".git", "build"}
    ]
    if invalid:
        raise ValueError(
            "generated logical paths may not overlay workspace state: "
            + ", ".join(invalid)
        )
    generated.sort(key=lambda item: item["logical_path"])
    return {
        "schema_version": SCHEMA_VERSION,
        "build_key": key,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "bundle_dir": str(paths["bundle"]),
        "generated_root": str(paths["generated"]),
        "inputs": inputs,
        "resolved": {
            "soc_name": soc_name,
            "pdk": mosaic_cfg.pdk,
            "profile": mosaic_cfg.profile,
            "target": mosaic_cfg.target,
            "bus": mosaic_cfg.bus,
            "bus_opts": mosaic_cfg.bus_opts,
            "num_harts": xheep.num_harts(),
            "is_multi_core": xheep.is_multi_core(),
            "cores": groups,
            # The selectable platform blocks. Without these the manifest cannot
            # distinguish the hardened Block A design from one that still
            # carries a debug module, a PLIC or a DMA -- so the physical
            # attestation could not tell them apart either.
            "platform": {
                "dma": mosaic_cfg.dma,
                "debug": mosaic_cfg.debug,
                "plic": mosaic_cfg.plic,
                "spi_mode": mosaic_cfg.spi_mode,
                "multicore_timer": mosaic_cfg.multicore_timer,
                "gpio_ao": mosaic_cfg.gpio_ao,
                "ao_rv_timer": mosaic_cfg.ao_rv_timer,
                "ao_fast_intr": mosaic_cfg.ao_fast_intr,
            },
            "memory": {
                "declared_sram_kb": mosaic_cfg.memory.sram_kb,
                "declared_boot_rom_kb": mosaic_cfg.memory.boot_rom_kb,
                "declared_scratchpad_bytes": mosaic_cfg.memory.scratchpad_bytes,
                "resolved_sram_bytes": xheep.memory_ss().ram_size_address(),
                "resolved_banks": xheep.memory_ss().ram_numbanks(),
                "resolved_interleaved_banks": xheep.memory_ss().ram_numbanks_il(),
            },
            "scheduler": {
                "tdu": mosaic_cfg.scheduler.tdu,
                "mode": mosaic_cfg.scheduler.mode,
            },
            "declared_peripherals": list(mosaic_cfg.peripherals),
            "resolved_user_peripherals": user_peripherals,
            "resolved_base_peripherals": base_peripherals,
        },
        "build": {
            "fusesoc_core": "openhwgroup.org:systems:core-v-mini-mcu",
            "target": "sim",
            "tool": "verilator",
            "flags": flags,
            "selected_dependencies": sorted(dict.fromkeys(dependencies)),
            "include_roots": [
                "hw/vendor/pulp_platform/obi/include",
                "hw/vendor/mosaic/idma/rtl/include",
            ],
            # Berkeley's extracted FIRRTL closure is still expressed by its
            # ordered .f fragment. gen_filelist.py retains this one narrow
            # fallback until berkeley.core itself carries the full 299-module
            # fileset; every other selected core comes from the FuseSoC graph.
            "ordered_filelist_fallbacks": (
                ["hw/vendor/mosaic/berkeley/berkeley.f"]
                if "mosaic_berkeley" in flags
                else []
            ),
        },
        "generated_files": generated,
    }


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def register_generated_file(
    manifest_path: str | os.PathLike[str],
    logical_path: str | os.PathLike[str],
    generated_path: str | os.PathLike[str],
    *,
    generator: str = "platform-generator",
) -> Dict[str, Any]:
    """Register a config-generated platform artifact in an existing bundle.

    Platform generators (for example, a future per-hart PLIC regtool step) may
    run after ``mcu_gen.py``.  They write below the bundle's ``generated`` tree
    and use this API to add or replace the repository-logical file that the
    FuseSoC overlay must consume.
    """

    manifest_file = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_file)
    logical = Path(logical_path)
    if (
        logical.is_absolute()
        or ".." in logical.parts
        or (logical.parts and logical.parts[0] in {".git", "build"})
    ):
        raise ValueError(f"logical path must stay within the repository: {logical}")
    generated = Path(generated_path).resolve()
    if not generated.is_file():
        raise FileNotFoundError(f"generated platform artifact not found: {generated}")

    generated_root = Path(manifest["generated_root"]).resolve()
    try:
        generated.relative_to(generated_root)
    except ValueError as error:
        raise ValueError(
            f"generated platform artifact must be below {generated_root}: {generated}"
        ) from error

    record = {
        "logical_path": str(logical),
        "path": str(generated),
        "generator": generator,
        "sha256": _sha256(generated),
    }
    records = [
        item
        for item in manifest.get("generated_files", [])
        if str(Path(item["logical_path"])) != str(logical)
    ]
    records.append(record)
    manifest["generated_files"] = sorted(records, key=lambda item: item["logical_path"])
    write_manifest(manifest_file, manifest)
    return record


def load_manifest(path: str | os.PathLike[str]) -> Dict[str, Any]:
    manifest_path = Path(path).resolve()
    with manifest_path.open() as stream:
        manifest = json.load(stream)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported manifest schema {manifest.get('schema_version')}; "
            f"expected {SCHEMA_VERSION}"
        )
    return manifest


def _materialize_overlay_dir(stage_dir: Path, source_dir: Path) -> None:
    if not stage_dir.exists():
        stage_dir.mkdir(parents=True)


def _verify_manifest_inputs(manifest: Mapping[str, Any], repo_root: Path) -> None:
    """Fail if any declared config or source-closure input has drifted."""

    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise RuntimeError("MOSAIC manifest has no hashed inputs")
    source_record = inputs.get("generator_sources")
    if not isinstance(source_record, Mapping) or not source_record.get("sha256"):
        raise RuntimeError("MOSAIC manifest has no generator source hash")
    current_sources = _generator_source_record(repo_root)
    if current_sources["sha256"] != source_record["sha256"]:
        raise RuntimeError(
            "Generator/source closure changed after this manifest was created: "
            f"expected {source_record['sha256']}, got {current_sources['sha256']}; "
            "regenerate the MOSAIC bundle"
        )

    for label, record in inputs.items():
        if label == "generator_sources":
            continue
        if not isinstance(record, Mapping):
            raise RuntimeError(f"MOSAIC manifest input {label} is malformed")
        path = Path(str(record.get("absolute_path", "")))
        expected = record.get("sha256")
        if not path.is_file() or not isinstance(expected, str):
            raise RuntimeError(f"MOSAIC manifest input {label} is missing: {path}")
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"MOSAIC manifest input {label} changed: expected {expected}, "
                f"got {actual}; regenerate the bundle"
            )


def _snapshot_repository_sources(repo_root: Path, stage: Path) -> None:
    """Copy the hashed FuseSoC closure; never build through live-tree links."""

    for source in _generator_source_files(repo_root):
        relative = source.relative_to(repo_root)
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=True)


def stage_fusesoc_root(
    manifest_path: str | os.PathLike[str], stage_root: str | os.PathLike[str]
) -> Path:
    """Create a FuseSoC core root overlay for one resolved manifest."""

    manifest = load_manifest(manifest_path)
    repo_root = Path(manifest["repo_root"]).resolve()
    _verify_manifest_inputs(manifest, repo_root)
    stage = Path(stage_root).resolve()
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    _snapshot_repository_sources(repo_root, stage)

    for item in manifest.get("generated_files", []):
        logical = Path(item["logical_path"])
        if (
            logical.is_absolute()
            or ".." in logical.parts
            or (logical.parts and logical.parts[0] in {".git", "build"})
        ):
            raise ValueError(f"invalid generated logical path: {logical}")
        generated = Path(item["path"]).resolve()
        if not generated.is_file():
            raise FileNotFoundError(
                f"Generated file listed by manifest does not exist: {generated}"
            )
        expected_hash = item.get("sha256")
        if not expected_hash:
            raise RuntimeError(f"Generated file lacks a manifest hash: {generated}")
        actual_hash = _sha256(generated)
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"Generated file hash mismatch for {generated}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        source_cursor = repo_root
        stage_cursor = stage
        for component in logical.parts[:-1]:
            source_cursor = source_cursor / component
            stage_cursor = stage_cursor / component
            if not source_cursor.is_dir():
                stage_cursor.mkdir(parents=True, exist_ok=True)
            else:
                _materialize_overlay_dir(stage_cursor, source_cursor)
        output = stage / logical
        if output.exists() or output.is_symlink():
            output.unlink()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.symlink_to(generated)

    shutil.copy2(Path(manifest_path).resolve(), stage / "mosaic-build-manifest.json")
    return stage


def generated_path(manifest: Mapping[str, Any], logical_path: str) -> Path:
    normalized = str(Path(logical_path))
    for item in manifest.get("generated_files", []):
        if str(Path(item["logical_path"])) == normalized:
            return Path(item["path"])
    raise KeyError(f"Generated logical path not found in manifest: {logical_path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    locate = sub.add_parser("locate", help="print the deterministic manifest path")
    locate.add_argument("--config", required=True)
    locate.add_argument("--base-config", default="configs/general.hjson")
    locate.add_argument("--pads-cfg", default="configs/pad_cfg.py")
    locate.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    locate.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)

    flags = sub.add_parser("flags", help="print one FuseSoC flag per line")
    flags.add_argument("--manifest", required=True)

    stage = sub.add_parser("stage", help="create the FuseSoC overlay core root")
    stage.add_argument("--manifest", required=True)
    stage.add_argument("--output", required=True)

    generated = sub.add_parser("generated-path", help="print a generated file path")
    generated.add_argument("--manifest", required=True)
    generated.add_argument("--logical-path", required=True)

    register = sub.add_parser(
        "register", help="register a generated platform artifact in a manifest"
    )
    register.add_argument("--manifest", required=True)
    register.add_argument("--logical-path", required=True)
    register.add_argument("--path", required=True)
    register.add_argument("--generator", default="platform-generator")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "locate":
        paths = bundle_paths(
            args.config,
            args.base_config,
            args.pads_cfg,
            args.repo_root,
            args.output_root,
        )
        print(paths["manifest"])
        return 0
    if args.command == "flags":
        manifest = load_manifest(args.manifest)
        print("\n".join(manifest["build"]["flags"]))
        return 0
    if args.command == "stage":
        print(stage_fusesoc_root(args.manifest, args.output))
        return 0
    if args.command == "generated-path":
        print(generated_path(load_manifest(args.manifest), args.logical_path))
        return 0
    if args.command == "register":
        record = register_generated_file(
            args.manifest,
            args.logical_path,
            args.path,
            generator=args.generator,
        )
        print(record["path"])
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
