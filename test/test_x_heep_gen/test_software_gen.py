"""Software artifacts must be a deterministic projection of mosaic.yaml."""

import json
from pathlib import Path, PurePath
import shutil
import subprocess
import sys

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT / "util" / "xheep_gen"))

import mosaic_config
import software_gen
import pack_flash


def _config() -> dict:
    return {
        "soc": {
            "name": "software_contract",
            "pdk": "gf180mcu",
            "cores": [
                {
                    "ip": "cv32e20",
                    "isa": "rv32emc",
                    "count": 1,
                    "role": "titan",
                },
                {
                    "ip": "picorv32",
                    "isa": "rv32i",
                    "count": 2,
                    "role": "atlas",
                    "boot_addr": 0x1000,
                },
                {
                    "ip": "serv",
                    "isa": "rv32i",
                    "count": 2,
                    "role": "nano",
                    "boot_addr": 0x2000,
                },
            ],
            "memory": {"sram_kb": 32, "boot_rom_kb": 4},
            "bus": "obi",
            "scheduler": {"tdu": True, "mode": "power-aware"},
            "peripherals": ["uart", "gpio", "i2c"],
        }
    }


def _parse(tmp_path: Path, raw: dict | None = None):
    path = tmp_path / "mosaic.yaml"
    path.write_text(yaml.safe_dump(raw or _config(), sort_keys=False))
    return mosaic_config.parse_yaml(PurePath(path))


def test_complete_topology_and_memory_contract(tmp_path):
    cfg = _parse(tmp_path)
    out = tmp_path / "software"
    artifacts = software_gen.generate_software_artifacts(cfg, out)

    topology = artifacts.topology_header.read_text()
    assert "#define MOSAIC_NUM_HARTS 5u" in topology
    assert "#define MOSAIC_ATLAS_HART_MASK_WORD_0 0x00000006u" in topology
    assert "#define MOSAIC_NANO_HART_MASK_WORD_0 0x00000018u" in topology
    assert "#define MOSAIC_WORKER_HART_MASK_WORD_0 0x0000001Eu" in topology
    assert "#define MOSAIC_HART_0_BOOT_ADDR 0x00000180u" in topology
    assert "#define MOSAIC_HART_4_BOOT_ADDR 0x00002000u" in topology
    assert '#define MOSAIC_HART_0_ABI "ilp32e"' in topology
    assert '#define MOSAIC_HART_1_ABI "ilp32"' in topology
    assert "#define MOSAIC_SCHED_MODE 2u" in topology
    assert "MOSAIC_CAP_DEBUG" in topology

    memory_map = artifacts.memory_map_header.read_text()
    assert "#define MOSAIC_SRAM_SIZE 0x00008000u" in memory_map
    assert "#define MOSAIC_BOOT_ROM_SIZE 0x00001000u" in memory_map
    assert "#define MOSAIC_TDU_BASE 0x200A0000u" in memory_map
    assert "#define MOSAIC_TDU_TASK_QUEUE_DEPTH 8u" in memory_map
    assert "#define MOSAIC_TDU_PARK_REQ_OFFSET 0x00000060u" in memory_map
    assert "#define MOSAIC_CLINT_BASE 0x200B0000u" in memory_map
    assert "#define MOSAIC_CLINT_MTIME_LO_OFFSET 0x0000BFF8u" in memory_map
    assert "#define MOSAIC_HAS_UART 1u" in memory_map
    assert "#define MOSAIC_HAS_GPIO 1u" in memory_map
    assert "#define MOSAIC_HAS_I2C 1u" in memory_map
    assert "#define MOSAIC_HAS_SPI 0u" in memory_map
    # Multi-hart platform services are selected even when not public YAML items.
    assert "#define MOSAIC_HAS_PLIC 1u" in memory_map
    assert "#define MOSAIC_PLIC_NUM_TARGETS 5u" in memory_map
    assert "#define MOSAIC_PLIC_TARGET_STRIDE 0x00000100u" in memory_map
    assert "#define MOSAIC_PLIC_CLAIM_COMPLETE_OFFSET(hart)" in memory_map
    assert "#define MOSAIC_HAS_TIMER 1u" in memory_map

    assembler_map = artifacts.assembler_memory_map.read_text()
    assert "#define MOSAIC_ASM_TDU_TASK_POP 0x200A0014" in assembler_map
    assert "#define MOSAIC_ASM_TDU_PARK_REQ 0x200A0060" in assembler_map
    assert "#define MOSAIC_ASM_CLINT_BASE 0x200B0000" in assembler_map
    assert "#define MOSAIC_ASM_SENTINEL_BASE 0x00003000" in assembler_map
    assert "#define MOSAIC_ASM_TL_TDU_PARK_REQ 0x0C000060" in assembler_map

    isa = artifacts.isa_makefile.read_text()
    assert "MOSAIC_GROUP_0_MARCH := rv32emc" in isa
    assert "MOSAIC_GROUP_0_MABI := ilp32e" in isa
    assert "MOSAIC_GROUP_1_HARTS := 1 2" in isa
    assert "MOSAIC_HART_4_IMAGE := 2" in isa


