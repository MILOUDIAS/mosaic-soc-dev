"""Fail-closed evidence gates.

These are the negative fixtures the roadmap's M0 exit criteria call for
(``general_multicore_soc_generator_roadmap.md`` §16, M0):

    An exit-zero fixture with a non-waived DRC/LVS failure fails.
    A stage not run is UNKNOWN; an executed stage missing a mandatory report
    is INFRASTRUCTURE_ERROR; a threshold violation is FAIL.

The property under test throughout is that **absent evidence is never a pass**.
"""

import pytest

import harness.skills.flow_runner as fr
from harness.evidence.gate_guard import (
    FAIL_OPEN_ENV,
    gate_error_finding,
    gate_guard,
)
from harness.evidence.signoff import corroborated_count, parse_signoff
from harness.evidence.status import EvidenceStatus, ExecutionStatus, worst


# ── status vocabulary ────────────────────────────────────────────────

def test_only_pass_closes_a_required_node():
    assert EvidenceStatus.PASS.closes_required_node
    for status in EvidenceStatus:
        if status is not EvidenceStatus.PASS:
            assert not status.closes_required_node, status


def test_not_applicable_is_not_adverse_but_unknown_is():
    assert not EvidenceStatus.NOT_APPLICABLE.is_adverse
    assert EvidenceStatus.UNKNOWN.is_adverse
    assert EvidenceStatus.INFRASTRUCTURE_ERROR.is_adverse


def test_worst_never_lets_pass_mask_an_adverse_result():
    assert worst(EvidenceStatus.PASS, EvidenceStatus.FAIL) is EvidenceStatus.FAIL
    assert worst(
        EvidenceStatus.PASS, EvidenceStatus.INFRASTRUCTURE_ERROR
    ) is EvidenceStatus.INFRASTRUCTURE_ERROR
    assert worst(
        EvidenceStatus.FAIL, EvidenceStatus.INFRASTRUCTURE_ERROR
    ) is EvidenceStatus.FAIL
    assert worst(EvidenceStatus.PASS, EvidenceStatus.PASS) is EvidenceStatus.PASS
    assert worst() is EvidenceStatus.UNKNOWN


def test_only_completed_execution_observes_a_full_run():
    assert ExecutionStatus.COMPLETED.observed_a_complete_run
    for status in ExecutionStatus:
        if status is not ExecutionStatus.COMPLETED:
            assert not status.observed_a_complete_run, status


# ── gate_guard ───────────────────────────────────────────────────────

def test_a_gate_that_raises_is_not_a_pass():
    def broken():
        raise RuntimeError("parser blew up")

    result = gate_guard("broken", broken)
    assert not result.passed
    assert result.status is EvidenceStatus.INFRASTRUCTURE_ERROR
    assert result.errored
    assert "parser blew up" in result.reason


def test_gate_error_is_infrastructure_error_not_fail():
    """An errored gate never evaluated the threshold, so it cannot be a FAIL."""

    def broken():
        raise ValueError("no report")

    assert gate_guard("g", broken).status is not EvidenceStatus.FAIL


def test_fail_open_knob_downgrades_to_unknown_never_to_pass(monkeypatch):
    monkeypatch.setenv(FAIL_OPEN_ENV, "1")

    def broken():
        raise RuntimeError("x")

    result = gate_guard("broken", broken)
    assert result.status is EvidenceStatus.UNKNOWN
    assert result.skipped
    assert not result.passed, "the rollback knob must not manufacture a PASS"


def test_fail_open_knob_cannot_be_set_from_config(monkeypatch):
    """The escape hatch is environment-only and off by default."""
    monkeypatch.delenv(FAIL_OPEN_ENV, raising=False)

    def broken():
        raise RuntimeError("x")

    assert gate_guard("broken", broken).status is EvidenceStatus.INFRASTRUCTURE_ERROR


