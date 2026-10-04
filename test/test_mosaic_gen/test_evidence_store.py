"""Content-addressed evidence, and invalidation that cannot drift.

The store's whole design is that invalidation is not a mechanism. The key is
the digest of the inputs, so "is my evidence stale?" is "does a record exist
under the key today's inputs produce?" -- and if any input moved, it does not.
These tests hold that property one input at a time.
"""

import json
import pathlib

import pytest

from harness.core import REPO_ROOT
from harness.evidence.store import (
    EvidenceConflict,
    EvidenceInputs,
    EvidenceRecord,
    EvidenceStore,
    PDK_VIEW_KEYS,
    bundle_from_config,
    config_digest,
    parser_digest,
    pdk_view_digest,
    tool_digest,
)

RUNS = REPO_ROOT / "flow/librelane/experimental/runs"


def inputs(**overrides):
    base = dict(
        rtl_bundle="mosaic_block_a-fd4642d417f9",
        config_digest="c" * 64,
        pdk="gf180mcuD",
        std_cell_library="gf180mcu_fd_sc_mcu7t5v0",
        tool="t" * 64,
        parser="p" * 64,
        pdk_views="v" * 64,
    )
    base.update(overrides)
    return EvidenceInputs(**base)


def record(store_inputs=None, **kw):
    return EvidenceRecord(
        inputs=store_inputs or inputs(),
        design=kw.pop("design", "mosaic_block_a"),
        run_dir=kw.pop("run_dir", "runs/blocka_reharden"),
        summary=kw.pop("summary", {"adverse": 0}),
        **kw,
    )


# ── every input changes the key. one at a time. ──────────────────────

@pytest.mark.parametrize("field,changed", [
    ("rtl_bundle", "mosaic_block_a-000000000000"),
    ("config_digest", "d" * 64),
    ("pdk", "sky130A"),
    ("std_cell_library", "gf180mcu_fd_sc_mcu9t5v0"),
    ("tool", "u" * 64),
    ("parser", "q" * 64),
    ("pdk_views", "w" * 64),
])
def test_changing_any_input_changes_the_key(field, changed):
    """Config, RTL, PDK views, tool image and PARSER all invalidate."""
    assert inputs().key() != inputs(**{field: changed}).key()


def test_identical_inputs_give_the_same_key():
    """Deterministic, or the store never hits."""
    assert inputs().key() == inputs().key()


def test_the_key_does_not_depend_on_when_it_was_recorded():
    """A timestamp in the key would defeat the whole design."""
    a = record(recorded_at="2026-08-11T09:00:00Z")
    b = record(recorded_at="2026-01-01T00:00:00Z")
    assert a.key == b.key


def test_a_pdk_swap_is_a_different_measurement():
    """GF180 is the first PDK, not the only one.

    The same RTL on SkyWater is not the same evidence, and nothing about the
    numbers says so on their face -- only the key does.
    """
    gf = inputs(pdk="gf180mcuD")
    sky = inputs(pdk="sky130A")
    assert gf.key() != sky.key()


# ── immutability ─────────────────────────────────────────────────────

def test_re_recording_identical_evidence_is_a_no_op(tmp_path):
    store = EvidenceStore(tmp_path)
    first = store.put(record())
    second = store.put(record())
    assert first == second


def test_the_same_inputs_producing_different_output_is_refused(tmp_path):
    """The contradiction that matters.

    If the inputs determine the output, two different outputs under one key
    means an input is not being captured. Overwriting would hide exactly the
    bug the store exists to expose.
    """
    store = EvidenceStore(tmp_path)
    store.put(record(summary={"adverse": 0}))
    with pytest.raises(EvidenceConflict, match="not being captured"):
        store.put(record(summary={"adverse": 7}))


def test_a_stored_record_round_trips(tmp_path):
    store = EvidenceStore(tmp_path)
    original = record(recorded_at="2026-08-11T09:00:00Z")
    store.put(original)
    loaded = store.get(original.key)
    assert loaded == original


# ── lookup is the staleness check ────────────────────────────────────

def test_lookup_misses_when_an_input_moved(tmp_path):
    """There is no separate staleness API, and that is the design."""
    store = EvidenceStore(tmp_path)
    store.put(record())
    assert store.lookup(inputs()) is not None
    assert store.lookup(inputs(parser="different" + "0" * 55)) is None


def test_find_answers_what_a_change_would_invalidate(tmp_path):
    store = EvidenceStore(tmp_path)
    store.put(record(inputs(pdk="gf180mcuD"), run_dir="a"))
    store.put(record(inputs(pdk="gf180mcuD", rtl_bundle="x-000000000000"),
                     run_dir="b"))
    store.put(record(inputs(pdk="sky130A"), run_dir="c"))

    assert len(store.find(pdk="gf180mcuD")) == 2
    assert len(store.invalidated_by(pdk="sky130A")) == 1
    assert store.find(pdk="ihp-sg13g2") == []


