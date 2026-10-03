"""WP-1/WP-7: the external drivers are gated, or they do not launch.

The defect these close: `--driver claude` was a `subprocess.call` with a
prompt. Scope ceiling, evidence binding and completion gate all existed in
`AgentRunner` and applied to nothing the external driver did.

The load-bearing test is `test_the_wp1_acceptance_scenario`, which is the
acceptance criterion from the capability survey verbatim: a session whose
ceiling was derived as `simulation`, an external MCP client calling
`flow_run{flow: "harden-classic"}`, and the *same* refusal the built-in loop
produces in-process.
"""

import io
import json
import subprocess
import sys

from harness.agent import AgentRunner, classify_request_scope
from harness.agent_tools import TOOL_SPECS, AgentToolRegistry
from harness.core import REPO_ROOT
from harness.events import EventStream
from harness.mcp_server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    MCPServer,
    build_session,
)

SIMULATION_REQUEST = "simulate a two-core SoC and run the wake demo"


def server_for(request: str) -> MCPServer:
    import io

    return MCPServer(build_session(request),
                     stdin=io.StringIO(), stdout=io.StringIO())


def call(server: MCPServer, name: str, arguments: dict) -> dict:
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    payload["_isError"] = response["result"]["isError"]
    return payload


# ── WP-1: the acceptance criterion, stated verbatim ──────────────────

def test_the_wp1_acceptance_scenario():
    """Survey WP-1: same refusal, in-process or over MCP.

    Not "an equivalent refusal" — the identical summary and errors, because
    the gates are one implementation shared by both paths.
    """
    server = server_for(SIMULATION_REQUEST)
    assert server.session.authorized_scope == "simulation"

    call(server, "request_scope",
         {"scope": "simulation", "rationale": "user asked for a sim"})
    over_mcp = call(server, "flow_run",
                    {"flow": "harden-classic",
                     "config": "configs/mosaic_tapeout_ultra.yaml"})

    assert over_mcp["_isError"]
    assert over_mcp["summary"] == (
        "flow 'harden-classic' is not authorized by 'simulation' request scope")
    assert over_mcp["errors"] == [
        "the model cannot widen the user-derived authorization ceiling"]

    # And the in-process path, from the same gates.
    runner = AgentRunner(AgentToolRegistry(repo_root=REPO_ROOT), EventStream(None))
    runner.state.required_scope = "simulation"
    runner.state.scope = "simulation"
    runner._scope_required = True
    in_process = runner._gate_precondition(
        "flow_run", {"flow": "harden-classic",
                     "config": "configs/mosaic_tapeout_ultra.yaml"})
    assert in_process is not None
    assert in_process.summary == over_mcp["summary"]
    assert in_process.errors == over_mcp["errors"]


def test_the_ceiling_cannot_be_widened_by_the_client():
    server = server_for(SIMULATION_REQUEST)
    widened = call(server, "request_scope",
                   {"scope": "physical", "rationale": "i would like to"})
    assert widened["_isError"]
    assert "locked to simulation" in widened["summary"]


def test_a_client_that_never_sets_scope_is_refused():
    """Skipping the ceiling must not be a way around it."""
    server = server_for(SIMULATION_REQUEST)
    blocked = call(server, "flow_run",
                   {"flow": "tb-soc-wake", "config": "configs/x.yaml"})
    assert blocked["_isError"]
    assert "request_scope" in blocked["summary"]


def test_evidence_binding_survives_across_calls():
    """One AgentState for the session, or the gates mean nothing.

    `mosaic-gen-config` requires current `topology_check` evidence. If each
    call got a fresh state, this would pass and the binding would be fiction.
    """
    server = server_for("generate RTL for a two-core SoC")
    # The ceiling is derived, not assumed: this request classifies as
    # `simulation`, and request_scope must be called with exactly that.
    scope = server.session.authorized_scope
    accepted = call(server, "request_scope",
                    {"scope": scope, "rationale": "user asked for RTL"})
    assert not accepted["_isError"]

    blocked = call(server, "flow_run",
                   {"flow": "mosaic-gen-config",
                    "config": "configs/mosaic_blockb_3hart.yaml"})
    assert blocked["_isError"]
    assert "topology_check" in " ".join(blocked["errors"] + [blocked["summary"]])


def test_unknown_tools_are_refused_not_executed():
    server = server_for(SIMULATION_REQUEST)
    call(server, "request_scope",
         {"scope": "simulation", "rationale": "sim"})
    unknown = call(server, "no_such_tool", {})
    assert unknown["_isError"]


# ── the protocol itself ──────────────────────────────────────────────

def test_initialize_advertises_the_locked_ceiling():
    server = server_for(SIMULATION_REQUEST)
    result = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == SERVER_NAME
    # The client should be able to tell the model the ceiling up front rather
    # than burning a turn discovering it.
    assert "simulation" in result["instructions"]


