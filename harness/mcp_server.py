"""A session-scoped MCP stdio server over the typed harness registry.

WHAT THIS FIXES
---------------
`mosaic agent --driver claude` used to be a `subprocess.call`: the external
agent received a prompt and then acted with no scope ceiling, no evidence
binding and no completion gate. Every one of those rules existed in
`AgentRunner` and applied to nothing the external drivers did. A prompt asking
for a simulation could end with a physical-design flow, and nothing in the
harness would have objected.

This server puts the same gates in front of an external client. It speaks MCP
over stdio, so Claude Code, Codex, opencode and oh-my-pi all reach it from one
artifact, and it holds one `AgentState` per request: evidence recorded by one
call is what a later call is gated against.

TWO WAYS TO SET THE CEILING
---------------------------
LOCKED. `--request` (or MOSAIC_REQUEST) is the user's actual request. The
ceiling is derived from it with the same `classify_request_scope` the built-in
loop uses and locked before the client connects. `mosaic agent --driver
claude|omp` launches it this way.

PLUGIN. A host that loads us as a plugin starts the server before anyone has
typed anything, so there is no request to derive from. The ceiling is then a
standing allowlist the user writes into the host config (MOSAIC_SCOPES,
default: everything except integration and physical). The model binds each
request with `session_new` and picks a scope inside the allowlist. Because the
request text is model-supplied in this mode, the classifier is only a hint.

In both modes, selecting `physical` or `integration`, running an
approval-declared flow, and applying a wrapper need a person's approval token
(`mosaic approve <scope>`, TTY only). See harness/approvals.py.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No shell, no file editor, no "allow all tools" escape. The registry executes
registered harness operations only. Upstream projects reviewed for this work
ship `--dangerously-skip-permissions`, `--yolo` and `danger-full-access`; the
survey concluded WP-1 must not weaken our position for convenience, and it
does not.

PROTOCOL
--------
JSON-RPC 2.0, one message per line, on stdin/stdout — hand-rolled rather than
taking an `mcp` dependency for a handful of methods. stdout carries protocol
ONLY; anything human-readable goes to stderr, because a stray print corrupts
the stream.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional, TextIO

from .agent_tools import SESSION_TOOLS, TOOL_SPECS, AgentToolRegistry
from .core import REPO_ROOT, SkillResult

#: Newest first. The client's version is echoed when we speak it; otherwise we
#: answer with ours and the client decides (MCP lifecycle negotiation).
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_NAME = "mosaic"


def default_scope_allowlist() -> FrozenSet[str]:
    """Every scope except the two that change the tree or burn hours."""
    from .agent import REQUEST_SCOPES

    return frozenset(REQUEST_SCOPES - {"integration", "physical"})


def parse_scope_allowlist(text: Optional[str]) -> FrozenSet[str]:
    """MOSAIC_SCOPES: a comma list; unset or blank means the default.

    An unknown name is an error, not an ignored typo: a typo in an
    authorization list silently changes what runs.
    """
    from .agent import REQUEST_SCOPES

    if text is None or not text.strip():
        return default_scope_allowlist()
    names = {part.strip() for part in text.split(",") if part.strip()}
    unknown = names - REQUEST_SCOPES
    if unknown:
        raise ValueError(
            f"MOSAIC_SCOPES has unknown scope(s) {sorted(unknown)}; "
            f"valid: {sorted(REQUEST_SCOPES)}")
    return frozenset(names)


@dataclass
class GatedSession:
    """One `AgentState` at a time, and the shared gates in front.

    The built-in loop and this server both run tools through the same gate
    functions, so a refusal here is the same refusal produced in-process — not
    a second implementation that agrees today.
    """

    registry: AgentToolRegistry
    authorized_scope: Optional[str]
    request: str = ""
    scope_allowlist: FrozenSet[str] = field(
        default_factory=default_scope_allowlist)
    scope_required: bool = True
    state: Any = field(default=None)

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = self._fresh_state()

    @property
    def locked(self) -> bool:
        return self.authorized_scope is not None

    def _fresh_state(self) -> Any:
        from .agent import AgentState

        repo_root = Path(
            getattr(self.registry, "repo_root", REPO_ROOT)).resolve()
        state = AgentState(repo_root=repo_root)
        state.scope_approval_required = True
        if self.locked:
            # Lock the ceiling before any client can speak. `scope_locked` is
            # what makes `request_scope` refuse a widening value.
            state.required_scope = self.authorized_scope
            state.scope_locked = True
            state.bind_request(self.request, self.authorized_scope)
        else:
            state.scope_allowlist = self.scope_allowlist
        return state

    def execute(self, name: str, arguments: Mapping[str, Any]) -> SkillResult:
        """Gate, execute, record. The order is the policy."""
        from .gates import gate_precondition

        if name in SESSION_TOOLS:
            return self._session_tool(name, arguments)
        precondition = gate_precondition(
            self.state, self.registry, self.scope_required, name, arguments)
        if precondition is not None:
            return precondition
        try:
            result = self.registry.execute(name, arguments)
        except Exception as error:                 # noqa: BLE001 - reported
            result = SkillResult(
                ok=False, skill=name,
                summary=f"tool '{name}' rejected the request: {error}",
                errors=[str(error)])
        # Recording happens for failures too: a refused call is evidence about
        # the session, and `observe` is what keeps digests current.
        self.state.observe(name, arguments, result)
        if name == "request_scope" and result.ok and not self.locked:
            # The first accepted scope fixes this request's ceiling; a wider
            # one needs a new session. Bindings follow the chosen scope.
            scope = str(arguments["scope"])
            self.state.required_scope = scope
            self.state.scope_locked = True
            self.state.bind_request(self.state.user_request, scope)
        return result

    def _session_tool(self, name: str,
                      arguments: Mapping[str, Any]) -> SkillResult:
        from .agent import classify_request_scope
        from .approvals import APPROVAL_SCOPES

        approvals = self.registry.approvals
        approved = sorted(s for s in APPROVAL_SCOPES if approvals.granted(s))
        if name == "session_status":
            error = self.completion_error()
            return SkillResult(
                ok=True, skill=name,
                summary="done" if error is None else f"not done: {error}",
                details={
                    "done": error is None,
                    "completion_error": error,
                    "request": self.state.user_request,
                    "scope": self.state.scope,
                    "required_scope": self.state.required_scope,
                    "locked": self.locked,
                    "approved": approved,
                })
        request = arguments.get("request")
        if not isinstance(request, str) or not request.strip():
            return SkillResult(ok=False, skill=name,
                               summary="session_new needs the user's request",
                               errors=["pass the request text verbatim"])
        if self.locked:
            return SkillResult(
                ok=False, skill=name,
                summary="this session is bound to the request it was launched with",
                errors=["restart the agent with the new request"])
        self.state = self._fresh_state()
        self.state.user_request = request
        self.state.request_bound = True
        suggested = classify_request_scope(request)
        return SkillResult(
            ok=True, skill=name,
            summary=f"new session · suggested scope '{suggested}'",
            details={
                "request": request,
                "suggested_scope": suggested,
                "allowed_scopes": sorted(self.scope_allowlist),
                "needs_approval": sorted(APPROVAL_SCOPES),
                "approved": approved,
                "next": "call request_scope with one allowed scope",
            })

    def completion_error(self) -> Optional[str]:
        return self.state.completion_error()

    def instructions(self) -> str:
        from .approvals import APPROVAL_SCOPES

        tail = (" Selecting physical or integration, running an approval-"
                "gated flow and applying a wrapper need a person to run "
                "`mosaic approve <scope>` in a terminal first; you cannot "
                "grant it. Call session_status before claiming success: "
                "success requires deterministic evidence.")
        if self.locked:
            return (
                "Every MOSAIC action goes through these tools. The authorized "
                f"outcome scope for this session is '{self.authorized_scope}', "
                "derived from the user's request and locked — call "
                "request_scope with exactly that value first." + tail)
        return (
            "Every MOSAIC action goes through these tools. For each new user "
            "request call session_new with the request verbatim, then "
            "request_scope with one of "
            f"{sorted(self.scope_allowlist)} (needs approval: "
            f"{sorted(APPROVAL_SCOPES & self.scope_allowlist) or 'none allowed'})."
            + tail)


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _result(request_id: Any, payload: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _tool_content(result: SkillResult) -> Dict[str, Any]:
    """An MCP tool result carrying the SkillResult verbatim.

    `isError` is set for a refusal so the client cannot read a gate failure as
    success, and the full JSON is included because the errors list is the part
    that tells the model what to do instead.
    """
    return {
        "content": [{"type": "text", "text": result.to_json()}],
        "isError": not result.ok,
    }


def negotiate_protocol(requested: Any) -> str:
    return (requested if requested in SUPPORTED_PROTOCOL_VERSIONS
            else PROTOCOL_VERSION)


class MCPServer:
    """The stdio loop."""

    def __init__(self, session: GatedSession, *,
                 stdin: Optional[TextIO] = None,
                 stdout: Optional[TextIO] = None):
        self.session = session
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout

    # ── protocol ─────────────────────────────────────────────────────
    def handle(self, message: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        # A notification has no id and takes no reply, ever. Answering one is
        # a protocol violation that some clients treat as fatal.
        is_notification = "id" not in message

        if method == "initialize":
            return _result(request_id, {
                "protocolVersion": negotiate_protocol(
                    params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.4.0"},
                # Not decoration: the client should say this to the model, so
                # it knows the ceiling before it wastes a turn on a refusal.
                "instructions": self.session.instructions(),
            })

        if is_notification:
            return None

        if method == "tools/list":
            return _result(request_id, {
                "tools": [
                    {"name": spec.name,
                     "description": spec.description,
                     "inputSchema": spec.parameters}
                    for spec in TOOL_SPECS
                ]
            })

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                return _error(request_id, -32602, "tools/call needs a name")
            result = self.session.execute(name, arguments)
            return _result(request_id, _tool_content(result))

        if method == "ping":
            return _result(request_id, {})

        return _error(request_id, -32601, f"unknown method {method!r}")

    def serve_forever(self) -> int:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                self._send(_error(None, -32700, f"parse error: {error}"))
                continue
            try:
                response = self.handle(message)
            except Exception as error:             # noqa: BLE001 - protocol
                # A crash must not take the session's evidence with it: report
                # and keep serving.
                self._send(_error(message.get("id"), -32603, str(error)))
                continue
            if response is not None:
                self._send(response)
        return 0

    def _send(self, payload: Mapping[str, Any]) -> None:
        self.stdout.write(json.dumps(payload) + "\n")
        self.stdout.flush()


def build_session(request: Optional[str] = None, *,
                  repo_root: Path = REPO_ROOT,
                  required_evidence: str = "auto",
                  scope_allowlist: Optional[FrozenSet[str]] = None,
                  approvals: Any = None,
                  allow_write: bool = True,
                  allow_execute: bool = True) -> GatedSession:
    """Locked when a request is given, plugin mode otherwise.

    `approvals` defaults to static-none with no token directory, so a session
    built in a test is hermetic; the CLI passes the user's token directory.
    """
    from .agent import classify_request_scope

    registry = AgentToolRegistry(
        repo_root=repo_root,
        allow_write=allow_write,
        allow_execute=allow_execute,
        approvals=approvals,
    )
    if request:
        scope = (classify_request_scope(request)
                 if required_evidence == "auto" else required_evidence)
        return GatedSession(registry=registry, authorized_scope=scope,
                            request=request)
    if required_evidence != "auto":
        raise ValueError("--required-evidence needs --request")
    return GatedSession(
        registry=registry, authorized_scope=None,
        scope_allowlist=(scope_allowlist if scope_allowlist is not None
                         else default_scope_allowlist()))
