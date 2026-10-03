"""drc-triage skill — parse DRC/LVS reports and propose targeted fixes.

Reads DRC violation reports (Magic, KLayout, Netgen format), classifies
violations by type and severity, maps them to RTL locations when possible,
and proposes specific fixes.

Design principle: the skill does deterministic report parsing and
classification. The agent uses the structured output to decide which
fixes to apply — the skill never modifies RTL directly.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core import SkillResult, REPO_ROOT, log

# ── Violation patterns ───────────────────────────────────────────────

# Magic DRC format: "Violation: <type> (count: <n>) ... <layer>"
# KLayout format: "<rule_name>: <count> violations"
# Netgen LVS format: "Incorrect: <type> <count>"

class ViolationType:
    """Classification of a DRC/LVS violation."""
    SHORT = "short"
    OPEN = "open"
    SPACING = "spacing"
    WIDTH = "width"
    ENCLOSURE = "enclosure"
    AREA = "area"
    ANTENNA = "antenna"
    LVS_MISMATCH = "lvs_mismatch"
    PIN_ACCESS = "pin_access"
    OTHER = "other"


# Keywords → violation type mapping
VIOLATION_KEYWORDS: Dict[str, str] = {
    "short": ViolationType.SHORT,
    "shorted": ViolationType.SHORT,
    "open": ViolationType.OPEN,
    "missing": ViolationType.OPEN,
    "spacing": ViolationType.SPACING,
    "minimum spacing": ViolationType.SPACING,
    "width": ViolationType.WIDTH,
    "minimum width": ViolationType.WIDTH,
    "enclosure": ViolationType.ENCLOSURE,
    "enclosed": ViolationType.ENCLOSURE,
    "area": ViolationType.AREA,
    "minimum area": ViolationType.AREA,
    "antenna": ViolationType.ANTENNA,
    "antenna effect": ViolationType.ANTENNA,
    "lvs": ViolationType.LVS_MISMATCH,
    "mismatch": ViolationType.LVS_MISMATCH,
    "incorrect": ViolationType.LVS_MISMATCH,
    "pin access": ViolationType.PIN_ACCESS,
}


def _classify_violation(text: str) -> str:
    """Classify a violation line into a ViolationType."""
    lower = text.lower()
    for keyword, vtype in VIOLATION_KEYWORDS.items():
        if keyword in lower:
            return vtype
    return ViolationType.OTHER


# ── Report parsers ───────────────────────────────────────────────────

def _parse_magic_drc(content: str) -> List[Dict[str, Any]]:
    """Parse Magic DRC report format.

    Magic DRC output lines look like:
        Violation: minwidth (count: 3) MinWidth violation on layer li1.
    """
    violations = []
    pattern = re.compile(
        r"Violation:\s*(\S+)\s*\(count:\s*(\d+)\)\s*(.*)", re.IGNORECASE
    )
    for line in content.splitlines():
        m = pattern.search(line)
        if m:
            vtype_raw = m.group(1)
            count = int(m.group(2))
            detail = m.group(3).strip()
            violations.append({
                "type": _classify_violation(vtype_raw),
                "rule": vtype_raw,
                "count": count,
                "detail": detail,
                "raw": line.strip(),
            })
    return violations


def _parse_klayout_drc(content: str) -> List[Dict[str, Any]]:
    """Parse KLayout DRC report format.

    KLayout output lines look like:
        MinWidth: 3 violations
        Short: 1 violation (layer: Metal1)
    """
    violations = []
    pattern = re.compile(
        r"(\S+):\s*(\d+)\s+violation[s]?(.*)", re.IGNORECASE
    )
    for line in content.splitlines():
        m = pattern.search(line)
        if m:
            rule = m.group(1)
            count = int(m.group(2))
            detail = m.group(3).strip().strip("()")
            violations.append({
                "type": _classify_violation(rule),
                "rule": rule,
                "count": count,
                "detail": detail,
                "raw": line.strip(),
            })
    return violations


def _parse_netgen_lvs(content: str) -> List[Dict[str, Any]]:
    """Parse Netgen LVS report format.

    Netgen output lines look like:
        Incorrect: net VDD (1 connection)
        Incorrect: device NMOS (3 instances)
    """
    violations = []
    pattern = re.compile(
        r"Incorrect:\s+(\S+)\s+(.*)", re.IGNORECASE
    )
    for line in content.splitlines():
        m = pattern.search(line)
        if m:
            rule = m.group(1)
            detail = m.group(2).strip()
            violations.append({
                "type": ViolationType.LVS_MISMATCH,
                "rule": rule,
                "count": 1,
                "detail": detail,
                "raw": line.strip(),
            })
    return violations


def _parse_generic(content: str) -> List[Dict[str, Any]]:
    """Fallback parser: look for violation/error/warning patterns."""
    violations = []
    for line in content.splitlines():
        lower = line.lower()
        if any(kw in lower for kw in ["violation", "error", "incorrect", "fail"]):
            violations.append({
                "type": _classify_violation(line),
                "rule": "generic",
                "count": 1,
                "detail": line.strip(),
                "raw": line.strip(),
            })
    return violations


# ── Fix suggestions ──────────────────────────────────────────────────

# Maps violation types to suggested RTL/flow fixes.
FIX_SUGGESTIONS: Dict[str, List[str]] = {
    ViolationType.SHORT: [
        "Check layer routing for overlapping geometries",
        "Verify power/ground net connections",
        "Review via placement at layer transitions",
    ],
    ViolationType.OPEN: [
        "Check for unconnected nets or missing vias",
        "Verify all pins are properly connected",
        "Review power mesh continuity",
    ],
    ViolationType.SPACING: [
        "Increase spacing between adjacent geometries",
        "Check DRC rule deck for minimum spacing requirements",
        "Review metal density near violation location",
    ],
    ViolationType.WIDTH: [
        "Widen narrow wire segments",
        "Check minimum width rules for each metal layer",
        "Consider using wider power rails",
    ],
    ViolationType.ENCLOSURE: [
        "Increase via enclosure on enclosing layer",
        "Check minimum enclosure rules for each via type",
    ],
    ViolationType.AREA: [
        "Increase metal area for small floating shapes",
        "Remove unnecessary metal fragments",
    ],
    ViolationType.ANTENNA: [
        "Add antenna diodes or protection devices",
        "Break long metal runs with jumpers",
        "Check antenna rules for each layer",
    ],
    ViolationType.LVS_MISMATCH: [
        "Compare extracted netlist with schematic",
        "Check for shorted or open nets in layout",
        "Verify device parameters (W/L, connections)",
        "Review port/pin naming consistency",
    ],
    ViolationType.PIN_ACCESS: [
        "Increase pin size or metal coverage",
        "Add blocking layers around pin access points",
    ],
    ViolationType.OTHER: [
        "Review the specific DRC rule that failed",
        "Check the rule deck documentation",
    ],
}


# ── DRCTriage ────────────────────────────────────────────────────────

class DRCTriage:
    """Skill: parse DRC/LVS reports and propose targeted fixes.

    Usage:
        triage = DRCTriage()
        result = triage.analyze_file(Path("build/reports/magic_drc.rpt"))
        result = triage.analyze_text(content, format="magic")
        # result.details["violations"] has classified violations
        # result.details["fix_suggestions"] has per-type fix proposals
    """

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT

    def analyze_file(
        self, path: Path, fmt: Optional[str] = None
    ) -> SkillResult:
        """Analyze a DRC/LVS report file.

        Args:
            path: Path to the report file.
            fmt: Format hint ('magic', 'klayout', 'netgen', 'auto').

        Returns:
            SkillResult with classified violations and fix suggestions.
        """
        if not path.exists():
            return SkillResult(
                ok=False, skill="drc-triage",
                summary=f"Report file not found: {path}",
                errors=[str(path)],
            )

        content = path.read_text(errors="replace")
        return self.analyze_text(content, fmt=fmt, source=str(path))

    def analyze_text(
        self,
        content: str,
        fmt: Optional[str] = None,
        source: str = "<inline>",
    ) -> SkillResult:
        """Analyze DRC/LVS report text.

        Args:
            content: Report text content.
            fmt: Format hint ('magic', 'klayout', 'netgen', 'auto').
            source: Source description for the report.

        Returns:
            SkillResult with classified violations and fix suggestions.
        """
        # Auto-detect format
        if fmt is None or fmt == "auto":
            fmt = self._detect_format(content)

        # Parse
        if fmt == "magic":
            violations = _parse_magic_drc(content)
        elif fmt == "klayout":
            violations = _parse_klayout_drc(content)
        elif fmt == "netgen":
            violations = _parse_netgen_lvs(content)
        else:
            violations = _parse_generic(content)

        # Classify by type
        by_type: Dict[str, int] = {}
        for v in violations:
            t = v["type"]
            by_type[t] = by_type.get(t, 0) + v["count"]

        total_violations = sum(v["count"] for v in violations)

        # Generate fix suggestions
        fix_suggestions: Dict[str, List[str]] = {}
        for vtype in by_type:
            if vtype in FIX_SUGGESTIONS:
                fix_suggestions[vtype] = FIX_SUGGESTIONS[vtype]

        # Severity assessment
        severity = "clean"
        if total_violations == 0:
            severity = "clean"
        elif ViolationType.SHORT in by_type or ViolationType.LVS_MISMATCH in by_type:
            severity = "critical"
        elif ViolationType.OPEN in by_type:
            severity = "high"
        elif total_violations > 10:
            severity = "medium"
        else:
            severity = "low"

        ok = total_violations == 0

        return SkillResult(
            ok=ok, skill="drc-triage",
            summary=(
                f"{source}: {total_violations} violations "
                f"({len(violations)} rules, severity={severity})"
            ) if total_violations > 0 else f"{source}: DRC/LVS clean",
            details={
                "source": source,
                "format": fmt,
                "total_violations": total_violations,
                "unique_rules": len(violations),
                "severity": severity,
                "by_type": by_type,
                "violations": violations[:50],  # cap for output size
                "fix_suggestions": fix_suggestions,
            },
        )

    def triage_directory(self, report_dir: Path) -> SkillResult:
        """Scan a directory for DRC/LVS reports and triage all of them."""
        if not report_dir.is_dir():
            return SkillResult(
                ok=False, skill="drc-triage",
                summary=f"Not a directory: {report_dir}",
            )

        report_patterns = ["*.rpt", "*.txt", "*drc*", "*lvs*", "*.drc", "*.lvs"]
        found_files: List[Path] = []
        for pattern in report_patterns:
            found_files.extend(report_dir.glob(pattern))

        if not found_files:
            return SkillResult(
                ok=False, skill="drc-triage",
                summary=f"No report files found in {report_dir}",
            )

        results = []
        total_violations = 0
        for f in sorted(found_files):
            r = self.analyze_file(f)
            results.append(r)
            if r.ok:
                total_violations += r.details.get("total_violations", 0)

        return SkillResult(
            ok=all(r.ok for r in results),
            skill="drc-triage",
            summary=f"Triaged {len(results)} reports: {total_violations} total violations",
            details={"reports": [r.to_json() for r in results]},
        )

    @staticmethod
    def _detect_format(content: str) -> str:
        """Auto-detect the report format from content."""
        lower = content.lower()
        if "violation:" in lower and "count:" in lower:
            return "magic"
        if "violations" in lower and re.search(r"\d+\s+violation", lower):
            return "klayout"
        if "incorrect:" in lower:
            return "netgen"
        return "generic"
