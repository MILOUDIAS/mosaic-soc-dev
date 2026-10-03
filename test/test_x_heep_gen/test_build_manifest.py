"""Tests for isolated MOSAIC generation/build bundles."""

import json
from pathlib import Path
import sys

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(REPO_ROOT / "util" / "xheep_gen"))

import build_manifest


def test_manifest_lookup_is_not_a_recursively_exported_make_variable():
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    assert "MOSAIC_MANIFEST = $(shell" not in makefile
    mosaic_recipe = makefile.split("mosaic-gen:", 1)[1].split("\n## ", 1)[0]
    assert mosaic_recipe.count("build_manifest.py locate") == 1


def _inputs(tmp_path: Path):
    config = tmp_path / "mosaic.yaml"
    base = tmp_path / "base.hjson"
    pads = tmp_path / "pads.py"
    config.write_text(yaml.safe_dump({"soc": {"name": "two harts"}}))
    base.write_text("{}\n")
    pads.write_text("# pads\n")
    return config, base, pads


def test_build_identity_is_content_addressed(tmp_path):
    config, base, pads = _inputs(tmp_path)
    generator = tmp_path / "util/xheep_gen/model.py"
    generator.parent.mkdir(parents=True)
    generator.write_text("SCHEMA = 1\n")
    first = build_manifest.compute_identity(config, base, pads, tmp_path)
    second = build_manifest.compute_identity(config, base, pads, tmp_path)
    assert first[:2] == second[:2]
    assert first[1].startswith("two-harts-")

    base.write_text('{"memory": 32768}\n')
    changed = build_manifest.compute_identity(config, base, pads, tmp_path)
    assert changed[1] != first[1]

    source_before = changed
    generator.write_text("SCHEMA = 2\n")
    source_changed = build_manifest.compute_identity(config, base, pads, tmp_path)
    assert source_changed[1] != source_before[1]


def test_generation_identity_is_pinned_and_rejects_midrun_drift(tmp_path):
    config, base, pads = _inputs(tmp_path)
    source = tmp_path / "util/xheep_gen/model.py"
    source.parent.mkdir(parents=True)
    source.write_text("SCHEMA = 1\n")
    pinned = build_manifest.bundle_paths(config, base, pads, tmp_path, "out")
    build_manifest.verify_pinned_identity(pinned, config, base, pads, tmp_path)

    source.write_text("SCHEMA = 2\n")
    with pytest.raises(RuntimeError, match="changed during generation"):
        build_manifest.verify_pinned_identity(pinned, config, base, pads, tmp_path)


def test_stage_rejects_generated_file_modified_after_manifest(tmp_path):
    repo = tmp_path / "repo"
    generated_root = tmp_path / "bundle/generated"
    manifest_path = tmp_path / "bundle/manifest.json"
    generated = generated_root / "hw/out.sv"
    generated.parent.mkdir(parents=True)
    generated.write_text("module original; endmodule\n")
    repo.mkdir()
    (repo / "core-v-mini-mcu.core").write_text("CAPI=2:\n")
    (repo / "waiver_v5.core").write_text("CAPI=2:\n")
    manifest = {
        "schema_version": build_manifest.SCHEMA_VERSION,
        "repo_root": str(repo),
        "generated_root": str(generated_root),
        "generated_files": [],
        "inputs": {
            "generator_sources": build_manifest._generator_source_record(repo)
        },
    }
    build_manifest.write_manifest(manifest_path, manifest)
    build_manifest.register_generated_file(manifest_path, "hw/out.sv", generated)
    generated.write_text("module tampered; endmodule\n")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        build_manifest.stage_fusesoc_root(manifest_path, tmp_path / "stage")


