"""The one escape hatch in the signoff runner must not be able to skip a check.

run_signoff.sh exists because every other experimental run passed a list of
--skips to shorten the loop, which is how "0 routing DRC" got mistaken for "DRC
clean". Its guarantee is negative: there is no way to turn a check off.

MOSAIC_RESOURCE_CONFIG puts a crack in that. It appends keys to the resolved
config so a memory-constrained host can cap thread counts -- KLayout DRC on
mosaic_block_c was SIGKILLed twice by the OOM killer at the deck's default
thread count. The previous fix was to hand-append a key to .resolved_<tag>.yaml,
which works and makes the run unreproducible, since that file is regenerated.

The hatch is safe only because of one restriction: it accepts nothing but
*_THREADS keys. A thread count changes how long a check takes, never whether it
passes. These tests hold that restriction in place by running the runner's OWN
patterns -- extracted from the script, not copied here -- over inputs that a
future edit might let through.
"""

import re
import shlex
import subprocess

import pytest
import yaml

from harness.core import REPO_ROOT

RUNNER = REPO_ROOT / "flow/librelane/experimental/run_signoff.sh"
PROFILE = REPO_ROOT / "flow/librelane/experimental/resources_lowmem.yaml"


def runner_patterns() -> tuple[str, str]:
    """The two grep patterns the script actually validates with.

    Extracted rather than duplicated: a test that restates the regex passes
    happily while the script it describes has been loosened.
    """
    text = RUNNER.read_text()
    match = re.search(
        r"grep -vE '([^']+)' \"\$RES\" \| grep -vE '([^']+)'", text)
    assert match, "the MOSAIC_RESOURCE_CONFIG validator is gone or was rewritten"
    return match.group(1), match.group(2)


def rejected_lines(content: str) -> list[str]:
    """Run the script's own pipeline over `content`; returns offending lines."""
    skip, accept = runner_patterns()
    # shlex.quote, not repr: repr escapes the backslash in `\s`, so the shell
    # would receive a literal `\\s` and the blank-line filter would match
    # nothing -- a test that looks stricter than the script it is checking.
    result = subprocess.run(
        f"grep -vE {shlex.quote(skip)} | grep -vE {shlex.quote(accept)}",
        shell=True, input=content, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


# ── what the hatch is for ────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    "KLAYOUT_DRC_THREADS: 2",
    "DRT_THREADS: 4",
    "STA_THREADS: 1",
    "KLAYOUT_XOR_THREADS: 2  # inline comments are fine",
])
def test_a_thread_count_is_accepted(line):
    assert not rejected_lines(line), line


def test_comments_and_blank_lines_are_accepted():
    assert not rejected_lines("# why this profile exists\n\n   \nDRT_THREADS: 2\n")


def test_the_committed_low_memory_profile_passes_its_own_validator():
    """The profile in the repo must be one the runner will actually take."""
    assert not rejected_lines(PROFILE.read_text())


# ── what it must never become ────────────────────────────────────────

@pytest.mark.parametrize("line", [
    # Turning a signoff check off is the entire thing this must not permit.
    "RUN_KLAYOUT_DRC: false",
    "RUN_MAGIC_DRC: false",
    "ERROR_ON_MAGIC_DRC: false",
    "ERROR_ON_KLAYOUT_DRC: false",
    # Nor may it substitute or null a step, which the config gate already bans.
    "OpenROAD.CheckAntennas: null",
    "substituting_steps: true",
    # Nor quietly move a result-bearing knob under cover of "resources".
    "DRT_ANTENNA_REPAIR_ITERS: 0",
    "GRT_DESIGN_REPAIR_MAX_SLEW_PCT: 10",
    "MAX_TRANSITION_CONSTRAINT: 8",
    # Near-misses on the name. THREADS has to be the whole final word, and the
    # value has to be a number -- "unlimited" is not a thread count.
    "KLAYOUT_DRC_THREADS_EXTRA: 2",
    "THREADS_KLAYOUT: 2",
    "KLAYOUT_DRC_THREADS: unlimited",
    "klayout_drc_threads: 2",
])
def test_anything_that_is_not_a_thread_count_is_rejected(line):
    assert rejected_lines(line) == [line], line


# ── the signoff SDC, whose COMMENTS are load-bearing ─────────────────

SIGNOFF_SDC = REPO_ROOT / "flow/librelane/experimental/signoff_library_limits.sdc"
TEMPLATE = REPO_ROOT / "flow/librelane/signoff_template.yaml"


