"""SoCView: one derived description of a generated SoC.

WHY THIS EXISTS
---------------
Nothing else describes a whole SoC as one object. The facts are spread over the
bundle manifest (a curated projection), boot_images.json, the generated
`core_v_mini_mcu_pkg.sv` (the only per-bundle crossbar and address map), the
core registry, the wrapper headers, run DEFs and external padframe files. Every
consumer that stitched its own subset got something wrong: topo-viz drew
`2*nh+1+4` crossbar masters and 2 RAM banks for every OBI design, and the
generated package says 7/9/11 masters and 1 bank for Blocks A/B/C.

HOW IT STAYS TRUE
-----------------
It never keeps its own copy of a fact the generator owns:

- the topology comes from `mosaic_to_xheep_kwargs`, the same `XHeep` object
  every template renders from;
- the crossbar, address map and PLIC come from rendering
  `core_v_mini_mcu_pkg.sv.tpl` in memory with those kwargs (byte-identical to
  the generated file apart from the trailing whitespace mcu_gen strips) and
  reading its localparams and address-rule tables;
- "is this block instantiated" mirrors the templates' own guard expressions
  (`_instantiated` below names the template line for each), because several
  blocks keep their address window and `_IS_INCLUDED` define when removed;
- IO and die come from the most authoritative artifact that exists, and every
  section records which one in its `basis`.

So a config gets a full view from YAML alone, before any RTL is generated.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path, PurePath
from typing import Any, Dict, Generator, List, Optional, Tuple

import yaml

from .core import REPO_ROOT

SCHEMA = 1
PKG_TEMPLATE = "hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv.tpl"
BASE_CONFIG = "configs/general.hjson"
PADS_CONFIG = "configs/pad_cfg.py"


# ── generator access ─────────────────────────────────────────────────


@contextlib.contextmanager
def _generator(repo_root: Path) -> Generator[None, None, None]:
    """Import the generator the way mcu_gen does: bare module names from
    util/mosaic_gen, cwd at the repo root. The generator prints its pad table on
    stdout; swallow it so `--json` output and the MCP stdio channel stay clean.
    ponytail: chdir is process-global; callers are single-threaded today."""
    gen = str(repo_root / "util" / "mosaic_gen")
    if gen not in sys.path:
        sys.path.insert(0, gen)
    old = os.getcwd()
    os.chdir(repo_root)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        os.chdir(old)


def _xheep_kwargs(config: Path, repo_root: Path) -> Dict[str, Any]:
    with _generator(repo_root):
        from mosaic_config import (  # type: ignore[import-not-found]
            load_mosaic_yaml, mosaic_to_xheep_kwargs)
        cfg = load_mosaic_yaml(PurePath(str(config)))
        return mosaic_to_xheep_kwargs(
            cfg, base_config=str(repo_root / BASE_CONFIG),
            pads_cfg_path=str(repo_root / PADS_CONFIG))


def render_pkg(kwargs: Dict[str, Any], repo_root: Path) -> str:
    """core_v_mini_mcu_pkg.sv exactly as mcu_gen would write it."""
    from mako.template import Template
    with _generator(repo_root):
        text = str(Template(filename=str(repo_root / PKG_TEMPLATE)).render_unicode(
            **kwargs, strict_undefined=True))
    return re.sub(r"[ \t]+$", "", text, flags=re.M)


# ── localparams ──────────────────────────────────────────────────────

_LOCALPARAM = re.compile(r"localparam\b[^=;]*?\b(\w+)\s*=\s*([^;]+);")
_LITERAL = re.compile(r"^(?:\d+)?'([dhb])([0-9a-fA-F_]+)$|^(\d+)$")
_RULE = re.compile(r"idx:\s*(\w+?)_IDX\b")


def _literal(tok: str) -> Optional[int]:
    m = _LITERAL.match(tok)
    if not m:
        return None
    if m.group(3):
        return int(m.group(3))
    base = {"d": 10, "h": 16, "b": 2}[m.group(1)]
    return int(m.group(2).replace("_", ""), base)


def localparams(pkg: str) -> Dict[str, int]:
    """Integer localparams, including `A + 32'hB` sums of earlier ones.
    Anything else (arrays, $clog2, ternaries) is skipped, not guessed."""
    out: Dict[str, int] = {}
    for name, expr in _LOCALPARAM.findall(pkg):
        terms = [t.strip() for t in expr.split("//")[0].split("+")]
        vals = [_literal(t) if _literal(t) is not None else out.get(t) for t in terms]
        if terms and all(v is not None for v in vals):
            out[name] = sum(vals)  # type: ignore[arg-type]
    return out


def _rules(pkg: str, table: str) -> List[str]:
    """Names in an `addr_map_rule_t ... <table> = '{ ... }` decode table, in
    order. These are the windows the RTL actually decodes."""
    m = re.search(rf"\b{table}\s*=\s*'\{{(.*?)\n\s*\}};", pkg, re.S)
    return _RULE.findall(m.group(1)) if m else []


# ── inclusion: mirrors the template guards ──────────────────────────

def _ext(xheep, name: str, default: bool) -> bool:
    v = xheep.get_extension(name)
    return default if v is None else bool(v)


def _instantiated(name: str, xheep) -> bool:
    """Whether the RTL instantiates block `name`. Each guard is the template's
    own expression; a block not listed is instantiated when it is decoded."""
    is_mc = xheep.is_multi_core()
    base = xheep.get_base_peripheral_domain()
    guards = {
        # ao_peripheral_subsystem.sv.tpl:8 / core_v_mini_mcu.sv.tpl:17
        "tdu": is_mc and _ext(xheep, "tdu_enabled", False),
        # core_v_mini_mcu_pkg.sv.tpl:132
        "clint": is_mc,
        # core_v_mini_mcu.sv.tpl:18-19
        "debug": _ext(xheep, "debug_enabled", True),
        # ao_peripheral_subsystem.sv.tpl:445 and :9-12
        "rv_timer_ao": _ext(xheep, "ao_rv_timer", True),
        "fast_intr_ctrl": _ext(xheep, "ao_fast_intr", True),
        # ao_peripheral_subsystem.sv.tpl:616
        "gpio_ao": base.contains_peripheral("gpio_ao"),
        # mosaic_config.py: dma "none" clears is_included but keeps the ports
        "dma": bool(base.get_dma().get_is_included()),
    }
    return guards.get(name, True)


# ── sections ─────────────────────────────────────────────────────────


def _cores(xheep) -> List[Dict[str, Any]]:
    from util.mosaic_gen.core_registry import CORE_SPECS
    out = []
    for g in xheep.cpus():
        ip = g.cpu.name
        spec = CORE_SPECS.get(ip)
        out.append({
            "ip": ip, "role": g.role, "isa": g.isa, "count": g.count,
            "hart_ids": list(range(g.hart_id_base, g.hart_id_base + g.count)),
            "params": {k: v for k, v in (g.params or {}).items()},
            "native_bus": spec.native_bus if spec else None,
            "sci_module": spec.sci_module if spec and spec.sci else None,
        })
    return out


def _masters(p: Dict[str, int], xheep) -> List[Dict[str, Any]]:
    debug, dma = _instantiated("debug", xheep), _instantiated("dma", xheep)
    out = []
    for name, idx in p.items():
        m = re.fullmatch(r"CORE(\d*)_(INSTR|DATA)_IDX", name)
        if m and (m.group(1) or "CORE0_INSTR_IDX" not in p):
            out.append({"name": f"core{m.group(1) or 0}.{m.group(2).lower()}",
                        "kind": "core", "idx": idx, "active": True})
        elif name == "DEBUG_MASTER_IDX":
            out.append({"name": "debug", "kind": "debug", "idx": idx,
                        "active": debug})
    # The template names only stream 0 (DMA_READ_P0_IDX, ...) but counts every
    # DMA master port's streams in SYSTEM_XBAR_NMASTER: the ports above the
    # named ones exist unnamed. Enumerate them all rather than guess roles.
    first = p.get("DMA_READ_P0_IDX")
    if first is not None:
        for i, idx in enumerate(range(first, p.get("SYSTEM_XBAR_NMASTER", first))):
            out.append({"name": f"dma.{i}", "kind": "dma", "idx": idx, "active": dma})
    return sorted(out, key=lambda m: m["idx"])


def _window(p: Dict[str, int], name: str) -> Dict[str, Any]:
    return {"name": name.lower(), "start": p.get(f"{name}_START_ADDRESS"),
            "size": p.get(f"{name}_SIZE")}


def _address_map(pkg: str, p: Dict[str, int], xheep) -> Dict[str, Any]:
    slaves = [dict(_window(p, n), idx=p.get(f"{n}_IDX"))
              for n in _rules(pkg, "XBAR_ADDR_RULES") if n != "ERROR"]
    ao = [dict(_window(p, n), instantiated=_instantiated(n.lower(), xheep))
          for n in _rules(pkg, "AO_PERIPHERALS_ADDR_RULES")]
    for extra in ("CLINT", "TDU"):
        if f"{extra}_START_ADDRESS" in p:
            ao.append(dict(_window(p, extra),
                           instantiated=_instantiated(extra.lower(), xheep)))
    user = [dict(_window(p, n), instantiated=True)
            for n in _rules(pkg, "PERIPHERALS_ADDR_RULES")]
    ext = _window(p, "EXT_SLAVE") if "EXT_SLAVE_START_ADDRESS" in p else None
    return {"slaves": slaves, "ao_peripherals": ao, "peripherals": user,
            "external": ext}


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _hex(v: Optional[int]) -> Optional[str]:
    return None if v is None else f"0x{v:08x}"


def _bundle(config: Path, repo_root: Path) -> Optional[Dict[str, Any]]:
    """The newest generated bundle whose manifest records this exact config
    file content. The generator closure may have moved since; the key says
    which closure it was, and the view never claims it is current."""
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    best = None
    for man in (repo_root / "build" / "mosaic").glob("*/manifest.json"):
        try:
            m = json.loads(man.read_text())
        except (OSError, ValueError):
            continue
        cfg = (m.get("inputs") or {}).get("mosaic_config") or {}
        if cfg.get("sha256") == digest and (
                best is None or m.get("created_utc", "") > best.get("created_utc", "")):
            best = m
    if best is None:
        return None
    boot = Path(best["bundle_dir"]) / "generated" / "sw" / "boot_images.json"
    harts = []
    if boot.is_file():
        harts = [{k: h.get(k) for k in ("hart_id", "ip", "isa", "abi", "role",
                                          "boot_address")}
                 for h in json.loads(boot.read_text()).get("harts", [])]
    return {"build_key": best.get("build_key"), "created_utc": best.get("created_utc"),
            "harts": harts}


#: the SoC name a run hardened, from the bundle path in its source list
_BUNDLE_KEY = re.compile(r"build/mosaic/([A-Za-z0-9_.-]+)-[0-9a-f]{12}/")


def _design_of(run_dir: Optional[Path]) -> Tuple[Optional[str], Dict[str, Any]]:
    if run_dir is None:
        return None, {}
    try:
        resolved = json.loads((run_dir / "resolved.json").read_text())
    except (OSError, ValueError):
        return None, {}
    return resolved.get("DESIGN_NAME"), resolved


def _pin_plan_dir(template: Optional[str], repo_root: Path) -> Optional[Path]:
    """The directory holding an external padframe's pin template. Runs record an
    absolute path that goes stale when the files move, so fall back to the
    same file name under flow/librelane/integration (run trees skipped)."""
    if not template:
        return None
    path = Path(str(template).removeprefix("dir::"))
    if path.is_file():
        return path.parent
    root = repo_root / "flow" / "librelane" / "integration"
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "runs"]
        if path.name in filenames:
            return Path(dirpath)
    return None


def _io(run_dir: Optional[Path], design: Optional[str], resolved: Dict[str, Any],
        address_map: Dict[str, Any], repo_root: Path) -> Dict[str, Any]:
    from .physical.defpins import parse_def_pins
    if run_dir and design:
        final_def = run_dir / "final" / "def" / f"{design}.def"
        if final_def.is_file():
            dp = parse_def_pins(final_def)
            cells: Dict[str, Dict[str, Any]] = {}
            plan = _pin_plan_dir(resolved.get("FP_DEF_TEMPLATE"), repo_root)
            if plan is not None:
                for iface in plan.glob("*_interface.yaml"):
                    for t in (yaml.safe_load(iface.read_text()) or {}).get("pins", []):
                        cells[t["project_pin"]] = {
                            "group": t.get("user_pin_name"), "cell": t.get("cell"),
                            "slot": t.get("physical_pad_slot")}
            pins = []
            for p in dp.pins:
                extra = cells.get(p.name, {})
                pins.append({"name": p.name, "direction": p.direction, "use": p.use,
                             "side": p.side,
                             "x_um": None if p.x_um is None else round(p.x_um, 3),
                             "y_um": None if p.y_um is None else round(p.y_um, 3),
                             "group": extra.get("group") or re.sub(r"\[\d+\]$", "", p.name),
                             "cell": extra.get("cell"), "slot": extra.get("slot")})
            basis = f"placed pins from {_rel(final_def, repo_root)}"
            if cells:
                basis += f"; pad cells from {_rel(plan, repo_root)}"  # type: ignore[arg-type]
            return {"basis": basis, "placed": True, "pins": pins}
    if design:
        wrapper = repo_root / "flow" / "librelane" / "experimental" / f"{design}.sv"
        if wrapper.is_file():
            from .skills.wrapper_smith import _ports_regex
            ports = _ports_regex(wrapper.read_text(), design)
            return {"basis": f"delivery wrapper ports, not placed ({_rel(wrapper, repo_root)})",
                    "placed": False,
                    "pins": [{"name": p.name, "direction": p.dir, "width": p.width,
                              "side": None, "group": p.name} for p in ports]}
    # ponytail: which blocks drive pins is a short hand list; derive it from the
    # generated peripheral ports once wrappers are generated (roadmap R7).
    internal = {"rv_plic", "rv_timer"}
    wanting = ([p["name"] for p in address_map["peripherals"] if p["name"] not in internal]
               + [p["name"] for p in address_map["ao_peripherals"]
                  if p["instantiated"] and p["name"] in ("spi_flash", "gpio_ao")])
    return {"basis": "not assigned: no delivery wrapper or pin plan for this config yet",
            "placed": False, "pins": [], "interfaces_needing_pins": wanting}


def _die(run_dir: Optional[Path], design: Optional[str], resolved: Dict[str, Any],
         soc: Dict[str, Any], repo_root: Path) -> Dict[str, Any]:
    def box(v) -> Optional[List[float]]:
        if isinstance(v, str):
            v = v.split()
        return [float(x) for x in v] if v and len(v) == 4 else None

    if run_dir is not None:
        # A run's die is the run's own record, never a later derivation: a
        # relative-sized run has no DIE_AREA in resolved.json, and the newer
        # generated config for the same design describes a different die.
        die_um = box(resolved.get("DIE_AREA"))
        basis = f"run {run_dir.name} resolved.json"
        final_def = run_dir / "final" / "def" / f"{design}.def"
        if die_um is None and final_def.is_file():
            from .physical.defpins import die_area
            area = die_area(final_def.read_text())
            if area:
                die_um, basis = list(area[1]), f"run {run_dir.name} final DEF DIEAREA"
        out: Dict[str, Any] = {"basis": basis if die_um else f"run {run_dir.name} records no die",
                               "die_um": die_um, "core_um": box(resolved.get("CORE_AREA"))}
        try:
            m = json.loads((run_dir / "final" / "metrics.json").read_text())
            out["utilisation"] = m.get("design__instance__utilization")
        except (OSError, ValueError):
            pass
        return out
    if design:
        gen = repo_root / "flow" / "librelane" / "experimental" / f".generated_{design}.yaml"
        if gen.is_file():
            g = yaml.safe_load(gen.read_text()) or {}
            if g.get("DIE_AREA"):
                return {"basis": f"latest derived hardening config {gen.name} (not a run)",
                        "die_um": box(g["DIE_AREA"]), "core_um": box(g.get("CORE_AREA"))}
    from .physical.floorplan import derive_floorplan
    fp, errors = derive_floorplan(soc)
    if fp is None:
        return {"basis": "not estimated: " + "; ".join(errors)[:300],
                "die_um": None, "core_um": None}
    d, c, m = fp.die_side_um, fp.core_side_um, fp.margin_um
    return {"basis": f"estimated ({fp.basis})", "die_um": [0, 0, d, d],
            "core_um": [m, m, m + c, m + c], "utilisation": fp.target_utilisation}


# ── entry point ──────────────────────────────────────────────────────


def build_view(config: Path, *, run_dir: Optional[Path] = None,
               design: Optional[str] = None,
               repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    """The SoC described by `config`, joined with a hardening run when given.
    Raises whatever the generator raises for an invalid config: a view of a
    config the generator rejects would describe nothing that can exist."""
    config = Path(config).resolve()
    run_dir = Path(run_dir).resolve() if run_dir else None
    if run_dir is not None and not (run_dir / "resolved.json").is_file():
        # a mistyped run must not silently fall back to an estimated die
        raise ValueError(f"{run_dir}: not a LibreLane run (no resolved.json)")
    soc = (yaml.safe_load(config.read_text()) or {}).get("soc", {})
    kw = _xheep_kwargs(config, repo_root)
    xheep = kw["xheep"]
    pkg = render_pkg(kw, repo_root)
    p = localparams(pkg)
    amap = _address_map(pkg, p, xheep)
    run_design, resolved = _design_of(run_dir)
    hardened = _BUNDLE_KEY.search(json.dumps(resolved)) if resolved else None
    if hardened and hardened.group(1) != xheep.get_extension("soc_name"):
        raise ValueError(
            f"run {run_dir.name if run_dir else ''} hardened SoC '{hardened.group(1)}', "
            f"not '{xheep.get_extension('soc_name')}' from {config.name}")
    design = design or run_design
    memory = xheep.memory_ss()
    return {
        "schema": SCHEMA,
        "name": xheep.get_extension("soc_name"),
        "config": _rel(config, repo_root),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "pdk": xheep.get_extension("pdk"),
        "target": xheep.get_extension("implementation_target"),
        "design": design,
        "bus": {"kind": soc.get("bus", "obi"), "xheep_type": xheep.bus_type().name,
                "opts": xheep.get_extension("bus_opts")},
        "cores": _cores(xheep),
        "num_harts": xheep.num_harts(),
        "crossbar": {"masters": _masters(p, xheep),
                     "nmaster": p.get("SYSTEM_XBAR_NMASTER"),
                     "nslave": p.get("SYSTEM_XBAR_NSLAVE")},
        "address_map": amap,
        "memory": {"banks": memory.ram_numbanks(), "interleaved_banks": memory.ram_numbanks_il(),
                   "ram_bytes": p.get("MEM_SIZE"),
                   "boot_rom_bytes": p.get("BOOTROM_SIZE"),
                   "flash_window": _window(p, "FLASH_MEM")},
        "blocks": {name: _instantiated(name, xheep)
                   for name in ("debug", "dma", "tdu", "clint", "rv_timer_ao",
                                "fast_intr_ctrl", "gpio_ao")},
        "plic": {"instantiated": any(x["name"] == "rv_plic" for x in amap["peripherals"]),
                 "sources": p.get("PLIC_NINT"), "used_sources": p.get("PLIC_USED_NINT")},
        "scheduler": {"mode": xheep.get_extension("sched_mode")},
        "spi_mode": xheep.get_extension("spi_mode"),
        "io": _io(run_dir, design, resolved, amap, repo_root),
        "die": _die(run_dir, design, resolved, soc, repo_root),
        "bundle": _bundle(config, repo_root),
        "run": _rel(run_dir, repo_root) if run_dir else None,
        "basis": {
            "topology": "mosaic_to_xheep_kwargs (the object every template renders from)",
            "crossbar_and_map": f"{PKG_TEMPLATE} rendered in memory",
            "inclusion": "template guard expressions (socview._instantiated)",
        },
    }


def hexify(view: Dict[str, Any]) -> Any:
    """A copy with every start/size as 0x........ for human-facing output."""
    def walk(v):
        if isinstance(v, dict):
            return {k: (_hex(x) if k in ("start", "size") and isinstance(x, int) else walk(x))
                    for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(view)
