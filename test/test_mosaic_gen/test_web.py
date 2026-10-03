"""The web viewer and the run-progress reader behind it.

`progress` is tested on verbatim LibreLane flow.log lines, including the
unicode ellipsis and the rich `[repr.filename]` markup, because a synthetic
log would not have either. A failed run leaves no marker in flow.log, so the
reader must call a quiet, unfinished run "stopped", never "complete".

The site is built once from the real tree and checked the way a reader would
use it: every page parses, every internal link resolves, nothing is fetched
from outside, and the runs it calls accepted are exactly the runs
`physical-intent ledger` accepts.
"""

import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from harness.physical.progress import run_progress  # noqa: E402

FLOW_LOG = """Starting…
Running 'Verilator.Lint' at 'experimental/runs/opt_blockc_density_03/01-verilator-lint'…
Logging subprocess to [repr.filename]'experimental/runs/opt_blockc_density_03/01-verilator-lint/verilator-lint.log'[/repr.filename]…
Running 'Checker.LintTimingConstructs' at 'experimental/runs/opt_blockc_density_03/02-checker-linttimingconstructs'…
"""
FLOW_END = """No max cap violations found
Running 'Misc.ReportManufacturability' at 'experimental/runs/opt_blockc_density_03/77-misc-reportmanufacturability'…
Saving views to '/x/flow/librelane/experimental/runs/opt_blockc_density_03/final'…
Flow complete.
"""


def _run(tmp_path, text, name="r"):
    run = tmp_path / "runs" / name
    run.mkdir(parents=True)
    (run / "flow.log").write_text(text)
    return run


def test_progress_reads_a_finished_run(tmp_path):
    p = run_progress(_run(tmp_path, FLOW_LOG + FLOW_END))
    assert p["state"] == "complete"
    assert (p["step"], p["step_number"]) == ("Misc.ReportManufacturability", 77)


def test_an_unfinished_run_is_running_then_stopped_never_complete(tmp_path):
    run = _run(tmp_path, FLOW_LOG)
    assert run_progress(run)["state"] == "running"
    later = run_progress(run, now=time.time() + 3 * 3600)
    assert later["state"] == "stopped" and "failed or killed" in later["reason"]
    assert later["step"] == "Checker.LintTimingConstructs"


def test_activity_in_the_current_step_counts_not_only_flow_log(tmp_path):
    """Detailed routing writes its own log for hours and nothing to flow.log."""
    run = _run(tmp_path, FLOW_LOG)
    old = time.time() - 5 * 3600
    os.utime(run / "flow.log", (old, old))
    step = run / "02-checker-linttimingconstructs"
    step.mkdir()
    (step / "x.log").write_text("busy")
    assert run_progress(run)["state"] == "running"


def test_plateau_marker_and_missing_log(tmp_path):
    run = _run(tmp_path, FLOW_LOG)
    (run / ".plateau_abort").write_text("")
    assert run_progress(run)["state"] == "aborted"
    assert run_progress(tmp_path / "nope")["state"] == "not_started"


def test_expected_steps_come_from_a_complete_sibling(tmp_path):
    _run(tmp_path, FLOW_LOG + FLOW_END, "done")
    live = _run(tmp_path, FLOW_LOG, "live")
    assert run_progress(live)["expected_steps"] == 3


# ── the site ──────────────────────────────────────────────────────────


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("href", "src") and v:
                self.refs.append(v)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    from harness.web import build_site
    out = tmp_path_factory.mktemp("site")
    result = build_site(out, repo_root=REPO)
    assert result.ok, result.errors
    return out, result


def test_every_page_parses_links_resolve_and_nothing_is_fetched(site):
    out, result = site
    pages = list(out.rglob("*.html"))
    assert len(pages) >= 1 + result.details["designs"] + result.details["runs"]
    for page in pages:
        text = page.read_text()
        parser = _Links()
        parser.feed(text)
        for ref in parser.refs:
            assert not re.match(r"^[a-z]+:", ref), (page.name, ref)  # no scheme: nothing external
            target = (page.parent / ref.split("#")[0]).resolve()
            assert ref.startswith("#") or target.is_file(), (page.name, ref)
        urls = set(re.findall(r"https?://[^\"'\s<]+", text))
        assert urls <= {"http://www.w3.org/2000/svg"}, (page.name, urls)


def test_accepted_runs_are_exactly_what_ledger_accepts(site):
    from harness.physical.ppa import ledger
    from harness.web import RUN_ROOTS
    out, _ = site
    index = (out / "index.html").read_text()
    shown = set(re.findall(r'<a href="run/([^"]+)\.html">[^<]*</a></td><td>[^<]*</td><td>'
                           r'<span class="chip ok">accepted', index))
    expected = set()
    for root in RUN_ROOTS:
        if (REPO / root).is_dir():
            runs, _ = ledger(REPO / root, repo_root=REPO)
            # The viewer lists a run only when its flow.log is present; a
            # directory holding just the tracked metrics is evidence, not a run.
            expected |= {Path(p.run).name for p in runs if p.accepted
                         and (REPO / root / Path(p.run).name / "flow.log").is_file()}
    assert shown == expected


# ── the server ────────────────────────────────────────────────────────


def test_server_is_read_only_loopback_and_confined(site):
    from http.server import ThreadingHTTPServer
    from harness.web import make_handler, serve, run_dirs
    out, _ = site
    with pytest.raises(ValueError):
        serve(out, host="0.0.0.0", port=0)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(out, REPO))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def status(path, data=None):
        try:
            return urllib.request.urlopen(base + path, data=data, timeout=10).status
        except urllib.error.HTTPError as err:
            return err.code

    try:
        assert status("/index.html") == 200
        assert status("/api/progress/..%2f..%2fetc") == 404
        assert status("/api/progress/no_such_run") == 404
        assert status("/../../../etc/passwd") == 404
        assert status("/index.html", data=b"x") == 405
        runs = run_dirs(REPO)
        if runs:
            assert status(f"/api/progress/{runs[0].name}") == 200
    finally:
        httpd.shutdown()