def test_boot_manifest_and_linkers_match_boot_slots(tmp_path):
    cfg = _parse(tmp_path)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "software")
    manifest = json.loads(artifacts.boot_manifest.read_text())

    assert [image["load_address"] for image in manifest["images"]] == [
        "0x00000180",
        "0x00001000",
        "0x00002000",
    ]
    assert manifest["images"][1]["harts"] == [1, 2]
    assert manifest["images"][1]["shared"] is True
    assert manifest["images"][1]["startup_source"] is None
    assert manifest["images"][1]["startup_identity"] is None
    assert manifest["harts"][4]["image_id"] == 2
    assert manifest["memory"]["shared_control_base"] == "0x00003000"
    assert len(manifest["topology_sha256"]) == 64

    linker = artifacts.linker_script.read_text()
    assert "image_0_rx (rx) : ORIGIN = 0x00000180, LENGTH = 0x00000E80" in linker
    assert "image_1_rx (rx) : ORIGIN = 0x00001000, LENGTH = 0x00001000" in linker
    assert "shared_rw (rw) : ORIGIN = 0x00003000, LENGTH = 0x00000200" in linker
    assert "*(.atlas)" in linker
    assert "*(.nano)" in linker
    assert len(artifacts.image_linker_scripts) == 3
    atlas_linker = artifacts.image_linker_scripts[1].read_text()
    nano_linker = artifacts.image_linker_scripts[2].read_text()
    assert "__mosaic_image_hart_count = 2;" in atlas_linker
    assert "__mosaic_image_hart_0 = 1;" in atlas_linker
    assert "__mosaic_image_hart_1 = 2;" in atlas_linker
    assert "__mosaic_stack_stride = 0x00000100;" in atlas_linker
    assert ".hart_stacks (NOLOAD)" in atlas_linker
    assert "__mosaic_stack_top_1" in atlas_linker
    assert "__mosaic_image_hart_count = 2;" in nano_linker
    assert "__mosaic_stack_end" in nano_linker
    runtime = (artifacts.root / "include/mosaic_runtime.h").read_text()
    assert "#define MOSAIC_IMAGE_1_HART_COUNT 2u" in runtime
    assert "#define MOSAIC_IMAGE_1_INIT_HART 1u" in runtime
    assert "#define MOSAIC_IMAGE_1_HAS_GENERATED_CRT0 0u" in runtime
    assert not (artifacts.root / "startup/image_1_crt0.S").exists()
    crt0 = (artifacts.root / "startup/image_0_crt0.S").read_text()
    assert "li a0, 0" in crt0
    assert "la sp, __mosaic_stack_top_0" in crt0
    assert "la t0, __bss_start" in crt0
    assert "__mosaic_init_release" in crt0
    assert "bnez t1, .Lalready_initialized" in crt0
    assert "__mosaic_init_epoch" not in crt0
    assert "fence rw, rw" in crt0
    assert "call mosaic_main" in crt0
    software_gen.validate_production_demo_files(
        artifacts.boot_manifest, artifacts.isa_makefile
    )


