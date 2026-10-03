"""Roadmap M2: a number with a unit, a corner, a PDK and a source.

Two of M2's exit criteria are enforced at construction here rather than
audited later:

  * "every numeric metric has a unit and source artifact"
  * "timing/power evidence without corner/voltage is rejected"

The third thing this guards is not in M2 but is in the project's goal: GF180 is
the first PDK, not the only one. A metric recorded without its PDK will be
compared against a different process eventually.
"""

import json

import pytest

from harness.core import REPO_ROOT
from harness.evidence.metric import (
    COUNT,
    FF,
    MM2,
    MW,
    NS,
    PF,
    PS,
    UM2,
    WATT,
    Dimension,
    Metric,
    MetricError,
    from_librelane,
    split_corner,
    typed_metrics,
    unit_coverage,
    unit_for,
)

RUN = REPO_ROOT / "flow/librelane/experimental/runs/blocka_reharden/final/metrics.json"


# ── provenance is not optional ───────────────────────────────────────

def test_a_metric_without_a_source_is_refused():
    """A number with no artefact behind it is not evidence."""
    with pytest.raises(MetricError, match="read from"):
        Metric("design__instance__area", 976364, UM2, source="")


def test_a_timing_measurement_without_a_corner_is_refused():
    """M2: timing/power evidence without corner/voltage is rejected."""
    with pytest.raises(MetricError, match="corner"):
        Metric("timing__setup__ws", 20.94, NS, source="final/metrics.json")

    # With the corner, it is fine.
    ok = Metric("timing__setup__ws", 20.94, NS, source="final/metrics.json",
                corner="nom_tt_025C_5v00")
    assert ok.corner == "nom_tt_025C_5v00"


def test_a_chosen_value_may_be_corner_free_but_must_say_so():
    """CLOCK_PERIOD is a constraint, not a measurement, and the difference
    has to be stated rather than inferred from the name."""
    constraint = Metric("CLOCK_PERIOD", 100.0, NS, source="config.json",
                        kind="constraint")
    assert constraint.corner is None
    with pytest.raises(MetricError, match="unknown kind"):
        Metric("x", 1.0, COUNT, source="s", kind="vibes")


def test_counts_and_areas_need_no_corner():
    Metric("design__max_slew_violation__count", 4, COUNT, source="s")
    Metric("design__instance__area", 976364, UM2, source="s")


# ── conversion cannot cross dimensions ───────────────────────────────

def test_conversion_within_a_dimension():
    area = Metric("die", 2_183_600, UM2, source="s")
    assert area.to(MM2).value == pytest.approx(2.1836)
    assert area.to(MM2).to(UM2).value == pytest.approx(2_183_600)

    slack = Metric("ws", 20.94, NS, source="s", corner="c")
    assert slack.to(PS).value == pytest.approx(20940)


def test_converting_across_dimensions_is_refused():
    """The bug this type exists to make impossible."""
    slack = Metric("ws", 20.94, NS, source="s", corner="c")
    with pytest.raises(MetricError, match="cannot convert"):
        slack.to(UM2)
    area = Metric("die", 100.0, UM2, source="s")
    with pytest.raises(MetricError, match="cannot convert"):
        area.to(NS)


def test_an_unknown_unit_refuses_to_convert():
    """Converting a unit we never established would invent a fact."""
    from harness.evidence.metric import UNKNOWN

    m = Metric("mystery", 3.0, UNKNOWN, source="s")
    with pytest.raises(MetricError, match="unknown"):
        m.to(NS)


def test_base_value_allows_comparison_across_scales():
    assert (Metric("a", 1.0, PF, source="s").base_value
            == pytest.approx(Metric("b", 1000.0, FF, source="s").base_value))
    assert (Metric("a", 1.0, WATT, source="s", corner="c").base_value
            == pytest.approx(Metric("b", 1000.0, MW, source="s", corner="c").base_value))


# ── reading LibreLane's naming convention ────────────────────────────

@pytest.mark.parametrize("key,dimension", [
    ("design__max_slew_violation__count", Dimension.COUNT),
    ("timing__setup__ws__corner:nom_tt_025C_5v00", Dimension.TIME),
    ("timing__setup__wns__corner:x", Dimension.TIME),
    ("timing__hold__tns__corner:x", Dimension.TIME),
    ("design__core__area", Dimension.AREA),
    ("power__internal__total", Dimension.POWER),
    ("design_powergrid__drop__worst", Dimension.VOLTAGE),
    ("design__instance__utilization", Dimension.RATIO),
])
def test_units_are_inferred_from_the_naming_convention(key, dimension):
    assert unit_for(key).dimension is dimension


def test_unrecognised_keys_are_unknown_not_guessed():
    assert unit_for("some__tool__specific__thing").dimension is Dimension.UNKNOWN


def test_the_corner_is_split_off_the_key():
    assert split_corner("timing__setup__ws__corner:max_ss_125C_4v50") == (
        "timing__setup__ws", "max_ss_125C_4v50")
    assert split_corner("design__core__area") == ("design__core__area", None)


def test_non_numeric_entries_are_skipped_not_coerced():
    assert from_librelane("x", "a string", source="s") is None
    assert from_librelane("x", None, source="s") is None
    # bool is an int in Python, and a flag is not a measurement
    assert from_librelane("x", True, source="s") is None


def test_an_unqualified_corner_metric_becomes_a_constraint():
    """LibreLane aggregates the worst corner into an unqualified key.

    Refusing those would discard the numbers the gate actually reads, so they
    are kept and labelled rather than dropped or silently called measurements.
    """
    m = from_librelane("timing__setup__ws", 20.94, source="s")
    assert m is not None and m.kind == "constraint" and m.corner is None
    q = from_librelane("timing__setup__ws__corner:nom_tt", 20.94, source="s")
    assert q is not None and q.kind == "measurement" and q.corner == "nom_tt"


# ── against a real run ───────────────────────────────────────────────

def test_a_real_run_types_without_raising():
    if not RUN.is_file():
        pytest.skip("blocka_reharden run tree not present")
    metrics = json.loads(RUN.read_text())
    typed = typed_metrics(metrics, source=str(RUN), pdk="gf180mcuD")
    assert len(typed) > 300
    assert all(m.source for m in typed)
    assert all(m.pdk == "gf180mcuD" for m in typed)
    # Every corner-dependent MEASUREMENT carries its corner, by construction.
    for m in typed:
        if m.kind == "measurement" and m.unit.dimension.name in {"TIME", "POWER", "VOLTAGE"}:
            assert m.corner


def test_unit_coverage_is_reported_not_asserted_at_100():
    """A test demanding full coverage would be satisfied by guessing.

    This pins the floor so coverage cannot silently regress, and leaves room
    for it to improve honestly.
    """
    if not RUN.is_file():
        pytest.skip("blocka_reharden run tree not present")
    typed, total = unit_coverage(json.loads(RUN.read_text()))
    assert total > 300
    assert typed / total > 0.65, f"unit coverage regressed to {typed}/{total}"


def test_the_pdk_travels_with_the_metric():
    """GF180 is the first PDK, not the only one.

    Almost every physical number this project has measured is process-specific
    — the site size, the 4.0 ns max transition, 0.419 mm2/KB SRAM, the area
    calibration. A metric that does not record its PDK will eventually be
    compared against a different one.
    """
    m = from_librelane("design__core__area", 1169330, source="s", pdk="gf180mcuD")
    assert m is not None and m.pdk == "gf180mcuD"
    assert from_librelane("design__core__area", 1, source="s").pdk is None
