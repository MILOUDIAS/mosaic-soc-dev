"""The intent boundary: "no direct access to raw YAML".

This is the one slice of the planned intermediate representation that is
load-bearing now. The IR can be deferred without cost *provided the physical
lowering reads through a small explicit interface rather than raw YAML*, and
yet the floorplan derivation first shipped as
`derive_floorplan(soc.get("cores", []))`, so the constraint that was meant to
preserve the option was never imposed.

The tests that matter here are the equivalence ones. A refactor whose exit
criterion is "existing generated artifacts remain semantically equivalent"
earns nothing except confidence, and it only earns that if the equivalence is
actually checked against every shipped config.
"""

import yaml

import pytest

from harness.core import REPO_ROOT
from harness.intent import CoreGroup, DesignIntent, Memory, Objectives, coerce
from harness.physical.floorplan import (
    clock_period_ns,
    derive_floorplan,
    estimate_logic_area,
)
from harness.physical.hardening import generate_hardening_config

SHIPPED = sorted((REPO_ROOT / "configs").glob("mosaic_*.yaml"))


def soc_of(path):
    return (yaml.safe_load(path.read_text()) or {}).get("soc") or {}


def test_there_are_configs_to_check():
    """A glob that silently matched nothing would make this file vacuous."""
    assert len(SHIPPED) >= 3


# ── equivalence: the mapping path and the typed path agree ───────────

@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_the_typed_view_derives_the_identical_floorplan(path):
    soc = soc_of(path)
    from_mapping, errors_mapping = derive_floorplan(soc)
    from_intent, errors_intent = derive_floorplan(DesignIntent.from_soc(soc))

    assert from_mapping == from_intent
    assert errors_mapping == errors_intent


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_the_typed_view_estimates_the_identical_area(path):
    soc = soc_of(path)
    assert estimate_logic_area(soc) == estimate_logic_area(
        DesignIntent.from_soc(soc))


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_the_typed_view_derives_the_identical_clock(path):
    soc = soc_of(path)
    assert clock_period_ns(soc) == clock_period_ns(DesignIntent.from_soc(soc))


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_the_generated_hardening_config_is_byte_identical(path):
    """The strongest form of "semantically equivalent" available: the bytes."""
    soc = soc_of(path)
    text_mapping, errors_mapping = generate_hardening_config(
        soc, "mosaic_block_x", repo_root=REPO_ROOT, clock_period_override=100.0)
    text_intent, errors_intent = generate_hardening_config(
        DesignIntent.from_soc(soc), "mosaic_block_x", repo_root=REPO_ROOT,
        clock_period_override=100.0)

    assert text_mapping == text_intent
    assert errors_mapping == errors_intent


# ── the round trip does not invent or lose anything ──────────────────

@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_validated_key_survives_the_round_trip(path):
    """A lossy view is worse than raw YAML: the loss is invisible."""
    soc = soc_of(path)
    round_tripped = DesignIntent.from_soc(soc).to_soc()
    assert set(round_tripped) >= set(soc), (
        f"lost {sorted(set(soc) - set(round_tripped))}")


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_objective_survives_the_round_trip(path):
    """The check above compares TOP-LEVEL keys, so `objectives` being present
    satisfied it while a field inside was dropped -- which is how to_soc lost
    repair_margin_pct, Block A's 32, the value between zero violations and 28
    max-slew plus 14 max-cap on its mandated die. Nested, the loss is visible.
    """
    soc = soc_of(path)
    stated = soc.get("objectives") or {}
    if not stated:
        pytest.skip("this design states no objectives")
    returned = DesignIntent.from_soc(soc).to_soc().get("objectives") or {}
    assert set(returned) >= set(stated), (
        f"lost {sorted(set(stated) - set(returned))}")
    for key, value in stated.items():
        assert returned[key] == value, key


