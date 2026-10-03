import sys
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from bus_type import BusType
from memory_ss.memory_ss import MemorySS
from cpu.cpu import CPU
from cv_x_if import CvXIf
from peripherals.abstractions import PeripheralDomain
from peripherals.base_peripherals_domain import BasePeripheralDomain
from peripherals.user_peripherals_domain import UserPeripheralDomain
from pads.pad_ring import PadRing


@dataclass
class CpuConfig:
    """Extended CPU configuration for multi-core support.

    Wraps a CPU with per-group metadata.
    """

    cpu: CPU
    role: str = "nano"  # titan | atlas | nano
    isa: str = "rv32i"  # ISA string
    count: int = 1  # number of instances in this group
    hart_id_base: int = 0  # first hart ID assigned to this group
    params: Dict[str, Any] = field(default_factory=dict)  # per-core-type params

    VALID_ROLES = frozenset({"titan", "atlas", "nano"})

    def __post_init__(self):
        if not isinstance(self.cpu, CPU):
            raise TypeError(f"CpuConfig.cpu should be of type CPU not {type(self.cpu)}")
        if self.role not in self.VALID_ROLES:
            raise ValueError(
                f"Invalid CPU role '{self.role}'. Must be one of: "
                f"{', '.join(sorted(self.VALID_ROLES))}"
            )
        if (
            not isinstance(self.count, int)
            or isinstance(self.count, bool)
            or self.count < 1
        ):
            raise ValueError(f"CPU count must be a positive integer, got {self.count!r}")
        if (
            not isinstance(self.hart_id_base, int)
            or isinstance(self.hart_id_base, bool)
            or self.hart_id_base < 0
        ):
            raise ValueError(
                f"hart_id_base must be a non-negative integer, got {self.hart_id_base!r}"
            )

    def hart_ids(self) -> List[int]:
        """Return the list of hart IDs for this group."""
        return list(range(self.hart_id_base, self.hart_id_base + self.count))

    @property
    def name(self) -> str:
        return self.cpu.get_name()


