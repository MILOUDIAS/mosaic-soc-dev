"""Diagrams of a generated SoC, drawn from its SoCView (harness/socview.py).

Two views of the same data:

- **logical**: harts -> SCI wrappers (edge labelled with the core's native bus,
  coloured by protocol) -> interconnect -> crossbar targets -> what sits on the
  two peripheral buses. Blocks the RTL does not instantiate are drawn dashed,
  because several of them keep their address window and crossbar port.
- **chip**: the die (to scale when a run or derivation gives one), the core
  area, the IO on its real die edges when placed pins exist, and the blocks on
  a logical grid inside the core. The grid is NOT the layout; it says so.

Pure SVG strings, no dependencies, deterministic for a given view. Colours are
CSS variables on `.soc`, with a light default, a dark `prefers-color-scheme`
block and `[data-theme]` overrides, so the same SVG works standalone and inside
the web viewer's theme.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Tuple

_LIGHT = {"ink": "#16202a", "muted": "#5b6770", "line": "#c3ccd0", "node": "#f3f5f4",
          "core": "#d9eee8", "wrap": "#ebe3f6", "fab": "#1d2b36", "fabink": "#f3f5f4",
          "mem": "#dbe7f5", "die": "#f7f8f6", "corea": "#eef1ef", "pad": "#9aa6ac",
          "obi": "#0b6e69", "wb": "#7a4fb5", "ahb": "#b5651d", "axi": "#2c6db5",
          "tl": "#b03a5b", "simple": "#5a7d2a", "reg": "#8a949a"}
_DARK = {"ink": "#e2e8ea", "muted": "#95a2a8", "line": "#34414a", "node": "#1b2328",
         "core": "#153a34", "wrap": "#2b2240", "fab": "#d8e2e6", "fabink": "#10171b",
         "mem": "#1d2c40", "die": "#141b1f", "corea": "#1a2227", "pad": "#6d7a80",
         "obi": "#43b9af", "wb": "#a98be0", "ahb": "#e0a060", "axi": "#6aa3e8",
         "tl": "#e07a98", "simple": "#9bc160", "reg": "#7f8b91"}


def _vars(tokens: Dict[str, str]) -> str:
    return ";".join(f"--{k}:{v}" for k, v in tokens.items())


def _paint(prop: str, token: str) -> str:
    """`prop` from a theme token, with the light literal first: a renderer
    without CSS variables (librsvg, some viewers) keeps the literal."""
    return f"{prop}:{_LIGHT[token]};{prop}:var(--{token})"


# Light by default; dark under prefers-color-scheme; an explicit [data-theme] on
# the host page wins in both directions. `.soc` also matches a standalone SVG,
# whose root element carries the class. Colours go through classes, never
# `fill="var(...)"` attributes, which several renderers do not resolve.
STYLE = ("<style>"
         f".soc{{{_vars(_LIGHT)}}}"
         f"@media (prefers-color-scheme:dark){{.soc{{{_vars(_DARK)}}}"
         f":root[data-theme=light] .soc{{{_vars(_LIGHT)}}}}}"
         f":root[data-theme=dark] .soc{{{_vars(_DARK)}}}"
         '.soc text{font-family:"IBM Plex Sans",system-ui,sans-serif;' + _paint("fill", "ink") + "}"
         '.soc .mono{font-family:"IBM Plex Mono",ui-monospace,monospace}'
         ".soc .t{font-size:12px;font-weight:600}"
         ".soc .s{font-size:10px;" + _paint("fill", "muted") + "}"
         ".soc .h{font-size:10px;font-weight:600;letter-spacing:.08em;" + _paint("fill", "muted") + "}"
         ".soc .n{stroke-width:1;" + _paint("stroke", "line") + "}"
         ".soc .off{fill:none!important;stroke-dasharray:4 3}.soc .offt{opacity:.55}"
         ".soc .e{fill:none;stroke-width:1.6}.soc .el{font-size:9px}"
         ".soc .fabt{" + _paint("fill", "fabink") + "}"
         + "".join(f".soc .k-{k}{{{_paint('fill', k)}}}" for k in
                   ("core", "wrap", "fab", "mem", "node", "die", "corea", "pad"))
         + "".join(f".soc .p-{k}{{{_paint('stroke', k)};{_paint('fill', k)}}}" for k in
                   ("obi", "wb", "ahb", "axi", "tl", "simple", "reg"))
         + ".soc path.e{fill:none!important}.soc text.el{stroke:none!important}"
         + ".soc .ln{fill:none;stroke-dasharray:2 3;" + _paint("stroke", "line") + "}"
         + "</style>")

#: protocol colour class for a native bus name (presentation only)
PROTOCOL = {
    "obi": "p-obi", "wishbone-lite": "p-wb", "wishbone-classic": "p-wb",
    "ahb-lite": "p-ahb", "axi4": "p-axi", "tilelink-c": "p-tl",
    "req-gnt": "p-simple", "mem-valid-ready": "p-simple", "reqrsp": "p-simple",
}




def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _hex(v: Optional[int]) -> str:
    return "?" if v is None else f"0x{v:08x}"


def _size(v: Optional[int]) -> str:
    if v is None:
        return "?"
    for unit, div in (("MiB", 1 << 20), ("KiB", 1 << 10)):
        if v >= div and v % div == 0:
            return f"{v // div} {unit}"
    return f"{v} B"


def _node(x: float, y: float, w: float, h: float, title: str, sub: str,
          kind: str, tip: str, off: bool = False) -> str:
    ink = " fabt" if kind == "fab" and not off else ""
    dim = " offt" if off else ""
    return (f'<g><title>{_e(tip)}</title>'
            f'<rect class="n k-{kind}{" off" if off else ""}" x="{x:.0f}" y="{y:.0f}" '
            f'width="{w:.0f}" height="{h:.0f}" rx="6"/>'
            f'<text class="t{dim}{ink}" x="{x + 10:.0f}" y="{y + 18:.0f}">{_e(title)}</text>'
            f'<text class="s{dim}{ink}" x="{x + 10:.0f}" y="{y + 33:.0f}">{_e(sub)}</text></g>')


def _edge(x1: float, y1: float, x2: float, y2: float, proto: str,
          label: str = "") -> str:
    mx = (x1 + x2) / 2
    out = (f'<path class="e {proto}" d="M {x1:.0f} {y1:.0f} C {mx:.0f} {y1:.0f}, '
           f'{mx:.0f} {y2:.0f}, {x2:.0f} {y2:.0f}"/>')
    if label:
        out += (f'<text class="el mono {proto}" '
                f'x="{mx:.0f}" y="{(y1 + y2) / 2 - 5:.0f}" text-anchor="middle">{_e(label)}</text>')
    return out


def _svg(w: float, h: float, body: str, label: str) -> str:
    return (f'<svg class="soc" xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="{_e(label)}" viewBox="0 0 {w:.0f} {h:.0f}" '
            f'width="{w:.0f}" height="{h:.0f}">{STYLE}{body}</svg>')


def _group_label(g: Dict[str, Any]) -> Tuple[str, str]:
    ids = g["hart_ids"]
    harts = f"hart {ids[0]}" if len(ids) == 1 else f"harts {ids[0]}–{ids[-1]}"
    title = g["ip"] + (f" ×{g['count']}" if g["count"] > 1 else "")
    return title, f"{g['role']} · {g['isa']} · {harts}"


# ── logical view ─────────────────────────────────────────────────────

W, H, GAP_X, GAP_Y, PAD, TOP = 176, 44, 96, 14, 24, 44


def logical_svg(view: Dict[str, Any]) -> str:
    cores = view["cores"]
    masters = view["crossbar"]["masters"]
    blocks = view["blocks"]
    amap = view["address_map"]
    bus = view["bus"]["kind"]
    has_wrap = any(g["sci_module"] for g in cores)
    xs = [PAD + i * (W + GAP_X) for i in range(5)]
    if not has_wrap:  # no wrapper column: close the gap
        xs = [xs[0], xs[0], xs[1], xs[2], xs[3]]

    extra = []
    dma_ports = [m for m in masters if m["kind"] == "dma"]
    dbg = [m for m in masters if m["kind"] == "debug"]
    if dbg:
        extra.append(("debug module", "DM master port" if blocks["debug"] else
                      "port tied off (debug: false)", not blocks["debug"],
                      "Debug module crossbar master"))
    if dma_ports:
        extra.append(("DMA" if blocks["dma"] else "DMA (removed)",
                      f"{len(dma_ports)} crossbar port(s)" + ("" if blocks["dma"] else " tied off"),
                      not blocks["dma"], "DMA crossbar master ports: " +
                      ", ".join(m["name"] for m in dma_ports)))

    slaves = amap["slaves"]
    rams = [s for s in slaves if s["name"].startswith("ram")]
    others = [s for s in slaves if not s["name"].startswith("ram")]
    targets: List[Tuple[str, str, bool, str, str]] = []
    if rams:
        total = sum(s["size"] or 0 for s in rams)
        targets.append((f"RAM ×{len(rams)}" if len(rams) > 1 else rams[0]["name"].upper(),
                        f"{_size(total)} @ {_hex(rams[0]['start'])}", False, "mem",
                        "; ".join(f"{s['name']} {_hex(s['start'])} {_size(s['size'])}" for s in rams)))
    for s in others:
        off = s["name"] == "debug" and not blocks["debug"]
        label = {"ao_peripheral": "AO peripheral bus", "peripheral": "peripheral bus",
                 "flash_mem": "flash (XIP)", "debug": "debug window"}.get(s["name"]) or str(s["name"])
        targets.append((label, f"{_hex(s['start'])} · {_size(s['size'])}", off,
                        "node", f"{s['name']} crossbar slave {s.get('idx')}"))

    rows = max(len(cores) + len(extra), len(targets), 3)
    height = TOP + rows * (H + GAP_Y) + PAD + 150
    width = xs[4] + W + PAD + 20
    parts = []
    for i, head in enumerate(["HARTS", "WRAPPERS", "INTERCONNECT", "TARGETS", "ON THE BUSES"]):
        if head == "WRAPPERS" and not has_wrap:
            continue
        parts.append(f'<text class="h" x="{xs[i]}" y="{TOP - 16}">{head}</text>')

    def y(r: int) -> float:
        return TOP + r * (H + GAP_Y)

    # interconnect: one tall node spanning every master row
    span = max(len(cores) + len(extra), len(targets))
    fy, fh = y(0), span * (H + GAP_Y) - GAP_Y
    nm, ns = view["crossbar"]["nmaster"], view["crossbar"]["nslave"]
    opts = view["bus"].get("opts") or {}
    fab_title = {"obi": "OBI crossbar", "log": "LOG interconnect",
                 "floonoc": "FlooNoC"}.get(bus, bus)
    fab_sub = f"{nm} masters × {ns} slaves"
    if bus == "log":
        fab_sub = f"{view['memory']['banks']} banks · {(opts.get('log') or {}).get('topology', 'lic')}"
    parts.append(_node(xs[2], fy, W, fh, fab_title, fab_sub, "fab",
                       f"{fab_title}: {nm} master ports, {ns} slave ports "
                       f"({view['bus']['xheep_type']})"))

    for r, g in enumerate(cores):
        title, sub = _group_label(g)
        cy = y(r) + H / 2
        parts.append(_node(xs[0], y(r), W, H, title, sub, "core",
                           f"{g['ip']} {g['role']} {g['isa']} harts {g['hart_ids']} params {g['params']}"))
        proto = PROTOCOL.get(g["native_bus"] or "", "p-obi")
        if g["sci_module"]:
            parts.append(_edge(xs[0] + W, cy, xs[1], cy, proto, g["native_bus"] or "?"))
            parts.append(_node(xs[1], y(r), W, H, g["sci_module"], f"{g['native_bus']} → OBI",
                               "wrap", f"hw/sci/{g['sci_module']}.sv"))
            parts.append(_edge(xs[1] + W, cy, xs[2], cy, "p-obi", "obi ×2" if g["count"] == 1 else f"obi ×{2 * g['count']}"))
        else:
            parts.append(_edge(xs[0] + W, cy, xs[2], cy, "p-obi",
                               "obi ×2" if g["count"] == 1 else f"obi ×{2 * g['count']}"))
    for i, (title, sub, off, tip) in enumerate(extra):
        r = len(cores) + i
        parts.append(_node(xs[0], y(r), W, H, title, sub, "node", tip, off))
        if not off:
            parts.append(_edge(xs[0] + W, y(r) + H / 2, xs[2], y(r) + H / 2, "p-obi"))

    ao_y = per_y = None
    for r, (title, sub, off, kind, tip) in enumerate(targets):
        parts.append(_edge(xs[2] + W, y(r) + H / 2, xs[3], y(r) + H / 2, "p-obi"))
        parts.append(_node(xs[3], y(r), W, H, title, sub, kind, tip, off))
        if title == "AO peripheral bus":
            ao_y = y(r) + H / 2
        if title == "peripheral bus":
            per_y = y(r) + H / 2

    # the two peripheral buses, listed
    def listing(entries: List[Dict[str, Any]], x: float, top: float, head: str,
                src_y: Optional[float]) -> float:
        lh = 15
        h = 26 + lh * max(1, len(entries))
        parts.append(f'<g><title>{_e(head)}</title><rect x="{x:.0f}" y="{top:.0f}" '
                     f'width="{W + 40}" height="{h}" rx="6" class="n k-node"/>'
                     f'<text class="s" x="{x + 10:.0f}" y="{top + 16:.0f}">{_e(head)}</text></g>')
        for i, e in enumerate(entries or [{"name": "(none)", "instantiated": True, "start": None, "size": None}]):
            cls = "s mono" if e.get("instantiated", True) else "s mono offt"
            deco = "" if e.get("instantiated", True) else ' text-decoration="line-through"'
            parts.append(f'<text class="{cls}"{deco} x="{x + 10:.0f}" y="{top + 32 + i * lh:.0f}">'
                         f'<title>{_e(e["name"])} {_hex(e.get("start"))} {_size(e.get("size"))}'
                         f'{"" if e.get("instantiated", True) else " (decoded, not instantiated)"}</title>'
                         f'{_e(e["name"])}  {_hex(e.get("start"))}</text>')
        if src_y is not None:
            parts.append(_edge(xs[3] + W, src_y, x, top + 16, "p-reg"))
        return top + h

    bottom = listing(amap["ao_peripherals"], xs[4], y(0), "AO peripheral bus", ao_y)
    per = list(amap["peripherals"])
    bottom = listing(per, xs[4], bottom + 16, "peripheral bus" +
                     (f" · PLIC {view['plic']['used_sources']}/{view['plic']['sources']} sources"
                      if view["plic"]["instantiated"] else " · no PLIC"), per_y)

    # protocol legend
    used = sorted({g["native_bus"] for g in cores if g["native_bus"]} | {"obi"})
    ly = max(bottom, y(rows)) + 24
    lx = PAD
    parts.append(f'<text class="h" x="{lx}" y="{ly}">PROTOCOLS</text>')
    for i, p in enumerate(used):
        px = lx + 90 + i * 130
        parts.append(f'<rect class="{PROTOCOL.get(p, "p-obi")}" x="{px}" y="{ly - 9}" width="18" height="4"/>'
                     f'<text class="s mono" x="{px + 24}" y="{ly - 4}">{_e(p)}</text>')
    parts.append(f'<text class="s" x="{lx}" y="{ly + 22}">Dashed or struck through: '
                 f'decoded or wired but not instantiated in the RTL.</text>')
    height = ly + 40
    return _svg(width, height, "".join(parts), f"{view['name']} logical diagram")


# ── chip view ────────────────────────────────────────────────────────

SIDE, PADW, CH = 620, 10, 6.2   # die drawing side px, pad tick px, mono px/char


def _blocks(view: Dict[str, Any]) -> List[Tuple[str, str, str, bool]]:
    """(title, subtitle, kind, off) for the logical grid inside the core."""
    out: List[Tuple[str, str, str, bool]] = [
        (*_group_label(g), "core", False) for g in view["cores"]]
    b = view["blocks"]
    amap = view["address_map"]
    rams = [s for s in amap["slaves"] if s["name"].startswith("ram")]
    if rams:
        out.append((f"RAM ×{len(rams)}" if len(rams) > 1 else "RAM",
                    _size(sum(s["size"] or 0 for s in rams)), "mem", False))
    for name, title in (("tdu", "TDU"), ("clint", "CLINT"), ("dma", "DMA"), ("debug", "debug")):
        if name in b:
            out.append((title, "instantiated" if b[name] else "removed", "node", not b[name]))
    if view["plic"]["instantiated"]:
        out.append(("PLIC", f"{view['plic']['used_sources']} sources", "node", False))
    for p in amap["peripherals"]:
        if p["name"] != "rv_plic":
            out.append((p["name"], _hex(p["start"]), "node", False))
    out.append(("boot ROM", _size(view["memory"]["boot_rom_bytes"]), "node", False))
    out.append(("flash ctrl", f"XIP · {view.get('spi_mode') or 'spi'}", "node", False))
    return out


def _pin_groups(pins: List[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for p in pins:
        groups.setdefault((p["side"], p["group"]), []).append(p)
    return groups


def chip_svg(view: Dict[str, Any]) -> str:
    die, io = view["die"], view["io"]
    pins = io.get("pins") or []
    die_um = die.get("die_um")
    placed = [p for p in pins if p.get("side") and p.get("x_um") is not None] if die_um else []
    groups = _pin_groups(placed)
    # room for the longest pin label on each side (N/S labels run at 60 degrees)
    longest = {k: 0 for k in "NESW"}
    for (side_, g), v in groups.items():
        longest[side_] = max(longest[side_], len(g + (f" ×{len(v)}" if len(v) > 1 else "")))
    m = {k: 36 + PADW + CH * n * (0.87 if k in "NS" else 1.0) for k, n in longest.items()}
    m["N"] = max(m["N"], 70)          # the IO note sits above the die when nothing is placed
    m["W"], m["E"] = max(m["W"], 36), max(m["E"], 36)
    width = m["W"] + SIDE + m["E"]
    height = m["N"] + SIDE + m["S"] + 50
    x0, y0 = m["W"], m["N"]
    parts: List[str] = []
    scale = SIDE / (die_um[2] - die_um[0]) if die_um else None
    parts.append(f'<g><title>die: {_e(die["basis"])}</title>'
                 f'<rect class="n k-die{"" if die_um else " off"}" x="{x0:.0f}" y="{y0:.0f}" '
                 f'width="{SIDE}" height="{SIDE}" style="stroke-width:1.5"/></g>')
    cx0, cy0, cside = x0 + 24, y0 + 24, SIDE - 48
    if die_um and die.get("core_um") and scale:
        c = die["core_um"]
        cx0, cy0 = x0 + (c[0] - die_um[0]) * scale, y0 + (die_um[3] - c[3]) * scale
        cside = (c[2] - c[0]) * scale
    parts.append(f'<rect class="k-corea ln" x="{cx0:.1f}" y="{cy0:.1f}" width="{cside:.1f}" '
                 f'height="{cside:.1f}"/>')

    blocks = _blocks(view)
    cols = 3 if len(blocks) <= 9 else 4
    gap = 10
    bw = (cside - 32 - (cols - 1) * gap) / cols
    for i, (title, sub, kind, off) in enumerate(blocks):
        r, c = divmod(i, cols)
        parts.append(_node(cx0 + 16 + c * (bw + gap), cy0 + 34 + r * (44 + gap), bw, 44,
                           title, sub, kind, f"{title}: {sub}", off))
    parts.append(f'<text class="s" x="{cx0 + 16:.0f}" y="{cy0 + 20:.0f}">core area · '
                 f'logical placement, not the layout</text>')

    if placed and scale:
        for (s, g), members in sorted(groups.items()):
            pts = []
            for p in members:
                px = x0 + (p["x_um"] - die_um[0]) * scale
                py = y0 + (die_um[3] - p["y_um"]) * scale
                pts.append((px, py))
                if s in "WE":
                    rx, ry, rw, rh = (px - PADW - 1 if s == "W" else px + 1), py - 2, PADW, 4
                else:
                    rx, ry, rw, rh = px - 2, (py - PADW - 1 if s == "N" else py + 1), 4, PADW
                tip = f"{p['name']} · {p.get('direction', '')} · edge {s}" + (
                    f" · {p['cell']}" if p.get("cell") else "") + (
                    f" · slot {p['slot']}" if p.get("slot") else "")
                parts.append(f'<rect class="k-pad" x="{rx:.1f}" y="{ry:.1f}" width="{rw}" '
                             f'height="{rh}"><title>{_e(tip)}</title></rect>')
            mx = sum(p[0] for p in pts) / len(pts)
            my = sum(p[1] for p in pts) / len(pts)
            label = g + (f" ×{len(pts)}" if len(pts) > 1 else "")
            if s == "W":
                parts.append(f'<text class="s mono" x="{x0 - PADW - 8:.0f}" y="{my + 3:.0f}" '
                             f'text-anchor="end">{_e(label)}</text>')
            elif s == "E":
                parts.append(f'<text class="s mono" x="{x0 + SIDE + PADW + 8:.0f}" '
                             f'y="{my + 3:.0f}">{_e(label)}</text>')
            elif s == "N":
                ty = y0 - PADW - 8
                parts.append(f'<text class="s mono" transform="rotate(-60 {mx:.0f} {ty:.0f})" '
                             f'x="{mx:.0f}" y="{ty:.0f}">{_e(label)}</text>')
            else:
                ty = y0 + SIDE + PADW + 8
                parts.append(f'<text class="s mono" transform="rotate(60 {mx:.0f} {ty:.0f})" '
                             f'x="{mx:.0f}" y="{ty:.0f}">{_e(label)}</text>')
    else:
        # IO without placement: dashed ticks spread round the ring, and the reason
        loose = [p for p in pins if p.get("use") not in ("POWER", "GROUND")]
        per = 4 * SIDE / max(len(loose), 1)
        for i, p in enumerate(loose):
            d = (i + 0.5) * per
            k, off = divmod(d, SIDE)
            px, py = [(x0 + off, y0 - 9), (x0 + SIDE + 9, y0 + off),
                      (x0 + SIDE - off, y0 + SIDE + 9), (x0 - 9, y0 + SIDE - off)][int(k) % 4]
            parts.append(f'<rect class="off" x="{px - 3:.0f}" y="{py - 3:.0f}" width="6" '
                         f'height="6" style="stroke:{_LIGHT["pad"]};stroke:var(--pad)">'
                         f'<title>{_e(p["name"])} (not placed)</title></rect>')
        parts.append(f'<text class="s" x="{x0:.0f}" y="{y0 - 44:.0f}">IO: {_e(io["basis"])}</text>')
        need = io.get("interfaces_needing_pins") or []
        if need:
            parts.append(f'<text class="s mono" x="{x0:.0f}" y="{y0 - 28:.0f}">interfaces '
                         f'needing pins: {_e(", ".join(need))}</text>')

    power = [p["name"] for p in pins if p.get("use") in ("POWER", "GROUND") and not p.get("side")]
    size = (f"{die_um[2] - die_um[0]:.1f} × {die_um[3] - die_um[1]:.1f} µm" if die_um
            else "die size not estimated")
    util = die.get("utilisation")
    foot = size + (f" · utilisation {util:.0%}" if isinstance(util, (int, float)) else "")
    parts.append(f'<text class="t" x="{x0:.0f}" y="{height - 40:.0f}">{_e(foot)}</text>')
    parts.append(f'<text class="s" x="{x0:.0f}" y="{height - 24:.0f}">die: {_e(die["basis"])}'
                 f'{" · power straps (no single edge): " + ", ".join(power) if power else ""}</text>')
    return _svg(width, height, "".join(parts), f"{view['name']} chip view")


# ── standalone page (topo-viz render, soc-from-prompt) ───────────────

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{--bg:#f4f6f5;--fg:#16202a;--muted:#5b6770;--line:#c3ccd0;--card:#fff;--acc:#0b6e69}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1316;--fg:#e2e8ea;--muted:#95a2a8;--line:#29343b;--card:#151c20;--acc:#43b9af;color-scheme:dark}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,sans-serif;padding-inline:16px}}
main{{max-width:1180px;margin:0 auto;padding-block:28px 48px;display:grid;gap:20px}}
h1{{margin:0;font-size:1.6rem}}.meta{{color:var(--muted);margin:0}}
.tabs input{{position:absolute;opacity:0}}.tabs label{{display:inline-block;padding:6px 14px;border:1px solid var(--line);border-radius:4px;margin-right:6px;cursor:pointer}}
.tabs input:checked+label{{border-color:var(--acc);color:var(--acc);font-weight:600}}
.tabs input:focus-visible+label{{outline:2px solid var(--acc)}}
.pane{{display:none;overflow:auto;border:1px solid var(--line);background:var(--card);border-radius:6px;padding:12px;margin-top:10px}}
#t1:checked~.p1,#t2:checked~.p2{{display:block}}.pane svg{{max-width:100%;height:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left}}
th{{color:var(--muted);font-weight:600}}code{{font-family:ui-monospace,monospace}}.scroll{{overflow-x:auto}}
</style></head><body><main>
<h1>{title}</h1><p class="meta">{meta}</p>
<div class="tabs"><input type="radio" name="v" id="t1" checked><label for="t1">Logical</label><input type="radio" name="v" id="t2"><label for="t2">Chip</label>
<div class="pane p1">{logical}</div><div class="pane p2">{chip}</div></div>
<h2>Memory map</h2><div class="scroll"><table><tr><th>Region</th><th>Start</th><th>Size</th><th>Instantiated</th></tr>{memmap}</table></div>
</main></body></html>
"""


