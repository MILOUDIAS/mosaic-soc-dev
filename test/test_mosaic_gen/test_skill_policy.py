"""The rule that says what a skill may do, and every skill checked against it.

Twenty-one flows declared effect/cost/scopes/approval; the ten skills that
orchestrate them declared nothing, so the boundary was stated for the thing
being run and not for the thing running it.

The rule is `approval == (cost is HOURS) or evidence`. Its first clause is not
invented: it is what all 21 flows already did. The second is the extension, and
it exists because cost says nothing about evidence integrity and the next
skills this project wants are the ones that would write a waiver.
"""
from __future__ import annotations

import pytest

from harness.flow_spec import Cost, Effect, FlowSpec, FlowSpecError, \
    REQUEST_SCOPES, requires_approval
from harness.skill_policy import SKILLS, SKILL_SPECS, VERIFIED, approval_required
from harness.skills.flow_runner import FLOWS


# ── the rule itself ──────────────────────────────────────────────────
@pytest.mark.parametrize("cost,evidence,expected", [
    (Cost.HOURS,   False, True),   # spends an afternoon
    (Cost.SECONDS, True,  True),   # writes a waiver in milliseconds
    (Cost.HOURS,   True,  True),
    (Cost.MINUTES, False, False),
    (Cost.SECONDS, False, False),
])
def test_the_rule(cost, evidence, expected):
    assert requires_approval(cost, evidence) is expected


def test_evidence_is_independent_of_cost():
    """The whole point of the second clause.

    A waiver takes milliseconds to write, which is exactly why cost alone
    cannot gate it.
    """
    assert requires_approval(Cost.SECONDS, True) is True


# ── the first clause is derived, not invented ────────────────────────
def test_the_cost_clause_matches_what_every_flow_already_did():
    """21/21. If this breaks, the rule stopped describing the flow table."""
    for name, entry in FLOWS.items():
        if "effect" not in entry:
            continue
        hours = entry["cost"] == "hours"
        assert entry["approval"] == hours, (
            f"flow {name} declares approval={entry['approval']} at "
            f"cost={entry['cost']}")


def test_no_flow_writes_evidence_today():
    """Recorded so the first one that does is a visible change."""
    assert all(e.get("evidence") is False
               for e in FLOWS.values() if "effect" in e)


# ── enforcement is fail-closed, in both directions ───────────────────
def _entry(**over):
    base = {"effect": "read", "cost": "seconds", "scopes": ["analysis"],
            "approval": False, "evidence": False}
    base.update(over)
    return base


def test_hours_without_approval_is_refused():
    with pytest.raises(FlowSpecError, match="cost is hours"):
        FlowSpec.from_mapping("x", _entry(cost="hours", approval=False))


def test_evidence_without_approval_is_refused():
    with pytest.raises(FlowSpecError, match="writes evidence"):
        FlowSpec.from_mapping("x", _entry(evidence=True, approval=False))


def test_approval_for_neither_is_refused():
    """An unnecessary gate is a real cost: it trains people to click through."""
    with pytest.raises(FlowSpecError, match="requires False"):
        FlowSpec.from_mapping("x", _entry(approval=True))


def test_a_missing_evidence_field_is_refused():
    entry = _entry()
    del entry["evidence"]
    with pytest.raises(FlowSpecError, match="evidence"):
        FlowSpec.from_mapping("x", entry)


def test_evidence_must_be_a_bool():
    with pytest.raises(FlowSpecError, match="must be a bool"):
        FlowSpec.from_mapping("x", _entry(evidence="yes", approval=True))


# ── every skill checked against it ───────────────────────────────────
def test_every_skill_module_has_a_declaration():
    """A skill with no policy is not authorized by default."""
    import pathlib
    from harness.core import REPO_ROOT
    modules = {p.stem.replace("_", "-")
               for p in (REPO_ROOT / "harness/skills").glob("*.py")
               if p.stem != "__init__"}
    assert modules == set(SKILLS), {
        "module without a declaration": sorted(modules - set(SKILLS)),
        "declaration without a module": sorted(set(SKILLS) - modules),
    }


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_each_skill_obeys_the_rule(name):
    spec = SKILL_SPECS[name]
    assert spec.approval == requires_approval(spec.cost, spec.evidence)


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_each_skill_declares_legal_scopes(name):
    spec = SKILL_SPECS[name]
    assert spec.scopes, f"{name} declares no scopes, so nothing could run it"
    assert spec.scopes <= REQUEST_SCOPES


