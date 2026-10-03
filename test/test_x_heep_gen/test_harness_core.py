"""Tests for harness/core.py + flow_runner P0 hygiene (oh-my-soc Phase 2).

Guards:
- registry single-sourcing: the AST-read core sets in harness.core must equal
  the real util/xheep_gen values (the drift that silently broke validation of
  the 2026-07 cores can never return).
- every shipped configs/mosaic_*.yaml validates (would have caught the rv64
  rejection).
- FLOWS entries point at real scripts; flow timeouts are handled (regression
  for the missing `import subprocess` NameError).

Run from the repo root: python3 -m pytest test/test_x_heep_gen/test_harness_core.py
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(directory))  # for `harness`
sys.path.append(
    str(directory.joinpath("util/xheep_gen"))
)  # test shim (like test_new_cores)

from harness import core as hcore
from harness.skills.config_author import ConfigAuthor, PRESETS, TAPEOUT_PRESETS
from harness.skills import flow_runner as fr

REPO_ROOT = directory


# ── registry single-sourcing ─────────────────────────────────────────


def test_registry_sync_available_cpus():
    from cpu.cpu import CPU  # real import via the shim

    assert hcore.VALID_CORE_IPS == set(CPU.AVAILABLE_CPUS)


def test_registry_sync_sci_cores():
    import mosaic_config  # real import via the shim

    assert hcore.SCI_CORES == set(mosaic_config.SCI_CORES)


def test_sim_only_subset():
    assert hcore.SIM_ONLY_CORES <= hcore.VALID_CORE_IPS


# ── shipped configs all validate (anti-drift guard) ──────────────────


@pytest.mark.parametrize(
    "cfg_path",
    sorted((REPO_ROOT / "configs").glob("mosaic_*.yaml")),
    ids=lambda p: p.name,
)
def test_all_shipped_configs_validate(cfg_path):
    result = ConfigAuthor().validate_file(cfg_path)
    assert result.ok, f"{cfg_path.name}: {result.errors}"


def test_poc_mosaic_yaml_validates():
    result = ConfigAuthor().validate_file(REPO_ROOT / "mosaic.yaml")
    assert result.ok, result.errors


def test_tutorial_config_validates():
    result = ConfigAuthor().validate_file(
        REPO_ROOT / "tutorial" / "configs" / "tutorial_soc.yaml"
    )
    assert result.ok, result.errors


# ── ISA / boot_addr / sim-only validation semantics ──────────────────


def test_isa_regex_accepts_rv64():
    cfg = {
        "soc": {
            "profile": "testbench",
            "cores": [{"ip": "rocket", "isa": "rv64imc", "count": 1,
                       "role": "atlas", "boot_addr": 0x1000}],
            "memory": {"sram_kb": 32},
            "bus": "obi",
            "scheduler": {"mode": "dynamic"},
            "peripherals": [],
        }
    }
    assert hcore.validate_config(cfg) == []


def test_sim_only_rejected_for_tapeout():
    cfg = {
        "soc": {
            "profile": "testbench",
            "cores": [{"ip": "cva6", "isa": "rv32imc", "count": 1, "role": "titan"}],
            "memory": {"sram_kb": 32},
            "bus": "obi",
            "scheduler": {"mode": "dynamic"},
            "peripherals": [],
        }
    }
    assert hcore.validate_config(cfg, allow_sim_only=True) == []
    errs = hcore.validate_config(cfg, allow_sim_only=False)
    assert any("SIMULATION-ONLY" in e for e in errs)


def test_bad_boot_addr_rejected():
    cfg = {
        "soc": {
            "cores": [
                {"ip": "serv", "count": 1, "role": "titan", "boot_addr": "not-an-addr"}
            ],
            "memory": {"sram_kb": 32},
            "bus": "obi",
            "scheduler": {"mode": "static"},
            "peripherals": [],
        }
    }
    errs = hcore.validate_config(cfg)
    assert any("boot_addr" in e for e in errs)


# ── config-author additions ──────────────────────────────────────────


@pytest.mark.parametrize("core", ["picorv32", "snitch", "cva6", "rocket", "boom"])
def test_new_core_defaults_generate(core, tmp_path):
    author = ConfigAuthor()
    result = author.generate(
        name=f"t_{core}",
        cores=[
            {"ip": "cv32e20", "count": 1, "role": "titan"},
            {"ip": core, "count": 1, "role": "nano"},
        ],
        output_path=tmp_path / f"{core}.yaml",
    )
    assert result.ok, result.errors
    # sim_only metadata never reaches the YAML
    text = (tmp_path / f"{core}.yaml").read_text()
    assert "sim_only" not in text


def test_wake_demo_config_matches_shipped_shape(tmp_path):
    author = ConfigAuthor()
    result = author.wake_demo_config("picorv32", output_path=tmp_path / "wd.yaml")
    assert result.ok, result.errors
    cfg = result.details["config"]["soc"]
    ips = [c["ip"] for c in cfg["cores"]]
    assert ips == ["cv32e20", "picorv32", "picorv32"]
    roles = [c["role"] for c in cfg["cores"]]
    assert roles == ["titan", "atlas", "nano"]
    boots = [c.get("boot_addr") for c in cfg["cores"]]
    assert boots == [None, 0x1000, 0x2000]
    assert cfg["scheduler"] == {"tdu": True, "mode": "dynamic"}
    assert cfg["memory"]["sram_kb"] == 32


def test_config_author_skips_explicit_worker_boot_slots(tmp_path):
    author = ConfigAuthor()
    result = author.generate(
        name="mixed_boot_slots",
        cores=[
            {"ip": "cv32e20", "count": 1, "role": "titan"},
            {
                "ip": "fazyrv",
                "count": 1,
                "role": "atlas",
                "boot_addr": 0x1000,
            },
            {"ip": "serv", "count": 1, "role": "nano"},
        ],
        output_path=tmp_path / "mixed_boot_slots.yaml",
    )
    assert result.ok, result.errors
    assert [
        core.get("boot_addr") for core in result.details["config"]["soc"]["cores"]
    ] == [None, 0x1000, 0x2000]


@pytest.mark.parametrize("core", ["rocket", "boom"])
def test_config_author_assigns_berkeley_titan_translation_boot(core, tmp_path):
    result = ConfigAuthor().generate(
        name=f"{core}_controller",
        cores=[
            {"ip": core, "count": 1, "role": "titan"},
            {"ip": "serv", "count": 1, "role": "nano"},
        ],
        output_path=tmp_path / f"{core}_controller.yaml",
    )
    assert result.ok, result.errors
    soc = result.details["config"]["soc"]
    assert soc["profile"] == "testbench"
    assert soc["target"] == "simulation"
    assert soc["cores"][0]["boot_addr"] == 0x180
    assert soc["cores"][1]["boot_addr"] == 0x1000


def test_tapeout_presets_have_no_sim_only_cores():
    for name in TAPEOUT_PRESETS:
        soc = PRESETS[name]["soc"]
        ips = {c["ip"] for c in soc["cores"]}
        assert not (ips & hcore.SIM_ONLY_CORES), f"preset {name} has sim-only cores"


# ── flow-runner hygiene ──────────────────────────────────────────────


def test_flow_paths_exist():
    for name, spec in fr.FLOWS.items():
        cwd = REPO_ROOT / spec.get("cwd", ".")
        assert cwd.is_dir(), f"{name}: cwd {cwd} missing"
        cmd = spec.get("cmd") or spec.get("cmd_prefix")
        # script-based flows: the script path must exist
        if cmd and cmd[0] == "bash":
            assert (REPO_ROOT / cmd[1]).is_file(), f"{name}: {cmd[1]} missing"


def test_timeout_returns_skillresult(monkeypatch):
    runner = fr.FlowRunner()

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(fr, "run_cmd", _boom)
    result = runner.run("tb-tl-obi")
    assert not result.ok
    assert "timed out" in result.summary


def test_env_config_key_plumbing(monkeypatch):
    """run.sh flows must receive the config via MOSAIC_CFG env, not argv."""
    captured = {}

    class _P:
        returncode = 0
        stdout = "EXIT SUCCESS"
        stderr = ""

    def _capture(cmd, cwd=None, timeout=0, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _P()

    monkeypatch.setattr(fr, "run_cmd", _capture)
    result = fr.FlowRunner().run("tb-soc-wake", config="configs/mosaic_picorv32.yaml")
    assert result.ok
    assert captured["env"]["MOSAIC_CFG"] == "configs/mosaic_picorv32.yaml"
    assert "configs/mosaic_picorv32.yaml" not in captured["cmd"]


def test_run_cmd_scrubs_model_credentials_but_preserves_tool_env(
    monkeypatch, tmp_path, caplog,
):
    """Provider secrets must stop at the in-process model boundary."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "driver": "api",
        "api": {"env_key": "MY_PRIVATE_GO_KEY"},
    }))
    monkeypatch.setenv("OH_MY_SOC_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("OPENAI_API_KEY", "openai-value-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-value-must-not-leak")
    monkeypatch.setenv("OPENCODE_API_KEY", "opencode-value-must-not-leak")
    monkeypatch.setenv("MY_PRIVATE_GO_KEY", "custom-value-must-not-leak")
    monkeypatch.setenv("EDA_KEEP_ME", "present")

    program = (
        "import json,os; print(json.dumps({"
        "'path': bool(os.environ.get('PATH')),"
        "'keep': os.environ.get('EDA_KEEP_ME'),"
        "'overlay': os.environ.get('MOSAIC_CFG'),"
        "'openai': 'OPENAI_API_KEY' in os.environ,"
        "'anthropic': 'ANTHROPIC_API_KEY' in os.environ,"
        "'opencode': 'OPENCODE_API_KEY' in os.environ,"
        "'custom': 'MY_PRIVATE_GO_KEY' in os.environ}))"
    )
    result = hcore.run_cmd(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env={
            "MOSAIC_CFG": "configs/test.yaml",
            # An explicit overlay cannot bypass final sanitization.
            "OPENAI_API_KEY": "overlay-value-must-not-leak",
        },
    )
    observed = json.loads(result.stdout)
    assert observed == {
        "path": True,
        "keep": "present",
        "overlay": "configs/test.yaml",
        "openai": False,
        "anthropic": False,
        "opencode": False,
        "custom": False,
    }
    for value in (
        "openai-value-must-not-leak",
        "anthropic-value-must-not-leak",
        "opencode-value-must-not-leak",
        "custom-value-must-not-leak",
        "overlay-value-must-not-leak",
    ):
        assert value not in caplog.text
        assert value not in result.stdout


def test_flow_runner_passes_only_sanitized_env_to_tool(
    monkeypatch, tmp_path,
):
    """Flow-level overlays retain tool inputs but cannot carry API keys."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "driver": "api",
        "api": {"env_key": "CUSTOM_PROVIDER_SECRET"},
    }))
    monkeypatch.setenv("OH_MY_SOC_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    monkeypatch.setenv("CUSTOM_PROVIDER_SECRET", "secret")
    monkeypatch.setenv("EDA_PARENT_FLAG", "parent")
    captured = {}

    class _P:
        returncode = 0
        stdout = "EXIT SUCCESS"
        stderr = ""

    def _capture(cmd, cwd=None, timeout=0, env=None):
        captured["env"] = env
        return _P()

    monkeypatch.setattr(fr, "run_cmd", _capture)
    result = fr.FlowRunner().run(
        "tb-soc-wake",
        config="configs/mosaic_picorv32.yaml",
        env={
            "FLOW_TOOL_FLAG": "overlay",
            "OPENCODE_API_KEY": "explicit-secret",
        },
    )
    assert result.ok
    assert captured["env"]["PATH"] == os.environ["PATH"]
    assert captured["env"]["EDA_PARENT_FLAG"] == "parent"
    assert captured["env"]["FLOW_TOOL_FLAG"] == "overlay"
    assert captured["env"]["MOSAIC_CFG"] == "configs/mosaic_picorv32.yaml"
    assert "OPENCODE_API_KEY" not in captured["env"]
    assert "CUSTOM_PROVIDER_SECRET" not in captured["env"]


def test_config_on_wrong_flow_errors():
    result = fr.FlowRunner().run("firmware-build", config="x.yaml")
    assert not result.ok
    assert "does not accept" in result.summary


def test_require_exit_success_gates(monkeypatch):
    """A sim that never prints EXIT SUCCESS must FAIL even with rc 0."""

    class _P:
        returncode = 0
        stdout = "### RESULT: no EXIT SUCCESS"
        stderr = ""

    monkeypatch.setattr(fr, "run_cmd", lambda *a, **k: _P())
    result = fr.FlowRunner().run("tb-soc-wake", config="c.yaml")
    assert not result.ok


def test_launcher_executable():
    """./oh-my-soc must run from any cwd and exit 0 on a trivial skill."""
    launcher = REPO_ROOT / "oh-my-soc"
    assert launcher.exists()
    assert launcher.stat().st_mode & 0o111, "launcher not executable"
    proc = subprocess.run(
        [str(launcher), "config-author", "presets"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "presets available" in proc.stdout


def test_pytest_parser():
    parsed = fr._parse_pytest("....\n35 passed in 1.27s\n")
    assert parsed == {"passed": 35, "all_pass": True}
    parsed = fr._parse_pytest("2 failed, 33 passed in 2s")
    assert parsed["failed"] == 2 and not parsed["all_pass"]
