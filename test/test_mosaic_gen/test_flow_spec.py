"""M2: every flow declares effect, cost, required scope and approval — no default.

The defect this closes was an asymmetry inside one function. `gates.py` was
fail-closed for tools:

    elif allowed is None:
        return "... has no request-scope policy" / "fail-closed tool authorization"

and fail-OPEN for flows, deciding authorization by reading the name:

    elif flow.startswith("tb-") or flow in {"verilator-run", "pytest"}:
        ...
    else:
        flow_scopes = {"rtl", "simulation", "integration", "physical"}

So a flow whose authorization nobody chose inherited one from its prefix, and
adding a flow without deciding who may run it succeeded silently.
"""

import pytest

from harness.flow_spec import (
    REQUEST_SCOPES,
    REQUIRED_FIELDS,
    Cost,
    Effect,
    FlowSpec,
    FlowSpecError,
    build_specs,
)
from harness.skills.flow_runner import FLOWS


# ── the exit criterion, over the real table ──────────────────────────

def test_every_shipped_flow_declares_its_policy():
    specs = build_specs(FLOWS)
    assert len(specs) == len(FLOWS)
    for name, spec in specs.items():
        assert isinstance(spec.effect, Effect), name
        assert isinstance(spec.cost, Cost), name
        assert spec.scopes, name
        assert isinstance(spec.approval, bool), name


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_a_flow_missing_any_required_field_is_refused(field):
    entry = dict(FLOWS["mosaic-gen"])
    entry.pop(field)
    with pytest.raises(FlowSpecError, match="does not declare"):
        FlowSpec.from_mapping("mosaic-gen", entry)


def test_the_whole_table_is_refused_if_one_flow_is_undeclared():
    """All-or-nothing: a partly typed table invites the old fallback back."""
    broken = dict(FLOWS)
    broken["sneaky"] = {"description": "no policy", "timeout": 10,
                        "cmd": ["true"]}
    with pytest.raises(FlowSpecError, match="sneaky"):
        build_specs(broken)


# ── the values have to mean something ────────────────────────────────

def test_typos_in_the_authorization_table_are_caught():
    """A typo'd scope makes a flow unreachable or over-permitted."""
    entry = dict(FLOWS["mosaic-gen"], scopes=["simulaton"])
    with pytest.raises(FlowSpecError, match="unknown scope"):
        FlowSpec.from_mapping("x", entry)


def test_a_flow_nobody_can_run_is_refused():
    entry = dict(FLOWS["mosaic-gen"], scopes=[])
    with pytest.raises(FlowSpecError, match="no scopes"):
        FlowSpec.from_mapping("x", entry)


@pytest.mark.parametrize("field,bad", [
    ("effect", "destroy"), ("cost", "eternal"), ("approval", "yes"),
])
def test_invalid_values_are_refused(field, bad):
    entry = dict(FLOWS["mosaic-gen"], **{field: bad})
    with pytest.raises(FlowSpecError):
        FlowSpec.from_mapping("x", entry)


def test_declared_scopes_are_all_real_request_scopes():
    for name, spec in build_specs(FLOWS).items():
        assert spec.scopes <= REQUEST_SCOPES, name


# ── the declarations match what the flows actually do ────────────────

def test_the_hours_long_flows_are_hardening_and_formal():
    """Cost is an expectation, not the timeout.

    Hardening: measured 2h19m to 11h. `lec` joins them because formal
    equivalence on a 70k-cell design runs for hours and may not converge --
    declaring it `minutes` would let a driver start it casually.
    """
    specs = build_specs(FLOWS)
    hours = {n for n, s in specs.items() if s.cost is Cost.HOURS}
    assert hours == {"harden-classic", "harden-chip", "lec"}


def test_physical_flows_are_physical_scope_only_and_need_approval():
    """Hours of compute producing tapeout candidates: scope is necessary
    and not sufficient."""
    specs = build_specs(FLOWS)
    for name in ("harden-classic", "harden-chip"):
        assert specs[name].scopes == {"physical"}, name
        assert specs[name].approval is True, name


def test_approval_is_reserved_for_the_expensive():
    """Approval is for the expensive and irreversible, or it is noise.

    Exactly the hours-long flows need it, and nothing else does -- an approval
    prompt on a two-minute simulation trains people to click through.
    """
    specs = build_specs(FLOWS)
    needing = {n for n, s in specs.items() if s.approval}
    assert needing == {"harden-classic", "harden-chip", "lec"}
    assert needing == {n for n, s in specs.items() if s.cost is Cost.HOURS}


def test_generation_flows_write_and_simulations_execute():
    specs = build_specs(FLOWS)
    assert specs["mosaic-gen"].effect is Effect.WRITE
    assert specs["mosaic-gen-config"].effect is Effect.WRITE
    assert specs["tb-soc-wake"].effect is Effect.EXECUTE


def test_running_the_test_suite_is_allowed_under_analysis():
    """Otherwise checking the tooling forces someone to widen their scope."""
    assert "analysis" in build_specs(FLOWS)["pytest"].scopes


# ── the gate reads the declaration, not the name ─────────────────────

def gate(flow: str, scope: str):
    from harness.agent import AgentState
    from harness.agent_tools import AgentToolRegistry
    from harness.core import REPO_ROOT
    from harness.gates import gate_precondition

    state = AgentState(repo_root=REPO_ROOT)
    state.required_scope = scope
    state.scope = scope
    return gate_precondition(state, AgentToolRegistry(repo_root=REPO_ROOT),
                             True, "flow_run",
                             {"flow": flow, "config": "configs/x.yaml"})


def test_a_physical_flow_is_refused_under_simulation_scope():
    refusal = gate("harden-classic", "simulation")
    assert refusal is not None
    assert "not authorized by 'simulation'" in refusal.summary


def scope_refusal(flow: str, scope: str):
    """The refusal ONLY if it came from the scope gate.

    `tb-soc-wake` under `simulation` passes the scope check and is then held
    by evidence binding ("blocked until mosaic-gen-config passes"), which is a
    separate and correct rule. Asserting `is None` would conflate the two and
    make this test pass for the wrong reason.
    """
    refusal = gate(flow, scope)
    if refusal is None:
        return None
    if "not authorized by" in refusal.summary or "unknown flow" in refusal.summary:
        return refusal
    return None


def test_a_simulation_flow_is_permitted_under_simulation_scope():
    assert scope_refusal("tb-soc-wake", "simulation") is None


def test_an_unknown_flow_is_refused_rather_than_defaulted():
    """The old `else` branch gave this rtl-level authorization."""
    refusal = gate("totally-new-flow", "rtl")
    assert refusal is not None
    assert "unknown flow" in refusal.summary


def test_scope_no_longer_depends_on_the_name_prefix(monkeypatch):
    """Rename a simulation flow away from `tb-` and its policy must follow it.

    Under the old inference this flow would have fallen into the `else` branch
    and been authorized at rtl scope.
    """
    import harness.skills.flow_runner as fr

    renamed = dict(FLOWS)
    renamed["soc-wake"] = dict(FLOWS["tb-soc-wake"])
    monkeypatch.setattr(fr, "FLOWS", renamed)

    # Still simulation-scoped, because it says so.
    assert scope_refusal("soc-wake", "simulation") is None
    # And still refused at a scope it does not declare.
    refusal = scope_refusal("soc-wake", "config")
    assert refusal is not None and "not authorized" in refusal.summary
