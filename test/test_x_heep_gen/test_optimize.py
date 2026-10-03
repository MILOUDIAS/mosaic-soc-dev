"""The optimizer: a line search over physical knobs, one setting per run.

Nothing here launches LibreLane. The search runs against a toy design whose
achieved critical path tracks the clock target down to a 38 ns floor, so
every verdict is known in advance: which clock closes, which one the
post-routing screen prunes, which gain is only the energy proxy. The launcher
and the kill are tested on real processes, because those two are what stand
between a prune and a second OpenROAD on an 11 GB machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from harness.physical.optimize import (_boot_id, _key, optimize, parse_knob,
                                       plan, signoff_launcher)
from harness.physical.report import HARD_CHECKS

FLOOR_NS = 38.0     # the toy design's fastest achievable critical path


class FakeRun:
    pgid = -1

    def __init__(self, finish, polls=2):
        self._finish, self._polls, self.killed = finish, polls, False

    def alive(self):
        if self.killed:
            return False
        if self._polls:
            self._polls -= 1
            return True
        self._finish()
        self._finish = lambda: None
        return False

    def kill(self):
        self.killed = True


NOMINAL_LOGIC_UM2 = 1_100_000.0   # what the area model predicts, so it sizes the die
BASE_UTIL = 0.70                  # the demonstrated-clean target this design starts at
ROUTES_UPTO = 0.80                # denser than this and the cells stop fitting cleanly


def toy(runs_root, launched, *, hold=0.1, bundle=lambda n: "d-0123456789ab"):
    """Tools work only as hard as the target asks (the real data: 79 ns of
    path at a 100 ns target, 38 ns at 40), down to a 38 ns floor. Dynamic
    power follows the clock, so energy per cycle is flat, and a repair margin
    below 40 saves 5% of it and nothing else.

    The die follows UTILISATION and not the clock, which is what the real runs
    show: Block B at 80, 66.7 and 50 ns shared one DIE_AREA, because the die is
    sized from the area model's prediction rather than from what a run achieved.
    Past `ROUTES_UPTO` the cells no longer fit cleanly and the run comes back
    with slew violations.
    """
    def launch(tag, ov):
        n = len(launched)
        launched.append(ov)
        period = ov.get("clock_period_ns", 100.0)
        margin = ov.get("repair_margin_pct", 45)
        util = ov.get("target_utilisation", BASE_UTIL)
        ws = period - max(FLOOR_NS, 0.8 * period)
        side = round((NOMINAL_LOGIC_UM2 / util) ** 0.5, 2)
        d = runs_root / tag
        (d / "44-openroad-stamidpnr-3").mkdir(parents=True)
        (d / "45-openroad-detailedrouting").mkdir()
        (d / "44-openroad-stamidpnr-3/state_out.json").write_text(json.dumps(
            {"metrics": {"timing__setup__ws": ws + 2, "timing__hold__ws": 0.3}}))
        (d / "resolved.json").write_text(json.dumps({
            "DESIGN_NAME": "d", "PDK": "p", "CLOCK_PERIOD": period,
            "GRT_DESIGN_REPAIR_MAX_SLEW_PCT": margin,
            "DIE_AREA": [0, 0, side, side],
            "CORE_AREA": [16, 16, round(side - 16, 2), round(side - 16, 2)],
            "VERILOG_FILES": [f"/r/build/mosaic/{bundle(n)}/a.sv"]}))

        def finish():
            (d / "final").mkdir()
            (d / "final/metrics.json").write_text(json.dumps({
                **{k: 0 for k in HARD_CHECKS},
                "timing__setup__ws": ws, "timing__hold__ws": hold,
                "design__max_slew_violation__count":
                    0 if util <= ROUTES_UPTO else 42,
                "design__max_cap_violation__count": 0,
                "design__die__area": round(side * side, 2),
                # Crowding costs cell area and energy, at the rates the real
                # Block B density pair measured: +5.1 points of utilisation
                # bought +0.53% cell area and +3.8% energy.
                "design__instance__area__class:stdcell":
                    (NOMINAL_LOGIC_UM2 + (100 - period) * 50)
                    * (1 + (util - BASE_UTIL) * 0.104),
                "power__total":
                    0.05 * 100 / period * (0.95 if margin < 40 else 1.0)
                    * (1 + (util - BASE_UTIL) * 0.745)}))
        return FakeRun(finish)
    return launch


def _search(tmp_path, space, max_runs=10, **toy_kw):
    launched = []
    out = optimize(space, journal=tmp_path / "j.jsonl",
                   runs_root=tmp_path / "runs", max_runs=max_runs,
                   launch=toy(tmp_path / "runs", launched, **toy_kw),
                   poll_s=0, sleep=lambda s: None, repo_root=tmp_path)
    return out, launched


CLOCK = [("clock_period_ns", [100, 80, 60, 45, 35, 30])]


# ── the search ───────────────────────────────────────────────────────
def test_the_search_tightens_the_clock_until_the_screen_prunes(tmp_path):
    out, launched = _search(tmp_path, CLOCK)
    assert out["incumbent"] == {"clock_period_ns": 45}
    assert [h["status"] for h in out["history"]] == ["accepted"] * 3 + ["pruned"]
    # 100 ns is the baseline's own clock and 30 ns lies past a failure:
    # neither was launched
    assert [o.get("clock_period_ns") for o in launched] == [None, 80, 60, 45, 35]
    assert out["ended"] == "every knob walked"


DENSITY = [("target_utilisation", [0.74, 0.78, 0.84])]


def test_a_density_walk_shrinks_the_die_and_stops_at_the_routing_ceiling(tmp_path):
    """One knob moving TWO derived keys. Utilisation is sized absolutely, so
    `derive_floorplan` emits both DIE_AREA and CORE_AREA -- the single-variable
    rule is about the knob, and a knob owns a set of keys."""
    out, launched = _search(tmp_path, DENSITY, max_runs=5)
    assert out["incumbent"] == {"target_utilisation": 0.78}, out["ended"]
    assert [h["status"] for h in out["history"]] == [
        "accepted", "accepted", "rejected"]
    assert [h["kept"] for h in out["history"]] == [True, True, False]
    # 0.84 is past the toy's routing ceiling: it comes back with slew
    # violations, so the gate rejects it and the knob ends there
    assert out["history"][-1]["overrides"]["target_utilisation"] == 0.84
    from harness.physical.ppa import evaluate
    base, _ = evaluate(tmp_path / "runs/opt_j_00", repo_root=tmp_path)
    best, _ = evaluate(tmp_path / "runs" / out["incumbent_run"].split("/")[-1],
                       repo_root=tmp_path)
    assert best.die_mm2 < base.die_mm2


def test_a_density_already_known_to_fail_costs_nothing(tmp_path):
    """The cheap stages exist to refuse a candidate before it is launched, and
    the search was not consulting them. Block C failed to route at a 0.75
    target, so 0.75 and denser must never reach a launcher: that is 2-4 h to
    re-prove a known failure."""
    from harness.physical.stages import screen_model
    launched = []

    def prescreen(overrides):
        u = overrides.get("target_utilisation")
        if u is None:
            return None
        verdict = screen_model(4, u)            # 4 harts: clean 0.65, failed 0.75
        return None if verdict.keep else verdict.reason

    out = optimize([("target_utilisation", [0.72, 0.76, 0.80])],
                   journal=tmp_path / "j.jsonl", runs_root=tmp_path / "runs",
                   max_runs=5, launch=toy(tmp_path / "runs", launched),
                   poll_s=0, sleep=lambda s: None, repo_root=tmp_path,
                   weights={"die_mm2": 3.0})
    # unscreened, every value is launched -- 0.76 and 0.80 cost a run each
    assert [o.get("target_utilisation") for o in launched] == [
        None, 0.72, 0.76, 0.80]

    launched.clear()
    out = optimize([("target_utilisation", [0.72, 0.76, 0.80])],
                   journal=tmp_path / "screened.jsonl",
                   runs_root=tmp_path / "runs2", max_runs=5,
                   launch=toy(tmp_path / "runs2", launched), poll_s=0,
                   sleep=lambda s: None, repo_root=tmp_path,
                   weights={"die_mm2": 3.0}, prescreen=prescreen)
    # 0.76 is past the recorded 0.75 failure: refused without a launch, and the
    # knob ends there rather than trying 0.80
    assert [o.get("target_utilisation") for o in launched] == [None, 0.72]
    assert out["incumbent"] == {"target_utilisation": 0.72}
    assert out["history"][-1]["status"] == "screened"
    assert "failed to route" in out["history"][-1]["reason"]
    # and the knob ENDS there: 0.80 is refused for the same reason, so it is
    # not even screened again
    assert len(out["history"]) == 2


def test_a_density_walk_needs_its_weights(tmp_path):
    """Why `--weight` exists. A density step trades die area against cell area
    and energy, and with equal weights the gain is thin; weighted heavily
    toward energy the same step reads as a regression and the knob stops at
    once. The search has to say what it is for."""
    launched = []
    out = optimize(DENSITY, journal=tmp_path / "j.jsonl",
                   runs_root=tmp_path / "runs", max_runs=5,
                   launch=toy(tmp_path / "runs", launched), poll_s=0,
                   sleep=lambda s: None, repo_root=tmp_path,
                   weights={"die_mm2": 1.0, "logic_um2": 1.0,
                            "energy_nj": 20.0, "fmax_mhz": 1.0})
    assert out["incumbent"] == {}, out["ended"]
    assert out["history"][0]["kept"] is False
    assert out["history"][0]["decided_by"] == "energy_nj"


def test_the_budget_counts_every_launch_including_the_baseline(tmp_path):
    out, launched = _search(tmp_path, CLOCK, max_runs=2)
    assert len(launched) == 2 and "budget spent" in out["ended"]
    assert out["incumbent"] == {"clock_period_ns": 80}


def test_a_resumed_journal_is_priced_for_what_is_left(tmp_path):
    """A continuation spends only the runs it has not done. Counting the whole
    plan again refused Block B's one-run 40 ns continuation for wanting 20 GB
    of disk when it needed 4."""
    from harness.physical.optimize import launches_recorded
    journal = tmp_path / "j.jsonl"
    assert launches_recorded(journal) == 0          # nothing yet
    _search(tmp_path, [("clock_period_ns", [80, 60])], max_runs=3)
    assert launches_recorded(journal) == 3          # baseline + two candidates
    # notes and results are not launches
    with journal.open("a") as fh:
        fh.write(json.dumps({"event": "note", "text": "seeded"}) + "\n")
    assert launches_recorded(journal) == 3


def test_a_routing_plateau_is_a_prune_and_a_cliff_is_not(tmp_path):
    """Both trajectories are real: Block C at a 0.75 target plateaued near 3500
    violations and burned eleven hours, and at 0.65 it fell off a cliff and was
    clean by iteration 10. The optimizer polls unattended, so the guard that is
    opt-in for a hand-launched run is on here."""
    from harness.physical.optimize import _routing_plateau
    d = tmp_path / "45-openroad-detailedrouting"
    d.mkdir(parents=True)

    def trajectory(counts):
        (d / "drt.log").write_text(
            "Start 0th optimization iteration\n"
            + "".join(f"Number of violations = {v}\n" for v in counts))

    trajectory((3500, 3480, 3470, 3460, 3455, 3450, 2906, 8552))
    assert _routing_plateau(tmp_path)
    trajectory((26579, 11986, 10827, 620, 55, 3, 0))
    assert _routing_plateau(tmp_path) is None


def test_the_journal_says_when_each_run_happened(tmp_path):
    """Without a stamp, how long a candidate took can only be recovered from
    run-directory mtimes -- and not at all once the run tree is pruned."""
    _search(tmp_path, [("clock_period_ns", [80])], max_runs=2)
    records = [json.loads(line) for line
               in (tmp_path / "j.jsonl").read_text().splitlines() if line.strip()]
    assert records and all(r["at"].startswith("20") for r in records)


def test_a_restart_repeats_nothing(tmp_path):
    first, launched = _search(tmp_path, CLOCK, max_runs=3)
    assert "budget spent" in first["ended"] and len(launched) == 3
    again, relaunched = _search(tmp_path, CLOCK, max_runs=10)
    assert [o["clock_period_ns"] for o in relaunched] == [45, 35]
    assert again["incumbent"] == {"clock_period_ns": 45}


def test_a_run_that_finished_while_the_optimizer_was_down_is_read(tmp_path):
    toy(tmp_path / "runs", [])("opt_j_00", {})._finish()
    gone = subprocess.Popen(["true"])
    gone.wait()
    (tmp_path / "j.jsonl").write_text(json.dumps({
        "event": "launch", "key": _key({}), "overrides": {},
        "tag": "opt_j_00", "pgid": gone.pid, "boot": _boot_id()}) + "\n")
    out, launched = _search(tmp_path, [])
    assert launched == [] and out["incumbent_run"].endswith("opt_j_00")


def test_a_process_group_from_before_a_reboot_is_not_the_run(tmp_path):
    """After a reboot the recorded number can belong to anything. Waiting on
    it would stall the search behind a stranger's process."""
    toy(tmp_path / "runs", [])("opt_j_00", {})._finish()
    stranger = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        (tmp_path / "j.jsonl").write_text(json.dumps({
            "event": "launch", "key": _key({}), "overrides": {},
            "tag": "opt_j_00", "pgid": stranger.pid,
            "boot": "a-previous-boot"}) + "\n")
        out = optimize(
            [], journal=tmp_path / "j.jsonl", runs_root=tmp_path / "runs",
            max_runs=1, launch=toy(tmp_path / "runs", []), poll_s=0,
            sleep=lambda s: pytest.fail("waited on another boot's process"),
            repo_root=tmp_path)
        assert out["incumbent_run"].endswith("opt_j_00")
    finally:
        stranger.kill()
        stranger.wait()