def memory_rows(view: Dict[str, Any]) -> List[Tuple[str, Optional[int], Optional[int], bool]]:
    amap = view["address_map"]
    rows = [(s["name"], s["start"], s["size"],
             not (s["name"] == "debug" and not view["blocks"]["debug"])) for s in amap["slaves"]]
    rows += [(f"ao.{p['name']}", p["start"], p["size"], p["instantiated"]) for p in amap["ao_peripherals"]]
    rows += [(f"periph.{p['name']}", p["start"], p["size"], p["instantiated"]) for p in amap["peripherals"]]
    return sorted(rows, key=lambda r: (r[1] is None, r[1] or 0))


def meta_line(view: Dict[str, Any]) -> str:
    c = view["crossbar"]
    return (f"{view['bus']['kind']} · {view['num_harts']} harts · {c['nmaster']} crossbar "
            f"masters · {view['memory']['banks']} RAM bank(s) · pdk {view['pdk']} · "
            f"target {view['target']}")


def standalone_html(view: Dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td><code>{_e(n)}</code></td><td><code>{_hex(s)}</code></td>"
        f"<td>{_size(z)}</td><td>{'yes' if inc else 'no'}</td></tr>"
        for n, s, z, inc in memory_rows(view))
    return PAGE.format(title=_e(f"{view['name']} topology"), meta=_e(meta_line(view)),
                       logical=logical_svg(view), chip=chip_svg(view), memmap=rows)
