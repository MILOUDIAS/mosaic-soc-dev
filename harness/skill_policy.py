"""What each skill is allowed to do, in the same terms as a flow.

Twenty-one flows declared effect, cost, scopes and approval; the ten skills
that ORCHESTRATE those flows declared nothing. So the boundary was stated for
the thing being run and not for the thing doing the running, and a new skill
inherited no policy at all.

These declarations go through `build_specs`, the same function the flow table
uses, so skills are held to the same rule and the same import-time refusals:
an undeclared field, an unknown scope, or an approval column that disagrees
with `requires_approval` fails at import rather than at run time.

HOW THESE WERE DERIVED
----------------------
Each entry comes from reading the skill module, its CLI wiring in
`harness/__main__.py`, its agent surface in `harness/agent_tools.py`, and its
card under `.claude/skills/`. Three were then adversarially re-checked by an
independent pass that tried to refute them (config_author, doc_gen,
drc_triage); all three survived. The remaining seven carry the classification
without that second pass, and the honest status is recorded per entry.

The claim that matters most -- that NO skill writes evidence -- does not rest
on either pass. It is checkable in one line and was checked separately:
`signoff_waivers` is named in two skill modules: `flow_runner`, at two
`"waivers":` keys that feed `load_waivers`, and `waiver_author`, which loads
the waiver file to audit it. `signoff_template` is named in none. All are
read-side. Nothing here can author a waiver today,
which is why every entry declares evidence False and why the rule's second
clause currently binds nothing.

That will change with the first `waiver-author` skill, and the point of
declaring the field now is that such a skill cannot be added without answering
the question.

COST IS WHAT IT INVOKES, NOT WHAT IT RETURNS
--------------------------------------------
`flow_runner` and `tb_matrix` return in seconds and are declared HOURS, because
what they launch is a LibreLane hardening run and a full covering-array sweep.
Declaring the Python's own runtime would have put the two skills that can spend
an afternoon of compute on the no-approval side of the rule.
"""
from __future__ import annotations

from typing import Dict

from .flow_spec import FlowSpec, build_specs

