"""Waivers: bounded, design-scoped, dated, and never silent.

A waiver mechanism is the feature most able to quietly destroy an evidence
gate, so these tests are written against the ways it could go wrong rather
than the way it is meant to be used.

The properties pinned here:

- a waiver is a CEILING, so a regression past the waived count still fails;
- a waiver is scoped to ONE design, so Block A's waivers cannot travel to the
  next design we harden -- which is literally the next thing on the roadmap;
- a waiver EXPIRES, so it cannot outlive the person who granted it;
- a waiver cannot suppress DRC, LVS, timing or antenna verdicts, only findings
  from the generic adverse sweep;
- a malformed waiver file RAISES rather than degrading to "no waivers";
- a waived run is a PASS that says so.
"""

import datetime

import pytest
import yaml

from harness.evidence.librelane import LibreLaneRun
from harness.evidence.status import EvidenceStatus
from harness.evidence.waivers import (
    IDENTITY_REQUIRED,
    WaiverError,
    apply_waivers,
    load_waivers,
    parse_waivers,
)

GOOD = {
    "metric": "design__max_slew_violation__count",
    "design": "mosaic_block_a",
    "accepted_max": 591,
    "review_by": "2026-11-30",
    "recorded_by": "MILOUDIAS",
    "evidence": "flow/librelane/experimental/runs/blocka_signoff",
    "justification": "x" * 60,
}
TODAY = datetime.date(2026, 8, 7)


def waiver(**overrides):
    entry = dict(GOOD)
    entry.update(overrides)
    return parse_waivers({"waivers": [entry]})


# ── the ceiling ──────────────────────────────────────────────────────

def test_a_waiver_accepts_exactly_the_measured_count():
    remaining, waived, _ = apply_waivers(
        [("design__max_slew_violation__count", 591.0)],
        waiver(), design="mosaic_block_a", today=TODAY,
    )
    assert remaining == []
    assert waived[0]["observed"] == 591.0


def test_a_regression_past_the_ceiling_still_fails():
    """592 is not 591. A waiver freezes a defect; it does not absorb growth."""
    remaining, waived, notes = apply_waivers(
        [("design__max_slew_violation__count", 592.0)],
        waiver(), design="mosaic_block_a", today=TODAY,
    )
    assert remaining == [("design__max_slew_violation__count", 592.0)]
    assert waived == []
    assert "exceeds its waived ceiling" in " ".join(notes)


# ── design scoping ───────────────────────────────────────────────────

def test_a_waiver_does_not_travel_to_another_design():
    """The trap this mechanism exists to avoid.

    Phase 1 of the roadmap is hardening a SECOND configuration. If Block A's
    waivers applied to it, the first thing we would learn about that design is
    a lie.
    """
    remaining, waived, notes = apply_waivers(
        [("design__max_slew_violation__count", 591.0)],
        waiver(), design="mosaic_picorv32_soc", today=TODAY,
    )
    assert remaining == [("design__max_slew_violation__count", 591.0)]
    assert waived == []
    assert "different design" in " ".join(notes)


def test_an_unknown_design_waives_nothing():
    """No run-bound identity means no waiver can match."""
    remaining, waived, _ = apply_waivers(
        [("design__max_slew_violation__count", 591.0)],
        waiver(), design=None, today=TODAY,
    )
    assert remaining and waived == []


# ── expiry ───────────────────────────────────────────────────────────

def test_an_expired_waiver_stops_applying():
    remaining, waived, notes = apply_waivers(
        [("design__max_slew_violation__count", 591.0)],
        waiver(), design="mosaic_block_a",
        today=datetime.date(2026, 12, 1),
    )
    assert remaining and waived == []
    assert "expired" in " ".join(notes)


def test_the_review_date_itself_is_still_valid():
    _, waived, _ = apply_waivers(
        [("design__max_slew_violation__count", 591.0)],
        waiver(), design="mosaic_block_a",
        today=datetime.date(2026, 11, 30),
    )
    assert len(waived) == 1


# ── what will not load ───────────────────────────────────────────────

@pytest.mark.parametrize("overrides, fragment", [
    ({"justification": "known issue"}, "at least"),
    ({"justification": ""}, "at least"),
    ({"metric": "design__max_*"}, "pattern"),
    ({"accepted_max": -1}, "negative"),
    ({"accepted_max": "many"}, "must be a number"),
    ({"review_by": "soon"}, "ISO date"),
    ({"recorded_by": ""}, "non-empty"),
    ({"evidence": ""}, "non-empty"),
    ({"design": ""}, "non-empty"),
])
def test_invalid_waivers_are_refused(overrides, fragment):
    with pytest.raises(WaiverError) as excinfo:
        waiver(**overrides)
    assert fragment in str(excinfo.value)


def test_every_field_is_required():
    for field in GOOD:
        entry = {k: v for k, v in GOOD.items() if k != field}
        with pytest.raises(WaiverError, match="missing required key"):
            parse_waivers({"waivers": [entry]})


def test_an_unknown_key_is_refused():
    with pytest.raises(WaiverError, match="unknown key"):
        waiver(reason="typo for justification")


def test_two_ceilings_for_one_metric_are_ambiguous():
    with pytest.raises(WaiverError, match="duplicate waiver"):
        parse_waivers({"waivers": [dict(GOOD), dict(GOOD)]})