def test_flash_packer_emits_authenticated_worker_table(tmp_path):
    cfg = _parse(tmp_path)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "software")
    image_paths = {}
    for image_id, payload in enumerate((b"titan-xip", b"atlas", b"nano-worker")):
        path = tmp_path / f"image_{image_id}.bin"
        path.write_bytes(payload)
        image_paths[image_id] = path
    output = tmp_path / "mosaic_flash.bin"
    deployment = pack_flash.pack(artifacts.boot_manifest, image_paths, output)

    assert deployment["boot_mode"] == "spi-memio-xip-titan-load-workers"
    assert deployment["table"]["entry_count"] == 2
    assert output.read_bytes()[0x180 : 0x180 + len(b"titan-xip")] == b"titan-xip"
    assert output.with_suffix(".hex").read_text().startswith("@00000000\n")
    assert all(len(item["sha256"]) == 64 for item in deployment["images"])

    raw = bytearray(output.read_bytes())
    magic, version, header_size, entry_count, flags, topo, table_crc, titan = (
        pack_flash.HEADER.unpack_from(raw)
    )
    assert (magic, version, entry_count, titan) == (
        pack_flash.MAGIC,
        pack_flash.VERSION,
        2,
        pack_flash.TITAN_OFFSET,
    )
    entries = bytes(raw[pack_flash.HEADER.size : header_size])
    import binascii

    assert binascii.crc32(entries) & 0xFFFF_FFFF == table_crc


def test_flash_packer_hard_fails_missing_or_oversized_worker(tmp_path):
    cfg = _parse(tmp_path)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "software")
    titan = tmp_path / "titan.bin"
    titan.write_bytes(b"x")
    with pytest.raises(pack_flash.PackError, match="missing binary paths"):
        pack_flash.pack(artifacts.boot_manifest, {0: titan}, tmp_path / "missing.bin")

    huge = tmp_path / "huge.bin"
    huge.write_bytes(b"x" * 0x1001)
    nano = tmp_path / "nano.bin"
    nano.write_bytes(b"n")
    with pytest.raises(pack_flash.PackError, match="exceeds SRAM slot"):
        pack_flash.pack(
            artifacts.boot_manifest,
            {0: titan, 1: huge, 2: nano},
            tmp_path / "huge-flash.bin",
        )


