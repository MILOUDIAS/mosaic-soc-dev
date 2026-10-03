"""Does the netlist still compute what the RTL said? kepler-formal's answer.

WHY, AFTER GLS ALREADY EXISTS
-----------------------------
GLS proved a netlist can boot. That is one firmware image walking one path
through 22 bonded pins, and it is the strongest evidence this project had --
which is why a netlist that passed DRC, LVS, XOR, antenna, routing DRC and
timing at every corner, and did NOT boot, went unnoticed until someone ran GLS
by hand.

Equivalence checking is the complement. GLS covers the path exercised;
LEC/SEC covers the logic. Neither subsumes the other: a design can be
equivalent to RTL that is itself wrong, and it can boot correctly while being
inequivalent on paths the firmware never takes.

kepler-formal (fossi-foundation/nix-eda#kepler-formal, keplertech/kepler-formal)
does both: `-v lec` for gate-level combinational equivalence and `-v sec` for
sequential, including RTL-versus-gates through a SystemVerilog file list. It
parses SV with slang, the same front end this project already feeds LibreLane.

THE TRAP, MEASURED
------------------
**kepler-formal exits 0 whether it proves equivalence or finds a
counterexample.** Verified on a deliberately inequivalent pair:

    equivalent   -> exit 0, "No difference was found. SEC proved equivalence at k = 0."
    inequivalent -> exit 0, "Difference was found. SEC found a counterexample at k = 0."

So the verdict is the log marker, never the exit status. This is the third
tool in this project with that property -- the Verilator testbenches and
iverilog GLS both exit 0 having printed a failure -- and each one has cost
somebody a false pass somewhere.

COVERAGE IS PART OF THE VERDICT
-------------------------------
kepler-formal reports `SEC checked-output coverage: 100.00% (1/1
covered/existing outputs)`. A proof over 60% of the outputs is not a proof of
the design; it is a proof about the part that was checked. `PROVEN` therefore
requires the marker AND full coverage, and anything else is `INCONCLUSIVE`
rather than a pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

_PROVED = re.compile(r"No difference was found\.\s*(LEC|SEC) proved equivalence")
_COUNTEREXAMPLE = re.compile(r"Difference was found\.\s*(LEC|SEC) found a counterexample")
_COVERAGE = re.compile(
    r"checked-output coverage:\s*([\d.]+)%\s*\((\d+)/(\d+)")
_LOAD_FAILED = re.compile(r"Netlist loading failed|compilation failed")
_K = re.compile(r"at k = (\d+)")


class LecStatus(Enum):
    PROVEN = "proven"
    DISPROVEN = "disproven"
    INCONCLUSIVE = "inconclusive"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class LecResult:
    status: LecStatus
    coverage_pct: Optional[float] = None
    outputs_checked: Optional[int] = None
    outputs_total: Optional[int] = None
    k: Optional[int] = None
    log: Optional[str] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def blocks_signoff(self) -> bool:
        """Only a full-coverage proof clears it.

        INCONCLUSIVE blocks deliberately: a formal run that timed out, failed
        to elaborate, or proved 60% of the outputs has not established the
        thing being claimed, and the one behaviour that must never happen is
        for that to read like a pass.
        """
        return self.status is not LecStatus.PROVEN


def parse_lec_log(text: str) -> LecResult:
    """Read kepler-formal's output. The marker decides, not the exit status."""
    reasons: List[str] = []
    coverage = _COVERAGE.search(text)
    pct = float(coverage.group(1)) if coverage else None
    checked = int(coverage.group(2)) if coverage else None
    total = int(coverage.group(3)) if coverage else None
    k_match = _K.search(text)
    k = int(k_match.group(1)) if k_match else None

    if _COUNTEREXAMPLE.search(text):
        reasons.append(
            "a counterexample was found: the netlist does not compute what the "
            "RTL specifies. Note kepler-formal exits 0 in this case, so the "
            "marker is the verdict")
        status = LecStatus.DISPROVEN
    elif _PROVED.search(text):
        if pct is not None and pct < 100.0:
            reasons.append(
                f"equivalence was proved over only {pct:.2f}% of outputs "
                f"({checked}/{total}). That is a proof about the part checked, "
                "not about the design")
            status = LecStatus.INCONCLUSIVE
        else:
            status = LecStatus.PROVEN
    elif _LOAD_FAILED.search(text):
        reasons.append(
            "the design failed to load, so nothing was checked. A tool that "
            "could not read the design has not agreed with it")
        status = LecStatus.INCONCLUSIVE
    else:
        reasons.append(
            "no equivalence verdict in the log -- the run did not finish, or "
            "was killed. Formal runs time out, and a timeout is not a pass")
        status = LecStatus.INCONCLUSIVE
    return LecResult(status=status, coverage_pct=pct, outputs_checked=checked,
                     outputs_total=total, k=k, reasons=reasons)


def lec_for_run(run_dir: Path, *, repo_root: Path) -> LecResult:
    """Whether THIS run's netlist has been proved equivalent to its RTL.

    Matched by run identity for the same reason GLS is: one log in a fixed
    place is evidence about whichever netlist produced it, and a stale one
    reads as evidence about whatever you happen to ask.
    """
    log = repo_root / "build" / "lec" / f"{run_dir.name}.log"
    if not log.is_file():
        return LecResult(LecStatus.NOT_RUN, reasons=[
            f"no equivalence check recorded at {log}. Run the `lec` flow with "
            f"LEC_RUN={run_dir}"])
    result = parse_lec_log(log.read_text())
    return LecResult(result.status, result.coverage_pct, result.outputs_checked,
                     result.outputs_total, result.k, str(log), result.reasons)
