"""wrapper-smith skill — wrap any open-source core/IP for the SCI.

The deterministic half of the scaffold → agent-fill → TB-verified triangle:

  analyze   parse a core's module ports (verible → yosys → regex ladder),
            classify its native bus against the protocol families proven in
            hw/sci/ (harness/templates/wrapper/families.py), extract control
            signals (clk/reset polarity/irq/boot addr), emit analysis JSON
            with a port map, TODO list and the 8-touchpoint checklist.
  scaffold  stage hw/sci/<core>_sci.sv from the family template plus ALL
            integration touchpoints (registries, cpu_subsystem branch,
            sci.core, gen_filelist, bring-up config) with idempotent,
            marker-guarded edits. Default is a DRY-RUN into
            build/wrapper_smith/<core>/stage/ — review, fill the
            TODO(wrapper-smith) markers, then --apply.
  families  list the family table.

The semantic gaps a template cannot know (exact handshake quirks, irq
mapping, byte-enable derivation) are emitted as TODO(wrapper-smith) markers —
the agent's work queue. tb-smith closes the loop by generating and running
the self-checking TB.
"""

import ast
import json
import re
import shutil
import yaml
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core import (
    SkillResult, REPO_ROOT, run_cmd, dump_json, log,
)

import importlib.util as _ilu

_FAM_PATH = Path(__file__).resolve().parent.parent / "templates" / "wrapper" / "families.py"
_spec = _ilu.spec_from_file_location("wrapper_families", _FAM_PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_FAM_PATH}"
_fam_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_fam_mod)
FAMILIES: Dict[str, Dict[str, Any]] = _fam_mod.FAMILIES
CONTROL_PATTERNS: Dict[str, List[str]] = _fam_mod.CONTROL_PATTERNS
CLASSIFY_THRESHOLD: float = _fam_mod.CLASSIFY_THRESHOLD


def audit_core_matrix() -> "SkillResult":
    """Report which catalogued cores can actually be built, and what is missing.

    wrapper-smith exists to close exactly these gaps, so the list of gaps
    belongs next to it. Before this, the only way to learn that a core in the
    catalog had no wrapper was to generate RTL for it and read an unresolved
    module reference out of an elaboration log.
    """
    from ..core import REPO_ROOT, SkillResult
    from util.mosaic_gen.core_integration import audit

    rows = audit(REPO_ROOT)
    gaps = {n: r for n, r in rows.items() if not r.complete}
    detail = {
        name: {
            "wrapper": r.sci_module or "(x-heep native, none needed)",
            "wrapper_present": r.has_sci,
            "template_branch": r.has_branch,
            "complete": r.complete,
            **({"missing": r.missing} if not r.complete else {}),
        }
        for name, r in rows.items()
    }
    return SkillResult(
        ok=not gaps,
        skill="wrapper-smith",
        summary=(f"{len(rows)} catalogued cores, all buildable"
                 if not gaps else
                 f"{len(gaps)} of {len(rows)} catalogued cores cannot be built"),
        details={"cores": detail, "incomplete": sorted(gaps)},
        errors=[r.refusal() for r in gaps.values()],
    )


def _mapping_has_string_key(text: str, variable: str, key: str) -> bool:
    """Return whether a module-level dictionary contains ``key``.

    CORE_SPECS is an annotated assignment, so use the Python AST instead of
    searching the whole file for a coincidental string occurrence.
    """
    tree = ast.parse(text)
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and target.id == variable
            and isinstance(value, ast.Dict)
        ):
            return any(
                isinstance(item, ast.Constant) and item.value == key
                for item in value.keys
            )
    raise ValueError(f"could not locate dictionary {variable}")


def _insert_core_spec(text: str, core: str, family: str) -> str:
    """Append a conservative typed CoreSpec to the authoritative registry."""
    tree = ast.parse(text)
    mapping = None
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "CORE_SPECS"
            and isinstance(node.value, ast.Dict)
        ):
            mapping = node.value
            break
    if mapping is None or mapping.end_lineno is None:
        raise ValueError("could not locate dictionary CORE_SPECS")

    port_shape = FAMILIES[family].get("port_shape")
    capability = "unified_obi" if port_shape == "unified" else "split_obi"
    entry = (
        f'    "{core}": CoreSpec(\n'
        f'        "{core}",\n'
        '        frozenset({"rv32i"}),\n'
        "        _COMMON_BOOT,\n"
        f'        capabilities=frozenset({{"{capability}"}}),\n'
        "    ),\n"
    )
    lines = text.splitlines(keepends=True)
    closing_index = mapping.end_lineno - 1
    if lines[closing_index].strip() != "}":
        raise ValueError("CORE_SPECS dictionary has an unsupported layout")
    lines.insert(closing_index, entry)
    return "".join(lines)


# ── data types ───────────────────────────────────────────────────────

@dataclass
class Port:
    name: str
    dir: str          # input | output | inout
    width: int = 1


@dataclass
class Classification:
    family: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    runner_up: Optional[Dict[str, Any]] = None


def _normalize(name: str) -> str:
    n = name.lower()
    n = re.sub(r"^(io_|i_|o_)", "", n)
    n = re.sub(r"(_i|_o|_ni|_no|_n)$", "", n)
    return n


