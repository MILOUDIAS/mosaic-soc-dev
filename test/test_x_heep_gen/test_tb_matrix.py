"""tb-matrix tests: registry-derived axes, pairwise coverage guarantees,
oracle validity of every synthesized config, and the tiered runner.

The central promise under test: every legal value pair of every two axes is
either COVERED by a generated config or reported BLOCKED with a reason —
never silently dropped — and every generated config satisfies
validate_soc_config (the single schema+topology oracle).

Run from the repo root: python3 -m pytest test/test_x_heep_gen/test_tb_matrix.py
"""

import json
import pathlib
import subprocess
import sys

import pytest

directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(directory))

from harness.skills import tb_matrix as tbm  # noqa: E402
from harness.skills.tb_matrix import (  # noqa: E402
    TbMatrix,
    canonical,
    conflict,
    contract_params,
    derive_axes,
    pairwise_rows,
    sim_boundary_rows,
    synth_config,
)
from util.xheep_gen.core_registry import (  # noqa: E402
    CORE_SPECS,
    VALID_BUS,
    VALID_SCHED_MODES,
    validate_soc_config,
)

REPO_ROOT = directory


# ── Axes are single-sourced from the registry ────────────────────────

def test_axes_track_the_registry():
    axes = derive_axes()
    assert axes["titan_ip"] == sorted(CORE_SPECS)
    assert axes["worker_ip"] == sorted(CORE_SPECS)
    assert axes["second_worker"] == ["none"] + sorted(CORE_SPECS)
    assert axes["bus"] == sorted(VALID_BUS)
    assert axes["sched_mode"] == sorted(VALID_SCHED_MODES)


def test_new_registry_core_enters_the_matrix(monkeypatch):
    """A core added via wrapper-smith must appear with zero tb-matrix edits."""
    fake = dict(CORE_SPECS)
    fake["fakecore99"] = CORE_SPECS["hazard3"]
    monkeypatch.setattr(tbm, "CORE_SPECS", fake)
    axes = derive_axes()
    assert "fakecore99" in axes["titan_ip"]
    assert "fakecore99" in axes["worker_ip"]
    assert "fakecore99" in axes["second_worker"]


# ── ISA contract synthesis ───────────────────────────────────────────

@pytest.mark.parametrize("ip", sorted(CORE_SPECS))
def test_contract_params_validate_for_every_supported_isa(ip):
    """Every (core, ISA) pair the registry allows must synthesize a config
    that the oracle accepts — contract_params mirrors the cross-field rules."""
    for isa in sorted(CORE_SPECS[ip].isas):
        entry = {"ip": ip, "isa": isa, "count": 1, "role": "nano",
                 "boot_addr": 0x1000}
        entry.update(contract_params(ip, isa))
        cfg = {"soc": {
            "name": "mx_contract", "pdk": "gf180mcu", "profile": "testbench",
            "cores": [entry],
            "memory": {"sram_kb": 32, "boot_rom_kb": 2}, "bus": "obi",
            "scheduler": {"tdu": True, "mode": "dynamic"},
            "peripherals": ["uart", "gpio", "timer", "spi"],
        }}
        assert validate_soc_config(cfg) == [], (ip, isa)


# ── Pairwise covering array ──────────────────────────────────────────

def _covered_pairs(rows):
    covered = set()
    for row in rows:
        can = canonical(row)
        keys = sorted(can)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                covered.add((a, can[a], b, can[b]))
    return covered


def test_pairwise_covers_every_legal_pair_or_reports_it_blocked():
    axes = derive_axes()
    rows, blocked = pairwise_rows(axes)
    covered = _covered_pairs(rows)
    blocked_pairs = {p for p, _ in blocked}
    names = list(axes)
    missing = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for va in axes[a]:
                for vb in axes[b]:
                    pair = (a, va, b, vb) if a < b else (b, vb, a, va)
                    if pair not in covered and (a, va, b, vb) not in blocked_pairs:
                        missing.append((a, va, b, vb))
    assert missing == [], f"{len(missing)} pairs silently dropped: {missing[:5]}"


