"""Preflight checks, one per wasted run.

A three-block re-harden on 2026-09-09/10 lost roughly seven hours, and not
one of those hours went on a hard problem. Every failure was a precondition,
and every one reported itself as something downstream. These tests pin the
checks to the incidents that bought them.
"""
from __future__ import annotations

import pathlib

import pytest

from harness.core import REPO_ROOT
from harness.skills.flow_preflight import (
    NEEDS_GB, TEMPLATE_MARKERS, audit, preflight)


def _cfg(tmp_path, text, name="c.yaml"):
    p = tmp_path / name
    p.write_text(text)
    return p


WHOLE = "\n".join(f"{k}: x" for k in TEMPLATE_MARKERS) + "\nDESIGN_NAME: d\n"


# ── the fragment that cost two runs ──────────────────────────────────
def test_a_config_without_the_template_is_blocking(tmp_path):
    """run_signoff.sh does `cat CONFIG FRAGMENT` -- the config IS the whole
    thing. A file of design keys alone parses, runs, and silently takes
    LibreLane's defaults; USE_SLANG went False and the first symptom was a
    yosys syntax error in a vendored lowrisc file."""
    frag = _cfg(tmp_path, "DESIGN_NAME: d\nDIE_AREA: [0,0,10,10]\n")
    rep = preflight("harden", config=str(frag), repo_root=tmp_path)
    assert not rep.ok
    joined = " ".join(rep.blocking)
    assert "FRAGMENT" in joined and "does not merge the template" in joined


def test_a_whole_config_passes_that_check(tmp_path):
    rep = preflight("harden", config=str(_cfg(tmp_path, WHOLE)),
                    repo_root=tmp_path)
    assert not any("FRAGMENT" in b for b in rep.blocking)


# ── the disk that filled three hours in ──────────────────────────────
def test_disk_is_checked_against_the_stage(tmp_path, monkeypatch):
    """The run that ignored this died at step 53 of 60 with OSError 28,
    after routing had already reached 0 DRT and 0 antenna violations."""
    import harness.skills.flow_preflight as m
    monkeypatch.setattr(m, "_free_gb", lambda p: 3.0)
    rep = m.preflight("harden", repo_root=tmp_path)
    assert not rep.ok and any("3G free" in b for b in rep.blocking)
    # a regen needs more than a harden, and the message must say which
    assert NEEDS_GB["regen"] > NEEDS_GB["harden"]
    rep2 = m.preflight("regen", repo_root=tmp_path)
    assert any(str(NEEDS_GB["regen"]) in b for b in rep2.blocking)


def test_enough_disk_is_not_blocking(tmp_path, monkeypatch):
    import harness.skills.flow_preflight as m
    monkeypatch.setattr(m, "_free_gb", lambda p: 500.0)
    assert not m.preflight("harden", repo_root=tmp_path).blocking


# ── the reused run tag that cost a misdiagnosis ──────────────────────
def test_an_existing_run_tag_warns_but_does_not_block(tmp_path, monkeypatch):
    """Step directories from the earlier attempt survive alongside the new
    ones. Reading a stale config.json as the live run cost ~15 minutes.

    Disk is pinned here because tmp_path is on a DIFFERENT filesystem from
    the repo -- often a small one -- so the real check would fire and mask
    what this test is about.
    """
    import harness.skills.flow_preflight as m
    monkeypatch.setattr(m, "_free_gb", lambda p: 500.0)
    (tmp_path / "flow/librelane/experimental/runs/t1").mkdir(parents=True)
    rep = preflight("harden", tag="t1", repo_root=tmp_path)
    assert any("already exists" in w for w in rep.warnings)
    assert not rep.blocking, "a stale tag is recoverable; do not block on it"
    assert not preflight("harden", tag="t2", repo_root=tmp_path).warnings


# ── rules pinned to names that do not survive ────────────────────────
def test_synthesis_named_rules_are_flagged(tmp_path):
    """Twice now: `_28773_` looked like a smoking gun in the GLS work, and a
    DRT_ASSIGN_NDR pinned to `_06890_` stopped applying after a re-harden."""
    cfg = _cfg(tmp_path, WHOLE + "DRT_ASSIGN_NDR:\n  '^_06890_$': wide\n")
    rep = preflight("harden", config=str(cfg), repo_root=tmp_path)
    assert any("_06890_" in w and "re-synthesis" in w for w in rep.warnings)


def test_a_comment_about_the_hazard_is_not_the_hazard(tmp_path):
    """The first version scanned raw text and fired on a comment EXPLAINING
    that pinning to synthesis names is a mistake. A guard that trips on prose
    describing the hazard is one people learn to ignore."""
    cfg = _cfg(tmp_path, WHOLE + "# do not pin rules to _06890_ or _07065_\n")
    rep = preflight("harden", config=str(cfg), repo_root=tmp_path)
    assert not any("re-synthesis" in w for w in rep.warnings)


# ── shape ────────────────────────────────────────────────────────────
def test_preflight_never_repairs_anything(tmp_path):
    """A preflight that fixes state hides the thing it exists to surface."""
    cfg = _cfg(tmp_path, WHOLE)
    before = sorted(p.name for p in tmp_path.iterdir())
    preflight("harden", config=str(cfg), tag="x", repo_root=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_it_reports_what_it_checked_even_when_clean(tmp_path, monkeypatch):
    """Silence is indistinguishable from 'did not run'."""
    import harness.skills.flow_preflight as m
    monkeypatch.setattr(m, "_free_gb", lambda p: 500.0)
    rep = m.preflight("harden", config=str(_cfg(tmp_path, WHOLE)),
                      repo_root=tmp_path)
    assert rep.ok and len(rep.checked) >= 3


def test_the_skill_is_declared_and_carded():
    from harness.skill_policy import CARDS, SKILLS, approval_required
    assert "flow-preflight" in SKILLS
    assert approval_required("flow-preflight") is False
    card = REPO_ROOT / ".claude/skills" / CARDS["flow-preflight"] / "SKILL.md"
    assert card.is_file(), f"no card at {card}"


def test_it_runs_against_the_real_repo():
    r = audit("harden", soc_config="configs/mosaic_tapeout_ultra.yaml")
    assert any("disk:" in c for c in r.details["checked"])
    assert any("bundle:" in c for c in r.details["checked"])
