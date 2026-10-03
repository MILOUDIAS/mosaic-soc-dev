"""Authoritative MOSAIC core capabilities and configuration validation.

This module deliberately has no x-heep imports.  It is shared by the RTL
generator and the agent harness, so a configuration accepted by one path is
accepted by the other path with exactly the same semantics.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple, Type


@dataclass(frozen=True)
class ParamSpec:
    """Type and range contract for one core-specific YAML parameter."""

    types: Tuple[Type[Any], ...]
    choices: Optional[FrozenSet[Any]] = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    address: bool = False

    def validate(self, path: str, value: Any) -> Optional[str]:
        if self.address:
            try:
                parsed = value if type(value) is int else int(value, 0)
            except (TypeError, ValueError):
                return f"{path} must be an integer or base-prefixed integer string"
            if parsed < 0 or parsed > 0xFFFFFFFF:
                return f"{path} must be in the 32-bit address range"
            if parsed & 0x3:
                return f"{path} must be 4-byte aligned"
            return None

        # Exact types are intentional: bool is a subclass of int in Python,
        # but accepting `true` for a counter or bank count is a schema bug.
        if type(value) not in self.types:
            expected = " or ".join(t.__name__ for t in self.types)
            return f"{path} must be {expected}, got {type(value).__name__}"
        if self.choices is not None and value not in self.choices:
            return f"{path} must be one of {sorted(self.choices, key=str)}, got {value!r}"
        if self.minimum is not None and value < self.minimum:
            return f"{path} must be >= {self.minimum}, got {value}"
        if self.maximum is not None and value > self.maximum:
            return f"{path} must be <= {self.maximum}, got {value}"
        return None


BOOL = ParamSpec((bool,))
BOOL_BIT = ParamSpec((bool, int), choices=frozenset({False, True, 0, 1}))
NONNEG_INT = ParamSpec((int,), minimum=0)
MHPM_COUNTERS = ParamSpec((int,), minimum=0, maximum=29)
BOOT_ADDR = ParamSpec((int, str), address=True)


@dataclass(frozen=True)
class CoreSpec:
    """Capabilities that the current MOSAIC integration actually supports."""

    name: str
    isas: FrozenSet[str]
    parameters: Mapping[str, ParamSpec] = field(default_factory=dict)
    sci: bool = True
    sim_only: bool = False
    capabilities: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def xlen(self) -> int:
        return 64 if any(isa.startswith("rv64") for isa in self.isas) else 32

    @property
    def fusesoc_flag(self) -> str:
        """Conditional fileset flag for this catalog entry."""

        shared = {
            "serv": "mosaic_serv",
            "qerv": "mosaic_serv",
            "rocket": "mosaic_berkeley",
            "boom": "mosaic_berkeley",
        }
        return shared.get(self.name, f"mosaic_{self.name}")

    @property
    def sci_module(self) -> str:
        """The SCI wrapper module this core instantiates.

        Usually ``<name>_sci``, but a core that shares another core's
        implementation shares its wrapper too.  QERV is SERV with a wider
        datapath: the same vendored servile stack and the same
        W-parameterised wrapper, instantiated with W=4 instead of W=1.
        Deriving this from the core name alone would demand a ``qerv_sci.sv``
        that should not exist, and ``SCI_CORES`` on its own says a wrapper is
        needed without saying which one.

        Same shape and same reason as ``fusesoc_flag`` and
        ``fusesoc_dependency`` below: a catalog entry and a concrete
        implementation are not one to one.
        """

        shared = {"qerv": "serv"}
        return f"{shared.get(self.name, self.name)}_sci"

    @property
    def native_bus(self) -> Optional[str]:
        """The bus the core itself speaks, before its wrapper makes it OBI.

        Diagrams and docs read this so an interface is derived, not drawn by
        hand. No default: a new core without an entry reads None and
        test_socview fails, rather than silently claiming OBI. Each value is
        checked against the wrapper header's own description there too.
        """

        return {
            "cv32e20": "obi", "cv32e40p": "obi", "cv32e40px": "obi",
            "cv32e40x": "obi",
            "serv": "wishbone-lite", "qerv": "wishbone-lite",
            "fazyrv": "wishbone-classic",
            "ibex": "req-gnt",
            "picorv32": "mem-valid-ready",
            "snitch": "reqrsp",
            "hazard3": "ahb-lite",
            "cva6": "axi4",
            "rocket": "tilelink-c", "boom": "tilelink-c",
        }.get(self.name)

    @property
    def fusesoc_dependency(self) -> str:
        """VLNV that closes the concrete implementation of this entry."""

        dependencies = {
            "cv32e20": "openhwgroup:cve2:cve2_top",
            "cv32e40p": "openhwgroup.org:ip:cv32e40p",
            "cv32e40px": "x-heep:ip:cv32e40px",
            "cv32e40x": "openhwgroup.org:ip:cv32e40x",
            "serv": "mosaic:ip:servile",
            "qerv": "mosaic:ip:servile",
            "rocket": "mosaic:ip:berkeley",
            "boom": "mosaic:ip:berkeley",
        }
        return dependencies.get(self.name, f"mosaic:ip:{self.name}")


_COMMON_BOOT = {"boot_addr": BOOT_ADDR}
_CV32_ISAS = frozenset({"rv32imc"})

CORE_SPECS: Dict[str, CoreSpec] = {
    "cv32e20": CoreSpec(
        "cv32e20",
        frozenset({"rv32ec", "rv32emc", "rv32ic", "rv32imc"}),
        {
            **_COMMON_BOOT,
            "rv32e": BOOL_BIT,
            "rv32m": ParamSpec(
                (str,),
                choices=frozenset(
                    {"RV32MNone", "RV32MSlow", "RV32MFast", "RV32MSingleCycle"}
                ),
            ),
        },
        sci=False,
        capabilities=frozenset({"split_obi", "debug", "interrupts", "mhartid"}),
    ),
    "cv32e40p": CoreSpec(
        "cv32e40p",
        _CV32_ISAS,
        {
            **_COMMON_BOOT,
            "num_mhpmcounters": MHPM_COUNTERS,
        },
        sci=False,
        capabilities=frozenset({"split_obi", "debug", "interrupts", "mhartid"}),
    ),
    "cv32e40px": CoreSpec(
        "cv32e40px",
        _CV32_ISAS,
        {
            **_COMMON_BOOT,
            "num_mhpmcounters": MHPM_COUNTERS,
        },
        sci=False,
        capabilities=frozenset(
            {"split_obi", "debug", "interrupts", "xif", "mhartid"}
        ),
    ),
    "cv32e40x": CoreSpec(
        "cv32e40x",
        _CV32_ISAS,
        {**_COMMON_BOOT, "num_mhpmcounters": MHPM_COUNTERS},
        sci=False,
        capabilities=frozenset({"split_obi", "debug", "interrupts", "mhartid"}),
    ),
    "fazyrv": CoreSpec(
        "fazyrv",
        frozenset({"rv32i", "rv32ic"}),
        {
            **_COMMON_BOOT,
            "chunksize": ParamSpec((int,), choices=frozenset({1, 2, 4, 8})),
            "conf": ParamSpec((str,), choices=frozenset({"MIN", "INT", "CSR"})),
            "rftype": ParamSpec(
                (str,),
                choices=frozenset(
                    {"LOGIC", "BRAM", "BRAM_BP", "BRAM_DP", "BRAM_DP_BP"}
                ),
            ),
            "rvc": ParamSpec(
                (str,), choices=frozenset({"NONE", "COMB", "REG", "HYBR"})
            ),
            "memdly1": BOOL_BIT,
        },
        capabilities=frozenset({"split_obi", "timer_interrupt"}),
    ),
    "serv": CoreSpec(
        "serv",
        frozenset({"rv32i", "rv32ic", "rv32im", "rv32imc"}),
        {
            **_COMMON_BOOT,
            "w": ParamSpec((int,), choices=frozenset({1})),
            "with_csr": BOOL_BIT,
            "compressed": BOOL_BIT,
            "mdu": BOOL_BIT,
            "pre_register": BOOL_BIT,
        },
        capabilities=frozenset({"unified_obi", "timer_interrupt"}),
    ),
    "qerv": CoreSpec(
        "qerv",
        frozenset({"rv32i", "rv32ic", "rv32im", "rv32imc"}),
        {
            **_COMMON_BOOT,
            "w": ParamSpec((int,), choices=frozenset({4})),
            "with_csr": BOOL_BIT,
            "compressed": BOOL_BIT,
            "mdu": BOOL_BIT,
            "pre_register": BOOL_BIT,
        },
        capabilities=frozenset({"unified_obi", "timer_interrupt"}),
    ),
    "ibex": CoreSpec(
        "ibex",
        frozenset({"rv32ic", "rv32imc", "rv32ec", "rv32emc"}),
        {**_COMMON_BOOT, "rv32e": BOOL_BIT, "mhpmcounters": MHPM_COUNTERS},
        capabilities=frozenset({"split_obi", "debug", "interrupts", "mhartid"}),
    ),
    "hazard3": CoreSpec(
        "hazard3",
        frozenset({"rv32imc"}),
        _COMMON_BOOT,
        capabilities=frozenset({"split_obi", "interrupts", "mhartid"}),
    ),
    "picorv32": CoreSpec(
        "picorv32",
        frozenset({"rv32i", "rv32im", "rv32imc"}),
        {
            **_COMMON_BOOT,
            "counters": BOOL_BIT,
            "barrel_shifter": BOOL_BIT,
            "compressed": BOOL_BIT,
            "mul": BOOL_BIT,
            "div": BOOL_BIT,
        },
        capabilities=frozenset({"unified_obi"}),
    ),
    "snitch": CoreSpec(
        "snitch",
        frozenset({"rv32i"}),
        {**_COMMON_BOOT, "rve": BOOL_BIT, "rvm": BOOL_BIT},
        capabilities=frozenset({"split_obi", "mhartid"}),
    ),
    "cva6": CoreSpec(
        "cva6",
        frozenset({"rv32imc"}),
        _COMMON_BOOT,
        sim_only=True,
        capabilities=frozenset(
            {"unified_obi", "cached", "interrupts", "mhartid"}
        ),
    ),
    "rocket": CoreSpec(
        "rocket",
        frozenset({"rv64imc"}),
        _COMMON_BOOT,
        sim_only=True,
        capabilities=frozenset({"unified_obi", "cached", "interrupts"}),
    ),
    "boom": CoreSpec(
        "boom",
        frozenset({"rv64imc"}),
        _COMMON_BOOT,
        sim_only=True,
        capabilities=frozenset({"unified_obi", "cached", "interrupts"}),
    ),
}

VALID_CORE_IPS = frozenset(CORE_SPECS)
SCI_CORES = frozenset(name for name, spec in CORE_SPECS.items() if spec.sci)
SIM_ONLY_CORES = frozenset(name for name, spec in CORE_SPECS.items() if spec.sim_only)
VALID_ROLES = frozenset({"titan", "atlas", "nano"})
VALID_BUS = frozenset({"obi", "log", "floonoc"})
VALID_SCHED_MODES = frozenset({"static", "dynamic", "power-aware"})
# `ihp-sg13g2` was added 2026-09-08 as the second PDK. Its collateral is on
# disk (an IHP Open PDK tree with its own libs.tech/librelane config) and its
# site geometry and corner names are declared in harness/physical/technology.py
# -- but nothing has been hardened on it, so it stays RTL/simulation-only via
# TAPEOUT_PDK below. Naming it here is what lets a config reach the generator
# paths that have never seen a non-GF180 value; that is the point of adding it.
VALID_PDKS = frozenset({"gf180mcu", "sky130", "ihp-sg13g2"})
VALID_PROFILES = frozenset({"soc", "testbench"})
VALID_TARGETS = frozenset({"rtl", "simulation", "tapeout"})
VALID_PERIPHERALS = frozenset({"uart", "gpio", "timer", "spi", "i2c", "serial_link"})
VALID_ISAS = frozenset(isa for spec in CORE_SPECS.values() for isa in spec.isas)

# This is deliberately a *qualified implementation matrix*, not a list of
# syntactically valid generator settings.  LOG, FlooNoC, Sky130, and alternate
# memory sizes remain useful RTL/simulation configurations, but the repository
# does not currently ship the physical collateral needed to call them tapeout
# targets.  Expanding this matrix requires physical-flow evidence and tests.
TAPEOUT_PDK = "gf180mcu"
TAPEOUT_BUS = "obi"
# Block A (see docs/rtl_freeze_blocka.md): no on-chip SRAM pool, a 128 B
# flip-flop scratchpad, 1 KB boot ROM, code executed XIP from external flash.
TAPEOUT_SRAM_KB = 0
TAPEOUT_BOOT_ROM_KB = 1
TAPEOUT_SCRATCHPAD_BYTES = 128
MIN_BOOT_IMAGE_BYTES = 0x400

# x-heep's external-slave window, from core_v_mini_mcu.h:
#   #define EXT_SLAVE_START_ADDRESS 0xF0000000
#   #define EXT_SLAVE_SIZE          0x01000000
# The external-memory profile (memory.sram_kb: 0) places off-chip SRAM here.
EXT_SLAVE_BASE = 0xF000_0000
EXT_SLAVE_SIZE_KB = 0x0100_0000 // 1024

# The memory-mapped SPI-flash execute-in-place window, from configs/general.hjson
# `flash_mem` and core_v_mini_mcu.h:
#   #define FLASH_MEM_START_ADDRESS 0x40000000
#   #define FLASH_MEM_SIZE          0x01000000
# Code is read-only and non-volatile, so a reset vector here is valid with no
# initialisation beyond enabling the window -- which the boot ROM already does
# in `_execute_from_flash`. This is what lets workers run with no RAM at all.
FLASH_XIP_BASE = 0x4000_0000
FLASH_XIP_SIZE = 0x0100_0000
TAPEOUT_CORE_MATRIX = (
    {
        "ip": "serv",
        "isa": "rv32ic",
        "count": 1,
        "role": "titan",
        "with_csr": 1,
        "compressed": 1,
    },
    {
        "ip": "serv",
        "isa": "rv32i",
        "count": 1,
        "role": "atlas",
        "boot_addr": 0x4001_0000,
        "with_csr": 0,
    },
)

# The platform blocks Block A removes. A design carrying any of them has no
# physical evidence behind it, so it is not the qualified part even if its core
# list matches.
# What each knob defaults to when a config omits it -- so an omitted knob is
# judged on its effective value, not treated as if it matched.
_TAPEOUT_PLATFORM_DEFAULTS = {
    "dma": "idma",
    "debug": True,
    "plic": True,
    "spi_mode": "full",
    "multicore_timer": True,
    "gpio_ao": True,
    "ao_rv_timer": True,
    "ao_fast_intr": True,
}

TAPEOUT_PLATFORM = {
    "dma": "none",
    "debug": False,
    "plic": False,
    "spi_mode": "xip_only",
    "multicore_timer": False,
    "gpio_ao": False,
    "ao_rv_timer": False,
    "ao_fast_intr": False,
}

TAPEOUT_PERIPHERALS = frozenset({"uart"})

# The public MOSAIC schema fixes iDMA at two streams. Each stream contributes
# exactly two OBI masters (read and write); the legacy simple-DMA address
# master is not part of an explicit MOSAIC topology.
# DMA engine selection (soc.dma).
#   idma   pulp-platform iDMA: one OBI read/write pair per stream. The default,
#          and the CLAUDE.md design choice for every explicit topology.
#   xheep  x-heep's simple DMA: a read/write/addr triplet.
#   none   no DMA at all. The AO subsystem skips the instantiation entirely
#          (ao_peripheral_subsystem.sv.tpl guards on get_is_included()), and
#          num_bus_masters() drops its master ports, so the crossbar shrinks
#          with it. Measured saving on a 3-core XIP SoC: 0.319 mm2 in GF180,
#          8.2% of the die -- see docs/area_study_gf180_min_soc.md.
VALID_DMA = frozenset({"idma", "xheep", "none"})

# SPI subsystem flavour.
#   full      OpenTitan spi_host + the obi_spimemio XIP reader (x-heep default)
#   xip_only  obi_spimemio ONLY -- read-only execute-in-place, no SPI host
# The XIP reader is 0.030 mm2; the whole subsystem with the host is 0.717 mm2,
# the largest single block in the minimum-area SoC. Dropping the host means the
# part can still boot and execute from flash but cannot drive SPI as a
# general-purpose host, and cannot WRITE to the flash.
VALID_SPI_MODE = frozenset({"full", "xip_only"})

STANDARD_DMA_MASTER_PORTS = 2
IDMA_OBI_PORTS_PER_STREAM = 2
MAX_LOG_BANKS = 32

PERIPHERAL_EXPANSION: Mapping[str, FrozenSet[str]] = {
    "uart": frozenset({"uart"}),
    "gpio": frozenset({"gpio"}),
    "timer": frozenset({"rv_timer"}),
    "spi": frozenset({"spi_host"}),
    "i2c": frozenset({"i2c"}),
    # The serial-link data window requires its register and receive-FIFO blocks.
    "serial_link": frozenset(
        {"serial_link", "serial_link_reg", "serial_link_receiver_fifo"}
    ),
}
MANDATORY_USER_PERIPHERALS = frozenset({"rv_plic"})
MULTICORE_USER_PERIPHERALS = frozenset({"rv_timer"})

SOC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _unknown_keys(path: str, value: Mapping[str, Any], allowed: FrozenSet[str]) -> List[str]:
    return [f"{path}.{key}: unknown key" for key in sorted(set(value) - set(allowed))]


# ── schema versioning ────────────────────────────────────────────────
#
# `allowed_soc` below is a single evolving frozenset, so until now a config
# could not state which schema it was written against. A config authored months
# ago and one authored today are indistinguishable, and a config rots silently
# as the set changes underneath it: keys are added, meanings tighten, and
# nothing records which rules it was actually accepted under.
#
# `soc.schema` fixes that with one key. The discipline is OpenADA's, whose
# operation profiles are immutable and versioned by filename -- changing a
# required field, a value's meaning, or a closed shape requires a NEW
# identifier, and the old one keeps working. Concretely, when v2 lands:
#
#   * `mosaic/v1` stays in this table pointing at a FROZEN copy of today's
#     rules, so a v1 config keeps validating exactly as it does now;
#   * `mosaic/v2` gets its own entry;
#   * a config that says neither is refused by name.
#
# Omitting the key means `mosaic/v1`, so every config in the repo today is
# already valid and nothing needs a migration.
CURRENT_SCHEMA = "mosaic/v1"
KNOWN_SCHEMAS: FrozenSet[str] = frozenset({CURRENT_SCHEMA})


# ── objectives ───────────────────────────────────────────────────────
#
# The schema is otherwise purely STRUCTURAL: it says what is in the SoC and
# nothing about what the SoC must achieve. That gap is why a hardening config
# cannot be generated -- there is no field a clock period could be derived
# from, so Block A's `CLOCK_PERIOD: 100` was a human decision recorded in a
# comment.
#
# `objectives` is the smallest addition that closes it. Deliberately not a
# performance model: `target_clock_mhz` is a REQUEST that the physical flow
# either meets or does not, and STA remains the only thing that decides which.
# Budgets are optional and, when present, are checked against measured results
# rather than used to prune anything.
# `die_um` is a MANDATE (an MPW slot the block must match exactly); the max_*
# keys are BOUNDS the derived die must fit inside. Different meanings, so
# different keys -- a slot delivery is not "at most this big", it is this big.
# `repair_margin_pct` is a per-design override of the signoff template's
# GRT_DESIGN_REPAIR_MAX_SLEW_PCT. It sits with the die and the clock because
# it is a statement about THIS design: Block A cannot reach zero max-slew at
# the shipped 45 on its mandated 1110 um die, while B and C can.
# `target_utilisation` is the density the die is sized at, and it belongs with
# the die because it is what makes a die achievable: Block A's mandated 1110 um
# slot is REFUSED at the model's conservative ~81% and emitted at the 82.3% its
# shipping run demonstrates. Until this key existed the number could only arrive
# as MOSAIC_HARDEN_UTIL, so a searched density -- Block C's 0.74, worth 11.9% of
# its die -- could not be written down as a design decision at all.
OBJECTIVE_KEYS = frozenset(
    {"target_clock_mhz", "die_um", "max_die_um", "max_area_mm2",
     "repair_margin_pct", "target_utilisation"}
)


def objectives_errors(soc: Mapping[str, Any]) -> List[str]:
    """Validate the optional ``soc.objectives`` mapping."""
    objectives = soc.get("objectives")
    if objectives is None:
        return []
    if not isinstance(objectives, Mapping):
        return ["soc.objectives must be a mapping"]
    errors = _unknown_keys("soc.objectives", objectives, OBJECTIVE_KEYS)
    for key in sorted(OBJECTIVE_KEYS & set(objectives)):
        value = objectives[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"soc.objectives.{key} must be a number")
        elif value <= 0:
            errors.append(f"soc.objectives.{key} must be greater than 0")
        elif key == "repair_margin_pct" and not (
                isinstance(value, int) and 0 < value < 100):
            # A percentage, and an integer one: LibreLane's key is an int, and
            # 45.0 would render as "45.0" into a config that expects 45.
            errors.append(
                "soc.objectives.repair_margin_pct must be a whole percentage "
                "between 1 and 99")
        elif key == "target_utilisation" and not value < 1:
            # A fraction of one. 82 for 0.82 asks for a die 82x denser than its
            # cells; the optimizer's knob guards the same slip.
            errors.append(
                "soc.objectives.target_utilisation is a fraction of one "
                "(0.82, not 82)")
    return errors


def schema_errors(soc: Mapping[str, Any]) -> List[str]:
    """Validate ``soc.schema``, refusing an unknown version by name."""
    declared = soc.get("schema", CURRENT_SCHEMA)
    if not isinstance(declared, str) or not declared.strip():
        return ["soc.schema must be a string such as 'mosaic/v1'"]
    declared = declared.strip()
    if declared not in KNOWN_SCHEMAS:
        return [
            f"soc.schema {declared!r} is not a schema this generator "
            f"implements (known: {sorted(KNOWN_SCHEMAS)}). This config was "
            "written for a different version of the format; validating it "
            "against the current rules would report misleading errors."
        ]
    return []


def target_capability_errors(soc: Mapping[str, Any]) -> List[str]:
    """Return implementation-target errors for an otherwise public SoC map.

    ``target: rtl`` (the default) and ``target: simulation`` describe generator
    products and therefore retain the full schema-supported design space.
    ``target: tapeout`` is intentionally narrower: it is a claim that the
    selected combination is covered by the checked-in GF180 implementation
    flow.  A later LibreLane preflight also requires the concrete bound RTL and
    SRAM macro bundle; schema validation alone never claims signoff completion.
    """

    target = soc.get("target", "rtl")
    if not isinstance(target, str) or target not in VALID_TARGETS:
        return [f"soc.target {target!r} not in {sorted(VALID_TARGETS)}"]
    if target != "tapeout":
        return []

    errors: List[str] = []
    profile = soc.get("profile", "soc")
    pdk = soc.get("pdk", "gf180mcu")
    bus = soc.get("bus", "obi")
    memory = soc.get("memory", {})

    if profile != "soc":
        errors.append("soc.target 'tapeout' requires soc.profile: soc")
    if pdk != TAPEOUT_PDK:
        errors.append(
            "soc.target 'tapeout' is qualified only for pdk 'gf180mcu'; "
            f"{pdk!r} is RTL/simulation-only"
        )
    if bus != TAPEOUT_BUS:
        errors.append(
            "soc.target 'tapeout' is qualified only for bus 'obi'; "
            f"{bus!r} is RTL/simulation-only until physical qualification"
        )

    if isinstance(memory, Mapping):
        sram_kb = memory.get("sram_kb", 32)
        boot_rom_kb = memory.get("boot_rom_kb", 2)
        if sram_kb != TAPEOUT_SRAM_KB:
            errors.append(
                f"soc.target 'tapeout' requires memory.sram_kb={TAPEOUT_SRAM_KB}; "
                f"{sram_kb!r} has no qualified GF180 physical configuration"
            )
        if boot_rom_kb != TAPEOUT_BOOT_ROM_KB:
            errors.append(
                f"soc.target 'tapeout' requires memory.boot_rom_kb={TAPEOUT_BOOT_ROM_KB}; "
                f"{boot_rom_kb!r} has no qualified GF180 physical configuration"
            )
        scratchpad = memory.get("scratchpad_bytes", TAPEOUT_SCRATCHPAD_BYTES)
        if scratchpad != TAPEOUT_SCRATCHPAD_BYTES:
            errors.append(
                "soc.target 'tapeout' requires memory.scratchpad_bytes="
                f"{TAPEOUT_SCRATCHPAD_BYTES}; {scratchpad!r} was not hardened"
            )

    cores = soc.get("cores", [])
    if isinstance(cores, list):
        normalized_cores: List[Dict[str, Any]] = []
        for entry in cores:
            if not isinstance(entry, Mapping):
                continue
            normalized = dict(entry)
            normalized["count"] = normalized.get("count", 1)
            if "boot_addr" in normalized:
                try:
                    normalized["boot_addr"] = (
                        normalized["boot_addr"]
                        if type(normalized["boot_addr"]) is int
                        else int(normalized["boot_addr"], 0)
                    )
                except (TypeError, ValueError):
                    pass
            normalized_cores.append(normalized)
        if normalized_cores != list(TAPEOUT_CORE_MATRIX):
            errors.append(
                "soc.target 'tapeout' is qualified only for the Chipathon "
                "Block A topology (1x SERV TITAN rv32ic with CSRs, 1x SERV "
                "ATLAS rv32i without CSRs at 0x40010000)"
            )

    # The platform blocks Block A removes.
    for key, expected in sorted(TAPEOUT_PLATFORM.items()):
        actual = soc.get(key, _TAPEOUT_PLATFORM_DEFAULTS.get(key))
        if actual != expected:
            errors.append(
                f"soc.target 'tapeout' requires soc.{key}={expected!r}; "
                f"{actual!r} was not part of the hardened Block A design"
            )
    scheduler = soc.get("scheduler", {})
    if not isinstance(scheduler, Mapping) or scheduler.get("tdu") is not True:
        errors.append("soc.target 'tapeout' requires scheduler.tdu=true")
    if not isinstance(scheduler, Mapping) or scheduler.get("mode") != "dynamic":
        errors.append("soc.target 'tapeout' requires scheduler.mode='dynamic'")
    peripherals = soc.get("peripherals", [])
    if not isinstance(peripherals, list) or set(peripherals) != TAPEOUT_PERIPHERALS:
        errors.append(
            "soc.target 'tapeout' requires peripherals "
            + "/".join(sorted(TAPEOUT_PERIPHERALS))
        )
    return errors


def validate_soc_config(cfg: Any, allow_sim_only: bool = True) -> List[str]:
    """Validate the complete public YAML schema and topology invariants."""

    errors: List[str] = []
    if not isinstance(cfg, dict):
        return ["configuration must be a mapping"]
    errors.extend(_unknown_keys("root", cfg, frozenset({"soc"})))
    soc = cfg.get("soc")
    if not isinstance(soc, dict):
        return errors + ["Missing top-level 'soc' mapping"]

    allowed_soc = frozenset(
        {
            "schema", "objectives",
            "name", "pdk", "profile", "target", "cores", "memory", "bus", "bus_opts",
            "scheduler", "peripherals", "dma", "debug", "plic", "spi_mode",
            "multicore_timer", "gpio_ao", "ao_rv_timer", "ao_fast_intr",
        }
    )
    errors.extend(_unknown_keys("soc", soc, allowed_soc))

    # Checked before anything else: a config that declares a schema we do not
    # implement must be refused BY NAME, not diagnosed as twenty unknown keys
    # from a validator that was never meant to read it.
    errors.extend(schema_errors(soc))
    errors.extend(objectives_errors(soc))

    name = soc.get("name", "mosaic_soc")
    if not isinstance(name, str) or not SOC_NAME_RE.fullmatch(name):
        errors.append(
            "soc.name must start with a lowercase letter and contain only "
            "lowercase letters, digits, or underscores (maximum 64 characters)"
        )
    pdk = soc.get("pdk", "gf180mcu")
    if not isinstance(pdk, str) or pdk not in VALID_PDKS:
        errors.append(f"soc.pdk {pdk!r} not in {sorted(VALID_PDKS)}")
    profile = soc.get("profile", "soc")
    if not isinstance(profile, str) or profile not in VALID_PROFILES:
        errors.append(f"soc.profile {profile!r} not in {sorted(VALID_PROFILES)}")
    target = soc.get("target", "rtl")
    if not isinstance(target, str) or target not in VALID_TARGETS:
        errors.append(f"soc.target {target!r} not in {sorted(VALID_TARGETS)}")

    cores = soc.get("cores")
    if not isinstance(cores, list) or not cores:
        errors.append("At least one core group required in 'cores'")
        cores = []

    total_cores = 0
    roles: List[str] = []
    for index, entry in enumerate(cores):
        path = f"cores[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{path} must be a mapping")
            continue
        ip = entry.get("ip")
        if not isinstance(ip, str) or ip not in CORE_SPECS:
            errors.append(f"{path}.ip {ip!r} not in {sorted(CORE_SPECS)}")
            spec = None
        else:
            spec = CORE_SPECS[ip]
            if spec.sim_only and profile != "testbench":
                errors.append(
                    f"{path}.ip '{ip}' is demo/simulation-only and requires "
                    "soc.profile: testbench"
                )
            if spec.sim_only and target == "tapeout":
                errors.append(
                    f"{path}.ip '{ip}' is simulation-only and cannot be used with "
                    "soc.target: tapeout"
                )
            if not allow_sim_only and spec.sim_only:
                errors.append(
                    f"{path}.ip '{ip}' is SIMULATION-ONLY and is not allowed "
                    "in a tapeout configuration"
                )

        allowed_core = {"ip", "isa", "count", "role"}
        if spec is not None:
            allowed_core.update(spec.parameters)
        errors.extend(_unknown_keys(path, entry, frozenset(allowed_core)))

        count = entry.get("count", 1)
        if type(count) is not int or count < 1:
            errors.append(f"{path}.count must be an integer >= 1, got {count!r}")
        else:
            total_cores += count

        role = entry.get("role")
        if not isinstance(role, str) or role not in VALID_ROLES:
            errors.append(f"{path}.role {role!r} not in {sorted(VALID_ROLES)}")
        else:
            roles.append(role)
            if ip in {"rocket", "boom"} and role == "titan":
                # The extracted tile exposes only one hart-id bit.  A singleton
                # leading tile is nevertheless a truthful simulation controller:
                # its uncached windows reach shared state, TDU and soc_ctrl.
                if index != 0 or count != 1:
                    errors.append(
                        f"{path}: {ip} may be a TITAN only as one leading "
                        "simulation hart at cores[0]"
                    )

        isa = entry.get("isa")
        if not isinstance(isa, str):
            errors.append(f"{path}.isa is required and must be a string")
        elif spec is not None and isa not in spec.isas:
            errors.append(
                f"{path}.isa '{isa}' is not supported by {ip}; valid: {sorted(spec.isas)}"
            )

        if spec is not None:
            for parameter, parameter_spec in spec.parameters.items():
                if parameter in entry:
                    error = parameter_spec.validate(
                        f"{path}.{parameter}", entry[parameter]
                    )
                    if error:
                        errors.append(error)

            # Cross-field capability constraints: the ISA declaration is a
            # contract, not documentation.  Reject settings that would build
            # a different instruction set than the one declared.
            isa_extensions = isa[4:] if isinstance(isa, str) and len(isa) > 4 else ""
            if ip == "fazyrv":
                rvc = entry.get("rvc", "NONE")
                if ("c" in isa_extensions) != (rvc != "NONE"):
                    errors.append(f"{path}.isa and {path}.rvc disagree on compressed ISA")
                if entry.get("conf", "CSR") == "CSR" and entry.get(
                    "rftype", "BRAM_DP_BP"
                ) == "LOGIC":
                    errors.append(f"{path}: FazyRV CONF=CSR cannot use RFTYPE=LOGIC")
                # MEMDLY1=1 builds a core that asserts stb/cyc for exactly one
                # cycle and ignores the ack for flow control
                # (fazyrv_cntrl.sv: `if (MEMDLY1 | imem_ack_i) state_n = DECODE`).
                # It is only correct against a memory that ALWAYS answers in one
                # cycle. On a shared OBI crossbar a slower slave lets the IR load
                # pulse land mid-execution and overwrite the decoded instruction
                # registers, silently corrupting results. fazyrv_sci also presents
                # a 0-latency view by clock-stalling, so there is no latency for
                # MEMDLY1 to describe. Reject it here rather than after a
                # two-minute RTL build.
                if entry.get("memdly1", False):
                    errors.append(
                        f"{path}: FazyRV memdly1 is unsupported — fazyrv_sci "
                        "presents a 0-latency Wishbone view by clock-stalling, "
                        "and MEMDLY1=1 makes the core ignore the ack and assume "
                        "a fixed 1-cycle memory the OBI crossbar cannot promise"
                    )
            elif ip in {"serv", "qerv"}:
                if ("c" in isa_extensions) != bool(entry.get("compressed", False)):
                    errors.append(
                        f"{path}.isa and {path}.compressed disagree on compressed ISA"
                    )
                if ("m" in isa_extensions) != bool(entry.get("mdu", False)):
                    errors.append(f"{path}.isa and {path}.mdu disagree on M extension")
            elif ip == "picorv32":
                if ("c" in isa_extensions) != bool(entry.get("compressed", False)):
                    errors.append(
                        f"{path}.isa and {path}.compressed disagree on compressed ISA"
                    )
                has_mul = bool(entry.get("mul", False))
                has_div = bool(entry.get("div", False))
                has_muldiv = has_mul and has_div
                if has_mul != has_div:
                    errors.append(
                        f"{path}: standard M requires both mul=true and div=true"
                    )
                if ("m" in isa_extensions) != has_muldiv:
                    errors.append(f"{path}.isa and mul/div parameters disagree on M extension")
            elif ip == "ibex":
                is_rv32e = isa.startswith("rv32e")
                if "rv32e" in entry and is_rv32e != bool(entry["rv32e"]):
                    errors.append(f"{path}.isa and {path}.rv32e disagree on register profile")
            elif ip == "cv32e20":
                is_rv32e = isa.startswith("rv32e")
                has_m = "m" in isa_extensions
                if "rv32e" in entry and is_rv32e != bool(entry["rv32e"]):
                    errors.append(f"{path}.isa and {path}.rv32e disagree on register profile")
                if "rv32m" in entry:
                    mode_has_m = entry["rv32m"] != "RV32MNone"
                    if has_m != mode_has_m:
                        errors.append(f"{path}.isa and {path}.rv32m disagree on M extension")
            elif ip == "snitch" and (
                bool(entry.get("rve", False)) or bool(entry.get("rvm", False))
            ):
                errors.append(
                    f"{path}: current Snitch integration is RV32I-only; RVE/RVM are unavailable"
                )

    dma_kind = soc.get("dma", "idma")
    if dma_kind not in VALID_DMA:
        errors.append(
            f"soc.dma {dma_kind!r} not in {sorted(VALID_DMA)}"
        )
    elif dma_kind == "xheep" and total_cores > 1:
        # Not a knob: ao_peripheral_subsystem.sv.tpl keys the DMA flavour off
        # is_mc, and the two flavours have DIFFERENT PORT GEOMETRY -- the
        # simple DMA adds a third `dma_addr_*` master per stream
        # (DMA_OBI_PORTS_PER_STREAM 3 vs the iDMA's 2), which changes the
        # module port list, core_v_mini_mcu wiring, system_bus, and the
        # SYSTEM_XBAR_NMASTER index map. Accepting this value and silently
        # instantiating the iDMA anyway would be worse than refusing it.
        errors.append(
            "soc.dma 'xheep' is not wired for multi-core configs "
            f"({total_cores} cores): the simple DMA's third address master "
            "per stream changes the crossbar index map. Use 'idma', or "
            "'none' if the design issues no bulk copies."
        )

    # Debug subsystem and PLIC. Both default to present; both are pure area
    # when a design does not use them. Removing the PLIC removes the ONLY path
    # from a peripheral interrupt (UART/SPI/GPIO/I2C) to a hart -- CLINT timer
    # and software interrupts are unaffected, which is why a TDU-driven wake
    # design can drop it. Removing the debug subsystem removes JTAG/DTM and the
    # RISC-V debug module: no halt, no step, no external bring-up access.
    for key in ("debug", "plic", "multicore_timer", "gpio_ao",
                "ao_rv_timer", "ao_fast_intr"):
        value = soc.get(key, True)
        if type(value) is not bool:
            errors.append(
                f"soc.{key} must be a boolean, got {type(value).__name__}"
            )

    spi_mode = soc.get("spi_mode", "full")
    if spi_mode not in VALID_SPI_MODE:
        errors.append(f"soc.spi_mode {spi_mode!r} not in {sorted(VALID_SPI_MODE)}")

    mem = soc.get("memory", {})
    if not isinstance(mem, dict):
        errors.append("memory must be a mapping")
        mem = {}
    errors.extend(
        _unknown_keys(
            "memory",
            mem,
            frozenset({"sram_kb", "boot_rom_kb", "external", "scratchpad_bytes"}),
        )
    )
    sram_kb = mem.get("sram_kb", 32)
    # sram_kb == 0 declares that the SoC carries no on-chip SRAM macros. Two
    # shapes are legal (docs/external_memory_boot_design.md, Option C):
    #
    #   XIP-only        no memory.external. Every hart executes in place from
    #                   the flash window and NOTHING writes to memory: no
    #                   stack, no .data, no .bss. Liveness/telemetry has to go
    #                   to peripheral registers. This is the bring-up shape --
    #                   it needs no memory controller at all.
    #
    #   external RAM    memory.external declared. Code still XIPs from flash;
    #                   the off-chip region carries only writable data, which
    #                   crt0 initialises the ordinary way (zero .bss, copy
    #                   .data from flash). No image staging is required
    #                   because code is already resident in non-volatile flash.
    #
    # Both rest on the same fact: code is read-only, so it never needs to be
    # copied anywhere.
    if sram_kb == 0:
        pass  # both shapes are legal; boot_addr placement decides which
    elif type(sram_kb) is not int or not 8 <= sram_kb <= 512:
        errors.append(
            "memory.sram_kb must be 0 (external-memory profile) or an integer "
            f"from 8 to 512, got {sram_kb!r}"
        )
    elif sram_kb & (sram_kb - 1):
        errors.append(f"memory.sram_kb must be a power of two, got {sram_kb}")

    external = mem.get("external")
    external_range: Optional[Tuple[int, int]] = None
    if external is not None:
        if not isinstance(external, dict):
            errors.append("memory.external must be a mapping")
        else:
            errors.extend(
                _unknown_keys("memory.external", external, frozenset({"base", "size_kb"}))
            )
            raw_base = external.get("base", EXT_SLAVE_BASE)
            try:
                base = raw_base if type(raw_base) is int else int(raw_base, 0)
            except (TypeError, ValueError):
                base = None
                errors.append(f"memory.external.base {raw_base!r} is not an address")
            size_kb = external.get("size_kb", EXT_SLAVE_SIZE_KB)
            if type(size_kb) is not int or not 1 <= size_kb <= EXT_SLAVE_SIZE_KB:
                errors.append(
                    "memory.external.size_kb must be an integer from 1 to "
                    f"{EXT_SLAVE_SIZE_KB}, got {size_kb!r}"
                )
            elif base is not None:
                if not EXT_SLAVE_BASE <= base < EXT_SLAVE_BASE + EXT_SLAVE_SIZE_KB * 1024:
                    errors.append(
                        f"memory.external.base 0x{base:08x} must select the "
                        f"external-slave window [0x{EXT_SLAVE_BASE:08x}, "
                        f"0x{EXT_SLAVE_BASE + EXT_SLAVE_SIZE_KB * 1024:08x})"
                    )
                else:
                    external_range = (base, base + size_kb * 1024)

    # ── memory.scratchpad_bytes ──────────────────────────────────────
    # A sub-KiB on-chip writable scratchpad for the Option C profiles. It exists
    # because `memory.sram_kb` is integer KiB and the interesting size is below
    # 1 KiB: the smallest GF180 SRAM macros are 64/128/256/512 BYTES, and
    # gf180mcu_fd_ip_sram__sram512x8m8wm1 measures 0.209 mm² for 512 B against
    # 0.419 mm² for a 1 KiB pair. At a 1.25 mm² budget that difference is 17%
    # of the die, so rounding it up in the schema would quietly cost real area.
    #
    # This is DATA-ONLY storage. Code still executes in place from flash
    # (docs/external_memory_boot_design.md, Option C); the scratchpad exists so
    # the C runtime has a stack -- Cheshire's LLC-as-SPM lesson, scaled down.
    scratchpad_bytes = mem.get("scratchpad_bytes")
    if scratchpad_bytes is not None:
        if type(scratchpad_bytes) is not int or scratchpad_bytes <= 0:
            errors.append(
                "memory.scratchpad_bytes must be a positive integer, "
                f"got {scratchpad_bytes!r}"
            )
        elif scratchpad_bytes >= 1024:
            errors.append(
                f"memory.scratchpad_bytes {scratchpad_bytes} is 1 KiB or larger; "
                "use memory.sram_kb for that. This field exists only for "
                "sub-KiB scratchpads, where integer KiB cannot express the size"
            )
        elif scratchpad_bytes & (scratchpad_bytes - 1):
            errors.append(
                "memory.scratchpad_bytes must be a power of two, got "
                f"{scratchpad_bytes}"
            )
        elif scratchpad_bytes < 64:
            errors.append(
                f"memory.scratchpad_bytes {scratchpad_bytes} is below the "
                "smallest realisable macro (64 B)"
            )
        elif sram_kb != 0:
            errors.append(
                "memory.scratchpad_bytes requires memory.sram_kb: 0 — a design "
                "with an on-chip SRAM pool should size it with sram_kb rather "
                "than carry a second, separate writable memory"
            )

    boot_rom_kb = mem.get("boot_rom_kb", 2)
    if type(boot_rom_kb) is not int or not 1 <= boot_rom_kb <= 64:
        errors.append(
            f"memory.boot_rom_kb must be an integer from 1 to 64, got {boot_rom_kb!r}"
        )
    elif boot_rom_kb & (boot_rom_kb - 1):
        errors.append(f"memory.boot_rom_kb must be a power of two, got {boot_rom_kb}")

    # Resolve software load slots at the schema boundary. Mandatory bundle
    # generation uses these values for linkers and boot-image manifests, so a
    # configuration accepted here must not fail later in software_gen.
    boot_slots: Dict[int, List[Tuple[str, str]]] = {}
    for index, entry in enumerate(cores):
        if not isinstance(entry, dict):
            continue
        ip = entry.get("ip")
        isa = entry.get("isa")
        role = entry.get("role")
        if profile == "soc" and role in {"atlas", "nano"} and "boot_addr" not in entry:
            errors.append(
                f"cores[{index}].boot_addr is required for an AMP worker in profile 'soc'"
            )
        if profile == "soc" and role == "titan" and "boot_addr" in entry:
            errors.append(
                f"cores[{index}].boot_addr must be omitted for a production TITAN; "
                "the reset vector must remain the boot ROM"
            )
        if ip in {"rocket", "boom"} and "boot_addr" not in entry:
            errors.append(
                f"cores[{index}].boot_addr is required for the {ip} translated code window"
            )
        raw_address = entry.get("boot_addr", 0x180)
        try:
            address = raw_address if type(raw_address) is int else int(raw_address, 0)
        except (TypeError, ValueError):
            continue  # ParamSpec already reports the field-level error.
        # A worker's reset vector must land in a declared *executable* region.
        # Historically that meant on-chip SRAM only; the external-memory profile
        # adds memory.external, so the check is now "any declared region".
        if type(sram_kb) is int and 8 <= sram_kb <= 512:
            if address < 0 or address >= sram_kb * 1024:
                errors.append(
                    f"cores[{index}].boot_addr 0x{address:08x} must select SRAM "
                    f"[0x00000000, 0x{sram_kb * 1024:08x})"
                )
        elif sram_kb == 0 and "boot_addr" in entry:
            # Only an explicit reset vector is region-checked. A TITAN has none
            # (it enters the platform BOOT_ADDR, i.e. the boot ROM), so its
            # 0x180 placeholder must not be measured against external memory.
            #
            # With no on-chip SRAM a worker may reset into the flash XIP window
            # (its code is already resident and read-only) or into a declared
            # external RAM region. Anything else has no backing store.
            in_flash = FLASH_XIP_BASE <= address < FLASH_XIP_BASE + FLASH_XIP_SIZE
            in_external = (
                external_range is not None
                and external_range[0] <= address < external_range[1]
            )
            if not (in_flash or in_external):
                allowed = (
                    f"the flash XIP window [0x{FLASH_XIP_BASE:08x}, "
                    f"0x{FLASH_XIP_BASE + FLASH_XIP_SIZE:08x})"
                )
                if external_range is not None:
                    allowed += (
                        f" or the declared external region "
                        f"[0x{external_range[0]:08x}, 0x{external_range[1]:08x})"
                    )
                errors.append(
                    f"cores[{index}].boot_addr 0x{address:08x} must select "
                    f"{allowed} when memory.sram_kb is 0"
                )
        if isinstance(isa, str):
            abi = "ilp32e" if isa.startswith("rv32e") else (
                "ilp32" if isa.startswith("rv32") else "lp64"
            )
            boot_slots.setdefault(address, []).append((abi, f"cores[{index}]"))

    if profile == "soc":
        for address, users in boot_slots.items():
            abis = {abi for abi, _ in users}
            if len(abis) > 1:
                errors.append(
                    f"boot image at 0x{address:08x} mixes incompatible ABIs "
                    f"{sorted(abis)}; assign distinct boot_addr values"
                )

    if boot_slots and type(sram_kb) is int and 8 <= sram_kb <= 512:
        last_boot = max(boot_slots)
        shared_base = (last_boot + 0x1000 + 0xFF) & ~0xFF
        slot_addresses = sorted(boot_slots)
        for slot_index, address in enumerate(slot_addresses):
            slot_end = (
                slot_addresses[slot_index + 1]
                if slot_index + 1 < len(slot_addresses)
                else shared_base
            )
            if slot_end - address < MIN_BOOT_IMAGE_BYTES:
                errors.append(
                    f"boot image slot at 0x{address:08x} is only "
                    f"{slot_end - address} bytes; each distinct image needs at "
                    f"least {MIN_BOOT_IMAGE_BYTES} bytes"
                )
        shared_size = max(0x200, ((total_cores * 8 + 0xFF) // 0x100) * 0x100)
        required_end = shared_base + shared_size + 0x400
        if required_end > sram_kb * 1024:
            errors.append(
                "boot images plus shared-control and minimum stack do not fit SRAM: "
                f"need through 0x{required_end:08x}, SRAM ends at "
                f"0x{sram_kb * 1024:08x}"
            )

    bus = soc.get("bus", "obi")
    if not isinstance(bus, str) or bus not in VALID_BUS:
        errors.append(f"bus {bus!r} not in {sorted(VALID_BUS)}")

    bus_opts = soc.get("bus_opts", {})
    if not isinstance(bus_opts, dict):
        errors.append("bus_opts must be a mapping")
        bus_opts = {}
    errors.extend(_unknown_keys("bus_opts", bus_opts, frozenset({"log", "floonoc"})))
    for fabric, allowed in {
        "log": frozenset({"topology", "num_banks"}),
        "floonoc": frozenset({"route_algo", "endpoints"}),
    }.items():
        options = bus_opts.get(fabric, {})
        if not isinstance(options, dict):
            errors.append(f"bus_opts.{fabric} must be a mapping")
            continue
        errors.extend(
            f"bus_opts.{fabric}.{key}: unknown option"
            for key in sorted(set(options) - set(allowed))
        )
    log_options = bus_opts.get("log", {})
    if isinstance(log_options, dict):
        topology = log_options.get("topology", "lic")
        if topology != "lic":
            errors.append(
                "bus_opts.log.topology currently supports only 'lic'; the "
                "standard MOSAIC master count is not a power of two"
            )
        num_banks = log_options.get("num_banks", "auto")
        if num_banks != "auto" and (type(num_banks) is not int or num_banks < 1):
            errors.append("bus_opts.log.num_banks must be 'auto' or an integer >= 1")
        if bus == "log" and total_cores > 0 and type(sram_kb) is int:
            if dma_kind == "none":
                dma_masters = 0
            elif dma_kind == "xheep":
                dma_masters = 3 * STANDARD_DMA_MASTER_PORTS
            else:
                dma_masters = IDMA_OBI_PORTS_PER_STREAM * STANDARD_DMA_MASTER_PORTS
            num_masters = 2 * total_cores + 1 + dma_masters
            required_banks = 1 << (num_masters - 1).bit_length()
            resolved_banks = required_banks if num_banks == "auto" else num_banks
            if type(resolved_banks) is int and resolved_banks >= 1:
                if resolved_banks > MAX_LOG_BANKS:
                    errors.append(
                        f"bus 'log' needs {required_banks} banks for "
                        f"{num_masters} masters, exceeding the {MAX_LOG_BANKS}-bank backend"
                    )
                if resolved_banks & (resolved_banks - 1):
                    errors.append("bus_opts.log.num_banks must be a power of two")
                if resolved_banks < num_masters:
                    errors.append(
                        f"bus 'log' needs num_banks >= {num_masters} bus masters"
                    )
                if sram_kb % resolved_banks != 0 or sram_kb < resolved_banks:
                    errors.append(
                        f"memory.sram_kb={sram_kb} must be divisible by LOG bank "
                        f"count {resolved_banks} with at least 1 KiB per bank"
                    )
    floo_options = bus_opts.get("floonoc", {})
    if isinstance(floo_options, dict):
        route_algo = floo_options.get("route_algo", "ID")
        if route_algo != "ID":
            errors.append(
                "bus_opts.floonoc.route_algo currently supports only 'ID' "
                "for the compact single-router topology"
            )
        endpoints = floo_options.get("endpoints", "compact")
        if endpoints != "compact":
            errors.append("bus_opts.floonoc.endpoints currently supports only 'compact'")

    scheduler = soc.get("scheduler", {})
    if not isinstance(scheduler, dict):
        errors.append("scheduler must be a mapping")
        scheduler = {}
    errors.extend(_unknown_keys("scheduler", scheduler, frozenset({"tdu", "mode"})))
    tdu = scheduler.get("tdu", False)
    if type(tdu) is not bool:
        errors.append(f"scheduler.tdu must be boolean, got {type(tdu).__name__}")
        tdu_enabled = False
    else:
        tdu_enabled = tdu
    mode = scheduler.get("mode", "static")
    if not isinstance(mode, str) or mode not in VALID_SCHED_MODES:
        errors.append(f"scheduler.mode {mode!r} not in {sorted(VALID_SCHED_MODES)}")
    # The generated PLIC, debug mask, and TDU register ABI all use a maximum
    # of sixteen contexts.  Apply the bound to every topology (including an
    # all-TITAN SMP with the TDU disabled) so schema validation cannot accept
    # a design that a later platform-generator step must reject.
    if total_cores > 16:
        errors.append(
            f"MOSAIC platform services support at most 16 harts, "
            f"configuration has {total_cores}"
        )

    peripherals = soc.get("peripherals", [])
    if not isinstance(peripherals, list):
        errors.append("peripherals must be a list")
        peripherals = []
    seen_peripherals = set()
    for index, peripheral in enumerate(peripherals):
        if not isinstance(peripheral, str) or peripheral not in VALID_PERIPHERALS:
            errors.append(
                f"peripherals[{index}] {peripheral!r} not in {sorted(VALID_PERIPHERALS)}"
            )
        elif peripheral in seen_peripherals:
            errors.append(f"peripherals[{index}] duplicates '{peripheral}'")
        else:
            seen_peripherals.add(peripheral)

    if roles:
        has_worker = any(role != "titan" for role in roles)
        titan_groups = [index for index, role in enumerate(roles) if role == "titan"]
        if profile == "soc":
            if has_worker:
                leading_titan_count = (
                    cores[0].get("count", 0)
                    if titan_groups and titan_groups[0] == 0 and isinstance(cores[0], dict)
                    else 0
                )
                if titan_groups != [0] or leading_titan_count != 1:
                    errors.append(
                        "AMP topology requires exactly one leading TITAN hart at cores[0]"
                    )
                if not tdu_enabled:
                    errors.append(
                        "AMP topology with ATLAS/NANO workers requires scheduler.tdu=true"
                    )
                worker_roles = [role for role in roles if role != "titan"]
                if worker_roles != sorted(
                    worker_roles, key={"atlas": 0, "nano": 1}.get
                ):
                    errors.append("AMP core groups must order ATLAS before NANO workers")
        # All-TITAN SMP is valid with or without the TDU.  Zero-TITAN worker
        # arrangements are reserved for explicit simulation testbenches.
        if profile == "testbench" and titan_groups and titan_groups[0] != 0:
            errors.append("testbench TITAN groups must still begin at cores[0]")
        if (
            profile == "testbench"
            and has_worker
            and total_cores > 1
            and not tdu_enabled
        ):
            errors.append(
                "multi-hart testbench topology with ATLAS/NANO workers requires "
                "scheduler.tdu=true to release dormant workers"
            )

    # Keep physical qualification as a final, orthogonal gate.  This lets the
    # normal schema report field errors while also making any false tapeout
    # claim impossible to miss.
    if isinstance(target, str) and target in VALID_TARGETS:
        errors.extend(target_capability_errors(soc))

    return errors


def expanded_user_peripherals(
    peripherals: List[str],
    *,
    multicore: bool = False,
    plic: bool = True,
    multicore_timer: bool = True,
) -> FrozenSet[str]:
    """Map public peripheral names to concrete x-heep peripheral blocks.

    ``plic=False`` drops rv_plic from the otherwise-mandatory set. The
    peripheral_subsystem template already carries a complete tie-off branch
    for its absence, so this is an area choice, not a structural one -- but it
    leaves external peripheral interrupts with no route to any hart.
    """

    selected = set(MANDATORY_USER_PERIPHERALS) if plic else set()
    if multicore and multicore_timer:
        # MULTICORE_USER_PERIPHERALS force-adds rv_timer to every multi-core
        # config. A design whose harts take their timer and software interrupts
        # from mosaic_clint does not need it, and it measures 0.062 mm2.
        # Setting soc.multicore_timer: false drops it; an explicit `timer` entry
        # in soc.peripherals still brings it back through PERIPHERAL_EXPANSION.
        selected.update(MULTICORE_USER_PERIPHERALS)
    for peripheral in peripherals:
        selected.update(PERIPHERAL_EXPANSION[peripheral])
    return frozenset(selected)


def resolved_capabilities(ip: str, params: Mapping[str, Any]) -> FrozenSet[str]:
    """Return capabilities of the concrete parameterized core instance.

    Capability headers and platform masks must describe instantiated RTL, not
    the maximum feature set of the core family.
    """

    capabilities = set(CORE_SPECS[ip].capabilities)
    if ip == "fazyrv" and params.get("conf", "CSR") == "MIN":
        capabilities.discard("timer_interrupt")
    if ip in {"serv", "qerv"} and not bool(params.get("with_csr", True)):
        capabilities.discard("timer_interrupt")
    return frozenset(capabilities)
