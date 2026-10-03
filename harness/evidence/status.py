"""Evidence and execution status vocabularies.

Two orthogonal enums, deliberately kept disjoint.

``ExecutionStatus`` answers *"could we invoke and observe the process?"* and
follows ``openada.result/v0alpha1`` (simra-tech/OpenADA, MIT).

``EvidenceStatus`` answers *"what does the evidence support?"* and follows
roadmap §12.2, which is a strict superset of OpenADA's four engineering states:
it adds ``UNSUPPORTED`` (a deterministic capability proof that the requested
operation cannot be implemented) and ``INFRASTRUCTURE_ERROR`` (the stage ran
but did not produce a parseable mandatory report).

The rules encoded here, quoting roadmap §12.2:

- ``UNKNOWN``: the node has not run, or an upstream prerequisite has no
  evidence.
- ``UNSUPPORTED``: deterministic capability analysis proves the requested
  operation cannot be implemented by the selected backend/tool/PDK.
- ``INFRASTRUCTURE_ERROR``: an executed stage did not produce a mandatory
  report, or the report could not be parsed or validated.
- ``FAIL``: the stage executed validly but a threshold was violated.
- ``NOT_APPLICABLE``: deterministic graph construction proves the node is
  irrelevant. *The model cannot assign it.*
- ``PASS``: the node executed, all mandatory reports are valid, and every
  required threshold passed.

A zero exit code never implies ``PASS``; a nonzero exit code never implies
``FAIL``. An incomplete execution normally leaves evidence ``UNKNOWN``.
"""

from __future__ import annotations

from enum import Enum


class ExecutionStatus(str, Enum):
    """Whether the harness invoked and observed the native process."""

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    NOT_AVAILABLE = "not_available"
    INVALID_REQUEST = "invalid_request"
    FAILED = "failed"

    @property
    def observed_a_complete_run(self) -> bool:
        """True only when the process ran to completion and was observed.

        Every other execution status means the engineering conclusion cannot
        be stronger than ``UNKNOWN`` (or ``INFRASTRUCTURE_ERROR`` when the run
        started but produced no usable report).
        """
        return self is ExecutionStatus.COMPLETED


class EvidenceStatus(str, Enum):
    """What the collected evidence supports (roadmap §12.2)."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"

    @property
    def closes_required_node(self) -> bool:
        """Only ``PASS`` may close a required evidence-graph node.

        ``NOT_APPLICABLE`` does not close a node — deterministic graph
        construction *removes* the node instead, which is why it is not
        accepted here and why the model is never allowed to assign it.
        """
        return self is EvidenceStatus.PASS

    @property
    def is_adverse(self) -> bool:
        """True when this status should surface as a failure to the caller.

        ``UNKNOWN`` is deliberately included: a gate that did not run is not a
        pass, and a caller that requested the gate must not proceed as though
        it had.
        """
        return self is not EvidenceStatus.PASS and self is not EvidenceStatus.NOT_APPLICABLE


def worst(*statuses: EvidenceStatus) -> EvidenceStatus:
    """Combine evidence statuses, returning the most adverse one.

    Ordering, most to least adverse::

        FAIL > INFRASTRUCTURE_ERROR > UNSUPPORTED > UNKNOWN > NOT_APPLICABLE > PASS

    ``FAIL`` outranks ``INFRASTRUCTURE_ERROR`` because a proven threshold
    violation is a stronger, more actionable statement than a broken harness.
    ``PASS`` is weakest so that combining it with anything else cannot mask
    the other result.
    """
    order = {
        EvidenceStatus.FAIL: 5,
        EvidenceStatus.INFRASTRUCTURE_ERROR: 4,
        EvidenceStatus.UNSUPPORTED: 3,
        EvidenceStatus.UNKNOWN: 2,
        EvidenceStatus.NOT_APPLICABLE: 1,
        EvidenceStatus.PASS: 0,
    }
    if not statuses:
        return EvidenceStatus.UNKNOWN
    return max(statuses, key=lambda s: order[s])
