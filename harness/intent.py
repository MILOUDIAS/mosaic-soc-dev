"""A typed view over a validated SoC config, so consumers stop reading YAML.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This is roadmap M1's fourth exit criterion -- "templates no longer need direct
access to raw YAML" -- and nothing else. It is NOT `DesignIntentIR`, and it is
certainly not `ResolvedSoCIR`: those are twenty-odd types covering pads,
packages, supplies, power domains, coherence and software, and by M1's own exit
criteria ("existing generated artifacts remain semantically equivalent") the
whole refactor ends with zero capability delta.

So this builds the one piece that is load-bearing NOW. `docs/prompt_to_gds_path
.md` argued the IR could be deferred without cost *provided the physical
lowering reads through a small explicit interface rather than raw YAML*, and
then Phase 2 shipped `derive_floorplan(soc.get("cores", []))`. The constraint
that was supposed to preserve the option was never imposed. This imposes it.

WHY A VIEW AND NOT A PARSER
---------------------------
`DesignIntent` does not re-validate. It is constructed from a mapping that
`validate_soc_config` has already accepted, and `from_mapping` runs that
validator rather than inventing a second, drifting notion of what a legal
config is. One validator, one schema, one place to change.

The consequence worth stating: a field absent from the YAML gets the same
default here that the consumers were applying inline, and those defaults are
recorded on the dataclass instead of being repeated at every call site. That
is the actual bug class this closes -- `soc.get("memory", {})` in one module
and `soc.get("memory") or {}` in another disagree when `memory:` is present
but null.
"""

from __future__ import annotations

from dataclasses import fields as _dataclass_fields, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# Defaults live here, once. Previously each consumer inlined its own, and they
# did not all agree.
DEFAULT_BUS = "obi"
DEFAULT_PDK = "gf180mcu"
DEFAULT_TARGET = "rtl"
DEFAULT_SRAM_KB = 32
DEFAULT_SOC_NAME = "mosaic_soc"


@dataclass(frozen=True)
class CoreGroup:
    """One `cores:` entry: an IP, how many of it, and what job it does."""

    ip: str
    count: int = 1
    role: Optional[str] = None
    isa: Optional[str] = None
    # Core-specific knobs (`chunksize`, `conf`, `w`, ...). Kept as a mapping
    # because CORE_SPECS decides which are legal per IP, and duplicating that
    # here would be the second source of truth this module exists to avoid.
    parameters: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, entry: Mapping[str, Any]) -> "CoreGroup":
        known = {"ip", "isa", "count", "role"}
        count = entry.get("count", 1)
        return cls(
            ip=str(entry.get("ip", "")),
            count=count if isinstance(count, int) else 1,
            role=entry.get("role"),
            isa=entry.get("isa"),
            parameters={k: v for k, v in entry.items() if k not in known},
        )


@dataclass(frozen=True)
class Objectives:
    """What the design is asked to achieve, as opposed to what it contains.

    Every field is optional and none is a claim. `target_clock_mhz` is a
    REQUEST carried into the hardening config; STA decides whether it was met.
    """

    target_clock_mhz: Optional[float] = None
    die_um: Optional[float] = None
    max_die_um: Optional[float] = None
    max_area_mm2: Optional[float] = None
    #: Per-design override of the template's GRT_DESIGN_REPAIR_MAX_SLEW_PCT.
    #: Normally absent -- the template's value is right for most designs. It
    #: exists because Block A cannot reach zero max-slew at the shipped 45 on
    #: its mandated 1110 um die (86.5% utilisation, against B's 78% and C's
    #: 68%) and a re-targeted NDR does not clear it, while 32 is a measured
    #: 0 and 0. A design that needs a different margin should SAY so next to
    #: its die and clock, not carry a hand-edited hardening config.
    repair_margin_pct: Optional[int] = None
    #: The density the die is sized at, as a fraction of one. Absent means
    #: "pick the densest target this design size has been shown to route at".
    #: Stating it is what lets a design claim a slot the conservative model
    #: would otherwise refuse, and what lets a searched density become a config
    #: rather than an environment variable.
    target_utilisation: Optional[float] = None

    @property
    def clock_period_ns(self) -> Optional[float]:
        if self.target_clock_mhz is None:
            return None
        return 1000.0 / float(self.target_clock_mhz)

    @classmethod
    def from_mapping(cls, objectives: Optional[Mapping[str, Any]]) -> "Objectives":
        objectives = objectives or {}
        return cls(
            target_clock_mhz=objectives.get("target_clock_mhz"),
            die_um=objectives.get("die_um"),
            max_die_um=objectives.get("max_die_um"),
            max_area_mm2=objectives.get("max_area_mm2"),
            repair_margin_pct=objectives.get("repair_margin_pct"),
            target_utilisation=objectives.get("target_utilisation"),
        )


@dataclass(frozen=True)
class Memory:
    sram_kb: int = DEFAULT_SRAM_KB
    extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_macros(self) -> bool:
        """Whether this design needs SRAM macros placed.

        The area model refuses these outright: macro placement is unmodelled,
        and every calibration point has `sram_kb: 0`.
        """
        return bool(self.sram_kb)

    @classmethod
    def from_mapping(cls, memory: Optional[Mapping[str, Any]]) -> "Memory":
        # `or {}` and `, {}` are NOT the same when the key is present and null,
        # and both spellings existed in the codebase.
        memory = memory or {}
        sram = memory.get("sram_kb", DEFAULT_SRAM_KB)
        return cls(
            sram_kb=sram if isinstance(sram, int) else DEFAULT_SRAM_KB,
            extras={k: v for k, v in memory.items() if k != "sram_kb"},
        )


