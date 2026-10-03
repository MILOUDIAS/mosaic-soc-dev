"""Topology-renderer matrix tests for ``cpu_subsystem.sv.tpl``.

These tests deliberately render the template directly.  A core being present
in a Python registry is not sufficient evidence that the generator has an RTL
branch for it, nor that a one-hart SCI topology selects that branch.
"""

from pathlib import Path
import sys

from mako.template import Template
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT / "util" / "xheep_gen"))

from bus_type import BusType
from cpu.cpu import CPU
from cpu.cv32e20 import cv32e20
from cpu.cv32e40p import cv32e40p
from cpu.cv32e40px import cv32e40px
from cpu.cv32e40x import cv32e40x
from memory_ss.memory_ss import MemorySS
from xheep import CpuConfig, XHeep


TEMPLATE = Template(
    filename=str(REPO_ROOT / "hw" / "core-v-mini-mcu" / "cpu_subsystem.sv.tpl"),
    strict_undefined=True,
)

EXPECTED_INSTANCE = {
    "boom": "boom_sci #(",
    "cv32e20": "cve2_xif_wrapper #(",
    "cv32e40p": "cv32e40p_top #(",
    "cv32e40px": "cv32e40px_xif_wrapper #(",
    "cv32e40x": "cv32e40x_core #(",
    "cva6": "cva6_sci #(",
    "fazyrv": "fazyrv_sci #(",
    "hazard3": "hazard3_sci #(",
    "ibex": "ibex_sci #(",
    "picorv32": "picorv32_sci #(",
    "qerv": "serv_sci #(",
    "rocket": "rocket_sci #(",
    "serv": "serv_sci #(",
    "snitch": "snitch_sci #(",
}


def make_cpu(name):
    """Use native parameter classes where the renderer expects them."""
    native = {
        "cv32e20": cv32e20,
        "cv32e40p": cv32e40p,
        "cv32e40px": cv32e40px,
        "cv32e40x": cv32e40x,
    }
    return native.get(name, lambda: CPU(name))()


def render(groups, sram_kb=None, profile=None):
    xheep = XHeep(BusType.NtoM)
    if sram_kb is not None:
        memory = MemorySS()
        memory.add_ram_banks([sram_kb])
        xheep.set_memory_ss(memory)
    xheep.set_cpus(groups)
    if profile is not None:
        xheep.add_extension("soc_profile", profile)
    return TEMPLATE.render_unicode(xheep=xheep)


def group(name, role, count=1, hart_id_base=0, **params):
    return CpuConfig(
        cpu=make_cpu(name),
        role=role,
        count=count,
        hart_id_base=hart_id_base,
        params=params,
    )


def test_renderer_matrix_covers_cpu_registry():
    assert set(EXPECTED_INSTANCE) == set(CPU.AVAILABLE_CPUS)


@pytest.mark.parametrize("name", sorted(CPU.AVAILABLE_CPUS))
def test_every_registered_core_renders_as_singleton_primary(name):
    rtl = render([group(name, "titan", boot_addr="0x1234")])

    assert "parameter int NUM_HARTS = 1" in rtl
    assert EXPECTED_INSTANCE[name] in rtl
    assert f"Core 0.0: {name} (titan, hart 0)" in rtl
    assert "localparam logic [31:0] BOOT_ADDR_0_0 = 32'h00001234;" in rtl
    assert "assign fetch_enable_0_0 = 1'b1;" in rtl
    assert "core_run_0_0" not in rtl


@pytest.mark.parametrize("name", sorted(CPU.AVAILABLE_CPUS))
def test_every_registered_core_renders_as_secondary_worker(name):
    rtl = render(
        [
            group("cv32e20", "titan", hart_id_base=0),
            group(name, "atlas", hart_id_base=1, boot_addr=0x4000),
        ]
    )

    assert EXPECTED_INSTANCE[name] in rtl
    assert f"Core 1.0: {name} (atlas, hart 1)" in rtl
    assert "localparam int HART_1_0 = 1;" in rtl
    assert "localparam logic [31:0] BOOT_ADDR_1_0 = 32'h00004000;" in rtl
    assert "if (core_wake_i[HART_1_0] ||" in rtl
    assert "debug_req_i[HART_1_0])" in rtl
    assert "else if (core_park_i[HART_1_0])" in rtl
    assert "core_run_1_0 <= 1'b0;" in rtl
    assert "reset_clock_hold_1_0 <= core_run_1_0;" in rtl
    assert ".en_i      (hart_clock_enable_1_0)" in rtl


def test_worker_only_testbench_releases_only_hart_zero_for_bootstrap():
    rtl = render(
        [
            group("serv", "nano", hart_id_base=0),
            group("qerv", "nano", hart_id_base=1),
        ],
        profile="testbench",
    )
    assert "Explicit worker-only testbench bootstrap" in rtl
    assert "assign fetch_enable_0_0 = 1'b1;" in rtl
    assert "BOOT_ADDR_0_0 = 32'h00000180;" in rtl
    assert "core_run_0_0" not in rtl
    assert "logic core_run_1_0;" in rtl
    assert "BOOT_ADDR_1_0 = 32'h00000180;" in rtl
    assert "assign fetch_enable_1_0 = core_run_1_0;" in rtl


