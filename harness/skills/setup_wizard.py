"""setup skill — omp-style first-run driver/provider selection.

oh-my-pi's onboarding picks the model provider for its agent loop. oh-my-soc
stores the DRIVER that owns the visible planning/tool loop:

  deterministic  auditable scope-aware workflow with the same live event transcript
                 (default; no model, no keys, CI-safe; not labelled as an LLM)
  claude         Claude Code interactive agent drives the documented CLI skills
  omp            oh-my-pi full TUI drives its oh_my_soc tool and skill cards
  api            built-in multi-turn model/tool/observation agent loop
                 (anthropic, openai-compatible, or OpenCode Go; the key is
                 read from an ENV VAR at call time — never stored)

Config lives at ~/.config/oh-my-soc/config.json (user-level, out of the
repo, so keys/choices never end up in git). The in-process API and deterministic
drivers enforce the gate policy in Python. External interactive drivers use
their native permission/UI model and must consume the deterministic CLI results.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ..core import SkillResult, log
from ..llm import (
    OPENCODE_GO_BASE_URL,
    OPENCODE_GO_DEFAULT_MODEL,
    OPENCODE_GO_ENV_KEY,
    normalize_api_config,
)

CONFIG_DIR = Path(os.environ.get("OH_MY_SOC_CONFIG_DIR",
                                 Path.home() / ".config" / "oh-my-soc"))
CONFIG_PATH = CONFIG_DIR / "config.json"

DRIVERS = ("deterministic", "claude", "omp", "api")
ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def load_user_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            log.warning(f"corrupt {CONFIG_PATH} — ignoring")
            return {}
        except OSError as error:
            log.warning(f"cannot read {CONFIG_PATH}: {error}")
            return {}
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError as error:
            log.warning(f"cannot secure {CONFIG_PATH} to mode 0600: {error}")
        return config
    return {}


def save_user_config(cfg: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    CONFIG_PATH.chmod(0o600)


def detect_environment() -> Dict[str, Any]:
    return {
        "claude": shutil.which("claude"),
        "omp": shutil.which("omp"),
        "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
        "opencode_key": bool(os.environ.get(OPENCODE_GO_ENV_KEY)),
    }


class SetupWizard:
    """Skill: choose + persist the intent driver (interactive or flags)."""

    def show(self) -> SkillResult:
        cfg = load_user_config()
        env = detect_environment()
        return SkillResult(
            ok=True, skill="setup",
            summary=("driver: " + cfg.get("driver", "deterministic (default — "
                     "run `oh-my-soc setup` to change)")),
            details={"config_path": str(CONFIG_PATH), "config": cfg,
                     "detected": env},
        )

    def configure(self, driver: Optional[str] = None,
                  api_kind: Optional[str] = None,
                  model: Optional[str] = None,
                  base_url: Optional[str] = None,
                  env_key: Optional[str] = None,
                  interactive: bool = True) -> SkillResult:
        env = detect_environment()

        if driver is None and interactive and sys.stdin.isatty():
            driver = self._ask_driver(env)
        if driver is None:
            return SkillResult(
                ok=False, skill="setup",
                summary="no driver chosen (non-interactive: pass --driver)",
                errors=[f"valid: {DRIVERS}"])
        if driver not in DRIVERS:
            return SkillResult(ok=False, skill="setup",
                               summary=f"unknown driver '{driver}'",
                               errors=[f"valid: {DRIVERS}"])

        cfg: Dict[str, Any] = {"driver": driver}
        warnings = []
        if driver == "claude" and not env["claude"]:
            warnings.append("`claude` not found on PATH — install Claude Code "
                            "or the driver will fail at dispatch time")
        if driver == "omp" and not env["omp"]:
            warnings.append("`omp` not found on PATH — install oh-my-pi "
                            "(refs/IP_Tools/oh-my-pi) first")
        if driver == "api":
            kind = api_kind
            if kind is None and interactive and sys.stdin.isatty():
                kind = self._ask(
                    "API kind [anthropic/openai-compatible/opencode-go]",
                    default="anthropic",
                )
                if kind.startswith("openai"):
                    kind = "openai"
            kind = kind or "anthropic"
            if kind not in ("anthropic", "openai", "opencode-go"):
                return SkillResult(ok=False, skill="setup",
                                   summary=f"unknown api kind '{kind}'",
                                   errors=["valid: anthropic, openai, opencode-go"])
            if kind == "opencode-go":
                default_env = OPENCODE_GO_ENV_KEY
                model = model or OPENCODE_GO_DEFAULT_MODEL
                base_url = base_url or OPENCODE_GO_BASE_URL
            else:
                default_env = ("ANTHROPIC_API_KEY" if kind == "anthropic"
                               else "OPENAI_API_KEY")
            api_cfg = {
                "kind": kind,
                "model": model,          # None -> harness default per kind
                "base_url": base_url,    # None -> provider default
                "env_key": env_key or default_env,
            }
            if not ENV_KEY_RE.fullmatch(api_cfg["env_key"]):
                return SkillResult(
                    ok=False,
                    skill="setup",
                    summary="invalid API key environment-variable name",
                    errors=[
                        "--env-key accepts a variable name such as "
                        f"{default_env}, never the key value itself"
                    ],
                )
            try:
                normalize_api_config(api_cfg)
            except (AttributeError, TypeError, ValueError) as error:
                return SkillResult(
                    ok=False,
                    skill="setup",
                    summary=f"invalid API configuration: {error}",
                    errors=[str(error)],
                )
            cfg["api"] = api_cfg
            if not os.environ.get(cfg["api"]["env_key"], ""):
                warnings.append(f"env var {cfg['api']['env_key']} is not set "
                                f"— the api driver will fail until it is "
                                f"(the key itself is never stored)")

        save_user_config(cfg)
        return SkillResult(
            ok=True, skill="setup",
            summary=f"driver '{driver}' saved to {CONFIG_PATH}"
                    + (f" | {'; '.join(warnings)}" if warnings else ""),
            details={"config": cfg, "detected": env, "warnings": warnings},
        )

    # ── interactive helpers ──────────────────────────────────────────

    @staticmethod
    def _ask(question: str, default: str = "") -> str:
        reply = input(f"{question}{f' [{default}]' if default else ''}: ").strip()
        return reply or default

    def _ask_driver(self, env: Dict[str, Any]) -> str:
        print("\noh-my-soc — choose your intent driver (like oh-my-pi's "
              "provider picker, but for the deterministic harness):\n")
        rows = [
            ("1", "deterministic",
            "visible scope-aware workflow; no LLM/keys (CI-safe)"),
            ("2", "claude",
             "Claude Code interactive agent drives the skill cards"
             + ("  [detected]" if env["claude"] else "  [NOT on PATH]")),
            ("3", "omp",
             "oh-my-pi full TUI drives the same cards"
             + ("  [detected]" if env["omp"] else "  [NOT on PATH]")),
            ("4", "api",
             "built-in streaming LLM tool loop"
             + (f"  [{OPENCODE_GO_ENV_KEY} set]" if env["opencode_key"]
                else ("  [ANTHROPIC_API_KEY set]" if env["anthropic_key"]
                else ("  [OPENAI_API_KEY set]" if env["openai_key"] else "")))),
        ]
        for n, name, desc in rows:
            print(f"  {n}) {name:14s} {desc}")
        choice = self._ask("\nSelect 1-4", default="1")
        return {r[0]: r[1] for r in rows}.get(choice, choice)
