#!/usr/bin/env python3
"""mosaic — CLI entry point for the MOSAIC-SoC agentic harness.

Based on oh-my-pi, adapted for MOSAIC-SoC EDA flows.

Usage:
    python -m harness <skill> <command> [args...]
    python -m harness config-author generate --name my_soc ...
    python -m harness config-author validate configs/mosaic.yaml
    python -m harness config-author presets
    python -m harness flow-runner list
    python -m harness flow-runner run mosaic-gen
    python -m harness drc-triage analyze report.rpt
    python -m harness drc-triage scan build/reports/
    python -m harness doc-gen config mosaic.yaml
    python -m harness doc-gen memory-map
    python -m harness doc-gen dashboard
"""

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from .core import SkillResult


# Set by main() from --json: machine mode prints the raw SkillResult JSON
# (consumed by the .omp/tools shim and tests) and exits non-zero on failure.
from .physical.floorplan import DEFAULT_MARGIN_UM as _DEFAULT_MARGIN

_JSON_MODE = False
_PROGRESS_JSONL = False


class _StrictArgumentParser(argparse.ArgumentParser):
    """Disable long-option prefix matching in every nested subparser."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)


def _progress(kind: str, message: str, **details):
    if not _PROGRESS_JSONL:
        return
    print(
        json.dumps({"kind": kind, "message": message, "details": details}, default=str),
        file=sys.stderr,
        flush=True,
    )


def external_agent_prompt(driver: str, text: str, *, surface: str = "mcp") -> str:
    """The system framing handed to an external agent harness.

    Public because `demo/03_blocka_from_prompt.sh` drives `claude -p` with the
    very same string: a demo that invented its own prompt would be evidence
    about the demo, not about the surface a user actually gets from
    `mosaic agent`.

    `surface` exists because there are now genuinely two, and telling a model
    to use tools it has not been given is worse than either. `mosaic agent
    --driver claude` supplies the gated MCP server, so it gets "mcp". The demo
    supplies a Bash scoped to `python3 -m harness` and no MCP server, so it
    asks for "cli" — and is therefore a probe of prose-to-typed-flags
    translation, NOT of the enforcement path.
    """
    if driver not in {"claude", "omp"}:
        raise ValueError(driver)
    if surface == "cli" and driver == "omp":
        raise ValueError("the omp surface is MCP only")
    if surface == "cli":
        tool_clause = ("invoke python3 -m harness commands as separate visible "
                       "Bash tool calls, react to each JSON result")
    elif surface == "mcp":
        tool_clause = ("use the mosaic MCP tools for every MOSAIC action, "
                       "starting with request_scope, and react to each gate "
                       "result")
    else:
        raise ValueError(surface)
    return (
        "Act as the MOSAIC-SoC agent. Read the project .claude/skills cards, "
        f"make an explicit plan, {tool_clause}, and do not claim success "
        f"without deterministic evidence. User request: {text}"
    )


# The MCP tools, as Claude Code names them once the server is loaded. Passing
# these to --allowedTools is not belt-and-braces: without it the model keeps
# Bash, and `python3 -m harness ...` in a Bash call bypasses the session state
# entirely. An enforced driver that leaves the bypass open is not enforced.
def mcp_tool_names(server: Optional[str] = None, *,
                   plugin: Optional[str] = None) -> list[str]:
    """Our tools as Claude Code names them.

    The name depends on how the server was loaded: `mcp__<server>__<tool>`
    from an --mcp-config or .mcp.json key, `mcp__plugin_<plugin>_<server>__
    <tool>` from a plugin. Derived from the key actually written, never a
    hard-coded prefix that a renamed server would silently stop matching.
    """
    from .agent_tools import TOOL_SPECS
    from .mcp_server import SERVER_NAME

    server = server or SERVER_NAME
    prefix = (f"mcp__plugin_{plugin}_{server}__" if plugin
              else f"mcp__{server}__")
    return [prefix + spec.name for spec in TOOL_SPECS]


def _mcp_tool_allowlist(server: Optional[str] = None) -> list[str]:
    allowed = mcp_tool_names(server)
    if naja_scope_command():
        # Enumerated, never wildcarded: a wildcard would silently admit any
        # tool a future naja-scope release adds, including a writing one.
        allowed += [f"mcp__{NAJA_SCOPE_SERVER}__{name}"
                    for name in NAJA_SCOPE_TOOLS]
    return allowed


# naja-scope (najaeda/naja-scope) answers questions ABOUT a design: what drives
# a net, the hierarchy under an instance, the combinational cone behind a pin,
# the source line an object came from. This session needed all of those and had
# none of them -- "what drives spi_flash_cs_o?" is the open debugging question,
# and "what drives spi_flash_sd_io[0..3]?" is the remaining slew waiver, which
# was reasoned about from a text report.
#
# THE BOUNDARY THAT MATTERS. Our own server produces EVIDENCE: gated, scope-
# ceilinged, digest-bound. naja-scope produces FACTS. An agent that knows the
# connectivity has not proven anything, and nothing here may treat a naja-scope
# answer as a gate result. It is admitted as a second server precisely so the
# distinction is visible in the config rather than implied.
#
# `save_snapshot` is EXCLUDED. It writes to disk, and the whole point of
# --allowedTools is that the enforced session cannot write outside the gated
# tools. The load_* tools are permitted: they mutate the server's own memory,
# not the repository.
NAJA_SCOPE_SERVER = "naja-scope"
NAJA_SCOPE_TOOLS = (
    "status", "load_systemverilog", "load_verilog", "load_liberty",
    "load_primitives", "load_snapshot", "reset_universe", "resolve", "find",
    "get_hierarchy", "get_drivers", "get_loads", "trace_cone", "get_source",
    "get_module_card", "get_stats", "get_intent", "load_intent",
)
NAJA_SCOPE_EXCLUDED = ("save_snapshot",)


def naja_scope_command() -> Optional[str]:
    """The naja-scope entry point, if it is installed. Optional by design.

    Checks the project venv explicitly. PEP 668 blocks installing into a
    system interpreter, so naja-scope realistically lives in `.venv/bin` while
    the harness itself is often run by `python3` -- looking only alongside
    `sys.executable` finds nothing in the normal setup.
    """
    import shutil

    from .core import REPO_ROOT

    direct = shutil.which("naja-scope-mcp")
    if direct:
        return direct
    for candidate in (Path(sys.executable).parent / "naja-scope-mcp",
                      REPO_ROOT / ".venv" / "bin" / "naja-scope-mcp"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def write_mcp_config(request: str, repo_root: Path, target: Path) -> Path:
    """The --mcp-config file that binds the client to this session's ceiling.

    The request is passed as an argv element rather than interpolated into a
    shell string: it is user text, and it must not be able to become syntax.
    """
    from .mcp_server import SERVER_NAME

    servers = {
        SERVER_NAME: {
            "command": sys.executable,
            "args": ["-m", "harness", "mcp-server", "--request", request],
            "cwd": str(repo_root),
        }
    }
    scope = naja_scope_command()
    if scope:
        servers[NAJA_SCOPE_SERVER] = {"command": scope, "cwd": str(repo_root)}

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return target


def _external_agent_command(
    driver: str, binary: str, prompt: str, *, interactive: bool,
    mcp_config: Optional[Path] = None,
) -> list[str]:
    """Select a visible external-agent surface without fake slash commands."""

    if driver == "omp":
        # --no-tools removes every BUILT-IN tool (bash, edit, write, task...),
        # which is omp's equivalent of claude's --disallowedTools: the gated
        # MCP server from .omp/mcp.json is what remains. MOSAIC_REQUEST is
        # set by the caller and expanded into that server's env, which locks
        # its ceiling to this request.
        command = [binary, "--no-tools"]
        if not interactive:
            command += ["--mode", "json", "-p"]
        return command + [prompt]
    if driver == "claude":
        command = [binary]
        if mcp_config is not None:
            command += [
                "--mcp-config", str(mcp_config),
                # Ignore every other MCP configuration: a server the user
                # happens to have installed is not part of this session's
                # policy and must not be reachable from it.
                "--strict-mcp-config",
                "--allowedTools", *_mcp_tool_allowlist(),
                # Belt to that brace. Bash is the bypass; name it explicitly
                # so a future change to --allowedTools defaults cannot quietly
                # reopen it.
                "--disallowedTools", "Bash", "Write", "Edit", "NotebookEdit",
            ]
        if not interactive:
            command.append("-p")
        command.append(prompt)
        return command
    raise ValueError(driver)


def _cancel_handler(_signum, _frame):
    raise KeyboardInterrupt


def _print_result(result: SkillResult, verbose: bool = False):
    """Pretty-print a SkillResult (or raw JSON in --json mode)."""
    if _JSON_MODE:
        print(result.to_json())
        if not result.ok:
            sys.exit(1)
        return
    status = "OK" if result.ok else "FAIL"
    print(f"[{status}] {result.summary}")
    if result.errors:
        for e in result.errors:
            print(f"  ERROR: {e}")
    if verbose and result.details:
        # Print markdown if available
        md = result.details.get("markdown")
        if md:
            print()
            print(md)
        else:
            print(json.dumps(result.details, indent=2, default=str))
    if not result.ok:
        sys.exit(1)


def _parse_kv_extras(blob: str, *, what: str) -> dict:
    """`k=v,k=v` → dict, with the ints and bools YAML would have produced.

    Used for `--core` extras and `--platform`. A malformed pair is an error
    rather than a silently dropped field: a knob that vanishes here would show
    up as a silently different SoC.
    """
    out: dict = {}
    for pair in blob.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"{what}: expected key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        key, value = key.strip(), value.strip()
        if value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        else:
            try:
                # base 0, so `0x40010000` lands as the int the schema wants.
                # A boot_addr left as a string compares unequal to an
                # otherwise identical config and reads as a real difference.
                out[key] = int(value, 0)
            except ValueError:
                out[key] = value
    return out



def _config_dir_of(output, repo_root):
    """Repo-relative directory of a generated config, or None."""
    if not output:
        return None
    from pathlib import Path as _P
    p = _P(output)
    p = p if p.is_absolute() else (_P.cwd() / p)
    try:
        return str(p.resolve().parent.relative_to(_P(repo_root).resolve()))
    except ValueError:
        return None


def cmd_physical_intent(args):
    """Derive the design-specific part of a hardening config."""
    from pathlib import Path as _Path

    import yaml as _yaml

    from .core import REPO_ROOT as _ROOT
    from .physical.floorplan import derive_floorplan
    from .physical.hardening import (
        DEFAULT_TEMPLATE, generate_hardening_config, wrapper_path_for)

    if args.physical_intent_cmd == "netlist-diff":
        from .physical.netlist import diff, load_summary, summarise_run

        try:
            import najaeda  # noqa: F401
        except ImportError:
            _print_result(SkillResult(
                ok=False, skill="physical-intent",
                summary="najaeda is not installed",
                errors=["pip install najaeda -- it is optional, and only the "
                        "netlist diff needs it"]))
            return
        summaries = []
        for label in ("run_a", "run_b"):
            run = _Path(getattr(args, label))
            netlist_path, libs = summarise_run(run)
            if netlist_path is None:
                _print_result(SkillResult(
                    ok=False, skill="physical-intent",
                    summary=f"no netlist under {run}/final/nl",
                    errors=["only a completed hardening run has one"]))
                return
            summaries.append(load_summary(netlist_path, libs))
        report = diff(*summaries)
        kinds = ", ".join(f"{k} {v:+d}" for k, v in report["by_kind"].items())
        _print_result(SkillResult(
            ok=True, skill="physical-intent",
            summary=(f"{report['instances']['delta']:+d} instances; {kinds}"
                     + ("; LOGIC CHANGED" if report["logic_changed"]
                        else "; no logic change")),
            details=report,
        ), verbose=True)
        return

    if args.physical_intent_cmd == "evidence":
        from .core import REPO_ROOT as _R
        from .evidence.store import EvidenceInputs, EvidenceStore

        store = EvidenceStore(_R / "build" / "evidence")
        if args.run_dir:
            found = EvidenceInputs.from_run(_Path(args.run_dir), repo_root=_R)
            if found is None:
                _print_result(SkillResult(
                    ok=False, skill="physical-intent",
                    summary=f"{args.run_dir} has no resolved.json",
                    errors=["only a completed run records its own inputs"]))
                return
            held = store.lookup(found)
            _print_result(SkillResult(
                ok=True, skill="physical-intent",
                summary=("evidence is current" if held else
                         "no stored evidence for these inputs — something "
                         "changed, or it was never recorded"),
                details={"key": found.key(), "current": held is not None,
                         "inputs": found.__dict__,
                         "summary": held.summary if held else None},
            ), verbose=True)
            return
        criteria = {k: v for k, v in (("pdk", args.pdk),
                                      ("rtl_bundle", args.rtl_bundle)) if v}
        matches = store.find(**criteria) if criteria else list(store.records())
        _print_result(SkillResult(
            ok=True, skill="physical-intent",
            summary=f"{len(matches)} record(s)"
                    + (f" matching {criteria}" if criteria else " stored"),
            details={"records": [
                {"key": m.key[:12], "design": m.design, "run": m.run_dir,
                 "pdk": m.inputs.pdk, "adverse": m.summary.get("adverse")}
                for m in matches]},
        ), verbose=True)
        return

    if args.physical_intent_cmd == "ppa":
        from .physical.ppa import compare, evaluate

        cand, errors = evaluate(_Path(args.run_dir), pdk=args.pdk)
        if cand is None:
            _print_result(SkillResult(
                ok=False, skill="physical-intent",
                summary=f"no metrics under {args.run_dir}", errors=errors))
            return
        details = {"candidate": cand.as_dict()}
        errs = [f"gate {g.name}: {g.detail}" for g in cand.gates if not g.passed]
        ok = cand.accepted
        summary = (f"{cand.design}: accepted" if ok else
                   f"{cand.design}: REJECTED ({', '.join(cand.failing)})")
        if args.baseline:
            base, berr = evaluate(_Path(args.baseline), pdk=args.pdk)
            if base is None:
                ok, errs = False, errs + berr
            else:
                details["baseline"] = base.as_dict()
                result, refusals = compare(cand, base)
                if result is None:
                    ok, errs = False, errs + refusals
                    summary += "; not comparable with the baseline"
                else:
                    details["comparison"] = result
                    verdict = ("dominates" if result["dominates"] else
                               "dominated" if result["dominated"] else "trade-off")
                    summary += f"; vs baseline {result['score']:+.2%}, {verdict}"
                    moved = result["moved"]
                    summary += (
                        f", caused by {result['attribution']}"
                        if result["attribution"] else
                        "; settings unknown, no cause named" if moved is None else
                        "; a repeat, no setting moved" if not moved else
                        f"; {len(moved)} settings moved, no cause named")
        _print_result(SkillResult(ok=ok, skill="physical-intent",
                                  summary=summary, details=details, errors=errs))
        return

    if args.physical_intent_cmd == "ledger":
        from collections import Counter

        from .core import REPO_ROOT as _R
        from .physical.ppa import ledger

        root = (_Path(args.runs) if args.runs
                else _R / "flow/librelane/experimental/runs")
        runs, experiments = ledger(root, design=args.design, pdk=args.pdk)
        per = Counter((r.design, r.pdk) for r in runs)
        pairs = sum(n * (n - 1) // 2 for n in per.values())
        _print_result(SkillResult(
            ok=True, skill="physical-intent",
            summary=(f"{len(runs)} runs ({sum(r.accepted for r in runs)} "
                     f"accepted), {len(experiments)} single-variable "
                     f"experiment(s) among {pairs} same-design pairs"),
            details={
                "runs": [{"run": _Path(r.run).name, "design": r.design,
                          "accepted": r.accepted, "failing": r.failing,
                          "setting": r.setting, "logic_um2": r.logic_um2,
                          "power_w": r.power_w, "fmax_mhz": r.fmax_mhz}
                         for r in runs],
                "experiments": [{**e, "runs": [_Path(x).name for x in e["runs"]]}
                                for e in experiments]},
        ), verbose=True)
        return

    if args.physical_intent_cmd == "power":
        from .core import REPO_ROOT as _R
        from .physical.power import run_power_analysis

        run = _Path(args.run_dir)
        out = _Path(args.out_dir) if args.out_dir else run / "91-mosaic-workloadpower"
        parsed, problems = run_power_analysis(
            run, repo_root=_R, vcd=_Path(args.vcd) if args.vcd else None,
            vcd_scope=args.vcd_scope, corner=args.rc_corner, out_dir=out,
            timeout=args.timeout)
        if parsed is None:
            _print_result(SkillResult(
                ok=False, skill="physical-intent",
                summary=f"no power report from {run.name}",
                errors=problems), verbose=True)
            return
        # Report every corner. A single number would have to pick one, and
        # picking one silently is exactly what the corner-less script did.
        rows = {basis: {corner: {"total_w": r.total.total if r.total else None,
                                 "internal_w": r.total.internal if r.total else None,
                                 "switching_w": r.total.switching if r.total else None,
                                 "leakage_w": r.total.leakage if r.total else None,
                                 "clock_fraction": r.clock_fraction,
                                 "source": r.source}
                        for corner, r in sorted(corners_.items())}
                for basis, corners_ in sorted(parsed.items())}
        workload = rows.get("workload") or {}
        summary = (f"{run.name}: {len(workload) or len(rows.get('default', {}))} "
                   f"corner(s) reported"
                   + (" with workload activity" if workload
                      else " (default toggle proxy only -- pass --vcd)"))
        _print_result(SkillResult(
            ok=True, skill="physical-intent", summary=summary,
            details={"power": rows, "out_dir": str(out), "notes": problems},
        ), verbose=True)
        return

    if args.physical_intent_cmd == "screen":
        from .physical.stages import (SYNTH_TO_LOGIC, logic_area_band,
                                      screen_post_grt, synthesised_area)

        run = _Path(args.run_dir)
        verdict = screen_post_grt(run)
        details = {"post_grt": verdict.__dict__}
        synth = synthesised_area(run)
        if synth is not None:
            lo, hi = logic_area_band(synth)
            details["synthesis"] = {
                "synthesised_um2": synth,
                "final_logic_um2_band": [round(lo), round(hi)],
                "basis": f"{SYNTH_TO_LOGIC[0]}-{SYNTH_TO_LOGIC[1]}x, "
                         "22 gf180mcu runs"}
        _print_result(SkillResult(
            ok=verdict.keep, skill="physical-intent",
            summary=("keep" if verdict.keep else "PRUNE") + f": {verdict.reason}",
            details=details, errors=[] if verdict.keep else [verdict.reason],
        ), verbose=True)
        return

    if args.physical_intent_cmd == "optimize":
        import shlex
        import shutil

        from .core import REPO_ROOT as _R
        from .physical.optimize import (RUN_GB, launches_recorded, optimize,
                                        parse_knob, plan, signoff_launcher)

        from .physical.ppa import OBJECTIVES
        try:
            space = [parse_knob(k) for k in args.knob]
            weights = None
            if args.weight:
                names = {f for f, _ in OBJECTIVES}
                weights = {}
                for item in args.weight:
                    name, _, value = item.partition("=")
                    if name not in names:
                        raise ValueError(
                            f"{name!r} is not an objective; weigh one of "
                            f"{sorted(names)}")
                    weights[name] = float(value)
        except ValueError as e:
            _print_result(SkillResult(
                ok=False, skill="physical-intent",
                summary="bad --knob or --weight", errors=[str(e)]))
            return
        journal = (_Path(args.journal) if args.journal
                   else _R / "build/optimizer" / f"{args.design}.jsonl")
        steps = plan(space)
        # Price only what THIS invocation will spend. A resumed journal already
        # holds the runs it launched, and counting those again refused Block B's
        # one-run 40 ns continuation for wanting 20 GB when it needed 4.
        done = launches_recorded(journal)
        most = max(0, min(len(steps), args.max_runs) - done)
        free_gb = shutil.disk_usage(_R).free / 1e9
        errors = ([f"{most} new run(s) need about {most * RUN_GB:.0f} GB and "
                   f"{free_gb:.0f} GB is free"]
                  if most * RUN_GB > free_gb else [])
        if args.dry_run or errors:
            argv = " ".join(shlex.quote(a) for a in sys.argv[1:]
                            if a != "--dry-run")
            _print_result(SkillResult(
                ok=not errors, skill="physical-intent",
                summary=(f"plan: at most {most} new run(s) of a "
                         f"{len(steps)}-step longest walk, {done} already in "
                         "the journal"
                         + (" (dry run)" if args.dry_run else "")),
                details={"plan": steps, "max_runs": args.max_runs,
                         "already_launched": done,
                         "disk_free_gb": round(free_gb, 1),
                         "disk_needed_gb": most * RUN_GB,
                         "journal": str(journal),
                         # From the repo root: `python3 -m harness` and a
                         # relative --config only resolve there. The log's
                         # directory must exist before the shell redirects.
                         "start_detached": (
                             f"cd {shlex.quote(str(_R))} && "
                             f"mkdir -p {shlex.quote(str(journal.parent))} && "
                             f"setsid nohup python3 -m harness {argv} > "
                             f"{journal.parent}/optimizer.log 2>&1 "
                             "< /dev/null &")},
                errors=errors), verbose=True)
            return
        # The cheap stages refuse a candidate before it costs hours: a density
        # at or above one that already failed to route needs no run to know.
        def _prescreen(overrides):
            u = overrides.get("target_utilisation")
            if u is None:
                return None
            import yaml

            from .intent import coerce
            from .physical.stages import screen_model
            soc = (yaml.safe_load(_Path(args.config).read_text())
                   or {}).get("soc") or {}
            intent = coerce(soc)
            verdict = screen_model(intent.hart_count, u,
                                   getattr(intent, "pdk", None))
            return None if verdict.keep else verdict.reason

        out = optimize(
            space, journal=journal, prescreen=_prescreen,
            runs_root=_R / "flow/librelane/experimental/runs",
            max_runs=args.max_runs, poll_s=args.poll,
            launch=signoff_launcher(
                _Path(args.config).resolve(), args.design, _R,
                log_dir=journal.parent / "logs",
                resource_config=args.resource_config,
                manifest=args.manifest),
            weights=weights)
        _print_result(SkillResult(
            ok=out["incumbent_run"] is not None, skill="physical-intent",
            summary=(f"best {out['incumbent'] or 'the baseline'} after "
                     f"{out['launches']} run(s): {out['ended']}"),
            details=out), verbose=True)
        return

    if args.physical_intent_cmd == "metrics":
        from .evidence.librelane import load_metrics
        from .evidence.metric import unit_coverage
        from .physical.report import signoff_summary

        summary, errors = signoff_summary(
            _Path(args.run_dir), pdk=args.pdk,
            compare=_Path(args.compare) if args.compare else None)
        if summary is None:
            _print_result(SkillResult(
                ok=False, skill="physical-intent",
                summary=f"no metrics under {args.run_dir}", errors=errors))
            return
        raw, _ = load_metrics(_Path(args.run_dir))
        typed, total = unit_coverage(raw)
        recorded = None
        if args.record:
            from datetime import datetime, timezone

            from .core import REPO_ROOT as _R
            from .evidence.store import (
                EvidenceInputs, EvidenceRecord, EvidenceStore)

            found = EvidenceInputs.from_run(_Path(args.run_dir), repo_root=_R)
            if found is None:
                errors.append(f"{args.run_dir} has no resolved.json; "
                              "cannot record what produced it")
            else:
                store = EvidenceStore(_R / "build" / "evidence")
                path = store.put(EvidenceRecord(
                    inputs=found, design=summary.get("design"),
                    run_dir=str(args.run_dir), summary=summary,
                    recorded_at=datetime.now(timezone.utc).isoformat()))
                recorded = {"key": found.key(), "path": str(path)}
        _print_result(SkillResult(
            ok=True, skill="physical-intent",
            summary=(f"{summary['design'] or args.run_dir}: "
                     f"{summary['adverse']} adverse, "
                     f"{typed}/{total} metrics typed"
                     + (f", recorded {recorded['key'][:12]}" if recorded else "")),
            errors=errors,
            details={**summary, **({"recorded": recorded} if recorded else {})},
        ), verbose=True)
        return

    if args.physical_intent_cmd == "watch":
        from .physical.routability import assess, first_plateau, parse_drt_passes

        run_dir = _Path(args.run_dir)
        logs = sorted(run_dir.glob("*detailedrouting/*.log"))
        if not logs:
            _print_result(SkillResult(
                ok=False, skill="physical-intent",
                summary=f"no detailed-routing log under {run_dir}",
                errors=["detailed routing has not started, or the run tag is "
                        "wrong. This is not a verdict about the design"],
            ))
            return
        passes = parse_drt_passes(logs[-1].read_text())
        current = passes[-1] if passes else []
        verdict = assess(current)
        # Sticky: a run that plateaued and later drifted downward is still a
        # run the guard would have killed. Assessing only the tail called the
        # eleven-hour failure "converging".
        plateaued_at = first_plateau(current)
        abort = verdict.should_abort or plateaued_at is not None
        summary = f"{verdict.state}: {verdict.reason}"
        if plateaued_at is not None and not verdict.should_abort:
            summary = (f"plateaued at iteration {plateaued_at} (now "
                       f"{verdict.state}): {verdict.reason}")
        _print_result(SkillResult(
            # A plateau is a real answer, so `ok` reports whether the
            # assessment succeeded, not whether the news is good.
            ok=True, skill="physical-intent",
            summary=summary,
            details={
                "state": verdict.state,
                "should_abort": abort,
                "first_plateau_iteration": plateaued_at,
                "iterations": verdict.iterations,
                "initial": verdict.initial,
                "latest": verdict.latest,
                "passes": len(passes),
                "log": str(logs[-1]),
            },
        ), verbose=True)
        if args.fail_on_plateau and abort:
            raise SystemExit(3)
        return

    # Validated, not just parsed. This used to be a bare `.get("soc")`: a
    # config with a typo'd key, an unknown core or a bad topology produced a
    # die size anyway, and the first sign of trouble was hours into a flow.
    from .intent import DesignIntent

    document = _yaml.safe_load(_Path(args.config).read_text()) or {}
    soc, config_errors = DesignIntent.from_config(document)
    if soc is None:
        _print_result(SkillResult(
            ok=False, skill="physical-intent",
            summary=f"{args.config} is not a valid SoC config",
            errors=config_errors,
        ), verbose=True)
        return

    if args.physical_intent_cmd == "floorplan":
        floorplan, errors = derive_floorplan(
            soc, target_utilisation=args.utilisation, margin_um=args.margin)
        result = SkillResult(
            ok=floorplan is not None,
            skill="physical-intent",
            summary=(
                f"die {floorplan.die_side_um:.1f} um square "
                f"({floorplan.die_area_mm2:.4f} mm2), {floorplan.basis}"
                if floorplan else "cannot size a die"
            ),
            errors=errors,
            details=({} if floorplan is None else {
                "die_side_um": round(floorplan.die_side_um, 2),
                "die_area_mm2": round(floorplan.die_area_mm2, 4),
                "core_side_um": round(floorplan.core_side_um, 2),
                "logic_um2": floorplan.logic_um2,
                "target_utilisation": floorplan.target_utilisation,
                "basis": floorplan.basis,
                "reason": floorplan.reason,
                "references": list(floorplan.references),
                "warnings": list(floorplan.warnings),
                "librelane": floorplan.as_librelane(),
            }),
        )
        _print_result(result, verbose=True)
        return

    text, errors = generate_hardening_config(
        soc, args.design, repo_root=_ROOT,
        target_utilisation=args.utilisation, margin_um=args.margin,
        pin_template=getattr(args, 'pin_template', None),
        template=getattr(args, 'template', None) or DEFAULT_TEMPLATE,
        config_dir=_config_dir_of(getattr(args, 'output', None), _ROOT),
        clock_period_override=args.clock_period,
        repair_margin_override=getattr(args, "repair_margin_pct", None),
    )
    if text is not None and args.output:
        _Path(args.output).write_text(text)
    elif text is not None:
        print(text)
    _print_result(SkillResult(
        ok=text is not None,
        skill="physical-intent",
        summary=(f"hardening config for {args.design}"
                 + (f" -> {args.output}" if args.output and text else "")
                 if text is not None else
                 f"could not generate a hardening config for {args.design}"),
        errors=errors,
        details=({} if text is None else {
            "design": args.design,
            "output": args.output,
            "wrapper": wrapper_path_for(args.design),
        }),
    ))


def cmd_config_author(args):
    from .skills.config_author import ConfigAuthor
    author = ConfigAuthor()

    if args.config_author_cmd == "generate":
        cores = []
        if args.core:
            for c in args.core:
                # ip[:count[:role[:k=v,k=v]]] — the trailing extras carry
                # isa/with_csr/compressed/boot_addr, which are what separate
                # e.g. the Block A TITAN (rv32ic, CSRs) from its worker.
                parts = c.split(":")
                extras = {}
                if len(parts) >= 4 and parts[3]:
                    try:
                        extras = _parse_kv_extras(parts[3], what=f"--core {c!r}")
                    except ValueError as exc:
                        _print_result(SkillResult(
                            ok=False, skill="config-author",
                            summary="bad --core extras", errors=[str(exc)]))
                        return
                if len(parts) >= 3:
                    core = {"ip": parts[0], "count": int(parts[1]), "role": parts[2]}
                elif len(parts) == 2:
                    core = {"ip": parts[0], "count": int(parts[1]), "role": "nano"}
                else:
                    core = {"ip": parts[0], "count": 1, "role": "nano"}
                core.update(extras)
                cores.append(core)

        peripherals = args.peripheral.split(",") if args.peripheral else []

        platform = {}
        for blob in args.platform or []:
            try:
                platform.update(_parse_kv_extras(blob, what="--platform"))
            except ValueError as exc:
                _print_result(SkillResult(
                    ok=False, skill="config-author",
                    summary="bad --platform value", errors=[str(exc)]))
                return

        result = author.generate(
            name=args.name,
            cores=cores if cores else None,
            sram_kb=args.sram,
            boot_rom_kb=args.boot_rom,
            bus=args.bus,
            dma=args.dma,
            tdu=args.tdu,
            sched_mode=args.mode,
            peripherals=peripherals,
            pdk=args.pdk,
            target=args.target,
            preset=args.preset,
            output_path=Path(args.output) if args.output else None,
            scratchpad_bytes=args.scratchpad_bytes,
            platform=platform or None,
            target_clock_mhz=args.target_clock_mhz,
            repair_margin_pct=args.repair_margin_pct,
        )
        _print_result(result, verbose=True)

    elif args.config_author_cmd == "validate":
        result = author.validate_file(Path(args.file))
        _print_result(result, verbose=True)

    elif args.config_author_cmd == "eval":
        import json as _json

        from .prompt_eval import REQUESTS, run, system_prompt
        if args.print_prompts:
            # Everything a model needs, and nothing else: drive any provider
            # with this, then score its replies with --answers.
            print(_json.dumps({"system": system_prompt(),
                               "requests": [{"id": r.id, "text": r.text,
                                             "expect": r.expect}
                                            for r in REQUESTS]}, indent=2))
            return
        only = args.only.split(",") if args.only else None
        provider_errors: list = []
        replies = None
        if args.provider:
            # The one place this command touches a network: an explicit
            # --provider. The credential comes from the environment variable
            # the provider preset names, never from a flag or a config file.
            from .prompt_eval import answers_from_provider
            cfg = {"kind": args.provider}
            if args.model:
                cfg["model"] = args.model
            replies, failures = answers_from_provider(cfg, only)
            # Keep the replies: a score with no answers beside it cannot be
            # re-checked, and a provider's answers are the evidence.
            kept = Path("build/prompt_eval") / f"{args.model or args.provider}.jsonl"
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text("".join(
                _json.dumps({"id": k, "answer": v}) + "\n"
                for k, v in replies.items()))
            provider_errors = [f"{rid}: {why}" for rid, why in failures.items()]
        out = run(answers=Path(args.answers) if args.answers else None,
                  only=only, replies=replies)
        s = out["summary"]
        who = (args.model or args.provider if args.provider
               else "model" if args.answers else "grammar")
        _print_result(SkillResult(
            ok=s["correct"] == s["n"] and not provider_errors,
            skill="config-author",
            summary=(f"{who}: {s['correct']}/{s['n']} correct, fallback rate "
                     f"{s['fallback_rate']}, {s['repairs_total']} repair(s)"),
            details=out,
            errors=(([f"wrong: {', '.join(s['wrong'])}"] if s["wrong"] else [])
                    + provider_errors),
        ), verbose=True)

    elif args.config_author_cmd == "presets":
        result = author.list_presets()
        _print_result(result, verbose=True)

    elif args.config_author_cmd == "wake-demo":
        result = author.wake_demo_config(
            args.core_name,
            output_path=Path(args.output) if args.output else None,
        )
        _print_result(result, verbose=True)


def cmd_flow_runner(args):
    from .skills.flow_runner import FlowRunner
    runner = FlowRunner()

    if args.flow_runner_cmd == "list":
        result = runner.list_flows()
        _print_result(result, verbose=True)

    elif args.flow_runner_cmd == "run":
        if (
            os.environ.get("MOSAIC_AGENT_TOOL") == "1"
            and args.flow_name in {"harden-classic", "harden-chip"}
        ):
            _print_result(
                SkillResult(
                    ok=False,
                    skill="flow-runner",
                    summary="physical flow requires an explicit user-run command",
                    errors=["omp agent-tool origin is not authorized for physical design"],
                ),
                verbose=True,
            )
        output = None
        if _PROGRESS_JSONL:
            output = lambda line: _progress("flow_output", line, flow=args.flow_name)
        elif not _JSON_MODE:
            output = lambda line: print(f"  │ {line}", flush=True)
        _progress("flow_start", f"running {args.flow_name}", flow=args.flow_name)
        result = runner.run(
            args.flow_name,
            config=args.config,
            on_output=output,
        )
        _progress(
            "flow_end",
            result.summary,
            flow=args.flow_name,
            ok=result.ok,
        )
        _print_result(result, verbose=True)


def cmd_drc_triage(args):
    from .skills.drc_triage import DRCTriage
    triage = DRCTriage()

    if args.drc_triage_cmd == "analyze":
        result = triage.analyze_file(Path(args.file), fmt=args.format)
        _print_result(result, verbose=True)

    elif args.drc_triage_cmd == "scan":
        result = triage.triage_directory(Path(args.directory))
        _print_result(result, verbose=True)


def cmd_doc_gen(args):
    from .skills.doc_gen import DocGen
    docgen = DocGen()

    if args.doc_gen_cmd == "config":
        result = docgen.config_summary(Path(args.file))
        _print_result(result, verbose=True)

    elif args.doc_gen_cmd == "memory-map":
        result = docgen.memory_map()
        _print_result(result, verbose=True)

    elif args.doc_gen_cmd == "dashboard":
        result = docgen.dashboard_summary(
            Path(args.file) if args.file else None
        )
        _print_result(result, verbose=True)


def cmd_soc_from_prompt(args):
    from .skills.soc_from_prompt import SocFromPrompt
    sfp = SocFromPrompt()

    if args.soc_from_prompt_cmd == "plan":
        result = sfp.plan(args.text, use_llm=args.llm)
        _print_result(result, verbose=True)

    elif args.soc_from_prompt_cmd == "run":
        result = sfp.run(args.text, execute=args.run, name=args.name,
                         use_llm=args.llm)
        _print_result(result, verbose=True)


def cmd_mcp_server(args):
    """Serve the gated registry over stdio until the client closes it.

    Nothing human-readable may reach stdout here: it carries JSON-RPC frames,
    and one stray line corrupts the session for the client.
    """
    from .approvals import Approvals, approvals_dir
    from .core import REPO_ROOT as _ROOT
    from .mcp_server import MCPServer, build_session, parse_scope_allowlist

    # A host config cannot always pass argv, so the request may also arrive
    # through the environment (omp expands it into the server's env).
    request = args.request or os.environ.get("MOSAIC_REQUEST") or None
    try:
        allowlist = parse_scope_allowlist(os.environ.get("MOSAIC_SCOPES"))
        session = build_session(
            request, repo_root=_ROOT,
            required_evidence=args.required_evidence,
            scope_allowlist=allowlist,
            approvals=Approvals(token_dir=approvals_dir()))
    except ValueError as error:
        print(f"mosaic MCP server: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    ceiling = (f"scope ceiling '{session.authorized_scope}' (locked)"
               if session.locked else
               f"plugin mode · scopes {sorted(session.scope_allowlist)}")
    print(
        f"mosaic MCP server · {ceiling} · repo {_ROOT} · "
        f"{len(session.registry.specs)} tools",
        file=sys.stderr, flush=True,
    )
    raise SystemExit(MCPServer(session).serve_forever())


def cmd_approve(args):
    from .approvals import cli_approve

    raise SystemExit(cli_approve(args.scope, args.minutes,
                                 revoke_only=args.revoke))


def cmd_install(args):
    from .install import install

    _print_result(install(args.host, write=args.write,
                          config_file=args.config_file,
                          scopes=args.scopes), verbose=True)


def cmd_setup(args):
    from .skills.setup_wizard import SetupWizard
    wiz = SetupWizard()
    if args.setup_cmd == "show":
        _print_result(wiz.show(), verbose=True)
    else:
        result = wiz.configure(
            driver=args.driver, api_kind=args.api_kind, model=args.model,
            base_url=args.base_url, env_key=args.env_key,
            interactive=not args.non_interactive,
        )
        _print_result(result, verbose=True)


def cmd_agent(args):
    """Run the in-process agent loop or hand off to an interactive agent UI."""
    import shutil
    import subprocess
    from datetime import datetime, timezone

    from .agent import AgentRunner
    from .agent_tools import AgentToolRegistry
    from .events import (
        CompositeSink,
        EventStream,
        JsonlJournal,
        JsonlRenderer,
        TerminalRenderer,
    )
    from .llm import create_tool_provider
    from .skills.setup_wizard import load_user_config

    user_config = load_user_config()
    driver = args.driver or user_config.get("driver", "deterministic")
    text = args.text
    if driver in {"claude", "omp"}:
        unsupported = []
        for enabled, flag in (
            (args.dry_run, "--dry-run"),
            (args.name is not None, "--name"),
            (args.max_turns is not None, "--max-turns"),
            (args.allow_physical, "--allow-physical"),
            (args.allow_integration, "--allow-integration"),
            (args.headless, "--headless"),
            (args.events_jsonl, "--events-jsonl"),
            (args.quiet, "--quiet"),
            (args.no_color, "--no-color"),
            (args.no_tool_output, "--no-tool-output"),
            (args.require_evidence != "auto", "--require-evidence"),
        ):
            if enabled:
                unsupported.append(flag)
        if unsupported:
            result = SkillResult(
                ok=False,
                skill="agent",
                summary=f"external driver '{driver}' does not implement harness policy flags",
                errors=[
                    f"unsupported: {', '.join(unsupported)}; use driver=api or deterministic",
                    "for approvals, run `mosaic approve <scope>` in a terminal",
                ],
            )
            _print_result(result, verbose=True)
            return
        binary = shutil.which(driver)
        if binary is None:
            result = SkillResult(
                ok=False,
                skill="agent",
                summary=f"configured agent driver '{driver}' is not installed",
                errors=[f"'{driver}' was not found on PATH; run mosaic setup"],
            )
            _print_result(result, verbose=True)
            return
        if _JSON_MODE:
            result = SkillResult(
                ok=False,
                skill="agent",
                summary=f"--json is not the {driver} event protocol",
                errors=[
                    "use --events-jsonl for headless streaming or run the "
                    "driver interactively"
                ],
            )
            _print_result(result, verbose=True)
            return
        prompt = external_agent_prompt(driver, text)
        interactive = (
            sys.stdin.isatty()
            and sys.stdout.isatty()
        )
        if not interactive:
            result = SkillResult(
                ok=False,
                skill="agent",
                summary=f"external driver '{driver}' requires an interactive TTY",
                errors=["use driver=api for normalized headless JSONL sessions"],
            )
            _print_result(result, verbose=True)
            return
        # WP-7. This handoff used to be unconditional, and everything the
        # harness knows about authorization stayed behind: no ceiling, no
        # evidence binding, no completion gate. A driver may now launch only
        # if the gates travel with it.
        from .agent import classify_request_scope
        from .approvals import APPROVAL_SCOPES, Approvals, approvals_dir
        from .core import REPO_ROOT as repo_root
        from .mcp_server import SERVER_NAME

        scope = classify_request_scope(text)
        mcp_config = None
        child_env = None
        if driver == "claude":
            mcp_config = write_mcp_config(
                text, repo_root,
                repo_root / "build" / "agent" / "mcp" / "claude.json")
            disabled = "Bash/Write/Edit disabled"
        else:
            # omp reads the committed .omp/mcp.json, which expands
            # MOSAIC_REQUEST into the server's env: same locked ceiling as
            # the claude driver's --request argv.
            if not (repo_root / ".omp" / "mcp.json").is_file():
                _print_result(SkillResult(
                    ok=False, skill="agent",
                    summary="omp cannot be gated: .omp/mcp.json is missing",
                    errors=[f"expected {repo_root / '.omp' / 'mcp.json'}"],
                ), verbose=True)
                return
            child_env = dict(os.environ, MOSAIC_REQUEST=text)
            disabled = "built-in tools disabled"
        print(
            f"Enforced session · scope ceiling '{scope}' · tools via the "
            f"{SERVER_NAME} MCP server ({disabled})",
            flush=True,
        )
        if scope in APPROVAL_SCOPES and not Approvals(
                token_dir=approvals_dir()).granted(scope):
            print(
                f"Note: '{scope}' work needs a person's approval first: run "
                f"`mosaic approve {scope}` in another terminal.",
                flush=True,
            )
        command = _external_agent_command(
            driver, binary, prompt, interactive=interactive,
            mcp_config=mcp_config,
        )
        print(
            f"Handing off to {driver} {'interactive UI' if interactive else 'event stream'}…",
            flush=True,
        )
        raise SystemExit(subprocess.call(command, cwd=str(repo_root),
                                         env=child_env))

    from .core import REPO_ROOT as repo_root

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    journal = JsonlJournal(
        repo_root
        / "build"
        / "agent"
        / "sessions"
        / f"{timestamp}.jsonl"
    )
    sinks = [journal]
    if args.events_jsonl:
        sinks.insert(0, JsonlRenderer())
    elif not args.quiet and not _JSON_MODE:
        sinks.insert(
            0,
            TerminalRenderer(
                color=False if args.no_color else None,
                show_output=not args.no_tool_output,
            ),
        )
    events = EventStream(CompositeSink(*sinks))
    provider = None
    if driver == "api":
        api_config = user_config.get("api")
        if not isinstance(api_config, dict):
            result = SkillResult(
                ok=False,
                skill="agent",
                summary="API agent is not configured",
                errors=["run mosaic setup --driver api --api-kind ..."],
            )
            journal.close()
            _print_result(result, verbose=True)
            return
        try:
            provider = create_tool_provider(api_config)
        except Exception as error:
            result = SkillResult(
                ok=False, skill="agent", summary=f"invalid API config: {error}", errors=[str(error)]
            )
            journal.close()
            _print_result(result, verbose=True)
            return
    from .approvals import Approvals, approvals_dir

    registry = AgentToolRegistry(
        allow_write=not args.dry_run,
        allow_execute=not args.dry_run,
        allow_physical=args.allow_physical,
        allow_integration=args.allow_integration,
        approvals=Approvals(token_dir=approvals_dir()),
    )
    runner = AgentRunner(
        registry,
        events,
        provider=provider,
        max_turns=args.max_turns or 12,
    )
    try:
        result = runner.run(
            text,
            driver=driver,
            name=args.name,
            dry_run=args.dry_run,
            required_evidence=args.require_evidence,
        )
    finally:
        journal.close()
    result.details.setdefault("agent", {})["journal"] = str(journal.path)
    if _JSON_MODE:
        _print_result(result, verbose=True)
    elif args.events_jsonl:
        if not result.ok:
            raise SystemExit(1)
    elif not result.ok:
        raise SystemExit(1)


def cmd_netlist_diff(args):
    from .skills.netlist_diff import audit
    _print_result(audit(args.run_a, args.run_b,
                        stage=getattr(args, "stage", None) or "pnl"),
                  verbose=True)


def cmd_gls_triage(args):
    from .skills.gls_triage import audit
    _print_result(audit(getattr(args, "run_dir", None),
                        control=getattr(args, "control", None)), verbose=True)


def cmd_flow_preflight(args):
    from .skills.flow_preflight import audit
    _print_result(audit(getattr(args, "stage", "harden"),
                        config=getattr(args, "config", None),
                        soc_config=getattr(args, "soc_config", None),
                        tag=getattr(args, "tag", None)),
                  verbose=True)


def cmd_doctor(args):
    from .toolchain import doctor
    result = doctor()
    if not _JSON_MODE:
        print(f"runtime: {result.details['runtime']}")
        for t in result.details["tools"]:
            mark = "ok" if t["ok"] else ("FAIL" if t["required"] else "--")
            print(f"  [{mark:>4}] {t['name']:<14} {t['found'] or 'not found'}")
        for p in result.details["pdks"]:
            where = ", ".join(p["paths"]) or "not installed"
            print(f"  [{'ok' if p['present'] else '--':>4}] {p['pdk']:<14} {where}")
        for w in result.details["warnings"]:
            print(f"  WARNING: {w}")
    _print_result(result)


def cmd_pdk_port(args):
    from .skills.pdk_port import audit, GF180_7T
    _print_result(audit(getattr(args, "technology", None) or GF180_7T),
                  verbose=True)


def cmd_waiver_author(args):
    from .skills.waiver_author import audit
    from pathlib import Path as _P
    _print_result(
        audit(_P(args.file) if getattr(args, "file", None) else None),
        verbose=True)


def cmd_wrapper_smith(args):
    from .skills.wrapper_smith import WrapperSmith
    ws = WrapperSmith()

    if args.wrapper_smith_cmd == "fetch":
        result = ws.fetch(args.url, name=args.name, subdir=args.subdir)
        _print_result(result, verbose=True)

    elif args.wrapper_smith_cmd == "analyze":
        result = ws.analyze(
            Path(args.rtl), top=args.top,
            out=Path(args.output) if args.output else None,
        )
        _print_result(result, verbose=True)

    elif args.wrapper_smith_cmd == "scaffold":
        if os.environ.get("MOSAIC_AGENT_TOOL") == "1" and args.apply:
            _print_result(
                SkillResult(
                    ok=False,
                    skill="wrapper-smith",
                    summary="wrapper apply requires an explicit user-run command",
                    errors=["omp agent-tool origin may stage and review only"],
                ),
                verbose=True,
            )
        result = ws.scaffold(
            args.core_name,
            analysis=Path(args.from_analysis),
            apply=args.apply,
            vendor_from=Path(args.vendor_from) if args.vendor_from else None,
            family_override=args.family,
        )
        _print_result(result, verbose=True)

    elif args.wrapper_smith_cmd == "families":
        result = ws.families()
        _print_result(result, verbose=True)

    elif args.wrapper_smith_cmd == "core-matrix":
        from .skills.wrapper_smith import audit_core_matrix
        _print_result(audit_core_matrix(), verbose=True)


def cmd_tb_smith(args):
    from .skills.tb_smith import TbSmith
    ts = TbSmith()

    if args.tb_smith_cmd == "generate":
        result = ts.generate(
            args.core_name,
            boot_addr=int(str(args.boot_addr), 0),
            unified=(True if args.unified else False if args.split else None),
            watchdog_cycles=args.watchdog,
            analysis=Path(args.analysis) if args.analysis else None,
        )
        _print_result(result, verbose=True)

    elif args.tb_smith_cmd == "run":
        result = ts.run(args.core_name, timeout=args.timeout)
        _print_result(result, verbose=True)

    elif args.tb_smith_cmd == "wake-demo":
        result = ts.wake_demo(args.core_name, execute=not args.config_only)
        _print_result(result, verbose=True)


def cmd_tb_matrix(args):
    from .skills.tb_matrix import TbMatrix
    tm = TbMatrix()

    if args.tb_matrix_cmd == "axes":
        result = tm.axes()
        _print_result(result, verbose=True)

    elif args.tb_matrix_cmd == "plan":
        result = tm.plan(tier=args.tier)
        _print_result(result, verbose=True)

    elif args.tb_matrix_cmd == "run":
        result = tm.run(tier=args.tier, limit=args.limit,
                        resume=not args.no_resume)
        _print_result(result, verbose=True)

    elif args.tb_matrix_cmd == "report":
        result = tm.report()
        _print_result(result, verbose=True)


def cmd_topo_viz(args):
    from .skills.topo_viz import TopoViz
    viz = TopoViz()

    if args.topo_viz_cmd == "check":
        result = viz.check(Path(args.file))
        _print_result(result, verbose=True)

    elif args.topo_viz_cmd == "render":
        result = viz.render(
            Path(args.file),
            output=Path(args.output) if args.output else None,
            svg_only=args.svg,
            view_kind=args.view,
            run_dir=Path(args.run_dir) if args.run_dir else None,
            design=args.design,
        )
        _print_result(result, verbose=True)


def cmd_web(args):
    from .web import build_site, serve
    out = Path(args.out)
    if args.web_cmd == "build" or not args.no_build:
        result = build_site(out)
        if args.web_cmd == "build":
            _print_result(result, verbose=True)
            return
        print(result.summary, file=sys.stderr, flush=True)
    raise SystemExit(serve(out, port=args.port))


def main():
    # Convert cooperative tool cancellation (including the omp custom tool's
    # SIGTERM) into Python's normal unwind path so run_cmd can terminate the
    # complete EDA process group rather than orphaning simulator descendants.
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _cancel_handler)
    parser = _StrictArgumentParser(
        prog="mosaic",
        description="Agentic harness for MOSAIC-SoC EDA flows (based on oh-my-pi)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="Emit the raw SkillResult JSON (machine mode; "
                             "exit code 1 on failure)")
    parser.add_argument(
        "--progress-jsonl",
        action="store_true",
        help="Emit live progress JSON Lines on stderr while --json stays clean",
    )
    subparsers = parser.add_subparsers(dest="skill", help="Skill to use")

    # physical-intent
    pi = subparsers.add_parser(
        "physical-intent",
        help="Derive the design-specific part of a hardening config")
    pi_sub = pi.add_subparsers(dest="physical_intent_cmd", required=True)
    for name, blurb in (("floorplan", "Size a die from the design"),
                        ("harden", "Emit a complete hardening config")):
        sp = pi_sub.add_parser(name, help=blurb)
        sp.add_argument("--config", required=True, help="mosaic.yaml to size")
        # No default: omitting it means "use the densest target this design
        # size has been shown to route at", which varies with hart count. A
        # fixed default is what sized the 4-hart die that never routed.
        sp.add_argument("--utilisation", type=float, default=None,
                        help="target post-CTS utilisation (default: the "
                             "highest demonstrated routable value for this "
                             "design size)")
        sp.add_argument("--margin", type=float, default=_DEFAULT_MARGIN,
                        help="ring margin per side, um (default %(default)s)")
        if name == "harden":
            sp.add_argument("--design", required=True,
                            help="DESIGN_NAME; must equal the top module name")
            sp.add_argument("--output", help="write here instead of stdout")
            sp.add_argument("--template", default=None,
                            help="Hardening template to derive from. Use a "
                                 "signoff_template_*.yaml variant to change "
                                 "ONE setting without touching the shared "
                                 "template.")
            sp.add_argument("--pin-template", default=None,
                            help="FP_DEF_TEMPLATE: a padframe DEF that fixes "
                                 "pin placement. Use dir:: to keep the path "
                                 "relative to the generated config.")
            sp.add_argument("--clock-period", type=float,
                            help="ns; overrides objectives.target_clock_mhz")
            sp.add_argument("--repair-margin-pct", type=int, default=None,
                            help="GRT_DESIGN_REPAIR_MAX_SLEW_PCT; overrides "
                                 "objectives.repair_margin_pct")

    # `watch` takes a run directory, not a config: it reads what routing is
    # doing rather than predicting what it will do.
    sp_watch = pi_sub.add_parser(
        "watch", help="Assess whether a run's detailed routing will converge")
    sp_watch.add_argument("--run-dir", required=True,
                          help="LibreLane run directory (runs/<tag>)")
    sp_watch.add_argument(
        "--fail-on-plateau", action="store_true",
        help="exit 3 when the trajectory has plateaued, so a caller can act")

    # `metrics` reads a finished run and reports typed values. Comparing two
    # runs by hand is how this session repeatedly derived numbers in prose.
    sp_metrics = pi_sub.add_parser(
        "metrics", help="Typed signoff metrics from a run, with units")
    sp_metrics.add_argument("--run-dir", required=True,
                            help="LibreLane run directory (runs/<tag>)")
    sp_metrics.add_argument("--pdk", default="gf180mcuD",
                            help="PDK the run used; recorded on every metric")
    sp_metrics.add_argument("--compare",
                            help="a second run directory to diff against")
    sp_metrics.add_argument(
        "--record", action="store_true",
        help="store this run's evidence, keyed on RTL bundle, config, PDK, "
             "tool image and parser version")

    # `ppa` is the objective an automatic improvement loop optimises: hard
    # gates first, then area / power / fmax, and a comparison that refuses to
    # rank runs of different designs, PDKs or power bases.
    sp_ppa = pi_sub.add_parser(
        "ppa", help="PPA gates and objectives for a run, optionally vs a baseline")
    sp_ppa.add_argument("--run-dir", required=True,
                        help="LibreLane run directory (runs/<tag>)")
    sp_ppa.add_argument("--baseline",
                        help="an accepted run of the same design to compare against")
    sp_ppa.add_argument("--pdk", default=None,
                        help="label recorded on the metrics; the PDK check "
                             "itself reads each run's resolved.json")

    # `ledger` is every finished run with its verdict and a digest of its
    # settings, plus the pairs that differ in exactly one setting -- the
    # experiments that were actually run, and the history an optimizer reads.
    sp_led = pi_sub.add_parser(
        "ledger", help="every run's PPA verdict, and the single-variable "
                       "experiments among them")
    sp_led.add_argument("--runs", default=None,
                        help="runs directory (default "
                             "flow/librelane/experimental/runs)")
    sp_led.add_argument("--design", default=None, help="only this DESIGN_NAME")
    sp_led.add_argument("--pdk", default=None,
                        help="label recorded on the metrics")

    # `screen` is the cheap-stage verdict on a run, finished or still going:
    # the post-global-routing timing screen, and the final-area band implied
    # by synthesis. A screen prunes only on proof; signoff still decides.
    # `power` is the only way to ask for an activity-driven power number. It
    # takes a RUN, not a design name, so it works for anything the generator
    # produces; the corners come from that run's own liberty set.
    sp_pw = pi_sub.add_parser(
        "power", help="report power per corner, with workload activity when a "
                      "VCD is given")
    sp_pw.add_argument("--run-dir", required=True,
                       help="finished LibreLane run directory (runs/<tag>)")
    sp_pw.add_argument("--vcd", default=None,
                       help="switching activity from a gate-level run "
                            "(tb/gls/run_gls.sh with GLS_VCD set). Without it "
                            "only the default-toggle proxy is reported")
    sp_pw.add_argument("--vcd-scope", default="gls_tb/dut",
                       help="DUT path inside the testbench hierarchy; without "
                            "it OpenSTA finds no netlist nets and silently "
                            "reports default activity")
    sp_pw.add_argument("--rc-corner", default="nom",
                       choices=("nom", "min", "max"),
                       help="parasitic corner selecting the SPEF; every PVT "
                            "corner of the run is reported against it")
    sp_pw.add_argument("--out-dir", default=None,
                       help="where to write <basis>/<corner>/power.rpt "
                            "(default: <run-dir>/91-mosaic-workloadpower)")
    sp_pw.add_argument("--timeout", type=int, default=3600,
                       help="seconds; reading a large VCD dominates")

    sp_scr = pi_sub.add_parser(
        "screen", help="cheap-stage verdict on a run: prunes only on proof")
    sp_scr.add_argument("--run-dir", required=True,
                        help="LibreLane run directory (runs/<tag>), "
                             "finished or in progress")

    # `optimize` improves a design by itself: a line search over the physical
    # knobs, one setting per run, gated by `ppa` and cut short by `screen`. It
    # launches multi-hour runs, so --max-runs has no default and --dry-run
    # shows the plan first.
    sp_opt = pi_sub.add_parser(
        "optimize", help="line-search the physical knobs, one setting per run")
    sp_opt.add_argument("--config", required=True,
                        help="mosaic.yaml of the design")
    sp_opt.add_argument("--design", required=True,
                        help="DESIGN_NAME; must equal the top module name")
    sp_opt.add_argument("--knob", action="append", required=True,
                        metavar="NAME=V1,V2,...",
                        help="values to try in order, least to most "
                             "aggressive: clock_period_ns or "
                             "repair_margin_pct. Repeat for several knobs")
    sp_opt.add_argument("--max-runs", type=int, required=True,
                        help="launch budget, the baseline included")
    sp_opt.add_argument("--resource-config", default=None,
                        help="passed through as MOSAIC_RESOURCE_CONFIG")
    sp_opt.add_argument("--manifest", default=None,
                        help="pin the series' RTL bundle to this "
                             "build/mosaic/<bundle>/manifest.json. Without it "
                             "each run resolves the bundle from today's "
                             "generator sources, so editing a flow script "
                             "mid-search moves the RTL and stops the search")
    sp_opt.add_argument("--journal", default=None,
                        help="JSONL journal (default build/optimizer/"
                             "<design>.jsonl); re-run the command to resume")
    sp_opt.add_argument("--poll", type=float, default=300.0,
                        help="seconds between screens of the running run")
    sp_opt.add_argument("--weight", action="append", metavar="NAME=W",
                        help="objective weight, repeatable: die_mm2, "
                             "logic_um2, energy_nj, fmax_mhz. A density walk "
                             "wants one — equal weights average a die saving "
                             "against three other terms and a real gain can "
                             "land near zero")
    sp_opt.add_argument("--dry-run", action="store_true",
                        help="print the plan and launch nothing")

    sp_nd = pi_sub.add_parser(
        "netlist-diff",
        help="Structurally diff two hardened netlists (needs najaeda)")
    sp_nd.add_argument("--run-a", required=True, help="baseline run directory")
    sp_nd.add_argument("--run-b", required=True, help="run to compare")

    sp_ev = pi_sub.add_parser(
        "evidence", help="Query the content-addressed evidence store")
    sp_ev.add_argument("--run-dir",
                       help="ask whether THIS run's evidence is still current")
    sp_ev.add_argument("--pdk", help="list records measured on this PDK")
    sp_ev.add_argument("--rtl-bundle", help="list records from this RTL bundle")

    # config-author
    ca = subparsers.add_parser("config-author", help="Generate/validate mosaic.yaml")
    ca_sub = ca.add_subparsers(dest="config_author_cmd", required=True)

    ca_gen = ca_sub.add_parser("generate", help="Generate a config")
    ca_gen.add_argument("--name", default="mosaic_soc", help="SoC name")
    ca_gen.add_argument(
        "--core", action="append",
        help="Core spec: ip:count:role[:k=v,k=v] (repeatable). Extras carry "
             "isa/with_csr/compressed/boot_addr, e.g. "
             "serv:1:titan:isa=rv32ic,with_csr=1,compressed=1")
    ca_gen.add_argument("--sram", type=int, default=32, help="SRAM KB")
    ca_gen.add_argument("--boot-rom", type=int, default=2, help="Boot ROM KB")
    ca_gen.add_argument("--bus", choices=("obi", "log", "floonoc"), default="obi")
    ca_gen.add_argument(
        "--dma", choices=("idma", "none"), default="idma",
        help="DMA engine; 'none' omits it entirely (saves 0.355 mm2 in GF180)",
    )
    ca_gen.add_argument("--pdk", choices=("gf180mcu", "sky130"), default="gf180mcu")
    ca_gen.add_argument(
        "--target", choices=("rtl", "simulation", "tapeout"), default="rtl",
        help="Implementation intent; tapeout activates the strict physical matrix",
    )
    ca_gen.add_argument("--tdu", action="store_true", help="Enable TDU")
    ca_gen.add_argument("--mode", default="static", help="Scheduling mode")
    ca_gen.add_argument("--peripheral", help="Comma-separated peripherals")
    ca_gen.add_argument("--preset", help="Use a named preset")
    ca_gen.add_argument("--output", help="Output path")
    ca_gen.add_argument(
        "--repair-margin-pct", type=int, default=None,
        help="per-design GRT_DESIGN_REPAIR_MAX_SLEW_PCT override; omit to "
             "take the signoff template's value")
    ca_gen.add_argument(
        "--target-clock-mhz", type=float, default=None,
        help="Target clock in MHz. DESIGN INTENT: physical-intent harden "
             "derives CLOCK_PERIOD from it, so the frequency lives in the SoC "
             "config rather than in a hand-edited hardening config. A request, "
             "not a result — STA decides whether it was met")
    ca_gen.add_argument(
        "--scratchpad-bytes", type=int, default=None,
        help="Flip-flop scratchpad size. Required reading for a part with "
             "sram_kb: 0 — it is where the shared-control window lives")
    ca_gen.add_argument(
        "--platform", action="append", metavar="K=V",
        help="Selectable platform blocks as key=value, comma-separated or "
             "repeated: debug, plic, spi_mode, multicore_timer, gpio_ao, "
             "ao_rv_timer, ao_fast_intr, dma. Omitted keys keep generator "
             "defaults")

    ca_val = ca_sub.add_parser("validate", help="Validate a config file")
    ca_val.add_argument("file", help="Path to mosaic.yaml")

    # `eval` turns "works with small LLMs" into a number: fixed requests, the
    # prompt the harness really sends, and a score computed by code. No
    # provider is called here: --print-prompts drives any model (Ollama, a
    # hosted one, a subagent) and --answers scores whatever it replied.
    ca_eval = ca_sub.add_parser(
        "eval", help="score a model's SoC-request replies, or the grammar "
                     "baseline")
    ca_eval.add_argument("--answers", default=None,
                         help='JSONL of {"id", "answer"}; omit for the '
                              'deterministic grammar baseline')
    ca_eval.add_argument("--only", default=None,
                         help="comma-separated request ids")
    ca_eval.add_argument("--print-prompts", action="store_true",
                         help="emit the system prompt and the requests as "
                              "JSON, then stop")
    ca_eval.add_argument("--provider", default=None,
                         choices=("opencode-go", "openai", "anthropic"),
                         help="ask this provider every request through the "
                              "harness's own translate_intent and score the "
                              "replies; the only mode here that touches the "
                              "network. The credential comes from the "
                              "environment variable the preset names "
                              "(OPENCODE_API_KEY, OPENAI_API_KEY, "
                              "ANTHROPIC_API_KEY), never from a flag")
    ca_eval.add_argument("--model", default=None,
                         help="model id for --provider: the raw id, not the "
                              "OpenCode TUI's provider/model prefix")

    ca_sub.add_parser("presets", help="List available presets")

    ca_wake = ca_sub.add_parser("wake-demo",
                                help="Emit the canonical 3-hart wake-demo config for a core")
    ca_wake.add_argument("core_name", help="Worker core ip (e.g. picorv32)")
    ca_wake.add_argument("--output", help="Output path (default configs/mosaic_<core>.yaml)")

    # flow-runner
    fr = subparsers.add_parser("flow-runner", help="Run EDA flows")
    fr_sub = fr.add_subparsers(dest="flow_runner_cmd", required=True)

    fr_sub.add_parser("list", help="List available flows")

    fr_run = fr_sub.add_parser("run", help="Run a flow")
    fr_run.add_argument("flow_name", help="Flow to run")
    fr_run.add_argument("--config", help="Config path for mosaic-gen")

    # drc-triage
    dt = subparsers.add_parser("drc-triage", help="Analyze DRC/LVS reports")
    dt_sub = dt.add_subparsers(dest="drc_triage_cmd", required=True)

    dt_analyze = dt_sub.add_parser("analyze", help="Analyze a report file")
    dt_analyze.add_argument("file", help="Report file path")
    dt_analyze.add_argument("--format", help="Report format (magic/klayout/netgen/auto)")

    dt_scan = dt_sub.add_parser("scan", help="Scan a directory for reports")
    dt_scan.add_argument("directory", help="Directory to scan")

    # doc-gen
    dg = subparsers.add_parser("doc-gen", help="Generate documentation")
    dg_sub = dg.add_subparsers(dest="doc_gen_cmd", required=True)

    dg_config = dg_sub.add_parser("config", help="Config summary doc")
    dg_config.add_argument("file", help="Path to mosaic.yaml")

    dg_sub.add_parser("memory-map", help="Memory-map reference doc")

    dg_dash = dg_sub.add_parser("dashboard", help="Dashboard summary")
    dg_dash.add_argument("--file", help="Dashboard path (default: DASHBOARD.md)")

    # netlist-diff
    nd = subparsers.add_parser(
        "netlist-diff", help="What the tools did differently between two runs")
    nd.add_argument("run_a")
    nd.add_argument("run_b")
    nd.add_argument("--stage", choices=["pnl", "nl"], default="pnl",
                    help="pnl = post-PnR (default, what GLS simulates)")

    # gls-triage
    gt = subparsers.add_parser(
        "gls-triage",
        help="What a gate-level verdict can and cannot support")
    gt.add_argument("run_dir", nargs="?", help="hardening run directory")
    gt.add_argument("--control", help="a run that boots, for differential attribution")

    # flow-preflight
    fp = subparsers.add_parser(
        "flow-preflight",
        help="Check a long flow's preconditions before it spends hours")
    fp.add_argument("stage", nargs="?", default="harden",
                    choices=("harden", "regen"),
                    help="what is about to run (default: harden)")
    fp.add_argument("--config", help="the hardening config the flow will use")
    fp.add_argument("--soc-config", help="the SoC yaml, for the bundle check")
    fp.add_argument("--tag", help="the run tag the flow will write to")

    # doctor
    subparsers.add_parser(
        "doctor",
        help="Check this machine's toolchain against the pins (docs/reproducing.md)")

    # pdk-port
    pp = subparsers.add_parser(
        "pdk-port",
        help="What a technology must supply before the tools can size for it")
    pp.add_argument("technology", nargs="?",
                    help="pdk or pdk:cell_library (default: GF180 7-track)")

    # waiver-author
    wa = subparsers.add_parser(
        "waiver-author",
        help="Audit signoff waivers against the runs they cite")
    wa.add_argument("--file", help="waiver YAML (default: signoff_waivers.yaml)")

    # wrapper-smith
    ws = subparsers.add_parser(
        "wrapper-smith",
        help="Wrap any open-source core/IP: analyze bus protocol + scaffold SCI integration")
    ws_sub = ws.add_subparsers(dest="wrapper_smith_cmd", required=True)

    ws_fetch = ws_sub.add_parser(
        "fetch", help="Clone + pin a core repo: <url>[@<ref-or-commit>]")
    ws_fetch.add_argument("url", help="Repo URL, optionally @ref or @commit")
    ws_fetch.add_argument("--name", help="Local name (default: repo basename)")
    ws_fetch.add_argument("--subdir",
                          help="RTL subdirectory inside the repo (e.g. hdl)")

    ws_an = ws_sub.add_parser("analyze", help="Parse ports + classify the native bus")
    ws_an.add_argument("rtl", help="RTL file or directory")
    ws_an.add_argument("--top", help="Top module name")
    ws_an.add_argument("-o", "--output", help="Analysis JSON output path")

    ws_sc = ws_sub.add_parser("scaffold",
                              help="Stage wrapper + all integration touchpoints")
    ws_sc.add_argument("core_name", help="Core name (lowercase, e.g. hazard3)")
    ws_sc.add_argument("--from", dest="from_analysis", required=True,
                       help="analysis.json from `analyze`")
    ws_sc.add_argument("--apply", action="store_true",
                       help="Apply to the tree (default: dry-run into build/)")
    ws_sc.add_argument("--vendor-from", help="Copy vendor RTL from this directory")
    ws_sc.add_argument("--family", help="Override the classified family")

    ws_sub.add_parser("families", help="List protocol families")

    ws_sub.add_parser(
        "core-matrix",
        help="Audit every catalogued core for a complete integration path")

    # tb-smith
    ts = subparsers.add_parser(
        "tb-smith", help="Generate + run per-core verification (TB + wake demo)")
    ts_sub = ts.add_subparsers(dest="tb_smith_cmd", required=True)

    ts_gen = ts_sub.add_parser("generate", help="Emit tb/sci/<core>/ TB assets")
    ts_gen.add_argument("core_name")
    ts_gen.add_argument("--boot-addr", default="0x180")
    ts_gen.add_argument("--watchdog", type=int, default=200_000)
    ts_gen.add_argument("--unified", action="store_true")
    ts_gen.add_argument("--split", action="store_true")
    ts_gen.add_argument("--analysis", help="wrapper-smith analysis.json")

    ts_run = ts_sub.add_parser("run", help="Run the generated TB")
    ts_run.add_argument("core_name")
    ts_run.add_argument("--timeout", type=int, default=600)

    ts_wd = ts_sub.add_parser("wake-demo", help="Full-SoC wake demo for the core")
    ts_wd.add_argument("core_name")
    ts_wd.add_argument("--config-only", action="store_true",
                       help="Write the config without running the sim")

    # tb-matrix
    tm = subparsers.add_parser(
        "tb-matrix",
        help="Combination-coverage testing of the SoC integration space")
    tm_sub = tm.add_subparsers(dest="tb_matrix_cmd", required=True)

    tm_sub.add_parser("axes", help="Show the registry-derived axes")

    tm_plan = tm_sub.add_parser(
        "plan", help="Enumerate the covering set for a tier (no execution)")
    tm_plan.add_argument("--tier", default="render",
                         choices=["validate", "render", "sim"])

    tm_run = tm_sub.add_parser(
        "run", help="Execute a tier's gate on every planned config")
    tm_run.add_argument("--tier", default="validate",
                        choices=["validate", "render", "sim"])
    tm_run.add_argument("--limit", type=int, default=None,
                        help="Run at most N not-yet-passing configs")
    tm_run.add_argument("--no-resume", action="store_true",
                        help="Re-run configs that already passed")

    tm_sub.add_parser("report", help="Summarize all recorded tier results")

    # soc-from-prompt
    sfp = subparsers.add_parser(
        "soc-from-prompt",
        help="Deterministic natural-language -> SoC pipeline (no LLM needed)")
    sfp_sub = sfp.add_subparsers(dest="soc_from_prompt_cmd", required=True)

    sfp_plan = sfp_sub.add_parser("plan", help="Parse only — show the grammar's reading")
    sfp_plan.add_argument("text", help="The natural-language SoC request")
    sfp_plan.add_argument("--llm", action="store_true",
                          help="Translate intent via the configured api driver "
                               "(mosaic setup); grammar fallback on failure")

    sfp_run = sfp_sub.add_parser("run", help="Write the config (+ --run: verify pipeline)")
    sfp_run.add_argument("text", help="The natural-language SoC request")
    sfp_run.add_argument("--run", action="store_true",
                         help="Execute the gated pipeline (mosaic-gen + wake demo)")
    sfp_run.add_argument("--name", help="SoC/config name override")
    sfp_run.add_argument("--llm", action="store_true",
                         help="Translate intent via the configured api driver")

    # setup (omp-style driver/provider picker)
    st = subparsers.add_parser(
        "setup", help="Choose the intent driver: deterministic | claude | omp | api")
    st_sub = st.add_subparsers(dest="setup_cmd")
    st_sub.add_parser("show", help="Show the current driver config + detection")
    st_cfg = st_sub.add_parser("configure", help="Configure (interactive without flags)")
    for p in (st, st_cfg):
        p.add_argument("--driver", choices=["deterministic", "claude", "omp", "api"])
        p.add_argument(
            "--api-kind",
            choices=["anthropic", "openai", "opencode-go"],
            dest="api_kind",
        )
        p.add_argument("--model", help="Model override (default per provider)")
        p.add_argument("--base-url", dest="base_url",
                       help="API base URL (fixed automatically for opencode-go)")
        p.add_argument("--env-key", dest="env_key",
                       help="Env var holding the API key (never stored)")
        p.add_argument("--non-interactive", action="store_true")

    # agent (dispatch to the configured driver)
    ag = subparsers.add_parser(
        "agent", help="Run a visible model/tool agent or deterministic workflow")
    ag.add_argument("text", help="Natural-language MOSAIC request")
    ag.add_argument(
        "--driver",
        choices=["deterministic", "claude", "omp", "api"],
        help="Override the configured driver for this session",
    )
    ag.add_argument("--name", help="Generated SoC name for deterministic workflow")
    ag.add_argument("--dry-run", action="store_true", help="Read/plan only; deny writes and execution")
    ag.add_argument("--max-turns", type=int, default=None, help="Bound API agent turns (default: 12)")
    ag.add_argument(
        "--require-evidence",
        choices=[
            "auto",
            "analysis",
            "config",
            "rtl",
            "simulation",
            "physical",
            "integration",
            "testbench",
            "documentation",
            "drc",
        ],
        default="auto",
        help="Lock completion/side-effect scope; auto derives a conservative ceiling from the user request",
    )
    ag.add_argument(
        "--allow-physical",
        action="store_true",
        help="Allow an API agent to invoke registered physical flows",
    )
    ag.add_argument(
        "--allow-integration",
        action="store_true",
        help="Allow wrapper-scaffold to apply changes outside its staging area",
    )
    ag.add_argument("--headless", action="store_true", help="Use external-driver headless mode")
    ag.add_argument(
        "--events-jsonl",
        action="store_true",
        help="Emit one normalized agent event per JSON line",
    )
    ag.add_argument("--quiet", action="store_true", help="Write the journal without terminal events")
    ag.add_argument("--no-color", action="store_true")
    ag.add_argument("--no-tool-output", action="store_true", help="Hide live child lines but retain results")
    # mcp-server — the enforced surface for external clients
    from .agent import REQUEST_SCOPES as _SCOPES

    mcp = subparsers.add_parser(
        "mcp-server",
        help="Serve the gated tool registry to an MCP client over stdio")
    mcp.add_argument(
        "--request", default=None,
        help="the user's request; the scope ceiling is derived from it and "
             "locked before any client connects. Omitted (plugin mode): each "
             "request is bound with session_new and the ceiling is the "
             "MOSAIC_SCOPES allowlist")
    mcp.add_argument(
        "--required-evidence", default="auto",
        choices=["auto", *sorted(_SCOPES)],
        help="override the derived ceiling (default: auto-classify)")

    # approve — a person's time-limited yes; refuses without a TTY
    ap = subparsers.add_parser(
        "approve",
        help="Approve a scope for agent sessions (interactive terminal only)")
    ap.add_argument("scope", choices=sorted(_SCOPES))
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--revoke", action="store_true",
                    help="delete the approval instead of granting it")

    # install — wire the MCP server into an agent host
    ins = subparsers.add_parser(
        "install",
        help="Print (or --write) the MCP config for an agent host")
    ins.add_argument("--host", required=True,
                     choices=["claude", "codex", "opencode", "omp"])
    ins.add_argument("--write", action="store_true",
                     help="merge into the host's config file after a "
                          "timestamped backup")
    ins.add_argument("--config-file", default=None,
                     help="host config file to merge into (default: the "
                          "host's user config)")
    ins.add_argument("--scopes", default=None,
                     help="MOSAIC_SCOPES to write (default: every scope "
                          "except integration and physical)")

    # topo-viz
    tv = subparsers.add_parser("topo-viz",
                               help="Semantic config checks + topology diagram")
    tv_sub = tv.add_subparsers(dest="topo_viz_cmd", required=True)

    tv_check = tv_sub.add_parser("check", help="Semantic checks on a config")
    tv_check.add_argument("file", help="mosaic.yaml-style config")

    tv_render = tv_sub.add_parser("render", help="Render the topology diagram")
    tv_render.add_argument("file", help="mosaic.yaml-style config")
    tv_render.add_argument("-o", "--output", help="Output file (.html or .svg)")
    tv_render.add_argument("--view", choices=("logical", "chip"), default="logical",
                           help="which diagram --svg writes (HTML holds both)")
    tv_render.add_argument("--run-dir", help="hardening run: placed pins and die from it")
    tv_render.add_argument("--design", help="delivery wrapper / hardening design name")

    wb = subparsers.add_parser(
        "web", help="Viewer for designs, diagrams, runs and evidence (read-only)")
    wb_sub = wb.add_subparsers(dest="web_cmd", required=True)
    wb_build = wb_sub.add_parser("build", help="Write the static site")
    wb_build.add_argument("--out", default="build/web", help="site directory")
    wb_build.set_defaults(no_build=False, port=None)
    wb_serve = wb_sub.add_parser(
        "serve", help="Build, then serve on 127.0.0.1 with live run progress")
    wb_serve.add_argument("--out", default="build/web", help="site directory")
    wb_serve.add_argument("--port", type=int, default=8765)
    wb_serve.add_argument("--no-build", action="store_true",
                          help="serve the existing site without rebuilding")
    tv_render.add_argument("--svg", action="store_true",
                           help="Emit a plain SVG instead of HTML")

    args = parser.parse_args()

    if args.json and getattr(args, "events_jsonl", False):
        parser.error("--json and --events-jsonl are mutually exclusive")

    global _JSON_MODE, _PROGRESS_JSONL
    _JSON_MODE = args.json
    _PROGRESS_JSONL = args.progress_jsonl

    if not args.skill:
        # omp-style first run: bare interactive invocation with no saved
        # config launches the driver picker (never in pipes/CI — TTY only).
        from .skills.setup_wizard import CONFIG_PATH, SetupWizard
        if sys.stdin.isatty() and sys.stdout.isatty() and not CONFIG_PATH.exists():
            print("First run — no driver configured yet.")
            _print_result(SetupWizard().configure(), verbose=False)
            print("\nNow try:  mosaic agent \"an SoC with one cv32e20 "
                  "controller and two picorv32 workers, tdu, a uart\"")
            sys.exit(0)
        parser.print_help()
        sys.exit(1)

    # Set up logging
    import logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(name)s: %(message)s",
    )

    dispatch = {
        "physical-intent": cmd_physical_intent,
        "config-author": cmd_config_author,
        "flow-runner": cmd_flow_runner,
        "drc-triage": cmd_drc_triage,
        "doc-gen": cmd_doc_gen,
        "topo-viz": cmd_topo_viz,
        "web": cmd_web,
        "soc-from-prompt": cmd_soc_from_prompt,
        "wrapper-smith": cmd_wrapper_smith,
        "tb-smith": cmd_tb_smith,
        "tb-matrix": cmd_tb_matrix,
        "setup": cmd_setup,
        "agent": cmd_agent,
        "mcp-server": cmd_mcp_server,
        "approve": cmd_approve,
        "install": cmd_install,
        "waiver-author": cmd_waiver_author,
        "flow-preflight": cmd_flow_preflight,
        "doctor": cmd_doctor,
        "pdk-port": cmd_pdk_port,
        "gls-triage": cmd_gls_triage,
        "netlist-diff": cmd_netlist_diff,
    }

    dispatch[args.skill](args)


if __name__ == "__main__":
    main()