def test_worker_only_soc_profile_never_gets_testbench_bootstrap():
    rtl = render([group("serv", "nano", hart_id_base=0)], profile="soc")
    assert "logic core_run_0_0;" in rtl
    assert "assign fetch_enable_0_0 = core_run_0_0;" in rtl


def test_worker_default_boot_matches_generated_sram_image_not_platform_boot_rom():
    rtl = render(
        [
            group("cv32e20", "titan", hart_id_base=0),
            group("fazyrv", "atlas", hart_id_base=1),
        ]
    )
    assert "BOOT_ADDR_0_0 = BOOT_ADDR;" in rtl
    assert "BOOT_ADDR_1_0 = 32'h00000180;" in rtl


def test_repeated_groups_keep_distinct_harts_roles_and_boot_addresses():
    rtl = render(
        [
            group("serv", "titan", count=2, hart_id_base=0, boot_addr=0x180),
            group("serv", "atlas", count=2, hart_id_base=2, boot_addr=0x2000),
            group("serv", "nano", hart_id_base=4, boot_addr="0x3000"),
        ]
    )

    assert "parameter int NUM_HARTS = 5" in rtl
    for hart, group_index, instance in (
        (0, 0, 0),
        (1, 0, 1),
        (2, 1, 0),
        (3, 1, 1),
        (4, 2, 0),
    ):
        assert f"localparam int HART_{group_index}_{instance} = {hart};" in rtl
    assert rtl.count("BOOT_ADDR_0_") >= 2
    assert "BOOT_ADDR_1_0 = 32'h00002000" in rtl
    assert "BOOT_ADDR_1_1 = 32'h00002000" in rtl
    assert "BOOT_ADDR_2_0 = 32'h00003000" in rtl
    assert "core_run_0_0" not in rtl
    assert "core_run_0_1" not in rtl
    assert "core_run_1_0" in rtl
    assert "core_run_2_0" in rtl


def test_berkeley_windows_follow_nondefault_memory_and_software_layout():
    rtl = render(
        [
            group("cv32e20", "titan", hart_id_base=0),
            group("rocket", "atlas", hart_id_base=1, boot_addr=0x2000),
            group("boom", "nano", hart_id_base=2, boot_addr=0x3000),
        ],
        sram_kb=64,
    )
    # MEM_SIZE comes from the 64 KiB generated package instead of the bridge's
    # historical fixed 32 KiB default. The shared base follows the same
    # max-boot+4KiB layout function as software_gen (0x3000 -> 0x4000).
    assert rtl.count(".CODE_WINDOW_SIZE({32'b0, MEM_SIZE})") == 2
    assert rtl.count(".SENTINEL_DEST(32'h00004000)") == 2

    for wrapper in ("rocket_sci.sv", "boom_sci.sv"):
        source = (REPO_ROOT / "hw/sci" / wrapper).read_text()
        assert ".WIN_CODE_SIZE(CODE_WINDOW_SIZE)" in source
        assert ".WIN_SENT_DEST(SENTINEL_DEST)" in source


@pytest.mark.parametrize("name", ["cv32e20", "cv32e40p", "cv32e40px", "cv32e40x"])
def test_native_instruction_obi_fields_are_fully_driven(name):
    rtl = render([group(name, "titan")])
    prefix = "core_instr_req_o[HART_0_0]"
    assert f"assign {prefix}.wdata = '0;" in rtl
    assert f"assign {prefix}.we    = 1'b0;" in rtl
    assert f"assign {prefix}.be    = 4'b1111;" in rtl


def test_unknown_renderer_fails_generation_instead_of_falling_back():
    cpu = CPU("serv")
    cpu.name = "not_integrated"
    with pytest.raises(ValueError, match="no cpu_subsystem topology renderer"):
        render([CpuConfig(cpu=cpu, role="titan")])


@pytest.mark.parametrize("role", ["worker", "primary", ""])
def test_invalid_role_fails_before_render(role):
    with pytest.raises(ValueError, match="Invalid CPU role"):
        group("serv", role)


def test_non_contiguous_or_overlapping_harts_are_rejected():
    xheep = XHeep(BusType.NtoM)
    with pytest.raises(ValueError, match="expected hart_id_base 1"):
        xheep.set_cpus(
            [
                group("serv", "titan", hart_id_base=0),
                group("serv", "nano", hart_id_base=0),
            ]
        )


@pytest.mark.parametrize("boot_addr", [True, -1, 0x1_0000_0000, "invalid"])
def test_invalid_boot_address_fails_render(boot_addr):
    with pytest.raises(ValueError, match="boot_addr"):
        render([group("serv", "titan", boot_addr=boot_addr)])


def test_legacy_xheep_set_cpu_still_uses_scalar_renderer():
    xheep = XHeep(BusType.NtoM)
    xheep.set_cpu(cv32e20())

    assert not xheep.is_multi_core()
    rtl = TEMPLATE.render_unicode(xheep=xheep)
    assert "parameter int NUM_HARTS" not in rtl
    assert "cve2_xif_wrapper #(" in rtl


def test_legacy_scalar_path_rejects_sci_core_instead_of_falling_back():
    xheep = XHeep(BusType.NtoM)
    xheep.set_cpu(CPU("serv"))

    with pytest.raises(ValueError, match="requires an explicit CpuConfig topology"):
        TEMPLATE.render_unicode(xheep=xheep)