def test_a_new_objective_cannot_be_forgotten_by_the_view():
    """to_soc derives the objectives from the dataclass rather than a list, so
    a field added to Objectives travels without anyone remembering to."""
    from dataclasses import fields

    from harness.intent import Objectives
    from util.mosaic_gen.core_registry import OBJECTIVE_KEYS

    assert {f.name for f in fields(Objectives)} == set(OBJECTIVE_KEYS)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_the_round_trip_still_validates(path):
    from util.mosaic_gen.core_registry import validate_soc_config

    soc = soc_of(path)
    rebuilt = {"soc": DesignIntent.from_soc(soc).to_soc()}
    assert validate_soc_config(rebuilt) == []


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_core_groups_survive_with_their_parameters(path):
    """SERV's `conf`, FazyRV's `chunksize`: dropping them changes the design."""
    soc = soc_of(path)
    intent = DesignIntent.from_soc(soc)
    for original, group in zip(soc.get("cores") or [], intent.cores):
        assert group.ip == original["ip"]
        assert group.count == original.get("count", 1)
        for key, value in original.items():
            if key in {"ip", "isa", "count", "role"}:
                continue
            assert group.parameters[key] == value


# ── the defaults that used to be inlined, inconsistently ─────────────

def test_a_null_memory_block_reads_as_the_default_not_a_crash():
    """`soc.get("memory", {})` and `soc.get("memory") or {}` disagree here.

    Both spellings existed in the codebase. With `memory:` present and null,
    the first returns None and the next `.get` raises.
    """
    assert Memory.from_mapping(None).sram_kb == 32
    assert DesignIntent.from_soc({"memory": None}).memory.sram_kb == 32


def test_silence_about_sram_is_not_zero_sram():
    """The area model refuses SRAM designs, so this default is load-bearing."""
    assert DesignIntent.from_soc({}).memory.has_macros is True
    assert DesignIntent.from_soc({"memory": {"sram_kb": 0}}).memory.has_macros is False


def test_objectives_are_all_optional_and_none_is_a_claim():
    empty = Objectives.from_mapping(None)
    assert empty.clock_period_ns is None
    assert empty.die_um is None
    assert Objectives.from_mapping({"target_clock_mhz": 25}).clock_period_ns == 40.0


def test_hart_count_sums_the_groups():
    intent = DesignIntent.from_soc({"cores": [
        {"ip": "serv", "count": 3}, {"ip": "serv"}]})
    assert intent.hart_count == 4
    assert intent.is_only("serv")


def test_is_only_is_false_for_a_mixed_design():
    intent = DesignIntent.from_soc({"cores": [
        {"ip": "serv", "count": 2}, {"ip": "cv32e20"}]})
    assert not intent.is_only("serv")
    assert intent.core_ips == frozenset({"serv", "cv32e20"})


# ── construction refuses what the validator refuses ──────────────────

def test_from_config_validates_rather_than_forming_its_own_opinion():
    """One validator. This module must never become a second one."""
    good = yaml.safe_load((REPO_ROOT / "configs/mosaic_blockc_4hart.yaml").read_text())
    intent, errors = DesignIntent.from_config(good)
    assert errors == [] and intent is not None
    assert intent.hart_count == 4

    bad = yaml.safe_load(yaml.safe_dump(good))
    bad["soc"]["memroy"] = {"sram_kb": 0}
    intent, errors = DesignIntent.from_config(bad)
    assert intent is None
    assert any("memroy" in e for e in errors)


def test_an_unknown_core_is_refused_by_name():
    good = yaml.safe_load((REPO_ROOT / "configs/mosaic_blockc_4hart.yaml").read_text())
    good["soc"]["cores"][0]["ip"] = "not_a_core"
    intent, errors = DesignIntent.from_config(good)
    assert intent is None
    assert any("not_a_core" in e for e in errors)


def test_coerce_accepts_both_and_is_idempotent():
    intent = DesignIntent.from_soc({"cores": [{"ip": "serv", "count": 2}]})
    assert coerce(intent) is intent
    assert coerce({"cores": [{"ip": "serv", "count": 2}]}) == intent
    assert coerce(None).hart_count == 0


def test_core_group_survives_a_non_integer_count_without_crashing():
    """The validator rejects this; the view must not explode reaching it."""
    assert CoreGroup.from_mapping({"ip": "serv", "count": "two"}).count == 1


# ── the boundary itself ──────────────────────────────────────────────

def test_the_physical_lowering_no_longer_reads_raw_yaml():
    """The boundary, enforced rather than asserted in prose.

    `soc.get(...)` in these modules means a consumer went around the typed
    view, which is how the boundary erodes.
    """
    for module in ("floorplan.py", "hardening.py"):
        source = (REPO_ROOT / "harness" / "physical" / module).read_text()
        offenders = [line.strip() for line in source.splitlines()
                     if "soc.get(" in line and not line.strip().startswith("#")]
        assert not offenders, f"{module} still reads raw YAML: {offenders}"