def sdc_text() -> str:
    return SIGNOFF_SDC.read_text()


def sdc_code() -> str:
    """Executable lines only."""
    return "\n".join(l for l in sdc_text().splitlines()
                     if not l.lstrip().startswith("#"))


def test_the_signoff_sdc_keeps_the_propagated_clock_markers():
    """openroad/common/io.tcl greps THIS file, not the file it sources.

        if { ![string_in_file $::env(_SDC_IN) "set_propagated_clock"]
             && ![string_in_file $::env(_SDC_IN) "unset_propagated_clock"] } { ... }

    base.sdc handles clock propagation itself. Our file only sources it, so if
    neither name appears here io.tcl applies propagation a SECOND time. The
    names live in a comment, which means an editor tidying the comment would
    silently change timing. Hence this test.
    """
    text = sdc_text()
    assert "set_propagated_clock" in text
    assert "unset_propagated_clock" in text


def test_the_signoff_sdc_does_not_trip_the_driving_cell_rewrite():
    """The mirror image: a literal that must NOT appear.

        if { [env_var_used $::env(_SDC_IN) SYNTH_DRIVING_CELL_PIN] == 1 } { ... }

    and `env_var_used` looks for the literal `$::env(NAME)`. base.sdc splits
    SYNTH_DRIVING_CELL itself, so tripping this rewrites it twice. The first
    draft of the SDC tripped exactly this by naming the variable inside the
    comment that warned about it.
    """
    forbidden = "$" + "::env(SYNTH_DRIVING_CELL_PIN)"
    assert forbidden not in sdc_text(), (
        "the literal env reference must not appear, even in a comment")


def test_the_signoff_sdc_removes_the_blanket_transition_and_nothing_else():
    code = sdc_code()
    assert "set_max_transition" not in code, (
        "the whole point is that signoff does NOT apply a blanket limit")
    assert "unset -nocomplain ::env(MAX_TRANSITION_CONSTRAINT)" in code
    # Sourced, not forked: a copy of base.sdc would drift from the SDC PnR uses,
    # which is the same class of bug this file exists to fix.
    assert "source $_mosaic_base_sdc" in code
    for constraint in ("set_max_fanout", "set_input_delay", "set_driving_cell",
                       "set_clock_uncertainty", "set_timing_derate"):
        assert constraint not in code, (
            f"{constraint} must come from base.sdc, not be restated here")


def test_pnr_keeps_the_transition_target_while_signoff_does_not():
    """The split is the design. Losing either half breaks it.

    MAX_TRANSITION_CONSTRAINT: null was measured and is WRONG -- PnR then stops
    buffering toward 4.0 and the design degrades past the LIBRARY's own limits
    (runs/blocka_libtran: 10 max-slew at 7.1998 ns against a 7.0 ns pin rating,
    plus 5 max-capacitance violations). The 4.0 target is what keeps the design
    inside its qualified range; it was only ever wrong as a SIGNOFF limit.
    """
    template = yaml.safe_load(TEMPLATE.read_text())
    assert template["MAX_TRANSITION_CONSTRAINT"] == 4, (
        "PnR must keep a transition target; null was measured and degrades the design")
    # `dir::` resolves against the resolved config's directory: experimental/
    # for experiments, integration/ for the D15 tapeout run. One path must
    # reach the one copy of the file from both.
    sdc = template["SIGNOFF_SDC_FILE"]
    assert sdc.startswith("dir::")
    for work in ("experimental", "integration"):
        assert (REPO_ROOT / "flow/librelane" / work / sdc[len("dir::"):]).resolve() \
            == SIGNOFF_SDC.resolve(), work
    assert "PNR_SDC_FILE" not in template, (
        "PnR must keep using LibreLane's base.sdc unchanged")


def test_one_bad_key_rejects_the_whole_file():
    """Not filtered out -- rejected.

    Dropping the offending line and running anyway is how a run ends up not
    being the run someone thinks they configured.
    """
    content = "KLAYOUT_DRC_THREADS: 2\nRUN_KLAYOUT_DRC: false\nDRT_THREADS: 4\n"
    assert rejected_lines(content) == ["RUN_KLAYOUT_DRC: false"]
    assert "exit 3" in RUNNER.read_text().split("may only set")[1][:400], (
        "a rejected resource config must abort the run, not be filtered")
