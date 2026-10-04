"""Truthful parsing of physical signoff evidence.

Before this module, ``flow-runner`` decided the fate of ``harden-classic`` and
``harden-chip`` from the ``make`` exit code alone: those flows were routed into
the cocotb parser, which searches for ``TESTS=/PASS=/FAIL=`` and
``EXIT SUCCESS`` — markers LibreLane never emits — so no gate metric was ever
produced and ``ok`` collapsed to ``returncode == 0``. A run that completed with
DRC violations reported PASS.

The requirement: *"An exit-zero flow with negative
required slack, a non-waived DRC violation, or an LVS mismatch must be FAIL.
… An unexecuted flow is UNKNOWN; an executed flow with a missing or
unparseable mandatory report is INFRASTRUCTURE_ERROR."*

One rule here goes beyond that requirement, adapted from CoreSmith
(``orchestrator/langgraph/backend_helpers.py``, MIT), which hit it in
production: a report can parse perfectly and still yield **zero** because the
tool printed a blank summary. So a zero count from a primary source is not
trusted on its own —

    :func:`corroborated_count` takes the maximum of the primary count and an
    independent recount of the report body.

A blank primary count therefore cannot mask real violations, while a genuinely
clean run still reports zero from both sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from harness.evidence.librelane import (
    ANTENNA_METRIC_KEYS,
    AREA_KEYS,
    DRC_METRIC_KEYS,
    LVS_METRIC_KEYS,
    TNS_KEYS,
    WORST_SLACK_KEYS,
    LibreLaneRun,
    adverse_metrics,
    first_metric,
    load_run,
    sum_metrics,
)
from harness.evidence.status import EvidenceStatus, worst
from harness.evidence.waivers import Waiver, apply_waivers


# The report parsers live in the drc-triage skill, and importing them at module
# scope is a cycle: harness.evidence.signoff -> harness.skills.drc_triage ->
# harness.skills/__init__ -> flow_runner -> harness.evidence.signoff. It
# resolved only when `harness.skills` happened to be imported first, so
# `import harness.evidence.signoff` on its own raised ImportError while the test
# suite never noticed (pytest always reaches skills first). Deferring the import
# to call time keeps one source of truth for the parsers without the cycle.
def _parse_magic_drc(text: str):
    from harness.skills.drc_triage import _parse_magic_drc as impl
    return impl(text)


def _parse_klayout_drc(text: str):
    from harness.skills.drc_triage import _parse_klayout_drc as impl
    return impl(text)


def _parse_netgen_lvs(text: str):
    from harness.skills.drc_triage import _parse_netgen_lvs as impl
    return impl(text)

# Primary summary lines emitted by the flows we drive.
_DRC_COUNT_PATTERNS = (
    re.compile(r"(?im)^\s*DRC\s+count\s*[:=]\s*(\d+)\s*$"),
    re.compile(r"(?im)^\s*DRC\s+violations\s*[:=]\s*(\d+)\s*$"),
    re.compile(r"(?im)total\s+(?:number\s+of\s+)?DRC\s+violations?\s*[:=]\s*(\d+)"),
)
_LVS_CLEAN_PATTERNS = (
    re.compile(r"(?im)^\s*Circuits?\s+match\s+uniquely\.?\s*$"),
    re.compile(r"(?im)\bLVS\s+(?:result\s*[:=]\s*)?match(?:es|ed)?\b"),
)
_LVS_FAIL_PATTERNS = (
    re.compile(r"(?im)\bNetlists?\s+do\s+not\s+match\b"),
    re.compile(r"(?im)\bfailed\s+pin\s+matching\b"),
    re.compile(r"(?im)\bLVS\s+(?:result\s*[:=]\s*)?mismatch\b"),
)
_WNS_PATTERN = re.compile(
    r"(?im)^\s*(?:wns|worst\s+negative\s+slack)\s*[:=]?\s*(-?\d+(?:\.\d+)?)"
)
_TNS_PATTERN = re.compile(
    r"(?im)^\s*(?:tns|total\s+negative\s+slack)\s*[:=]?\s*(-?\d+(?:\.\d+)?)"
)


@dataclass
class SignoffEvidence:
    """Parsed physical signoff evidence for one hardening run."""

    status: EvidenceStatus = EvidenceStatus.UNKNOWN
    drc_violations: Optional[int] = None
    lvs_match: Optional[bool] = None
    antenna_violations: Optional[int] = None
    wns_ns: Optional[float] = None
    tns_ns: Optional[float] = None
    area: Dict[str, float] = field(default_factory=dict)
    other_violations: List[str] = field(default_factory=list)
    checks_skipped: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)
    run_dir: Optional[str] = None
    design_name: Optional[str] = None
    # Findings accepted under a recorded, bounded, dated waiver. Always
    # reported: a waived run is a PASS that says so, never a silent PASS.
    waived: List[Dict[str, Any]] = field(default_factory=list)

    def as_metrics(self) -> Dict[str, Any]:
        """Flatten into the ``RunReport.metrics`` dict ``flow-runner`` uses."""
        metrics: Dict[str, Any] = {"signoff_status": self.status.value}
        if self.drc_violations is not None:
            metrics["drc_violations"] = self.drc_violations
        if self.lvs_match is not None:
            metrics["lvs_match"] = self.lvs_match
        if self.antenna_violations is not None:
            metrics["antenna_violations"] = self.antenna_violations
        if self.wns_ns is not None:
            metrics["wns_ns"] = self.wns_ns
        if self.tns_ns is not None:
            metrics["tns_ns"] = self.tns_ns
        if self.area:
            metrics["area"] = self.area
        if self.other_violations:
            metrics["other_violations"] = self.other_violations
        if self.checks_skipped:
            metrics["signoff_checks_skipped"] = sorted(self.checks_skipped)
        if self.reasons:
            metrics["signoff_reasons"] = self.reasons
        if self.sources:
            metrics["signoff_sources"] = self.sources
        if self.run_dir:
            metrics["run_dir"] = self.run_dir
        if self.design_name:
            metrics["design_name"] = self.design_name
        if self.waived:
            metrics["signoff_waived"] = self.waived
        return metrics


def corroborated_count(primary: Optional[int], report_text: str, fmt: str) -> int:
    """Return a violation count that a blank primary summary cannot suppress.

    ``primary`` is the count scraped from the tool's own summary line, which
    may be ``None`` (absent) or ``0`` (possibly blank rather than clean). The
    report body is re-parsed independently and the larger count wins.
    """
    if fmt == "magic":
        rows = _parse_magic_drc(report_text)
    elif fmt == "klayout":
        rows = _parse_klayout_drc(report_text)
    else:
        rows = []
    recount = sum(int(r.get("count", 1)) for r in rows)
    if primary is None:
        return recount
    return max(primary, recount)


def _first_int(patterns, text: str) -> Optional[int]:
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    return None


def _first_float(pattern, text: str) -> Optional[float]:
    m = pattern.search(text)
    return float(m.group(1)) if m else None


def _detect_format(text: str) -> str:
    if re.search(r"(?i)\bmagic\b|Violation:\s*\S+\s*\(count:", text):
        return "magic"
    if re.search(r"(?i)klayout|\d+\s+violations?\b", text):
        return "klayout"
    return "generic"


def parse_signoff(
    output: str,
    report_dir: Optional[Path] = None,
    *,
    runs_dir: Optional[Path] = None,
    require_drc: bool = True,
    require_lvs: bool = True,
    require_timing: bool = False,
    require_antenna: bool = False,
    waivers: Optional[Sequence[Waiver]] = None,
) -> SignoffEvidence:
    """Derive signoff evidence for one hardening run.

    When ``runs_dir`` points at a LibreLane ``runs/`` tree and the newest run
    carries a metrics file, that structured evidence is authoritative and the
    flow's console output is not consulted for verdicts. Otherwise we fall
    back to scraping the output and any ``report_dir``, which is what
    non-LibreLane tools and unit fixtures exercise.

    ``require_*`` describes what this flow was *supposed* to prove. A required
    check with no parseable evidence yields ``INFRASTRUCTURE_ERROR``: the run
    happened, but the threshold was never evaluated. A check the flow
    structurally does not perform (``harden-nodrc`` skips ``Magic.DRC``,
    ``KLayout.DRC`` and ``KLayout.Antenna``) must be passed as
    ``require_drc=False`` so it is reported as skipped rather than clean.
    """
    if runs_dir is not None:
        run = load_run(runs_dir)
        if run is not None and run.has_metrics:
            return _from_librelane_run(
                run,
                require_drc=require_drc,
                require_lvs=require_lvs,
                require_timing=require_timing,
                require_antenna=require_antenna,
                waivers=waivers,
            )
    return _from_text(
        output,
        report_dir,
        require_drc=require_drc,
        require_lvs=require_lvs,
        require_timing=require_timing,
    )


def _from_text(
    output: str,
    report_dir: Optional[Path] = None,
    *,
    require_drc: bool = True,
    require_lvs: bool = True,
    require_timing: bool = False,
) -> SignoffEvidence:
    """Scrape signoff evidence from console output and loose report files."""
    evidence = SignoffEvidence()
    text = output or ""

    report_text = ""
    if report_dir is not None and report_dir.is_dir():
        collected = []
        for pattern in ("*.rpt", "*.log", "*drc*", "*lvs*"):
            for path in sorted(report_dir.glob(pattern)):
                if not path.is_file():
                    continue
                try:
                    collected.append(path.read_text(errors="replace"))
                    evidence.sources[path.name] = str(path)
                except OSError:
                    continue
        report_text = "\n".join(collected)
    haystack = text + "\n" + report_text

    statuses: List[EvidenceStatus] = []

    # ── DRC ──────────────────────────────────────────────────────────
    if require_drc:
        primary = _first_int(_DRC_COUNT_PATTERNS, haystack)
        fmt = _detect_format(haystack)
        count = corroborated_count(primary, haystack, fmt)
        if primary is None and count == 0:
            # No summary line and no parseable violations: we cannot tell a
            # clean run from a run whose report never appeared.
            evidence.reasons.append(
                "no DRC count and no parseable DRC report - DRC was not evaluated"
            )
            statuses.append(EvidenceStatus.INFRASTRUCTURE_ERROR)
        else:
            evidence.drc_violations = count
            if primary is not None and count > primary:
                evidence.reasons.append(
                    f"DRC summary reported {primary} but the report body holds "
                    f"{count}; using the larger count"
                )
            if count > 0:
                evidence.reasons.append(f"{count} DRC violation(s)")
                statuses.append(EvidenceStatus.FAIL)
            else:
                statuses.append(EvidenceStatus.PASS)
    else:
        evidence.checks_skipped.append("drc")

    # ── LVS ──────────────────────────────────────────────────────────
    if require_lvs:
        mismatched = any(p.search(haystack) for p in _LVS_FAIL_PATTERNS)
        matched = any(p.search(haystack) for p in _LVS_CLEAN_PATTERNS)
        lvs_rows = _parse_netgen_lvs(haystack)
        if mismatched or lvs_rows:
            evidence.lvs_match = False
            evidence.reasons.append(
                f"LVS mismatch ({len(lvs_rows)} incorrect item(s))"
                if lvs_rows else "LVS reported a mismatch"
            )
            statuses.append(EvidenceStatus.FAIL)
        elif matched:
            evidence.lvs_match = True
            statuses.append(EvidenceStatus.PASS)
        else:
            evidence.reasons.append(
                "no LVS verdict found - LVS was not evaluated"
            )
            statuses.append(EvidenceStatus.INFRASTRUCTURE_ERROR)
    else:
        evidence.checks_skipped.append("lvs")

    # ── Timing ───────────────────────────────────────────────────────
    wns = _first_float(_WNS_PATTERN, haystack)
    tns = _first_float(_TNS_PATTERN, haystack)
    evidence.wns_ns = wns
    evidence.tns_ns = tns
    if require_timing:
        if wns is None:
            evidence.reasons.append(
                "no WNS reported - timing was not evaluated"
            )
            statuses.append(EvidenceStatus.INFRASTRUCTURE_ERROR)
        elif wns < 0:
            evidence.reasons.append(f"negative worst slack: WNS={wns} ns")
            statuses.append(EvidenceStatus.FAIL)
        else:
            statuses.append(EvidenceStatus.PASS)
    else:
        evidence.checks_skipped.append("timing")

    if not statuses:
        # Every check was declared not-applicable for this flow. That is an
        # honest report of a flow that structurally cannot sign anything off.
        evidence.status = EvidenceStatus.NOT_APPLICABLE
        evidence.reasons.append(
            "flow performs no signoff checks - cannot establish qualification"
        )
    else:
        evidence.status = worst(*statuses)
    return evidence


def _read(path: Optional[Path]) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _from_librelane_run(
    run: LibreLaneRun,
    *,
    require_drc: bool,
    require_lvs: bool,
    require_timing: bool,
    require_antenna: bool,
    waivers: Optional[Sequence[Waiver]] = None,
) -> SignoffEvidence:
    """Derive a verdict from a LibreLane run's metrics file and step reports."""
    evidence = SignoffEvidence(
        run_dir=str(run.run_dir), design_name=run.design_name
    )
    if run.metrics_source:
        evidence.sources["metrics"] = run.metrics_source
    for name, path in run.reports.items():
        evidence.sources[name] = str(path)

    metrics = run.metrics
    statuses: List[EvidenceStatus] = []
    accounted: set = set()

    # ── DRC ──────────────────────────────────────────────────────────
    if require_drc:
        matched, total = sum_metrics(metrics, DRC_METRIC_KEYS)
        accounted.update(matched)
        magic_text = _read(run.reports.get("magic_drc"))
        klayout_text = _read(run.reports.get("klayout_drc"))
        primary = int(total) if total is not None else None
        count = corroborated_count(primary, magic_text, "magic")
        count = max(count, corroborated_count(None, klayout_text, "klayout"))
        if primary is None and not magic_text and not klayout_text:
            evidence.reasons.append(
                "no DRC metric and no DRC report in the run - DRC was not evaluated"
            )
            statuses.append(EvidenceStatus.INFRASTRUCTURE_ERROR)
        else:
            evidence.drc_violations = count
            if primary is not None and count > primary:
                evidence.reasons.append(
                    f"DRC metric reported {primary} but the report body holds "
                    f"{count}; using the larger count"
                )
            if count > 0:
                evidence.reasons.append(f"{count} DRC violation(s)")
                statuses.append(EvidenceStatus.FAIL)
            else:
                statuses.append(EvidenceStatus.PASS)
    else:
        evidence.checks_skipped.append("drc")

    # ── LVS ──────────────────────────────────────────────────────────
    if require_lvs:
        matched, total = sum_metrics(metrics, LVS_METRIC_KEYS)
        accounted.update(matched)
        lvs_text = _read(run.reports.get("netgen_lvs"))
        report_rows = _parse_netgen_lvs(lvs_text) if lvs_text else []
        if total is None and not lvs_text:
            evidence.reasons.append(
                "no LVS metric and no LVS report in the run - LVS was not evaluated"
            )
            statuses.append(EvidenceStatus.INFRASTRUCTURE_ERROR)
        else:
            errors = int(total or 0)
            if report_rows and len(report_rows) > errors:
                evidence.reasons.append(
                    f"LVS metric reported {errors} but the report holds "
                    f"{len(report_rows)}; using the larger count"
                )
                errors = len(report_rows)
            evidence.lvs_match = errors == 0
            if errors:
                evidence.reasons.append(f"LVS mismatch ({errors} item(s))")
                statuses.append(EvidenceStatus.FAIL)
            else:
                statuses.append(EvidenceStatus.PASS)
    else:
        evidence.checks_skipped.append("lvs")

    # ── Antenna ──────────────────────────────────────────────────────
    matched, antenna = sum_metrics(metrics, ANTENNA_METRIC_KEYS)
    accounted.update(matched)
    if antenna is not None:
        evidence.antenna_violations = int(antenna)
    if require_antenna:
        if antenna is None:
            evidence.reasons.append(
                "no antenna metric - antenna was not evaluated"
            )
            statuses.append(EvidenceStatus.INFRASTRUCTURE_ERROR)
        elif antenna > 0:
            evidence.reasons.append(f"{int(antenna)} antenna violation(s)")
            statuses.append(EvidenceStatus.FAIL)
        else:
            statuses.append(EvidenceStatus.PASS)
    else:
        evidence.checks_skipped.append("antenna")

    # ── Timing ───────────────────────────────────────────────────────
    ws_key, ws = first_metric(metrics, WORST_SLACK_KEYS)
    if ws_key:
        accounted.add(ws_key)
        evidence.wns_ns = ws
    tns_key, tns = first_metric(metrics, TNS_KEYS)
    if tns_key:
        accounted.add(tns_key)
        evidence.tns_ns = tns
    if require_timing:
        if ws is None:
            evidence.reasons.append("no worst-slack metric - timing was not evaluated")
            statuses.append(EvidenceStatus.INFRASTRUCTURE_ERROR)
        elif ws < 0:
            evidence.reasons.append(f"negative worst slack: {ws_key}={ws}")
            statuses.append(EvidenceStatus.FAIL)
        else:
            statuses.append(EvidenceStatus.PASS)
    else:
        evidence.checks_skipped.append("timing")

    # ── Area (recorded, never a verdict without a declared budget) ────
    for key in AREA_KEYS:
        if key in metrics:
            value = metrics[key]
            try:
                evidence.area[key] = float(value)
            except (TypeError, ValueError):
                continue

    # ── Generic sweep: unknown violation counters still fail ─────────
    # LibreLane's metric vocabulary drifts between versions. Any adverse
    # metric we did not explicitly account for is surfaced and fails the run,
    # so an upgrade cannot silently blind the gate.
    findings = [(key, value) for key, value in adverse_metrics(metrics)
                if key not in accounted]

    # Recorded waivers are applied LAST, to findings the sweep already made.
    # A waiver can only ever reduce a FAIL it can name; it cannot stop a metric
    # being measured, cannot suppress DRC/LVS/timing/antenna verdicts above,
    # and cannot apply to a design other than the one this run built.
    from harness.evidence.librelane import disconnected_pins
    findings, evidence.waived, waiver_notes = apply_waivers(
        findings, waivers or (), design=evidence.design_name,
        identities={"design__disconnected_pin__count": disconnected_pins(run.run_dir)},
        metrics=metrics,
    )
    evidence.reasons.extend(waiver_notes)

    for key, value in findings:
        evidence.other_violations.append(f"{key}={value:g}")
        statuses.append(EvidenceStatus.FAIL)
    if evidence.other_violations:
        evidence.reasons.append(
            "unaccounted adverse metrics: " + ", ".join(evidence.other_violations)
        )
    if evidence.waived:
        evidence.reasons.append(
            "accepted under recorded waiver: " + ", ".join(
                f"{record['metric']}={record['observed']:g} "
                f"(ceiling {record['accepted_max']:g}, "
                f"review by {record['review_by']})"
                for record in evidence.waived
            )
        )

    if not statuses:
        evidence.status = EvidenceStatus.NOT_APPLICABLE
        evidence.reasons.append(
            "flow performs no signoff checks - cannot establish qualification"
        )
    else:
        evidence.status = worst(*statuses)
    return evidence