class XHeep:
    """
    Represents the whole X-HEEP system.

    An instance of this class is passed to the mako templates.

    :param BusType bus_type: The bus type chosen for this mcu.
    :raise TypeError: when parameters are of incorrect type.
    """

    IL_COMPATIBLE_BUS_TYPES = [BusType.NtoM, BusType.LOG]
    """Constant set of bus types that support interleaved memory banks"""

    def __init__(
        self,
        bus_type: BusType,
    ):
        if not type(bus_type) is BusType:
            raise TypeError(
                f"XHeep.bus_type should be of type BusType not {type(self._bus_type)}"
            )

        self._cpu = None
        self._cpus: List[CpuConfig] = []  # multi-core list

        self._xif: CvXIf = None

        self._bus_type: BusType = bus_type

        self._memory_ss = None

        self._base_peripheral_domain = None
        self._user_peripheral_domain = None
        self._padring: PadRing = None

        self._extensions = {}

    # ------------------------------------------------------------
    # CPU (single-core backward compat)
    # ------------------------------------------------------------

    def set_cpu(self, cpu: CPU):
        """
        Sets the CPU of the system.

        :param CPU cpu: The CPU to set.
        :raise TypeError: when cpu is of incorrect type.
        """
        if not isinstance(cpu, CPU):
            raise TypeError(f"XHeep.cpu should be of type CPU not {type(self._cpu)}")
        self._cpu = cpu

    def cpu(self) -> CPU:
        """
        :return: the configured CPU
        :rtype: CPU
        """
        return self._cpu

    # ------------------------------------------------------------
    # CPUs (multi-core)
    # ------------------------------------------------------------

    def add_cpu(self, cfg: CpuConfig):
        """Add a CPU config group to the system."""
        if not isinstance(cfg, CpuConfig):
            raise TypeError(f"Expected CpuConfig, got {type(cfg)}")
        expected_base = self.num_harts()
        if cfg.hart_id_base != expected_base:
            raise ValueError(
                "CPU groups must form a contiguous, non-overlapping hart topology: "
                f"expected hart_id_base {expected_base}, got {cfg.hart_id_base}"
            )
        self._cpus.append(cfg)
        # Keep _cpu pointing to the first (TITAN) core for backward compat
        if self._cpu is None:
            self._cpu = cfg.cpu

    def set_cpus(self, cpus: List[CpuConfig]):
        """Set the full list of CPU config groups."""
        checked = list(cpus)
        expected_base = 0
        for cfg in checked:
            if not isinstance(cfg, CpuConfig):
                raise TypeError(f"Expected CpuConfig, got {type(cfg)}")
            if cfg.hart_id_base != expected_base:
                raise ValueError(
                    "CPU groups must form a contiguous, non-overlapping hart topology: "
                    f"expected hart_id_base {expected_base}, got {cfg.hart_id_base}"
                )
            expected_base += cfg.count
        self._cpus = checked
        if checked:
            self._cpu = checked[0].cpu

    def cpus(self) -> List[CpuConfig]:
        """:return: list of CPU config groups."""
        return self._cpus

    def num_harts(self) -> int:
        """Total number of harts (cores) across all groups."""
        return sum(g.count for g in self._cpus)

    def is_multi_core(self) -> bool:
        """True when the explicit per-hart topology renderer must be used.

        A one-hart MOSAIC topology still needs the topology renderer: it may be
        an SCI-wrapped core and it carries role, boot-address and wake metadata
        that the legacy x-heep scalar CPU path cannot represent.  Legacy HJSON
        configurations, which only call :meth:`set_cpu`, retain the scalar path.
        """
        return bool(self._cpus)

    # ------------------------------------------------------------
    # CORE-V eXtension Interface (CV-X-IF)
    # ------------------------------------------------------------

    def set_xif(self, xif: CvXIf):
        """
        Sets the configuration of the CORE-V eXtension Interface (CV-X-IF).

        :param CvXIf xif: CV-X-IF instance with the desired paramters.

        :raise TypeError: when xif is of incorrect type.
        """
        if not isinstance(xif, CvXIf):
            raise TypeError(f"XHeep.xif should be of type CvXIf not {type(xif)}")
        self._xif = xif

    def xif(self) -> CvXIf:
        """
        :return: the configured CV-X-IF
        :rtype: CvXIf
        """
        return self._xif

    # ------------------------------------------------------------
    # Bus
    # ------------------------------------------------------------

    def set_bus_type(self, bus_type: BusType):
        """
        Sets the bus type of the system.

        :param BusType bus_type: The bus type to set.
        :raise TypeError: when bus_type is of incorrect type.
        """
        if not type(bus_type) is BusType:
            raise TypeError(
                f"XHeep.bus_type should be of type BusType not {type(self._bus_type)}"
            )
        self._bus_type = bus_type

    def bus_type(self) -> BusType:
        """
        :return: the configured bus type
        :rtype: BusType
        """
        return self._bus_type

    def num_bus_masters(self) -> int:
        """Number of master ports on the internal system crossbar.

        Mirrors ``SYSTEM_XBAR_NMASTER`` in ``core_v_mini_mcu_pkg.sv.tpl``:
        2 OBI ports (instr + data) per hart and 1 debug master. Explicit
        MOSAIC topologies use iDMA's read/write pair per stream; legacy scalar
        x-heep configurations retain the simple DMA's read/write/addr triplet.

        :return: the number of internal crossbar master ports
        :rtype: int
        """
        nh = max(1, self.num_harts())
        dma_ports = 0
        if self.are_base_peripherals_configured():
            dma = self._base_peripheral_domain.get_dma()
            if dma is not None:
                # Counted even when soc.dma is "none". The DMA is then stubbed
                # to one master port and its instantiation is skipped, but
                # core_v_mini_mcu_pkg.sv.tpl computes SYSTEM_XBAR_NMASTER from
                # get_num_master_ports() without consulting get_is_included(),
                # so subtracting here would make this count disagree with the
                # generated RTL -- and this value drives the LOG-bus bank check.
                # The residual cost is two unused crossbar masters; removing
                # them means changing the template and the DMA port indices
                # together.
                dma_ports = int(dma.get_num_master_ports())
        dma_obi_ports = 2 if self.is_multi_core() else 3
        return 2 * nh + 1 + dma_obi_ports * dma_ports

    # ------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------

    def set_memory_ss(self, memory_ss: MemorySS):
        """
        Sets the memory subsystem of the system.

        :param MemorySS memory_ss: The memory subsystem to set.
        :raise TypeError: when memory_ss is of incorrect type.
        """
        if not isinstance(memory_ss, MemorySS):
            raise TypeError(
                f"XHeep.memory_ss should be of type MemorySS not {type(self._memory_ss)}"
            )
        self._memory_ss = memory_ss

    def memory_ss(self) -> MemorySS:
        """
        :return: the configured memory subsystem
        :rtype: MemorySS
        """
        return self._memory_ss

    # ------------------------------------------------------------
    # Peripherals
    # ------------------------------------------------------------

    def are_base_peripherals_configured(self) -> bool:
        """
        :return: `True` if the base peripherals are configured, `False` otherwise.
        :rtype: bool
        """
        return self._base_peripheral_domain is not None

    def are_user_peripherals_configured(self) -> bool:
        """
        :return: `True` if the user peripherals are configured, `False` otherwise.
        :rtype: bool
        """
        return self._user_peripheral_domain is not None

    def are_peripherals_configured(self) -> bool:
        """
        :return: `True` if both base and user peripherals are configured, `False` otherwise.
        :rtype: bool
        """
        return (
            self.are_base_peripherals_configured()
            and self.are_user_peripherals_configured()
        )

    def add_peripheral_domain(self, domain: PeripheralDomain):
        """
        Add a peripheral domain to the system. The domain should already contain all peripherals well configured. When adding a domain, a deepcopy is made to avoid side effects.

        :param PeripheralDomain domain: The domain to add.
        """
        if isinstance(domain, BasePeripheralDomain):
            self._base_peripheral_domain = deepcopy(domain)
        elif isinstance(domain, UserPeripheralDomain):
            self._user_peripheral_domain = deepcopy(domain)
        else:
            raise ValueError(
                "Domain is neither a BasePeripheralDomain nor a UserPeripheralDomain"
            )

    def get_user_peripheral_domain(self):
        """
        Returns a deepcopy of the user peripheral domain.

        :return: The user peripheral domain.
        :rtype: UserPeripheralDomain
        """
        return deepcopy(self._user_peripheral_domain)

    def get_base_peripheral_domain(self):
        """
        Returns a deepcopy of the base peripheral domain.

        :return: The base peripheral domain.
        :rtype: BasePeripheralDomain
        """
        return deepcopy(self._base_peripheral_domain)

    # ------------------------------------------------------------
    # Pad Ring
    # ------------------------------------------------------------

    def set_padring(self, pad_ring: PadRing):
        """
        Sets the pad ring of the system.

        :param PadRing pad_ring: The pad ring to set.
        :raise TypeError: when pad_ring is of incorrect type.
        """
        if not isinstance(pad_ring, PadRing):
            raise TypeError(
                f"xheep.get_padring() should be of type PadRing not {type(self._padring)}"
            )
        self._padring = pad_ring

    def get_padring(self):
        return self._padring

    # ------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------

    def add_extension(self, name, extension):
        """
        Register an external extension or configuration (object, dict, etc.).

        :param str name: Name of the extension.
        :param Any extension: The extension object.
        """
        self._extensions[name] = extension

    def get_extension(self, name):
        """
        Retrieve a previously registered extension.

        :param str name: Name of the extension.
        :return: The extension object.
        :rtype: Any
        """
        return self._extensions.get(name, None)

    def is_extension_defined(self, name):
        """
        Check if an extension is defined.

        :param str name: Name of the extension.
        :return: `True` if the extension is defined, `False` otherwise.
        :rtype: bool
        """
        return name in self._extensions

    # ------------------------------------------------------------
    # Build and Validate
    # ------------------------------------------------------------

    def build(self):
        """
        Makes the system ready to be used.
        """

        if self.memory_ss():
            self.memory_ss().build()
        if self.are_base_peripherals_configured():
            self._base_peripheral_domain.build()
        if self.are_user_peripherals_configured():
            self._user_peripheral_domain.build()

    def validate(self):
        """
        Does some basics checks on the configuration

        This should be called before using the XHeep object to generate the project.
        """
        # Single-core or multi-core must have at least one CPU
        if not self._cpus and not self._cpu:
            raise RuntimeError("[MCU-GEN] ERROR: At least one CPU must be configured")
        if not self._cpu:
            raise RuntimeError(
                "[MCU-GEN] ERROR: A primary CPU must be configured (single or multi-core)"
            )

        if not self.memory_ss():
            raise RuntimeError("[MCU-GEN] ERROR: A memory subsystem must be configured")
        self.memory_ss().validate(max_banks=32 if self._bus_type == BusType.LOG else 16)

        if self.memory_ss().has_il_ram() and (
            self._bus_type not in self.IL_COMPATIBLE_BUS_TYPES
        ):
            raise RuntimeError(
                f"[MCU-GEN] ERROR: This system has a {self._bus_type} bus, one of {self.IL_COMPATIBLE_BUS_TYPES} is required for interleaved memory"
            )

        if self._bus_type == BusType.LOG:
            self._validate_log_bus()

        # Check that each peripheral domain is valid
        if self.are_base_peripherals_configured():
            self._base_peripheral_domain.validate()
        if self.are_user_peripherals_configured():
            self._user_peripheral_domain.validate()

        # Check that peripherals domains do not overlap
        if (
            self.are_base_peripherals_configured()
            and self._base_peripheral_domain.get_start_address()
            < self._user_peripheral_domain.get_start_address()
            and self._base_peripheral_domain.get_start_address()
            + self._base_peripheral_domain.get_length()
            > self._user_peripheral_domain.get_start_address()
        ):  # base peripheral domain comes before user peripheral domain
            raise RuntimeError(
                f"[MCU-GEN] ERROR: The base peripheral domain (ends at {self._base_peripheral_domain.get_start_address() + self._base_peripheral_domain.get_length():#08X}) overflows over user peripheral domain (starts at {self._user_peripheral_domain.get_start_address():#08X})."
            )

        if (
            self.are_user_peripherals_configured()
            and self._user_peripheral_domain.get_start_address()
            < self._base_peripheral_domain.get_start_address()
            and self._user_peripheral_domain.get_start_address()
            + self._user_peripheral_domain.get_length()
            > self._base_peripheral_domain.get_start_address()
        ):  # user peripheral domain comes before base peripheral domain
            raise RuntimeError(
                f"[MCU-GEN] ERROR: The user peripheral domain (ends at {self._user_peripheral_domain.get_start_address() + self._user_peripheral_domain.get_length():#08X}) overflows over base peripheral domain (starts at {self._base_peripheral_domain.get_start_address():#08X})."
            )

        if (
            self.are_user_peripherals_configured()
            and self.are_base_peripherals_configured()
            and self._user_peripheral_domain.get_start_address()
            == self._base_peripheral_domain.get_start_address()
        ):  # both domains start at the same address
            raise RuntimeError(
                f"[MCU-GEN] ERROR: The base peripheral domain and the user peripheral domain should not start at the same address (current addresses are {self._base_peripheral_domain.get_start_address():#08X} and {self._user_peripheral_domain.get_start_address():#08X})."
            )

        if (
            self.are_base_peripherals_configured()
            and self._base_peripheral_domain.get_start_address() < 0x10000
        ):  # from mcu_gen.py
            raise RuntimeError(
                f"[MCU-GEN] ERROR: Always on peripheral start address must be greater than 0x10000, current address is {self._base_peripheral_domain.get_start_address():#08X}."
            )

        # Check that the extension interface is enabled with a supported core
        if self.xif() is not None and self.cpu().get_name() in ["cv32e40p"]:
            raise RuntimeError(
                f"[MCU-GEN] ERROR: CV-X-IF enabled (xheep.set_xif()) with incompatible CPU ({self.cpu().get_name()})."
            )

        if not self._padring:
            raise RuntimeError("[MCU-GEN] ERROR: A padring must be configured")
        self._padring.validate()

        return True

    def _validate_log_bus(self):
        """Checks specific to the LOG (logarithmic interconnect) bus.

        The classic ``tcdm_interconnect`` requires NumOut >= NumIn (a sim-only
        $fatal at tcdm_interconnect.sv:318 — this check is the authoritative
        gate) and interleaves the whole banked pool, so all RAM banks must
        form a single interleaved group whose bank count is a power of two
        and at least the number of crossbar masters.
        """
        from memory_ss.ram_bank import is_pow2

        n_masters = self.num_bus_masters()
        n_banks = self.memory_ss().ram_numbanks()
        n_banks_il = self.memory_ss().ram_numbanks_il()
        required = 1 << (n_masters - 1).bit_length()  # next power of two

        if not self.memory_ss().has_il_ram() or n_banks_il != n_banks:
            raise RuntimeError(
                "[MCU-GEN] ERROR: bus 'log' requires ALL RAM banks in one "
                f"interleaved group (banks={n_banks}, interleaved={n_banks_il}). "
                "The mosaic flow sets this up automatically; for hjson flows use "
                "interleaved banks only."
            )
        if n_banks < n_masters or not is_pow2(n_banks):
            ram_kb = self.memory_ss().ram_size_address() // 1024
            raise RuntimeError(
                f"[MCU-GEN] ERROR: bus 'log' needs num_banks >= bus masters "
                f"({n_masters}) and a power of two: required >= {required}, "
                f"got {n_banks}. With sram_kb={ram_kb} that is "
                f"{max(1, ram_kb // required)} KB/bank — set "
                f"bus_opts.log.num_banks and/or raise memory.sram_kb."
            )

        bus_opts = self.get_extension("bus_opts") or {}
        topology = bus_opts.get("log", {}).get("topology", "lic")
        if topology in ("bfly2", "bfly4") and not is_pow2(n_masters):
            raise RuntimeError(
                f"[MCU-GEN] ERROR: bus_opts.log.topology '{topology}' requires "
                f"a power-of-two number of masters, got {n_masters}. Use "
                f"topology 'lic' instead."
            )
