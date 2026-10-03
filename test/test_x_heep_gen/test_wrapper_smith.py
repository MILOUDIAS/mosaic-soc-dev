"""wrapper-smith tests: ground-truth classifier corpus (all integrated cores
must classify to their proven family), scaffold staging + idempotency, and
regen fidelity vs the shipped picorv32 wrapper.

Run from the repo root: python3 -m pytest test/test_x_heep_gen/test_wrapper_smith.py
"""

import json
import pathlib
import re
import sys
from types import SimpleNamespace

import pytest

directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(directory))

from harness.skills.wrapper_smith import (  # noqa: E402
    WrapperSmith,
    _ports_yosys,
    classify,
    Port,
)

REPO_ROOT = directory

# The ground-truth corpus: every integrated core's wrapper-facing top and the
# family its proven SCI wrapper implements. THE classifier regression suite.
CORPUS = [
    ("hw/vendor/mosaic/serv/servile/servile.v", "servile", "wishbone_unified"),
    ("hw/vendor/mosaic/serv/rtl/serv_top.v", "serv_top", "wishbone_split"),
    ("hw/vendor/mosaic/fazyrv/rtl/fazyrv_top.sv", "fazyrv_top", "wishbone_split"),
    ("hw/vendor/mosaic/picorv32/picorv32.v", "picorv32", "unified_native"),
    ("hw/vendor/mosaic/snitch/rtl/snitch.sv", "snitch", "reqrsp_split"),
    ("hw/vendor/mosaic/ibex/rtl/ibex_top.sv", "ibex_top", "reqgnt_split"),
    ("hw/vendor/mosaic/cva6/core/cva6.sv", "cva6", "axi4_struct"),
    ("hw/vendor/mosaic/berkeley/rtl/RocketTile.sv", "RocketTile", "tilelink_unified"),
    ("hw/vendor/mosaic/berkeley/rtl/BoomTile.sv", "BoomTile", "tilelink_unified"),
]


@pytest.mark.parametrize("rtl,top,family", CORPUS, ids=[c[1] for c in CORPUS])
def test_ground_truth_classification(rtl, top, family, tmp_path):
    ws = WrapperSmith(REPO_ROOT)
    result = ws.analyze(REPO_ROOT / rtl, top=top, out=tmp_path / "a.json")
    assert result.ok, result.errors
    cls = result.details["analysis"]["classification"]
    assert cls["family"] == family, (
        f"{top}: classified {cls['family']} ({cls['confidence']}), "
        f"expected {family}; evidence {cls['evidence']}"
    )
    assert cls["confidence"] >= 0.5


def test_ahb_signatures_recognize_hazard3_shape():
    """Synthetic Hazard3-style 2-port AHB port list -> ahb_split."""
    names = [
        "clk",
        "rst_n",
        "i_haddr",
        "i_hwrite",
        "i_hsize",
        "i_htrans",
        "i_hwdata",
        "i_hrdata",
        "i_hready",
        "i_hresp",
        "d_haddr",
        "d_hwrite",
        "d_hsize",
        "d_htrans",
        "d_hwdata",
        "d_hrdata",
        "d_hready",
        "d_hresp",
        "irq",
        "soft_irq",
        "timer_irq",
    ]
    ports = [Port(name=n, dir="input", width=1) for n in names]
    cls = classify(ports)
    assert cls.family == "ahb_split", (cls.family, cls.confidence)


def test_unknown_below_threshold():
    ports = [
        Port(name=n, dir="input", width=1)
        for n in ("clk", "rst_n", "foo", "bar", "baz")
    ]
    cls = classify(ports)
    assert cls.family == "unknown"


@pytest.mark.parametrize(
    "core",
    ["../escape", "foo/bar", "/tmp/escape", ".", "..", "bad-core", "bad core", "A"],
)
def test_scaffold_rejects_noncanonical_core_before_file_io(tmp_path, core):
    result = WrapperSmith(REPO_ROOT).scaffold(core, analysis=tmp_path / "missing.json")
    assert not result.ok
    assert "invalid core identifier" in result.summary


@pytest.mark.parametrize("name", ["../escape", "foo/bar", "/tmp/escape", ".", ".."])
def test_fetch_rejects_traversal_name_before_git(monkeypatch, name):
    import harness.skills.wrapper_smith as module

    monkeypatch.setattr(
        module,
        "run_cmd",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("git called")),
    )
    result = WrapperSmith(REPO_ROOT).fetch("https://example.invalid/core.git", name=name)
    assert not result.ok
    assert "invalid fetch name" in result.summary


@pytest.mark.parametrize("subdir", ["../escape", "/tmp/escape", "rtl/../../escape"])
def test_fetch_rejects_traversal_subdir_before_git(monkeypatch, subdir):
    import harness.skills.wrapper_smith as module

    monkeypatch.setattr(
        module,
        "run_cmd",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("git called")),
    )
    result = WrapperSmith(REPO_ROOT).fetch(
        "https://example.invalid/core.git", name="safe", subdir=subdir
    )
    assert not result.ok
    assert "invalid fetch subdir" in result.summary


