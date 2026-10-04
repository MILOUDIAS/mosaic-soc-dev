"""Every core the validator accepts must be buildable, or refused by name.

`AVAILABLE_CPUS` and `CORE_SPECS` say which cores a config may name. Neither
knows whether the files needed to build one exist. The gap is not theoretical:
`qerv` is accepted, needs an SCI wrapper, and there is no `qerv_sci.sv` on disk,
because QERV shares SERV's W-parameterised wrapper at W=4. Deriving the wrapper
name from the core name says a file is missing when it is not.

Without these tests the failure mode for a genuinely missing wrapper is an
unresolved module reference during elaboration, several tool invocations after
the config was accepted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.core import REPO_ROOT
from util.mosaic_gen.core_integration import (
    CPU_SUBSYSTEM_TPL, audit, compiled_wrappers, incomplete, inspect,
)
from util.mosaic_gen.core_registry import CORE_SPECS


def test_every_catalog_core_has_a_complete_integration_path():
    """The invariant. If this fails, the refusal message names the missing part."""
    gaps = incomplete(REPO_ROOT)
    assert not gaps, "\n".join(r.refusal() for r in gaps.values())


def test_the_catalog_and_the_cpu_validator_agree():
    """Two lists of legal core names must not drift apart."""
    from util.mosaic_gen.cpu.cpu import CPU

    assert set(CORE_SPECS) == set(CPU.AVAILABLE_CPUS), {
        "in CORE_SPECS only": sorted(set(CORE_SPECS) - set(CPU.AVAILABLE_CPUS)),
        "in AVAILABLE_CPUS only": sorted(set(CPU.AVAILABLE_CPUS) - set(CORE_SPECS)),
    }


def test_qerv_resolves_to_servs_wrapper():
    """The case that motivated sci_module. QERV is SERV at W=4."""
    qerv = inspect("qerv", REPO_ROOT)
    assert qerv.sci_module == "serv_sci"
    assert qerv.complete
    assert not (REPO_ROOT / "hw/sci/qerv_sci.sv").exists(), (
        "a qerv_sci.sv would be a second copy of serv_sci.sv"
    )


def test_native_cores_need_no_wrapper():
    """The cv32e* family is x-heep-native and instantiated directly."""
    for name in ("cv32e20", "cv32e40p", "cv32e40px", "cv32e40x"):
        r = inspect(name, REPO_ROOT)
        assert r.sci_module is None, name
        assert r.has_sci, name
        assert r.complete, name


@pytest.mark.parametrize("name", sorted(CORE_SPECS))
def test_each_core_has_a_template_branch(name):
    """Part three of the rule in AGENTS.md."""
    assert inspect(name, REPO_ROOT).has_branch, (
        f'cpu_subsystem.sv.tpl has no branch for "{name}"'
    )


def test_an_unknown_core_is_refused_by_name():
    r = inspect("definitely_not_a_core", REPO_ROOT)
    assert not r.complete
    assert "definitely_not_a_core" in r.refusal()
    assert "core_registry.CORE_SPECS" in r.refusal()


def test_a_missing_wrapper_is_reported_with_its_path(tmp_path):
    """Simulate the fifteenth core: catalogued, branch present, no wrapper."""
    (tmp_path / "hw/sci").mkdir(parents=True)
    tpl = tmp_path / CPU_SUBSYSTEM_TPL
    tpl.parent.mkdir(parents=True)
    tpl.write_text('% elif group.name == "serv":\n')

    r = inspect("serv", tmp_path)
    assert not r.complete
    assert r.has_branch
    assert not r.has_sci
    msg = r.refusal()
    assert "serv_sci" in msg and "hw/sci" in msg, msg
    # It must say WHICH part is missing, not merely that something is.
    assert "no SCI wrapper" in msg


def test_a_missing_branch_is_reported_separately(tmp_path):
    """A wrapper without a template branch is the other half of the rule."""
    (tmp_path / "hw/sci").mkdir(parents=True)
    (tmp_path / "hw/sci/serv_sci.sv").write_text("module serv_sci; endmodule\n")
    tpl = tmp_path / CPU_SUBSYSTEM_TPL
    tpl.parent.mkdir(parents=True)
    tpl.write_text("// no branches here\n")

    r = inspect("serv", tmp_path)
    assert not r.complete
    assert r.has_sci and not r.has_branch
    assert "cpu_subsystem.sv.tpl" in r.refusal()


def test_the_audit_covers_the_whole_catalog():
    assert set(audit(REPO_ROOT)) == set(CORE_SPECS)


# ── the fourth part: written, but never compiled ─────────────────────
# A wrapper can exist on disk, declare the right module, have a template
# branch, and still leave elaboration with an unresolved module reference --
# because nobody added it to hw/sci/sci.core. That failure is identical to a
# missing wrapper from the tool's point of view and much harder to see, since
# the file is sitting right there next to the ones that work.
def test_every_wrapper_is_listed_in_the_fusesoc_fileset():
    built = compiled_wrappers(REPO_ROOT)
    assert built, "parsed no files at all out of hw/sci/sci.core"
    for name, r in audit(REPO_ROOT).items():
        if r.sci_module is None:
            continue
        assert f"{r.sci_module}.sv" in built, (
            f"{name} resolves to {r.sci_module}.sv, which exists but is not "
            "listed in hw/sci/sci.core, so FuseSoC will not compile it")


def test_the_fileset_is_parsed_not_grepped():
    """A commented-out entry satisfies a substring search and builds nothing.
    That is how the first version of this check passed a mutation that
    removed a wrapper from the build."""
    assert "hazard3_sci.sv" in compiled_wrappers(REPO_ROOT)
    assert "# - hazard3_sci.sv" not in "".join(compiled_wrappers(REPO_ROOT))


def test_a_wrapper_missing_from_the_fileset_is_reported_by_name(tmp_path):
    """The refusal has to name the fileset, not just say 'wrapper missing' --
    the two have the same symptom and opposite fixes."""
    (tmp_path / "hw/sci").mkdir(parents=True)
    (tmp_path / "hw/core-v-mini-mcu").mkdir(parents=True)
    (tmp_path / "hw/sci/serv_sci.sv").write_text("module serv_sci; endmodule")
    (tmp_path / "hw/sci/sci.core").write_text("filesets:\n  files_rtl:\n")
    (tmp_path / CPU_SUBSYSTEM_TPL).write_text('group.name == "serv"')

    r = inspect("serv", tmp_path)
    assert r.has_sci is True and r.has_branch is True    # parts 2 and 3 fine
    assert r.in_fileset is False and r.complete is False
    assert any("sci.core" in m and "never compiles" in m for m in r.missing)
    assert "serv_sci.sv" in r.refusal()


def test_the_fileset_check_does_not_fire_for_native_cores(tmp_path):
    """cv32e* need no wrapper, so they cannot be missing from a fileset."""
    (tmp_path / "hw/core-v-mini-mcu").mkdir(parents=True)
    (tmp_path / CPU_SUBSYSTEM_TPL).write_text('group.name == "cv32e20"')
    r = inspect("cv32e20", tmp_path)
    assert r.sci_module is None and r.in_fileset is True and r.complete is True