# ── port extraction ladder ───────────────────────────────────────────

_ANSI_PORT_RE = re.compile(
    r"(?:^|[(,])\s*(input|output|inout)\s+"
    r"(?:(?:wire|reg|logic|var)\s+)?"
    r"(?:(?:signed|unsigned)\s+)?"
    r"(?:\[\s*([^\]:]+)\s*:\s*([^\]]+)\s*\]\s*)?"
    r"([A-Za-z_][A-Za-z0-9_$]*)\s*[,)]?", re.M)

_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)", re.M)


def _width_of(msb: Optional[str], lsb: Optional[str]) -> int:
    if msb is None:
        return 1
    try:
        return abs(int(str(msb), 0) - int(str(lsb), 0)) + 1
    except (ValueError, TypeError):
        return 0  # parameterized width — unknown


def _module_span(text: str, top: str) -> Optional[str]:
    m = re.search(rf"^\s*module\s+{re.escape(top)}\b", text, re.M)
    if not m:
        return None
    end = text.find("endmodule", m.start())
    return text[m.start():end if end > 0 else len(text)]


def _ports_regex(text: str, top: str) -> List[Port]:
    span = _module_span(text, top)
    if span is None:
        return []
    # only the header (up to the first ');' closing the port list)
    hdr_end = span.find(");")
    hdr = span[:hdr_end if hdr_end > 0 else len(span)]
    ports = []
    for m in _ANSI_PORT_RE.finditer(hdr):
        ports.append(Port(name=m.group(4), dir=m.group(1),
                          width=_width_of(m.group(2), m.group(3))))
    return ports


def _ports_verible(path: Path, top: str) -> Optional[List[Port]]:
    """verible-verilog-syntax is used as a tolerant SYNTAX gate; the actual
    port harvest is the regex on the verified file (verible's CST dump is
    version-sensitive; the regex on syntax-clean input is deterministic)."""
    if not shutil.which("verible-verilog-syntax"):
        return None
    proc = run_cmd(["verible-verilog-syntax", str(path)], timeout=60)
    if proc.returncode != 0:
        return None
    return _ports_regex(path.read_text(errors="replace"), top)


def _ports_yosys(files: List[Path], top: str) -> Optional[List[Port]]:
    if not shutil.which("yosys"):
        return None
    import tempfile
    # Never interpolate untrusted RTL paths into a Yosys command string. Yosys
    # treats semicolons as command separators; a crafted filename could inject
    # commands (including plugin loading). Copy sources to generated safe names
    # and run a generated script containing only those names.
    with tempfile.TemporaryDirectory(prefix="mosaic-yosys-") as temp:
        temp_dir = Path(temp)
        safe_files = []
        for index, source in enumerate(files):
            suffix = source.suffix if source.suffix in {".sv", ".v"} else ".sv"
            safe_source = temp_dir / f"source_{index:06d}{suffix}"
            shutil.copyfile(source, safe_source)
            safe_files.append(safe_source)
        out_json = temp_dir / "ports.json"
        script = temp_dir / "ports.ys"
        lines = [f"read_verilog -sv -defer {path.name}" for path in safe_files]
        lines.extend(
            [
                f"hierarchy -top {top}",
                "proc",
                f"write_json {out_json.name}",
            ]
        )
        script.write_text("\n".join(lines) + "\n")
        proc = run_cmd(
            ["yosys", "-q", "-s", script.name], cwd=temp_dir, timeout=300
        )
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(out_json.read_text())
            mod = data["modules"].get(top)
            if not mod:
                return None
            return [
                Port(name=pn, dir=pd["direction"], width=len(pd["bits"]))
                for pn, pd in mod["ports"].items()
            ]
        except Exception as e:  # noqa: BLE001 — any parse issue → next rung
            log.warning(f"yosys json parse failed: {e}")
            return None


# ── classification ───────────────────────────────────────────────────

def classify(ports: List[Port]) -> Classification:
    norm = [_normalize(p.name) for p in ports]
    scores: List[Tuple[str, float, List[str]]] = []
    for fam, spec in FAMILIES.items():
        sigs = spec["signatures"]
        total = sum(sigs.values())
        hit_w = 0.0
        evidence = []
        for group, w in sigs.items():
            alts = group if isinstance(group, tuple) else (group,)
            hit = next(((a, p) for a in alts for p in norm if a in p), None)
            if hit:
                hit_w += w
                evidence.append(f"{hit[0]} -> {hit[1]}")
        score = hit_w / total if total else 0.0
        # requires_any: at least one of these substrings anywhere
        req = spec.get("requires_any")
        if req and not any(any(r in p for r in req) for p in norm):
            score = 0.0
        # anti_signatures: presence disqualifies (e.g. split wb vs unified wb)
        anti = spec.get("anti_signatures")
        if anti and any(any(a in p for a in anti) for p in norm):
            score *= 0.3
        # min_matches: at least N distinct signature hits
        if spec.get("min_matches") and len(evidence) < spec["min_matches"]:
            score = 0.0
        scores.append((fam, round(score, 3), evidence))

    scores.sort(key=lambda t: t[1], reverse=True)
    best, second = scores[0], scores[1]
    if best[1] < CLASSIFY_THRESHOLD:
        return Classification(
            family="unknown", confidence=best[1],
            evidence=[f"best guess {best[0]}: " + "; ".join(best[2][:6])],
            runner_up={"family": best[0], "confidence": best[1],
                       "evidence": best[2][:6]},
        )
    return Classification(
        family=best[0], confidence=best[1], evidence=best[2][:10],
        runner_up={"family": second[0], "confidence": second[1],
                   "evidence": second[2][:4]},
    )


