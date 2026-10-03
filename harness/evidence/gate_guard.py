"""Fail-closed gate execution.

A gate that cannot run is not a pass. This module exists because the opposite
default is easy to write by accident: wrap a gate in ``try/except``, return
``True`` on error, and every broken parser, missing tool, or typo in the gate
itself silently ships as a PASS.

The mechanism is adapted from CoreSmith's ``orchestrator/langgraph/gate_guard.py``
(facebookexperimental/coresmith, MIT), whose docstring names the bug it fixed:
call sites that returned ``passed=True`` on any error, *"a fail-OPEN default
that silently shipped a block whose gate could not run."* The status vocabulary
is ours (roadmap §12.2), so a raised gate here becomes
``INFRASTRUCTURE_ERROR`` rather than a bare ``False``.

Contract:

- ``fn`` returns normally -> ``PASS``, or ``classify(value)`` decides.
- ``fn`` raises -> ``INFRASTRUCTURE_ERROR`` with a traceback tail. An error is
  **not** an honest skip and **not** a fail: the threshold was never
  evaluated.
- ``MOSAIC_GATE_FAIL_OPEN=1`` is the single documented rollback knob. Under it
  a raised gate is tolerated as ``UNKNOWN`` with ``skipped=True`` — still not
  a ``PASS``, because no evidence was produced either way.

Fail-closed is the default everywhere. This is a correctness property, not a
policy choice, so nothing seeds the rollback variable.
"""

from __future__ import annotations

import logging
import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from harness.evidence.status import EvidenceStatus

log = logging.getLogger("oh-my-soc.gate")

FAIL_OPEN_ENV = "MOSAIC_GATE_FAIL_OPEN"


def gate_fail_open_enabled() -> bool:
    """True when ``MOSAIC_GATE_FAIL_OPEN`` is set truthy.

    Read from the environment directly and never from harness config, so the
    escape hatch cannot be turned on by a config file, a preset, or an agent.
    """
    raw = (os.environ.get(FAIL_OPEN_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass
class GateResult:
    """Outcome of running one gate through :func:`gate_guard`."""

    gate: str
    status: EvidenceStatus
    skipped: bool = False
    reason: str = ""
    error: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    value: Any = None

    @property
    def passed(self) -> bool:
        """Only ``PASS`` counts. ``UNKNOWN`` is not a pass."""
        return self.status.closes_required_node

    @property
    def errored(self) -> bool:
        """True iff the wrapped gate raised (a traceback tail was recorded)."""
        return bool(self.error)


def _traceback_tail(exc: BaseException, limit: int = 2000) -> str:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return tb[-limit:]


def gate_guard(
    name: str,
    fn: Callable[..., Any],
    *args: Any,
    classify: Optional[Callable[[Any], EvidenceStatus]] = None,
    **kwargs: Any,
) -> GateResult:
    """Run ``fn(*args, **kwargs)`` fail-closed and return a :class:`GateResult`.

    ``classify`` maps the gate's return value to an :class:`EvidenceStatus`.
    Without it a normal return is ``PASS`` — appropriate only for gates that
    signal failure by raising.
    """
    try:
        value = fn(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - catching it is the whole point
        tail = _traceback_tail(exc)
        reason = f"{type(exc).__name__}: {exc}"
        if gate_fail_open_enabled():
            log.error(
                "gate %r raised, but %s is set - tolerating as UNKNOWN "
                "(this is NOT a pass): %s", name, FAIL_OPEN_ENV, reason,
            )
            return GateResult(
                gate=name, status=EvidenceStatus.UNKNOWN, skipped=True,
                reason=reason, error=tail,
            )
        log.error("gate %r raised (fail-closed, NOT a pass): %s", name, reason)
        return GateResult(
            gate=name, status=EvidenceStatus.INFRASTRUCTURE_ERROR,
            reason=reason, error=tail,
        )

    status = EvidenceStatus.PASS if classify is None else classify(value)
    return GateResult(gate=name, status=status, value=value)


def gate_error_finding(
    gate: str,
    reason: str,
    error: str = "",
    *,
    code: str = "GATE_INFRASTRUCTURE_ERROR",
    path: str = "",
) -> Dict[str, Any]:
    """Structured finding for a gate that errored.

    Shape follows roadmap §10 (``code``/``severity``/``path``/``message``/
    ``suggestions``) so it lands in the same reporting path as validation
    findings instead of disappearing into a log line.
    """
    message = (
        f"gate {gate!r} did not run to completion: {reason}. "
        "This is NOT a pass - the threshold was never evaluated."
    )
    suggestions = [
        "fix the gate environment (missing tool, unreadable report, parser bug)",
        "re-run the flow once the gate can execute",
    ]
    if error:
        suggestions.append(f"traceback tail:\n{error}")
    return {
        "code": code,
        "severity": "error",
        "path": path or f"gate.{gate}",
        "message": message,
        "suggestions": suggestions,
    }
