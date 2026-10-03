"""The authorization gates, shared by every driver that can act.

WHY THIS IS ITS OWN MODULE
--------------------------
These checks used to be methods on `AgentRunner`, which meant they applied to
the built-in loop and to nothing else. The `claude` and `omp` drivers were a
`subprocess.call` handoff: the model got a prompt and ran with no scope
ceiling, no evidence binding and no completion gate. The policy existed and
simply was not on that path.

An MCP server has to enforce exactly the same rules, and "exactly the same"
cannot survive two copies. So the gates live here as free functions over
`(state, registry, scope_required)`, and `AgentRunner` and the MCP session both
call them. Neither owns them.

WHAT THEY ENFORCE
-----------------
1. **The ceiling is derived from the user's request, not chosen by the model.**
   `request_scope` is locked once authorized; a model that asks to widen it is
   refused.
2. **Evidence binding.** `mosaic-gen-config` needs a current `topology_check`;
   the tb-* flows need a current generation. "Current" means the digest still
   matches, so a stale pass does not count.
3. **Fail-closed.** A tool with no entry in the scope table is refused rather
   than allowed -- adding a tool without deciding its authorization is a
   mistake that should stop the session, not widen it.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .agent_tools import AgentToolRegistry
from .core import SkillResult


def _approval_refusal(registry: Any, scope: Optional[str], *,
                      scopes_needing: bool = False) -> Optional[list]:
    """Errors when `scope` lacks a person's approval, else None.

    With scopes_needing, only the scopes that need approval to be SELECTED
    (physical, integration) are checked; otherwise any scope is.
    """
    from .approvals import APPROVAL_SCOPES, how_to_approve

    if scope is None or (scopes_needing and scope not in APPROVAL_SCOPES):
        return None
    approvals = getattr(registry, "approvals", None)
    reason = (approvals.refusal(scope) if approvals is not None
              else "the registry carries no approvals")
    if reason is None:
        return None
    return [reason, how_to_approve(scope)]


def gate_precondition(
    state: Any,
    registry: Any,
    scope_required: bool,
    name: str,
    arguments: Mapping[str, Any],
) -> Optional[SkillResult]:
    if name == "request_scope":
        requested = str(arguments.get("scope", ""))
        authorized = state.required_scope
        if state.scope_locked and requested != authorized:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"request scope is locked to {authorized}",
                errors=["the user-derived authorization ceiling cannot be changed by the model"],
            )
        allowlist = getattr(state, "scope_allowlist", None)
        if allowlist is not None and not state.scope_locked:
            # Plugin session: the model supplied the request, so the ceiling
            # is what the USER wrote into the host config, not a classifier.
            if not state.request_bound:
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary="no request is bound to this session",
                    errors=["call session_new with the user's request first"],
                )
            if requested not in allowlist:
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary=f"scope '{requested}' is not in this installation's allowlist",
                    errors=[f"allowed: {sorted(allowlist)}",
                            "the user widens it in MOSAIC_SCOPES; the model cannot"],
                )
        if getattr(state, "scope_approval_required", False):
            refusal = _approval_refusal(registry, requested, scopes_needing=True)
            if refusal is not None:
                return SkillResult(ok=False, skill=name,
                                   summary=f"scope '{requested}' needs a person's approval",
                                   errors=refusal)
        return None
    if name != "flow_run":
        if scope_required and state.scope is None:
            return SkillResult(
                ok=False,
                skill=name,
                summary="tool blocked until request_scope classifies the requested outcome",
                errors=["call request_scope first"],
            )
        effect_error = scope_effect_precondition(state, registry, scope_required, name, arguments)
        if effect_error is not None:
            return effect_error
        from .agent_tools import tb_matrix_needs_approval

        if name == "tb_matrix_run" and tb_matrix_needs_approval(arguments):
            refusal = _approval_refusal(registry, state.scope)
            if refusal is not None:
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary=f"tb-matrix above validate needs a person's approval for the '{state.scope}' scope",
                    errors=refusal,
                )
        if name in {"soc_generate", "config_generate"} and not state.config_writes_allowed:
            return SkillResult(
                ok=False,
                skill=name,
                summary="existing-config verification does not authorize config regeneration",
                errors=["explicitly request create/update/regenerate to permit config writes"],
            )
        if name == "soc_generate" and (
            str(arguments.get("request", "")) != state.user_request
            or not state.planned_request_ok
        ):
            return SkillResult(
                ok=False,
                skill=name,
                summary="soc_generate requires a successful plan for the exact user request",
                errors=["run soc_plan with the unchanged user request first"],
            )
        if (
            name == "config_generate"
            and not state.requested_configs
            and not state.planned_request_ok
        ):
            return SkillResult(
                ok=False,
                skill=name,
                summary="structured config generation requires the exact user plan",
                errors=["run soc_plan before correcting its topology"],
            )
        if name == "tb_generate" and not state.testbench_writes_allowed:
            return SkillResult(
                ok=False,
                skill=name,
                summary="running existing tests does not authorize testbench regeneration",
                errors=["explicitly request testbench generation to permit source writes"],
            )
        if name == "tb_wake_demo" and not state.wake_demo_allowed:
            return SkillResult(
                ok=False,
                skill=name,
                summary="existing-config verification does not authorize wake-demo config generation",
                errors=["use flow_run on the requested config or explicitly request a wake demo"],
            )
        if name == "wrapper_scaffold" and not state._analysis_current(
            str(arguments.get("analysis", ""))
        ):
            return SkillResult(
                ok=False,
                skill=name,
                summary="wrapper scaffold requires current session analysis evidence",
                errors=["run wrapper_analyze with a persisted build/wrapper_smith output first"],
            )
        if name == "wrapper_scaffold" and arguments.get("vendor_from"):
            analysis_path, _ = state.fingerprint(arguments.get("analysis", ""))
            analyzed_root = state.wrapper_analysis_roots.get(analysis_path)
            vendor_root = str(state._path(arguments["vendor_from"]))
            if analyzed_root != vendor_root:
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary="wrapper vendor source does not match analyzed RTL root",
                    errors=["analyze the exact vendor_from tree before scaffolding"],
                )
        if name == "wrapper_scaffold" and bool(arguments.get("apply", False)):
            core = str(arguments.get("core", ""))
            existing_vendor = (
                state.repo_root
                / "hw"
                / "vendor"
                / "mosaic"
                / core
                / f"{core}.core"
            ).is_file()
            if not arguments.get("vendor_from") and not existing_vendor:
                return SkillResult(
                    ok=False,
                    skill=name,
                    summary="wrapper apply requires a complete vendor RTL core",
                    errors=["provide vendor_from or install hw/vendor/mosaic/<core>/<core>.core"],
                )
            if not arguments.get("vendor_from") and existing_vendor:
                analysis_path, _ = state.fingerprint(
                    arguments.get("analysis", "")
                )
                analyzed_root = state.wrapper_analysis_roots.get(
                    analysis_path
                )
                expected_root = str(
                    (
                        state.repo_root
                        / "hw"
                        / "vendor"
                        / "mosaic"
                        / core
                    ).resolve()
                )
                if analyzed_root != expected_root:
                    return SkillResult(
                        ok=False,
                        skill=name,
                        summary="existing vendor apply requires analysis of that vendor tree",
                        errors=[f"analyze {expected_root} before applying"],
                    )
        return None
    flow = arguments.get("flow")
    config = arguments.get("config", "")
    if scope_required and state.scope is None:
        return SkillResult(
            ok=False,
            skill=name,
            summary="flow blocked until request_scope classifies the requested outcome",
            errors=["call request_scope first"],
        )
    effect_error = scope_effect_precondition(state, registry, scope_required, name, arguments)
    if effect_error is not None:
        return effect_error
    from .agent_tools import flow_needs_approval

    if flow_needs_approval(str(flow)):
        refusal = _approval_refusal(registry, state.scope)
        if refusal is not None:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"flow '{flow}' needs a person's approval for the '{state.scope}' scope",
                errors=refusal,
            )
    if flow == "mosaic-gen-config" and not state.has_current(
        state.topology_ok, config
    ):
        return SkillResult(
            ok=False,
            skill=name,
            summary="mosaic generation blocked until topology_check passes",
            errors=[f"no successful topology_check evidence for {config!r}"],
        )
    if flow in {
        "tb-soc-generic",
        "tb-soc-wake",
        "tb-soc-titan",
        "tb-soc-fw",
    } and not state.has_current(
        state.generated_ok, config
    ):
        return SkillResult(
            ok=False,
            skill=name,
            summary=f"{flow} blocked until mosaic-gen-config passes",
            errors=[f"no successful generation evidence for {config!r}"],
        )
    return None

def scope_effect_precondition(
    state: Any,
    registry: Any,
    scope_required: bool,
    name: str,
    arguments: Mapping[str, Any],
) -> Optional[SkillResult]:
    scope = state.scope
    if scope is None:
        return None
    registry_specs = getattr(registry, "specs", None)
    if registry_specs is not None and name not in registry_specs:
        # Let the typed registry return its canonical unknown-tool
        # observation; it never executes an unregistered operation.
        return None
    from .agent import REQUEST_SCOPES

    all_scopes = REQUEST_SCOPES
    allowed = {
        # Decided in gate_precondition before this table is consulted.
        "request_scope": all_scopes,
        "soc_plan": all_scopes,
        "config_validate": all_scopes,
        "topology_check": all_scopes,
        "flow_list": all_scopes,
        "doc_config": all_scopes,
        "doc_dashboard": all_scopes,
        "drc_analyze": {"analysis", "drc", "physical"},
        "drc_scan": {"analysis", "drc", "physical"},
        "soc_generate": {"config", "rtl", "simulation", "integration", "physical"},
        "config_generate": {"config", "rtl", "simulation", "integration", "physical"},
        "topology_render": {"documentation"},
        "wrapper_analyze": {"analysis", "integration", "physical"},
        "wrapper_scaffold": {"integration", "physical"},
        "tb_generate": {"testbench", "simulation", "integration", "physical"},
        "tb_run": {"testbench", "simulation", "integration", "physical"},
        "tb_wake_demo": {"testbench", "simulation", "integration", "physical"},
        "tb_matrix_plan": all_scopes,
        # Above the validate tier it also needs approval (gate_precondition).
        "tb_matrix_run": {"simulation", "integration", "physical"},
        # Served by the MCP session itself; listed so the table stays total.
        "session_new": all_scopes,
        "session_status": all_scopes,
    }.get(name)
    if allowed is not None and scope not in allowed:
        return SkillResult(
            ok=False,
            skill=name,
            summary=f"{name} is not authorized by '{scope}' request scope",
            errors=["the model cannot widen the user-derived authorization ceiling"],
        )
    if name == "wrapper_analyze" and scope == "analysis" and arguments.get("output"):
        return SkillResult(
            ok=False,
            skill=name,
            summary="analysis-only wrapper inspection cannot persist output",
            errors=["omit output or request integration scope explicitly"],
        )
    if name == "flow_run":
        flow = str(arguments.get("flow", ""))
        # Read the flow's DECLARED scopes. This used to be inferred from the
        # name -- `flow.startswith("tb-")`, with an `else` that handed any
        # unrecognised flow rtl-level authorization. Tools in this same
        # function fail closed; flows failed open, so adding a flow without
        # deciding who may run it silently succeeded. M2: "every flow declares
        # effect, cost, required scope, and approval with no default."
        from .flow_spec import FlowSpecError, FlowSpec
        from .skills.flow_runner import FLOWS

        entry = FLOWS.get(flow)
        if entry is None:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"unknown flow '{flow}'",
                errors=[f"registered flows: {sorted(FLOWS)}"],
            )
        try:
            flow_scopes = FlowSpec.from_mapping(flow, entry).scopes
        except FlowSpecError as error:
            # Fail closed, exactly as an unlisted tool does.
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"flow '{flow}' has no request-scope policy",
                errors=[str(error), "fail-closed flow authorization"],
            )
        if scope not in flow_scopes:
            return SkillResult(
                ok=False,
                skill=name,
                summary=f"flow '{flow}' is not authorized by '{scope}' request scope",
                errors=["the model cannot widen the user-derived authorization ceiling"],
            )
    elif allowed is None:
        return SkillResult(
            ok=False,
            skill=name,
            summary=f"{name} has no request-scope policy",
            errors=["fail-closed tool authorization"],
        )
    return None