def test_a_malformed_file_raises_rather_than_waiving_nothing(tmp_path):
    """Degrading to "no waivers" would turn a typo into an unexplained FAIL."""
    path = tmp_path / "w.yaml"
    path.write_text("waivers: [oops\n")
    with pytest.raises(WaiverError):
        load_waivers(path)


def test_a_missing_file_is_simply_no_waivers(tmp_path):
    assert load_waivers(tmp_path / "absent.yaml") == []


# ── what a waiver may not touch ──────────────────────────────────────

def _run(tmp_path, metrics, design="mosaic_block_a"):
    return LibreLaneRun(run_dir=tmp_path, metrics=metrics, design_name=design)


def test_a_waiver_cannot_suppress_a_drc_violation(tmp_path):
    """Waivers apply to the generic sweep only, never to a first-class check."""
    from harness.evidence.signoff import _from_librelane_run

    result = _from_librelane_run(
        _run(tmp_path, {
            "magic__drc_error__count": 3,
            "design__lvs_error__count": 0,
        }),
        require_drc=True, require_lvs=True,
        require_timing=False, require_antenna=False,
        waivers=parse_waivers({"waivers": [
            dict(GOOD, metric="magic__drc_error__count", accepted_max=99)
        ]}),
    )
    assert result.status is EvidenceStatus.FAIL
    assert result.drc_violations == 3
    assert result.waived == []


def test_a_waived_run_passes_and_says_so(tmp_path):
    from harness.evidence.signoff import _from_librelane_run

    result = _from_librelane_run(
        _run(tmp_path, {
            "magic__drc_error__count": 0,
            "klayout__drc_error__count": 0,
            "design__lvs_error__count": 0,
            "design__max_slew_violation__count": 591,
        }),
        require_drc=True, require_lvs=True,
        require_timing=False, require_antenna=False,
        waivers=waiver(),
    )
    assert result.status is EvidenceStatus.PASS
    assert [r["metric"] for r in result.waived] == [
        "design__max_slew_violation__count"
    ]
    assert "accepted under recorded waiver" in " ".join(result.reasons)
    assert "signoff_waived" in result.as_metrics()


# ── the file we actually ship ────────────────────────────────────────

def test_the_shipped_waiver_file_is_valid_and_bounded():
    from harness.core import REPO_ROOT

    path = REPO_ROOT / "flow/librelane/signoff_waivers.yaml"
    waivers = load_waivers(path)
    # Both max-slew waivers were RETIRED 2026-08-16 -- not argued down, measured
    # to zero. They accepted violations of `set_max_transition 4.0
    # [current_design]`, the tt_025C_5v00 number applied at all nine corners,
    # while every violation sat at ss_125C_4v50 where the pins are rated to
    # 7.0 ns. Signoff now uses each pin's own liberty limit (SIGNOFF_SDC_FILE)
    # and all three designs report 0 with byte-identical netlists.
    #
    # max-fanout does NOT follow: the GF180 libraries declare no max_fanout at
    # all, so MAX_FANOUT_CONSTRAINT: 10 overrides nothing and its violations are
    # real. Checked, not assumed by symmetry.
    #
    # design__disconnected_pin__count joined 2026-09-24: the D15 padframe's
    # eleven _IN readbacks of output-only pads. A hard check, so it is waived
    # only by the exact pin names (IDENTITY_REQUIRED), never by count.
    assert {w.metric for w in waivers} == {"design__max_fanout_violation__count",
                                           "design__disconnected_pin__count"}
    for entry in waivers:
        if entry.metric in IDENTITY_REQUIRED:
            assert entry.pinned, f"{entry.metric} can only be waived by name"
    # Every waiver names a design that has actually been hardened. A waiver
    # for a design nobody built is a waiver nobody measured.
    assert {w.design for w in waivers} == {"mosaic_block_a"}
    for entry in waivers:
        # Each justification must name what the waiver does NOT cover.
        assert "NOT waived" in entry.justification, (
            f"{entry.metric}: a waiver must state its own limits"
        )
        assert entry.review_by > datetime.date(2026, 8, 7)


def test_the_hardening_flows_declare_the_waiver_file():
    from harness.skills.flow_runner import FLOWS

    for name in ("harden-classic", "harden-chip"):
        assert FLOWS[name]["waivers"] == "flow/librelane/signoff_waivers.yaml"


def test_shipped_waivers_are_inert_for_any_other_design():
    """Re-stated against the real file, because this is the expensive mistake."""
    from harness.core import REPO_ROOT

    waivers = load_waivers(REPO_ROOT / "flow/librelane/signoff_waivers.yaml")
    findings = [("design__max_slew_violation__count", 591.0),
                ("design__max_fanout_violation__count", 1.0)]
    remaining, waived, _ = apply_waivers(
        findings, waivers, design="some_future_block", today=TODAY)
    assert waived == []
    assert sorted(remaining) == sorted(findings)


def test_the_waiver_file_parses_as_plain_yaml():
    """Guards against a justification block that silently swallows a key."""
    from harness.core import REPO_ROOT

    data = yaml.safe_load(
        (REPO_ROOT / "flow/librelane/signoff_waivers.yaml").read_text())
    # One, since the two max-slew waivers were retired 2026-08-16.
    assert isinstance(data["waivers"], list) and len(data["waivers"]) == 2
    # Every record must carry all its keys -- the failure this guards against
    # is a `>` block absorbing the next key into the justification text.
    for entry in data["waivers"]:
        assert set(entry) >= {"metric", "design", "accepted_max", "review_by",
                              "recorded_by", "evidence", "justification"}
