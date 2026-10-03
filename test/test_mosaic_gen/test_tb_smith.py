"""tb-smith tests: generated artifacts, marker parsing, and (slow) a real
single-hart TB run for picorv32.

Run from the repo root: python3 -m pytest test/test_mosaic_gen/test_tb_smith.py
"""

import pathlib
import sys

import pytest

directory = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(directory))

from harness.skills.tb_smith import TbSmith  # noqa: E402

REPO_ROOT = directory


@pytest.fixture
def staged_root(tmp_path):
    """A throwaway repo root that tb-smith may write into.

    `generate()` writes `tb/sci/<core>/` under its repo_root, and these tests
    passed the REAL one. So running the suite silently rewrote three tracked
    benches -- and on 2026-09-08 that reverted an async-reset fix which had
    already been committed, in `tb_serv_sci.sv`, `tb_picorv32_sci.sv` and
    `tb_fazyrv_sci.sv`. A test that mutates tracked source is a test that can
    undo work and report success.

    Symlink what generate() and run() READ; give them a real, empty tb/sci to
    write. run() invokes run.sh with cwd=repo_root and the script uses
    repo-relative paths, so the symlinks have to keep those resolvable.
    """
    root = tmp_path / "repo"
    root.mkdir()
    for rel in ("hw", "sw", "scripts", "util", "configs"):
        src = REPO_ROOT / rel
        if src.exists():
            (root / rel).symlink_to(src, target_is_directory=True)
    (root / "tb").mkdir()
    (root / "tb" / "mosaic").symlink_to(REPO_ROOT / "tb" / "mosaic",
                                       target_is_directory=True)
    # every generated run.sh sources the shared toolchain check
    (root / "tb" / "tools.sh").symlink_to(REPO_ROOT / "tb" / "tools.sh")
    (root / "tb" / "sci").mkdir()
    # run.sh points verilator at build/<obj>; verilator will not create the
    # parent, so a staged root without build/ fails with "Can't write file".
    (root / "build").mkdir()
    return root


@pytest.mark.parametrize(
    "core", ["../escape", "foo/bar", "/tmp/escape", ".", "..", "bad-core"]
)
def test_tb_smith_rejects_noncanonical_core_before_path_io(tmp_path, core):
    ts = TbSmith(tmp_path)
    assert not ts.generate(core).ok
    assert not ts.run(core).ok
    assert not ts.wake_demo(core, execute=False).ok


def test_generate_serv_artifacts(staged_root):
    ts = TbSmith(staged_root)
    result = ts.generate("serv")
    assert result.ok, result.errors
    d = pathlib.Path(result.details["dir"])
    assert (d / "tb_serv_sci.sv").exists()
    assert (d / "run.sh").exists()
    assert (d / "deps.f").exists()
    run_sh = (d / "run.sh").read_text()
    # the pinned-Verilator check and the SHARED memory model must be present
    assert 'source "$REPO/tb/tools.sh"' in run_sh
    assert "mosaic_need_verilator" in run_sh
    assert "/mnt/" not in run_sh
    assert "tb/mosaic/tb_obi_mem.sv" in run_sh
    tb = (d / "tb_serv_sci.sv").read_text()
    assert "serv_sci dut" in tb
    assert "TB PASS" in tb and "TB FAIL" in tb
    # serv is unified: single memory
    assert "u_mem (" in tb and "u_dmem" not in tb


def test_generate_split_core(staged_root):
    ts = TbSmith(staged_root)
    result = ts.generate("fazyrv")
    assert result.ok
    tb = (pathlib.Path(result.details["dir"]) / "tb_fazyrv_sci.sv").read_text()
    assert "u_imem" in tb and "u_dmem" in tb


def test_generate_unknown_wrapper_fails(staged_root):
    # Staged too, even though it refuses before writing: an absolute rule in
    # the guard below is worth more than an exception list that has to be
    # maintained correctly forever.
    ts = TbSmith(staged_root)
    result = ts.generate("nonexistentcore")
    assert not result.ok
    assert "wrapper-smith scaffold first" in result.summary


