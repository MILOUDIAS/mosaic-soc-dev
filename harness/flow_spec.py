"""Typed flow declarations: effect, cost, scope and approval, with no default.

The rule: "every flow declares effect, cost, required
scope, and approval with no default."

THE DEFECT THIS CLOSES
----------------------
`harness/gates.py` is fail-closed for TOOLS -- a tool missing from the scope
table is refused with "fail-closed tool authorization". For FLOWS it was
fail-OPEN, and it decided authorization by reading the flow's NAME:

    if flow in AgentToolRegistry.PHYSICAL_FLOWS:
        flow_scopes = {"physical"}
    elif flow.startswith("tb-") or flow in {"verilator-run", "pytest"}:
        flow_scopes = {"testbench", "simulation", "integration", "physical"}
    else:
        flow_scopes = {"rtl", "simulation", "integration", "physical"}

So a new flow got rtl-level authorization silently, and a simulation flow that
happened not to start with `tb-` got a wider scope than it should. Adding a
flow without deciding what may run it SUCCEEDED. "No default" means that has
to be impossible, and here it is an import-time error.

WHY EACH FIELD
--------------
`effect` -- read, write or execute. The same vocabulary `AgentToolSpec` uses,
so a flow and a tool can be reasoned about together.

`cost` -- what running it spends. Not a timeout, which is a limit: this is the
expectation, and it is the difference between a driver running something and
asking first. Measured, not guessed: `harden-classic` is HOURS because the runs
in this project took 2 h 19 m to 11 h.

`scopes` -- the request scopes that may run it, stated rather than inferred
from a prefix.

`approval` -- whether scope alone is insufficient. Physical flows consume hours
of compute and produce tapeout candidates; a correct scope is necessary and
not sufficient for those.

`evidence` -- whether what it writes encodes a DECISION rather than a result
that can be regenerated. The four fields above describe cost well and say
nothing about evidence integrity, and the gap is not hypothetical: the next
skills this project wants are `waiver-author` and a closure-report generator,
and both write files that assert something about silicon.

The test is regeneration. Run it again from the same inputs: if you get the
same bytes, it is a rebuildable output -- generated RTL, a config, a wrapper
scaffold, a diagram, a summary that restates metrics.json. If you cannot,
because a person decided something, it is evidence: a waiver accepting a known
defect, a signoff template deciding what gets checked, an LVS config deciding
what gets compared.

`scopes` cannot express this and it was tried: `mosaic-gen` carries the
`physical` scope and writes only regenerable RTL, so gating on scope would
demand approval for routine generation while still missing a waiver edit.

THE RULE
--------
    approval == (cost is HOURS) or evidence

The first clause is not invented. It is what all 21 existing flows already do,
without exception: the three hour-cost flows require approval and the other
eighteen do not. The second clause is the extension, and it is deliberately
independent of cost -- writing a waiver takes milliseconds, and that is
precisely why it must not be allowed to happen unattended.

`requires_approval` below is that rule as code, and `from_mapping` refuses any
declaration that disagrees with it. A table whose approval column drifts from
the rule is a table that no longer states a policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping

# Every request scope the policy classifier can derive. A flow declaring a
# scope outside this set is a typo, and typos in an authorization table are
# how a flow becomes unreachable or over-permitted.
REQUEST_SCOPES: FrozenSet[str] = frozenset({
    "analysis", "config", "rtl", "simulation", "physical",
    "integration", "testbench", "documentation", "drc",
})


class Effect(Enum):
    """What running the flow does to the tree. Matches AgentToolSpec."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class Cost(Enum):
    """What it spends. An expectation, unlike `timeout`, which is a limit."""

    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"


class FlowSpecError(ValueError):
    """A flow that does not declare its policy. Raised at import."""


def requires_approval(cost: "Cost", evidence: bool) -> bool:
    """The rule, in one place, for flows and skills alike.

    Approval when it spends hours, OR when it writes evidence at any cost.
    Kept as a function rather than inlined so the two callers cannot drift and
    so a test can assert the rule itself rather than its consequences.
    """

    return cost is Cost.HOURS or bool(evidence)


REQUIRED_FIELDS = ("effect", "cost", "scopes", "approval", "evidence")


@dataclass(frozen=True)
class FlowSpec:
    """One flow's policy, separate from how it is executed."""

    name: str
    description: str
    effect: Effect
    cost: Cost
    scopes: FrozenSet[str]
    approval: bool
    evidence: bool = False

    def permits(self, scope: str) -> bool:
        return scope in self.scopes

    @classmethod
    def from_mapping(cls, name: str, entry: Mapping[str, Any]) -> "FlowSpec":
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        if missing:
            raise FlowSpecError(
                f"flow {name!r} does not declare {missing}. Every flow states "
                "its effect, cost, scopes, approval and evidence explicitly -- "
                "there is "
                "no default, because a flow whose authorization nobody chose "
                "used to inherit one from its name prefix")

        try:
            effect = Effect(entry["effect"])
        except ValueError:
            raise FlowSpecError(
                f"flow {name!r}: effect {entry['effect']!r} is not one of "
                f"{[e.value for e in Effect]}") from None
        try:
            cost = Cost(entry["cost"])
        except ValueError:
            raise FlowSpecError(
                f"flow {name!r}: cost {entry['cost']!r} is not one of "
                f"{[c.value for c in Cost]}") from None

        scopes = frozenset(entry["scopes"])
        if not scopes:
            raise FlowSpecError(
                f"flow {name!r} declares no scopes, so nothing could ever run "
                "it. Delete it or say who may")
        unknown = scopes - REQUEST_SCOPES
        if unknown:
            raise FlowSpecError(
                f"flow {name!r} declares unknown scope(s) {sorted(unknown)}; "
                f"valid scopes are {sorted(REQUEST_SCOPES)}")

        approval = entry["approval"]
        if not isinstance(approval, bool):
            raise FlowSpecError(
                f"flow {name!r}: approval must be a bool, got {approval!r}")

        evidence = entry["evidence"]
        if not isinstance(evidence, bool):
            raise FlowSpecError(
                f"flow {name!r}: evidence must be a bool, got {evidence!r}")

        expected = requires_approval(cost, evidence)
        if approval != expected:
            why = ("cost is hours" if cost is Cost.HOURS else "") + \
                  (" and " if cost is Cost.HOURS and evidence else "") + \
                  ("it writes evidence" if evidence else "")
            raise FlowSpecError(
                f"flow {name!r} declares approval={approval} but the rule "
                f"requires {expected}"
                + (f", because {why}" if expected else
                   " -- it costs neither hours nor writes evidence, so an "
                   "approval gate here trains people to click through the "
                   "ones that matter"))

        return cls(name=name, description=str(entry.get("description", "")),
                   effect=effect, cost=cost, scopes=scopes, approval=approval,
                   evidence=evidence)


def build_specs(flows: Mapping[str, Mapping[str, Any]]) -> Dict[str, FlowSpec]:
    """Type every flow, or refuse the whole table.

    All-or-nothing on purpose: a partially typed table invites the same
    fallback this module exists to delete.
    """
    return {name: FlowSpec.from_mapping(name, entry)
            for name, entry in flows.items()}