def test_classify_maps_return_value_to_status():
    result = gate_guard("count", lambda: 7, classify=lambda v: (
        EvidenceStatus.FAIL if v else EvidenceStatus.PASS
    ))
    assert result.status is EvidenceStatus.FAIL
    assert result.value == 7


def test_gate_error_finding_shape():
    finding = gate_error_finding("drc", "OSError: boom", "tb tail")
    assert finding["severity"] == "error"
    assert finding["path"] == "gate.drc"
    assert "NOT a pass" in finding["message"]
    assert finding["suggestions"]


# ── signoff parsing ──────────────────────────────────────────────────

_CLEAN = """
DRC count: 0
Circuits match uniquely.
wns 0.42
"""

_DIRTY_DRC = """
DRC count: 3
Violation: minwidth (count: 3) MinWidth violation on layer li1.
Circuits match uniquely.
"""

_BLANK_COUNT_BUT_VIOLATIONS = """
DRC count: 0
Violation: minspacing (count: 2) Spacing violation on layer met1.
Circuits match uniquely.
"""


def test_clean_run_passes():
    ev = parse_signoff(_CLEAN)
    assert ev.status is EvidenceStatus.PASS
    assert ev.drc_violations == 0
    assert ev.lvs_match is True


def test_exit_zero_with_drc_violations_fails():
    """M0 exit criterion: a non-waived DRC violation must fail."""
    ev = parse_signoff(_DIRTY_DRC)
    assert ev.status is EvidenceStatus.FAIL
    assert ev.drc_violations == 3


def test_blank_drc_count_cannot_mask_real_violations():
    """The corroborated-zero rule (CoreSmith's honesty fallback)."""
    ev = parse_signoff(_BLANK_COUNT_BUT_VIOLATIONS)
    assert ev.status is EvidenceStatus.FAIL
    assert ev.drc_violations == 2
    assert any("report body holds" in r for r in ev.reasons)


def test_corroborated_count_takes_the_larger_source():
    body = "Violation: minwidth (count: 5) x"
    assert corroborated_count(0, body, "magic") == 5
    assert corroborated_count(None, body, "magic") == 5
    assert corroborated_count(9, body, "magic") == 9
    assert corroborated_count(0, "nothing here", "magic") == 0


def test_lvs_mismatch_fails():
    ev = parse_signoff("DRC count: 0\nNetlists do not match\n")
    assert ev.status is EvidenceStatus.FAIL
    assert ev.lvs_match is False


def test_missing_report_is_infrastructure_error_not_pass():
    """An executed stage with no parseable mandatory report."""
    ev = parse_signoff("make: nothing to be done\n")
    assert ev.status is EvidenceStatus.INFRASTRUCTURE_ERROR
    assert not ev.status.closes_required_node


def test_negative_slack_fails_when_timing_is_required():
    ev = parse_signoff(
        "DRC count: 0\nCircuits match uniquely.\nWNS -0.31\n",
        require_timing=True,
    )
    assert ev.status is EvidenceStatus.FAIL
    assert ev.wns_ns == pytest.approx(-0.31)


def test_missing_timing_when_required_is_infrastructure_error():
    ev = parse_signoff(
        "DRC count: 0\nCircuits match uniquely.\n", require_timing=True
    )
    assert ev.status is EvidenceStatus.INFRASTRUCTURE_ERROR


def test_flow_that_checks_nothing_is_not_applicable_not_pass():
    """`harden-nodrc`-shaped flows must not read as signed off."""
    ev = parse_signoff(
        "done\n", require_drc=False, require_lvs=False, require_timing=False
    )
    assert ev.status is EvidenceStatus.NOT_APPLICABLE
    assert not ev.status.closes_required_node
    assert set(ev.checks_skipped) == {"drc", "lvs", "timing"}


# ── flow_runner integration: the live fail-open bug ──────────────────