def test_run_marker_parsing(monkeypatch, staged_root):
    ts = TbSmith(staged_root)

    class _P:
        returncode = 0
        stdout = "TB PASS instr_reqs=3 data_reqs=4 cycles=250"
        stderr = ""

    import harness.skills.tb_smith as mod

    monkeypatch.setattr(mod, "run_cmd", lambda *a, **k: _P())
    assert ts.generate("serv").ok          # run() needs the script to exist
    result = ts.run("serv")
    assert result.ok
    assert result.details["metrics"] == {
        "pass": True,
        "reason": None,
        "instr_reqs": 3,
        "data_reqs": 4,
        "cycles": 250,
    }


def test_run_fail_reason(monkeypatch, staged_root):
    ts = TbSmith(staged_root)

    class _P:
        returncode = 0
        stdout = (
            "TB FAIL reason=dormancy_bus_activity instr_reqs=9 data_reqs=0 cycles=205"
        )
        stderr = ""

    import harness.skills.tb_smith as mod

    monkeypatch.setattr(mod, "run_cmd", lambda *a, **k: _P())
    assert ts.generate("serv").ok
    result = ts.run("serv")
    assert not result.ok
    assert "dormancy_bus_activity" in result.summary


def _pinned_verilator() -> bool:
    """tb/tools.sh refuses any other Verilator, so a run needs THIS one."""
    import os
    from harness.toolchain import check_tools
    return check_tools(os.environ)[0]["ok"]


@pytest.mark.slow
@pytest.mark.skipif(not _pinned_verilator(),
                    reason="pinned Verilator not available (nix develop .#sim)")
def test_real_run_picorv32(staged_root):
    ts = TbSmith(staged_root)
    assert ts.generate("picorv32").ok
    result = ts.run("picorv32")
    assert result.ok, result.details.get("stderr_tail", "")
    assert result.details["metrics"]["pass"] is True
    assert result.details["metrics"]["data_reqs"] > 0


def test_generating_does_not_touch_the_tracked_benches(staged_root):
    """The regression that motivated `staged_root`.

    Generating into a staged root must leave the real tb/sci/ alone. Checked
    by content, not mtime: the failure mode was a silent overwrite with
    ALMOST the same text, which an mtime check would catch only by luck and a
    content check catches always.
    """
    tracked = sorted((REPO_ROOT / "tb" / "sci").glob("*/tb_*_sci.sv"))
    assert tracked, "no tracked SCI benches found — the guard would be vacuous"
    before = {f: f.read_bytes() for f in tracked}

    ts = TbSmith(staged_root)
    for core in ("serv", "fazyrv", "picorv32"):
        assert ts.generate(core).ok

    changed = [f.name for f, b in before.items() if f.read_bytes() != b]
    assert not changed, f"tb-smith rewrote tracked benches: {changed}"
    # ...and it really did write, somewhere else.
    assert (staged_root / "tb/sci/serv/tb_serv_sci.sv").is_file()


def test_no_test_here_points_a_writing_tb_smith_at_the_real_repo():
    """Structural, because the content guard above only covers its own calls.

    `generate()` writes into its repo_root. Any test that constructs
    `TbSmith(REPO_ROOT)` and generates will rewrite tracked benches again, and
    the damage is silent -- the suite passes, the working tree quietly moves.
    Read-only uses are fine; this only forbids pairing the real root with a
    write.
    """
    src = pathlib.Path(__file__).read_text()
    # Drop this test's own body so its mention of the pattern is not a hit.
    src = src.split("def test_no_test_here_points_a_writing_tb_smith")[0]
    offenders = []
    for block in src.split("\ndef ")[1:]:
        name = block.split("(")[0]
        if "TbSmith(REPO_ROOT)" in block and ".generate(" in block:
            offenders.append(name)
    assert not offenders, (
        f"these tests generate into the real repo root: {offenders}. Use the "
        "`staged_root` fixture -- on 2026-09-08 this pattern reverted a "
        "committed async-reset fix in three tracked benches, and the suite "
        "stayed green while doing it")