def test_a_second_setting_moving_stops_the_search(tmp_path):
    """The RTL bundle changed under the third run, as when a generator source
    is edited mid-session. That run measures two things at once."""
    out, _ = _search(tmp_path, CLOCK, bundle=lambda n: (
        "d-0123456789ab" if n < 2 else "d-fedcba987654"))
    assert "rtl_bundle" in out["ended"]
    assert out["incumbent"] == {"clock_period_ns": 80}


def test_a_rejected_baseline_is_not_a_starting_point(tmp_path):
    out, launched = _search(tmp_path, CLOCK, hold=-0.1)
    assert len(launched) == 1 and "baseline is rejected" in out["ended"]


def test_a_gain_decided_by_the_energy_proxy_is_not_taken(tmp_path):
    """A lower repair margin that only saves default-toggle energy: a positive
    score, and exactly the gain a loop must not act on."""
    out, _ = _search(tmp_path, [("repair_margin_pct", [32])])
    step, = out["history"]
    assert step["score"] > 0 and step["decided_by"] == "energy_nj"
    assert not step["kept"] and out["incumbent"] == {}


# ── what it may touch ────────────────────────────────────────────────
def test_only_declared_knobs_can_be_set():
    """The optimizer must never reach a key like RUN_KLAYOUT_DRC."""
    assert parse_knob("clock_period_ns=80,62.5") == ("clock_period_ns",
                                                     [80.0, 62.5])
    assert parse_knob("target_utilisation=0.74,0.78") == (
        "target_utilisation", [0.74, 0.78])
    for bad in ("RUN_KLAYOUT_DRC=0", "clock_period_ns=", "clock_period_ns=-5",
                "repair_margin_pct=32.5",
                # a utilisation written as a percentage asks for a die 78x
                # denser than its cells, not one a fifth empty
                "target_utilisation=78", "target_utilisation=1.2"):
        with pytest.raises(ValueError):
            parse_knob(bad)


