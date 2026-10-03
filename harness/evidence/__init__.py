"""Typed evidence primitives for the oh-my-soc harness.

This package implements the mechanics for the evidence model specified in
``docs/general_multicore_soc_generator_roadmap.md`` §12, starting with the
parts M0 needs: fail-closed gates, an explicit evidence-state vocabulary, and
truthful signoff parsing.

The design rule that motivates the whole package is roadmap §12.2:

    An exit code is execution evidence, not qualification evidence.
    Only ``PASS`` may close a required graph node.

See ``docs/evidence_gate_hardening_proposal.md`` for the rationale and for the
upstream sources (OpenADA, CoreSmith) each mechanism is adapted from.
"""

from harness.evidence.gate_guard import (
    GateResult,
    gate_error_finding,
    gate_fail_open_enabled,
    gate_guard,
)
from harness.evidence.status import EvidenceStatus, ExecutionStatus

__all__ = [
    "EvidenceStatus",
    "ExecutionStatus",
    "GateResult",
    "gate_error_finding",
    "gate_fail_open_enabled",
    "gate_guard",
]
