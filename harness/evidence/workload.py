"""What a workload run establishes, and what it does not establish about power.

ROADMAP M2:
  * "workload runner and region-of-interest activity capture"
  * "workload evidence without firmware/workload hashes is rejected"
  * "failed workload oracles, incomplete ROIs, and insufficient activity
     coverage cannot produce valid power evidence"

THE FINDING THAT CAME FIRST
---------------------------
Every signoff run in this project reports power. `blocka_reharden` says
`power__total` 0.0586 W and that number is already in the evidence store. It
is not workload power. LibreLane's STA script runs

    report_power -corner $corner_name

with no activity input at all -- no `read_vcd`, no `read_saif`, no
`set_power_activity` -- so OpenSTA falls back to its default toggle model. The
report shows the signature plainly: combinational switching is 0.5% of total
while clock and sequential are 99.5%, which is what you get when nothing told
the tool what the design was doing.

That is a legitimate number for "what does the clock tree cost", and it is not
what anyone means by power. So the first job here is not to build a better
estimate; it is to stop the existing one being mistaken for one. `Metric`
already refuses timing without a corner. Power without activity is the same
class of missing provenance, and this module names it.

WHAT A RUN CAN AND CANNOT ANCHOR TO TODAY
-----------------------------------------
Corrected 2026-09-21 against the logs on disk, because this paragraph described
a bench that does not exist. NO testbench prints a bare `cycles=` count: the
gate-level bench prints `after N cycles` on completion and `(roi_cycles=M)` when
its activity window closes, and the full-SoC benches print no cycle count at
all. `tb/mosaic_soc/sim.log` and `sim-generic.log` contain `EXIT SUCCESS` and
nothing else countable. So the ROI came back None from every real artefact and
every workload was refused for "no region of interest" -- a refusal that was
right in outcome and wrong in reason.

The route out named below is also out of date. The gate-level bench DOES dump a
VCD, gated on `GLS_VCD`: `$dumpvars(0, dut)` over the whole DUT hierarchy for a
window set by `GLS_VCD_START`/`GLS_VCD_CYCLES` (tb/gls/gls_tb.sv:104-120). What
is missing is not a route but a run -- no capture has been taken, so no
`ActivityCapture` has ever been constructed.

The bound is now in the log too: `run_gls.sh` records its plusargs, so
`+maxcycles=` is readable and a truncated run can be told from a finished one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

_EXIT_SUCCESS = re.compile(r"EXIT SUCCESS")
# The ROI is the window a power number is claimed over, and the gate-level bench
# prints it when the window closes: "activity ROI closed at cycle N
# (roi_cycles=M)" (tb/gls/gls_tb.sv:118, written so the window sits in the log
# beside the run that produced it). That is the pattern to match FIRST.
#
# `_CYCLES` stays as a fallback for a bench that prints a bare `cycles=`, and
# its \b still matters: without it the pattern matches inside `+maxcycles=`
# and records the BOUND as the region of interest, so a truncated run would
# look like a complete one. The same \b is why it could never match
# `roi_cycles=` -- an underscore is a word character -- which is how the only
# string the benches DO print stayed invisible to this parser.
_ROI_CYCLES = re.compile(r"roi_cycles=\s*(\d+)")
_CYCLES = re.compile(r"\bcycles=\s*(\d+)")
_MAXCYCLES = re.compile(r"\+maxcycles=(\d+)")


class ActivityKind(Enum):
    VCD = "vcd"
    SAIF = "saif"


class WorkloadError(ValueError):
    """A workload record that cannot support the claim made of it."""


def digest_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


@dataclass(frozen=True)
class ActivityCapture:
    """A switching-activity artefact and how much of the run it covers."""

    kind: ActivityKind
    path: str
    digest: str
    cycles_covered: int

    def covers(self, roi_cycles: int) -> bool:
        return self.cycles_covered >= roi_cycles


@dataclass(frozen=True)
class WorkloadRun:
    """One execution of a named workload on a named design.

    `workload` and `firmware_digest` are required: M2 rejects workload evidence
    without them, and the reason is concrete -- two runs of "the wake demo" are
    different measurements if the firmware changed, and nothing else in the
    record would show it.
    """

    workload: str
    design: str
    config_digest: str
    firmware_digest: str
    firmware_path: Optional[str] = None
    oracle_passed: bool = False
    roi_cycles: Optional[int] = None
    max_cycles: Optional[int] = None
    activity: Optional[ActivityCapture] = None

    def __post_init__(self) -> None:
        if not self.workload:
            raise WorkloadError("a workload run must name its workload")
        if not self.firmware_digest:
            raise WorkloadError(
                f"{self.workload}: no firmware digest. Two runs of one "
                "workload are different measurements if the firmware moved, "
                "and nothing else in the record would show it (M2)")

    # ── M2's power rule, stated as three separate refusals ───────────
    def power_evidence_problems(self) -> List[str]:
        """Why this run cannot support a power number. Empty means it can."""
        problems = []
        if not self.oracle_passed:
            problems.append(
                "the workload oracle failed, so the activity is of a design "
                "that did not do the thing being measured")
        if not self.roi_cycles:
            problems.append(
                "no region of interest: without a measured window the "
                "activity covers an unknown fraction of the workload")
        elif self.max_cycles and self.roi_cycles >= self.max_cycles:
            problems.append(
                f"the run hit its {self.max_cycles}-cycle bound, so it was "
                "truncated rather than finished and the ROI is incomplete")
        if self.activity is None:
            problems.append(
                "no switching activity was captured. Nothing in the "
                "simulation build passes --trace, so no VCD or FST exists, "
                "and OpenSTA's report_power falls back to a default toggle "
                "model -- that is a clock-tree cost, not workload power")
        elif self.roi_cycles and not self.activity.covers(self.roi_cycles):
            problems.append(
                f"activity covers {self.activity.cycles_covered} cycles of a "
                f"{self.roi_cycles}-cycle region of interest")
        return problems

    @property
    def supports_power_evidence(self) -> bool:
        return not self.power_evidence_problems()


def parse_sim_log(text: str) -> Tuple[bool, Optional[int], Optional[int]]:
    """`(oracle_passed, roi_cycles, max_cycles)` from a testbench log.

    The oracle is the TB's own `EXIT SUCCESS`, not the process exit status:
    a simulator can exit 0 having printed a failure, and this project's flows
    already gate on the marker for that reason.
    """
    passed = bool(_EXIT_SUCCESS.search(text))
    cycles = _ROI_CYCLES.search(text) or _CYCLES.search(text)
    bound = _MAXCYCLES.search(text)
    return (passed,
            int(cycles.group(1)) if cycles else None,
            int(bound.group(1)) if bound else None)


def power_metric_status(run: Optional[WorkloadRun]) -> Tuple[str, List[str]]:
    """How a power number from this run may be described.

    Returns `(status, reasons)` where status is `workload` or
    `default-activity`. There is deliberately no third value: either the
    number reflects a workload or it does not.
    """
    if run is None:
        return "default-activity", [
            "no workload run is associated with this power number, so it is "
            "OpenSTA's default toggle model"]
    problems = run.power_evidence_problems()
    if problems:
        return "default-activity", problems
    return "workload", []
