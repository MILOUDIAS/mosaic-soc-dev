"""Tests for the setup wizard (driver/provider picker) and the optional LLM
intent-translation path (fake backend — no network, no keys).

Run from the repo root: python3 -m pytest test/test_x_heep_gen/test_setup_wizard.py
"""

import json
import os
import stat
import pathlib
import subprocess
import sys

import pytest

directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(directory))

REPO_ROOT = directory


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Point the wizard at a throwaway config dir and reload the module."""
    monkeypatch.setenv("OH_MY_SOC_CONFIG_DIR", str(tmp_path / "cfg"))
    import importlib
    import harness.skills.setup_wizard as sw

    importlib.reload(sw)
    yield sw
    importlib.reload(sw)  # restore module-level paths for other tests


def test_default_driver_without_config(isolated_config):
    sw = isolated_config
    result = sw.SetupWizard().show()
    assert result.ok
    assert "deterministic" in result.summary


def test_configure_api_non_interactive(isolated_config):
    sw = isolated_config
    result = sw.SetupWizard().configure(
        driver="api", api_kind="anthropic", interactive=False
    )
    assert result.ok
    cfg = json.loads(sw.CONFIG_PATH.read_text())
    assert cfg["driver"] == "api"
    assert cfg["api"]["env_key"] == "ANTHROPIC_API_KEY"
    assert stat.S_IMODE(sw.CONFIG_PATH.stat().st_mode) == 0o600
    # the key itself must NEVER appear in the config
    assert "sk-" not in sw.CONFIG_PATH.read_text()


def test_configure_opencode_go_preset_is_self_describing(isolated_config, monkeypatch):
    sw = isolated_config
    monkeypatch.setenv("OPENCODE_API_KEY", "oc-secret-that-must-not-be-stored")
    result = sw.SetupWizard().configure(
        driver="api", api_kind="opencode-go", interactive=False
    )
    assert result.ok
    raw = sw.CONFIG_PATH.read_text()
    cfg = json.loads(raw)
    assert cfg["api"] == {
        "kind": "opencode-go",
        "model": "kimi-k2.7-code",
        "base_url": "https://opencode.ai/zen/go/v1",
        "env_key": "OPENCODE_API_KEY",
    }
    assert "oc-secret-that-must-not-be-stored" not in raw
    assert stat.S_IMODE(sw.CONFIG_PATH.stat().st_mode) == 0o600


def test_opencode_go_preset_rejects_messages_model(isolated_config):
    sw = isolated_config
    result = sw.SetupWizard().configure(
        driver="api",
        api_kind="opencode-go",
        model="qwen3.7-plus",
        interactive=False,
    )
    assert not result.ok
    assert "--api-kind anthropic" in result.summary
    assert not sw.CONFIG_PATH.exists()


def test_setup_rejects_secret_value_in_env_key_slot(isolated_config):
    sw = isolated_config
    result = sw.SetupWizard().configure(
        driver="api",
        api_kind="opencode-go",
        env_key="sk-user-pasted-a-secret",
        interactive=False,
    )
    assert not result.ok
    assert "variable name" in result.summary
    assert not sw.CONFIG_PATH.exists()


def test_cli_accepts_opencode_go_preset(tmp_path):
    config_dir = tmp_path / "config"
    env = {**os.environ, "OH_MY_SOC_CONFIG_DIR": str(config_dir)}
    env.pop("OPENCODE_API_KEY", None)
    result = subprocess.run(
        [
            str(REPO_ROOT / "oh-my-soc"),
            "--json",
            "setup",
            "--driver",
            "api",
            "--api-kind",
            "opencode-go",
            "--non-interactive",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"]
    assert payload["details"]["config"]["api"]["kind"] == "opencode-go"
    assert json.loads((config_dir / "config.json").read_text())["api"]["env_key"] == (
        "OPENCODE_API_KEY"
    )


def test_load_repairs_legacy_permissive_config_mode(isolated_config):
    sw = isolated_config
    sw.CONFIG_DIR.mkdir(parents=True)
    sw.CONFIG_PATH.write_text('{"driver": "deterministic"}\n')
    sw.CONFIG_PATH.chmod(0o644)
    assert sw.load_user_config()["driver"] == "deterministic"
    assert stat.S_IMODE(sw.CONFIG_PATH.stat().st_mode) == 0o600


def test_configure_rejects_unknown_driver(isolated_config):
    sw = isolated_config
    result = sw.SetupWizard().configure(driver="skynet", interactive=False)
    assert not result.ok


def test_non_interactive_without_driver_fails(isolated_config):
    sw = isolated_config
    result = sw.SetupWizard().configure(interactive=False)
    assert not result.ok
    assert "--driver" in result.summary


def test_missing_binary_warns(isolated_config, monkeypatch):
    sw = isolated_config
    monkeypatch.setattr(sw.shutil, "which", lambda _: None)
    result = sw.SetupWizard().configure(driver="omp", interactive=False)
    assert result.ok  # saved, but with a loud warning
    assert any("not found on PATH" in w for w in result.details["warnings"])


def test_llm_intent_uses_fake_backend(isolated_config, monkeypatch):
    """driver=api routes soc-from-prompt --llm through translate_intent, and
    the result STILL goes through the deterministic repairs/gates."""
    sw = isolated_config
    sw.SetupWizard().configure(driver="api", api_kind="anthropic", interactive=False)

    import harness.llm as llm
    import harness.skills.soc_from_prompt as sfp

    def _fake_translate(prompt, api_cfg, cores, periphs, aliases=None):
        return {
            "cores": [{"ip": "picorv32", "count": 2, "role": "atlas"}],
            "sram_kb": 64,
            "tdu": True,
            "peripherals": ["uart"],
            "unrecognized": [],
        }

    monkeypatch.setattr(llm, "translate_intent", _fake_translate)
    result = sfp.SocFromPrompt(REPO_ROOT).plan("whatever", use_llm=True)
    assert result.ok
    intent = result.details["intent"]
    # llm provenance is marked
    assert any("[llm]" in m for m in intent["matched"])
    # deterministic repairs still applied on top of the LLM translation
    assert any("titan" in r for r in intent["repairs"])
    assert intent["unrecognized"] == []


def test_llm_failure_falls_back_to_grammar(isolated_config, monkeypatch):
    sw = isolated_config
    sw.SetupWizard().configure(driver="api", api_kind="anthropic", interactive=False)
    import harness.llm as llm
    import harness.skills.soc_from_prompt as sfp

    def _boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(llm, "translate_intent", _boom)
    result = sfp.SocFromPrompt(REPO_ROOT).plan("two serv workers, tdu", use_llm=True)
    assert result.ok  # grammar fallback
    assert any(
        "serv" in m and "[llm]" not in m for m in result.details["intent"]["matched"]
    )


def test_llm_ignored_when_driver_not_api(isolated_config, monkeypatch):
    sw = isolated_config
    sw.SetupWizard().configure(driver="deterministic", interactive=False)
    import harness.llm as llm
    import harness.skills.soc_from_prompt as sfp

    def _should_not_run(*a, **k):
        raise AssertionError("LLM called despite deterministic driver")

    monkeypatch.setattr(llm, "translate_intent", _should_not_run)
    result = sfp.SocFromPrompt(REPO_ROOT).plan("one serv, tdu", use_llm=True)
    assert result.ok
