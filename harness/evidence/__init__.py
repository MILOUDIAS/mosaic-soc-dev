"""Typed evidence primitives for the mosaic harness.

This package implements the mechanics of the harness's evidence model,
starting with the parts every flow gate needs: fail-closed gates, an explicit
evidence-state vocabulary, and truthful signoff parsing.

The design rule that motivates the whole package:

    An exit code is execution evidence, not qualification evidence.
    Only ``PASS`` may close a required graph node.

The mechanisms are adapted from two upstream projects, OpenADA and CoreSmith.
Each module's docstring gives the rationale and names the source it follows.
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