def test_yosys_fallback_never_interpolates_untrusted_filename(monkeypatch, tmp_path):
    import harness.skills.wrapper_smith as module

    malicious = tmp_path / "evil; plugin -i injected.so; #.sv"
    malicious.write_text("module safe_top(input logic clk_i); endmodule\n")
    captured = {}

    def fake_run(cmd, *, cwd=None, **_kwargs):
        captured["cmd"] = list(cmd)
        captured["script"] = (pathlib.Path(cwd) / cmd[-1]).read_text()
        (pathlib.Path(cwd) / "ports.json").write_text(
            json.dumps(
                {
                    "modules": {
                        "safe_top": {
                            "ports": {"clk_i": {"direction": "input", "bits": [1]}}
                        }
                    }
                }
            )
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/yosys")
    monkeypatch.setattr(module, "run_cmd", fake_run)

    ports = _ports_yosys([malicious], "safe_top")

    assert ports and ports[0].name == "clk_i"
    assert captured["cmd"][1:3] == ["-q", "-s"]
    assert "plugin" not in captured["script"]
    assert "evil" not in captured["script"]
    assert "source_000000.sv" in captured["script"]


def test_read_only_analysis_never_invokes_external_parser(monkeypatch, tmp_path):
    import harness.skills.wrapper_smith as module

    rtl = tmp_path / "legacy.v"
    rtl.write_text("module legacy(clk_i); input clk_i; endmodule\n")
    monkeypatch.setattr(
        module,
        "run_cmd",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("external parser invoked")
        ),
    )

    result = WrapperSmith(tmp_path).analyze(
        rtl, persist=False, allow_external_parsers=False
    )

    assert not result.ok
    assert "enable external parsers" in result.errors[0]


def test_scaffold_stages_all_touchpoints(tmp_path):
    ws = WrapperSmith(REPO_ROOT)
    an = ws.analyze(
        REPO_ROOT / "hw/vendor/mosaic/picorv32/picorv32.v",
        top="picorv32",
        out=tmp_path / "a.json",
    )
    assert an.ok
    result = ws.scaffold("zzztestcore", analysis=tmp_path / "a.json")
    assert result.ok, result.errors
    staged = set(result.details["written"]) | {
        e.split(" (")[0] for e in result.details["edited"]
    }
    for expected in (
        "hw/sci/zzztestcore_sci.sv",
        "util/xheep_gen/cpu/cpu.py",
        "util/xheep_gen/core_registry.py",
        "hw/core-v-mini-mcu/cpu_subsystem.sv.tpl",
        "hw/sci/sci.core",
        "tb/mosaic_soc/gen_filelist.py",
    ):
        assert expected in staged, f"missing touchpoint: {expected}"
    # dry-run must not touch the tree
    assert not (REPO_ROOT / "hw/sci/zzztestcore_sci.sv").exists()
    # staged registry edit contains the core exactly once
    stage = pathlib.Path(result.details["stage"])
    cpu_py = (stage / "util/xheep_gen/cpu/cpu.py").read_text()
    assert cpu_py.count('"zzztestcore"') == 1
    registry_py = (stage / "util/xheep_gen/core_registry.py").read_text()
    assert registry_py.count('"zzztestcore"') == 2  # key plus CoreSpec name
    assert 'frozenset({"rv32i"})' in registry_py
    assert 'capabilities=frozenset({"unified_obi"})' in registry_py
    assert not (stage / "util/xheep_gen/mosaic_config.py").exists()
    # staged tpl branch is guard-wrapped and above the anchor
    tpl = (stage / "hw/core-v-mini-mcu/cpu_subsystem.sv.tpl").read_text()
    assert "## wrapper-smith:begin zzztestcore" in tpl
    assert tpl.index("## wrapper-smith:begin zzztestcore") < tpl.index(
        "## wrapper-smith:insert-here"
    )


def test_scaffold_idempotent_for_integrated_core(tmp_path):
    """Scaffolding an ALREADY-integrated core reports only skipped_existing."""
    ws = WrapperSmith(REPO_ROOT)
    an = ws.analyze(
        REPO_ROOT / "hw/vendor/mosaic/picorv32/picorv32.v",
        top="picorv32",
        out=tmp_path / "a.json",
    )
    result = ws.scaffold("picorv32", analysis=tmp_path / "a.json")
    assert result.ok
    assert result.details["written"] == []
    assert result.details["edited"] == []
    assert len(result.details["skipped_existing"]) >= 6


