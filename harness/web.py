"""oh-my-soc web: a viewer for designs, runs and evidence.

`build_site` writes static, self-contained pages (inline CSS/SVG, no external
URL) from what is on disk: every config's SoCView and diagrams, every
hardening run's gate verdicts and evidence, and each technology's port status.
`serve` puts the same site on 127.0.0.1 and adds one live endpoint,
`/api/progress/<tag>`, which run pages poll while a run is in flight.

It is a viewer, not a control surface: generating and hardening stay behind the
harness gates (CLI, agents, MCP). Nothing here writes outside the site dir.

Every number is read through the function the gates themselves use --
ppa.evaluate/ledger, report.signoff_summary, evidence.power, progress -- so the
page cannot disagree with `physical-intent ppa`.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from .core import REPO_ROOT, SkillResult

RUN_ROOTS = ("flow/librelane/experimental/runs", "flow/librelane/integration/runs")
_BUNDLE = re.compile(r"build/mosaic/([A-Za-z0-9_.-]+-[0-9a-f]{12})/")
_TAG = re.compile(r"^[A-Za-z0-9_.-]+$")


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _num(v: Any, fmt: str = "{:.3g}") -> str:
    return "–" if v is None else fmt.format(v)


# ── data ─────────────────────────────────────────────────────────────


def run_dirs(repo_root: Path) -> List[Path]:
    out = []
    for root in RUN_ROOTS:
        base = repo_root / root
        if base.is_dir():
            out += sorted(p for p in base.iterdir() if (p / "flow.log").is_file())
    return out


def run_config(run_dir: Path, repo_root: Path,
               by_name: Optional[Dict[str, Path]] = None) -> Tuple[Optional[Path], str]:
    """(the SoC config a run hardened, how that was established). Runs do not
    record their config; they record the bundle their Verilog came from, and
    the bundle's manifest names the config. Disk pruning deletes bundles, so
    fall back to the SoC name the bundle key starts with -- labelled, because
    today's config may no longer be the one that was hardened."""
    try:
        key = _BUNDLE.search((run_dir / "resolved.json").read_text())
    except OSError:
        return None, "no resolved.json"
    if key is None:
        return None, "resolved.json names no generated bundle"
    manifest = repo_root / "build/mosaic" / key.group(1) / "manifest.json"
    if manifest.is_file():
        rec = json.loads(manifest.read_text())["inputs"]["mosaic_config"]
        path = Path(rec["path"])
        path = path if path.is_absolute() else repo_root / path
        if path.is_file():
            return path, f"bundle {key.group(1)}"
    name = key.group(1)[:-13]  # <soc_name>-<12 hex>
    if by_name and name in by_name:
        return by_name[name], (f"inferred from bundle name {key.group(1)} (bundle deleted; "
                               "the config may have changed since this run)")
    return None, f"bundle {key.group(1)} deleted and no config is named {name}"


def _slug(path: Path, repo_root: Path) -> str:
    rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else Path(path.name)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(rel.with_suffix("")))


def _pdk_panel(repo_root: Path) -> List[Dict[str, Any]]:
    from .physical.floorplan import CALIBRATED_PDKS
    from .physical.technology import TECHNOLOGIES
    from .skills.pdk_port import survey
    from util.xheep_gen.core_registry import TAPEOUT_PDK
    rows = []
    for tech in TECHNOLOGIES:
        rep = survey(tech, repo_root=repo_root)
        rows.append({"technology": tech, "pdk": rep.pdk, "ready": rep.ready,
                     "missing": [r.key for r in rep.missing],
                     "silent": [r.key for r in rep.silent_gaps],
                     "calibrated": rep.pdk in CALIBRATED_PDKS,
                     "tapeout": rep.pdk.startswith(TAPEOUT_PDK)})
    return rows