def test_an_empty_store_answers_rather_than_raising(tmp_path):
    store = EvidenceStore(tmp_path / "nothing")
    assert list(store.records()) == []
    assert store.get("a" * 64) is None
    assert store.find(pdk="gf180mcuD") == []


def test_corrupt_records_are_skipped_not_fatal(tmp_path):
    store = EvidenceStore(tmp_path)
    store.put(record())
    bad = tmp_path / "zz" / ("f" * 64 + ".json")
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json")
    assert len(list(store.records())) == 1


# ── the digests read real artefacts ──────────────────────────────────

def test_the_parser_digest_covers_the_modules_that_interpret_a_run():
    from harness.evidence.store import PARSER_MODULES

    for relative in PARSER_MODULES:
        assert (REPO_ROOT / relative).is_file(), f"{relative} moved"
    assert len(parser_digest(REPO_ROOT)) == 64


def test_a_missing_parser_is_a_change_not_a_silent_pass(tmp_path):
    """Evidence derived by code that no longer exists must not be reused."""
    assert parser_digest(tmp_path) != parser_digest(REPO_ROOT)


def test_the_config_digest_ignores_machine_specific_paths():
    """Two checkouts of one commit must agree, or evidence is unshareable."""
    a = {"DESIGN_NAME": "x", "VERILOG_FILES": ["/home/alice/a.sv"],
         "PDK_ROOT": "/home/alice/pdk"}
    b = {"DESIGN_NAME": "x", "VERILOG_FILES": ["/home/bob/a.sv"],
         "PDK_ROOT": "/home/bob/pdk"}
    assert config_digest(a) == config_digest(b)
    # ...but a real setting still counts.
    assert config_digest(a) != config_digest({**a, "CLOCK_PERIOD": 40})


def test_the_rtl_bundle_is_recovered_from_the_runs_own_paths():
    resolved = {"VERILOG_FILES": [
        "/x/build/mosaic/mosaic_block_c-8811eae1dc01/runs/fusesoc.a/build/y.sv"]}
    assert bundle_from_config(resolved) == "mosaic_block_c-8811eae1dc01"
    assert bundle_from_config({"VERILOG_FILES": ["/tmp/loose.sv"]}) is None
    assert bundle_from_config({}) is None


def test_the_tool_digest_needs_more_than_a_version_string():
    """Two 3.0.0 images from different nixpkgs are different tools."""
    lock = REPO_ROOT / "flow/librelane/flake.lock"
    if not lock.is_file():
        pytest.skip("flake.lock not present")
    assert tool_digest("3.0.0", lock) != tool_digest("3.0.0", None)
    assert tool_digest("3.0.0", lock) != tool_digest("3.0.1", lock)


def _lock(nix_eda_hash, nixpkgs_hash, librelane_hash="sha256-LL"):
    """A lock shaped like flow/librelane/flake.lock: nix-eda and nixpkgs are
    reached only THROUGH librelane, and named nothing like it."""
    return {"root": "root", "version": 7, "nodes": {
        "root": {"inputs": {"librelane": "librelane"}},
        "librelane": {"inputs": {"nix-eda": "nix-eda"},
                      "locked": {"narHash": librelane_hash, "rev": "a"}},
        "nix-eda": {"inputs": {"nixpkgs": "nixpkgs"},
                    "locked": {"narHash": nix_eda_hash, "rev": "b"}},
        "nixpkgs": {"inputs": {}, "locked": {"narHash": nixpkgs_hash, "rev": "c"}},
    }}


def test_a_nix_eda_or_nixpkgs_bump_is_a_different_tool(tmp_path):
    """Both rebuild every EDA binary. The digest used to hash only lock nodes
    NAMED like librelane, so either bump kept the same evidence key."""
    import json
    paths = []
    for i, lock in enumerate((_lock("E1", "P1"), _lock("E2", "P1"), _lock("E1", "P2"))):
        path = tmp_path / f"flake{i}.lock"
        path.write_text(json.dumps(lock))
        paths.append(path)
    digests = {tool_digest("3.0.0", p) for p in paths}
    assert len(digests) == 3


def test_the_runtime_is_part_of_the_tool():
    """The IIC container's signoff tools are other versions than nix-eda's."""
    lock = REPO_ROOT / "flow/librelane/flake.lock"
    assert tool_digest("3.0.0", lock, "nix") != tool_digest("3.0.0", lock, "iic:2026.09")
    from harness.evidence.store import evidence_runtime
    assert evidence_runtime({}) == "nix"
    assert evidence_runtime({"MOSAIC_TOOLCHAIN": "nix"}) == "nix"
    assert evidence_runtime({"MOSAIC_TOOLCHAIN": "iic:2026.09"}) == "iic:2026.09"


