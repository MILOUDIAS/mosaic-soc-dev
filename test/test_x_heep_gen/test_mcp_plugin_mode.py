"""M4: the MCP server as an agent-host plugin.

A host (Claude Code plugin, Codex, opencode, omp) starts the server once,
before anyone has typed a request, so the old "derive the ceiling from
--request" rule had nothing to derive from: the server either refused to
start or sat at `analysis` for the whole session. And even with --request,
soc_generate / config_generate / tb_generate / tb_wake_demo were refused over
MCP no matter what, because the request binding lived only in AgentRunner.

What these tests pin, most of them over the REAL server process:
- the server starts without --request (plugin mode);
- the ceiling is the user's OH_MY_SOC_SCOPES allowlist, not the model;
- physical/integration, approval-declared flows and wrapper apply need a
  person's token that only a TTY `oh-my-soc approve` writes;
- session_new binds a request, and the plan → generate → validate chain now
  succeeds over MCP;
- the repo comes from OH_MY_SOC_REPO / the cwd, not the package location.
"""

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness.agent import REQUEST_SCOPES, AgentState
from harness.agent_tools import TOOL_SPECS, AgentToolRegistry
from harness.approvals import Approvals, token_status, write_token
from harness.core import REPO_ROOT
from harness.gates import scope_effect_precondition
from harness.mcp_server import (
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    MCPServer,
    build_session,
    default_scope_allowlist,
    parse_scope_allowlist,
)

HOOK = REPO_ROOT / "plugins" / "oh-my-soc" / "hooks" / "deny_bypass.py"


def _frame(i, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": i, "method": method,
                       "params": params or {}})


def _call(i, name, arguments):
    return _frame(i, "tools/call", {"name": name, "arguments": arguments})


def serve(frames, *, env=None, cwd=None, args=(), timeout=180):
    """Run the real server process over stdio; return (responses, proc)."""
    full_env = dict(os.environ)
    full_env.pop("OH_MY_SOC_REQUEST", None)
    full_env.pop("OH_MY_SOC_SCOPES", None)
    full_env.update(env or {})
    proc = subprocess.run(
        [sys.executable, "-m", "harness", "mcp-server", *args],
        input="\n".join(frames) + "\n", capture_output=True, text=True,
        cwd=str(cwd or REPO_ROOT), env=full_env, timeout=timeout)
    replies = {}
    for line in proc.stdout.splitlines():
        if line.strip():
            message = json.loads(line)
            replies[message.get("id")] = message
    return replies, proc


def payload(reply):
    result = reply["result"]
    body = json.loads(result["content"][0]["text"])
    body["_isError"] = result["isError"]
    return body


def config_env(tmp_path, **extra):
    env = {"OH_MY_SOC_CONFIG_DIR": str(tmp_path / "cfg"),
           "PYTHONPATH": str(REPO_ROOT)}
    env.update(extra)
    return env


# ── plugin mode over the real process ────────────────────────────────