# `verified` is provenance, not policy: True where an independent adversarial
# pass tried to refute the entry and failed. It is deliberately not a field of
# FlowSpec -- how confident we are in a declaration is not the same kind of
# fact as what the declaration says.
SKILLS: Dict[str, dict] = {
    "config-author": {
        "description": "Author and validate mosaic.yaml SoC configs",
        "effect": "write", "cost": "seconds",
        "scopes": ["config", "rtl", "simulation", "integration", "physical"],
        "approval": False, "evidence": False, "verified": True,
        # Writes configs/<name>.yaml. Regenerating from the same arguments
        # produces identical bytes, which is the rule's rebuildable case.
    },
    "doc-gen": {
        "description": "Config summaries, memory map, dashboard metrics",
        "effect": "read", "cost": "seconds",
        "scopes": ["analysis", "config", "rtl", "simulation", "integration",
                   "physical", "testbench", "documentation", "drc"],
        "approval": False, "evidence": False, "verified": True,
        # Writes nothing at all: every exit is stdout or a returned string.
        # Its closure summaries restate metrics.json rather than asserting
        # anything, so they stay rebuildable even when written by a caller.
    },
    "drc-triage": {
        "description": "Classify DRC/LVS reports into typed violations",
        "effect": "read", "cost": "seconds",
        "scopes": ["analysis", "drc", "physical"],
        "approval": False, "evidence": False, "verified": True,
        # Reads reports, returns findings. Notably does NOT write a waiver,
        # which is the adjacent thing a triage skill is tempted to do.
    },
    "flow-preflight": {
        "description": "Check a long flow's preconditions before it runs",
        "effect": "read", "cost": "seconds",
        "scopes": ["analysis", "physical", "config"],
        # NOT verified: `verified` means an independent attempt to refute the
        # entry survived, and nobody has tried to break this one. Its checks
        # are each pinned to an incident that already happened, which is
        # evidence they are real -- not evidence they are sufficient.
        "approval": False, "evidence": False, "verified": False,
        # Reads disk, the config, the run directory and the bundle index.
        # Writes nothing, and deliberately cannot fix anything -- a preflight
        # that repairs state hides the thing it is meant to surface.
    },
    "flow-runner": {
        "description": "Run RTL, simulation, firmware and hardening flows",
        "effect": "execute", "cost": "hours",
        "scopes": ["analysis", "config", "rtl", "simulation", "integration",
                   "physical", "testbench", "documentation", "drc"],
        "approval": True, "evidence": False, "verified": False,
        # HOURS because it can launch harden-classic. Reads signoff_waivers
        # via load_waivers to apply them to results; never writes them.
    },
    "setup-wizard": {
        "description": "Environment and toolchain setup",
        "effect": "write", "cost": "seconds",
        "scopes": ["config"],
        "approval": False, "evidence": False, "verified": False,
        # The board asked whether an agent may re-run environment setup. It
        # already cannot: setup_wizard appears ZERO times in agent_tools.py,
        # mcp_server.py and agent.py, and test_setup_wizard_is_cli_only pins
        # that. The reason it must stay that way is what the file selects --
        # the driver that owns the agent loop. `deterministic` and `api` set
        # required_scope in-process (agent.py:1131,1139); `claude` and `omp`
        # delegate enforcement to an external tool's permission model. Rewriting
        # it moves the gates out of the process doing the gating.
        #
        # The declaration below is still correct for what it DOES when run: a
        # user-level preference file is not evidence. Being unreachable from an
        # agent is a separate protection, and the card records it as deliberate.
    },
    "soc-from-prompt": {
        "description": "Natural language to verified SoC, gated pipeline",
        "effect": "execute", "cost": "minutes",
        "scopes": ["config", "rtl", "simulation", "integration", "physical"],
        "approval": False, "evidence": False, "verified": False,
        # Orchestrates config-author, topo-viz, mosaic-gen and a wake demo.
        # Minutes, not hours: it does not reach the hardening flows.
    },
    "tb-matrix": {
        "description": "Covering-array sweep of the integration space",
        "effect": "execute", "cost": "hours",
        "scopes": ["simulation", "integration", "physical"],
        "approval": True, "evidence": False, "verified": False,
        # HOURS: one render plus one liveness sim per point in the array.
    },
    "tb-smith": {
        "description": "Generate and run a wrapped core's testbench",
        "effect": "execute", "cost": "minutes",
        "scopes": ["simulation", "integration", "physical", "testbench"],
        "approval": False, "evidence": False, "verified": False,
        # Writes testbenches, then runs them. A generated TB is rebuildable.
    },
    "topo-viz": {
        "description": "Semantic topology checks and interactive diagram",
        "effect": "write", "cost": "seconds",
        "scopes": ["analysis", "config", "rtl", "simulation", "integration",
                   "physical", "documentation"],
        "approval": False, "evidence": False, "verified": False,
        # Writes an HTML/SVG diagram derived from the config.
    },
    "gls-triage": {
        "description": "Read a gate-level run and say what its verdict supports",
        "effect": "read", "cost": "seconds",
        "scopes": ["analysis", "simulation", "physical", "testbench"],
        "approval": False, "evidence": False, "verified": False,
        # Reads a log and, when offered a control, two netlists. It does not
        # RUN the simulation: that is the `gls` flow, minutes, gated on its
        # own marker. Keeping them separate is why this stays seconds.
    },
    "netlist-diff": {
        "description": "What the tools did differently between two runs",
        "effect": "read", "cost": "seconds",
        "scopes": ["analysis", "physical"],
        "approval": False, "evidence": False, "verified": False,
        # Reads two netlists. Seconds even on 12 MB files: 2.4 s for the
        # census fallback, and md5 short-circuits the identical case.
    },
    "pdk-port": {
        "description": "What a technology must supply, and what it would inherit",
        "effect": "read", "cost": "seconds",
        "scopes": ["analysis", "config", "physical"],
        "approval": False, "evidence": False, "verified": False,
        # Reports the porting surface. Porting means hardening a design on the
        # new technology and measuring it, which no skill can shortcut.
    },
    "waiver-author": {
        "description": "Audit a waiver against the run it cites",
        "effect": "read", "cost": "seconds",
        "scopes": ["analysis", "physical", "drc"],
        "approval": False, "evidence": False, "verified": False,
        # READ, and the name is the only thing about it that says otherwise.
        # It checks a waiver against its evidence; it does not write one.
        # Authoring a waiver is a decision to accept a defect in silicon, and
        # the rule in flow_spec would put an approval gate on a skill that did
        # it -- evidence=True, approval at any cost. That gate is correct and
        # the better answer is that the decision stays with a person, with a
        # tool that says whether it still matches the measurement.
    },
    "wrapper-smith": {
        "description": "Wrap an open-source core for the MOSAIC SCI",
        "effect": "execute", "cost": "minutes",
        "scopes": ["analysis", "integration", "physical"],
        "approval": False, "evidence": False, "verified": False,
        # The largest write surface here: a wrapper plus eight integration
        # touchpoints. All scaffolds, all regenerable, none evidence. Dry-run
        # by default; --apply is what commits to the tree.
    },
}

#: Where each skill's card lives under .claude/skills/. Declared, not derived:
#: the names are semantic rather than mechanical (config-author is documented as
#: soc-config), and a test that guessed the mapping reported six skills as
#: cardless when only one was.
CARDS: Dict[str, str] = {
    "config-author": "soc-config",
    "doc-gen": "soc-docs",
    "drc-triage": "soc-drc-triage",
    "flow-preflight": "flow-preflight",
    "flow-runner": "soc-flows",
    "gls-triage": "gls-triage",
    "netlist-diff": "netlist-diff",
    "pdk-port": "pdk-port",
    "setup-wizard": "setup-wizard",
    "soc-from-prompt": "soc-from-prompt",
    "tb-matrix": "tb-matrix",
    "tb-smith": "tb-smith",
    "topo-viz": "soc-topology",
    "waiver-author": "waiver-author",
    "wrapper-smith": "wrapper-smith",
}


SKILL_SPECS: Dict[str, FlowSpec] = build_specs(
    {name: {k: v for k, v in entry.items() if k != "verified"}
     for name, entry in SKILLS.items()}
)

#: Skills whose declaration survived an independent attempt to refute it.
VERIFIED = frozenset(n for n, e in SKILLS.items() if e["verified"])


def approval_required(skill: str) -> bool:
    """Whether this skill needs a human before it runs."""

    spec = SKILL_SPECS.get(skill)
    if spec is None:
        raise KeyError(
            f"no policy for skill {skill!r}. A skill with no declaration is "
            "not authorized by default; add it to SKILLS")
    return spec.approval
