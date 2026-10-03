"""The toolchain is pinned in one place, and nothing silently falls back.

Every signoff result the project holds came from LibreLane 3.0.0's own nix-eda
and nixpkgs (flow/librelane/flake.lock). The simulation toolchain (root
flake.nix, `nix develop .#sim`) must be built from the SAME revisions, or "the
pinned toolchain" means two different things. And the testbench runners used
to default to paths that exist on one machine, then fall back to PATH -- where
this machine's Verilator is the 5.047-devel build that miscompiles cv32e40x
(bug 21).
"""

import re
import subprocess
from pathlib import Path

import pytest

from harness.core import REPO_ROOT
from harness.skills.flow_runner import FLOWS, HOST_FLOWS, sim_shell_command
from harness.toolchain import (
    LOCK_PATHS, PINS, doctor, lock_revs, riscv_env, runtime,
)

ROOT_LOCK = REPO_ROOT / "flake.lock"
FLOW_LOCK = REPO_ROOT / "flow/librelane/flake.lock"


def test_the_sim_and_signoff_locks_pin_the_same_eda_revisions():
    sim, signoff = lock_revs(ROOT_LOCK), lock_revs(FLOW_LOCK)
    assert set(sim) == set(LOCK_PATHS)
    for name in LOCK_PATHS:
        assert sim[name]["rev"] == signoff[name]["rev"], name
        assert sim[name]["narHash"] == signoff[name]["narHash"], name


def test_the_root_flake_takes_librelane_at_the_pinned_version():
    text = (REPO_ROOT / "flake.nix").read_text()
    assert f'"github:librelane/librelane/{PINS["librelane"]}"' in text
    assert f'version = "{PINS["verilator"]}"' in text


def test_tools_sh_and_the_harness_pin_the_same_verilator():
    text = (REPO_ROOT / "tb/tools.sh").read_text()
    assert f'MOSAIC_VERILATOR_VERSION="{PINS["verilator"]}"' in text


# ── the testbench runners ─────────────────────────────────────────────

# Every script that invokes Verilator (directly or as cocotb's SIM), found by
# content so a new runner cannot be forgotten.
_RUNS_VERILATOR = re.compile(r"^\s*verilator\s|SIM=verilator", re.M)
RUNNERS = sorted(
    [p for p in (REPO_ROOT / "tb").rglob("*.sh")
     if p.name != "tools.sh" and _RUNS_VERILATOR.search(p.read_text())]
    + [REPO_ROOT / "harness/templates/tb/run.sh.mako"])


def test_the_runner_list_is_not_empty():
    assert len(RUNNERS) >= 15


