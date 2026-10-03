"""naja-scope in the enforced session: design FACTS, never evidence.

Phase 4 made `--driver claude` run against a gated MCP server with
`--strict-mcp-config`, so nothing else is reachable. Admitting a second server
widens that deliberately, and the boundary has to stay visible:

  * our server produces EVIDENCE -- scope-ceilinged, digest-bound, gated
  * naja-scope produces FACTS -- what drives a net, hierarchy, logic cones

An agent that knows the connectivity has not proven anything.
"""

import json
import subprocess

import pytest

from harness.__main__ import (
    NAJA_SCOPE_EXCLUDED,
    NAJA_SCOPE_SERVER,
    NAJA_SCOPE_TOOLS,
    _mcp_tool_allowlist,
    naja_scope_command,
    write_mcp_config,
)
from harness.core import REPO_ROOT


def installed() -> bool:
    return naja_scope_command() is not None


# ── the writing tool stays out ───────────────────────────────────────

def test_save_snapshot_is_never_allowed():
    """It writes to disk. The enforced session may not write outside the
    gated tools, and that is the whole point of --allowedTools."""
    assert "save_snapshot" in NAJA_SCOPE_EXCLUDED
    assert "save_snapshot" not in NAJA_SCOPE_TOOLS
    assert not any("save_snapshot" in t for t in _mcp_tool_allowlist())


def test_the_allowlist_is_enumerated_never_wildcarded():
    """A wildcard would silently admit any tool a future release adds,
    including a writing one."""
    for entry in _mcp_tool_allowlist():
        assert "*" not in entry, entry


def test_the_loaders_are_allowed_because_they_only_touch_server_memory():
    """You cannot query a design you have not loaded."""
    for name in ("load_systemverilog", "load_verilog", "load_liberty"):
        assert name in NAJA_SCOPE_TOOLS


# ── our own gates are unaffected ─────────────────────────────────────

def test_admitting_a_second_server_does_not_widen_our_own_surface():
    from harness.agent_tools import TOOL_SPECS
    from harness.mcp_server import SERVER_NAME

    allowed = _mcp_tool_allowlist()
    ours = [t for t in allowed if t.startswith(f"mcp__{SERVER_NAME}__")]
    assert len(ours) == len(TOOL_SPECS)


def test_the_write_and_shell_bypasses_stay_closed():
    from harness.__main__ import _external_agent_command

    config = write_mcp_config(
        "trace what drives spi_flash_cs_o", REPO_ROOT,
        REPO_ROOT / "build" / "agent" / "mcp" / "test_naja.json")
    command = _external_agent_command(
        "claude", "claude", "PROMPT", interactive=True, mcp_config=config)
    disallowed = command[command.index("--disallowedTools") + 1:]
    for tool in ("Bash", "Write", "Edit"):
        assert tool in disallowed
    assert "--strict-mcp-config" in command


def test_the_config_lists_both_servers_when_naja_scope_is_present():
    config = write_mcp_config(
        "x", REPO_ROOT, REPO_ROOT / "build" / "agent" / "mcp" / "test_naja.json")
    servers = json.loads(config.read_text())["mcpServers"]
    assert "oh-my-soc" in servers
    if installed():
        assert NAJA_SCOPE_SERVER in servers
    else:
        assert NAJA_SCOPE_SERVER not in servers, (
            "must not reference a server that is not installed")


# ── the declared list must match the real server ─────────────────────

def test_the_declared_tools_match_what_the_server_actually_exposes():
    """Hardcoding names invites drift; this probes the real server.

    If naja-scope adds a tool, this fails and someone decides whether it
    belongs in the session -- rather than it being admitted by a wildcard or
    silently missing from the allowlist.
    """
    command = naja_scope_command()
    if command is None:
        pytest.skip("naja-scope not installed (optional)")

    frames = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {},
                               "clientInfo": {"name": "t", "version": "1"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]) + "\n"
    proc = subprocess.run([command], input=frames, capture_output=True,
                          text=True, timeout=180)
    names = set()
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        message = json.loads(line)
        for tool in (message.get("result") or {}).get("tools", []):
            names.add(tool["name"])
    assert names, "naja-scope exposed no tools"

    declared = set(NAJA_SCOPE_TOOLS) | set(NAJA_SCOPE_EXCLUDED)
    assert names == declared, {
        "new in the server": sorted(names - declared),
        "declared but absent": sorted(declared - names),
    }
