"""tb-matrix skill — combination-coverage testing of the SoC integration space.

tb-smith proves ONE core; tb-matrix proves the SPACE. Every axis of the
integration matrix (which cores, in which roles, at which counts, on which
fabric, with which ISA/parameter variants, scheduler modes, memory sizes and
peripheral sets) is derived live from util/xheep_gen/core_registry.py — a core
added through wrapper-smith automatically enters the matrix with no edits here.

Combination strategy (full cartesian product is astronomically large):

  pairwise    a deterministic greedy covering array: every legal VALUE PAIR of
              every two axes appears in at least one generated config.  Pairs
              that no legal config can realize are reported as *blocked* with
              the constraint that blocks them — never silently dropped.
  boundary    a curated set for the expensive sim tier: every core as a woken
              worker, every fabric x port-shape class, SMP / worker-only /
              mixed-ABI / max-count / alt-parameter corners.

Tiers (each config passes through the same deterministic gates used by the
proven demos — nothing here invents a new pass criterion):

  validate    in-process validate_soc_config (the single schema+topology
              oracle) — milliseconds per config, run the whole array.
  render      flow-runner mosaic-gen-config: full mcu-gen RTL + software
              generation must succeed.
  sim         flow-runner tb-soc-generic: tb/mosaic_soc/run_generic.sh — the
              topology-generic liveness TB where EVERY configured hart must
              report before EXIT SUCCESS.

Results are persisted incrementally to build/tb_matrix/report.json; --resume
skips configs that already passed, so a campaign survives interruption.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core import SkillResult, REPO_ROOT, dump_yaml, log
from util.xheep_gen.core_registry import (
    CORE_SPECS,
    VALID_BUS,
    VALID_PERIPHERALS,
    VALID_SCHED_MODES,
    validate_soc_config,
)

# ── Axis derivation (single-sourced from the registry) ──────────────

_PERIPH_SETS = {
    "minimal": ("uart",),
    "standard": ("uart", "gpio", "timer", "spi"),
    "full": tuple(sorted(VALID_PERIPHERALS)),
}

# Values every axis can take. Cores/buses/modes come from the registry so the
# matrix grows automatically with the platform.
def derive_axes() -> Dict[str, List[Any]]:
    cores = sorted(CORE_SPECS)
    return {
        "shape": ["standard", "multi_titan", "worker_only"],
        "titan_ip": cores,
        "worker_ip": cores,
        "worker_role": ["atlas", "nano"],
        "worker_count": [1, 2, 4],
        "second_worker": ["none"] + cores,
        "variant": ["base", "alt"],
        "bus": sorted(VALID_BUS),
        "sched_mode": sorted(VALID_SCHED_MODES),
        "sram_kb": [32, 64],
        "periph_set": sorted(_PERIPH_SETS),
    }


def _isa(ip: str, variant: str) -> str:
    """base = simplest ISA the core supports; alt = richest."""
    isas = CORE_SPECS[ip].isas
    if variant == "alt":
        return sorted(isas, key=lambda s: (-len(s), s))[0]
    return sorted(isas, key=lambda s: (len(s), s))[0]


def contract_params(ip: str, isa: str) -> Dict[str, Any]:
    """Parameters the registry's cross-field ISA contracts require for (ip, isa).

    validate_soc_config rejects entries whose parameters build a different
    instruction set than the declared ISA — this is the one place that
    knowledge is encoded for synthesis, mirroring core_registry's rules.
    """
    ext = isa[4:]
    has_c, has_m = "c" in ext, "m" in ext
    is_e = ext.startswith("e")
    p: Dict[str, Any] = {}
    if ip == "fazyrv":
        p["rvc"] = "COMB" if has_c else "NONE"
    elif ip in {"serv", "qerv"}:
        p["compressed"] = int(has_c)
        p["mdu"] = int(has_m)
    elif ip == "picorv32":
        p["compressed"] = int(has_c)
        p["mul"] = int(has_m)
        p["div"] = int(has_m)
    elif ip == "ibex":
        p["rv32e"] = int(is_e)
    elif ip == "cv32e20":
        p["rv32e"] = int(is_e)
        p["rv32m"] = "RV32MFast" if has_m else "RV32MNone"
    return p


# Optional-feature parameters exercised by variant=alt (choice-backed values
# only; the ISA-coupled parameters always come from contract_params).
_ALT_PARAMS: Dict[str, Dict[str, Any]] = {
    "fazyrv": {"chunksize": 4},
    "picorv32": {"barrel_shifter": 1, "counters": 1},
    "ibex": {"mhpmcounters": 4},
    "cv32e40p": {"num_mhpmcounters": 4},
    "cv32e40px": {"num_mhpmcounters": 4},
    "cv32e40x": {"num_mhpmcounters": 4},
}


def _core_entry(ip: str, role: str, variant: str, count: int = 1,
                boot_addr: Optional[int] = None) -> Dict[str, Any]:
    isa = _isa(ip, variant)
    entry: Dict[str, Any] = {"ip": ip, "isa": isa, "count": count, "role": role}
    if boot_addr is not None:
        entry["boot_addr"] = boot_addr
    entry.update(contract_params(ip, isa))
    if variant == "alt":
        entry.update(_ALT_PARAMS.get(ip, {}))
    return entry


# ── Legality (constraints the covering array must honor) ────────────

def conflict(assign: Dict[str, Any]) -> Optional[str]:
    """Definite conflicts in a (possibly partial) assignment.

    Only rules that hold regardless of the unassigned axes belong here; the
    final word on any complete config is always validate_soc_config.
    """
    shape = assign.get("shape")
    titan = assign.get("titan_ip")
    if shape == "multi_titan" and titan is not None:
        if titan in {"rocket", "boom"}:
            return "rocket/boom may be a TITAN only as one leading hart"
        if "mhartid" not in CORE_SPECS[titan].capabilities:
            return (f"{titan} lacks mhartid — SMP harts sharing one "
                    f"free-running image cannot self-identify")
    worker = assign.get("worker_ip")
    second = assign.get("second_worker")
    if worker is not None and second is not None and second == worker:
        return "second worker must differ from the first (heterogeneity axis)"
    return None


# Axes that do not apply to a shape (their value pairs are *blocked*, not
# uncovered, when the shape makes them meaningless).
_INAPPLICABLE = {
    "multi_titan": {"worker_ip", "worker_role", "worker_count", "second_worker"},
    "worker_only": {"titan_ip"},
}


def canonical(assign: Dict[str, Any]) -> Dict[str, Any]:
    drop = _INAPPLICABLE.get(assign.get("shape") or "", set())
    return {k: v for k, v in assign.items() if k not in drop}


# ── Pairwise covering array (deterministic greedy) ───────────────────

def pairwise_rows(axes: Dict[str, List[Any]]) -> Tuple[
        List[Dict[str, Any]], List[Tuple[Tuple[str, Any, str, Any], str]]]:
    """Greedy covering array over `axes` honoring conflict()/canonical().

    Returns (rows, blocked) where every legal canonical value pair appears in
    at least one row, and `blocked` lists the pairs no legal config can
    realize, each with its reason.
    """
    names = list(axes)
    pending: set = set()
    blocked: List[Tuple[Tuple[str, Any, str, Any], str]] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for va in axes[a]:
                for vb in axes[b]:
                    reason = conflict({a: va, b: vb})
                    if reason is None and a == "shape":
                        if b in _INAPPLICABLE.get(va, set()):
                            reason = f"axis {b} does not apply to shape {va}"
                    if reason:
                        blocked.append(((a, va, b, vb), reason))
                    else:
                        pending.add((a, va, b, vb))

    rows: List[Dict[str, Any]] = []

    def _covered_by(row: Dict[str, Any]) -> set:
        can = canonical(row)
        return {p for p in pending
                if p[0] in can and p[2] in can
                and can[p[0]] == p[1] and can[p[2]] == p[3]}

    while pending:
        seed = min(pending, key=lambda p: (p[0], str(p[1]), p[2], str(p[3])))
        a, va, b, vb = seed
        row: Dict[str, Any] = {a: va, b: vb}
        dead = False
        for n in names:
            if n in row:
                continue
            best, best_gain = None, -1
            for v in axes[n]:
                trial = dict(row)
                trial[n] = v
                if conflict(trial):
                    continue
                trial_can = canonical(trial)
                gain = sum(1 for p in pending
                           if p[0] in trial_can and p[2] in trial_can
                           and trial_can[p[0]] == p[1] and trial_can[p[2]] == p[3])
                if gain > best_gain:
                    best, best_gain = v, gain
            if best is None:
                dead = True
                break
            row[n] = best
        if dead or conflict(row):
            pending.discard(seed)
            blocked.append((seed, conflict(row) or "no legal completion"))
            continue
        covered = _covered_by(row)
        if seed not in covered:
            # the seed pair itself was canonicalized away (shape-inapplicable)
            pending.discard(seed)
            blocked.append((seed, "pair not realizable in any canonical config"))
            continue
        rows.append(row)
        pending -= covered
    return rows, blocked


# ── Config synthesis ─────────────────────────────────────────────────

_WORKER_SLOTS = (0x1000, 0x2000)


def synth_config(assign: Dict[str, Any]) -> Dict[str, Any]:
    """Turn an axis assignment into a concrete mosaic.yaml-shaped dict."""
    shape = assign["shape"]
    variant = assign["variant"]
    cores: List[Dict[str, Any]] = []

    if shape == "multi_titan":
        titan = assign["titan_ip"]
        partner = "cv32e40x" if titan == "cv32e20" else "cv32e20"
        cores.append(_core_entry(titan, "titan", variant, count=2))
        cores.append(_core_entry(partner, "titan", "base", count=2))
    else:
        if shape == "standard":
            titan = assign["titan_ip"]
            boot = 0x180 if titan in {"rocket", "boom"} else None
            cores.append(_core_entry(titan, "titan", variant, boot_addr=boot))
        cores.append(_core_entry(
            assign["worker_ip"], assign["worker_role"], variant,
            count=assign["worker_count"], boot_addr=_WORKER_SLOTS[0]))
        second = assign["second_worker"]
        if second != "none":
            other_role = "nano" if assign["worker_role"] == "atlas" else "atlas"
            cores.append(_core_entry(second, other_role, "base",
                                     boot_addr=_WORKER_SLOTS[1]))

    name = "mx_" + hashlib.sha1(
        json.dumps(canonical(assign), sort_keys=True, default=str).encode()
    ).hexdigest()[:10]
    return {"soc": {
        "name": name,
        "pdk": "gf180mcu",
        "profile": "testbench",
        "cores": cores,
        "memory": {"sram_kb": assign["sram_kb"], "boot_rom_kb": 2},
        "bus": assign["bus"],
        "scheduler": {"tdu": True, "mode": assign["sched_mode"]},
        "peripherals": list(_PERIPH_SETS[assign["periph_set"]]),
    }}


# ── Plans per tier ───────────────────────────────────────────────────

_DEFAULTS = {
    "shape": "standard", "titan_ip": "cv32e20", "worker_ip": "serv",
    "worker_role": "nano", "worker_count": 1, "second_worker": "none",
    "variant": "base", "bus": "obi", "sched_mode": "dynamic",
    "sram_kb": 32, "periph_set": "standard",
}


def _row(**over: Any) -> Dict[str, Any]:
    row = dict(_DEFAULTS)
    row.update(over)
    return row


def sim_boundary_rows() -> List[Dict[str, Any]]:
    """Curated corner set for the expensive sim tier."""
    rows: List[Dict[str, Any]] = []
    # 1. every registry core once as the woken worker behind a cv32e20 TITAN
    for ip in sorted(CORE_SPECS):
        rows.append(_row(worker_ip=ip,
                         second_worker="none" if ip != "serv" else "fazyrv"))
    # 2. non-OBI fabrics x port-shape classes (split / unified / 64-bit bridge)
    for bus in ("log", "floonoc"):
        for ip in ("ibex", "serv", "rocket"):
            rows.append(_row(bus=bus, worker_ip=ip))
    # 3. topology shapes
    rows.append(_row(shape="multi_titan", titan_ip="cv32e40x"))
    rows.append(_row(shape="worker_only", worker_ip="serv",
                     second_worker="fazyrv", worker_count=1))
    # 4. variants: alt ISA/params + a mixed-ABI image set (rv32e titan,
    #    rv32i + rv32imc workers on distinct slots)
    rows.append(_row(worker_ip="picorv32", variant="alt"))
    rows.append(_row(worker_ip="fazyrv", variant="alt"))
    rows.append(_row(variant="alt", worker_ip="serv", second_worker="ibex"))
    # 5. capacity + configuration corners
    rows.append(_row(worker_ip="serv", worker_count=4, second_worker="fazyrv"))
    rows.append(_row(sram_kb=64))
    rows.append(_row(periph_set="full"))
    rows.append(_row(sched_mode="static"))
    rows.append(_row(sched_mode="power-aware"))
    # dedupe while preserving order
    seen, unique = set(), []
    for row in rows:
        key = json.dumps(canonical(row), sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


# ── The skill ────────────────────────────────────────────────────────

_TIERS = ("validate", "render", "sim")
_TIER_FLOW = {"render": "mosaic-gen-config", "sim": "tb-soc-generic"}


class TbMatrix:
    """Skill: enumerate + gate the SoC integration combination space."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or REPO_ROOT
        self.out_dir = self.repo_root / "build" / "tb_matrix"

    # -- planning -----------------------------------------------------

    def _plan_rows(self, tier: str) -> Tuple[
            List[Dict[str, Any]], List[Tuple[Tuple[str, Any, str, Any], str]]]:
        if tier == "sim":
            return sim_boundary_rows(), []
        return pairwise_rows(derive_axes())

    def axes(self) -> SkillResult:
        axes = derive_axes()
        return SkillResult(
            ok=True, skill="tb-matrix",
            summary=(f"{len(axes)} axes over {len(CORE_SPECS)} registry cores; "
                     "values derived live from core_registry"),
            details={"axes": axes,
                     "periph_sets": {k: list(v) for k, v in _PERIPH_SETS.items()}},
        )

    def plan(self, tier: str = "render") -> SkillResult:
        if tier not in _TIERS:
            return SkillResult(ok=False, skill="tb-matrix",
                               summary=f"unknown tier '{tier}'",
                               errors=[f"tier must be one of {_TIERS}"])
        rows, blocked = self._plan_rows(tier)
        configs, invalid = [], []
        for row in rows:
            cfg = synth_config(row)
            errors = validate_soc_config(cfg)
            entry = {"name": cfg["soc"]["name"], "assign": canonical(row)}
            if errors:
                entry["errors"] = errors[:3]
                invalid.append(entry)
            else:
                configs.append(entry)
        return SkillResult(
            ok=not invalid, skill="tb-matrix",
            summary=(f"tier {tier}: {len(configs)} valid configs planned, "
                     f"{len(invalid)} rejected by the schema oracle, "
                     f"{len(blocked)} pairs blocked by constraints"),
            details={"tier": tier, "configs": configs, "invalid": invalid,
                     "blocked": [{"pair": list(p), "reason": r}
                                 for p, r in blocked[:200]]},
            errors=[f"{e['name']}: {e['errors'][0]}" for e in invalid[:10]],
        )

    # -- execution ----------------------------------------------------

    def _load_report(self) -> Dict[str, Any]:
        path = self.out_dir / "report.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                log.warning("report.json unreadable — starting fresh")
        return {"tiers": {}}

    def _save_report(self, report: Dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str))

    def run(self, tier: str = "validate", limit: Optional[int] = None,
            resume: bool = True,
            on_output: Optional[Callable[[str], None]] = None) -> SkillResult:
        if tier not in _TIERS:
            return SkillResult(ok=False, skill="tb-matrix",
                               summary=f"unknown tier '{tier}'",
                               errors=[f"tier must be one of {_TIERS}"])
        rows, blocked = self._plan_rows(tier)
        report = self._load_report()
        tier_results: Dict[str, Any] = report["tiers"].setdefault(tier, {})
        cfg_dir = self.out_dir / "configs"
        cfg_dir.mkdir(parents=True, exist_ok=True)

        ran = skipped = 0
        statuses: Dict[str, int] = {}
        for row in rows:
            cfg = synth_config(row)
            name = cfg["soc"]["name"]
            if resume and tier_results.get(name, {}).get("status") == "pass":
                skipped += 1
                continue
            if limit is not None and ran >= limit:
                break
            ran += 1
            cfg_path = cfg_dir / f"{name}.yaml"
            dump_yaml(cfg, cfg_path)
            entry: Dict[str, Any] = {"assign": canonical(row),
                                     "config": str(cfg_path.relative_to(self.repo_root)),
                                     "when": time.strftime("%Y-%m-%d %H:%M:%S")}

            errors = validate_soc_config(cfg)
            if errors:
                entry.update(status="invalid", errors=errors[:5])
            elif tier == "validate":
                entry["status"] = "pass"
            else:
                from .flow_runner import FlowRunner
                started = time.monotonic()
                result = FlowRunner(self.repo_root).run(
                    _TIER_FLOW[tier],
                    config=str(cfg_path.relative_to(self.repo_root)),
                    on_output=on_output)
                entry.update(
                    status="pass" if result.ok else "fail",
                    elapsed_s=round(time.monotonic() - started, 1),
                    summary=result.summary)
                if not result.ok:
                    entry["errors"] = result.errors[:5]
            tier_results[name] = entry
            statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
            self._save_report(report)  # crash-safe incremental persistence
            log.info(f"[{tier}] {name}: {entry['status']}")

        total_pass = sum(1 for e in tier_results.values()
                         if e.get("status") == "pass")
        failed = {n: e for n, e in tier_results.items()
                  if e.get("status") in {"fail", "invalid"}}
        ok = not statuses.get("fail") and not statuses.get("invalid")
        return SkillResult(
            ok=ok, skill="tb-matrix",
            summary=(f"tier {tier}: ran {ran} ({statuses}), resumed past "
                     f"{skipped} already-passing; cumulative {total_pass}"
                     f"/{len(rows)} pass, {len(blocked)} pairs blocked"),
            details={"tier": tier, "ran": ran, "skipped": skipped,
                     "statuses": statuses, "cumulative_pass": total_pass,
                     "planned": len(rows),
                     "failed": {n: e.get("errors", [e.get("summary", "?")])
                                for n, e in sorted(failed.items())[:20]},
                     "report": str((self.out_dir / "report.json")
                                   .relative_to(self.repo_root))},
            errors=[f"{n}: {e.get('errors', ['?'])[0]}"
                    for n, e in sorted(failed.items())[:10]],
        )

    def report(self) -> SkillResult:
        report = self._load_report()
        if not report["tiers"]:
            return SkillResult(ok=False, skill="tb-matrix",
                               summary="no tb-matrix runs recorded yet — "
                                       "run `tb-matrix run --tier validate` first",
                               errors=["build/tb_matrix/report.json missing/empty"])
        tiers: Dict[str, Any] = {}
        for tier, entries in sorted(report["tiers"].items()):
            counts: Dict[str, int] = {}
            for entry in entries.values():
                status = entry.get("status", "?")
                counts[status] = counts.get(status, 0) + 1
            tiers[tier] = {
                "counts": counts,
                "failed": sorted(n for n, e in entries.items()
                                 if e.get("status") in {"fail", "invalid"}),
            }
        worst = [n for t in tiers.values() for n in t["failed"]]
        return SkillResult(
            ok=not worst, skill="tb-matrix",
            summary="; ".join(f"{t}: {v['counts']}" for t, v in tiers.items()),
            details={"tiers": tiers,
                     "report": str((self.out_dir / "report.json")
                                   .relative_to(self.repo_root))},
            errors=[f"failing: {', '.join(worst[:15])}"] if worst else [],
        )