def test_the_server_starts_without_a_request(tmp_path):
    replies, proc = serve([
        _frame(1, "initialize", {"protocolVersion": "2025-03-26"}),
        _frame(2, "tools/list"),
    ], env=config_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    init = replies[1]["result"]
    assert init["protocolVersion"] == "2025-03-26"        # echoed
    assert "session_new" in init["instructions"]
    assert "plugin mode" in proc.stderr
    names = {t["name"] for t in replies[2]["result"]["tools"]}
    assert {"session_new", "session_status"} <= names


def test_the_allowlist_is_the_ceiling_not_the_model(tmp_path):
    replies, _ = serve([
        _call(1, "request_scope", {"scope": "config", "rationale": "x"}),
        _call(2, "session_new", {"request": "write a config for a two-core SoC"}),
        _call(3, "request_scope", {"scope": "rtl", "rationale": "outside"}),
        _call(4, "request_scope", {"scope": "config", "rationale": "inside"}),
        # The first accepted scope fixes the ceiling for this request.
        _call(5, "request_scope", {"scope": "documentation", "rationale": "wider"}),
    ], env=config_env(tmp_path, OH_MY_SOC_SCOPES="analysis,config,documentation"))
    unbound = payload(replies[1])
    assert unbound["_isError"] and "session_new" in unbound["errors"][0]
    outside = payload(replies[3])
    assert outside["_isError"] and "allowlist" in outside["summary"]
    assert not payload(replies[4])["_isError"]
    assert "locked to config" in payload(replies[5])["summary"]


def test_physical_needs_a_token_even_when_allowlisted(tmp_path):
    frames = [
        _call(1, "session_new", {"request": "harden block b to GDS"}),
        _call(2, "request_scope", {"scope": "physical", "rationale": "harden"}),
    ]
    env = config_env(tmp_path, OH_MY_SOC_SCOPES="physical,analysis")
    replies, _ = serve(frames, env=env)
    refused = payload(replies[2])
    assert refused["_isError"]
    assert "approval" in refused["summary"]
    assert any("oh-my-soc approve physical" in e for e in refused["errors"])

    write_token("physical", 5, tmp_path / "cfg" / "approvals")
    replies, _ = serve(frames, env=env)
    assert not payload(replies[2])["_isError"]


def test_an_approval_flow_needs_the_scopes_token(tmp_path):
    """lec runs hours; its FlowSpec says approval=True, and that is enforced."""
    replies, _ = serve([
        _call(1, "session_new", {"request": "simulate a two-core SoC"}),
        _call(2, "request_scope", {"scope": "simulation", "rationale": "sim"}),
        _call(3, "flow_run", {"flow": "lec"}),
    ], env=config_env(tmp_path))
    lec = payload(replies[3])
    assert lec["_isError"]
    assert "needs a person's approval for the 'simulation' scope" in lec["summary"]


def test_a_bad_allowlist_stops_the_server(tmp_path):
    _, proc = serve([_frame(1, "ping")],
                    env=config_env(tmp_path, OH_MY_SOC_SCOPES="config,phsyical"))
    assert proc.returncode == 2
    assert "phsyical" in proc.stderr


def test_the_repo_comes_from_the_environment_not_the_cwd(tmp_path):
    """A host launches us from anywhere; flows must act on the checkout."""
    replies, proc = serve([
        _call(1, "session_new", {"request": "check configs/mosaic_blockb_3hart.yaml"}),
        _call(2, "request_scope", {"scope": "analysis", "rationale": "check"}),
        _call(3, "config_validate", {"path": "configs/mosaic_blockb_3hart.yaml"}),
    ], env=config_env(tmp_path, OH_MY_SOC_REPO=str(REPO_ROOT)), cwd=tmp_path)
    assert f"repo {REPO_ROOT}" in proc.stderr
    assert not payload(replies[3])["_isError"], payload(replies[3])


def test_plan_generate_validate_succeeds_over_mcp(tmp_path):
    """The chain that was dead over MCP: soc_generate needs the plan binding."""
    name = f"mcp_plugin_probe_{os.getpid()}"
    request = "an SoC with one cv32e20 controller and two serv workers"
    target = REPO_ROOT / "configs" / f"{name}.yaml"
    try:
        replies, _ = serve([
            _call(1, "session_new", {"request": request}),
            _call(2, "request_scope", {"scope": "config", "rationale": "config"}),
            _call(3, "soc_plan", {"request": request}),
            _call(4, "soc_generate", {"request": request, "name": name}),
            _call(5, "config_validate", {"path": f"configs/{name}.yaml"}),
            _call(6, "session_status", {}),
        ], env=config_env(tmp_path))
        for i in (1, 2, 3, 4, 5):
            assert not payload(replies[i])["_isError"], (i, payload(replies[i]))
        status = payload(replies[6])
        assert status["details"]["done"] is True, status
    finally:  # soc_generate also writes <config>.diagram.html
        target.unlink(missing_ok=True)
        target.with_suffix(".diagram.html").unlink(missing_ok=True)


def test_a_locked_session_cannot_be_rebound(tmp_path):
    replies, _ = serve([
        _call(1, "session_new", {"request": "harden block b to GDS"}),
    ], env=config_env(tmp_path), args=("--request", "simulate a two-core SoC"))
    assert payload(replies[1])["_isError"]


# ── approvals ────────────────────────────────────────────────────────

def test_approve_refuses_without_a_terminal(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "harness", "approve", "physical"],
        input="approve physical for 60 minutes\n", capture_output=True,
        text=True, cwd=str(REPO_ROOT), env=config_env(tmp_path), timeout=60)
    assert proc.returncode == 2
    assert "interactive terminal" in proc.stderr
    assert not (tmp_path / "cfg" / "approvals" / "physical.json").exists()