def test_notifications_get_no_reply():
    """Answering a notification is a protocol violation."""
    server = server_for(SIMULATION_REQUEST)
    assert server.handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_exposes_the_registry_and_nothing_else():
    server = server_for(SIMULATION_REQUEST)
    listed = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in listed} == {s.name for s in TOOL_SPECS}
    assert all(t["inputSchema"]["type"] == "object" for t in listed)
    # No shell, no file editor, in any spelling.
    assert not {"bash", "shell", "exec", "write_file", "edit"} & {
        t["name"] for t in listed}


def test_a_malformed_frame_does_not_kill_the_session():
    import io

    session = build_session(SIMULATION_REQUEST)
    out = io.StringIO()
    server = MCPServer(
        session,
        stdin=io.StringIO('not json\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'),
        stdout=out)
    assert server.serve_forever() == 0
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700     # parse error, reported
    assert replies[1]["id"] == 2                     # and still serving


def test_the_server_runs_as_a_real_subprocess():
    """Exercise the actual stdio path, not just handle() in-process."""
    frames = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "request_scope",
            "arguments": {"scope": "simulation", "rationale": "sim"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "flow_run",
            "arguments": {"flow": "harden-classic", "config": "configs/x.yaml"}}}),
    ]) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "harness", "mcp-server",
         "--request", SIMULATION_REQUEST],
        input=frames, capture_output=True, text=True, cwd=str(REPO_ROOT),
        timeout=120)
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 3
    # stdout is protocol only; the banner must go to stderr or it corrupts it.
    assert "scope ceiling" in proc.stderr
    refusal = json.loads(lines[2]["result"]["content"][0]["text"])
    assert lines[2]["result"]["isError"]
    assert "not authorized by 'simulation' request scope" in refusal["summary"]


# ── WP-7: no driver launches with the policy silently absent ─────────

def test_claude_launches_with_the_gates_attached():
    from harness.__main__ import _external_agent_command, write_mcp_config

    config = write_mcp_config(
        SIMULATION_REQUEST, REPO_ROOT,
        REPO_ROOT / "build" / "agent" / "mcp" / "test.json")
    command = _external_agent_command(
        "claude", "claude", "PROMPT", interactive=True, mcp_config=config)

    assert "--mcp-config" in command and str(config) in command
    # Otherwise a server the user happens to have installed is reachable from
    # a session whose policy says nothing about it.
    assert "--strict-mcp-config" in command
    assert all(f"mcp__{SERVER_NAME}__{spec.name}" in command
               for spec in TOOL_SPECS)


def test_the_bypass_is_closed():
    """Enforcement is theatre if the model keeps Bash.

    `python3 -m harness flow-runner run harden-classic` in a Bash call reaches
    the same flows with no AgentState in front of them.
    """
    from harness.__main__ import _external_agent_command, write_mcp_config

    config = write_mcp_config(
        SIMULATION_REQUEST, REPO_ROOT,
        REPO_ROOT / "build" / "agent" / "mcp" / "test.json")
    command = _external_agent_command(
        "claude", "claude", "PROMPT", interactive=True, mcp_config=config)
    disallowed = command[command.index("--disallowedTools") + 1:]
    for tool in ("Bash", "Write", "Edit"):
        assert tool in disallowed, f"{tool} would bypass the MCP gates"


def test_the_mcp_config_passes_the_request_as_argv_not_shell():
    """User text must never become syntax."""
    from harness.__main__ import write_mcp_config

    nasty = 'sim "; rm -rf /; echo "'
    config = write_mcp_config(
        nasty, REPO_ROOT, REPO_ROOT / "build" / "agent" / "mcp" / "test.json")
    spec = json.loads(config.read_text())["mcpServers"][SERVER_NAME]
    assert spec["args"][-1] == nasty          # one argv element, intact
    assert spec["args"][:4] == ["-m", "harness", "mcp-server", "--request"]


def _agent_args(argv):
    """Build the namespace through the real parser, not by hand.

    A hand-rolled SimpleNamespace drifts from the parser and then tests a
    surface that does not exist.
    """
    import argparse
    import contextlib
    import io
    from unittest import mock

    import harness.__main__ as entry

    captured = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        namespace = real(self, args, namespace)
        captured["ns"] = namespace
        return namespace

    with mock.patch.object(argparse.ArgumentParser, "parse_args", spy), \
            mock.patch.object(sys, "argv", ["oh-my-soc"] + argv), \
            mock.patch.object(entry, "cmd_agent", lambda _ns: None), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            entry.main()
        except SystemExit:
            pass
    return captured["ns"]


