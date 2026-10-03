"""The typed CLI surface an agent drives, and the framing it is driven with.

WHY THIS FILE EXISTS
--------------------
`demo/03_blocka_from_prompt.sh` step 6 hands a real model the Block A request
and requires it to reach the frozen tapeout config through `config-author
generate` flags -- with `soc-from-prompt` (the deterministic grammar)
explicitly off the table.

That demo is only meaningful if two things hold, and neither is obvious:

  1. The typed CLI can EXPRESS the tapeout design at all. Until this surface
     existed it could not: there were no flags for the eight platform knobs or
     for per-core with_csr/compressed/boot_addr, so the only route to Block A
     was `--preset blocka` -- i.e. typing a preset name, which demonstrates
     nothing about translation. A regression here would quietly turn step 6
     back into that.
  2. The demo drives the model with the SAME framing `mosaic agent` sends.
     A demo that invented its own prompt would be evidence about the demo.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
FROZEN = REPO / "configs" / "mosaic_tapeout_ultra.yaml"
DEMO = REPO / "demo" / "03_blocka_from_prompt.sh"

# The Block A design as typed CLI arguments -- the exact shape step 6 asks a
# model to produce. Kept here as data so a failure points at the flag that
# stopped working rather than at a shell script.
BLOCKA_ARGS = [
    "--core", "serv:1:titan:isa=rv32ic,with_csr=1,compressed=1",
    "--core", "serv:1:atlas:isa=rv32i,with_csr=0,boot_addr=0x40010000",
    "--sram", "0", "--boot-rom", "1", "--scratchpad-bytes", "128",
    "--dma", "none", "--tdu", "--mode", "dynamic", "--peripheral", "uart",
    "--platform", "debug=false,plic=false,spi_mode=xip_only",
    "--platform", "multicore_timer=false,gpio_ao=false",
    "--platform", "ao_rv_timer=false,ao_fast_intr=false",
    # Design intent, not a peripheral: physical-intent harden derives
    # CLOCK_PERIOD from this. Added when the frozen config gained
    # soc.objectives.target_clock_mhz -- the typed surface has to be able to
    # reach every field of the config it claims to reproduce, or the claim
    # quietly narrows to "every field except the ones we cannot express".
    "--target-clock-mhz", "20",
    # Same principle again, 2026-09-10: the frozen config gained
    # objectives.repair_margin_pct when Block A was pinned to margin 32 while
    # B and C ship at 45. Without a flag for it the typed surface could no
    # longer reach the config it claims to reproduce.
    "--repair-margin-pct", "32",
]


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "harness", "config-author", "generate", *args],
        cwd=str(REPO), capture_output=True, text=True,
    )


@pytest.fixture
def out_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "agent_probe.yaml"


def test_typed_cli_reaches_the_frozen_tapeout_config(out_path):
    """The headline: flags alone, no grammar and no preset, land on Block A."""
    result = run_cli("--name", "agent_probe", "--target", "tapeout",
                     "--output", str(out_path), *BLOCKA_ARGS)
    assert result.returncode == 0, result.stdout + result.stderr

    got = yaml.safe_load(out_path.read_text())["soc"]
    frozen = yaml.safe_load(FROZEN.read_text())["soc"]
    got.pop("name", None)
    frozen.pop("name", None)
    differing = {
        key: (got.get(key, "<absent>"), frozen.get(key, "<absent>"))
        for key in set(got) | set(frozen)
        if got.get(key, "<absent>") != frozen.get(key, "<absent>")
    }
    assert not differing, f"typed CLI diverged from the frozen config: {differing}"


def test_boot_addr_is_an_int_not_the_string_the_user_typed(out_path):
    """`boot_addr=0x40010000` must land as 1073807360.

    Left as a string it validates, generates, and compares UNEQUAL to an
    otherwise identical config -- a difference that looks real and is not.
    (Uses the Block A memory profile: 0x40010000 is the flash XIP window, and
    the validator correctly rejects it against a default 32-KiB SRAM part.)
    """
    result = run_cli("--name", "p", "--output", str(out_path), *BLOCKA_ARGS)
    assert result.returncode == 0, result.stdout + result.stderr
    worker = yaml.safe_load(out_path.read_text())["soc"]["cores"][1]
    assert worker["boot_addr"] == 0x40010000
    assert isinstance(worker["boot_addr"], int)


def test_platform_knobs_are_absent_unless_asked_for(out_path):
    """Omitted knobs keep generator defaults instead of a wall of `true`s."""
    run_cli("--name", "p", "--output", str(out_path),
            "--core", "serv:1:titan", "--platform", "debug=false")
    soc = yaml.safe_load(out_path.read_text())["soc"]
    assert soc["debug"] is False
    for untouched in ("plic", "gpio_ao", "ao_rv_timer", "ao_fast_intr"):
        assert untouched not in soc


def test_an_unknown_platform_key_is_refused(out_path):
    """A typo must not become a silently different SoC."""
    result = run_cli("--name", "p", "--output", str(out_path),
                     "--core", "serv:1:titan",
                     "--platform", "no_such_knob=false")
    assert result.returncode != 0
    assert "unknown platform key" in (result.stdout + result.stderr)


def test_malformed_core_extras_are_refused_not_dropped(out_path):
    result = run_cli("--name", "p", "--output", str(out_path),
                     "--core", "serv:1:titan:with_csr")
    assert result.returncode != 0
    assert "expected key=value" in (result.stdout + result.stderr)


def test_demo_uses_the_harness_framing_rather_than_its_own():
    """Step 6 must import the prompt, not retype it."""
    text = DEMO.read_text()
    assert "external_agent_prompt" in text, (
        "demo/03 step 6 no longer imports the harness framing; a hand-written "
        "prompt makes the step evidence about the demo, not about mosaic"
    )
    assert "soc-from-prompt" in text, (
        "step 6 must still tell the model the grammar is off the table"
    )


def test_external_agent_prompt_covers_both_harnesses():
    sys.path.insert(0, str(REPO))
    from harness.__main__ import external_agent_prompt

    request = "an SoC with two serv cores"
    claude = external_agent_prompt("claude", request)
    omp = external_agent_prompt("omp", request)

    for framing in (claude, omp):
        assert request in framing
        assert ".claude/skills" in framing
        assert "without deterministic evidence" in framing
    # Both enforced harnesses get the gated MCP server, never a shell tool.
    assert "MCP" in omp and "request_scope" in omp

    # Claude now has two, and the framing must match what it was actually
    # given. `mosaic agent --driver claude` supplies the gated MCP server
    # and disallows Bash, so the default framing must not tell it to shell
    # out -- that instruction described the bypass Phase 4 closed.
    assert "MCP" in claude and "request_scope" in claude
    assert "python3 -m harness" not in claude

    # The demo supplies a scoped Bash and no MCP server, so it asks for the
    # CLI framing explicitly.
    cli = external_agent_prompt("claude", request, surface="cli")
    assert "python3 -m harness" in cli
    assert "MCP" not in cli

    with pytest.raises(ValueError):
        external_agent_prompt("api", request)
    with pytest.raises(ValueError):
        external_agent_prompt("claude", request, surface="telepathy")


def test_every_demo_is_executable_as_documented():
    """demo/README.md invokes these by path; a lost +x makes that a hard error.

    01 and 02 were both mode 100644 while the README said `./demo/01_...`, so
    the two documented walkthroughs failed with "Permission denied" for anyone
    who followed it. Nothing caught that -- a demo nobody runs in CI rots
    quietly.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-s", "demo/"],
        cwd=str(REPO), capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    not_executable = [
        line.split("\t")[-1] for line in listing
        if line.split()[0] != "100755" and line.endswith(".sh")
    ]
    assert not not_executable, (
        f"demo scripts not executable in the index: {not_executable}"
    )


