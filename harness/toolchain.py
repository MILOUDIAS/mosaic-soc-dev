"""The pinned toolchain, and `oh-my-soc doctor` to check a machine against it.

nix-eda is the reference (root flake.nix, `nix develop .#sim`). Every piece of
signoff evidence the project holds was produced under LibreLane 3.0.0's own
nix-eda and nixpkgs, so those revisions are read from flake.lock rather than
restated here, and flow/librelane/flake.lock must name the same three
(test_toolchain_pin.py). The IIC-OSIC-TOOLS container is a supported runtime,
labelled `iic:<tag>` so its results are never mistaken for nix-produced ones.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .core import REPO_ROOT, SkillResult, load_json

# tb/tools.sh MOSAIC_VERILATOR_VERSION must equal this (tested).
PINS = {"verilator": "5.050", "librelane": "3.0.0"}

# The input paths whose revisions define the EDA binaries, in both locks.
LOCK_PATHS = {
    "librelane": ("librelane",),
    "nix-eda": ("librelane", "nix-eda"),
    "nixpkgs": ("librelane", "nix-eda", "nixpkgs"),
}

IIC_IMAGE = "hpretl/iic-osic-tools:2026.09"

_FIX = "nix develop .#sim   (see docs/reproducing.md)"


def lock_node(lock: Mapping[str, Any], path: Sequence[str]) -> Mapping[str, Any]:
    """The lock node an input path resolves to, following `follows` links.

    Node names are not stable (a second nixpkgs becomes `nixpkgs_2`), so the
    graph is walked from the root by input name.
    """
    nodes = lock["nodes"]
    key = lock["root"]
    for name in path:
        ref = nodes[key]["inputs"][name]
        key = ref if isinstance(ref, str) else lock_node(lock, ref)["_key"]
    return {**nodes[key], "_key": key}


def lock_revs(lock_path: Path) -> Dict[str, Dict[str, str]]:
    """{input: {rev, narHash}} for every LOCK_PATHS entry."""
    lock = load_json(lock_path)
    out = {}
    for name, path in LOCK_PATHS.items():
        locked = lock_node(lock, path)["locked"]
        out[name] = {"rev": locked["rev"], "narHash": locked["narHash"]}
    return out


def runtime(environ: Mapping[str, str]) -> str:
    """`nix` (the pinned sim shell), `iic:<tag>`, `nix-other`, or `path`."""
    label = environ.get("MOSAIC_TOOLCHAIN", "")
    if label == "nix" or label.startswith("iic:"):
        return label
    return "nix-other" if environ.get("IN_NIX_SHELL") else "path"


def riscv_env(environ: Mapping[str, str]) -> Dict[str, str]:
    """RISCV_XHEEP / COMPILER_PREFIX derived from RISCV_TC, when not set.

    Same derivation as the tb runners: RISCV_TC is `<root>/bin/<triple>`, the
    boot ROM Makefile wants `<root>` and `<triple minus "elf">`.
    """
    tc = environ.get("RISCV_TC")
    if not tc:
        return {}
    derived = {"RISCV_XHEEP": str(Path(tc).parent.parent),
               "COMPILER_PREFIX": re.sub(r"elf$", "", Path(tc).name)}
    return {k: v for k, v in derived.items() if not environ.get(k)}


def _riscv_gcc(environ: Mapping[str, str]) -> Optional[str]:
    if environ.get("RISCV_TC"):
        gcc = environ["RISCV_TC"] + "-gcc"
        return gcc if os.access(gcc, os.X_OK) else None
    for d in filter(None, environ.get("PATH", "").split(os.pathsep)):
        for gcc in sorted(Path(d).glob("riscv32-*-elf-gcc")):
            if os.access(gcc, os.X_OK):
                return str(gcc)
    return None


def _version(argv: List[str]) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else ""


def check_tools(environ: Mapping[str, str],
                which: Optional[Callable[[str], Optional[str]]] = None,
                version: Callable[[List[str]], str] = _version) -> List[Dict[str, Any]]:
    """One row per tool: required tools decide `doctor`'s verdict."""
    which = which or (lambda cmd: shutil.which(cmd, path=environ.get("PATH")))
    rows = []
    vbin = None
    if environ.get("VERILATOR_PIN"):
        for sub in ("usr/bin", "bin"):
            cand = Path(environ["VERILATOR_PIN"]) / sub / "verilator"
            if os.access(cand, os.X_OK):
                vbin = str(cand)
                break
    vbin = vbin or which("verilator")
    vfound = version([vbin, "--version"]) if vbin else ""
    want = PINS["verilator"]
    rows.append({
        "name": "verilator", "required": True,
        "ok": vfound.startswith(f"Verilator {want} "),
        "found": vfound or None, "path": vbin,
        "detail": f"needs {want} (bug 21: 5.047-devel miscompiles cv32e40x)",
        "fix": f"{_FIX}, or export VERILATOR_PIN=<{want} install prefix>",
    })
    gcc = _riscv_gcc(environ)
    rows.append({
        "name": "riscv-gcc", "required": True, "ok": gcc is not None,
        "found": version([gcc, "--version"]) if gcc else None, "path": gcc,
        "detail": "RISCV_TC, else the first riscv32-*-elf-gcc on PATH",
        "fix": f"{_FIX}, or export RISCV_TC=<dir>/bin/riscv32-unknown-elf",
    })
    for name, argv, detail in (
        ("iverilog", ["iverilog", "-V"], "gate-level simulation (tb/gls)"),
        # kepler-formal has no version flag; its nix store path names the build
        ("kepler-formal", ["kepler-formal"], "LEC (run_lec.sh)"),
        ("nix", ["nix", "--version"], "runs the LibreLane flow and the sim shell"),
    ):
        path = which(argv[0])
        rows.append({"name": name, "required": False, "ok": path is not None,
                     "found": (version(argv) if len(argv) > 1 else path) if path else None,
                     "path": path,
                     "detail": detail, "fix": _FIX})
    return rows