def test_regen_fidelity_picorv32(tmp_path):
    """The scaffolded wrapper for a unified_native core is the shipped
    picorv32 wrapper modulo rename + provenance banner."""
    ws = WrapperSmith(REPO_ROOT)
    an = ws.analyze(
        REPO_ROOT / "hw/vendor/mosaic/picorv32/picorv32.v",
        top="picorv32",
        out=tmp_path / "a.json",
    )
    result = ws.scaffold("pico2", analysis=tmp_path / "a.json")
    staged = pathlib.Path(result.details["stage"]) / "hw/sci/pico2_sci.sv"
    regen = staged.read_text().replace("pico2_sci", "picorv32_sci")
    regen = re.sub(r"\bpico2\b", "picorv32", regen)
    shipped = (REPO_ROOT / "hw/sci/picorv32_sci.sv").read_text()
    # strip generated banner + TODO annotations, then compare
    regen_body = "\n".join(
        l
        for l in regen.splitlines()
        if "wrapper-smith" not in l and not l.startswith("// GENERATED")
    )
    shipped_body = shipped.strip()
    assert shipped_body.splitlines()[-1] == regen_body.strip().splitlines()[-1]
    # >95% of shipped lines present verbatim in the regen
    shipped_lines = [l for l in shipped_body.splitlines() if l.strip()]
    regen_set = set(regen_body.splitlines())
    hits = sum(1 for l in shipped_lines if l in regen_set)
    assert hits / len(shipped_lines) > 0.95


def _make_fixture_repo(tmp_path):
    """A tiny local git repo with a LICENSE and one RTL file."""
    import subprocess as sp

    repo = tmp_path / "fixture_core"
    repo.mkdir()
    (repo / "LICENSE").write_text(
        "                                 Apache License\n"
        "                           Version 2.0, January 2004\n"
    )
    (repo / "tiny.v").write_text(
        "module tiny_core (\n"
        "  input         clk,\n"
        "  input         rst_n,\n"
        "  output        mem_valid,\n"
        "  input         mem_ready,\n"
        "  output [31:0] mem_addr,\n"
        "  output [31:0] mem_wdata,\n"
        "  output [3:0]  mem_wstrb,\n"
        "  input  [31:0] mem_rdata\n"
        ");\nendmodule\n"
    )
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        sp.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def test_fetch_local_repo(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    ws = WrapperSmith(REPO_ROOT)
    result = ws.fetch(f"file://{repo}", name="zzzfixture")
    try:
        assert result.ok, result.errors
        prov = result.details["provenance"]
        assert len(prov["commit"]) == 40
        assert prov["license"]["guess"] == "Apache-2.0"
        rtl_root = pathlib.Path(result.details["rtl_root"])
        assert (rtl_root / "tiny.v").exists()
        assert (rtl_root / ".wrapper-smith-provenance.json").exists()
    finally:
        import shutil

        shutil.rmtree(
            REPO_ROOT / "build/wrapper_smith/fetch/zzzfixture", ignore_errors=True
        )


def test_fetch_then_scaffold_carries_provenance(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    ws = WrapperSmith(REPO_ROOT)
    fetched = ws.fetch(f"file://{repo}", name="zzzfixture")
    try:
        rtl_root = pathlib.Path(fetched.details["rtl_root"])
        an = ws.analyze(rtl_root / "tiny.v", top="tiny_core", out=tmp_path / "a.json")
        assert an.ok
        assert an.details["analysis"]["classification"]["family"] == "unified_native"
        result = ws.scaffold(
            "zzzfixture", analysis=tmp_path / "a.json", vendor_from=rtl_root
        )
        assert result.ok, result.errors
        stage = pathlib.Path(result.details["stage"])
        core_file = (stage / "hw/vendor/mosaic/zzzfixture/zzzfixture.core").read_text()
        assert f"file://{repo}" in core_file  # provenance URL
        assert "Apache-2.0" in core_file  # license guess
        # depend edge added because a vendor .core is being staged
        sci = (stage / "hw/sci/sci.core").read_text()
        assert "- mosaic:ip:zzzfixture" in sci
    finally:
        import shutil

        shutil.rmtree(
            REPO_ROOT / "build/wrapper_smith/fetch/zzzfixture", ignore_errors=True
        )
        shutil.rmtree(REPO_ROOT / "build/wrapper_smith/zzzfixture", ignore_errors=True)


def test_scaffold_without_vendor_skips_depend_edge(tmp_path):
    ws = WrapperSmith(REPO_ROOT)
    an = ws.analyze(
        REPO_ROOT / "hw/vendor/mosaic/picorv32/picorv32.v",
        top="picorv32",
        out=tmp_path / "a.json",
    )
    result = ws.scaffold("zzznodep", analysis=tmp_path / "a.json")
    assert result.ok
    stage = pathlib.Path(result.details["stage"])
    sci = (stage / "hw/sci/sci.core").read_text()
    assert "- mosaic:ip:zzznodep" not in sci  # no dangling VLNV
    assert any("depend" in t["text"] for t in result.details["todos"])


def test_unknown_family_scaffold_refused(tmp_path):
    ws = WrapperSmith(REPO_ROOT)
    a = {
        "schema": "wrapper-smith/analysis@1",
        "top": "mystery",
        "top_file": "x.sv",
        "ports": [],
        "params": [],
        "classification": {
            "family": "unknown",
            "confidence": 0.1,
            "evidence": [],
            "runner_up": None,
        },
        "control": {},
        "todos": [],
        "checklist": [],
    }
    p = tmp_path / "a.json"
    p.write_text(json.dumps(a))
    result = ws.scaffold("mystery", analysis=p)
    assert not result.ok
    assert "UNKNOWN" in result.summary