def test_the_plan_is_the_longest_walk():
    assert plan(CLOCK)[:3] == [{}, {"clock_period_ns": 100},
                              {"clock_period_ns": 80}]
    assert len(plan(CLOCK)) == 1 + 6


def _script(tmp_path, body):
    script = tmp_path / "flow/librelane/experimental/run_signoff.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return script.parent


def test_the_launcher_passes_only_this_candidates_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSAIC_WATCH_ROUTING", "1")   # librelane out of a kill's reach
    monkeypatch.setenv("MOSAIC_HARDEN_UTIL", "0.9")   # a different die, silently
    monkeypatch.setenv("MOSAIC_HARDEN_MARGIN", "10")  # a stale override
    monkeypatch.setenv("MOSAIC_MANIFEST", "/stale/manifest.json")  # other RTL
    here = _script(tmp_path, 'env > env.txt\n'
                             'echo "$(ps -o sid= -p $$ | tr -d " ") $$" > sid.txt\n')
    run = signoff_launcher(tmp_path / "soc.yaml", "d", tmp_path,
                           log_dir=tmp_path / "logs")("t", {"clock_period_ns": 62.5})
    run.proc.wait(10)
    env = dict(line.split("=", 1) for line in
               (here / "env.txt").read_text().splitlines() if "=" in line)
    assert env["MOSAIC_HARDEN_CLOCK_NS"] == "62.5"
    for scrubbed in ("MOSAIC_WATCH_ROUTING", "MOSAIC_HARDEN_UTIL",
                     "MOSAIC_HARDEN_MARGIN", "MOSAIC_MANIFEST"):
        assert scrubbed not in env, scrubbed

    # pinning the series' RTL is the only way MOSAIC_MANIFEST gets set
    pinned = signoff_launcher(tmp_path / "soc.yaml", "d", tmp_path,
                              log_dir=tmp_path / "logs",
                              manifest="/b/manifest.json")("t2", {})
    pinned.proc.wait(10)
    env2 = dict(line.split("=", 1) for line in
                (here / "env.txt").read_text().splitlines() if "=" in line)
    assert env2["MOSAIC_MANIFEST"] == "/b/manifest.json"
    assert env["MOSAIC_HARDEN_FROM_SOC"] == env["MOSAIC_CFG"] == str(tmp_path / "soc.yaml")
    sid, pid = (here / "sid.txt").read_text().split()
    assert sid == pid       # a session of its own: a prune kills this tree only