def _extract_control(ports: List[Port], params: List[Dict[str, str]]) -> Dict[str, Any]:
    ctl: Dict[str, Any] = {}
    for p in ports:
        n = p.name.lower()
        nn = _normalize(p.name)
        if "clk" not in ctl and nn in ("clk", "clock") or n in ("clk_i", "clock"):
            ctl.setdefault("clk", p.name)
        if any(k in nn for k in ("rst", "reset")) and "gate" not in nn:
            pol = "active_low" if (n.endswith("n") or "n_" in n[-3:] or
                                   "resetn" in n or "rst_n" in n) else "active_high"
            ctl.setdefault("rst", p.name)
            ctl.setdefault("rst_polarity", pol)
        if any(k in nn for k in CONTROL_PATTERNS["irq"]) and p.dir == "input":
            ctl.setdefault("irq", {"name": p.name, "width": p.width})
    boot = None
    for prm in params:
        if any(b in prm["name"].lower() for b in CONTROL_PATTERNS["boot"]):
            boot = {"kind": "param", "name": prm["name"],
                    "default": prm.get("default", "")}
            break
    if boot is None:
        for p in ports:
            if any(b in p.name.lower() for b in CONTROL_PATTERNS["boot"]):
                boot = {"kind": "port", "name": p.name}
                break
    if boot:
        ctl["boot"] = boot
    return ctl


_PARAM_RE = re.compile(
    r"^\s*parameter\s+(?:[a-z_\[\]0-9: ]+\s+)?([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*([^,\n)]+)",
    re.M)


def _extract_params(text: str, top: str) -> List[Dict[str, str]]:
    span = _module_span(text, top)
    if span is None:
        return []
    hdr_end = span.find(");")
    hdr = span[:hdr_end if hdr_end > 0 else len(span)]
    return [{"name": m.group(1), "default": m.group(2).strip()}
            for m in _PARAM_RE.finditer(hdr)]


# ── the skill ────────────────────────────────────────────────────────

