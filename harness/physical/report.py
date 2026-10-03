"""Read a finished run's signoff numbers, typed, and diff two runs.

Every experiment in this project has ended with the same manual step: open two
`metrics.json` files, pull the same dozen keys out of each, convert um2 to mm2
by hand, and write the comparison into prose. That is how a confounded
experiment went unnoticed for a full run -- the die had moved and nothing
printed the die.

So this prints them, with units, from the typed layer, and takes a second run
to compare against.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from harness.evidence.metric import MM2, Metric, from_librelane
from harness.evidence.workload import power_metric_status

# The keys a signoff verdict actually turns on. Hard checks first: these are
# the ones where any nonzero value is a failure, not a trade.
HARD_CHECKS: Tuple[str, ...] = (
    "magic__drc_error__count",
    "klayout__drc_error__count",
    "magic__illegal_overlap__count",
    "design__lvs_error__count",
    "design__lvs_unmatched_net__count",
    "design__lvs_unmatched_pin__count",
    "design__lvs_unmatched_device__count",
    "design__xor_difference__count",
    "route__drc_errors",
    "route__antenna_violation__count",
    "design__disconnected_pin__count",
    "design__power_grid_violation__count",
)

# Recorded, gated differently, and traded against area.
QUALITY: Tuple[str, ...] = (
    "timing__setup__ws",
    "timing__hold__ws",
    "design__max_slew_violation__count",
    "design__max_cap_violation__count",
    "design__max_fanout_violation__count",
)

AREA: Tuple[str, ...] = ("design__core__area", "design__die__area")

# Reported, and NOT a measurement of what the design does. LibreLane runs
# `report_power` with no activity input -- no read_vcd, no read_saif, no
# set_power_activity -- so OpenSTA uses its default toggle model. The
# signature is visible in any run's power.rpt: combinational switching is
# ~0.5% of total while clock and sequential are ~99.5%. That is a clock-tree
# cost, not workload power, and it is labelled here so it cannot be quoted as
# the latter. See harness/evidence/workload.py.
POWER: Tuple[str, ...] = (
    "power__total", "power__internal__total",
    "power__switching__total", "power__leakage__total",
)

# Physical-only cells scale with the die rather than the design, so the logic
# figure the area model calibrates on excludes them.
_FILLER = {"fill_cell", "tap_cell", "endcap_cell", "antenna_cell"}
_CLASS = "design__instance__area__class:"


def logic_area_um2(metrics: Dict[str, Any]) -> Optional[float]:
    """Post-CTS logic area: the quantity CALIBRATION is fitted on."""
    total = 0.0
    seen = False
    for key, value in metrics.items():
        if not key.startswith(_CLASS):
            continue
        if key[len(_CLASS):] in _FILLER:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
            seen = True
    return total if seen else None


def _collect(metrics: Dict[str, Any], keys, source: str,
             pdk: Optional[str]) -> Dict[str, Metric]:
    out: Dict[str, Metric] = {}
    for key in keys:
        if key in metrics:
            metric = from_librelane(key, metrics[key], source=source, pdk=pdk)
            if metric is not None:
                out[key] = metric
    return out


def signoff_summary(
    run_dir: Path, *, pdk: Optional[str] = None,
    compare: Optional[Path] = None, repo_root: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """The numbers a signoff decision rests on, with units and provenance."""
    from harness.core import REPO_ROOT
    from harness.evidence.librelane import load_design_name, load_metrics

    repo_root = repo_root or REPO_ROOT
    metrics, source = load_metrics(run_dir)
    if not metrics:
        return None, [f"no metrics.json under {run_dir}; the run did not finish"]
    source = source or str(run_dir)

    hard = _collect(metrics, HARD_CHECKS, source, pdk)
    quality = _collect(metrics, QUALITY, source, pdk)
    area = _collect(metrics, AREA, source, pdk)
    logic = logic_area_um2(metrics)

    summary: Dict[str, Any] = {
        "run": str(run_dir),
        "design": load_design_name(run_dir),
        "pdk": pdk,
        "source": source,
        "hard_checks": {k: m.value for k, m in hard.items()},
        # Reported in mm2 because that is the unit anyone reasons in, and the
        # conversion is done by the type rather than by hand.
        "area_mm2": {k: round(m.to(MM2).value, 4) for k, m in area.items()},
        "logic_um2": None if logic is None else round(logic, 0),
        "quality": {k: m.value for k, m in quality.items()},
    }

    power = _collect(metrics, POWER, source, pdk)
    if power:
        status, reasons = power_metric_status(None)
        summary["power_watts"] = {k: m.value for k, m in power.items()}
        summary["power_basis"] = status
        summary["power_caveat"] = reasons
    # GLS is REPORTED, and does NOT block. It was a hard check for one day.
    #
    # It was promoted on 2026-08-12 because a netlist passed DRC, LVS, XOR,
    # antenna, routing DRC and timing at every corner and did not boot. That
    # observation was real; the inference was not. The failing netlist and the
    # booting one hold the SAME 36,572 logic instances -- identical names and
    # functions, empty diff -- and differ only in buffer, inverter, delay,
    # clkbuf and fill cells, every one of which is transparent in a zero-delay
    # simulation. They must behave identically. One did not.
    #
    # The cause is the oracle: these flops are UDP sequential primitives
    # compiled -DFUNCTIONAL, which strips the specify blocks carrying the
    # CLK->Q delay. Zero clock-to-Q with UDP flops is the textbook race, and
    # buffering changes event ordering without changing logic.
    #
    # A gate must be SOUND to be hard. This one demonstrably produces false
    # failures on provably-equivalent inputs, and a hard gate that does that
    # teaches people to bypass gates -- which costs more than the check is
    # worth. So it is reported prominently, with its reasons, and it is not
    # counted in `hard_checks_failing`.
    #
    # `blocks_signoff` is still computed and still published, because the
    # three-valued status is worth keeping and NOT_RUN must remain visibly
    # different from PASS.
    #
    # THE BLOCKER RECORDED HERE IS CLEARED (2026-09-07). It said this oracle
    # produces false failures on provably-equivalent inputs, and it did -- but
    # the cause was in the testbench, not in the delay model. gls_tb.sv
    # deasserted rst_n in the same timestep as the capture edge, so the
    # dffrnq/dffsnq flops saw RN rise as CLK rose and the winner was decided by
    # the delta-cycle depth of the reset tree versus the clock tree, i.e. by
    # buffering. Released half a cycle away, the two netlists that disagreed
    # now both boot in 12 400 cycles. Timing annotation was never the fix.
    #
    # `gates_signoff` stays False anyway, because promoting a check from
    # advisory to blocking is a policy decision and not this module's to make
    # on the strength of one repaired case. The technical objection is gone;
    # what remains is the choice. See tb/gls/README.md.
    from harness.evidence.gls import gls_for_run

    gls = gls_for_run(run_dir, repo_root=repo_root)
    summary["gls"] = {
        "status": gls.status.value,
        "cycles": gls.cycles,
        "log": gls.log,
        "reasons": gls.reasons,
        # Kept for callers that want the three-valued judgement, renamed so it
        # cannot be mistaken for "this run is blocked".
        "adverse": gls.blocks_signoff,
        "gates_signoff": False,
    }

    # Equivalence is reported beside GLS but does NOT block: kepler-formal on
    # a 70k-cell design is unproven at this scale, so making it a hard check
    # today would fail every run for want of a tool result rather than for a
    # defect. It blocks once it has been shown to converge here.
    from harness.evidence.lec import lec_for_run

    lec = lec_for_run(run_dir, repo_root=repo_root)
    summary["lec"] = {
        "status": lec.status.value,
        "coverage_pct": lec.coverage_pct,
        "log": lec.log,
        "reasons": lec.reasons,
    }

    failing = [k for k, m in hard.items() if m.value]
    # A hard check is excused only the way the evidence gate excuses it:
    # apply_waivers with the run's recorded identities and raw metrics, which
    # waives a disconnected-pin count only by the exact pin names.
    from harness.evidence.librelane import disconnected_pins
    from harness.evidence.waivers import apply_waivers, load_waivers
    still, waived, notes = apply_waivers(
        [(k, hard[k].value) for k in failing],
        load_waivers(repo_root / "flow/librelane/signoff_waivers.yaml"),
        design=summary.get("design"),
        identities={"design__disconnected_pin__count": disconnected_pins(run_dir)},
        metrics=metrics)
    failing = [k for k, _ in still]
    summary["hard_checks_failing"] = failing
    summary["hard_checks_waived"] = waived
    summary["waiver_notes"] = notes
    summary["adverse"] = len(failing)

    if compare is not None:
        other, other_source = load_metrics(compare)
        if not other:
            summary["compare_error"] = f"no metrics under {compare}"
            return summary, []
        other_logic = logic_area_um2(other)
        deltas: Dict[str, Any] = {}
        for key in QUALITY + AREA:
            if key in metrics and key in other:
                was, now = other[key], metrics[key]
                if isinstance(was, (int, float)) and isinstance(now, (int, float)):
                    deltas[key] = {"was": was, "now": now,
                                   "delta": round(now - was, 6)}
        if logic and other_logic:
            deltas["logic_um2"] = {
                "was": round(other_logic), "now": round(logic),
                "delta_pct": round(100 * (logic - other_logic) / other_logic, 2),
            }
        summary["compare"] = {"run": str(compare), "source": other_source,
                              "deltas": deltas}
    return summary, []
