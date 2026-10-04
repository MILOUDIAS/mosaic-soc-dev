"""A disconnected-pin count is waived only by the exact pins it names.

Block A's padframe wrapper has eleven _IN terminals (pad input buffers of
output-only pads) that the block never reads. They are block inputs with no
load, so LibreLane counts 11 disconnected pins, 0 critical. At the top level
LibreLane calls a port critical only when EVERY input (or output) is
disconnected, so one undriven status_o bit would also read as non-critical.
A count-only waiver would therefore hide exactly that defect.

The rule, applied identically by the PPA gate (report.signoff_summary) and the
evidence gate (signoff.py): the waiver names its pins, the run's recorded
table must name the same set, and its preconditions must hold on the raw
metrics. The table fixture is a verbatim excerpt of the real LibreLane 3.0
output (box-drawing rows, module name only on a module's first row).
"""

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from harness.evidence.librelane import disconnected_pins  # noqa: E402
from harness.evidence.waivers import IDENTITY_REQUIRED, Waiver, apply_waivers  # noqa: E402

TABLE = """\
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ Macro/Instance ┃ Power Pins ┃ Disconnected ┃ Signal Pins              ┃ Disconnected       ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ mosaic_block_a │ VDD        │              │ boot_select_i            │ spi_flash_cs_o_IN  │
│                │ VSS        │              │ boot_select_i_PD         │ uart_tx_o_IN       │
│                │            │              │ boot_select_i_PU         │                    │
│                │            │              │ uart_tx_o_SL             │                    │
└────────────────┴────────────┴──────────────┴──────────────────────────┴────────────────────┘
"""
KEY = "design__disconnected_pin__count"


def _run(tmp_path, table=TABLE):
    run = tmp_path / "runs" / "r"
    step = run / "49-odb-reportdisconnectedpins"
    step.mkdir(parents=True)
    (step / "full_disconnected_pins_table.txt").write_text(table)
    (run / "resolved.json").write_text(json.dumps({"DESIGN_NAME": "mosaic_block_a"}))
    return run


def _waiver(**over):
    base = dict(metric=KEY, design="mosaic_block_a", accepted_max=2,
                justification="pad readbacks", review_by=dt.date(2099, 1, 1),
                recorded_by="t", evidence="r",
                expected_violators=("spi_flash_cs_o_IN", "uart_tx_o_IN"),
                preconditions={"design__critical_disconnected_pin__count": 0})
    base.update(over)
    return Waiver(**base)


def test_the_table_names_the_top_modules_disconnected_pins(tmp_path):
    assert disconnected_pins(_run(tmp_path)) == ["spi_flash_cs_o_IN", "uart_tx_o_IN"]
    assert disconnected_pins(tmp_path / "nowhere") is None


def _apply(run, waiver, critical=0):
    return apply_waivers(
        [(KEY, 2.0)], [waiver], design="mosaic_block_a",
        identities={KEY: disconnected_pins(run)},
        metrics={KEY: 2, "design__critical_disconnected_pin__count": critical})


def test_an_exact_match_is_waived(tmp_path):
    remaining, waived, notes = _apply(_run(tmp_path), _waiver())
    assert remaining == [] and len(waived) == 1 and notes == []


def test_a_different_pin_at_the_same_count_still_fails(tmp_path):
    table = TABLE.replace("uart_tx_o_IN      ", "status_o_OUT[3]   ")
    remaining, _, notes = _apply(_run(tmp_path, table), _waiver())
    assert remaining == [(KEY, 2.0)] and "violators differ" in notes[0]
    assert "status_o_OUT[3]" in notes[0]


def test_a_count_only_waiver_cannot_excuse_disconnected_pins(tmp_path):
    assert KEY in IDENTITY_REQUIRED
    remaining, _, notes = _apply(_run(tmp_path), _waiver(expected_violators=()))
    assert remaining == [(KEY, 2.0)] and "named violators" in notes[0]


def test_no_recorded_table_means_no_waiver(tmp_path):
    remaining, _, notes = apply_waivers(
        [(KEY, 2.0)], [_waiver()], design="mosaic_block_a",
        identities={KEY: None}, metrics={"design__critical_disconnected_pin__count": 0})
    assert remaining == [(KEY, 2.0)] and "records none" in notes[0]


def test_a_critical_disconnect_withdraws_the_waiver(tmp_path):
    remaining, _, notes = _apply(_run(tmp_path), _waiver(), critical=1)
    assert remaining == [(KEY, 2.0)] and "critical" in notes[0]


def test_the_ppa_gate_uses_the_same_rule(tmp_path):
    """report.signoff_summary excuses the hard check only on an exact match."""
    from harness.physical.report import signoff_summary
    run = _run(tmp_path)
    (run / "final").mkdir()
    metrics = {k: 0 for k in __import__("harness.physical.report", fromlist=["HARD_CHECKS"]).HARD_CHECKS}
    metrics.update({KEY: 2, "design__critical_disconnected_pin__count": 0})
    (run / "final" / "metrics.json").write_text(json.dumps(metrics))
    waivers = tmp_path / "flow" / "librelane" / "signoff_waivers.yaml"
    waivers.parent.mkdir(parents=True)
    waivers.write_text(
        "waivers:\n  - metric: design__disconnected_pin__count\n"
        "    design: mosaic_block_a\n    accepted_max: 2\n    review_by: 2099-01-01\n"
        "    recorded_by: t\n    evidence: r\n"
        "    justification: two pad readbacks the block never reads, named below\n"
        "    expected_violators: [spi_flash_cs_o_IN, uart_tx_o_IN]\n"
        "    preconditions: {design__critical_disconnected_pin__count: 0}\n")
    summary, _ = signoff_summary(run, repo_root=tmp_path)
    assert KEY not in summary["hard_checks_failing"]
    assert [w["metric"] for w in summary["hard_checks_waived"]] == [KEY]
    waivers.write_text(waivers.read_text().replace("uart_tx_o_IN", "'status_o_IN[0]'"))
    summary, _ = signoff_summary(run, repo_root=tmp_path)
    assert KEY in summary["hard_checks_failing"]


def test_the_waiver_audit_reads_the_same_names_the_gates_do(tmp_path):
    """waiver-author verified pinned lists only from STA reports, so a
    disconnected-pin waiver was always 'unverified' there."""
    from harness.skills.waiver_author import _violators_in
    run = _run(tmp_path)
    assert _violators_in(run, KEY) == disconnected_pins(run)