def test_a_token_is_private_and_expires(tmp_path):
    path = write_token("integration", 5, tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token_status("integration", tmp_path) is None
    assert "another scope" not in (token_status("physical", tmp_path) or "")

    data = json.loads(path.read_text())
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    data["expires_at"] = past.isoformat()
    path.write_text(json.dumps(data))
    assert "expired" in token_status("integration", tmp_path)

    write_token("integration", 5, tmp_path)
    path.chmod(0o644)
    assert "unsafe" in token_status("integration", tmp_path)


def test_a_renamed_token_does_not_count(tmp_path):
    write_token("analysis", 5, tmp_path).rename(tmp_path / "physical.json")
    assert "another scope" in token_status("physical", tmp_path)


def test_wrapper_apply_needs_the_integration_token(tmp_path):
    registry = AgentToolRegistry(approvals=Approvals(token_dir=tmp_path))
    refused = registry.execute("wrapper_scaffold", {
        "core": "x", "analysis": "a.json", "apply": True})
    assert not refused.ok and "integration approval" in refused.summary


# ── policy tables ────────────────────────────────────────────────────

def test_every_tool_is_permitted_under_at_least_one_scope():
    """A tool no scope permits is dead on arrival (tb_matrix_* were)."""
    registry = AgentToolRegistry()
    dead = []
    for spec in TOOL_SPECS:
        arguments = {"flow": "pytest"} if spec.name == "flow_run" else {}
        permitted = False
        for scope in REQUEST_SCOPES:
            state = AgentState(repo_root=REPO_ROOT)
            state.scope = scope
            refusal = scope_effect_precondition(
                state, registry, True, spec.name, arguments)
            if refusal is None:
                permitted = True
                break
        if not permitted:
            dead.append(spec.name)
    assert dead == []


def test_the_default_allowlist_withholds_the_expensive_scopes():
    assert default_scope_allowlist() == REQUEST_SCOPES - {"integration", "physical"}
    assert parse_scope_allowlist("") == default_scope_allowlist()
    assert parse_scope_allowlist(" config , rtl ") == {"config", "rtl"}
    with pytest.raises(ValueError):
        parse_scope_allowlist("config,phsyical")


def test_protocol_negotiation():
    server = MCPServer(build_session())
    for asked, answer in (("2024-11-05", "2024-11-05"),
                          ("1999-01-01", PROTOCOL_VERSION),
                          (None, PROTOCOL_VERSION)):
        params = {"protocolVersion": asked} if asked else {}
        reply = server.handle({"jsonrpc": "2.0", "id": 1,
                               "method": "initialize", "params": params})
        assert reply["result"]["protocolVersion"] == answer
    assert PROTOCOL_VERSION == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_session_tools_stay_out_of_the_in_process_loop():
    """The in-process loop is bound to one request and cannot rebind it."""
    registry = AgentToolRegistry()
    assert {"session_new", "session_status"}.isdisjoint(
        s["name"] for s in registry.schemas())
    assert not registry.execute("session_new", {"request": "x"}).ok


# ── repo resolution ──────────────────────────────────────────────────

def test_a_wrong_repo_pin_is_loud(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-c", "import harness.core"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60,
        env=dict(os.environ, OH_MY_SOC_REPO=str(tmp_path)))
    assert proc.returncode != 0
    assert "is not a MOSAIC checkout" in proc.stderr


def test_the_repo_is_found_by_walking_up_from_the_cwd(tmp_path):
    inside = REPO_ROOT / "configs"
    env = {k: v for k, v in os.environ.items() if k != "OH_MY_SOC_REPO"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", "from harness.core import REPO_ROOT; print(REPO_ROOT)"],
        capture_output=True, text=True, cwd=str(inside), env=env, timeout=60)
    assert proc.stdout.strip() == str(REPO_ROOT)


# ── install ──────────────────────────────────────────────────────────

def _install(tmp_path, *argv):
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(REPO_ROOT),
               OH_MY_SOC_REPO=str(REPO_ROOT))
    env.pop("CODEX_HOME", None)
    env.pop("XDG_CONFIG_HOME", None)
    return subprocess.run(
        [sys.executable, "-m", "harness", "--json", "install", *argv],
        capture_output=True, text=True, cwd=str(tmp_path), env=env, timeout=60)


def test_install_codex_merges_and_backs_up(tmp_path):
    import tomllib

    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('model = "o3"\n\n[mcp_servers.oh-my-soc]\ncommand = "old"\n'
                      '\n[mcp_servers.oh-my-soc.env]\nX = "1"\n\n'
                      '[mcp_servers.other]\ncommand = "keep"\n')
    proc = _install(tmp_path, "--host", "codex", "--write")
    assert proc.returncode == 0, proc.stdout
    data = tomllib.loads(config.read_text())
    assert data["model"] == "o3"
    assert data["mcp_servers"]["other"]["command"] == "keep"
    ours = data["mcp_servers"]["oh-my-soc"]
    assert ours["args"] == ["-m", "harness", "mcp-server"]
    assert ours["env"]["OH_MY_SOC_REPO"] == str(REPO_ROOT)
    assert "X" not in ours["env"]                    # replaced, not merged
    assert list(config.parent.glob("config.toml.bak.*"))
    # Idempotent: a second write leaves exactly one table.
    _install(tmp_path, "--host", "codex", "--write")
    assert config.read_text().count("[mcp_servers.oh-my-soc]") == 1


