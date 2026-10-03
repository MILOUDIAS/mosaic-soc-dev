"""flow-runner skill — invoke EDA flows and summarize results.

Wraps make targets (mosaic-gen, verilator-build, librelane harden, etc.)
with structured logging, timing, and result parsing. The agent calls this
skill instead of shelling out directly — giving it structured reports
instead of raw stdout.

Design principle: deterministic execution + structured output. The agent
decides *what* to run; the skill ensures it runs correctly and captures
everything.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core import (
    SkillResult, RunReport, REPO_ROOT, build_subprocess_env, run_cmd,
    load_yaml, log,
)
from harness.evidence.gate_guard import gate_error_finding, gate_guard
from harness.evidence.signoff import parse_signoff
from harness.evidence.waivers import load_waivers
from harness.evidence.status import EvidenceStatus

# ── Flow definitions ─────────────────────────────────────────────────

FLOWS: Dict[str, Dict[str, Any]] = {
    "mosaic-gen": {
        "cmd": ["make", "mosaic-gen"],
        "description": "Generate multi-core SoC RTL from mosaic.yaml",
        "effect": "write",
        "cost": "minutes",
        "scopes": ["rtl", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 300,
        "outputs": ["hw/core-v-mini-mcu/core_v_mini_mcu.sv"],
    },
    "mosaic-gen-config": {
        "cmd_prefix": ["make", "mosaic-gen", "MOSAIC_CFG="],
        "description": "Generate SoC RTL from a specific config",
        "effect": "write",
        "cost": "minutes",
        "scopes": ["rtl", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 300,
    },
    "verilator-lint": {
        "cmd": ["make", "verilator-build"],
        "description": "Build Verilator model (includes lint)",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["rtl", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 600,
    },
    "verilator-run": {
        "cmd": ["make", "verilator-run"],
        "description": "Run Verilator simulation",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 300,
    },
    "tb-multicore": {
        "cmd": ["bash", "tb/mosaic/run.sh"],
        "description": "Multi-core SCI wake-loop test",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 300,
    },
    "tb-tdu": {
        "cmd": ["bash", "tb/tdu/soc/cocotb/run.sh"],
        "description": "TDU SoC-level cocotb test",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 300,
    },
    "tb-idma": {
        "cmd": ["bash", "tb/idma/cocotb/run.sh"],
        "description": "iDMA cocotb test",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 300,
    },
    "harden-classic": {
        "cmd": ["make", "classic"],
        "cwd": "flow/librelane",
        "description": "LibreLane classic flow (SoC core only)",
        "effect": "execute",
        "cost": "hours",
        "scopes": ["physical"],
        "approval": True,
        "evidence": False,
        "timeout": 7200,
        # A hardening run is gated on parsed signoff evidence, never on the
        # make exit code. Evidence comes from the LibreLane run tree
        # (runs/<TAG>/final/metrics.csv plus per-step reports), not stdout.
        # See harness/evidence/librelane.py and roadmap §12.5.
        #
        # timing and antenna were False, which made roadmap M0's first exit
        # criterion ("an exit-zero fixture with negative required WNS fails")
        # implemented, tested, and switched off. Both are now required: missing
        # timing or antenna evidence is INFRASTRUCTURE_ERROR, not a pass.
        "signoff": {"drc": True, "lvs": True, "timing": True, "antenna": True},
        "runs_dir": "flow/librelane/runs",
        "waivers": "flow/librelane/signoff_waivers.yaml",
        "report_dir": "flow/librelane/final_classic",
        "required_evidence": ["signoff_status"],
    },
    "harden-chip": {
        "cmd": ["make", "harden", "SLOT=mosaic"],
        "cwd": "flow/librelane",
        "description": "LibreLane chip flow (full chip + pad ring)",
        "effect": "execute",
        "cost": "hours",
        "scopes": ["physical"],
        "approval": True,
        "evidence": False,
        "timeout": 14400,
        "signoff": {"drc": True, "lvs": True, "timing": True, "antenna": True},
        "runs_dir": "flow/librelane/runs",
        "waivers": "flow/librelane/signoff_waivers.yaml",
        "report_dir": "flow/librelane/final",
        "required_evidence": ["signoff_status"],
    },
    "firmware-build": {
        "cmd": ["make"],
        "cwd": "sw/firmware",
        "description": "Build firmware (TITAN + workers)",
        "effect": "execute",
        "cost": "seconds",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 60,
    },
    "firmware-demo": {
        "cmd": ["make", "demo"],
        "cwd": "sw/firmware",
        "description": "Build scheduling demo firmware",
        "effect": "execute",
        "cost": "seconds",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 60,
    },
    # ── full-SoC functional sims (config selected via MOSAIC_CFG env) ──
    "tb-soc-wake": {
        "cmd": ["bash", "tb/mosaic_soc/run.sh"],
        "env_config_key": "MOSAIC_CFG",
        "require_exit_success": True,  # run.sh exits 0 even on sim failure
        "description": "Full-SoC TDU wake-and-run demo (EXIT SUCCESS gate)",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 3600,
    },
    "tb-soc-generic": {
        "cmd": ["bash", "tb/mosaic_soc/run_generic.sh"],
        "env_config_key": "MOSAIC_CFG",
        "require_exit_success": True,
        "description": "Topology-generic all-hart liveness demo (EXIT SUCCESS gate)",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 3600,
    },
    "tb-soc-titan": {
        "cmd": ["bash", "tb/mosaic_soc/run_titan.sh"],
        "env_config_key": "MOSAIC_CFG",
        "require_exit_success": True,
        "description": "All-TITAN SMP demo (EXIT SUCCESS gate)",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 3600,
    },
    "tb-soc-fw": {
        "cmd": ["bash", "tb/mosaic_soc/run_fw.sh"],
        "env_config_key": "MOSAIC_CFG",
        "require_exit_success": True,
        "description": "Production C firmware on the full SoC",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 3600,
    },
    # ── unit / fabric TBs ──
    "tb-tl-obi": {
        "cmd": ["bash", "tb/tl_obi/run.sh"],
        "description": "TileLink->OBI bridge unit TB (rocket/boom SCI)",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 600,
    },
    "tb-log-xbar": {
        "cmd": ["bash", "tb/log_xbar/run.sh"],
        "description": "Logarithmic-interconnect fabric unit TB",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 600,
    },
    "tb-floonoc": {
        "cmd": ["bash", "tb/floonoc/cocotb/run.sh"],
        "description": "FlooNoC bridges + NoC smoke (cocotb)",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 900,
    },
    # ── generator/config pytests ──
    # Gate-level simulation of the ROUTED netlist. Registered as a flow, and
    # required for a physical claim, because of what 2026-08-12 established:
    # runs/blocka_reharden passed DRC, LVS, XOR, antenna, routing DRC and
    # timing at every corner, and did not boot. LVS proves the layout matches
    # the NETLIST; nothing in this flow proved the netlist matches the RTL, and
    # GLS was something you had to remember to run by hand.
    #
    # GLS_RUN selects which hardening run's netlist to simulate. The powerup
    # init is regenerated per netlist by run_gls.sh's caller -- see
    # tb/gls/gen_powerup_init.py.
    "gls": {
        "cmd": ["bash", "tb/gls/run_gls.sh"],
        "require_exit_success": True,
        "description": "Gate-level sim of the routed netlist (EXIT SUCCESS gate)",
        "effect": "execute",
        "cost": "minutes",
        # `physical` because it judges a physical artefact, and the simulation
        # scopes because it is a simulation and refusing it under `simulation`
        # would push people to widen their scope to check a netlist.
        "scopes": ["testbench", "simulation", "integration", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 5400,
    },
    # Sequential equivalence: the routed netlist against the RTL it came from.
    # GLS proves a netlist can boot -- one firmware image, one path through 22
    # bonded pins. This covers the logic instead. Neither subsumes the other.
    #
    # kepler-formal exits 0 whether it proves equivalence or finds a
    # counterexample, so the gate is the EXIT SUCCESS marker run_lec.sh emits
    # only on a full-coverage proof.
    "lec": {
        "cmd": ["bash", "flow/librelane/scripts/run_lec.sh"],
        "require_exit_success": True,
        "description": "Netlist-vs-RTL sequential equivalence (kepler-formal)",
        "effect": "execute",
        "cost": "hours",
        "scopes": ["physical", "integration", "simulation", "testbench"],
        # Formal on a 70k-cell design can run for hours and may not converge.
        # That is a real cost, not a formality.
        "approval": True,
        "evidence": False,
        "timeout": 7200,
    },
    "pytest": {
        "cmd": ["python3", "-m", "pytest", "test/test_x_heep_gen", "-q"],
        "description": "Config-system + harness pytest suites",
        "effect": "execute",
        "cost": "minutes",
        "scopes": ["analysis", "config", "rtl", "simulation", "integration", "testbench", "documentation", "drc", "physical"],
        "approval": False,
        "evidence": False,
        "timeout": 900,
    },
}

# Flows that stay OUT of the pinned sim shell (`nix develop <repo>#sim`):
# - harden-*: LibreLane brings its own environment (flow/librelane).
# - pytest: runs the harness's own Python, which the sim shell does not carry.
# - gls: its evidence (Blocks A and C) came from the host's Icarus 13.0, and
#   nix-eda 6.11 ships a different Icarus snapshot. Moving it is a re-run of
#   that evidence, not a refactor.
HOST_FLOWS = frozenset({"harden-classic", "harden-chip", "pytest", "gls"})


def sim_shell_command(cmd: List[str], repo_root: Path, environ: Dict[str, str],
                      which: Callable[[str], Optional[str]] = shutil.which
                      ) -> List[str]:
    """`cmd` run inside the pinned sim shell, unless it cannot or need not be.

    Inside any nix shell the command runs as-is, and without nix it runs on
    PATH; either way tb/tools.sh then refuses a Verilator or RISC-V toolchain
    that is not the pinned one, so the fallback cannot silently pass.
    """
    if environ.get("IN_NIX_SHELL") or which("nix") is None:
        return cmd
    return ["nix", "develop", f"{repo_root}#sim", "--command", *cmd]


# ── Log parsers ──────────────────────────────────────────────────────

def _parse_verilator_lint(output: str) -> Dict[str, Any]:
    """Extract lint warnings/errors from Verilator output."""
    warnings = []
    errors = []
    for line in output.splitlines():
        if "%Warning" in line:
            warnings.append(line.strip())
        elif "%Error" in line:
            errors.append(line.strip())
    return {"warnings": warnings, "errors": errors,
            "warning_count": len(warnings), "error_count": len(errors)}


def _parse_make_output(output: str) -> Dict[str, Any]:
    """Extract timing and status from make output."""
    metrics: Dict[str, Any] = {}
    # Look for real time
    m = re.search(r"real\s+(\d+)m(\d+\.\d+)s", output)
    if m:
        metrics["wall_time_s"] = int(m.group(1)) * 60 + float(m.group(2))
    # Look for size info
    for line in output.splitlines():
        if "text" in line and "data" in line and "bss" in line:
            # Size output line
            parts = line.split()
            if len(parts) >= 5:
                metrics["text_bytes"] = parts[0]
                metrics["data_bytes"] = parts[1]
                metrics["bss_bytes"] = parts[2]
    return metrics


def _parse_pytest(output: str) -> Dict[str, Any]:
    """Extract pass/fail counts from pytest -q output."""
    result: Dict[str, Any] = {}
    m = re.search(r"(\d+) passed", output)
    if m:
        result["passed"] = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        result["failed"] = int(m.group(1))
    result["all_pass"] = result.get("failed", 0) == 0 and "passed" in result
    return result


def _parse_cocotb_result(output: str) -> Dict[str, Any]:
    """Extract PASS/FAIL from cocotb output."""
    result: Dict[str, Any] = {}
    m = re.search(r"TESTS=(\d+)\s+PASS=(\d+)\s+FAIL=(\d+)", output)
    if m:
        result["tests"] = int(m.group(1))
        result["pass"] = int(m.group(2))
        result["fail"] = int(m.group(3))
        result["all_pass"] = result["fail"] == 0
    # Check for the sim's EXIT SUCCESS marker. Anchored: run.sh prints
    # "### RESULT: no EXIT SUCCESS" on failure, which CONTAINS the substring —
    # a plain `in` check is false-positive on every failed run.
    if re.search(r"(?m)^(### RESULT: )?EXIT SUCCESS", output):
        result["exit_success"] = True
    elif re.search(r"(?m)EXIT FAILURE|no EXIT SUCCESS", output):
        result["exit_success"] = False
    return result


# ── FlowRunner ───────────────────────────────────────────────────────

class FlowRunner:
    """Skill: run EDA flows with structured reporting.

    Usage:
        runner = FlowRunner()
        result = runner.run("mosaic-gen")
        result = runner.run("mosaic-gen-config", config="configs/mosaic_sim.yaml")
        result = runner.run("tb-multicore")
    """

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT
        self.reports: List[RunReport] = []

    def list_flows(self) -> SkillResult:
        """List all available flows."""
        flows = {}
        for name, spec in FLOWS.items():
            flows[name] = {
                "description": spec["description"],
                "timeout": spec["timeout"],
                "cwd": spec.get("cwd", "."),
            }
        return SkillResult(
            ok=True, skill="flow-runner",
            summary=f"{len(FLOWS)} flows available",
            details={"flows": flows},
        )

    def run(
        self,
        flow_name: str,
        config: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> SkillResult:
        """Run an EDA flow by name.

        Args:
            flow_name: Name of the flow (key in FLOWS dict).
            config: Optional config path for mosaic-gen-config.
            extra_args: Additional command-line arguments.
            env: Additional environment-variable overlays. Model API secrets
                are removed at the subprocess boundary.

        Returns:
            SkillResult with timing, metrics, and parsed log output.
        """
        if flow_name not in FLOWS:
            return SkillResult(
                ok=False, skill="flow-runner",
                summary=f"Unknown flow '{flow_name}'",
                errors=[f"Available flows: {sorted(FLOWS.keys())}"],
            )

        spec = FLOWS[flow_name]
        if "cmd" in spec:
            cmd = list(spec["cmd"])
        else:
            # cmd_prefix flows (mosaic-gen-config) REQUIRE a config: the last
            # token is the "MOSAIC_CFG=" argv stub completed below.
            if not config:
                return SkillResult(
                    ok=False, skill="flow-runner",
                    summary=f"Flow '{flow_name}' requires --config",
                    errors=["cmd_prefix flow with no config given"],
                )
            cmd = list(spec["cmd_prefix"])

        # Handle config override: make-style flows take MOSAIC_CFG=<path> as an
        # argv token (cmd_prefix pattern); run.sh-style flows take it from the
        # ENVIRONMENT (env_config_key). Anything else with a config is an error
        # (the old behavior appended broken "MOSAIC_CFG=", "<path>" argv tokens).
        if config and flow_name == "mosaic-gen-config":
            cmd[-1] = f"MOSAIC_CFG={config}"
        elif config and spec.get("env_config_key"):
            env = dict(env) if env else {}
            env.setdefault(spec["env_config_key"], config)
        elif config:
            return SkillResult(
                ok=False, skill="flow-runner",
                summary=f"Flow '{flow_name}' does not accept a config override",
                errors=["Only mosaic-gen-config (argv) and env_config_key flows "
                        "(MOSAIC_CFG env) take --config"],
            )

        # Materialize an environment without known/configured model credentials
        # here so this policy remains visible at the flow boundary. run_cmd
        # enforces the same rule again for every other tool caller.
        env = build_subprocess_env(env)

        if extra_args:
            cmd.extend(extra_args)
        if flow_name not in HOST_FLOWS:
            cmd = sim_shell_command(cmd, self.repo_root, env)

        cwd = self.repo_root / spec["cwd"] if spec.get("cwd") else self.repo_root
        timeout = spec["timeout"]

        start = time.monotonic()
        try:
            run_kwargs = {"cwd": cwd, "timeout": timeout, "env": env}
            if on_output is not None:
                run_kwargs["on_output"] = on_output
            proc = run_cmd(cmd, **run_kwargs)
            elapsed = time.monotonic() - start
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return SkillResult(
                ok=False, skill="flow-runner",
                summary=f"Flow '{flow_name}' timed out after {timeout}s",
                details={"timeout": timeout},
            )
        except Exception as e:
            elapsed = time.monotonic() - start
            return SkillResult(
                ok=False, skill="flow-runner",
                summary=f"Flow '{flow_name}' failed: {e}",
                errors=[str(e)],
            )

        # Build report
        combined_output = proc.stdout + "\n" + proc.stderr

        # Parse flow-specific output
        metrics: Dict[str, Any] = {}
        warnings: List[str] = []
        errors: List[str] = []

        if "verilator" in flow_name:
            parsed = _parse_verilator_lint(combined_output)
            warnings = parsed["warnings"]
            errors = parsed["errors"]
            metrics.update(parsed)
        elif flow_name == "pytest":
            metrics.update(_parse_pytest(combined_output))
        elif spec.get("signoff"):
            # Hardening flows were previously parsed like cocotb testbenches,
            # which emit markers LibreLane never produces -- so no gate metric
            # existed and `ok` fell back to the make exit code. Signoff
            # evidence is now parsed explicitly and fail-closed.
            signoff_spec = spec["signoff"]
            report_dir = (
                self.repo_root / spec["report_dir"]
                if spec.get("report_dir") else None
            )
            runs_dir = (
                self.repo_root / spec["runs_dir"]
                if spec.get("runs_dir") else None
            )
            gate = gate_guard(
                f"signoff:{flow_name}",
                parse_signoff,
                combined_output,
                report_dir,
                runs_dir=runs_dir,
                require_drc=bool(signoff_spec.get("drc")),
                require_lvs=bool(signoff_spec.get("lvs")),
                require_timing=bool(signoff_spec.get("timing")),
                require_antenna=bool(signoff_spec.get("antenna")),
                waivers=load_waivers(
                    self.repo_root / spec["waivers"]
                    if spec.get("waivers") else None
                ),
                classify=lambda ev: ev.status,
            )
            if gate.errored:
                metrics["signoff_status"] = EvidenceStatus.INFRASTRUCTURE_ERROR.value
                finding = gate_error_finding(gate.gate, gate.reason, gate.error)
                errors.append(finding["message"])
            else:
                metrics.update(gate.value.as_metrics())
        elif "tb-" in flow_name or spec.get("require_exit_success"):
            # Not just tb- flows. `gls` declares require_exit_success and does
            # NOT match "tb-", so exit_success was never parsed for it and the
            # gate below compared `None is True`: a passing gate-level run
            # reported FAIL through flow-runner, every time. A flow that says
            # it is gated on a marker must have that marker parsed.
            metrics.update(_parse_cocotb_result(combined_output))

        metrics.update(_parse_make_output(combined_output))

        report = RunReport(
            skill="flow-runner",
            config=flow_name,
            elapsed_s=elapsed,
            exit_code=proc.returncode,
            metrics=metrics,
            warnings=warnings,
            errors=errors,
        )
        self.reports.append(report)

        ok = proc.returncode == 0
        # tb/mosaic_soc runners exit 0 even when the sim fails — the real gate
        # is the parsed EXIT SUCCESS marker (and cocotb all_pass where present).
        if spec.get("require_exit_success"):
            ok = ok and metrics.get("exit_success") is True
        elif "exit_success" in metrics:
            ok = ok and bool(metrics["exit_success"])
        if "all_pass" in metrics:
            ok = ok and bool(metrics["all_pass"])

        # Fail-closed evidence gate (roadmap §12.2/§12.5): a flow that declares
        # required evidence and does not produce it did NOT pass — the
        # threshold was never evaluated. An exit code is execution evidence,
        # not qualification evidence.
        for key in spec.get("required_evidence", ()):
            if key not in metrics:
                ok = False
                message = (
                    f"flow '{flow_name}' produced no '{key}' evidence; "
                    "required evidence is missing, so this is not a pass"
                )
                errors.append(message)
                metrics.setdefault(
                    "evidence_status", EvidenceStatus.INFRASTRUCTURE_ERROR.value
                )
        signoff_status = metrics.get("signoff_status")
        if signoff_status is not None:
            ok = ok and signoff_status == EvidenceStatus.PASS.value
        summary_parts = [f"Flow '{flow_name}' {'PASS' if ok else 'FAIL'}"]
        summary_parts.append(f"({elapsed:.1f}s, exit={proc.returncode})")
        if metrics.get("all_pass"):
            summary_parts.append("tests=all_pass")
        if signoff_status is not None:
            summary_parts.append(f"signoff={signoff_status}")
            if metrics.get("drc_violations"):
                summary_parts.append(f"drc={metrics['drc_violations']}")
            if metrics.get("lvs_match") is False:
                summary_parts.append("lvs=mismatch")
            # A pass that rests on waivers says so in the one line most people
            # read. "PASS" and "PASS with 2 waivers" are different claims.
            if metrics.get("signoff_waived"):
                summary_parts.append(f"waived={len(metrics['signoff_waived'])}")
        if errors:
            summary_parts.append(f"{len(errors)} errors")

        return SkillResult(
            ok=ok, skill="flow-runner",
            summary=" ".join(summary_parts),
            details={
                "exit_code": proc.returncode,
                "elapsed_s": round(elapsed, 2),
                "metrics": metrics,
                "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
                "stderr_tail": proc.stderr[-2000:] if proc.stderr else "",
            },
            errors=errors,
        )

    def run_all(self, flow_names: List[str], **kwargs) -> SkillResult:
        """Run multiple flows in sequence. Stops on first failure."""
        results = []
        for name in flow_names:
            r = self.run(name, **kwargs)
            results.append(r)
            if not r.ok:
                return SkillResult(
                    ok=False, skill="flow-runner",
                    summary=f"Sequence stopped at '{name}': {r.summary}",
                    details={"results": [rr.to_json() for rr in results]},
                    errors=r.errors,
                )
        return SkillResult(
            ok=True, skill="flow-runner",
            summary=f"All {len(flow_names)} flows passed",
            details={"results": [r.to_json() for r in results]},
        )

    def get_latest_report(self) -> Optional[RunReport]:
        return self.reports[-1] if self.reports else None