def test_every_blocked_pair_has_a_reason():
    _, blocked = pairwise_rows(derive_axes())
    assert blocked, "constraint set should block some pairs"
    for pair, reason in blocked:
        assert isinstance(reason, str) and reason, pair


def test_pairwise_is_deterministic():
    first = pairwise_rows(derive_axes())
    second = pairwise_rows(derive_axes())
    assert first == second


def test_every_pairwise_config_passes_the_oracle():
    rows, _ = pairwise_rows(derive_axes())
    assert len(rows) > 100
    for row in rows:
        errors = validate_soc_config(synth_config(row))
        assert errors == [], (canonical(row), errors[:3])


# ── Constraints ──────────────────────────────────────────────────────

def test_conflict_smp_needs_mhartid():
    assert conflict({"shape": "multi_titan", "titan_ip": "serv"})
    assert conflict({"shape": "multi_titan", "titan_ip": "rocket"})
    assert conflict({"shape": "multi_titan", "titan_ip": "cv32e40x"}) is None


def test_canonical_drops_inapplicable_axes():
    row = {"shape": "multi_titan", "titan_ip": "cv32e40x", "worker_ip": "serv",
           "worker_role": "nano", "worker_count": 1, "second_worker": "none",
           "variant": "base", "bus": "obi", "sched_mode": "dynamic",
           "sram_kb": 32, "periph_set": "standard"}
    can = canonical(row)
    assert "worker_ip" not in can and "titan_ip" in can
    row["shape"] = "worker_only"
    can = canonical(row)
    assert "titan_ip" not in can and "worker_ip" in can


# ── Sim boundary set ─────────────────────────────────────────────────

def test_sim_boundary_covers_every_core_as_worker():
    rows = sim_boundary_rows()
    workers = {row["worker_ip"] for row in rows if row["shape"] == "standard"}
    assert workers >= set(CORE_SPECS), set(CORE_SPECS) - workers
    buses = {row["bus"] for row in rows}
    assert buses == set(VALID_BUS)
    shapes = {row["shape"] for row in rows}
    assert shapes == {"standard", "multi_titan", "worker_only"}


def test_sim_boundary_configs_all_pass_the_oracle():
    for row in sim_boundary_rows():
        errors = validate_soc_config(synth_config(row))
        assert errors == [], (canonical(row), errors[:3])


# ── Runner + report ──────────────────────────────────────────────────

def test_validate_tier_runs_and_resumes(tmp_path):
    tm = TbMatrix(tmp_path)
    result = tm.run(tier="validate")
    assert result.ok, result.errors
    assert result.details["ran"] == result.details["planned"]
    report = json.loads((tmp_path / "build" / "tb_matrix" / "report.json")
                        .read_text())
    assert len(report["tiers"]["validate"]) == result.details["planned"]
    # resume: everything already passed, nothing re-runs
    again = tm.run(tier="validate")
    assert again.ok
    assert again.details["ran"] == 0
    assert again.details["skipped"] == result.details["planned"]


def test_unknown_tier_rejected(tmp_path):
    tm = TbMatrix(tmp_path)
    assert not tm.run(tier="bogus").ok
    assert not tm.plan(tier="bogus").ok


def test_report_summarizes_recorded_tiers(tmp_path):
    tm = TbMatrix(tmp_path)
    assert not tm.report().ok  # nothing recorded yet
    tm.run(tier="validate", limit=3)
    result = tm.report()
    assert result.ok, result.errors
    assert "validate" in result.details["tiers"]


def test_cli_plan_json():
    proc = subprocess.run(
        [sys.executable, "-m", "harness", "--json", "tb-matrix", "plan",
         "--tier", "sim"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=300)
    payload = json.loads(proc.stdout)
    assert payload["ok"], payload.get("errors")
    assert payload["details"]["configs"], "sim plan must not be empty"