def _run_record(run_dir: Path, repo_root: Path,
                by_name: Dict[str, Path]) -> Dict[str, Any]:
    from .evidence.power import power_reports_for_run
    from .physical.ppa import evaluate
    from .physical.progress import run_progress
    from .physical.report import signoff_summary
    ppa, errors = evaluate(run_dir, repo_root=repo_root)
    summary, _ = signoff_summary(run_dir, repo_root=repo_root)
    power = {}
    for basis, step in (("default activity", run_dir / "56-openroad-stapostpnr"),
                        ("workload", run_dir / "91-mosaic-workloadpower" / "workload"),
                        ("default (3 corners)", run_dir / "91-mosaic-workloadpower" / "default")):
        if step.is_dir():
            reps = power_reports_for_run(step)
            if reps:
                power[basis] = {c: (r.total.total if r.total else None) for c, r in sorted(reps.items())}
    render = sorted((run_dir / "final" / "render").glob("*.png"))
    cfg, cfg_basis = run_config(run_dir, repo_root, by_name)
    return {"tag": run_dir.name, "root": str(run_dir.parent.relative_to(repo_root)),
            "dir": run_dir, "ppa": ppa.as_dict() if ppa else None, "ppa_errors": errors,
            "summary": summary, "power": power, "progress": run_progress(run_dir),
            "render": render[0] if render else None, "config": cfg,
            "config_basis": cfg_basis,
            "finished": (run_dir / "final" / "metrics.json").stat().st_mtime
            if (run_dir / "final" / "metrics.json").is_file() else 0.0}


