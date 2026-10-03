"""tb-smith skill — generate + run per-core verification.

The verification corner of the scaffold → agent-fill → TB-verified triangle:

  generate   emit tb/sci/<core>/ — a single-hart SCI-level self-checking TB
             (dormancy / wake / liveness / sentinel phases) + run.sh (pinned
             Verilator) + deps.f (vendor visibility). Reuses
             tb/mosaic/tb_obi_mem.sv (baked 4-word program at 0x180 writing
             sentinel 0x55 to byte 0x40) — never copies it.
  run        execute the TB, parse the TB PASS/FAIL markers into metrics.
  wake-demo  the full-SoC gate: canonical 3-hart config (config-author
             wake-demo) + flow-runner tb-soc-wake (EXIT SUCCESS required).
"""

import re
import stat
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mako.template import Template

from ..core import SkillResult, REPO_ROOT, SCI_CORES, run_cmd, log

_TPL_DIR = Path(__file__).resolve().parent.parent / "templates" / "tb"

# Port shape of every integrated SCI wrapper (unified = single mem port).
# New cores: derived from the wrapper-smith analysis (--analysis) or the
# family registry; fallback = probe the wrapper source for mem_req_o.
_UNIFIED_CORES = {"serv", "qerv", "picorv32", "cva6", "rocket", "boom"}
_SPLIT_CORES = {"fazyrv", "ibex", "snitch", "hazard3"}