def test_no_skill_writes_evidence_today():
    """Checked independently of the classification that produced this table.

    `signoff_waivers` is named in two skill modules: flow_runner, at two
    `"waivers":` keys that feed load_waivers, and waiver_author, which loads
    the waiver file to audit it. `signoff_template` is named in none. All
    read-side. This test is that grep, so the first
    skill able to author a waiver fails here rather than shipping quietly.
    """
    import re
    from harness.core import REPO_ROOT
    pattern = re.compile(r"signoff_waivers|signoff_template")
    writers = []
    for path in sorted((REPO_ROOT / "harness/skills").glob("*.py")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if not pattern.search(line):
                continue
            if re.search(r"write|dump|open\([^)]*['\"][wa]", line, re.I):
                writers.append(f"{path.name}:{i}")
    assert not writers, (
        f"a skill writes an evidence artifact: {writers}. It must declare "
        "evidence=True, which the rule turns into an approval gate")
    assert all(s.evidence is False for s in SKILL_SPECS.values())


def test_the_two_hours_skills_require_approval():
    """Cost is what a skill INVOKES, not what its Python returns."""
    assert approval_required("flow-runner")   # can launch harden-classic
    assert approval_required("tb-matrix")     # a full covering-array sweep
    assert not approval_required("drc-triage")


def test_setup_wizard_is_answered_rather_than_left_open():
    """The board asked whether an agent may re-run environment setup.

    Under the rule it may: it writes environment files, not evidence, and costs
    seconds. What bounds it is the narrow scope, not an approval gate.
    """
    spec = SKILL_SPECS["setup-wizard"]
    assert spec.evidence is False
    assert spec.approval is False
    assert spec.scopes == {"config"}


def test_an_undeclared_skill_is_refused_not_defaulted():
    """Deliberately a name nothing will ever claim.

    This test first used "waiver-author" as the example, and then that skill
    was written and declared, so the test started asserting the opposite of
    what it meant. An example of an absent thing must be one that stays absent.
    """
    with pytest.raises(KeyError, match="no policy for skill"):
        approval_required("no-such-skill-and-never-will-be")


def test_verification_provenance_is_recorded():
    """Which entries survived an independent attempt to refute them."""
    assert VERIFIED == {"config-author", "doc-gen", "drc-triage"}
    assert VERIFIED < set(SKILLS), "provenance should not claim all ten"


# ── cards, and the one skill deliberately without an agent surface ───
def test_every_skill_has_a_card_at_its_declared_path():
    """The mapping is declared because the names are semantic, not mechanical.

    An earlier version of this check derived the card name by replacing
    underscores with hyphens and reported six skills as cardless when only
    setup-wizard was.
    """
    from harness.core import REPO_ROOT
    from harness.skill_policy import CARDS
    assert set(CARDS) == set(SKILLS), {
        "declared without a card entry": sorted(set(SKILLS) - set(CARDS)),
        "card entry without a skill": sorted(set(CARDS) - set(SKILLS)),
    }
    missing = [f"{s} -> .claude/skills/{c}/SKILL.md"
               for s, c in CARDS.items()
               if not (REPO_ROOT / ".claude/skills" / c / "SKILL.md").is_file()]
    assert not missing, missing


def test_setup_wizard_is_cli_only():
    """It selects the driver that owns the agent loop.

    `deterministic` and `api` set required_scope in-process; `claude` and `omp`
    delegate enforcement to an external tool. A component able to rewrite that
    choice can move the gates out of the process doing the gating, so nothing
    but a person may run it.
    """
    from harness.core import REPO_ROOT
    surfaces = ["harness/agent_tools.py", "harness/mcp_server.py",
                "harness/agent.py"]
    exposed = [
        s for s in surfaces
        if "setup_wizard" in (REPO_ROOT / s).read_text()
        or "setup-wizard" in (REPO_ROOT / s).read_text()
    ]
    assert not exposed, (
        f"setup-wizard became reachable from {exposed}. It chooses where gate "
        "enforcement lives; see .claude/skills/setup-wizard/SKILL.md before "
        "exposing it")