@dataclass(frozen=True)
class DesignIntent:
    """What the user asked for, typed. Not what the flow resolved it to."""

    name: str = DEFAULT_SOC_NAME
    schema: Optional[str] = None
    pdk: str = DEFAULT_PDK
    target: str = DEFAULT_TARGET
    profile: Optional[str] = None
    bus: str = DEFAULT_BUS
    cores: Tuple[CoreGroup, ...] = ()
    memory: Memory = field(default_factory=Memory)
    objectives: Objectives = field(default_factory=Objectives)
    peripherals: Tuple[str, ...] = ()
    # The remaining validated `soc` keys, unmodelled but not discarded: the
    # generator still reads them, and dropping them here would make this view
    # lossy in a way that is easy to miss and hard to debug.
    raw: Mapping[str, Any] = field(default_factory=dict)

    # ── the questions consumers actually ask ─────────────────────────
    @property
    def hart_count(self) -> int:
        return sum(group.count for group in self.cores)

    @property
    def core_ips(self) -> frozenset:
        return frozenset(group.ip for group in self.cores if group.ip)

    def is_only(self, ip: str) -> bool:
        """True when every core in the design is this IP.

        `estimate_logic_area` asks exactly this, and asked it by set algebra on
        raw dicts: the SCI wrapper dominates a SERV worker (162,000 um2 against
        the core's 21,151), so a different core family has a different
        constant and the calibration does not transfer.
        """
        return self.core_ips == frozenset({ip})

    def groups_with_role(self, role: str) -> Tuple[CoreGroup, ...]:
        return tuple(g for g in self.cores if g.role == role)

    # ── construction ─────────────────────────────────────────────────
    @classmethod
    def from_soc(cls, soc: Mapping[str, Any]) -> "DesignIntent":
        """Build from an ALREADY-VALIDATED `soc` mapping.

        Use `from_config` unless the caller has just validated it itself.
        """
        modelled = {"name", "schema", "pdk", "target", "profile", "bus",
                    "cores", "memory", "objectives", "peripherals"}
        peripherals = soc.get("peripherals") or []
        return cls(
            name=str(soc.get("name", DEFAULT_SOC_NAME)),
            schema=soc.get("schema"),
            pdk=str(soc.get("pdk", DEFAULT_PDK)),
            target=str(soc.get("target", DEFAULT_TARGET)),
            profile=soc.get("profile"),
            bus=str(soc.get("bus", DEFAULT_BUS)),
            cores=tuple(CoreGroup.from_mapping(entry)
                        for entry in (soc.get("cores") or [])
                        if isinstance(entry, Mapping)),
            memory=Memory.from_mapping(soc.get("memory")),
            objectives=Objectives.from_mapping(soc.get("objectives")),
            peripherals=tuple(str(p) for p in peripherals),
            raw={k: v for k, v in soc.items() if k not in modelled},
        )

    @classmethod
    def from_config(
        cls, cfg: Mapping[str, Any], *, allow_sim_only: bool = True
    ) -> Tuple[Optional["DesignIntent"], List[str]]:
        """Validate a whole `{soc: {...}}` document, then view it.

        Returns `(intent, errors)`. Validation is delegated to
        `validate_soc_config` -- this module must never become a second
        opinion about what a legal config is.
        """
        from util.xheep_gen.core_registry import validate_soc_config

        errors = validate_soc_config(cfg, allow_sim_only=allow_sim_only)
        if errors:
            return None, errors
        return cls.from_soc(cfg.get("soc") or {}), []

    def to_soc(self) -> Dict[str, Any]:
        """Round-trip back to the mapping shape, for the generator.

        Only keys that were present survive: adding defaults here would make a
        derived config differ from the one the user wrote, which is exactly
        the silent drift the schema key exists to prevent.
        """
        out: Dict[str, Any] = dict(self.raw)
        out["name"] = self.name
        if self.schema is not None:
            out["schema"] = self.schema
        out["pdk"] = self.pdk
        out["target"] = self.target
        if self.profile is not None:
            out["profile"] = self.profile
        out["bus"] = self.bus
        out["cores"] = [
            {"ip": g.ip, "count": g.count,
             **({"role": g.role} if g.role is not None else {}),
             **({"isa": g.isa} if g.isa is not None else {}),
             **dict(g.parameters)}
            for g in self.cores
        ]
        out["memory"] = {"sram_kb": self.memory.sram_kb,
                         **dict(self.memory.extras)}
        # Every field of Objectives, derived rather than listed: the hand-written
        # list here omitted repair_margin_pct, so a round trip silently dropped
        # Block A's 32 -- the value between zero violations and 28 max-slew plus
        # 14 max-cap on its mandated die. A listing that can fall behind the
        # dataclass will.
        objectives = {f.name: getattr(self.objectives, f.name)
                      for f in _dataclass_fields(self.objectives)
                      if getattr(self.objectives, f.name) is not None}
        if objectives:
            out["objectives"] = objectives
        if self.peripherals:
            out["peripherals"] = list(self.peripherals)
        return out


def coerce(soc: Any) -> DesignIntent:
    """Accept either a `DesignIntent` or a raw `soc` mapping.

    A transition shim, deliberately narrow. Every caller that still passes a
    mapping is a place the boundary has not reached yet; when the count hits
    zero this function goes away.
    """
    if isinstance(soc, DesignIntent):
        return soc
    return DesignIntent.from_soc(soc or {})
