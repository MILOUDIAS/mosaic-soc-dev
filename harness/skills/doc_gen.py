"""doc-gen skill — generate run reports and memory-map documentation.

Produces structured, reproducible documentation from:
  - mosaic.yaml configs → config summary docs
  - EDA run artifacts → run reports with metrics
  - RTL package files → memory-map reference docs
  - Dashboard data → project status summaries

Design principle: deterministic generation from structured data. No LLM
hallucination — the skill reads actual artifacts and produces factual docs.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core import SkillResult, REPO_ROOT, load_yaml, log

# ── Memory map definitions ───────────────────────────────────────────

# From core_v_mini_mcu_pkg.sv.tpl — standard x-heep memory map
MEMORY_MAP = {
    "RAM": {"start": "0x00000000", "size": "Variable (1-16 banks x 1-32 KB)"},
    "DEBUG": {"start": "0x10000000", "size": "1 MB"},
    "AO_PERIPHERAL": {"start": "0x20000000", "size": "1 MB"},
    "PERIPHERAL": {"start": "0x30000000", "size": "1 MB"},
    "FLASH_MEM": {"start": "0x40000000", "size": "16 MB"},
    "EXT_SLAVES": {"start": "0xF0000000", "size": "16 MB"},
}

# TDU register map (from tdu_pkg.sv)
TDU_REGISTERS = {
    "CORE_STATUS": {"offset": "0x00", "access": "RO", "description": "Per-core running/sleeping status"},
    "SCHED_MODE": {"offset": "0x04", "access": "RW", "description": "Scheduling mode (static/dynamic/power-aware)"},
    "WAKE_MASK": {"offset": "0x08", "access": "RW", "description": "Auto-wake bitmask on task push"},
    "WAKE_REQ": {"offset": "0x0C", "access": "W1S", "description": "Explicit wake pulse (write-1-to-set)"},
    "TASK_PUSH": {"offset": "0x10", "access": "WO", "description": "Enqueue task descriptor into FIFO"},
    "TASK_POP": {"offset": "0x14", "access": "RO", "description": "Dequeue task descriptor from FIFO"},
    "TASK_STATUS": {"offset": "0x18", "access": "RO", "description": "FIFO count, full, empty flags"},
    "ACTIVE_HART_CYCLES": {"offset": "0x1C", "access": "RO/RC", "description": "Active-hart-cycles accumulator: sum over time of running harts. Workload proxy, NOT energy — every hart is weighted equally. Saturates at 2^32-1; write to clear"},
    "CPI_EST_BASE": {"offset": "0x20", "access": "RW", "description": "Per-core CPI estimate array"},
}

# Peripheral base addresses (from ao_peripheral_subsystem.sv.tpl)
PERIPHERAL_MAP = {
    "soc_ctrl": "0x20000000",
    "boot_rom": "0x20010000",
    "spi_flash": "0x20020000",
    "dma": "0x20030000",
    "power_manager": "0x20040000",
    "timer": "0x20050000",
    "gpio": "0x20060000",
    "tdu": "0x200A0000",
}


class DocGen:
    """Skill: generate documentation from MOSAIC-SoC artifacts.

    Usage:
        docgen = DocGen()
        result = docgen.config_summary(Path("mosaic.yaml"))
        result = docgen.memory_map()
        result = docgen.run_report(report_data)
    """

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT

    def config_summary(self, config_path: Path) -> SkillResult:
        """Generate a summary document from a mosaic.yaml config."""
        try:
            cfg = load_yaml(config_path)
        except Exception as e:
            return SkillResult(
                ok=False, skill="doc-gen",
                summary=f"Failed to load config: {e}",
                errors=[str(e)],
            )

        soc = cfg.get("soc", {})
        name = soc.get("name", "unknown")
        cores = soc.get("cores", [])
        mem = soc.get("memory", {})
        sched = soc.get("scheduler", {})
        peripherals = soc.get("peripherals", [])

        total_cores = sum(c.get("count", 1) for c in cores)
        core_summary = []
        for c in cores:
            core_summary.append({
                "ip": c.get("ip"),
                "count": c.get("count", 1),
                "role": c.get("role"),
                "isa": c.get("isa", "rv32i"),
            })

        lines = [
            f"# Config Summary: {name}",
            "",
            f"- **PDK:** {soc.get('pdk', 'gf180mcu')}",
            f"- **Implementation target:** {soc.get('target', 'rtl')}",
            f"- **Total cores:** {total_cores}",
            f"- **SRAM:** {mem.get('sram_kb', 32)} KB",
            f"- **Boot ROM:** {mem.get('boot_rom_kb', 2)} KB",
            f"- **Bus:** {soc.get('bus', 'obi')}",
            f"- **TDU:** {'enabled' if sched.get('tdu') else 'disabled'}",
            f"- **Scheduling mode:** {sched.get('mode', 'static')}",
            f"- **Peripherals:** {', '.join(peripherals) if peripherals else 'none'}",
            "",
            "## Core Topology",
            "",
            "| IP | Count | Role | ISA |",
            "|-----|-------|------|-----|",
        ]
        for c in core_summary:
            lines.append(f"| {c['ip']} | {c['count']} | {c['role']} | {c['isa']} |")

        lines.extend(["", "## Memory Map", "", "| Region | Start | Size |", "|--------|-------|------|"])
        for region, info in MEMORY_MAP.items():
            lines.append(f"| {region} | {info['start']} | {info['size']} |")

        return SkillResult(
            ok=True, skill="doc-gen",
            summary=f"Generated config summary for '{name}' ({total_cores} cores)",
            details={"markdown": "\n".join(lines), "config": soc},
        )

    def memory_map(self) -> SkillResult:
        """Generate a memory-map reference document."""
        lines = [
            "# MOSAIC-SoC Memory Map",
            "",
            "## System Address Space",
            "",
            "| Region | Start Address | Size | Description |",
            "|--------|--------------|------|-------------|",
        ]
        for region, info in MEMORY_MAP.items():
            lines.append(f"| {region} | {info['start']} | {info['size']} | |")

        lines.extend([
            "",
            "## AO Peripheral Subsystem",
            "",
            "| Peripheral | Base Address | Description |",
            "|-----------|-------------|-------------|",
        ])
        for name, addr in PERIPHERAL_MAP.items():
            lines.append(f"| {name} | {addr} | |")

        lines.extend([
            "",
            "## TDU Register Map",
            "",
            "Base: `0x200A0000`",
            "",
            "| Register | Offset | Access | Description |",
            "|----------|--------|--------|-------------|",
        ])
        for name, info in TDU_REGISTERS.items():
            lines.append(
                f"| {name} | {info['offset']} | {info['access']} | {info['description']} |"
            )

        return SkillResult(
            ok=True, skill="doc-gen",
            summary="Generated memory-map reference document",
            details={"markdown": "\n".join(lines)},
        )

    def run_report(
        self,
        flow_name: str,
        exit_code: int,
        elapsed_s: float,
        metrics: Optional[Dict[str, Any]] = None,
        warnings: Optional[List[str]] = None,
        errors: Optional[List[str]] = None,
        config: Optional[str] = None,
    ) -> SkillResult:
        """Generate a structured run report from EDA artifacts."""
        timestamp = datetime.now(timezone.utc).isoformat()
        status = "PASS" if exit_code == 0 else "FAIL"

        lines = [
            f"# Run Report: {flow_name}",
            "",
            f"- **Timestamp:** {timestamp}",
            f"- **Status:** {status}",
            f"- **Exit code:** {exit_code}",
            f"- **Elapsed:** {elapsed_s:.1f}s",
        ]
        if config:
            lines.append(f"- **Config:** {config}")

        if metrics:
            lines.extend(["", "## Metrics", ""])
            for k, v in metrics.items():
                lines.append(f"- **{k}:** {v}")

        if warnings:
            lines.extend(["", f"## Warnings ({len(warnings)})", ""])
            for w in warnings[:20]:
                lines.append(f"- {w}")

        if errors:
            lines.extend(["", f"## Errors ({len(errors)})", ""])
            for e in errors[:20]:
                lines.append(f"- {e}")

        return SkillResult(
            ok=exit_code == 0, skill="doc-gen",
            summary=f"Run report: {flow_name} {status} ({elapsed_s:.1f}s)",
            details={
                "markdown": "\n".join(lines),
                "timestamp": timestamp,
                "flow": flow_name,
                "status": status,
            },
        )

    def dashboard_summary(self, dashboard_path: Optional[Path] = None) -> SkillResult:
        """Parse DASHBOARD.md and extract project metrics."""
        if dashboard_path is None:
            dashboard_path = self.repo_root / "DASHBOARD.md"

        if not dashboard_path.exists():
            return SkillResult(
                ok=False, skill="doc-gen",
                summary=f"Dashboard not found: {dashboard_path}",
            )

        content = dashboard_path.read_text(errors="replace")

        # Extract key metrics
        metrics: Dict[str, Any] = {}
        for line in content.splitlines():
            if "|" in line and "Metric" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) == 2:
                    metrics[parts[0]] = parts[1]

        # Count done items
        done_count = content.count("| DONE |") + content.count("✅")
        in_progress = content.count("| IN PROG")
        not_started = content.count("| NOT STARTED")

        return SkillResult(
            ok=True, skill="doc-gen",
            summary=f"Dashboard parsed: {done_count} done, {in_progress} in progress",
            details={
                "metrics": metrics,
                "done_count": done_count,
                "in_progress": in_progress,
                "not_started": not_started,
            },
        )
