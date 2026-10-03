"""Read a gate-level run and say what its verdict can and cannot mean.

THE INCIDENT THIS IS SHAPED BY. A GLS failure in this project was tracked
across four netlists and localised to a missing `dlya_2` cell. It was a cell
SWAP, not a removal: instance `_28773_` is `dlya_2` in every booting netlist
and `clkbuf_1` in the failing one, same nets, both non-inverting buffers. In
zero delay that substitution is a no-op, so it cannot be the cause of anything.

The cause was found on 2026-09-07 and it was neither the cell nor the netlist:
the testbench deasserted `rst_n` in the same timestep as the capture edge, so
every asynchronously-reset flop saw RN rise as CLK rose and the winner was
decided by the delta-cycle depth of the reset tree versus the clock tree --
that is, by buffering. Two netlists identical in logic, connectivity and cell
function therefore disagreed. Released half a cycle away, both boot in 12 400
cycles. Note that the intermediate diagnosis -- a zero-delay UDP flop race --
was also wrong, and was disproved by measurement rather than argument.

A triage skill that confidently named a cell would have been confidently wrong
there. So this one reports the oracle's competence BEFORE any finding, and
refuses to attribute a failure to a netlist difference the oracle cannot see.

WHAT THE ORACLE IS. Icarus, PDK cells compiled `-DFUNCTIONAL` so their specify
blocks are stripped, flops as UDP sequential primitives, and no SDF --
`run_gls.sh` refuses SDF outright because these models use `ifnone` with
edge-sensitive paths, which iverilog rejects. Timing is STA's job at nine
corners; this simulation is a functional check of the routed netlist and
nothing more.

WHAT THAT MAKES INVISIBLE. Buffers, inverters, delay cells, clock buffers and
fill are transparent to it. Two netlists differing only in those are the same
netlist as far as this oracle is concerned, and a differential result across
them is measuring the simulator, not the design.

THE ONE THING THAT CHANGED. Stripping the specify blocks also strips
clock-to-Q, and zero-delay UDP flops race on data capture: buffering changes
event ordering, so a transparent difference could still flip a verdict.
`run_gls.sh` now gives the sequential UDPs a nonzero clock-to-Q, which closes
that. Both oracles are still blind to buffering -- the difference is that only
the zero-delay one could be FOOLED by it on a capture. Which oracle ran is
recorded in the log, so this module reads it rather than assuming.

Be precise about what this bought: it did NOT fix the 2026-08-12 incident.
That was the reset release above, and with the bench corrected both netlists
boot under the zero-delay oracle too. The clock-to-Q patch closes a real
second race that simply was not the one biting. `race_prone` therefore means
"could a data-capture race have decided this", not "is this verdict sound" --
no flag here can promise the latter. See `harness/evidence/gls.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..core import REPO_ROOT, SkillResult
from ..evidence.gls import GlsResult, GlsStatus, parse_gls_log

#: Cell kinds a zero-delay simulation cannot tell apart. A difference confined
#: to these cannot explain a behavioural difference, and saying so is the whole
#: point of this module.
TRANSPARENT_KINDS = frozenset({"buffer", "clock", "fill", "physical", "antenna"})

_PADWRAP_FAIL = re.compile(r"^\[PADWRAP\] FAIL (.+)$", re.M)
_PADWRAP_OK = re.compile(r"^\[PADWRAP\] pad controls: (.+)$", re.M)
_RESET = re.compile(r"reset released at\s+(\d+)")
_PROGRESS = re.compile(r"^\[GLS\] (\d+) cycles, t=\s*(\d+)", re.M)
_RUN_TAG = re.compile(r"^### run\s*:\s*(\S+)", re.M)


ORACLE = {
    "simulator": "iverilog",
    "delay_mode": "zero",
    "cell_models": "-DFUNCTIONAL (specify blocks stripped)",
    "flop_model": "UDP sequential primitives",
    "sdf": "refused by design (ifnone edge paths rejected by iverilog)",
    "timing_authority": "STA at nine corners, not this simulation",
    "blind_to": sorted(TRANSPARENT_KINDS),
}


def oracle_model(seq_delay_ns: Optional[float]) -> Dict:
    """The oracle THIS log came from, not the one we wish had run.

    Combinational cells are zero-delay in both modes; only the clock-to-Q arc
    differs, and that is the arc the race lived on.
    """
    o = dict(ORACLE)
    if seq_delay_ns:
        o["delay_mode"] = (f"combinational zero, sequential clock-to-Q "
                           f"{seq_delay_ns:g} ns")
        o["race_prone"] = False
    else:
        o["delay_mode"] = "zero"
        o["race_prone"] = True
        o["caveat"] = (
            "zero clock-to-Q with UDP flops races; buffering can decide what a "
            "flop samples. Re-run with GLS_SEQ_DELAY=1")
    return o


@dataclass
class Triage:
    """What a gate-level run supports, and what it does not."""

    verdict: str
    run: Optional[str] = None
    gls: Optional[GlsResult] = None
    findings: List[str] = field(default_factory=list)
    refusals: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict in {"PASS", "PASS_WITH_UNCHECKED_ASSERTIONS"}


def _log_for(run_dir: Optional[Path], repo_root: Path) -> Optional[Path]:
    """The one log tb/gls writes, or a copy archived beside the run."""
    if run_dir is not None:
        local = run_dir / "sim-gls.log"
        if local.is_file():
            return local
    shared = repo_root / "tb/gls/sim-gls.log"
    return shared if shared.is_file() else None


def _check_padwrap(text: str) -> Dict[str, List[str]]:
    """The pad self-checks nothing in the harness has ever read.

    `mosaic_block_a_padwrap.sv` prints `[PADWRAP] FAIL ...` when a pad control
    is wrong, and no code under harness/ greps for it. A run with a wrong pad
    configuration reads as a clean PASS today.
    """
    fails = _PADWRAP_FAIL.findall(text)
    states = _PADWRAP_OK.findall(text)
    return {"failures": fails, "reported_state": states}


def _attributable(delta: Optional[Dict],
                  seq_delay_ns: Optional[float] = None) -> Optional[str]:
    """Whether a netlist difference could explain a behavioural one.

    Returns a refusal string when it could not. This is the dlya_2 check.

    Buffering is logically transparent under BOTH oracles, so a buffer-only
    difference is never attributable. What the oracle mode changes is the
    remaining explanation: under zero clock-to-Q the race explains it, and
    under a nonzero one that explanation is gone and the residue is either the
    connectivity this diff does not compare or the harness.
    """
    if delta is None:
        return None
    changed = {k for k, v in (delta.get("by_kind") or {}).items() if v}
    if not changed:
        return ("the two netlists are identical, so a differing verdict is "
                "the simulator, not the design")
    if changed <= TRANSPARENT_KINDS:
        if not seq_delay_ns:
            return (
                f"the netlists differ only in {sorted(changed)}, which a "
                "zero-delay simulation cannot distinguish. A behavioural "
                "difference across them is an artefact of the oracle. This is "
                "the shape of the dlya_2/clkbuf_1 incident: same nets, both "
                "non-inverting buffers, a no-op in zero delay")
        return (
            f"the netlists differ only in {sorted(changed)}, which is "
            "logically transparent at any delay, so this difference still "
            "does not attribute. The zero-delay race is excluded here "
            f"(clock-to-Q {seq_delay_ns:g} ns), which leaves two candidates "
            "the cell census cannot rule out: a rewiring that preserves every "
            "instance, and the testbench. Compare connectivity, not instance "
            "sets")
    return None


def triage(run_dir: Optional[Path] = None, *,
           control: Optional[Path] = None,
           repo_root: Optional[Path] = None) -> Triage:
    """Read a gate-level run and classify what it supports."""

    root = repo_root or REPO_ROOT
    log_path = _log_for(run_dir, root)

    if log_path is None:
        return Triage(
            verdict="NO_LOG",
            refusals=["no sim-gls.log found. Run the gls flow with GLS_RUN "
                      "pointing at the run you want evidence about"])

    text = log_path.read_text()
    result = parse_gls_log(text)
    t = Triage(verdict="", gls=result, run=str(run_dir) if run_dir else None)

    # Attribution first: a verdict about the wrong netlist is worse than none.
    tag = _RUN_TAG.search(text)
    if run_dir is not None:
        wanted = run_dir.name
        if tag is None:
            t.notes.append(
                f"{log_path.name} carries no `### run :` header, so it cannot "
                "be attributed to a run. Logs produced before that header was "
                "written to the log are all in this state")
        elif wanted not in tag.group(1):
            t.refusals.append(
                f"this log is evidence about {tag.group(1)}, not {wanted}")
            t.verdict = "WRONG_RUN"
            return t

    # The assertions nothing has been reading.
    pads = _check_padwrap(text)
    if pads["failures"]:
        t.findings.extend(f"pad self-check failed: {f}" for f in pads["failures"])
    elif pads["reported_state"]:
        t.notes.append(f"pad controls read back from the gates: "
                       f"{pads['reported_state'][0]}")

    # Differential attribution, when a control run is offered.
    delta = None
    if control is not None:
        try:
            from ..physical.netlist import diff, load_summary, summarise_run
            a_nl, a_libs = summarise_run(run_dir) if run_dir else (None, [])
            b_nl, b_libs = summarise_run(control)
            if a_nl and b_nl:
                delta = diff(load_summary(a_nl, a_libs),
                             load_summary(b_nl, b_libs))
        except ImportError:
            t.notes.append(
                "najaeda is not installed, so no netlist comparison was made. "
                "Without it a differential claim cannot be checked against "
                "what the oracle can see")
        except Exception as exc:                      # pragma: no cover
            t.notes.append(f"netlist comparison unavailable: {exc}")

    blind = _attributable(delta, result.seq_delay_ns)
    if blind:
        t.refusals.append(blind)

    # A pass from the race-prone oracle is still a pass -- it booted -- but the
    # reader should know which oracle said so.
    if result.race_prone and result.status is not GlsStatus.NOT_RUN:
        t.notes.append(
            "this verdict came from the zero clock-to-Q oracle"
            + ("" if result.seq_delay_ns is not None else
               " (the log predates the `### seq c2q` header)")
            + ", where UDP flops race and buffering alone can change the "
              "answer. GLS_SEQ_DELAY=1 is the race-free oracle")

    # The verdict, stated in terms of what it supports.
    if result.status is GlsStatus.PASS:
        t.verdict = ("PASS_WITH_UNCHECKED_ASSERTIONS"
                     if pads["failures"] else "PASS")
    elif result.status is GlsStatus.NOT_RUN:
        t.verdict = "NOT_RUN"
        t.refusals.extend(result.reasons)
    elif blind:
        t.verdict = "ORACLE_ARTEFACT_SUSPECTED"
    elif pads["failures"]:
        t.verdict = "HARNESS_FAULT"
    elif control is None:
        t.verdict = "UNDECIDABLE_NO_CONTROL"
        t.refusals.append(
            "a failing run alone cannot separate a design fault from an oracle "
            "artefact. Supply a control run that boots and the netlist "
            "difference decides it")
    else:
        t.verdict = "DESIGN_SUSPECT"

    return t


def audit(run_dir: Optional[str] = None, *, control: Optional[str] = None,
          repo_root: Optional[Path] = None) -> SkillResult:
    """Triage a gate-level run, always reporting the oracle's competence."""

    root = repo_root or REPO_ROOT
    t = triage(Path(run_dir) if run_dir else None,
               control=Path(control) if control else None,
               repo_root=root)
    return SkillResult(
        ok=t.ok,
        skill="gls-triage",
        summary=(f"{t.verdict}"
                 + (f", {t.gls.cycles} cycles" if t.gls and t.gls.cycles else "")),
        details={
            # Printed on every run, pass or fail. If an oracle produces false
            # failures its passes are weak too, and a reader deserves both.
            "oracle": oracle_model(t.gls.seq_delay_ns if t.gls else None),
            "verdict": t.verdict,
            "status": t.gls.status.value if t.gls else None,
            "cycles": t.gls.cycles if t.gls else None,
            "netlist": t.gls.netlist if t.gls else None,
            "findings": t.findings,
            "notes": t.notes,
        },
        errors=t.refusals,
    )
