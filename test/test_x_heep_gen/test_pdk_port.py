"""A PDK the tools have never measured must not get a die out of them.

Before the guard these tests cover, changing one config key to `pdk: sky130`
returned the identical GF180 die with basis="measured". That is the failure
mode: not a crash, not an obviously wrong number, but another process's answer
carrying a label that asserts it was measured.
"""
from __future__ import annotations

import copy

from pathlib import Path

import pytest
import yaml

from harness.core import REPO_ROOT
from harness.physical.floorplan import (
    CALIBRATED_PDKS, CALIBRATION, calibration_gap, derive_floorplan,
)
from harness.skills.pdk_port import GF180_7T, REQUIREMENTS, audit, survey


def _cfg(pdk):
    c = yaml.safe_load((REPO_ROOT / "configs/mosaic_tapeout_ultra.yaml").read_text())
    c["soc"]["pdk"] = pdk
    return c["soc"]


# ── the guard ────────────────────────────────────────────────────────
def test_the_calibrated_pdk_still_sizes():
    fp, errs = derive_floorplan(_cfg("gf180mcu"), target_utilisation=0.823)
    assert fp is not None, errs
    assert fp.die_side_um > 0


@pytest.mark.parametrize("pdk", ["sky130", "ihp-sg13g2", "asap7"])
def test_an_uncalibrated_pdk_is_refused_not_answered(pdk):
    fp, errs = derive_floorplan(_cfg(pdk), target_utilisation=0.823)
    assert fp is None, f"{pdk} got a die out of GF180 calibration"
    assert errs and "no area calibration" in errs[0]
    assert pdk in errs[0]


def test_the_refusal_says_what_would_fix_it():
    _, errs = derive_floorplan(_cfg("sky130"), target_utilisation=0.823)
    assert "Harden one design on this PDK" in errs[0]


def test_two_pdks_no_longer_get_the_same_die():
    """The exact demonstration that motivated this."""
    gf, _ = derive_floorplan(_cfg("gf180mcu"), target_utilisation=0.823)
    sky, errs = derive_floorplan(_cfg("sky130"), target_utilisation=0.823)
    assert gf is not None and sky is None, (
        "the same design on two processes must not yield one answer")


def test_a_config_with_no_pdk_is_not_refused():
    """Absent is not the same as wrong; the default path must still work."""
    assert calibration_gap(None) is None
    assert calibration_gap("") is None


# ── calibration provenance ───────────────────────────────────────────
def test_every_calibration_row_records_its_technology():
    for m in CALIBRATION:
        assert m.technology, f"{m.run_tag} does not say what it was measured on"
        assert ":" in m.technology, (
            f"{m.run_tag} records a PDK but not a cell library; site height "
            "differs between libraries in one PDK")


def test_calibrated_pdks_is_derived_not_hardcoded():
    assert CALIBRATED_PDKS == {m.technology.split(":", 1)[0] for m in CALIBRATION}


# ── the survey ───────────────────────────────────────────────────────
needs_pdk = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2]
         / "flow/librelane/gf180mcu").is_dir(),
    reason="GF180 PDK not cloned (make -C flow/librelane clone-pdk)")


@needs_pdk
def test_gf180_satisfies_every_requirement():
    rep = survey(GF180_7T)
    assert rep.ready
    assert len(rep.satisfied) == len(REQUIREMENTS)


@needs_pdk
def test_gf180_readiness_is_qualified_rather_than_celebrated():
    """"All eight met" must not read as "portable".

    This used to assert the word "hardcoded", because five requirements were
    satisfied only by GF180 constants with nowhere to put a second PDK's
    answer. They now have real per-technology storage
    (harness/physical/technology.py), so that note is gone and its absence is
    correct. What still qualifies the result is the narrowness of the
    evidence: three runs, one design family.
    """
    rep = survey(GF180_7T)
    assert rep.ready
    assert any("THREE hardened runs" in n for n in rep.notes)
    assert not any("hardcoded" in n for n in rep.notes), (
        "site geometry and corner names are per-technology now; a stale "
        "note here would understate what was done")


def test_an_unported_pdk_reports_its_silent_gaps():
    rep = survey("sky130")
    assert not rep.ready
    assert rep.silent_gaps, "the dangerous gaps are the silent ones"
    assert {r.key for r in rep.silent_gaps} <= {r.key for r in REQUIREMENTS}


def test_a_pdk_only_argument_is_reported_as_partial():
    """It cannot see a cell-library swap inside one PDK."""
    rep = survey("gf180mcu")
    assert any("cell libraries within one PDK" in n for n in rep.notes)


def test_the_skill_result_lists_only_silent_gaps_as_errors():
    r = audit("sky130")
    assert not r.ok
    assert r.errors
    assert len(r.errors) == len(survey("sky130").silent_gaps)


def test_the_skill_is_declared_read_only():
    from harness.flow_spec import Effect
    from harness.skill_policy import SKILL_SPECS
    spec = SKILL_SPECS["pdk-port"]
    assert spec.effect is Effect.READ
    assert spec.approval is False