def test_demos_do_not_write_into_tracked_paths():
    """A demo run must leave `git status` clean.

    demo/01 writes configs/<name>.yaml through the normal config-author path;
    that file is generated, so it is gitignored rather than tracked. Tracking
    it meant every run dirtied the tree and the committed copy drifted from
    what the generator actually emits.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "configs/prompted_demo.yaml", "configs/agent_probe.yaml"],
        cwd=str(REPO), capture_output=True, text=True, check=True,
    ).stdout.split()
    assert not tracked, f"demo output is tracked again: {tracked}"

    ignored = subprocess.run(
        ["git", "check-ignore", "configs/prompted_demo.yaml",
         "configs/agent_probe.yaml"],
        cwd=str(REPO), capture_output=True, text=True,
    ).stdout.split()
    assert len(ignored) == 2, f"demo output not gitignored: {ignored}"


def test_demo_model_step_cannot_fail_the_deterministic_demo():
    """A model run is evidence; only steps 2-5 govern the exit status.

    Without this, a flaky model turns a CI-able regression pin into a coin
    flip -- and the temptation would be to weaken step 3 instead.
    """
    text = DEMO.read_text()
    exit_block = text.split('if [ "$RC" -eq 0 ]')[-1]
    assert "$AGENT_STATUS" in exit_block
    assert "exit 1" in exit_block
    # The only hard failure path is step 3's RC, not the agent's status.
    assert 'AGENT_STATUS="diverged"' in text
    assert 'exit 1' not in text.split('AGENT_STATUS="diverged"')[1].split("esac")[0]


def test_wake_demo_config_is_valid_for_every_registered_core(tmp_path):
    """`tb-smith wake-demo <core>` must produce a config that VALIDATES.

    hazard3 had no CORE_DEFAULTS entry, so its wake-demo config was emitted as
    rv32i and rejected ("valid: ['rv32imc']") -- the documented Phase-2 route
    for the core the wrapper mechanism was built to demonstrate could not run.
    Nothing caught it because the shipped configs are tested, and this one is
    generated on demand. Any future core added to the registry without a
    CORE_DEFAULTS entry would repeat it, so assert over the registry itself.
    """
    sys.path.insert(0, str(REPO))
    from harness.skills.config_author import ConfigAuthor
    from util.mosaic_gen.core_registry import CORE_SPECS, VALID_CORE_IPS

    author = ConfigAuthor()
    broken = {}
    for core in sorted(VALID_CORE_IPS):
        result = author.wake_demo_config(
            core, output_path=tmp_path / f"{core}.yaml")
        if not result.ok:
            broken[core] = result.errors
            continue
        emitted = yaml.safe_load((tmp_path / f"{core}.yaml").read_text())["soc"]
        worker_isa = emitted["cores"][1]["isa"]
        assert worker_isa in CORE_SPECS[core].isas, (
            f"{core}: wake demo emits {worker_isa}, registry allows "
            f"{sorted(CORE_SPECS[core].isas)}"
        )
    assert not broken, f"wake_demo_config invalid for: {broken}"