def test_a_prune_kills_the_whole_process_group(tmp_path):
    """OpenROAD outliving its run would share 11 GB with the next one."""
    here = _script(tmp_path, "sleep 300 &\necho $! > child.pid\nwait\n")
    run = signoff_launcher(tmp_path / "soc.yaml", "d", tmp_path,
                           log_dir=tmp_path / "logs")("t", {})
    pidfile = here / "child.pid"
    for _ in range(100):
        if pidfile.is_file() and pidfile.read_text().strip():
            break
        time.sleep(0.05)
    child = int(pidfile.read_text())
    run.kill(grace_s=5)
    assert not run.alive()
    for _ in range(100):            # reaped by init a moment after it dies
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"child {child} survived the kill")


def test_run_signoff_forwards_every_knob_the_optimizer_sets():
    """This cost two runs on 2026-09-11. The optimizer set
    MOSAIC_HARDEN_CLOCK_NS, run_signoff.sh never forwarded it to `harden`, and
    the candidate silently re-ran the baseline's clock. The single-setting
    check caught it -- as a comparison where nothing had moved -- but only
    after a 2 h run. Every knob's variable must reach a flag, and testing
    `harden` alone does not show that."""
    from harness.core import REPO_ROOT
    from harness.physical.optimize import KNOBS
    script = (REPO_ROOT
              / "flow/librelane/experimental/run_signoff.sh").read_text()
    for var, _ in KNOBS.values():
        line = next((l for l in script.splitlines() if f"${{{var}:+" in l), None)
        assert line and "--" in line, f"{var} never reaches a harden flag"
    # and an override that cannot take effect must fail, not be ignored
    assert "need MOSAIC_HARDEN_FROM_SOC" in script
