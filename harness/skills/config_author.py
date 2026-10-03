"""config-author skill — translate intent into valid mosaic.yaml.

The agent provides structured parameters (cores, memory, peripherals, etc.)
and this skill validates, fills defaults, and writes a correct mosaic.yaml.

Design principle: the agent *describes* what it wants; the skill *ensures*
the output is valid and generation-ready. Deterministic validation, not LLM
guesswork.
"""

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from util.xheep_gen.core_registry import CURRENT_SCHEMA

from ..core import (
    SkillResult, REPO_ROOT, validate_config, dump_yaml,
    VALID_CORE_IPS, VALID_ROLES, VALID_ISAS, VALID_BUS,
    VALID_PERIPHERALS, VALID_SCHED_MODES, log,
)

# ── Defaults ─────────────────────────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "soc": {
        "schema": CURRENT_SCHEMA,
        "name": "mosaic_soc",
        "pdk": "gf180mcu",
        "target": "rtl",
        "cores": [],
        "memory": {"sram_kb": 32, "boot_rom_kb": 2},
        "dma": "idma",
        "bus": "obi",
        "scheduler": {"tdu": False, "mode": "static"},
        "peripherals": [],
    }
}

# Core type → default ISA and per-core params.
# ONLY parameters proven by shipped configs belong here: every non-standard
# key flows verbatim into group.params and must match what the generator's
# CPU classes / SCI template branches expect (e.g. cv32e20 rv32m is an ENUM
# STRING like "RV32MFast" — a bare True crashes mcu_gen; leave such knobs to
# explicit user configs).
# `sim_only` is harness METADATA (documentation + tapeout-preset guards); it is
# stripped before the YAML is written so mcu_gen never sees it.
CORE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "cv32e20":  {"isa": "rv32emc"},
    "cv32e40p": {"isa": "rv32imc"},
    "cv32e40px": {"isa": "rv32imc"},
    "cv32e40x": {"isa": "rv32imc"},
    "ibex":     {"isa": "rv32imc"},
    "fazyrv":   {"isa": "rv32i", "chunksize": 8},
    "serv":     {"isa": "rv32i"},
    "qerv":     {"isa": "rv32i"},
    "picorv32": {"isa": "rv32i"},
    "snitch":   {"isa": "rv32i"},   # RV32I: acc port tied off (RVM needs snitch_shared_muldiv)
    "cva6":     {"isa": "rv32imc", "sim_only": True},
    "rocket":   {"isa": "rv64imc", "sim_only": True},
    "boom":     {"isa": "rv64imc", "sim_only": True},
}

# Metadata keys stripped from core entries before YAML emission.
_META_KEYS = {"sim_only"}

# Presets: common SoC configurations
# The selectable platform blocks a config may switch off. Kept as a literal
# rather than imported so the harness does not depend on the generator's import
# path; test_target_capabilities.py checks the two stay in sync.
VALID_PLATFORM_KEYS = frozenset({
    "dma", "debug", "plic", "spi_mode",
    "multicore_timer", "gpio_ao", "ao_rv_timer", "ao_fast_intr",
})