def test_production_demo_rejects_all_titan_but_keeps_generated_bsp(tmp_path):
    raw = _config()
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32imc", "count": 4, "role": "titan"}
    ]
    raw["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    cfg = _parse(tmp_path, raw)
    generated_root = tmp_path / "all_titan_generated"
    artifacts = software_gen.generate_software_artifacts(cfg, generated_root / "sw")

    assert artifacts.topology_header.is_file()
    assert artifacts.linker_script.is_file()
    all_titan_linker = artifacts.image_linker_scripts[0].read_text()
    assert "__mosaic_image_hart_count = 4;" in all_titan_linker
    assert "__mosaic_stack_top_3" in all_titan_linker
    assert "__bss_start" in all_titan_linker
    all_titan_crt0 = (artifacts.root / "startup/image_0_crt0.S").read_text()
    assert "li t0, 3" in all_titan_crt0
    assert "la sp, __mosaic_stack_top_3" in all_titan_crt0
    assert "csrr a0, mhartid" in all_titan_crt0
    assert "bnez t1, .Lalready_initialized" in all_titan_crt0
    with pytest.raises(
        software_gen.SoftwareGenerationError,
        match="requires at least one ATLAS group",
    ) as error:
        software_gen.validate_production_demo_files(
            artifacts.boot_manifest, artifacts.isa_makefile
        )
    assert "Generated BSP headers" in str(error.value)

    make = shutil.which("make")
    if make is not None:
        result = subprocess.run(
            [
                make,
                "-C",
                str(REPO_ROOT / "sw/firmware"),
                "-n",
                f"BUILD={tmp_path / 'firmware'}",
                f"MOSAIC_GENERATED_ROOT={generated_root}",
                "all",
            ],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert result.returncode != 0
        assert "production sw/firmware demo is not applicable" in result.stdout
        assert "Generated BSP headers" in result.stdout


def test_production_demo_rejects_multiple_incompatible_atlas_images(tmp_path):
    raw = _config()
    raw["soc"]["memory"] = {"sram_kb": 64, "boot_rom_kb": 2}
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
        {
            "ip": "picorv32",
            "isa": "rv32i",
            "count": 1,
            "role": "atlas",
            "boot_addr": 0x1000,
        },
        {
            "ip": "ibex",
            "isa": "rv32imc",
            "count": 1,
            "role": "atlas",
            "boot_addr": 0x1800,
        },
        {
            "ip": "serv",
            "isa": "rv32i",
            "count": 1,
            "role": "nano",
            "boot_addr": 0x3000,
        },
    ]
    cfg = _parse(tmp_path, raw)
    artifacts = software_gen.generate_software_artifacts(
        cfg, tmp_path / "incompatible_workers"
    )
    manifest = json.loads(artifacts.boot_manifest.read_text())
    assert len(manifest["images"]) == 4

    with pytest.raises(software_gen.SoftwareGenerationError) as error:
        software_gen.validate_production_demo_files(
            artifacts.boot_manifest, artifacts.isa_makefile
        )
    message = str(error.value)
    assert "all ATLAS groups must share exactly one boot image" in message
    assert "all ATLAS groups must share one compatible ISA" in message
    assert "Generated BSP headers" in message


def test_run_fw_validates_demo_contract_before_expensive_setup():
    run_fw = (REPO_ROOT / "tb/mosaic_soc/run_fw.sh").read_text()
    validation = run_fw.index("--validate-production-demo")
    setup = run_fw.index("scripts/fusesoc-setup.sh")
    build = run_fw.index("make -C sw/firmware")
    assert validation < setup < build


def test_generation_is_byte_deterministic_and_idempotent(tmp_path):
    cfg = _parse(tmp_path)
    out = tmp_path / "software"
    first = software_gen.generate_software_artifacts(cfg, out)
    snapshot = {
        path.relative_to(out): path.read_bytes()
        for path in sorted(out.rglob("*"))
        if path.is_file()
    }
    mtimes = {path: path.stat().st_mtime_ns for path in out.rglob("*") if path.is_file()}

    second = software_gen.generate_software_artifacts(cfg, out)
    assert first == second
    assert snapshot == {
        path.relative_to(out): path.read_bytes()
        for path in sorted(out.rglob("*"))
        if path.is_file()
    }
    assert mtimes == {path: path.stat().st_mtime_ns for path in out.rglob("*") if path.is_file()}

    stale = out / "linker/image_99.ld"
    stale.write_text("stale\n")
    software_gen.generate_software_artifacts(cfg, out)
    assert not stale.exists()


def test_mixed_xlen_topology_gets_per_image_elf_classes(tmp_path):
    raw = _config()
    raw["soc"]["profile"] = "testbench"
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
        {
            "ip": "rocket",
            "isa": "rv64imc",
            "count": 1,
            "role": "atlas",
            "boot_addr": 0x1000,
        },
        {
            "ip": "boom",
            "isa": "rv64imc",
            "count": 1,
            "role": "nano",
            "boot_addr": 0x2000,
        },
    ]
    cfg = _parse(tmp_path, raw)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "mixed")
    assert 'OUTPUT_FORMAT("elf32-littleriscv"' in artifacts.image_linker_scripts[0].read_text()
    assert 'OUTPUT_FORMAT("elf64-littleriscv"' in artifacts.image_linker_scripts[1].read_text()
    assert "MOSAIC_HART_1_MABI := lp64" in artifacts.isa_makefile.read_text()


def test_layout_rejects_boot_image_that_cannot_fit_sram(tmp_path):
    raw = _config()
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
        {
            "ip": "serv",
            "isa": "rv32i",
            "count": 1,
            "role": "nano",
            "boot_addr": 0x7000,
        },
    ]
    raw["soc"]["memory"] = {"sram_kb": 32, "boot_rom_kb": 2}
    with pytest.raises(RuntimeError, match="do not fit SRAM"):
        _parse(tmp_path, raw)


def test_production_titan_reset_vector_cannot_override_boot_rom(tmp_path):
    raw = _config()
    raw["soc"]["cores"][0]["boot_addr"] = 0x1000
    with pytest.raises(RuntimeError, match="reset vector must remain the boot ROM"):
        _parse(tmp_path, raw)


