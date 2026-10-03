"""Shared types, validation, and logging for oh-my-soc skills."""

import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from util.xheep_gen.core_registry import (
    SCI_CORES,
    SIM_ONLY_CORES,
    VALID_BUS,
    VALID_CORE_IPS,
    VALID_ISAS,
    VALID_PERIPHERALS,
    VALID_ROLES,
    VALID_SCHED_MODES,
    VALID_TARGETS,
    validate_soc_config,
)

log = logging.getLogger("oh-my-soc")

# ── Repo root ────────────────────────────────────────────────────────

def _is_checkout(path: Path) -> bool:
    return ((path / "util" / "xheep_gen" / "core_registry.py").is_file()
            and (path / "configs").is_dir())


def _find_repo_root() -> Path:
    """The MOSAIC checkout this process acts on.

    The package location is only the last resort. An agent host launches the
    MCP server from its own install or plugin cache; flows, configs and
    evidence must still land in the user's checkout, not in site-packages.
    Order: OH_MY_SOC_REPO, then the first checkout at or above the cwd, then
    the package parent (editable install).
    """
    pinned = os.environ.get("OH_MY_SOC_REPO")
    if pinned:
        root = Path(pinned).expanduser().resolve()
        if not _is_checkout(root):
            # Loud on purpose: silently acting on some other tree is worse.
            raise RuntimeError(
                f"OH_MY_SOC_REPO={pinned} is not a MOSAIC checkout "
                "(no util/xheep_gen/core_registry.py or configs/)")
        return root
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _is_checkout(candidate):
            return candidate
    return Path(__file__).resolve().parents[1]


REPO_ROOT = _find_repo_root()


# Known model credentials and the actively configured provider credential
# belong to the in-process provider boundary.  EDA, build, parser, and test
# subprocesses need the normal host environment (PATH, PDK roots, compiler
# settings, etc.), but never need those model-provider secrets.
# Keep this as a denylist rather than an environment allowlist so existing
# toolchains retain their required variables.
MODEL_API_SECRET_ENV_VARS = frozenset({
    "AI_GATEWAY_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "CEREBRAS_API_KEY",
    "CODEX_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIREWORKS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "GROQ_API_KEY",
    "HUGGINGFACE_API_KEY",
    "MISTRAL_API_KEY",
    "OLLAMA_API_KEY",
    "OPENCODE_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_ACCESS_TOKEN",
    "OPENROUTER_API_KEY",
    "PERPLEXITY_API_KEY",
    "REPLICATE_API_TOKEN",
    "TOGETHER_API_KEY",
    "TOGETHER_AI_API_KEY",
    "VERCEL_AI_GATEWAY_API_KEY",
    "XAI_API_KEY",
})


def _configured_model_api_env_key() -> Optional[str]:
    """Return the configured provider key *name*, never its value.

    ``setup_wizard`` imports :mod:`harness.core`, so importing it here would
    create a circular dependency.  Reading the small user config directly also
    keeps this final process boundary available to every ``run_cmd`` caller.
    Errors are deliberately silent: known secret names are still stripped and
    neither config contents nor credential values reach logs.
    """
    config_dir = Path(os.environ.get(
        "OH_MY_SOC_CONFIG_DIR",
        Path.home() / ".config" / "oh-my-soc",
    ))
    try:
        config = json.loads((config_dir / "config.json").read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(config, dict):
        return None
    api = config.get("api")
    if not isinstance(api, dict):
        return None
    env_key = api.get("env_key")
    if (
        not isinstance(env_key, str)
        or not env_key
        or "=" in env_key
        or "\0" in env_key
    ):
        return None
    return env_key


def build_subprocess_env(
    overlays: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build a normal tool environment with known/configured credentials removed.

    The caller's environment is retained and ``overlays`` are applied first,
    then the credential denylist is enforced.  Consequently an explicit flow
    overlay cannot accidentally re-introduce a provider secret.  Environment
    names are compared case-insensitively for the same behavior on Windows.
    """
    child_env = dict(os.environ)
    if overlays:
        child_env.update(overlays)

    secret_names = set(MODEL_API_SECRET_ENV_VARS)
    configured = _configured_model_api_env_key()
    if configured:
        secret_names.add(configured)
    secret_names = {name.upper() for name in secret_names}
    for name in list(child_env):
        if name.upper() in secret_names:
            child_env.pop(name, None)
    return child_env


# ── Result types ─────────────────────────────────────────────────────

@dataclass
class SkillResult:
    """Standard return type for all skills."""

    ok: bool
    skill: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


@dataclass
class RunReport:
    """Structured report from an EDA flow run."""

    skill: str
    config: str
    elapsed_s: float
    exit_code: int
    log_path: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ── Validation helpers ───────────────────────────────────────────────
#
def validate_config(cfg: Dict[str, Any], allow_sim_only: bool = True) -> List[str]:
    """Validate a mosaic.yaml-shaped dict. Returns list of errors (empty = valid).

    allow_sim_only=False additionally rejects SIM_ONLY_CORES (cva6/rocket/boom)
    — used when authoring tapeout-oriented configs.
    """
    return validate_soc_config(cfg, allow_sim_only=allow_sim_only)


# ── Process runner ───────────────────────────────────────────────────

def run_cmd(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = 3600,
    env: Optional[Dict[str, str]] = None,
    on_output: Optional[Callable[[str], None]] = None,
) -> subprocess.CompletedProcess:
    """Run a command with timeout protection and optional live output.

    Output is always drained through the same process-group-aware runner.
    Interactive callers receive each merged stdout/stderr line while the
    process is alive; machine callers omit ``on_output`` and only receive the
    bounded capture after completion. ``env`` is an overlay on the normal host
    environment; known and configured model API credentials are removed after
    applying it.
    """
    log.info(f"Running: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd or REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=build_subprocess_env(env),
        start_new_session=True,
    )
    lines: List[str] = []
    captured_chars = 0
    max_capture_chars = 8 * 1024 * 1024
    output_queue: "queue.Queue[Optional[str]]" = queue.Queue()

    def _read_output() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=_read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    stream_done = False
    try:
        while not stream_done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    cmd, timeout, output="".join(lines)
                )
            try:
                line = output_queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if proc.poll() is not None and not reader.is_alive():
                    break
                continue
            if line is None:
                stream_done = True
                continue
            lines.append(line)
            captured_chars += len(line)
            while captured_chars > max_capture_chars and len(lines) > 1:
                captured_chars -= len(lines.pop(0))
            if on_output is not None:
                on_output(line.rstrip("\r\n"))
        return_code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except BaseException:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=1)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
        raise
    reader.join(timeout=1)
    result = subprocess.CompletedProcess(
        cmd, return_code, stdout="".join(lines), stderr=""
    )
    if result.returncode != 0:
        log.warning(f"Command exited {result.returncode}")
    return result


# ── Config I/O ───────────────────────────────────────────────────────

def load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file (uses PyYAML or fallback parser)."""
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def dump_yaml(data: Dict[str, Any], path: Path) -> None:
    """Write a YAML file with clean formatting."""
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False,
                  width=120, allow_unicode=True)
    log.info(f"Wrote {path}")


def load_json(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def dump_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log.info(f"Wrote {path}")