class _TTYCapture(io.StringIO):
    """Captures stdout while still claiming to be a terminal.

    `redirect_stdout` REPLACES sys.stdout, so patching the old object's
    `isatty` is pointless -- the code then asks the StringIO, which says
    False, and the external drivers refuse on the TTY check before reaching
    the branch under test.
    """

    def isatty(self) -> bool:
        return True


def _launch(argv, binary, *, with_env=False):
    """Run cmd_agent with a faked TTY; report whether it actually launched."""
    import contextlib
    from unittest import mock

    import harness.__main__ as entry

    args = _agent_args(argv)
    out = _TTYCapture()
    with mock.patch.object(sys.stdin, "isatty", return_value=True), \
            mock.patch("shutil.which", return_value=binary), \
            mock.patch("subprocess.call", return_value=0) as call, \
            contextlib.redirect_stdout(out):
        try:
            entry.cmd_agent(args)
        except SystemExit:
            pass
    command = list(call.call_args[0][0]) if call.called else []
    if with_env:
        env = call.call_args[1].get("env") if call.called else None
        return call.called, command, out.getvalue(), env
    return call.called, command, out.getvalue()


def test_omp_launches_with_the_gates_attached():
    """omp speaks stdio MCP (.omp/mcp.json), so it is gated, not refused.

    The request travels as OH_MY_SOC_REQUEST, which .omp/mcp.json expands
    into the server's --request argv: the same locked ceiling as claude.
    """
    launched, command, printed, env = _launch(
        ["agent", "--driver", "omp", "harden block b to GDS"],
        "/usr/bin/omp", with_env=True)
    assert launched and command[0] == "/usr/bin/omp"
    # Built-in tools (bash, edit, write...) are the bypass; MCP stays.
    assert "--no-tools" in command
    assert env["OH_MY_SOC_REQUEST"] == "harden block b to GDS"
    assert "scope ceiling 'physical'" in printed


def test_the_unenforced_escape_hatch_is_gone():
    """Every external driver is enforced now; there is nothing to opt out of."""
    import contextlib

    with contextlib.redirect_stderr(io.StringIO()):
        try:
            _agent_args(["agent", "--driver", "omp", "--unenforced", "x"])
        except KeyError:
            return          # the parser rejected it before cmd_agent ran
    raise AssertionError("--unenforced was accepted")


def test_claude_launches_enforced_through_the_real_cli_path():
    launched, command, printed = _launch(
        ["agent", "--driver", "claude", "harden block b to GDS"],
        "/usr/bin/claude")
    assert launched
    assert "--mcp-config" in command and "--strict-mcp-config" in command
    # The ceiling is stated to the operator before the handoff, not buried.
    assert "scope ceiling 'physical'" in printed


def test_omp_reads_the_committed_project_mcp_config():
    """omp discovers .omp/mcp.json from its cwd; the driver runs it there."""
    spec = json.loads((REPO_ROOT / ".omp" / "mcp.json").read_text())
    server = spec["mcpServers"][SERVER_NAME]
    assert server["args"][:2] == ["plugins/oh-my-soc/launch_mcp.py", "--request"]
    # An argv element, never env: omp runs env values starting with "!" as
    # shell commands, and user text must never become syntax.
    assert server["args"][2] == "${OH_MY_SOC_REQUEST:-}"
    assert "env" not in server
    # Hardening runs are hours long; the default 30 s client timeout is not.
    assert server["timeout"] == 0


def test_the_prompt_tells_claude_to_use_the_gated_tools():
    """It used to tell the model to shell out to `python3 -m harness`."""
    from harness.__main__ import external_agent_prompt

    prompt = external_agent_prompt("claude", SIMULATION_REQUEST)
    assert "MCP" in prompt and "request_scope" in prompt
    assert "Bash" not in prompt


def test_build_session_derives_the_same_ceiling_as_the_builtin_loop():
    for request in (
        SIMULATION_REQUEST,
        "harden block b to GDS",
        "just tell me what cores are available",
        "write a config for a three-core SoC",
    ):
        assert (build_session(request).authorized_scope
                == classify_request_scope(request))


def test_physical_capability_needs_a_person_not_just_a_request(tmp_path):
    """A physical REQUEST no longer carries physical CAPABILITY by itself.

    It used to: `allow_physical = (scope == "physical")`. The ceiling says
    what the user asked for; running hours of hardening also needs a
    person's time-limited yes, which only `oh-my-soc approve` writes.
    """
    from harness.approvals import Approvals, write_token

    assert build_session(SIMULATION_REQUEST).registry.allow_physical is False
    assert build_session("harden block b to GDS").registry.allow_physical is False
    write_token("physical", 5, tmp_path)
    approved = build_session("harden block b to GDS",
                             approvals=Approvals(token_dir=tmp_path))
    assert approved.registry.allow_physical is True