def pdk_roots(repo_root: Path, home: Path) -> List[Dict[str, Any]]:
    """Where each open PDK is on this machine (informational)."""
    ciel = home / ".ciel" / "ciel"
    found = {
        "gf180mcuD": [repo_root / "flow/librelane/gf180mcu/gf180mcuD"],
        "sky130A": sorted(ciel.glob("sky130/versions/*/sky130A")),
        "ihp-sg13g2": sorted(ciel.glob("ihp-sg13g2/versions/*/ihp-sg13g2")),
    }
    return [{"pdk": pdk, "present": any(p.is_dir() for p in paths),
             "paths": [str(p) for p in paths if p.is_dir()]}
            for pdk, paths in found.items()]


def ambient_pdk_warnings(environ: Mapping[str, str], repo_root: Path) -> List[str]:
    """PDK_ROOT / PDK exported by the shell override the Makefile's `?=`."""
    default_root = repo_root / "flow/librelane/gf180mcu"
    warnings = []
    root, pdk = environ.get("PDK_ROOT"), environ.get("PDK")
    if root and Path(root).resolve() != default_root.resolve():
        warnings.append(
            f"PDK_ROOT={root} is exported; flow/librelane/Makefile uses "
            f"`PDK_ROOT ?=`, so make targets there resolve THIS tree instead "
            f"of {default_root.relative_to(repo_root)}. run_signoff.sh passes "
            f"--pdk-root explicitly and is unaffected.")
    if pdk and pdk != "gf180mcuD":
        warnings.append(
            f"PDK={pdk} is exported; flow/librelane/Makefile's `PDK ?= "
            f"gf180mcuD` yields to it.")
    return warnings


def doctor(environ: Optional[Mapping[str, str]] = None,
           repo_root: Optional[Path] = None,
           which: Optional[Callable[[str], Optional[str]]] = None,
           version: Callable[[List[str]], str] = _version,
           home: Optional[Path] = None) -> SkillResult:
    environ = os.environ if environ is None else environ
    repo_root = repo_root or REPO_ROOT
    rt = runtime(environ)
    tools = check_tools(environ, which, version)
    lock = repo_root / "flake.lock"
    revs = lock_revs(lock) if lock.exists() else {}
    warnings = ambient_pdk_warnings(environ, repo_root)
    if rt.startswith("iic:"):
        warnings.append(f"runtime {rt}: evidence produced here is labelled "
                        f"{rt} and is not comparable with nix-produced evidence")
    failing = [t["name"] for t in tools if t["required"] and not t["ok"]]
    return SkillResult(
        ok=not failing, skill="doctor",
        summary=(f"runtime {rt}: toolchain matches the pins" if not failing
                 else f"runtime {rt}: {', '.join(failing)} not as pinned -- {_FIX}"),
        details={"runtime": rt, "pins": {**PINS, "lock": revs}, "tools": tools,
                 "pdks": pdk_roots(repo_root, home or Path.home()),
                 "warnings": warnings},
        errors=[f"{t['name']}: found {t['found'] or 'nothing'}; {t['detail']}. "
                f"fix: {t['fix']}" for t in tools if t["required"] and not t["ok"]],
    )