def test_worker_only_testbench_omits_inapplicable_xip_and_crt0(tmp_path):
    cfg = mosaic_config.parse_yaml(PurePath(REPO_ROOT / "configs/mosaic_sim.yaml"))
    artifacts = software_gen.generate_software_artifacts(
        cfg, tmp_path / "worker_only"
    )
    assert artifacts.titan_flash_linker is None
    assert not (artifacts.root / "linker/titan_flash.ld").exists()
    assert artifacts.image_startup_sources == ()
    manifest = json.loads(artifacts.boot_manifest.read_text())
    assert manifest["images"][0]["startup_source"] is None
    assert manifest["boot_policy"]["testbench_hart0_bootstrap"] is True


def test_small_sram_layout_moves_shared_window_and_assembles_worker(tmp_path):
    raw = _config()
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"},
        {
            "ip": "serv",
            "isa": "rv32i",
            "count": 1,
            "role": "nano",
            "boot_addr": 0x800,
        },
    ]
    raw["soc"]["memory"] = {"sram_kb": 8, "boot_rom_kb": 1}
    cfg = _parse(tmp_path, raw)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "small")
    memory_map = artifacts.memory_map_header.read_text()
    assembler_map = artifacts.assembler_memory_map.read_text()
    assert "#define MOSAIC_SRAM_SIZE 0x00002000u" in memory_map
    assert "#define MOSAIC_SHARED_CONTROL_BASE 0x00001800u" in memory_map
    assert "#define MOSAIC_ASM_SENTINEL_BASE 0x00001800" in assembler_map
    assert "#define MOSAIC_ASM_RESULT_BASE 0x00001900" in assembler_map

    compiler = shutil.which("riscv64-unknown-elf-gcc")
    if compiler is not None:
        subprocess.run(
            [
                compiler,
                "-march=rv32i",
                "-mabi=ilp32",
                "-nostdlib",
                "-ffreestanding",
                "-DMOSAIC_USE_BUILD_GENERATED_HEADERS",
                f"-I{artifacts.assembler_memory_map.parent}",
                "-c",
                str(REPO_ROOT / "sw/firmware/nano/nano_worker.S"),
                "-o",
                str(tmp_path / "small_nano.o"),
            ],
            check=True,
            cwd=REPO_ROOT,
        )