class TbSmith:
    """Skill: per-core TB generation + execution + full-SoC wake demo."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT

    @staticmethod
    def _core_identifier(core: str) -> Optional[str]:
        normalized = core.lower()
        return normalized if re.fullmatch(r"[a-z][a-z0-9_]*", normalized) else None

    @staticmethod
    def _invalid_core(core: str) -> SkillResult:
        return SkillResult(
            ok=False,
            skill="tb-smith",
            summary=f"invalid core identifier '{core}'",
            errors=["use letters, digits, and underscores; start with a letter"],
        )

    def _is_unified(self, core: str, unified: Optional[bool],
                    analysis: Optional[Path]) -> Optional[bool]:
        if unified is not None:
            return unified
        if analysis and Path(analysis).exists():
            import json
            a = json.loads(Path(analysis).read_text())
            fam = a.get("classification", {}).get("family", "")
            from .wrapper_smith import FAMILIES
            if fam in FAMILIES:
                return FAMILIES[fam]["port_shape"] == "unified"
        if core in _UNIFIED_CORES:
            return True
        if core in _SPLIT_CORES:
            return False
        # probe the wrapper source
        w = self.repo_root / "hw" / "sci" / f"{core}_sci.sv"
        if w.exists():
            return "mem_req_o" in w.read_text()
        return None

    def generate(self, core: str, boot_addr: int = 0x180,
                 unified: Optional[bool] = None,
                 watchdog_cycles: int = 200_000,
                 analysis: Optional[Path] = None) -> SkillResult:
        original_core = core
        core = self._core_identifier(core)
        if core is None:
            return self._invalid_core(original_core)
        wrapper = self.repo_root / "hw" / "sci" / f"{core}_sci.sv"
        if not wrapper.exists():
            return SkillResult(
                ok=False, skill="tb-smith",
                summary=f"hw/sci/{core}_sci.sv does not exist — run "
                        f"wrapper-smith scaffold first",
                errors=[str(wrapper)])
        is_unified = self._is_unified(core, unified, analysis)
        if is_unified is None:
            return SkillResult(
                ok=False, skill="tb-smith",
                summary=f"cannot derive the port shape for '{core}' — pass "
                        f"--unified or --split",
                errors=["unknown port shape"])

        out_dir = self.repo_root / "tb" / "sci" / core
        out_dir.mkdir(parents=True, exist_ok=True)

        tb_text = str(Template((_TPL_DIR / "single_hart_tb.sv.mako").read_text())
                      .render(core=core, unified=is_unified,
                              watchdog=watchdog_cycles))
        (out_dir / f"tb_{core}_sci.sv").write_text(tb_text)

        run_text = str(Template((_TPL_DIR / "run.sh.mako").read_text())
                       .render(core=core))
        run_path = out_dir / "run.sh"
        run_path.write_text(run_text)
        run_path.chmod(run_path.stat().st_mode | stat.S_IEXEC)

        # deps.f: -y every vendor subdir with RTL + the core's .f fragment
        vendor = self.repo_root / "hw" / "vendor" / "mosaic" / core
        lines = []
        if vendor.is_dir():
            dirs = {f.parent for f in list(vendor.rglob("*.v")) + list(vendor.rglob("*.sv"))}
            for d in sorted(dirs):
                lines.append(f"-y {d.relative_to(self.repo_root)}")
            frag = vendor / f"{core}.f"
            if frag.exists():
                for ln in frag.read_text().splitlines():
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        lines.append(ln)
        else:
            lines.append(f"# TODO(tb-smith): vendor tree hw/vendor/mosaic/{core} "
                         f"not found — add -y/-f entries for the core's RTL")
        # bridge deps for the bridge-based families
        if core in ("cva6",):
            lines.append("-y hw/vendor/mosaic/axi_obi")
        if core in ("rocket", "boom"):
            lines.append("hw/vendor/mosaic/tl_obi/xheep_tilelink_to_obi.sv")
            lines.append("-f hw/vendor/mosaic/berkeley/berkeley.f")
        (out_dir / "deps.f").write_text(
            "# deps.f — vendor visibility for tb_%s_sci (extend as needed)\n%s\n"
            % (core, "\n".join(lines)))

        note = ("" if boot_addr == 0x180 else
                f" NOTE: --boot-addr {boot_addr:#x} requested — the TB memory "
                f"bakes the program at 0x180; override the wrapper's boot "
                f"parameter in the TB by hand if needed.")
        return SkillResult(
            ok=True, skill="tb-smith",
            summary=f"generated tb/sci/{core}/ "
                    f"({'unified' if is_unified else 'split'} ports, "
                    f"watchdog {watchdog_cycles} cycles).{note}",
            details={"dir": str(out_dir),
                     "files": [f"tb_{core}_sci.sv", "run.sh", "deps.f"],
                     "unified": is_unified},
        )

    def run(
        self,
        core: str,
        timeout: int = 600,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> SkillResult:
        original_core = core
        core = self._core_identifier(core)
        if core is None:
            return self._invalid_core(original_core)
        script = self.repo_root / "tb" / "sci" / core / "run.sh"
        if not script.exists():
            return SkillResult(ok=False, skill="tb-smith",
                               summary=f"{script} missing — run generate first",
                               errors=[str(script)])
        proc = run_cmd(["bash", str(script)], cwd=self.repo_root,
                       timeout=timeout, on_output=on_output)
        out = proc.stdout + "\n" + proc.stderr
        m = re.search(r"TB (PASS|FAIL)(?: reason=(\S+))?"
                      r".*?instr_reqs=(\d+) data_reqs=(\d+) cycles=(\d+)", out)
        metrics: Dict[str, Any] = {}
        if m:
            metrics = {"pass": m.group(1) == "PASS", "reason": m.group(2),
                       "instr_reqs": int(m.group(3)),
                       "data_reqs": int(m.group(4)),
                       "cycles": int(m.group(5))}
        ok = proc.returncode == 0 and metrics.get("pass", False)
        return SkillResult(
            ok=ok, skill="tb-smith",
            summary=(f"tb/sci/{core}: "
                     + ("TB PASS" if ok else
                        f"TB FAIL ({metrics.get('reason') or 'build/run error'})")
                     + (f" — {metrics.get('cycles', '?')} cycles, "
                        f"{metrics.get('instr_reqs', 0)}+"
                        f"{metrics.get('data_reqs', 0)} reqs" if metrics else "")),
            details={"metrics": metrics,
                     "stdout_tail": proc.stdout[-2000:],
                     "stderr_tail": proc.stderr[-1500:]},
            errors=[] if ok else [metrics.get("reason") or "see stderr_tail"],
        )

    def wake_demo(
        self,
        core: str,
        execute: bool = True,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> SkillResult:
        original_core = core
        core = self._core_identifier(core)
        if core is None:
            return self._invalid_core(original_core)
        from .config_author import ConfigAuthor
        from .flow_runner import FlowRunner
        cfg = ConfigAuthor(self.repo_root).wake_demo_config(core)
        if not cfg.ok:
            return cfg
        stages: Dict[str, Any] = {"config": {"ok": True,
                                             "path": cfg.details["path"]}}
        if not execute:
            return SkillResult(ok=True, skill="tb-smith",
                               summary=f"wake-demo config written: "
                                       f"{cfg.details['path']}",
                               details=stages)
        wake = FlowRunner(self.repo_root).run(
            "tb-soc-wake", config=cfg.details["path"], on_output=on_output)
        stages["wake_demo"] = {"ok": wake.ok, "summary": wake.summary,
                               "metrics": wake.details.get("metrics", {})}
        return SkillResult(
            ok=wake.ok, skill="tb-smith",
            summary=f"wake-demo({core}): {wake.summary}",
            details=stages, errors=wake.errors,
        )