def test_install_opencode_and_omp(tmp_path):
    proc = _install(tmp_path, "--host", "opencode", "--write")
    assert proc.returncode == 0, proc.stdout
    oc = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text())
    entry = oc["mcp"]["servers"]["oh-my-soc"]            # opencode v2 shape
    assert entry["type"] == "local" and entry["command"][1:] == ["-m", "harness", "mcp-server"]

    proc = _install(tmp_path, "--host", "omp", "--write")
    assert proc.returncode == 0, proc.stdout
    omp = json.loads((tmp_path / ".omp" / "agent" / "mcp.json").read_text())
    assert omp["mcpServers"]["oh-my-soc"]["timeout"] == 0


def test_install_refuses_a_file_it_cannot_parse(tmp_path):
    target = tmp_path / ".config" / "opencode" / "opencode.jsonc"
    target.parent.mkdir(parents=True)
    original = '{\n  // comments make this JSONC\n  "theme": "x"\n}\n'
    target.write_text(original)
    proc = _install(tmp_path, "--host", "opencode", "--write")
    assert proc.returncode != 0
    assert target.read_text() == original


def test_install_claude_is_the_plugin(tmp_path):
    proc = _install(tmp_path, "--host", "claude")
    assert proc.returncode == 0
    assert "claude plugin install oh-my-soc@mosaic-soc" in proc.stdout
    assert _install(tmp_path, "--host", "claude", "--write").returncode != 0


# ── the Claude Code hook ─────────────────────────────────────────────

@pytest.mark.parametrize("tool,tool_input,denied", [
    ("Bash", {"command": "python3 -m harness flow-runner run harden-classic"}, True),
    ("Bash", {"command": "./oh-my-soc approve physical"}, True),
    ("Bash", {"command": "cd x && oh-my-soc agent 'harden'"}, True),
    ("Bash", {"command": "echo {} > ~/.config/oh-my-soc/approvals/physical.json"}, True),
    ("Write", {"file_path": "~/.config/oh-my-soc/approvals/physical.json"}, True),
    ("Bash", {"command": "git status && ls configs"}, False),
    ("Bash", {"command": "cat docs/FEATURES.md"}, False),
    ("Edit", {"file_path": "harness/gates.py"}, False),
])
def test_the_hook_denies_the_bypasses(tmp_path, tool, tool_input, denied):
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
        capture_output=True, text=True, timeout=30,
        env={k: v for k, v in os.environ.items() if k != "OH_MY_SOC_CONFIG_DIR"})
    assert (proc.returncode == 2) is denied, proc.stderr


def test_the_plugin_hooks_the_bypass_tools():
    hooks = json.loads((HOOK.parent / "hooks.json").read_text())
    matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
    for tool in ("Bash", "Write", "Edit"):
        assert tool in matcher.split("|")


@pytest.mark.slow
def test_the_mcp_chain_reaches_a_passing_simulation(tmp_path):
    """plan → generate → check → mosaic-gen → tb-soc-generic, over MCP only.

    Needs the RTL toolchain (FuseSoC, Verilator 5.050, RISC-V GCC); minutes.
    """
    name = f"mcp_plugin_sim_{os.getpid()}"
    request = "an SoC with one cv32e20 controller and two serv workers"
    config = f"configs/{name}.yaml"
    try:
        replies, proc = serve([
            _call(1, "session_new", {"request": request}),
            _call(2, "request_scope", {"scope": "simulation", "rationale": "sim"}),
            _call(3, "soc_plan", {"request": request}),
            _call(4, "soc_generate", {"request": request, "name": name}),
            _call(5, "topology_check", {"path": config}),
            _call(6, "flow_run", {"flow": "mosaic-gen-config", "config": config}),
            _call(7, "flow_run", {"flow": "tb-soc-generic", "config": config}),
            _call(8, "session_status", {}),
        ], env=config_env(tmp_path), timeout=3600)
        for i in range(1, 8):
            assert not payload(replies[i])["_isError"], (i, payload(replies[i]))
        assert payload(replies[8])["details"]["done"] is True
    finally:  # soc_generate also writes <config>.diagram.html
        (REPO_ROOT / config).unlink(missing_ok=True)
        (REPO_ROOT / config).with_suffix(".diagram.html").unlink(missing_ok=True)
