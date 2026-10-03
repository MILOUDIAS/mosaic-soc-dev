"""`mosaic install --host ...`: wire the gated MCP server into an agent host.

Every host gets the SAME server (`python -m harness mcp-server`, plugin mode)
pointed at THIS checkout through MOSAIC_REPO and PYTHONPATH, so the host's
own working directory does not matter. The only per-host work is the config
shape, which was read from what each host's own `mcp add` writes:

    codex     ~/.codex/config.toml   [mcp_servers.mosaic] + .env table
    opencode  ~/.config/opencode/opencode.json(c)   mcp.servers.mosaic (v2)
    omp       ~/.omp/agent/mcp.json  mcpServers.mosaic, timeout 0
    claude    the plugin under plugins/mosaic (marketplace at the repo root)

Without --write this only prints. With --write it merges into the host's
config after a timestamped backup, and refuses rather than guess when the
existing file cannot be parsed (a JSONC file with comments, say).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .core import REPO_ROOT, SkillResult
from .mcp_server import SERVER_NAME, parse_scope_allowlist

#: Long flows run through a single tool call; hosts must not time them out.
TOOL_TIMEOUT_SEC = 4 * 3600


def server_python(repo: Path = REPO_ROOT) -> str:
    venv = repo / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else sys.executable


def server_env(scopes: Optional[str], repo: Path = REPO_ROOT) -> Dict[str, str]:
    allowlist = parse_scope_allowlist(scopes)       # raises on a typo
    return {
        "MOSAIC_REPO": str(repo),
        "PYTHONPATH": str(repo),
        "MOSAIC_SCOPES": ",".join(sorted(allowlist)),
    }


def _toml_str(value: str) -> str:
    return json.dumps(value)            # a JSON string is a valid TOML basic string


def codex_block(env: Dict[str, str], python: str) -> str:
    lines = [
        f"[mcp_servers.{SERVER_NAME}]",
        f"command = {_toml_str(python)}",
        'args = ["-m", "harness", "mcp-server"]',
        "startup_timeout_sec = 60",
        f"tool_timeout_sec = {TOOL_TIMEOUT_SEC}",
        "",
        f"[mcp_servers.{SERVER_NAME}.env]",
        *(f"{key} = {_toml_str(value)}" for key, value in env.items()),
    ]
    return "\n".join(lines) + "\n"


def opencode_entry(env: Dict[str, str], python: str) -> Dict[str, Any]:
    return {"type": "local",
            "command": [python, "-m", "harness", "mcp-server"],
            "environment": env}


def omp_entry(env: Dict[str, str], python: str) -> Dict[str, Any]:
    return {"command": python, "args": ["-m", "harness", "mcp-server"],
            "env": env, "timeout": 0}


def default_config_file(host: str) -> Path:
    home = Path.home()
    if host == "codex":
        return Path(os.environ.get("CODEX_HOME", home / ".codex")) / "config.toml"
    if host == "opencode":
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "opencode"
        jsonc = base / "opencode.jsonc"
        return jsonc if jsonc.exists() else base / "opencode.json"
    if host == "omp":
        return home / ".omp" / "agent" / "mcp.json"
    raise ValueError(host)


def _backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)
    return backup


_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def merge_codex(text: str, block: str) -> str:
    """Replace our [mcp_servers.mosaic*] tables, keep everything else."""
    import tomllib

    tomllib.loads(text)                      # refuse to edit a broken file
    ours = {f"mcp_servers.{SERVER_NAME}", f'mcp_servers."{SERVER_NAME}"'}
    kept, skipping = [], False
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            name = header.group(1).strip()
            skipping = any(name == own or name.startswith(own + ".")
                           for own in ours)
        if not skipping:
            kept.append(line)
    merged = "\n".join(kept).rstrip("\n")
    merged = (merged + "\n\n" if merged else "") + block
    tomllib.loads(merged)                    # and never write a broken one
    return merged


def merge_json(text: str, keys: tuple, entry: Dict[str, Any]) -> str:
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("top level is not an object")
    node = data
    for key in keys[:-1]:
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            raise ValueError(f"'{key}' is not an object")
        node = child
    node[keys[-1]] = entry
    return json.dumps(data, indent=2) + "\n"


def install(host: str, *, write: bool = False,
            config_file: Optional[str] = None,
            scopes: Optional[str] = None) -> SkillResult:
    skill = "install"
    try:
        env = server_env(scopes)
    except ValueError as error:
        return SkillResult(ok=False, skill=skill, summary=str(error),
                           errors=[str(error)])
    python = server_python()

    if host == "claude":
        commands = [
            f"claude plugin marketplace add {REPO_ROOT}",
            "claude plugin install mosaic@mosaic-soc",
        ]
        details = {"host": host, "commands": commands,
                   "markdown": "\n".join(["Run:", *commands, "",
                                          "The plugin launches the server "
                                          "from the checkout you open Claude "
                                          "Code in (or MOSAIC_REPO)."])}
        if write:
            return SkillResult(
                ok=False, skill=skill,
                summary="claude is installed as a plugin, not by editing its config",
                details=details, errors=["run the printed commands"])
        return SkillResult(ok=True, skill=skill,
                           summary="Claude Code: install the plugin",
                           details=details)

    if host == "codex":
        snippet = codex_block(env, python)
    elif host == "opencode":
        snippet = json.dumps({"mcp": {"servers": {
            SERVER_NAME: opencode_entry(env, python)}}}, indent=2) + "\n"
    elif host == "omp":
        snippet = json.dumps({"mcpServers": {
            SERVER_NAME: omp_entry(env, python)}}, indent=2) + "\n"
    else:
        return SkillResult(ok=False, skill=skill,
                           summary=f"unknown host {host!r}",
                           errors=["hosts: claude, codex, opencode, omp"])

    target = Path(config_file).expanduser() if config_file else default_config_file(host)
    details: Dict[str, Any] = {"host": host, "config_file": str(target),
                               "snippet": snippet,
                               "markdown": f"Add to {target}:\n\n{snippet}"}
    if not write:
        return SkillResult(ok=True, skill=skill,
                           summary=f"{host}: config for {target}",
                           details=details)

    existing = target.read_text() if target.exists() else ""
    try:
        if host == "codex":
            merged = merge_codex(existing, snippet)
        elif host == "opencode":
            merged = merge_json(existing, ("mcp", "servers", SERVER_NAME),
                                opencode_entry(env, python))
        else:
            merged = merge_json(existing, ("mcpServers", SERVER_NAME),
                                omp_entry(env, python))
    except Exception as error:               # noqa: BLE001 - reported
        return SkillResult(
            ok=False, skill=skill,
            summary=f"not writing {target}: its current contents do not parse",
            details=details,
            errors=[str(error), "merge the printed snippet by hand"])
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(target)
    target.write_text(merged)
    details["backup"] = str(backup) if backup else None
    return SkillResult(ok=True, skill=skill,
                       summary=f"{host}: wrote {target}"
                               + (f" (backup {backup.name})" if backup else ""),
                       details=details)