def test_generated_headers_are_valid_c(tmp_path):
    cfg = _parse(tmp_path)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "software")
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("host C compiler unavailable")
    source = tmp_path / "contract.c"
    source.write_text(
        '#include "mosaic_topology.h"\n'
        '#include "mosaic_memory_map.h"\n'
        "_Static_assert(MOSAIC_NUM_HARTS == 5, \"hart count\");\n"
        "_Static_assert(MOSAIC_CLINT_BASE == 0x200B0000u, \"clint\");\n"
        "int main(void) { return (int)mosaic_hart_config[1].hart_id - 1; }\n"
    )
    subprocess.run(
        [
            cc,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsyntax-only",
            f"-I{artifacts.topology_header.parent}",
            str(source),
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def test_explicit_singleton_bsp_reports_the_clint_instantiated_by_rtl(tmp_path):
    raw = _config()
    raw["soc"]["cores"] = [
        {"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"}
    ]
    raw["soc"]["scheduler"] = {"tdu": False, "mode": "static"}
    cfg = _parse(tmp_path, raw)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "singleton")
    assert "#define MOSAIC_HAS_CLINT 1u" in artifacts.memory_map_header.read_text()


def test_titan_dispatch_uses_generated_topology_not_fixed_counts():
    source = (REPO_ROOT / "sw/firmware/titan/titan_main.c").read_text()
    assert "#define NUM_ATLAS" not in source
    assert "#define NUM_NANO" not in source
    assert "MOSAIC_WORKER_HART_MASK" in source
    assert "MOSAIC_SCHED_MODE" in source

    demo = (REPO_ROOT / "sw/firmware/titan/titan_scheduling_demo.c").read_text()
    assert "#define NUM_ATLAS" not in demo
    assert "#define NUM_NANO" not in demo
    assert "MOSAIC_NUM_ATLAS_HARTS" in demo
    assert "MOSAIC_NUM_NANO_HARTS" in demo

    workers = [
        "sw/firmware/atlas/atlas_worker.S",
        "sw/firmware/nano/nano_worker.S",
        "tb/mosaic_soc/prog/atlas.S",
        "tb/mosaic_soc/prog/nano.S",
        "tb/mosaic_soc/prog/atlas_tl.S",
        "tb/mosaic_soc/prog/nano_tl.S",
    ]
    for path in workers:
        assembly = (REPO_ROOT / path).read_text()
        assert "MOSAIC_USE_BUILD_GENERATED_HEADERS" in assembly
        assert "mosaic_memory_map.inc" in assembly


def test_titan_dispatch_compiles_against_build_generated_headers(tmp_path):
    compiler = shutil.which("riscv64-unknown-elf-gcc")
    if compiler is None:
        pytest.skip("RISC-V bare-metal compiler unavailable")
    cfg = _parse(tmp_path)
    artifacts = software_gen.generate_software_artifacts(cfg, tmp_path / "software")
    subprocess.run(
        [
            compiler,
            "-Os",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-ffreestanding",
            "-nostdlib",
            "-nostartfiles",
            "-march=rv32emc",
            "-mabi=ilp32e",
            "-DMOSAIC_USE_BUILD_GENERATED_HEADERS",
            f"-I{artifacts.topology_header.parent}",
            f"-I{REPO_ROOT / 'sw/device/lib/base'}",
            f"-I{REPO_ROOT / 'sw/firmware/common'}",
            "-c",
            str(REPO_ROOT / "sw/firmware/titan/titan_main.c"),
            "-o",
            str(tmp_path / "titan_main.o"),
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def test_standard_mcu_gen_registers_software_bundle_and_builds_images(tmp_path):
    output_root = tmp_path / "bundles"
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "util/xheep_gen/mcu_gen.py"),
            "--mosaic_config",
            "configs/mosaic_wake_demo.yaml",
            "--base_config",
            "configs/general.hjson",
            "--pads_cfg",
            "configs/pad_cfg.py",
            "--output-root",
            str(output_root),
            "--outtpl",
            "hw/core-v-mini-mcu/cpu_subsystem.sv.tpl",
            "--externaltpl",
            "",
        ],
        check=True,
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
    )
    manifests = list(output_root.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    generated_root = Path(manifest["generated_root"])
    records = [
        item
        for item in manifest["generated_files"]
        if item.get("generator") == "software_gen"
    ]
    assert len(records) == 15
    assert {item["logical_path"] for item in records} >= {
        "sw/firmware/generated/include/mosaic_topology.h",
        "sw/firmware/generated/include/mosaic_memory_map.inc",
        "sw/firmware/generated/make/mosaic_isa.mk",
        "sw/firmware/generated/boot_images.json",
    }
    assert (generated_root / "sw/linker/image_0.ld").is_file()
    assert (generated_root / "sw/linker/titan_flash.ld").is_file()
    assert (generated_root / "sw/include/mosaic_deployment.h").is_file()
    assert (generated_root / "sw/include/mosaic_runtime.h").is_file()
    assert (generated_root / "sw/startup/image_0_crt0.S").is_file()
    assert (generated_root / "sw/startup/image_1_crt0.S").is_file()

    compiler = shutil.which("riscv64-unknown-elf-gcc")
    make = shutil.which("make")
    if compiler is None or make is None:
        return
    tool_prefix = compiler.removesuffix("-gcc")
    firmware_build = tmp_path / "firmware"
    subprocess.run(
        [
            compiler,
            "-c",
            "-march=rv32emc",
            "-mabi=ilp32e",
            str(generated_root / "sw/startup/image_0_crt0.S"),
            "-o",
            str(tmp_path / "image_0_crt0.o"),
        ],
        check=True,
        cwd=REPO_ROOT,
    )
    subprocess.run(
        [
            make,
            "-C",
            str(REPO_ROOT / "sw/firmware"),
            f"BUILD={firmware_build}",
            f"RISCV_TC={tool_prefix}",
            f"MOSAIC_GENERATED_ROOT={generated_root}",
            "all",
        ],
        check=True,
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
    )
    combined_hex = (firmware_build / "mosaic_fw.hex").read_text()
    assert combined_hex.startswith("@00000000\n")
    deployment = json.loads((firmware_build / "mosaic_flash.json").read_text())
    assert deployment["boot_straps"] == {
        "boot_select": 1,
        "execute_from_flash": 1,
    }
    assert deployment["table"]["entry_count"] == 2
    assert [item["sram_destination"] for item in deployment["images"][1:]] == [
        "0x00001000",
        "0x00002000",
    ]