def test_stage_rejects_live_source_drift_and_materializes_snapshot(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "hw/rtl/core.sv"
    source.parent.mkdir(parents=True)
    source.write_text("module core; endmodule\n")
    (repo / "core-v-mini-mcu.core").write_text("CAPI=2:\n")
    (repo / "waiver_v5.core").write_text("CAPI=2:\n")
    manifest_path = tmp_path / "bundle/manifest.json"
    generated_root = tmp_path / "bundle/generated"
    manifest = {
        "schema_version": build_manifest.SCHEMA_VERSION,
        "repo_root": str(repo),
        "generated_root": str(generated_root),
        "generated_files": [],
        "inputs": {
            "generator_sources": build_manifest._generator_source_record(repo)
        },
    }
    build_manifest.write_manifest(manifest_path, manifest)
    stage = tmp_path / "stage"
    build_manifest.stage_fusesoc_root(manifest_path, stage)
    staged_source = stage / "hw/rtl/core.sv"
    assert staged_source.read_text() == source.read_text()
    assert not staged_source.is_symlink()

    source.write_text("module changed; endmodule\n")
    with pytest.raises(RuntimeError, match="source closure changed"):
        build_manifest.stage_fusesoc_root(manifest_path, tmp_path / "stale-stage")


def test_selected_flags_are_minimal_and_shared_closures_are_deduplicated():
    assert build_manifest.selected_flags(["ibex"]) == [
        "mosaic_configured",
        "mosaic_ibex",
    ]
    assert build_manifest.selected_flags(["serv", "qerv", "rocket", "boom"]) == [
        "mosaic_configured",
        "mosaic_serv",
        "mosaic_berkeley",
    ]
    with pytest.raises(ValueError, match="No FuseSoC selection flag"):
        build_manifest.selected_flags(["unregistered"])


def test_generated_platform_artifact_registration_and_overlay(tmp_path):
    repo = tmp_path / "repo"
    generated_root = tmp_path / "bundle" / "generated"
    manifest_path = tmp_path / "bundle" / "manifest.json"
    stage = tmp_path / "stage"
    (repo / "hw/platform").mkdir(parents=True)
    (repo / "hw/platform/static.sv").write_text("module static; endmodule\n")
    (repo / "hw/platform/rv_plic_reg_pkg.sv").write_text("package stale; endpackage\n")
    (repo / "core-v-mini-mcu.core").write_text("CAPI=2:\n")
    (repo / "waiver_v5.core").write_text("CAPI=2:\n")

    generated = generated_root / "hw/platform/rv_plic_reg_pkg.sv"
    generated.parent.mkdir(parents=True)
    generated.write_text("package per_hart; endpackage\n")
    manifest = {
        "schema_version": build_manifest.SCHEMA_VERSION,
        "build_key": "test-000000000000",
        "repo_root": str(repo),
        "bundle_dir": str(manifest_path.parent),
        "generated_root": str(generated_root),
        "generated_files": [],
        "build": {"flags": ["mosaic_configured"]},
        "inputs": {
            "generator_sources": build_manifest._generator_source_record(repo)
        },
    }
    build_manifest.write_manifest(manifest_path, manifest)
    build_manifest.register_generated_file(
        manifest_path,
        "hw/platform/rv_plic_reg_pkg.sv",
        generated,
        generator="rv_plic-regtool",
    )

    resolved = build_manifest.load_manifest(manifest_path)
    assert (
        build_manifest.generated_path(resolved, "hw/platform/rv_plic_reg_pkg.sv")
        == generated
    )
    build_manifest.stage_fusesoc_root(manifest_path, stage)
    assert (
        stage / "hw/platform/rv_plic_reg_pkg.sv"
    ).read_text() == generated.read_text()
    assert (stage / "hw/platform/static.sv").read_text() == "module static; endmodule\n"


def test_registration_rejects_artifact_outside_generated_root(tmp_path):
    outside = tmp_path / "outside.sv"
    outside.write_text("module outside; endmodule\n")
    manifest_path = tmp_path / "manifest.json"
    build_manifest.write_manifest(
        manifest_path,
        {
            "schema_version": build_manifest.SCHEMA_VERSION,
            "generated_root": str(tmp_path / "generated"),
            "generated_files": [],
        },
    )
    with pytest.raises(ValueError, match="must be below"):
        build_manifest.register_generated_file(manifest_path, "hw/outside.sv", outside)
    with pytest.raises(ValueError, match="must stay within"):
        build_manifest.register_generated_file(
            manifest_path, "build/recursive-overlay.sv", outside
        )


def test_core_descriptors_select_instead_of_aggregating_catalog():
    top = (REPO_ROOT / "core-v-mini-mcu.core").read_text()
    sci = (REPO_ROOT / "hw/sci/sci.core").read_text()
    assert "mosaic_cv32e20? (files_rtl_cv32e20)" in top
    assert "mosaic_configured? (files_rtl_mosaic_sci)" in top
    assert '"!mosaic_configured? (files_rtl_native_legacy)"' in top
    assert "mosaic_ibex? (ibex)" in sci
    assert '"!mosaic_configured? (files_rtl)"' in sci


def test_the_hardening_template_is_not_part_of_the_rtl_closure():
    """Editing a LibreLane setting must not invalidate the RTL bundle.

    `flow` is a closure root because `flow/librelane/**/*.sv` holds the
    delivery wrappers, which are synthesised. The signoff template is not RTL:
    it sets PnR corners, repair margins and PDN geometry, and its only readers
    are harness/physical/hardening.py and harness/skills/flow_runner.py.

    Counting it meant that changing one repair margin -- which is exactly what
    a flow experiment does -- moved the bundle hash and made the next run abort
    with "no MOSAIC manifest", demanding a multi-hour regen that reproduces
    byte-identical RTL. Measured: blocka_reharden and blocka_slewonly have
    different bundle hashes and identical generated/ trees. The hazard is not
    the wasted hours, it is the pressure to reuse a stale bundle instead.
    """
    ignored = build_manifest._ignored_source_path
    assert ignored(Path("flow/librelane/signoff_template.yaml"))
    assert ignored(Path("flow/librelane/signoff_waivers.yaml"))
    # Variant templates are the point: a one-variable experiment writes one.
    assert ignored(Path("flow/librelane/signoff_template_slew20.yaml"))


def test_the_delivery_wrappers_stay_in_the_rtl_closure():
    """The other half of the rule, and the one that matters if it breaks.

    Excluding too much is silent: a stale bundle gets hardened and nothing
    says so. These are synthesised, so they must always count.
    """
    ignored = build_manifest._ignored_source_path
    assert not ignored(Path("flow/librelane/experimental/mosaic_block_a.sv"))
    assert not ignored(Path("flow/librelane/src/mosaic_soc_core.sv"))
    # A yaml named like the template but elsewhere is NOT exempt -- the rule is
    # anchored to the one directory, not to the filename.
    assert not ignored(Path("flow/other/signoff_template.yaml"))


def test_a_signoff_run_does_not_poison_the_next_one():
    """`--save-views-to "$HERE/final_${TAG}"` writes a full set of views --
    GDS, DEF, ODB, netlists, metrics -- into flow/librelane/experimental/,
    which is a closure root.

    The exact names "final" and "final_classic" were ignored; "final_blocka_m45"
    was not. So each signoff run moved the hash of the RTL bundle it had just
    hardened and the NEXT run aborted with "no MOSAIC manifest. Generate the
    RTL first" -- a ~30 min regen each time, blamed on a missing input rather
    than on the previous run. Measured 2026-09-10: three consecutive
    re-hardens, three regenerations.

    This is the same defect the .resolved_/.filelist_ rule already fixed, in
    the directory that rule missed.
    """
    ignored = build_manifest._ignored_source_path
    for tag in ("blocka_m45", "blockb_m45", "blocka_1110_ndr", "m45_compileprobe"):
        p = Path(f"flow/librelane/experimental/final_{tag}/metrics.json")
        assert ignored(p), f"{p} would move the bundle hash"
    # The two names that were already covered must stay covered.
    assert ignored(Path("flow/librelane/experimental/final/mosaic_block_a.gds"))
    assert ignored(Path("flow/librelane/experimental/final_classic/x.gds"))
    # ...and excluding too much is the silent failure, so real sources still count.
    assert not ignored(Path("flow/librelane/experimental/mosaic_block_a.sv"))
    assert not ignored(Path("hw/asic/gf180/tc_pad.sv"))
    assert not ignored(Path("flow/librelane/experimental/config_blocka.yaml"))
