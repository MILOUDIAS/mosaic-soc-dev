"""Did the routed netlist actually boot? Signoff does not answer that.

WHY THIS IS PART OF SIGNOFF NOW
-------------------------------
On 2026-08-12 `runs/blocka_reharden` passed every check the flow has: Magic
DRC 0, KLayout DRC 0, LVS 0 with no unmatched nets, XOR 0, antenna 0, routing
DRC 0, disconnected pins 0, power-grid 0, setup +20.94 ns, hold +0.0667 ns.

It does not boot -- OR SO THIS SAID FOR THREE WEEKS; see the correction below
before quoting this paragraph. Gate-level simulation of that netlist never
asserts the flash chip-select and never fetches an instruction. The control --
the original netlist, same testbench, same firmware, same power-up state --
reaches EXIT SUCCESS in 12 399 cycles.

The reason the flow could not see this is worth stating exactly, because it is
a category error and not an oversight: **LVS proves the layout matches the
netlist. It says nothing about whether the netlist matches the RTL.** Between
RTL and netlist sit synthesis, CTS, resizing and repair, and nothing in this
flow checked that they preserved behaviour. GLS did, and GLS was something you
had to remember to run.

THE THREE-VALUED ANSWER
-----------------------
`NOT_RUN` is a distinct status from `FAIL`, and neither is `PASS`. A signoff
summary that omitted GLS entirely read as though the question had been
answered; one that defaulted it to pass would be worse. The whole point is
that absence of the check is visible.

AND THE FAILURE ABOVE WAS THE BENCH (resolved 2026-09-07)
--------------------------------------------------------
Keep the paragraph above -- the reasoning that GLS belongs in signoff stands --
but the specific netlist it was written about was never broken. `gls_tb.sv`
deasserted `rst_n` in the same timestep as the capture edge, so every
asynchronously-reset flop saw RN rise as CLK rose, and the winner was decided
by the delta-cycle depth of the reset tree versus the clock tree: by buffering.
Two netlists identical in logic, connectivity and cell function disagreed.
Released half a cycle away, both boot in 12 400 cycles.

WHICH ORACLE PRODUCED THE LOG
-----------------------------
There is a second, independent race, and it is the one `### seq c2q` is about.
Compiled `-DFUNCTIONAL` the flops are UDP primitives with zero clock-to-Q, and
zero-delay UDP flops race on data capture. `run_gls.sh` gives them a nonzero
clock-to-Q and records it; `race_prone` reports whether that was done.

Read `race_prone` narrowly. It means "a data-capture race could have decided
this verdict", not "this verdict is sound" -- measured: the zero-delay oracle
reaches the right answer on both netlists once the bench is fixed, and the
race-free one reaches the wrong one while the bench is broken. No flag in this
module can certify an oracle. Logs written before the header existed report
`seq_delay_ns=None` and are treated as race-prone, because that is what they
were.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

_PASS = re.compile(r"RESULT:\s*gate-level simulation PASSED")
_EXIT_SUCCESS = re.compile(r"EXIT SUCCESS")
_FAIL = re.compile(r"RESULT:\s*(?:gate-level simulation FAILED|FAIL\b)")
_CYCLES = re.compile(r"after (\d+) cycles")
_NETLIST = re.compile(r"### netlist\s*:\s*(\S+)")
# `### seq c2q : 1 ns on 18 sequential UDPs` / `### seq c2q : zero (races: ...)`
_SEQ_C2Q = re.compile(r"### seq c2q\s*:\s*(zero|[0-9.]+)")


class GlsStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class GlsResult:
    status: GlsStatus
    log: Optional[str] = None
    cycles: Optional[int] = None
    netlist: Optional[str] = None
    reasons: List[str] = None            # type: ignore[assignment]
    seq_delay_ns: Optional[float] = None

    def __post_init__(self) -> None:
        if self.reasons is None:
            object.__setattr__(self, "reasons", [])

    @property
    def blocks_signoff(self) -> bool:
        """Anything other than a pass does. NOT_RUN is not a pass."""
        return self.status is not GlsStatus.PASS

    @property
    def race_prone(self) -> bool:
        """Could buffering alone have decided this verdict?

        True for zero clock-to-Q, and true for a log that does not say --
        every log written before run_gls.sh recorded the oracle was zero
        clock-to-Q, so silence means the race, not the fix.
        """
        return not self.seq_delay_ns


def parse_gls_log(text: str) -> GlsResult:
    """Read a gate-level run's log.

    The oracle is the log marker, not an exit status: the simulator exits 0
    having printed FAIL, which is why run_gls.sh greps for the marker too.
    """
    reasons: List[str] = []
    cycles = _CYCLES.search(text)
    netlist = _NETLIST.search(text)
    seq = _SEQ_C2Q.search(text)
    seq_ns = None if seq is None else (
        0.0 if seq.group(1) == "zero" else float(seq.group(1)))
    if _PASS.search(text) or (_EXIT_SUCCESS.search(text) and not _FAIL.search(text)):
        status = GlsStatus.PASS
    elif _FAIL.search(text):
        status = GlsStatus.FAIL
        reasons.append(
            "the routed netlist did not reach EXIT SUCCESS. Signoff checks "
            "cannot see this: LVS proves the layout matches the netlist, not "
            "that the netlist matches the RTL")
        if not seq_ns:
            # The 2026-08-12 false alarm, in one line. Do not let a zero-delay
            # FAIL be attributed to the design without this being said.
            reasons.append(
                "this verdict came from the ZERO clock-to-Q oracle "
                + ("(the log predates the `### seq c2q` line)" if seq is None
                   else "(GLS_SEQ_DELAY=0)")
                + ", where UDP flops race and buffering alone can decide what "
                "a flop samples. Reproduce it with GLS_SEQ_DELAY=1 before "
                "attributing it to the netlist")
    else:
        status = GlsStatus.NOT_RUN
        reasons.append("no GLS result marker in the log")
    return GlsResult(status=status,
                     cycles=int(cycles.group(1)) if cycles else None,
                     netlist=netlist.group(1) if netlist else None,
                     reasons=reasons, seq_delay_ns=seq_ns)


def gls_for_run(run_dir: Path, *, repo_root: Path) -> GlsResult:
    """Whether THIS run's netlist has been gate-level simulated.

    Matching is by netlist identity, not by "a GLS log exists": tb/gls writes
    one log in a fixed place, so a stale log from a different run would
    otherwise be read as evidence about this one. That is precisely the
    mistake that made the re-hardened netlist look fine.
    """
    # The run's OWN copy first. The shared log holds only the last gate-level
    # run, so a verdict there is erased by the next one on any other design --
    # simulating Block C reset Block A to NOT_RUN with nothing else recording
    # it. run_gls.sh drops a copy in the run directory, which no other run can
    # overwrite. The shared log stays as a fallback for runs simulated before
    # that existed.
    #
    # The tag check applies to BOTH: run_gls.sh prints the netlist basename,
    # which is the design name and not the run tag, so the log must name this
    # run explicitly. Reading a log that names another netlist as evidence
    # about this one is the mistake that made a re-hardened netlist look fine.
    shared = repo_root / "tb/gls/sim-gls.log"
    for log in (run_dir / "gls.log", shared):
        if not log.is_file():
            continue
        text = log.read_text()
        if run_dir.name not in text:
            continue            # evidence about a different netlist
        result = parse_gls_log(text)
        return GlsResult(result.status, log=str(log), cycles=result.cycles,
                         netlist=result.netlist, reasons=result.reasons,
                         seq_delay_ns=result.seq_delay_ns)

    why = (f"no gate-level run recorded for {run_dir.name}. Run the `gls` flow "
           f"with GLS_RUN={run_dir}")
    if shared.is_file():
        why += (f" (the shared log at {shared} is evidence about a different "
                "netlist)")
    return GlsResult(GlsStatus.NOT_RUN, reasons=[why])