# ── against the real runs ────────────────────────────────────────────

def test_real_runs_produce_distinct_keys():
    present = [t for t in ("blocka_signoff", "blocka_reharden",
                           "blockb_reharden", "blockc_slew45")
               if (RUNS / t / "resolved.json").is_file()]
    if len(present) < 2:
        pytest.skip("run trees not present")
    keys = {}
    for tag in present:
        got = EvidenceInputs.from_run(RUNS / tag, repo_root=REPO_ROOT)
        assert got is not None
        assert got.pdk and got.std_cell_library
        keys[tag] = got.key()
    assert len(set(keys.values())) == len(present), keys


def test_the_two_block_a_runs_differ_by_their_inputs():
    """Same design, same die, different template -- different evidence."""
    for tag in ("blocka_signoff", "blocka_reharden"):
        if not (RUNS / tag / "resolved.json").is_file():
            pytest.skip("run trees not present")
    old = EvidenceInputs.from_run(RUNS / "blocka_signoff", repo_root=REPO_ROOT)
    new = EvidenceInputs.from_run(RUNS / "blocka_reharden", repo_root=REPO_ROOT)
    assert old.key() != new.key()
    # It is the config and the RTL bundle that moved, not the PDK or tool.
    assert old.pdk == new.pdk
    assert old.config_digest != new.config_digest


def test_a_run_without_a_resolved_config_yields_nothing():
    got = EvidenceInputs.from_run(RUNS / "does_not_exist", repo_root=REPO_ROOT)
    assert got is None


# ── the PDK views themselves, not just their names ───────────────────
# The store used to record the PDK by NAME, which caught a swap and not an
# in-place edit, and said so. The 1.2 GB that justified it was the wrong
# measurement: a run consumes what resolved.json names, which is ~500 files
# and ~86 MB.

def _resolved():
    path = RUNS / "blocka_signoff/resolved.json"
    if not path.is_file():
        pytest.skip("no hardening run staged")
    return json.loads(path.read_text())


def test_editing_a_pdk_view_in_place_changes_the_digest(tmp_path):
    """The hole this closes. Same PDK name, same library, edited liberty."""
    r = _resolved()
    before = pdk_view_digest(r)
    corner = sorted(r["LIB"])[0]
    original = pathlib.Path(r["LIB"][corner][0])
    patched = tmp_path / original.name
    patched.write_bytes(original.read_bytes() + b"\n// local patch\n")
    r["LIB"] = {**r["LIB"], corner: [str(patched)] + r["LIB"][corner][1:]}
    assert pdk_view_digest(r) != before


def test_the_same_pdk_at_a_different_path_is_the_same_digest(tmp_path):
    """resolved.json records paths under whichever checkout wrote it. Two
    clones of one commit must agree that an identical PDK is identical --
    the same reason config_digest drops the resolved file list."""
    r = _resolved()
    before = pdk_view_digest(r)
    corner = sorted(r["LIB"])[0]
    original = pathlib.Path(r["LIB"][corner][0])
    moved = tmp_path / original.name
    moved.write_bytes(original.read_bytes())
    r["LIB"] = {**r["LIB"], corner: [str(moved)] + r["LIB"][corner][1:]}
    assert pdk_view_digest(r) == before


def test_a_view_named_but_missing_is_not_silently_skipped(tmp_path):
    """Skipping it would let deleting a liberty reproduce the key of a run
    that had one, which is worse than the gap this replaced."""
    r = _resolved()
    before = pdk_view_digest(r)
    corner = sorted(r["LIB"])[0]
    r["LIB"] = {**r["LIB"],
                corner: [str(tmp_path / "not_there.lib")] + r["LIB"][corner][1:]}
    assert pdk_view_digest(r) != before


def test_a_config_naming_no_views_digests_to_none():
    """A non-LibreLane config is not a PDK edit, and must not read as one."""
    assert pdk_view_digest({}) is None
    assert pdk_view_digest({"PDK": "gf180mcuD"}) is None


def test_the_view_keys_cover_what_the_metrics_depend_on():
    """Timing, area, parasitics, gate-level sim and the DRC/LVS verdicts."""
    for expected in ("LIB", "TECH_LEFS", "CELL_LEFS", "RCX_RULESETS",
                     "CELL_VERILOG_MODELS", "CELL_GDS"):
        assert expected in PDK_VIEW_KEYS


def test_a_real_run_populates_the_views_digest():
    r = _resolved()
    d = pdk_view_digest(r)
    assert d is not None and len(d) == 64
