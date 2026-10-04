"""The activity-power producer must emit evidence, not four floats.

Until 2026-09-21 `power_from_activity.tcl` called read_liberty, read_spef and
report_power with no `-corner` and no `define_corners`, so all nine liberty
files shared one implicit corner and the reported number belonged to whichever
won -- decided by key order in resolved.json. It also wrote no file, printing
to stdout only. `harness/evidence/power.py` refuses a report with no corner
banner and `Metric` refuses power with no corner, so nothing the producer
emitted could ever become evidence. These tests hold the producer to the
contract the evidence layer already enforced.
"""
from __future__ import annotations

import json

import pytest

from harness.evidence.power import PowerReportError, power_reports_for_run
from harness.physical.power import (corner_names, split_reports,
                                   write_reports)

# The row shape OpenSTA really prints, with the banner LibreLane wraps it in.
_ROWS = """\
Group                  Internal  Switching    Leakage      Total
                          Power      Power      Power      Power (Watts)
----------------------------------------------------------------
Sequential             4.922040e-02 1.267514e-05 6.536210e-06 4.923961e-02  52.3%
Combinational          1.100000e-02 7.600000e-03 2.900000e-05 1.862900e-02  19.8%
Clock                  1.068000e-02 2.520000e-02 7.000000e-06 3.588700e-02  27.9%
Macro                  0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Pad                    0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
----------------------------------------------------------------
Total                  7.090000e-02 2.030000e-02 4.270000e-05 9.124000e-02 100.0%
"""


def _stdout(*pairs):
    out = []
    for basis, corner in pairs:
        out += [f"### MOSAIC_REPORT basis={basis} corner={corner}",
                f"======================= {corner} Corner =======================",
                _ROWS, "### MOSAIC_REPORT_END"]
    return "\n".join(out) + "\n"


def _run_with_lib(tmp_path, keys=("*_tt_025C_5v00", "*_ff_n40C_5v50")):
    (tmp_path / "resolved.json").write_text(json.dumps(
        {"DESIGN_NAME": "d", "LIB": {k: [f"/pdk/lib{i}.lib"]
                                     for i, k in enumerate(keys)}}))
    return tmp_path


# ── naming the corners ───────────────────────────────────────────────
def test_corners_are_named_from_the_runs_own_libraries(tmp_path):
    run = _run_with_lib(tmp_path)
    assert corner_names(run, "nom") == ["nom_tt_025C_5v00", "nom_ff_n40C_5v50"]
    assert corner_names(run, "max") == ["max_tt_025C_5v00", "max_ff_n40C_5v50"]


def test_a_run_with_no_libraries_names_no_corners(tmp_path):
    assert corner_names(tmp_path) == []
    (tmp_path / "resolved.json").write_text(json.dumps({"DESIGN_NAME": "d"}))
    assert corner_names(tmp_path) == []


# ── the split, and one report per file ───────────────────────────────
def test_each_basis_and_corner_becomes_its_own_report():
    marked = split_reports(_stdout(("default", "nom_tt_025C_5v00"),
                                   ("workload", "nom_tt_025C_5v00"),
                                   ("workload", "nom_ss_125C_4v50")))
    assert sorted(marked) == ["default", "workload"]
    assert sorted(marked["workload"]) == ["nom_ss_125C_4v50",
                                          "nom_tt_025C_5v00"]
    # each holds exactly ONE banner, or the typed parser would read rows from
    # two corners into a single result and the last would silently win
    for text in marked["workload"].values():
        assert text.count("Corner ==") == 1


def test_unmarked_output_yields_nothing_rather_than_a_guess():
    assert split_reports("Total 1.0 2.0 3.0 4.0\n") == {}


# ── the contract with the evidence layer ─────────────────────────────
def test_what_the_producer_writes_is_what_the_evidence_layer_reads(tmp_path):
    """The load-bearing join: producer output -> typed PowerReports, keyed by
    the corner the report itself declares."""
    marked = split_reports(_stdout(("default", "nom_tt_025C_5v00"),
                                   ("workload", "nom_tt_025C_5v00"),
                                   ("workload", "nom_ss_125C_4v50")))
    written = write_reports(marked, tmp_path)
    assert len(written) == 3
    assert (tmp_path / "workload/nom_ss_125C_4v50/power.rpt").is_file()

    reports = power_reports_for_run(tmp_path / "workload")
    assert sorted(reports) == ["nom_ss_125C_4v50", "nom_tt_025C_5v00"]
    tt = reports["nom_tt_025C_5v00"]
    assert tt.corner == "nom_tt_025C_5v00"
    assert tt.total.total == pytest.approx(9.124e-02)
    assert tt.groups["sequential"].internal == pytest.approx(4.922040e-02)
    # and every metric it yields carries that corner, which is what the corner rule requires
    assert {m.corner for m in tt.metrics()} == {"nom_tt_025C_5v00"}


def test_a_report_without_its_corner_is_still_refused(tmp_path):
    """The regression that matters: if the producer ever goes back to emitting
    a corner-less report, the evidence layer must refuse it."""
    corner_dir = tmp_path / "workload" / "whatever"
    corner_dir.mkdir(parents=True)
    (corner_dir / "power.rpt").write_text(_ROWS)      # rows, no banner
    with pytest.raises(PowerReportError) as caught:
        power_reports_for_run(tmp_path / "workload")
    assert "no corner banner" in str(caught.value)


def test_a_relative_run_directory_still_finds_its_artifacts(tmp_path, monkeypatch):
    """run_power_analysis launches openroad with cwd=flow/librelane, so a
    relative --run-dir resolved against THAT and the tool reported the ODB
    missing while it sat in plain sight (ORD-0007, Block C, 2026-09-22)."""
    import os

    from harness.physical import power as power_mod

    run = tmp_path / "runs" / "d"
    for sub in ("final/odb", "final/sdc", "final/spef/nom"):
        (run / sub).mkdir(parents=True)
    (run / "final/odb/d.odb").write_text("x")
    (run / "final/sdc/d.sdc").write_text("x")
    (run / "final/spef/nom/d.spef").write_text("x")
    lib = tmp_path / "pdk" / "d__tt_025C_5v00.lib"
    lib.parent.mkdir()
    lib.write_text("library(d){}")
    (run / "resolved.json").write_text(json.dumps(
        {"DESIGN_NAME": "d", "LIB": {"*_tt_025C_5v00": [str(lib)]}}))

    seen = {}

    def fake_run(argv, **kw):
        seen["env"] = kw.get("env", {})
        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(power_mod.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    from pathlib import Path as _P
    power_mod.run_power_analysis(_P("runs/d"), repo_root=tmp_path)
    assert os.path.isabs(seen["env"]["MOSAIC_ODB"]), seen["env"]["MOSAIC_ODB"]
    assert seen["env"]["MOSAIC_ODB"].endswith("runs/d/final/odb/d.odb")