@pytest.mark.parametrize("path", RUNNERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_runner_hardcodes_a_machine_path_or_bypasses_the_pin(path):
    text = path.read_text()
    assert "/mnt/" not in text and "/opt/riscv" not in text
    assert "mosaic_need_verilator" in text, "runs verilator without the pin check"


def _run_tools(snippet, env):
    return subprocess.run(
        ["bash", "-c", f'source "{REPO_ROOT}/tb/tools.sh"; {snippet}'],
        env=env, capture_output=True, text=True)


def _fake_bin(tmp_path, name, output):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tool = bin_dir / name
    tool.write_text(f"#!/bin/sh\necho '{output}'\n")
    tool.chmod(0o755)
    return bin_dir


def test_tools_sh_refuses_the_wrong_verilator_on_path(tmp_path):
    bin_dir = _fake_bin(tmp_path, "verilator",
                        "Verilator 5.047 devel rev v5.046-70-g07ed6aef5 (mod)")
    out = _run_tools("mosaic_need_verilator; echo REACHED",
                     {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert out.returncode == 2 and "REACHED" not in out.stdout
    assert "nix develop .#sim" in out.stderr and "VERILATOR_PIN" in out.stderr


def test_tools_sh_accepts_the_pinned_verilator_on_path(tmp_path):
    bin_dir = _fake_bin(tmp_path, "verilator", "Verilator 5.050 2026-01-01 rev v5.050")
    out = _run_tools("mosaic_need_verilator; echo REACHED",
                     {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert out.returncode == 0 and "REACHED" in out.stdout


def test_an_explicit_pin_is_used_as_given(tmp_path):
    prefix = tmp_path / "v"
    (prefix / "usr/bin").mkdir(parents=True)
    tool = prefix / "usr/bin/verilator"
    tool.write_text("#!/bin/sh\necho 'Verilator 5.049 x'\n")
    tool.chmod(0o755)
    out = _run_tools("mosaic_need_verilator; command -v verilator",
                     {"PATH": "/usr/bin:/bin", "VERILATOR_PIN": str(prefix)})
    assert out.returncode == 0 and str(tool) in out.stdout


def test_tools_sh_refuses_a_riscv_tc_that_is_not_there(tmp_path):
    out = _run_tools("mosaic_need_riscv_tc; echo REACHED",
                     {"PATH": "/usr/bin:/bin",
                      "RISCV_TC": str(tmp_path / "bin/riscv32-none-elf")})
    assert out.returncode == 2 and "REACHED" not in out.stdout
    assert "nix develop .#sim" in out.stderr


def test_tools_sh_finds_a_riscv_toolchain_on_path(tmp_path):
    bin_dir = _fake_bin(tmp_path, "riscv32-none-elf-gcc", "gcc")
    out = _run_tools('mosaic_need_riscv_tc; echo "TC=$TC"',
                     {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    assert out.returncode == 0
    assert f"TC={bin_dir}/riscv32-none-elf" in out.stdout


def test_the_nix_prefix_yields_the_boot_rom_variables():
    """The boot ROM calls $(RISCV_XHEEP)/bin/$(COMPILER_PREFIX)elf-gcc."""
    env = riscv_env({"RISCV_TC": "/nix/store/x-tc/bin/riscv32-none-elf"})
    assert env == {"RISCV_XHEEP": "/nix/store/x-tc", "COMPILER_PREFIX": "riscv32-none-"}
    assert riscv_env({"RISCV_TC": "/a/bin/riscv32-none-elf",
                      "COMPILER_PREFIX": "keep-"}) == {"RISCV_XHEEP": "/a"}
    assert riscv_env({}) == {}


# ── flow runner ───────────────────────────────────────────────────────

def test_simulation_flows_enter_the_pinned_shell():
    cmd = sim_shell_command(["bash", "tb/x.sh"], Path("/repo"), {},
                            which=lambda _: "/nix/bin/nix")
    assert cmd == ["nix", "develop", "/repo#sim", "--command", "bash", "tb/x.sh"]


def test_inside_a_nix_shell_or_without_nix_the_command_runs_as_is():
    cmd = ["bash", "tb/x.sh"]
    assert sim_shell_command(cmd, Path("/r"), {"IN_NIX_SHELL": "impure"},
                             which=lambda _: "/nix/bin/nix") == cmd
    assert sim_shell_command(cmd, Path("/r"), {}, which=lambda _: None) == cmd


def test_only_the_named_flows_stay_on_the_host():
    assert HOST_FLOWS <= set(FLOWS)
    assert {"tb-soc-generic", "tb-soc-titan", "mosaic-gen-config"}.isdisjoint(HOST_FLOWS)


# ── doctor ────────────────────────────────────────────────────────────

def _versions(table):
    return lambda argv: table.get(Path(argv[0]).name, "")


def test_doctor_passes_on_the_pinned_tools(tmp_path):
    bin_dir = _fake_bin(tmp_path, "verilator", "Verilator 5.050 2026 rev v5.050")
    _fake_bin(tmp_path, "riscv32-none-elf-gcc", "riscv32-none-elf-gcc (GCC) 14.3.0")
    result = doctor({"PATH": str(bin_dir), "MOSAIC_TOOLCHAIN": "nix"},
                    home=tmp_path,
                    version=_versions({"verilator": "Verilator 5.050 2026 rev v5.050",
                                       "riscv32-none-elf-gcc": "gcc 14.3.0"}))
    assert result.ok, result.errors
    assert result.details["runtime"] == "nix"
    assert result.details["pins"]["lock"]["nix-eda"]["rev"]


def test_doctor_fails_on_the_bug21_verilator_and_names_the_fix(tmp_path):
    bin_dir = _fake_bin(tmp_path, "verilator", "Verilator 5.047 devel")
    result = doctor({"PATH": str(bin_dir)}, home=tmp_path,
                    version=_versions({"verilator": "Verilator 5.047 devel"}))
    assert not result.ok
    assert result.details["runtime"] == "path"
    assert any("verilator" in e and "nix develop .#sim" in e for e in result.errors)
    assert any("riscv-gcc" in e for e in result.errors)


def test_doctor_warns_about_an_ambient_pdk_root(tmp_path):
    result = doctor({"PATH": "", "PDK_ROOT": "/elsewhere/IHP-Open-PDK",
                     "PDK": "ihp-sg13g2"}, home=tmp_path, version=_versions({}))
    warnings = " ".join(result.details["warnings"])
    assert "PDK_ROOT=/elsewhere/IHP-Open-PDK" in warnings
    assert "PDK=ihp-sg13g2" in warnings


def test_the_container_runtime_is_labelled():
    assert runtime({"MOSAIC_TOOLCHAIN": "iic:2026.09"}) == "iic:2026.09"
    assert runtime({"IN_NIX_SHELL": "impure"}) == "nix-other"
    assert runtime({}) == "path"
    launcher = (REPO_ROOT / "tools/iic-osic.sh").read_text()
    assert re.search(r'IIC_TAG:-2026\.09', launcher)
    assert "MOSAIC_TOOLCHAIN=iic:$TAG" in launcher and ":latest" not in launcher
