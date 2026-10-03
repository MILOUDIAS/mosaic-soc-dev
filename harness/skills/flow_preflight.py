"""Cheap checks before an expensive flow, each one bought with a wasted run.

WHY THIS EXISTS. On 2026-09-09/10 a three-block re-harden cost roughly seven
wasted hours, and not one of those hours was spent on a hard problem. Every
failure was a precondition that a five-second check would have caught, and
every one of them reported itself as something else:

  disk filled at step 53 of 60      3 h 06 lost, after routing had already
                                    reached 0 DRT and 0 antenna violations
  config missing the template       2 runs, ~10 min, surfaced as a yosys
                                    syntax error in a vendored lowrisc file
  bundle hash moved by a previous
  run's own output                  3 x ~30 min regeneration, reported as
                                    "no MOSAIC manifest. Generate the RTL
                                    first" -- a missing INPUT
  run tag reused                    ~15 min misdiagnosing a step config from
                                    the previous attempt as the live one
  NDR pinned to synthesis names     a 4 h run that could not converge

THE SHAPE OF ALL FIVE. A long flow consumed a precondition nobody checked,
failed late, and blamed something downstream. The cost is not the failed run;
it is that the error message points away from the cause.

WHAT THIS DOES NOT DO. It does not check whether the design is good. Every
check here is about whether the run can *mean* anything -- disk, inputs,
identity, and rules that will not survive the run they are written for. A
clean preflight says the flow will produce a result, not that the result will
be one you like.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..core import REPO_ROOT, SkillResult

#: Free space a stage needs, in GB. Measured on this project's three blocks:
#: a hardening run's outputs are ~5 GB and an RTL bundle is ~20 GB. The
#: headroom is deliberate -- the run that filled the disk died with 0 bytes
#: left, three hours in.
NEEDS_GB: Dict[str, int] = {"harden": 12, "regen": 30}

#: Keys a hardening config must carry to be a WHOLE config rather than a
#: fragment. run_signoff.sh does `cat CONFIG FRAGMENT`, so the config file is
#: the entire thing -- it does not merge the template. A file holding only
#: design keys parses, runs, and silently takes LibreLane's defaults:
#: USE_SLANG went False and yosys's Verilog-2005 frontend then rejected an
#: unpacked-array parameter that slang accepts.
TEMPLATE_MARKERS = ("meta", "USE_SLANG", "PNR_CORNERS", "CLOCK_PORT")

#: A net name of this shape is emitted by synthesis and does not denote the
#: same net across a re-synthesis. Two separate incidents: `_28773_` looked
#: like a smoking gun in the GLS investigation, and a DRT_ASSIGN_NDR pinned
#: to `_06890_` &c silently stopped applying after a re-harden.
_SYNTH_NET = re.compile(r"_\d{4,}_")


@dataclass
class Preflight:
    stage: str
    blocking: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocking


def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def check_disk(stage: str, repo_root: Path, rep: Preflight) -> None:
    need = NEEDS_GB.get(stage, 0)
    have = _free_gb(repo_root)
    rep.checked.append(f"disk: {have:.0f}G free, {stage} needs {need}G")
    if have < need:
        rep.blocking.append(
            f"only {have:.0f}G free and {stage} needs about {need}G. The run "
            "that ignored this died at step 53 of 60 with OSError 28 after "
            "3 h 06, having already routed clean")


def check_config_is_whole(config: Optional[Path], rep: Preflight) -> None:
    if config is None:
        return
    if not config.is_file():
        rep.blocking.append(f"no hardening config at {config}")
        return
    text = config.read_text()
    missing = [k for k in TEMPLATE_MARKERS
               if not re.search(rf"^{re.escape(k)}\s*:", text, re.M)]
    rep.checked.append(f"config: {config.name}, "
                       f"{len(TEMPLATE_MARKERS) - len(missing)}/"
                       f"{len(TEMPLATE_MARKERS)} template markers")
    if missing:
        rep.blocking.append(
            f"{config.name} is missing {missing}, so it is a FRAGMENT, not a "
            "whole config. run_signoff.sh concatenates the config with the "
            "file list -- it does not merge the template. LibreLane will take "
            "its own defaults for everything absent, and the first symptom "
            "will be a syntax error in a vendored source")


def check_run_tag_is_fresh(tag: Optional[str], repo_root: Path,
                           rep: Preflight) -> None:
    if not tag:
        return
    d = repo_root / "flow/librelane/experimental/runs" / tag
    rep.checked.append(f"run tag: {tag}{' (exists)' if d.exists() else ''}")
    if d.exists():
        rep.warnings.append(
            f"runs/{tag} already exists. Step directories from the earlier "
            "attempt survive alongside the new ones, and reading a stale "
            "config.json as though it were the live run is a real "
            "misdiagnosis -- it cost ~15 minutes on 2026-09-10. Use a fresh "
            "tag or delete the directory first")


def _settings_only(text: str) -> str:
    """YAML with comment lines removed.

    Not fussiness: the first version of this check scanned the raw file and
    flagged `_06890_` out of a comment EXPLAINING that pinning to such names
    is a mistake. A guard that fires on prose describing the hazard is a
    guard people learn to ignore.
    """
    return "\n".join(l for l in text.splitlines()
                      if not l.lstrip().startswith("#"))


def check_rules_survive_resynthesis(config: Optional[Path],
                                    rep: Preflight) -> None:
    if config is None or not config.is_file():
        return
    pinned = sorted(set(_SYNTH_NET.findall(_settings_only(config.read_text()))))
    rep.checked.append(f"routing rules: {len(pinned)} synthesis-named nets")
    if pinned:
        rep.warnings.append(
            f"{len(pinned)} rule(s) name synthesis-generated nets "
            f"({', '.join(pinned[:3])}{'...' if len(pinned) > 3 else ''}). "
            "Those names do not denote the same nets after a re-synthesis, so "
            "the rule will quietly stop applying or apply to something else. "
            "Prefer a name derived from the RTL; if there is none, re-derive "
            "the list from THIS run and expect to do it again")


def check_bundle(config_yaml: Optional[Path], repo_root: Path,
                 rep: Preflight) -> None:
    """Is there an RTL bundle for the current source closure?"""
    if config_yaml is None:
        return
    try:
        import sys
        sys.path.insert(0, str(repo_root / "util"))
        from xheep_gen.build_manifest import compute_identity
        _, key, _ = compute_identity(
            str(config_yaml), str(repo_root / "configs/general.hjson"),
            str(repo_root / "configs/pad_cfg.py"), str(repo_root))
    except Exception as exc:                        # pragma: no cover
        rep.warnings.append(f"could not compute the bundle key: {exc}")
        return
    present = (repo_root / "build/mosaic" / key).is_dir()
    rep.checked.append(f"bundle: {key} {'present' if present else 'ABSENT'}")
    if not present:
        rep.blocking.append(
            f"no RTL bundle for {key}. The flow will abort with 'no MOSAIC "
            "manifest. Generate the RTL first', which reads as a missing "
            "input and is usually a previous run's OUTPUT having moved the "
            f"hash. Run: make mosaic-gen MOSAIC_CFG={config_yaml}")


def preflight(stage: str = "harden", *, config: Optional[str] = None,
              soc_config: Optional[str] = None, tag: Optional[str] = None,
              repo_root: Optional[Path] = None) -> Preflight:
    root = Path(repo_root or REPO_ROOT)
    rep = Preflight(stage=stage)
    cfg = Path(config) if config else None
    if cfg is not None and not cfg.is_absolute():
        cfg = root / cfg
    soc = Path(soc_config) if soc_config else None
    if soc is not None and not soc.is_absolute():
        soc = root / soc

    check_disk(stage, root, rep)
    check_config_is_whole(cfg, rep)
    check_run_tag_is_fresh(tag, root, rep)
    check_rules_survive_resynthesis(cfg, rep)
    if stage == "harden":
        check_bundle(soc, root, rep)
    return rep


def audit(stage: str = "harden", *, config: Optional[str] = None,
          soc_config: Optional[str] = None, tag: Optional[str] = None,
          repo_root: Optional[Path] = None) -> SkillResult:
    """Check what a long flow needs before it spends hours finding out."""

    rep = preflight(stage, config=config, soc_config=soc_config, tag=tag,
                    repo_root=repo_root)
    return SkillResult(
        ok=rep.ok,
        skill="flow-preflight",
        summary=(f"{rep.stage}: ready" if rep.ok else
                 f"{rep.stage}: {len(rep.blocking)} blocking issue(s)")
        + (f", {len(rep.warnings)} warning(s)" if rep.warnings else ""),
        details={"stage": rep.stage, "checked": rep.checked,
                 "warnings": rep.warnings},
        errors=rep.blocking,
    )