class _Proc:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _run_harden(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(fr, "run_cmd", lambda *a, **k: _Proc(stdout, returncode))
    return fr.FlowRunner().run("harden-classic")


def test_harden_declares_required_signoff_evidence():
    for name in ("harden-classic", "harden-chip"):
        assert fr.FLOWS[name].get("required_evidence"), name
        assert fr.FLOWS[name].get("signoff"), name


def test_harden_exit_zero_with_drc_violations_now_fails(monkeypatch):
    """The regression this branch exists to fix.

    Previously `harden-*` was routed into the cocotb parser, produced no gate
    metric, and `ok` collapsed to `returncode == 0` -- so this exact output
    reported PASS.
    """
    result = _run_harden(monkeypatch, _DIRTY_DRC)
    assert not result.ok
    assert "signoff=FAIL" in result.summary


def test_harden_exit_zero_with_no_report_is_not_a_pass(monkeypatch):
    result = _run_harden(monkeypatch, "make: built target\n")
    assert not result.ok
    assert result.details["metrics"]["signoff_status"] == (
        EvidenceStatus.INFRASTRUCTURE_ERROR.value
    )


def test_harden_clean_run_passes(monkeypatch):
    result = _run_harden(monkeypatch, _CLEAN)
    assert result.ok, result.summary
    assert result.details["metrics"]["drc_violations"] == 0


def test_harden_nonzero_exit_never_passes(monkeypatch):
    result = _run_harden(monkeypatch, _CLEAN, returncode=1)
    assert not result.ok


def test_missing_required_evidence_key_fails_closed(monkeypatch):
    """A flow declaring evidence it never emits must not pass."""
    monkeypatch.setitem(
        fr.FLOWS,
        "mosaic-gen",
        dict(fr.FLOWS["mosaic-gen"], required_evidence=["never_emitted"]),
    )
    monkeypatch.setattr(fr, "run_cmd", lambda *a, **k: _Proc("all good\n"))
    result = fr.FlowRunner().run("mosaic-gen")
    assert not result.ok
    assert any("never_emitted" in e for e in result.errors)


# ── template: boolean core parameters must render as SystemVerilog ───

def test_boolean_core_params_are_coerced_to_int_in_template():
    """A YAML `true` must not reach SystemVerilog as Python `True`.

    `memdly1: true` used to render `.MEMDLY1(True)`, which passes config
    validation and RTL generation and only fails at Verilator elaboration
    with "Can't convert defparam value to constant". Every boolean core
    parameter must be wrapped in int().
    """
    import re
    from harness.core import REPO_ROOT

    tpl = (REPO_ROOT / "hw/core-v-mini-mcu/cpu_subsystem.sv.tpl").read_text()
    bool_params = (
        "memdly1", "with_csr", "compressed", "mdu", "pre_register", "mul", "div",
    )
    bare = []
    for name in bool_params:
        for m in re.finditer(r"[^(]group\.params\.get\('" + name + r"',", tpl):
            start = max(0, m.start() - 12)
            if "int(" not in tpl[start:m.start() + 8]:
                bare.append(name)
    assert not bare, (
        f"boolean params rendered without int() coercion: {sorted(set(bare))}"
    )


def test_every_evidence_module_imports_standalone():
    """`import harness.evidence.signoff` must work as the FIRST harness import.

    It did not. `harness/evidence/signoff.py` imported the report parsers from
    `harness.skills.drc_triage`, whose package __init__ imports flow_runner,
    which imports harness.evidence.signoff -- a cycle that resolved only when
    `harness.skills` happened to be imported first. Every test in this suite
    reaches skills first, so the whole suite passed while

        python3 -c "import harness.evidence.signoff"

    raised ImportError. Any consumer that starts from the evidence layer -- an
    MCP server being the obvious one -- would have hit it immediately.

    Each module is imported in a SEPARATE interpreter, because once any harness
    module is in sys.modules the cycle is masked.
    """
    import subprocess
    import sys

    from harness.core import REPO_ROOT

    modules = [
        "harness.evidence",
        "harness.evidence.signoff",
        "harness.evidence.librelane",
        "harness.evidence.gate_guard",
        "harness.evidence.status",
        "harness.skills.flow_runner",
    ]
    broken = {}
    for module in modules:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        if result.returncode != 0:
            broken[module] = result.stderr.strip().splitlines()[-1]
    assert not broken, f"modules that cannot be imported first: {broken}"


def test_pyproject_lists_every_harness_subpackage():
    """setuptools ships only what `packages` names, and it was wrong.

    `harness.evidence` was absent, so a built wheel contained
    harness/skills/flow_runner.py -- which imports harness.evidence.gate_guard
    at module load -- and no evidence modules at all. An editable install hid
    it, and nothing in this suite looked, because the tests import from the
    source tree.
    """
    import pathlib
    import tomllib

    from harness.core import REPO_ROOT

    declared = set(
        tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        ["tool"]["setuptools"]["packages"]
    )
    on_disk = {
        ".".join(p.parent.relative_to(REPO_ROOT).parts)
        for p in (REPO_ROOT / "harness").rglob("__init__.py")
        if "__pycache__" not in p.parts
    }
    missing = sorted(on_disk - declared)
    assert not missing, (
        f"subpackages that exist but would be absent from a wheel: {missing}"
    )


@pytest.mark.slow
def test_a_built_wheel_actually_runs(tmp_path):
    """WP-0: `pip install` the wheel and run the console script elsewhere.

    Marked slow because it builds a wheel and creates a venv (~20 s), but it is
    the only test that can catch this class of defect: every other test imports
    from the source tree, where cwd puts both `harness` and `util` on sys.path
    and the packaging is therefore never exercised.

    Two defects hid behind exactly that. `harness.evidence` was absent from
    `packages`, so the wheel shipped flow_runner.py without the module it
    imports at load time. And `harness.core` imports `util.xheep_gen.
    core_registry` while pyproject declared util/ "not part of the app", so the
    console script died with ModuleNotFoundError before doing anything -- for
    an editable install too, since a console script does not put cwd on the
    path.
    """
    import subprocess
    import sys

    from harness.core import REPO_ROOT

    wheelhouse = tmp_path / "wheelhouse"
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-q",
         "-w", str(wheelhouse), str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(wheelhouse.glob("oh_my_soc-*.whl"))
    assert wheels, "no wheel was built"

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                   capture_output=True)
    pip = venv / "bin" / "pip"
    install = subprocess.run(
        [str(pip), "install", "-q", "PyYAML", "Mako", str(wheels[0])],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    # Run from tmp_path: nothing of the repo is on sys.path or in cwd.
    result = subprocess.run(
        [str(venv / "bin" / "oh-my-soc"), "config-author", "presets"],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "the installed console script failed outside the repo:\n"
        + result.stdout + result.stderr
    )
    assert "presets available" in result.stdout

    # M4: an installed server acts on a CHECKOUT named by OH_MY_SOC_REPO, not
    # on site-packages. Before, REPO_ROOT was the package parent, so every
    # repo-touching tool resolved paths inside the venv and refused them.
    import json
    import os

    frames = "\n".join(json.dumps(f) for f in (
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "session_new",
            "arguments": {"request": "check configs/mosaic_blockb_3hart.yaml"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "request_scope",
            "arguments": {"scope": "analysis", "rationale": "check"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "config_validate",
            "arguments": {"path": "configs/mosaic_blockb_3hart.yaml"}}},
    )) + "\n"
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "OH_MY_SOC_REQUEST", "OH_MY_SOC_SCOPES"}}
    env.update(OH_MY_SOC_REPO=str(REPO_ROOT),
               OH_MY_SOC_CONFIG_DIR=str(tmp_path / "cfg"))
    served = subprocess.run(
        [str(venv / "bin" / "oh-my-soc"), "mcp-server"], input=frames,
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120)
    assert f"repo {REPO_ROOT}" in served.stderr, served.stderr
    replies = [json.loads(line) for line in served.stdout.splitlines()]
    assert [r["result"]["isError"] for r in replies] == [False, False, False], (
        served.stdout + served.stderr)