PRESETS: Dict[str, Dict[str, Any]] = {
    "poc": {
        "soc": {
            "name": "mosaic_poc_alpha",
            "pdk": "gf180mcu",
            # NOT 'tapeout'. The PoC is the RTL/simulation reference topology;
            # the qualified physical part is the 'blocka' preset below, which
            # is the one with DRC/LVS evidence behind it.
            "target": "rtl",
            "cores": [
                {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
                {"ip": "fazyrv", "isa": "rv32i", "chunksize": 8, "count": 2, "role": "atlas", "boot_addr": 0x1000},
                {"ip": "serv", "isa": "rv32i", "count": 4, "role": "nano", "boot_addr": 0x2000},
            ],
            "memory": {"sram_kb": 32, "boot_rom_kb": 2},
            "dma": "idma",
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart", "gpio", "timer", "spi"],
        }
    },
    # The frozen Chipathon Block A part. Mirrors configs/mosaic_tapeout_ultra.yaml;
    # docs/rtl_freeze_blocka.md is the evidence. Kept in sync by
    # test_target_capabilities.py, which validates it against the tapeout gate.
    "blocka": {
        "soc": {
            "name": "mosaic_tapeout_ultra",
            "pdk": "gf180mcu",
            "target": "tapeout",
            "cores": [
                {"ip": "serv", "isa": "rv32ic", "count": 1, "role": "titan",
                 "with_csr": 1, "compressed": 1},
                {"ip": "serv", "isa": "rv32i", "count": 1, "role": "atlas",
                 "boot_addr": 0x40010000, "with_csr": 0},
            ],
            "memory": {"sram_kb": 0, "scratchpad_bytes": 128, "boot_rom_kb": 1},
            "dma": "none",
            "debug": False,
            "plic": False,
            "spi_mode": "xip_only",
            "multicore_timer": False,
            "gpio_ao": False,
            "ao_rv_timer": False,
            "ao_fast_intr": False,
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart"],
        }
    },
    "minimal": {
        "soc": {
            "name": "mosaic_minimal",
            "pdk": "gf180mcu",
            "target": "rtl",
            "cores": [
                {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
                {"ip": "serv", "isa": "rv32i", "count": 1, "role": "nano", "boot_addr": 0x800},
            ],
            "memory": {"sram_kb": 8, "boot_rom_kb": 1},
            "dma": "idma",
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "static"},
            "peripherals": ["uart"],
        }
    },
    "max_cores": {
        "soc": {
            "name": "mosaic_max_cores",
            "pdk": "gf180mcu",
            "target": "rtl",
            "cores": [
                {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
                {"ip": "ibex", "isa": "rv32imc", "count": 1, "role": "atlas", "boot_addr": 0x1000},
                {"ip": "fazyrv", "isa": "rv32i", "chunksize": 8, "count": 2, "role": "atlas", "boot_addr": 0x2000},
                {"ip": "qerv", "isa": "rv32i", "count": 2, "role": "nano", "boot_addr": 0x3000},
                {"ip": "serv", "isa": "rv32i", "count": 4, "role": "nano", "boot_addr": 0x4000},
            ],
            "memory": {"sram_kb": 64, "boot_rom_kb": 2},
            "dma": "idma",
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "power-aware"},
            "peripherals": ["uart", "gpio", "timer", "spi"],
        }
    },
    # SIM-ONLY presets (cva6/rocket/boom are excluded from the GF180 tapeout)
    "sim_zoo": {
        "soc": {
            "name": "mosaic_sim_zoo",
            "pdk": "gf180mcu",
            "profile": "testbench",
            "target": "simulation",
            "cores": [
                {"ip": "cva6", "isa": "rv32imc", "count": 1, "role": "titan"},
                {"ip": "snitch", "isa": "rv32i", "count": 1, "role": "atlas",
                 "boot_addr": 0x1000},
                {"ip": "picorv32", "isa": "rv32i", "count": 1, "role": "nano",
                 "boot_addr": 0x2000},
            ],
            "memory": {"sram_kb": 32, "boot_rom_kb": 2},
            "dma": "idma",
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart", "gpio", "timer", "spi"],
        }
    },
    "berkeley": {
        "soc": {
            "name": "mosaic_berkeley_preset",
            "pdk": "gf180mcu",
            "profile": "testbench",
            "target": "simulation",
            "cores": [
                {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
                {"ip": "rocket", "isa": "rv64imc", "count": 1, "role": "atlas",
                 "boot_addr": 0x1000},
                {"ip": "boom", "isa": "rv64imc", "count": 1, "role": "nano",
                 "boot_addr": 0x2000},
            ],
            "memory": {"sram_kb": 32, "boot_rom_kb": 2},
            "dma": "idma",
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart", "gpio", "timer", "spi"],
        }
    },
}

# Only the canonical 32-KiB GF180/OBI PoC is within the currently qualified
# implementation matrix.  The other presets remain useful RTL generators.
TAPEOUT_PRESETS = {"blocka"}


class ConfigAuthor:
    """Skill: generate a valid mosaic.yaml from structured parameters.

    Usage:
        author = ConfigAuthor()
        result = author.generate(
            name="my_soc",
            cores=[
                {"ip": "cv32e20", "count": 1, "role": "titan"},
                {"ip": "serv", "count": 4, "role": "nano"},
            ],
            sram_kb=32,
            peripherals=["uart", "gpio"],
        )
        # result.ok == True, result.details["path"] = path to written YAML
    """

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT

    def generate(
        self,
        name: str = "mosaic_soc",
        cores: Optional[List[Dict[str, Any]]] = None,
        sram_kb: int = 32,
        boot_rom_kb: int = 2,
        bus: str = "obi",
        dma: str = "idma",
        tdu: bool = False,
        sched_mode: str = "static",
        peripherals: Optional[List[str]] = None,
        pdk: str = "gf180mcu",
        target: str = "rtl",
        preset: Optional[str] = None,
        output_path: Optional[Path] = None,
        scratchpad_bytes: Optional[int] = None,
        platform: Optional[Dict[str, Any]] = None,
        target_clock_mhz: Optional[float] = None,
        repair_margin_pct: Optional[int] = None,
    ) -> SkillResult:
        """Generate a mosaic.yaml from parameters.

        Args:
            name: SoC name.
            cores: List of core dicts with at least 'ip', 'count', 'role'.
            sram_kb: SRAM size in KB.
            boot_rom_kb: Boot ROM size in KB.
            bus: Bus type (obi/log/floonoc).
            dma: DMA engine (idma/none). "none" omits the engine and its
                crossbar masters; only correct if nothing issues bulk copies.
            tdu: Enable Task Dispatch Unit.
            sched_mode: Scheduling mode (static/dynamic/power-aware).
            peripherals: List of peripheral names.
            pdk: Target PDK.
            target: Implementation intent (rtl/simulation/tapeout).
            preset: Use a named preset instead of manual params.
            output_path: Where to write the YAML. Default: configs/<name>.yaml.

        Returns:
            SkillResult with validation status and output path.
        """
        # Start from preset if specified
        if preset:
            if preset not in PRESETS:
                return SkillResult(
                    ok=False, skill="config-author",
                    summary=f"Unknown preset '{preset}'",
                    errors=[f"Available presets: {sorted(PRESETS.keys())}"],
                )
            cfg = copy.deepcopy(PRESETS[preset])
            cfg["soc"]["name"] = name
            # A preset carries its own pdk, and an explicit --pdk used to be
            # discarded here: `generate --preset poc --pdk sky130` wrote
            # gf180mcu and reported success. A flag the caller set and the tool
            # ignored is worse than one it refuses, because the config then
            # names a technology nobody chose.
            if pdk != DEFAULT_CONFIG["soc"]["pdk"]:
                cfg["soc"]["pdk"] = pdk
        else:
            # Build from parameters
            if not cores:
                return SkillResult(
                    ok=False, skill="config-author",
                    summary="No cores specified",
                    errors=["At least one core group is required"],
                )

            cfg = {
                "soc": {
                    # Stated, not implied: a generated config records which
                    # schema it was authored against, so it cannot rot silently
                    # as the validator evolves.
                    "schema": CURRENT_SCHEMA,
                    "name": name,
                    "pdk": pdk,
                    "target": target,
                    "cores": [],
                    "memory": {"sram_kb": sram_kb, "boot_rom_kb": boot_rom_kb},
                    "dma": dma,
                    "bus": bus,
                    "scheduler": {"tdu": tdu, "mode": sched_mode},
                    "peripherals": peripherals or [],
                }
            }

            # A part with no SRAM pool needs its scratchpad stated: that is
            # where the shared-control window and any stack live.
            if scratchpad_bytes is not None:
                cfg["soc"]["memory"]["scratchpad_bytes"] = scratchpad_bytes

            # The clock is DESIGN INTENT and belongs in the SoC config, not in a
            # hardening config: `physical-intent harden` derives CLOCK_PERIOD
            # from it. Without this flag a frequency could only be set by
            # hand-editing a LibreLane config, which is how a stray
            # config_blocka_25mhz.yaml came to exist. Emitted only when asked,
            # so a config that does not state a clock still fails the
            # derivation loudly ("a clock nobody chose is the kind of number
            # that ends up in a datasheet") rather than defaulting to one.
            if target_clock_mhz is not None:
                cfg["soc"].setdefault("objectives", {})
                cfg["soc"]["objectives"]["target_clock_mhz"] = target_clock_mhz

            # Per-design repair margin. Same rule as the clock: emitted only
            # when asked, because a design that does not state one should
            # take the template's value rather than silently pin itself to
            # whatever the default happened to be the day it was generated.
            if repair_margin_pct is not None:
                cfg["soc"].setdefault("objectives", {})
                cfg["soc"]["objectives"]["repair_margin_pct"] = repair_margin_pct

            # Selectable platform blocks (soc.debug/plic/spi_mode/...). Only
            # emitted when asked for, so configs that do not care keep the
            # generator's defaults rather than gaining a wall of `true`s.
            for key, value in (platform or {}).items():
                if key not in VALID_PLATFORM_KEYS:
                    return SkillResult(
                        ok=False, skill="config-author",
                        summary=f"unknown platform key {key!r}",
                        errors=[f"valid platform keys: {sorted(VALID_PLATFORM_KEYS)}"],
                    )
                cfg["soc"][key] = value

            # Fill core defaults
            next_worker_boot = 0x1000
            used_worker_boots = set()
            for core in cores:
                if core.get("role") == "titan" or "boot_addr" not in core:
                    continue
                try:
                    address = (
                        int(core["boot_addr"], 0)
                        if isinstance(core["boot_addr"], str)
                        else core["boot_addr"]
                    )
                except (TypeError, ValueError):
                    continue  # authoritative validation reports the bad field
                used_worker_boots.add(address)
            has_sim_only = False
            for c in cores:
                core_entry = dict(c)
                ip = core_entry.get("ip", "")
                if ip in CORE_DEFAULTS:
                    defaults = CORE_DEFAULTS[ip]
                    has_sim_only |= bool(defaults.get("sim_only", False))
                    for k, v in defaults.items():
                        if k not in core_entry:
                            core_entry[k] = v
                if (
                    ip in {"rocket", "boom"}
                    and core_entry.get("role") == "titan"
                    and "boot_addr" not in core_entry
                ):
                    core_entry["boot_addr"] = 0x180
                # A production AMP worker is reset-held until its image is
                # dispatched.  Give every authored worker *group* a distinct,
                # explicit SRAM image slot unless the caller chose one.  Harts
                # within a group intentionally share that image.
                if (
                    core_entry.get("role") != "titan"
                    and "boot_addr" not in core_entry
                ):
                    while next_worker_boot in used_worker_boots:
                        next_worker_boot += 0x1000
                    core_entry["boot_addr"] = next_worker_boot
                    used_worker_boots.add(next_worker_boot)
                    next_worker_boot += 0x1000
                cfg["soc"]["cores"].append(core_entry)

            # Cores whose wrappers are qualified only by the simulation
            # harness must never silently turn an otherwise ordinary authored
            # config into a tapeout claim.
            if has_sim_only:
                cfg["soc"]["profile"] = "testbench"
                cfg["soc"]["target"] = "simulation"

            # Worker harts are reset-held until dispatched, so an AMP design
            # cannot function without the TDU.  Treat it as mandatory platform
            # infrastructure when authoring instead of emitting a dead SoC.
            if any(c.get("role") != "titan" for c in cfg["soc"]["cores"]):
                cfg["soc"]["scheduler"]["tdu"] = True

        # Strip harness-only metadata (mcu_gen must never see it)
        for c in cfg["soc"]["cores"]:
            for k in _META_KEYS:
                c.pop(k, None)

        # Validate (tapeout presets must stay free of sim-only cores)
        allow_sim_only = preset not in TAPEOUT_PRESETS if preset else True
        errors = validate_config(cfg, allow_sim_only=allow_sim_only)
        if errors:
            return SkillResult(
                ok=False, skill="config-author",
                summary=f"Config validation failed with {len(errors)} error(s)",
                errors=errors,
            )

        # Write
        if output_path is None:
            output_path = self.repo_root / "configs" / f"{name}.yaml"

        dump_yaml(cfg, output_path)

        return SkillResult(
            ok=True, skill="config-author",
            summary=f"Generated valid config '{name}' -> {output_path}",
            details={
                "path": str(output_path),
                "config": cfg,
                "core_count": sum(c.get("count", 1) for c in cfg["soc"]["cores"]),
                "peripheral_count": len(cfg["soc"]["peripherals"]),
                "target": cfg["soc"].get("target", "rtl"),
            },
        )

    def validate_file(self, path: Path) -> SkillResult:
        """Validate an existing mosaic.yaml file."""
        try:
            from ..core import load_yaml
            cfg = load_yaml(path)
        except Exception as e:
            return SkillResult(
                ok=False, skill="config-author",
                summary=f"Failed to parse {path}: {e}",
                errors=[str(e)],
            )

        errors = validate_config(cfg)
        if errors:
            return SkillResult(
                ok=False, skill="config-author",
                summary=f"{path.name} has {len(errors)} validation error(s)",
                errors=errors,
            )

        soc = cfg["soc"]
        total_cores = sum(c.get("count", 1) for c in soc.get("cores", []))
        return SkillResult(
            ok=True, skill="config-author",
            summary=f"{path.name} is valid ({total_cores} cores, {len(soc.get('peripherals', []))} peripherals)",
            details={"config": cfg, "total_cores": total_cores},
        )

    def wake_demo_config(self, core: str,
                         output_path: Optional[Path] = None) -> SkillResult:
        """Emit the canonical 3-hart TDU wake-demo config for a worker core.

        Shape (programmatic clone of configs/mosaic_picorv32.yaml): 1x cv32e20
        TITAN + 2x <core> workers at boot_addr 0x1000/0x2000, 32 KB SRAM,
        bus obi, TDU dynamic — so tb/mosaic_soc/run.sh, the demo firmware and
        the linker script are reused unchanged. Used by tb-smith wake-demo.
        """
        if core not in VALID_CORE_IPS:
            return SkillResult(
                ok=False, skill="config-author",
                summary=f"Unknown core '{core}'",
                errors=[f"Available cores: {sorted(VALID_CORE_IPS)}"],
            )
        # The ISA must come from the authoritative registry, not from a bare
        # "rv32i" default. CORE_DEFAULTS had no hazard3 entry, so wake-demo
        # configs for it were emitted as rv32i and rejected at validation
        # ("valid: ['rv32imc']") -- the documented `tb-smith wake-demo hazard3`
        # path could not run at all. Any core added to the registry without a
        # CORE_DEFAULTS entry would have repeated it.
        from util.xheep_gen.core_registry import CORE_SPECS

        supported = CORE_SPECS[core].isas
        preferred = CORE_DEFAULTS.get(core, {}).get("isa")
        if preferred not in supported:
            # Smallest workable ISA first: the demo firmware is plain rv32i,
            # so prefer a base variant and only climb when the core demands it.
            preferred = next(
                (candidate for candidate in
                 ("rv32i", "rv32ic", "rv32im", "rv32imc", "rv32e", "rv32ec",
                  "rv32emc")
                 if candidate in supported),
                sorted(supported)[0],
            )
        isa = preferred
        cores = [
            {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
            {"ip": core, "isa": isa, "count": 1, "role": "atlas",
             "boot_addr": 0x1000},
            {"ip": core, "isa": isa, "count": 1, "role": "nano",
             "boot_addr": 0x2000},
        ]
        return self.generate(
            name=f"mosaic_{core}",
            cores=cores,
            sram_kb=32,
            boot_rom_kb=2,
            bus="obi",
            tdu=True,
            sched_mode="dynamic",
            peripherals=["uart", "gpio", "timer", "spi"],
            output_path=output_path,
        )

    def list_presets(self) -> SkillResult:
        """List available preset configurations."""
        summaries = {}
        for pname, pcfg in PRESETS.items():
            soc = pcfg["soc"]
            cores_desc = ", ".join(
                f"{c.get('count', 1)}x {c['ip']}" for c in soc["cores"]
            )
            summaries[pname] = {
                "description": f"{soc['name']}: {cores_desc}",
                "sram_kb": soc["memory"]["sram_kb"],
                "tdu": soc["scheduler"]["tdu"],
                "target": soc.get("target", "rtl"),
            }
        return SkillResult(
            ok=True, skill="config-author",
            summary=f"{len(PRESETS)} presets available",
            details={"presets": summaries},
        )