def collect(repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    from .socview import build_view
    configs = sorted((repo_root / "configs").glob("mosaic*.yaml"))
    designs = []
    for cfg in configs:
        try:
            view, error = build_view(cfg, repo_root=repo_root), None
        except (Exception, SystemExit) as exc:  # the generator's verdict, shown
            view, error = None, " ".join(str(exc).split())[:300]
        designs.append({"config": cfg, "slug": _slug(cfg, repo_root), "view": view, "error": error})
    by_name = {d["view"]["name"]: d["config"] for d in designs if d["view"]}
    runs = [_run_record(r, repo_root, by_name) for r in run_dirs(repo_root)]
    by_config: Dict[Path, List[Dict[str, Any]]] = {}
    for r in runs:
        if r["config"]:
            by_config.setdefault(r["config"].resolve(), []).append(r)
    for d in designs:  # newest first, so "the run" means the latest one
        d["runs"] = sorted(by_config.get(d["config"].resolve(), []),
                           key=lambda r: r["finished"], reverse=True)
    from .evidence.waivers import load_waivers
    waivers = load_waivers(repo_root / "flow/librelane/signoff_waivers.yaml")
    return {"designs": designs, "runs": runs, "pdks": _pdk_panel(repo_root),
            "waivers": waivers}


# ── pages ────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#f2f4f3;--card:#fff;--ink:#16202a;--muted:#56626a;--line:#d3dad8;--acc:#0b6e69;
--acc-soft:#dcedeb;--ok:#2e7d32;--ok-bg:#e2f0e3;--warn:#9a5a00;--warn-bg:#faedd6;
--bad:#b42318;--bad-bg:#fbe3df;--none:#6e7a80;--none-bg:#e7eceb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--bg:#0e1316;
--card:#151c20;--ink:#e2e8ea;--muted:#95a2a8;--line:#29343b;--acc:#43b9af;--acc-soft:#12302d;
--ok:#6cc477;--ok-bg:#15301a;--warn:#e6a94e;--warn-bg:#3a2a10;--bad:#f27b6b;--bad-bg:#3e1814;
--none:#8a969c;--none-bg:#1e262b}}
:root[data-theme=dark]{color-scheme:dark;--bg:#0e1316;--card:#151c20;--ink:#e2e8ea;--muted:#95a2a8;
--line:#29343b;--acc:#43b9af;--acc-soft:#12302d;--ok:#6cc477;--ok-bg:#15301a;--warn:#e6a94e;
--warn-bg:#3a2a10;--bad:#f27b6b;--bad-bg:#3e1814;--none:#8a969c;--none-bg:#1e262b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px}
main{max-width:76rem;margin:0 auto;padding-block:20px 56px;display:grid;gap:28px}
nav{display:flex;gap:18px;align-items:baseline;border-bottom:1px solid var(--line);padding-block:14px}
nav b{font-size:1.05rem}nav a{color:var(--muted);text-decoration:none}nav a:hover,nav a:focus-visible{color:var(--acc)}
h1,h2,h3{margin:0;line-height:1.2;text-wrap:balance}h1{font-size:1.7rem}h2{font-size:1.2rem}
.meta{color:var(--muted);margin:4px 0 0}
section{display:grid;gap:12px}
a{color:var(--acc)}a:focus-visible,label:focus-within{outline:2px solid var(--acc);outline-offset:2px}
code,.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em}
.scroll{overflow-x:auto;border:1px solid var(--line);background:var(--card);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.8rem;background:var(--bg)}tr:last-child>td{border-bottom:0}
td.n{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.chip{display:inline-block;font:500 .72rem/1 "IBM Plex Mono",ui-monospace,monospace;padding:4px 7px;border-radius:3px;text-transform:uppercase;letter-spacing:.03em;white-space:nowrap}
.ok{color:var(--ok);background:var(--ok-bg)}.warn{color:var(--warn);background:var(--warn-bg)}
.bad{color:var(--bad);background:var(--bad-bg)}.none{color:var(--none);background:var(--none-bg)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:14px 16px;display:grid;gap:6px;align-content:start}
.card .k{color:var(--muted);font-size:.85rem}
.tabs{display:grid;gap:10px}.tabs input{position:absolute;opacity:0}
.tabs .bar{display:flex;gap:6px;flex-wrap:wrap}
.tabs label{padding:6px 14px;border:1px solid var(--line);border-radius:4px;cursor:pointer;background:var(--card)}
.pane{display:none;overflow:auto;border:1px solid var(--line);background:var(--card);border-radius:6px;padding:12px}
.pane svg,.pane img{max-width:100%;height:auto;display:block}
#v1:checked~.bar label[for=v1],#v2:checked~.bar label[for=v2],#v3:checked~.bar label[for=v3]{border-color:var(--acc);color:var(--acc);font-weight:600}
#v1:checked~.p1,#v2:checked~.p2,#v3:checked~.p3{display:block}
.basis{color:var(--muted);font-size:.85rem}
.spark{width:100%;max-width:520px;height:90px}
"""


def _page(title: str, body: str, depth: int, *, extra: str = "") -> str:
    up = "../" * depth
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{_e(title)}</title><style>{CSS}</style></head><body>"
            f'<nav aria-label="site"><b>MOSAIC-SoC</b><a href="{up}index.html#designs">Designs</a>'
            f'<a href="{up}index.html#runs">Runs</a><a href="{up}index.html#pdks">PDKs</a></nav>'
            f"<main>{body}</main>{extra}</body></html>")


def _table(head: List[str], rows: List[List[str]], numeric: Tuple[int, ...] = ()) -> str:
    th = "".join(f"<th scope=col>{_e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(
        f'<td class="n">{c}</td>' if i in numeric else f"<td>{c}</td>"
        for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def _verdict(r: Dict[str, Any]) -> str:
    p = r["ppa"]
    if p is None:
        state = r["progress"]["state"]
        cls = {"running": "warn", "stopped": "bad", "aborted": "bad"}.get(state, "none")
        return f'<span class="chip {cls}">{_e(state)}</span>'
    if p["accepted"]:
        return '<span class="chip ok">accepted</span>'
    failing = [g["name"] for g in p["gates"] if not g["passed"]]
    return f'<span class="chip bad">rejected</span> <span class="basis">{_e(", ".join(failing))}</span>'


def _cores_text(view: Dict[str, Any]) -> str:
    return ", ".join(f"{g['ip']}×{g['count']} {g['role']}" for g in view["cores"])


def _index(data: Dict[str, Any], stamp: str) -> str:
    pdk_cards = "".join(
        f'<div class="card"><b class="mono">{_e(p["technology"])}</b>'
        f'<div>{"<span class=\"chip ok\">ported</span>" if p["ready"] else f"<span class=\"chip bad\">{len(p[chr(109)+chr(105)+chr(115)+chr(115)+chr(105)+chr(110)+chr(103)])} missing</span>"} '
        f'{"<span class=\"chip ok\">calibrated</span>" if p["calibrated"] else "<span class=\"chip none\">uncalibrated</span>"} '
        f'{"<span class=\"chip ok\">tapeout target</span>" if p["tapeout"] else ""}</div>'
        + (f'<div class="k">missing: {_e(", ".join(p["missing"]))}</div>' if p["missing"] else "")
        + "</div>" for p in data["pdks"])
    drows = []
    for d in data["designs"]:
        v = d["view"]
        name = f'<a href="design/{_e(d["slug"])}.html">{_e(d["config"].stem)}</a>'
        if v is None:
            drows.append([name, '<span class="chip bad">rejected</span>', _e(d["error"]), "", "", "", ""])
            continue
        best = next((r for r in d["runs"] if r["ppa"] and r["ppa"]["accepted"]), None)
        drows.append([name, _e(v["bus"]["kind"]), _e(_cores_text(v)), str(v["num_harts"]),
                      _e(v["pdk"]), _e(v["target"]),
                      f'{len(d["runs"])}' + (' · <span class="chip ok">signed off</span>' if best else "")])
    rrows = []
    for r in data["runs"]:
        p = r["ppa"] or {}
        rrows.append([f'<a href="run/{_e(r["tag"])}.html">{_e(r["tag"])}</a>',
                      _e(r["root"].split("/")[-2]), _verdict(r), _e(p.get("design")),
                      _num(p.get("fmax_mhz"), "{:.2f}"), _num(p.get("die_mm2"), "{:.3f}"),
                      _num((p.get("logic_um2") or 0) / 1e6 if p.get("logic_um2") else None, "{:.3f}"),
                      _num(p.get("energy_nj"), "{:.2f}"), _e(p.get("gls") or "–")])
    wrows = [[f"<code>{_e(w.metric)}</code>", _e(w.design), str(w.accepted_max),
              _e(w.review_by)] for w in data["waivers"]]
    body = (f"<header><h1>Designs, runs and evidence</h1><p class=meta>Built {_e(stamp)} from what is on "
            f"disk. Every verdict is the one <code>physical-intent ppa</code> gives.</p></header>"
            f'<section id="pdks" aria-labelledby="h-pdk"><h2 id="h-pdk">Technologies</h2><div class="cards">{pdk_cards}</div></section>'
            f'<section id="designs" aria-labelledby="h-d"><h2 id="h-d">Designs ({len(drows)})</h2>'
            + _table(["Config", "Bus", "Cores", "Harts", "PDK", "Target", "Runs"], drows, (3,))
            + f'</section><section id="runs" aria-labelledby="h-r"><h2 id="h-r">Hardening runs ({len(rrows)})</h2>'
            + _table(["Run", "Tree", "Verdict", "Design", "fmax MHz", "Die mm²", "Logic mm²", "Energy nJ", "GLS"],
                     rrows, (4, 5, 6, 7))
            + '</section><section aria-labelledby="h-w"><h2 id="h-w">Active waivers</h2>'
            + _table(["Metric", "Design", "Accepted max", "Review by"], wrows, (2,)) + "</section>")
    return _page("MOSAIC-SoC viewer", body, 0)


def _tabs(panes: List[Tuple[str, str]]) -> str:
    inputs = "".join(f'<input type="radio" name="view" id="v{i + 1}"{" checked" if i == 0 else ""}>'
                     for i in range(len(panes)))
    labels = "".join(f'<label for="v{i + 1}">{_e(t)}</label>' for i, (t, _) in enumerate(panes))
    divs = "".join(f'<div class="pane p{i + 1}">{c}</div>' for i, (_, c) in enumerate(panes))
    return f'<div class="tabs">{inputs}<div class="bar">{labels}</div>{divs}</div>'


def _design_page(d: Dict[str, Any], repo_root: Path, assets: Path) -> str:
    from .diagram import _size, chip_svg, logical_svg, memory_rows, meta_line
    from .socview import build_view
    v = d["view"]
    title = d["config"].stem
    if v is None:
        return _page(title, f"<h1>{_e(title)}</h1><p><span class='chip bad'>rejected by the "
                            f"generator</span></p><p class=mono>{_e(d['error'])}</p>", 1)
    run = next((r for r in d["runs"] if r["ppa"] and r["ppa"]["accepted"]), d["runs"][0] if d["runs"] else None)
    chip_view, join_note = v, ""
    if run:
        try:
            chip_view = build_view(d["config"], run_dir=run["dir"], repo_root=repo_root)
        except (Exception, SystemExit) as exc:
            join_note = f" · could not join run {run['tag']}: {' '.join(str(exc).split())[:200]}"
            run = None
    panes = [("Logical", logical_svg(v)), ("Chip", chip_svg(chip_view))]
    if run and run["render"]:
        name = f"{run['tag']}.png"
        shutil.copyfile(run["render"], assets / name)
        panes.append(("Layout", f'<img src="../assets/{_e(name)}" alt="Layout of {_e(run["tag"])} '
                                f'rendered by LibreLane" loading="lazy">'))
    cores = [[_e(g["ip"]), _e(g["role"]), f"<code>{_e(g['isa'])}</code>", str(g["count"]),
              _e(", ".join(map(str, g["hart_ids"]))), _e(g["native_bus"]),
              f"<code>{_e(g['sci_module'] or 'native OBI')}</code>",
              f"<code>{_e(json.dumps(g['params']))}</code>"] for g in v["cores"]]
    mem = [[f"<code>{_e(n)}</code>", f"<code>{'–' if s is None else f'0x{s:08x}'}</code>",
            _e(_size(z)), "yes" if inc else '<span class="chip none">no</span>'] for n, s, z, inc in memory_rows(v)]
    pins = [[f"<code>{_e(p['name'])}</code>", _e(p.get("direction")), _e(p.get("side") or "–"),
             _e(p.get("cell") or ""), _e(p.get("slot") or "")] for p in chip_view["io"]["pins"]]
    runs = [[f'<a href="../run/{_e(r["tag"])}.html">{_e(r["tag"])}</a>', _verdict(r)] for r in d["runs"]]
    body = (f"<header><h1>{_e(title)}</h1><p class=meta>{_e(meta_line(v))}</p>"
            f"<p class=basis>config <code>{_e(v['config'])}</code>"
            + (f" · diagrams joined with run <code>{_e(run['tag'])}</code>" if run else _e(join_note))
            + "</p></header>" + _tabs(panes)
            + "<section><h2>Cores</h2>" + _table(["Core", "Role", "ISA", "Count", "Harts", "Native bus", "Wrapper", "Parameters"], cores, (3,))
            + "</section><section><h2>Memory map</h2>" + _table(["Region", "Start", "Size", "Instantiated"], mem)
            + f"</section><section><h2>IO</h2><p class=basis>{_e(chip_view['io']['basis'])}</p>"
            + (_table(["Pin", "Direction", "Edge", "Pad cell", "Slot"], pins) if pins else "")
            + "</section><section><h2>Runs</h2>" + (_table(["Run", "Verdict"], runs) if runs else "<p class=basis>No hardening run of this config.</p>")
            + "</section><section><h2>Where this comes from</h2><ul class=basis>"
            + "".join(f"<li>{_e(k)}: {_e(b)}</li>" for k, b in v["basis"].items())
            + f"<li>die: {_e(chip_view['die']['basis'])}</li></ul></section>")
    return _page(f"{title} · MOSAIC-SoC", body, 1)


def _sparkline(traj: List[int]) -> str:
    if len(traj) < 2:
        return ""
    w, h, pad = 520, 90, 6
    top = max(traj) or 1
    pts = " ".join(f"{pad + i * (w - 2 * pad) / (len(traj) - 1):.1f},"
                   f"{h - pad - (v / top) * (h - 2 * pad):.1f}" for i, v in enumerate(traj))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="Detailed-routing '
            f'violations per iteration, {traj[0]} to {traj[-1]}"><polyline fill="none" '
            f'stroke="currentColor" stroke-width="1.6" points="{pts}"/></svg>')


def _progress_html(p: Dict[str, Any]) -> str:
    cls = {"complete": "ok", "running": "warn", "stopped": "bad", "aborted": "bad"}.get(p["state"], "none")
    of = f" of ~{p['expected_steps']}" if p.get("expected_steps") else ""
    out = (f'<span class="chip {cls}">{_e(p["state"])}</span> <span class=basis>{_e(p.get("reason"))}'
           f' · step {_e(p.get("step_number"))}{of}: <code>{_e(p.get("step"))}</code></span>')
    rt = p.get("routing")
    if rt:
        out += (f'<p class=basis>Detailed routing: {_e(rt["state"])}, {_e(rt["reason"])}'
                + (f" · first plateau at iteration {rt['first_plateau_iteration']}"
                   if rt.get("first_plateau_iteration") is not None else "") + "</p>"
                + _sparkline(rt.get("trajectory") or []))
    return out


LIVE = """<script>
(function(){if(!/^https?:$/.test(location.protocol))return;var el=document.getElementById('progress');
var tag=el.getAttribute('data-tag');function tick(){fetch('../api/progress/'+encodeURIComponent(tag))
.then(function(r){return r.ok?r.text():null}).then(function(t){if(t)el.innerHTML=t}).catch(function(){})}
setInterval(tick,10000)})();
</script>"""


def _run_page(r: Dict[str, Any], repo_root: Path, assets: Path) -> str:
    from .diagram import chip_svg
    from .socview import build_view
    p, s = r["ppa"], r["summary"] or {}
    gates = [[_e(g["name"]), '<span class="chip ok">pass</span>' if g["passed"] else '<span class="chip bad">fail</span>',
              _e(g["detail"])] for g in (p["gates"] if p else [])]
    checks = [[f"<code>{_e(k)}</code>", _num(v, "{:g}")] for k, v in (s.get("hard_checks") or {}).items()]
    power = []
    for basis, corners in r["power"].items():
        for corner, total in corners.items():
            power.append([_e(basis), f"<code>{_e(corner)}</code>", _num(total * 1e3 if total else None, "{:.2f}")])
    gls, lec = s.get("gls") or {}, s.get("lec") or {}
    diagram = ""
    if r["config"]:
        try:
            diagram = chip_svg(build_view(r["config"], run_dir=r["dir"], repo_root=repo_root))
        except (Exception, SystemExit) as exc:
            diagram = f"<p class=basis>No diagram: {_e(' '.join(str(exc).split())[:200])}</p>"
    else:
        diagram = f"<p class=basis>No diagram: {_e(r['config_basis'])}.</p>"
    layout = ""
    if r["render"]:
        name = f"{r['tag']}.png"
        shutil.copyfile(r["render"], assets / name)
        layout = f'<img src="../assets/{_e(name)}" alt="Layout of {_e(r["tag"])} rendered by LibreLane" loading="lazy">'
    metrics = []
    if p:
        metrics = [["fmax", _num(p["fmax_mhz"], "{:.2f}") + " MHz"], ["die", _num(p["die_mm2"], "{:.4f}") + " mm²"],
                   ["logic", _num(p["logic_um2"], "{:,.0f}") + " µm²"], ["energy / cycle", _num(p["energy_nj"], "{:.3f}") + " nJ"],
                   ["setup WNS", _num(p["setup_ws_ns"], "{:+.3f}") + " ns"], ["hold WNS", _num(p["hold_ws_ns"], "{:+.3f}") + " ns"]]
    body = (f"<header><h1>{_e(r['tag'])}</h1><p class=meta>{_e(r['root'])} · "
            f"{_e((p or {}).get('design') or s.get('design'))} · {_e((p or {}).get('pdk') or s.get('pdk'))}</p></header>"
            f'<section><h2>Progress</h2><div id="progress" data-tag="{_e(r["tag"])}">{_progress_html(r["progress"])}</div></section>'
            f"<section><h2>Verdict</h2><p>{_verdict(r)}</p>"
            + (_table(["Gate", "Result", "Detail"], gates) if gates else f"<p class=basis>{_e('; '.join(r['ppa_errors']))}</p>")
            + (_table(["Objective", "Value"], metrics, (1,)) if metrics else "")
            + "</section><section><h2>Signoff checks</h2>" + (_table(["Metric", "Count"], checks, (1,)) if checks else "")
            + f"<p>GLS: <b>{_e(gls.get('status'))}</b> · LEC: <b>{_e(lec.get('status'))}</b></p>"
            + "".join(f"<p class=basis>{_e(x)}</p>" for x in (gls.get("reasons") or [])[:2])
            + "</section><section><h2>Power by corner</h2>"
            + (_table(["Basis", "Corner", "Total mW"], power, (2,)) if power else "<p class=basis>No power reports.</p>")
            + f"</section><section><h2>Diagram</h2><p class=basis>config: {_e(r['config_basis'])}</p>"
            + _tabs([("Chip", diagram)] + ([("Layout", layout)] if layout else [])) + "</section>")
    return _page(f"{r['tag']} · MOSAIC-SoC", body, 1, extra=LIVE)


def build_site(out: Path, repo_root: Path = REPO_ROOT) -> SkillResult:
    out = Path(out)
    for sub in ("design", "run", "assets"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    data = collect(repo_root)
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_root,
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        rev = ""
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M") + (f" at {rev}" if rev else "")
    (out / "index.html").write_text(_index(data, stamp))
    for d in data["designs"]:
        (out / "design" / f"{d['slug']}.html").write_text(_design_page(d, repo_root, out / "assets"))
    for r in data["runs"]:
        (out / "run" / f"{r['tag']}.html").write_text(_run_page(r, repo_root, out / "assets"))
    return SkillResult(ok=True, skill="web",
                       summary=f"site written to {out}: {len(data['designs'])} designs, {len(data['runs'])} runs",
                       details={"out": str(out), "designs": len(data["designs"]), "runs": len(data["runs"]),
                                "rejected_configs": [d["config"].name for d in data["designs"] if d["view"] is None]})


# ── live server ──────────────────────────────────────────────────────


def find_run(tag: str, repo_root: Path) -> Optional[Path]:
    if not _TAG.match(tag):
        return None
    for root in RUN_ROOTS:
        cand = repo_root / root / tag
        if (cand / "flow.log").is_file() or cand.is_dir():
            return cand
    return None


def make_handler(site: Path, repo_root: Path):
    from http.server import SimpleHTTPRequestHandler
    from .physical.progress import run_progress

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(site), **kw)

        def log_message(self, format, *args):  # noqa: A002 -- quiet; stdlib name
            pass

        def do_GET(self):  # noqa: N802 (stdlib name)
            m = re.fullmatch(r"/api/progress/([^/?#]+)", self.path.split("?")[0])
            if m:
                run = find_run(m.group(1), repo_root)
                if run is None:
                    self.send_error(404, "no such run")
                    return
                body = _progress_html(run_progress(run)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/"):
                self.send_error(404)
                return
            super().do_GET()

        def do_POST(self):  # noqa: N802 -- read-only viewer
            self.send_error(405, "read-only")

    return Handler


def serve(site: Path, port: int = 8765, host: str = "127.0.0.1",
          repo_root: Path = REPO_ROOT) -> int:
    """Serve `site` read-only. Loopback only: the pages show paths, run tags and
    evidence from this machine, and nothing here is built to face a network."""
    from http.server import ThreadingHTTPServer
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError(f"refusing to bind {host}: the viewer serves loopback only")
    httpd = ThreadingHTTPServer((host, port), make_handler(Path(site), repo_root))
    print(f"serving {site} at http://{host}:{httpd.server_address[1]}/ (Ctrl-C stops)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