class WrapperSmith:
    """Skill: analyze + scaffold SCI integration for a new core/IP."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT

    # ── analyze ──────────────────────────────────────────────────────

    def _find_top_file(self, rtl: Path, top: Optional[str]) -> Tuple[Optional[Path], Optional[str]]:
        """Locate (file, module) for the top. Directory: prefer filename==top,
        else the file containing the module `top`, else the largest module."""
        if rtl.is_file():
            text = rtl.read_text(errors="replace")
            mods = _MODULE_RE.findall(text)
            if top and top in mods:
                return rtl, top
            if top:
                return (rtl, top) if top in mods else (rtl, None)
            # largest module in the file
            return rtl, (max(mods, key=lambda m: len(_module_span(text, m) or ""))
                         if mods else None)
        candidates = sorted(list(rtl.rglob("*.sv")) + list(rtl.rglob("*.v")))
        if top:
            named = [f for f in candidates if f.stem == top]
            for f in named + candidates:
                if re.search(rf"^\s*module\s+{re.escape(top)}\b",
                             f.read_text(errors="replace"), re.M):
                    return f, top
            return None, None
        # no top given: biggest file's biggest module
        if not candidates:
            return None, None
        f = max(candidates, key=lambda p: p.stat().st_size)
        mods = _MODULE_RE.findall(f.read_text(errors="replace"))
        return f, (mods[0] if mods else None)

    def analyze(self, rtl: Path, top: Optional[str] = None,
                out: Optional[Path] = None, *, persist: bool = True,
                allow_external_parsers: bool = True) -> SkillResult:
        rtl = Path(rtl)
        if not rtl.exists():
            return SkillResult(ok=False, skill="wrapper-smith",
                               summary=f"{rtl} does not exist",
                               errors=[str(rtl)])
        top_file, top_mod = self._find_top_file(rtl, top)
        if not top_file or not top_mod:
            return SkillResult(
                ok=False, skill="wrapper-smith",
                summary=f"could not locate top module"
                        f"{' ' + top if top else ''} under {rtl}",
                errors=["pass --top <module> and/or a more specific path"])

        text = top_file.read_text(errors="replace")

        # extraction ladder: verible (syntax gate + regex) → regex → yosys enrich
        parser = "verible"
        ports = (
            _ports_verible(top_file, top_mod) if allow_external_parsers else None
        )
        if ports is None or not ports:
            parser = "regex"
            ports = _ports_regex(text, top_mod)
        if not ports and allow_external_parsers:
            parser = "yosys"
            src_files = ([top_file] if rtl.is_file()
                         else sorted(set(rtl.rglob("*.sv")) | set(rtl.rglob("*.v"))))
            ports = _ports_yosys(src_files, top_mod) or []
        if not ports:
            return SkillResult(
                ok=False, skill="wrapper-smith",
                summary=f"no ports extracted from {top_mod} ({top_file})",
                errors=[
                    "regex extraction failed; enable external parsers for "
                    "Verible/Yosys fallback" if not allow_external_parsers else
                    "verible/regex/yosys all failed — is the header ANSI-style?"
                ])

        params = _extract_params(text, top_mod)
        cls = classify(ports)
        control = _extract_control(ports, params)

        todos: List[str] = []
        if cls.family == "unknown":
            todos.append("bus family UNKNOWN — pick one manually with "
                         "`scaffold --family <f>` after reviewing the port list")
        if control.get("rst_polarity") == "active_high":
            todos.append(f"reset '{control.get('rst')}' is ACTIVE-HIGH: the "
                         f"wrapper must invert (rst_ni & fetch_enable_i)")
        if "boot" not in control:
            todos.append("no boot-address parameter/port recognized — find how "
                         "the core selects its reset PC (hardwired?)")
        irq = control.get("irq")
        if irq and irq["width"] not in (1, 32):
            todos.append(f"irq '{irq['name']}' width {irq['width']} — map onto "
                         f"the 32-bit SCI irq_i vector explicitly")
        fam_spec = FAMILIES.get(cls.family, {})
        if fam_spec.get("port_shape") == "unified":
            todos.append("unified port: cpu_subsystem branch must tie "
                         "core_instr_req_o[HART] = '0")

        analysis = {
            "schema": "wrapper-smith/analysis@1",
            "top": top_mod,
            "top_file": str(top_file),
            "source_root": str(rtl),
            "parser": parser,
            "ports": [asdict(p) for p in ports],
            "params": params,
            "classification": asdict(cls),
            "control": control,
            "todos": todos,
            "checklist": [
                {"touchpoint": t, "status": "pending"} for t in (
                    "util/mosaic_gen/cpu/cpu.py AVAILABLE_CPUS",
                    "util/mosaic_gen/core_registry.py CORE_SPECS",
                    "hw/sci/<core>_sci.sv wrapper",
                    "hw/core-v-mini-mcu/cpu_subsystem.sv.tpl branch",
                    "hw/sci/sci.core files list",
                    "tb/mosaic_soc/gen_filelist.py visibility",
                    "hw/vendor/mosaic/<core>/ + .core stub",
                    "configs/mosaic_<core>.yaml bring-up config",
                )],
        }

        resolved_out: Optional[Path] = None
        if persist:
            resolved_out = (
                Path(out)
                if out
                else self.repo_root / "build" / "wrapper_smith"
                / f"{top_mod}.analysis.json"
            )
            dump_json(analysis, resolved_out)

        ru = cls.runner_up or {}
        destination = str(resolved_out) if resolved_out else "in-memory analysis"
        return SkillResult(
            ok=True, skill="wrapper-smith",
            summary=f"{top_mod}: family={cls.family} "
                    f"(confidence {cls.confidence:.2f}, parser {parser}, "
                    f"{len(ports)} ports; runner-up {ru.get('family')} "
                    f"{ru.get('confidence', 0):.2f}) -> {destination}",
            details={
                "analysis_path": str(resolved_out) if resolved_out else None,
                "analysis": analysis,
            },
        )

    # ── fetch ────────────────────────────────────────────────────────

    _LICENSE_HINTS = [
        ("Apache License", "Apache-2.0"),
        ("Apache-2.0", "Apache-2.0"),
        ("MIT License", "MIT"),
        ("Permission is hereby granted, free of charge", "MIT"),
        ("BSD 3-Clause", "BSD-3-Clause"),
        ("Redistribution and use in source and binary forms", "BSD"),
        ("Solderpad", "SHL"),
        ("SHL-", "SHL"),
        ("GNU GENERAL PUBLIC LICENSE", "GPL"),
        ("GNU LESSER", "LGPL"),
        ("CERN Open Hardware", "CERN-OHL"),
    ]

    def _detect_license(self, repo_dir: Path) -> Dict[str, str]:
        for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING",
                     "LICENSE.rst", "COPYING.md"):
            p = repo_dir / name
            if p.exists():
                head = p.read_text(errors="replace")[:2000]
                for hint, spdx in self._LICENSE_HINTS:
                    if hint in head:
                        return {"file": name, "guess": spdx}
                return {"file": name, "guess": "UNKNOWN — read it"}
        return {"file": "", "guess": "NO LICENSE FILE FOUND — do not vendor "
                                     "without clarifying terms"}

    def fetch(self, url: str, name: Optional[str] = None,
              subdir: Optional[str] = None) -> SkillResult:
        """Clone + pin a core repo for vendoring: `<url>[@<ref-or-commit>]`.

        Clones into build/wrapper_smith/fetch/<name>/, records the exact
        commit + license guess in .wrapper-smith-provenance.json (consumed by
        `scaffold --vendor-from` for the .core header). GPL-family licenses
        are flagged — review before vendoring into an Apache/SHL tree.
        """
        ref = None
        if "@" in url.rsplit("/", 1)[-1]:
            url, ref = url.rsplit("@", 1)
        name = (name or url.rstrip("/").rsplit("/", 1)[-1]
                .removesuffix(".git")).lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) or name in {".", ".."}:
            return SkillResult(
                ok=False,
                skill="wrapper-smith",
                summary=f"invalid fetch name '{name}'",
                errors=["use a single path-safe name containing letters, digits, dot, dash, or underscore"],
            )
        subpath = Path(subdir) if subdir else None
        if subpath is not None and (subpath.is_absolute() or ".." in subpath.parts):
            return SkillResult(
                ok=False,
                skill="wrapper-smith",
                summary=f"invalid fetch subdir '{subdir}'",
                errors=["subdir must be a relative path contained by the fetched repository"],
            )
        dst = self.repo_root / "build" / "wrapper_smith" / "fetch" / name
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)

        is_sha = bool(ref and re.fullmatch(r"[0-9a-f]{7,40}", ref))
        if ref and not is_sha:
            proc = run_cmd(["git", "clone", "--depth", "1", "--branch",
                            str(ref), url, str(dst)], timeout=600)
        else:
            proc = run_cmd(["git", "clone",
                            *([] if is_sha else ["--depth", "1"]),
                            url, str(dst)], timeout=600)
        if proc.returncode != 0:
            return SkillResult(ok=False, skill="wrapper-smith",
                               summary=f"clone failed: {url}",
                               errors=[proc.stderr[-800:]])
        if is_sha:
            proc = run_cmd(["git", "-C", str(dst), "checkout", str(ref)],
                           timeout=120)
            if proc.returncode != 0:
                return SkillResult(ok=False, skill="wrapper-smith",
                                   summary=f"checkout {ref} failed",
                                   errors=[proc.stderr[-800:]])
        commit = run_cmd(["git", "-C", str(dst), "rev-parse", "HEAD"],
                         timeout=60).stdout.strip()
        lic = self._detect_license(dst)

        rtl_root = (dst / subpath).resolve() if subpath is not None else dst.resolve()
        try:
            rtl_root.relative_to(dst.resolve())
        except ValueError:
            return SkillResult(
                ok=False,
                skill="wrapper-smith",
                summary=f"fetch subdir escapes through a symlink: '{subdir}'",
                errors=[str(rtl_root)],
            )
        if not rtl_root.is_dir():
            return SkillResult(
                ok=False,
                skill="wrapper-smith",
                summary=f"fetch subdir does not exist: '{subdir}'",
                errors=[str(rtl_root)],
            )
        provenance = {"url": url, "commit": commit, "ref": ref,
                      "license": lic, "fetched_into": str(dst),
                      "rtl_root": str(rtl_root)}
        dump_json(provenance, rtl_root / ".wrapper-smith-provenance.json")

        errors = []
        if "GPL" in lic["guess"]:
            errors.append(f"license {lic['guess']} — review compatibility "
                          f"before vendoring (repo tree is Apache/SHL)")
        return SkillResult(
            ok=True, skill="wrapper-smith",
            summary=f"fetched {name} @ {commit[:12]} (license: {lic['guess']}) "
                    f"-> {rtl_root}; next: wrapper-smith analyze {rtl_root} "
                    f"--top <module>",
            details={"provenance": provenance, "rtl_root": str(rtl_root)},
            errors=errors,
        )

    # ── scaffold ─────────────────────────────────────────────────────

    # Families with a proven in-tree wrapper: scaffolding CLONES it (rename +
    # provenance + TODO banner) — the same starting point a human would take,
    # and diff-identical for the regen test. ahb_split renders its real
    # template instead (no proven wrapper yet — the Hazard3 family).
    _PROVEN = {
        "wishbone_unified": ("serv_sci.sv", "serv"),
        "wishbone_split": ("fazyrv_sci.sv", "fazyrv"),
        "reqgnt_split": ("ibex_sci.sv", "ibex"),
        "unified_native": ("picorv32_sci.sv", "picorv32"),
        "reqrsp_split": ("snitch_sci.sv", "snitch"),
        "axi4_unified": ("cva6_sci.sv", "cva6"),
        "axi4_struct": ("cva6_sci.sv", "cva6"),
        "tilelink_unified": ("rocket_sci.sv", "rocket"),
    }
    # Which proven cpu_subsystem.sv.tpl branch to clone per family (shape).
    _BRANCH_SOURCE = {
        "wishbone_unified": "serv",
        "wishbone_split": "fazyrv",
        "reqgnt_split": "ibex",
        "unified_native": "picorv32",
        "reqrsp_split": "snitch",
        "axi4_unified": "cva6",
        "axi4_struct": "cva6",
        "tilelink_unified": "rocket",
        "ahb_split": "snitch",  # split ports + BOOT_ADDR param idiom
    }

    def _render_wrapper(self, core: str, family: str, analysis: Dict[str, Any]) -> str:
        todos = "".join(f"//   TODO(wrapper-smith): {t}\n" for t in analysis.get("todos", []))
        banner = (
            f"// GENERATED by wrapper-smith for core '{core}' "
            f"(family {family}, top {analysis['top']}).\n"
            f"// Agent work queue from the analysis:\n{todos}"
            f"// Verify with: python3 -m harness tb-smith generate {core} && run {core}\n\n"
        )
        if family == "ahb_split":
            from mako.template import Template
            tpl = (Path(__file__).resolve().parent.parent / "templates" /
                   "wrapper" / "ahb_split.sv.mako").read_text()
            return str(Template(tpl).render(
                core=core, top=analysis["top"],
                analysis_name=Path(analysis.get("top_file", "analysis")).name))
        src_name, src_core = self._PROVEN[family]
        src = (self.repo_root / "hw" / "sci" / src_name).read_text()
        out = src.replace(f"{src_core}_sci", f"{core}_sci")
        out = re.sub(rf"\b{src_core}\b", core, out)
        # mark the core instantiation as the primary fill site
        out = re.sub(r"(\n\s*)(\S+\s+#\(|\S+\s+i_core|\S+\s+i_tile|\S+\s+i_cva6)",
                     rf"\1// TODO(wrapper-smith): replace this instantiation with"
                     rf" {analysis['top']} (see analysis ports)\1\2",
                     out, count=1)
        return banner + out

    def _extract_branch(self, source_core: str) -> Optional[str]:
        tpl = (self.repo_root / "hw" / "core-v-mini-mcu" /
               "cpu_subsystem.sv.tpl").read_text()
        m = re.search(
            rf'(      % elif group\.name == "{source_core}":\n)(.*?)(?=\n      % elif|\n## wrapper-smith:insert-here)',
            tpl, re.S)
        return (m.group(1) + m.group(2)) if m else None

    def scaffold(self, core: str, analysis: Path, apply: bool = False,
                 vendor_from: Optional[Path] = None,
                 family_override: Optional[str] = None) -> SkillResult:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", core):
            return SkillResult(
                ok=False,
                skill="wrapper-smith",
                summary=f"invalid core identifier '{core}'",
                errors=["use lowercase letters, digits, and underscores; start with a letter"],
            )
        a = json.loads(Path(analysis).read_text())
        family = family_override or a["classification"]["family"]
        if family == "unknown":
            return SkillResult(
                ok=False, skill="wrapper-smith",
                summary="analysis classified the bus as UNKNOWN — rerun with "
                        "--family <f> (see `wrapper-smith families`)",
                errors=[json.dumps(a["classification"], indent=1)])
        if family not in FAMILIES:
            return SkillResult(ok=False, skill="wrapper-smith",
                               summary=f"unknown family '{family}'",
                               errors=[f"valid: {sorted(FAMILIES)}"])
        stage = self.repo_root / "build" / "wrapper_smith" / core / "stage"
        # A stage is a deterministic snapshot, not an incremental worktree.
        # Remove artifacts from an older scaffold schema (notably the former
        # mosaic_config.py SCI_CORES patch) before writing this run.
        if stage.exists():
            shutil.rmtree(stage)
        written: List[str] = []
        edited: List[str] = []
        skipped: List[str] = []
        todos: List[Dict[str, str]] = [{"file": f"hw/sci/{core}_sci.sv",
                                        "tag": "TODO(wrapper-smith)", "text": t}
                                       for t in a.get("todos", [])]

        def _stage(rel: str, content: str):
            p = stage / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            written.append(rel)

        # (3) wrapper
        wrapper_rel = f"hw/sci/{core}_sci.sv"
        if (self.repo_root / wrapper_rel).exists():
            skipped.append(wrapper_rel)
        else:
            _stage(wrapper_rel, self._render_wrapper(core, family, a))

        # (1) x-heep's runtime CPU constructor still gates names separately.
        cpu_rel = "util/mosaic_gen/cpu/cpu.py"
        cpu_text = (self.repo_root / cpu_rel).read_text()
        if re.search(rf'"{re.escape(core)}"', cpu_text):
            skipped.append(f"{cpu_rel} (AVAILABLE_CPUS)")
        else:
            cpu_new, count = re.subn(
                r"(AVAILABLE_CPUS = \{\n)",
                rf'\1        "{core}",\n',
                cpu_text,
                count=1,
            )
            if count != 1:
                return SkillResult(
                    ok=False,
                    skill="wrapper-smith",
                    summary=f"could not locate AVAILABLE_CPUS in {cpu_rel}",
                    errors=[cpu_rel],
                )
            _stage(cpu_rel, cpu_new)
            edited.append(f"{cpu_rel} (AVAILABLE_CPUS)")

        # (2) CORE_SPECS is authoritative. SCI_CORES/VALID_CORE_IPS are derived
        # from it; mosaic_config imports them and must never be patched.
        registry_rel = "util/mosaic_gen/core_registry.py"
        registry_text = (self.repo_root / registry_rel).read_text()
        try:
            registered = _mapping_has_string_key(registry_text, "CORE_SPECS", core)
            registry_new = (
                registry_text
                if registered
                else _insert_core_spec(registry_text, core, family)
            )
        except (SyntaxError, ValueError) as error:
            return SkillResult(
                ok=False,
                skill="wrapper-smith",
                summary=f"could not update CORE_SPECS in {registry_rel}",
                errors=[str(error)],
            )
        if registered:
            skipped.append(f"{registry_rel} (CORE_SPECS)")
        else:
            _stage(registry_rel, registry_new)
            edited.append(f"{registry_rel} (CORE_SPECS)")
            todos.append(
                {
                    "file": registry_rel,
                    "tag": "review",
                    "text": f"review {core} ISA, parameters and capabilities; "
                    "the scaffold uses conservative RV32I defaults",
                }
            )

        # (4) cpu_subsystem.sv.tpl branch at the anchor
        tpl_rel = "hw/core-v-mini-mcu/cpu_subsystem.sv.tpl"
        tpl_text = (self.repo_root / tpl_rel).read_text()
        guard = f"## wrapper-smith:begin {core}"
        if f'group.name == "{core}"' in tpl_text or guard in tpl_text:
            skipped.append(tpl_rel)
        else:
            branch_src = self._BRANCH_SOURCE[family]
            branch = self._extract_branch(branch_src)
            if branch is None:
                return SkillResult(ok=False, skill="wrapper-smith",
                                   summary=f"could not extract the {branch_src} "
                                           f"branch from {tpl_rel}",
                                   errors=[tpl_rel])
            branch = branch.replace(f"{branch_src}_sci", f"{core}_sci")
            branch = re.sub(rf'"{branch_src}"', f'"{core}"', branch)
            anchor = "## wrapper-smith:insert-here"
            insert = (f"{guard} (family {family}; clone of the {branch_src} "
                      f"branch — review port wiring)\n{branch}\n"
                      f"## wrapper-smith:end {core}\n")
            new = tpl_text.replace(anchor, insert + anchor, 1)
            _stage(tpl_rel, new)
            edited.append(tpl_rel)
            todos.append({"file": tpl_rel, "tag": "review",
                          "text": f"branch cloned from {branch_src} — check "
                                  f"params/ports match {core}_sci"})

        # (5) sci.core files list + depend edge. The depend edge is added ONLY
        # when the vendor .core will exist (vendor_from now, or already in the
        # tree) — a dangling VLNV breaks every fusesoc invocation.
        will_have_vendor_core = bool(vendor_from) or \
            (self.repo_root / "hw" / "vendor" / "mosaic" / core /
             f"{core}.core").exists()
        score_rel = "hw/sci/sci.core"
        score_text = (self.repo_root / score_rel).read_text()
        score_new = score_text
        score_changed = False
        if f"{core}_sci.sv" in score_text:
            skipped.append(f"{score_rel} (file)")
        else:
            score_new, n = re.subn(r"(\n    file_type: systemVerilogSource)",
                                   rf"\n    - {core}_sci.sv\1", score_new,
                                   count=1)
            score_changed = n == 1
        if f"mosaic:ip:{core}" in score_new:
            skipped.append(f"{score_rel} (depend)")
        elif will_have_vendor_core:
            score_new, n = re.subn(r"(\n    files:)",
                                   rf"\n      - mosaic:ip:{core}\1",
                                   score_new, count=1)
            score_changed = score_changed or n == 1
        else:
            todos.append({"file": score_rel, "tag": "pending",
                          "text": f"add '- mosaic:ip:{core}' to depend: once "
                                  f"the vendor .core exists (rerun scaffold "
                                  f"with --vendor-from)"})
        if score_changed:
            _stage(score_rel, score_new)
            edited.append(score_rel)

        # (6) gen_filelist.py visibility (simple vendor dir -> -y list)
        gfl_rel = "tb/mosaic_soc/gen_filelist.py"
        gfl_text = (self.repo_root / gfl_rel).read_text()
        vendor_rel = f"hw/vendor/mosaic/{core}"
        if core in gfl_text:
            skipped.append(gfl_rel)
        else:
            new, n = re.subn(
                r'(for y in \[\s*"hw/sci",)',
                rf'\1\n    "{vendor_rel}/rtl",\n    "{vendor_rel}",',
                gfl_text,
                count=1,
            )
            if n == 1:
                _stage(gfl_rel, new)
                edited.append(gfl_rel)
                todos.append({"file": gfl_rel, "tag": "review",
                              "text": "-y works for module-per-file trees; "
                                      "packages/filename!=module need explicit "
                                      "entries (see the snitch/cva6 blocks)"})

        # (7) vendor copy + .core stub (provenance from `wrapper-smith fetch`
        # is folded into the .core header when present)
        if vendor_from:
            vsrc = Path(vendor_from)
            prov = None
            prov_p = vsrc / ".wrapper-smith-provenance.json"
            if prov_p.exists():
                prov = json.loads(prov_p.read_text())
            for f in sorted(vsrc.rglob("*")):
                if f.is_file() and f.suffix in (".v", ".sv", ".svh", ".vh", ".f"):
                    rel = f"{vendor_rel}/rtl/{f.relative_to(vsrc)}"
                    _stage(rel, f.read_text(errors="replace"))
            if prov:
                prov_hdr = (
                    f"# Vendored by wrapper-smith fetch+scaffold from:\n"
                    f"#   {prov['url']} @ {prov['commit']}\n"
                    f"#   License: {prov['license']['guess']}"
                    f" (upstream {prov['license']['file'] or 'file MISSING'})\n")
            else:
                prov_hdr = ("# TODO(wrapper-smith): record upstream URL + "
                            "commit + license (use `wrapper-smith fetch` to "
                            "automate this)\n")
                todos.append({"file": f"{vendor_rel}/{core}.core",
                              "tag": "review",
                              "text": "record upstream URL + commit + license"})
            _stage(f"{vendor_rel}/{core}.core",
                   "CAPI=2:\n\n"
                   f"name: mosaic:ip:{core}\n"
                   f"description: \"{core} — wrapper-smith vendored\"\n\n"
                   + prov_hdr + "\n"
                   "filesets:\n  core:\n    files:\n"
                   + "".join(f"      - rtl/{f.relative_to(vsrc)}\n"
                             for f in sorted(vsrc.rglob("*"))
                             if f.is_file() and f.suffix in (".v", ".sv"))
                   + "    file_type: systemVerilogSource\n\n"
                     "targets:\n  default:\n    filesets:\n      - core\n")
        elif not will_have_vendor_core:
            todos.append({"file": vendor_rel, "tag": "pending",
                          "text": "vendor the RTL (rerun with --vendor-from DIR "
                                  "or copy by hand + write the .core)"})
        else:
            skipped.append(f"{vendor_rel} (existing vendor core)")

        # (8) bring-up config
        cfg_rel = f"configs/mosaic_{core}.yaml"
        if (self.repo_root / cfg_rel).exists():
            skipped.append(cfg_rel)
        else:
            from .config_author import ConfigAuthor
            r = ConfigAuthor(self.repo_root).wake_demo_config(
                core, output_path=stage / cfg_rel)
            if r.ok:
                written.append(cfg_rel)
            else:
                # core not in registries YET (expected pre-apply): stage the
                # canonical shape directly
                cfg = {
                    "soc": {
                        "name": f"mosaic_{core}",
                        "pdk": "gf180mcu",
                        "target": "rtl",
                        "cores": [
                            {
                                "ip": "cv32e20",
                                "isa": "rv32emc",
                                "count": 1,
                                "role": "titan",
                            },
                            {
                                "ip": core,
                                "isa": "rv32i",
                                "count": 1,
                                "role": "atlas",
                                "boot_addr": 0x1000,
                            },
                            {
                                "ip": core,
                                "isa": "rv32i",
                                "count": 1,
                                "role": "nano",
                                "boot_addr": 0x2000,
                            },
                        ],
                        "memory": {"sram_kb": 32, "boot_rom_kb": 2},
                        "bus": "obi",
                        "scheduler": {"tdu": True, "mode": "dynamic"},
                        "peripherals": ["uart", "gpio", "timer", "spi"],
                    }
                }
                _stage(cfg_rel, yaml.safe_dump(cfg, sort_keys=False))

        # ── apply ──
        fusesoc_smoke: Optional[Dict[str, Any]] = None
        if apply:
            for rel in written + [e.split(" (")[0] for e in edited]:
                src_p = stage / rel
                if src_p.exists():
                    dst = self.repo_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src_p, dst)
            # post-apply smoke: the FuseSoC graph must still resolve (a bad
            # .core or a dangling depend edge breaks EVERY flow). Full setup:
            # register generators + filelist, ~30-60 s. The boot_rom generator
            # inside it needs RISCV_XHEEP/COMPILER_PREFIX; derive them from
            # RISCV_TC exactly as the tb runners do (the sim shell sets all
            # three). No machine-specific default: an absent toolchain makes
            # the smoke fail and say so.
            import os
            from ..toolchain import riscv_env
            env = {**os.environ, **riscv_env(os.environ)}
            smoke = run_cmd(["bash", "scripts/fusesoc-setup.sh"],
                            cwd=self.repo_root, timeout=600, env=env)
            fusesoc_smoke = {"ok": smoke.returncode == 0,
                             "tail": (smoke.stdout + smoke.stderr)[-1200:]}
            if smoke.returncode != 0:
                return SkillResult(
                    ok=False, skill="wrapper-smith",
                    summary=f"scaffolded '{core}' but the FuseSoC graph no "
                            f"longer resolves — inspect the staged .core / "
                            f"sci.core depend edge",
                    details={"stage": str(stage), "written": written,
                             "edited": edited, "fusesoc_smoke": fusesoc_smoke},
                    errors=[fusesoc_smoke["tail"][-400:]])

        mode = "APPLIED to tree" if apply else f"staged (dry-run) in {stage}"
        return SkillResult(
            ok=True, skill="wrapper-smith",
            summary=f"scaffolded '{core}' as {family}: "
                    f"{len(written)} written, {len(edited)} edited, "
                    f"{len(skipped)} already-present — {mode}; "
                    f"{len(todos)} TODO(s) for the agent",
            details={"stage": str(stage), "written": written, "edited": edited,
                     "skipped_existing": skipped, "todos": todos,
                     "family": family, "fusesoc_smoke": fusesoc_smoke},
        )

    def families(self) -> SkillResult:
        rows = {f: {"description": s["description"],
                    "port_shape": s["port_shape"],
                    "proven_by": s["proven_by"]}
                for f, s in FAMILIES.items()}
        return SkillResult(ok=True, skill="wrapper-smith",
                           summary=f"{len(FAMILIES)} protocol families",
                           details={"families": rows})
