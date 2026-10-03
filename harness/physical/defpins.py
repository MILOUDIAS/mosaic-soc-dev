"""Pins of a DEF file, and which die edge each one sits on.

Two shapes occur in this project and both must read the same:

- a hardened run's final DEF places each signal pin with `+ PLACED ( x y )` and
  gives its shape relative to that origin (`+ LAYER Metal3 ( -560 -560 ) ...`);
- an integrator's padframe DEF (the Chipathon D15 files) writes
  `+ FIXED ( 0 0 )` and gives the shape in ABSOLUTE coordinates.

So a pin's position is its origin plus the centre of its first shape, in DEF
database units divided by the UNITS line. The divisor is not always 1000 (the
padframe DEFs use 200, hardened runs 2000); reading coordinates without it is
off by 5-10x and still looks plausible.

Power pins with many straps (`+ SPECIAL` + `PORT` blocks spanning the die) have
no single edge; they are reported with `side=None` rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

_UNITS = re.compile(r"^UNITS DISTANCE MICRONS (\d+)", re.M)
_DIEAREA = re.compile(
    r"^DIEAREA \( *(-?\d+) +(-?\d+) *\) *\( *(-?\d+) +(-?\d+) *\)", re.M)
_PINS = re.compile(r"^PINS \d+ ;(.*?)^END PINS", re.M | re.S)
_HEAD = re.compile(r"^- (\S+) \+ NET (\S+)(.*)$")
# DEF 5.8: + LAYER name [MASK n] [SPACING d | DESIGNRULEWIDTH w] pt pt
_RECT = re.compile(
    r"\+ LAYER (\S+)(?: MASK \d+)?(?: (?:SPACING|DESIGNRULEWIDTH) \d+)?"
    r" \( *(-?\d+) +(-?\d+) *\) \( *(-?\d+) +(-?\d+) *\)")
_ORIGIN = re.compile(r"\+ (?:PLACED|FIXED|COVER) \( *(-?\d+) +(-?\d+) *\)")

Rect = Tuple[float, float, float, float]


@dataclass(frozen=True)
class Pin:
    name: str
    net: str
    direction: str            # INPUT | OUTPUT | INOUT | "" when unstated
    use: str                  # SIGNAL | POWER | GROUND | CLOCK | ...
    layer: Optional[str]
    x_um: Optional[float]     # centre of the first shape, microns
    y_um: Optional[float]
    side: Optional[str]       # N | S | E | W, None for multi-strap power pins
    shapes: int


@dataclass(frozen=True)
class DefPins:
    path: str
    dbu: int
    die_um: Rect              # x0, y0, x1, y1 in microns
    pins: Tuple[Pin, ...]

    def by_side(self) -> dict:
        out = {"N": [], "S": [], "E": [], "W": [], None: []}
        for p in self.pins:
            out[p.side].append(p)
        return out


def die_area(text: str) -> Optional[Tuple[int, Rect]]:
    """(database units per micron, DIEAREA rectangle in microns), or None."""
    units, area = _UNITS.search(text), _DIEAREA.search(text)
    if not units or not area:
        return None
    dbu = int(units.group(1))
    x0, y0, x1, y1 = (int(v) / dbu for v in area.groups())
    return dbu, (x0, y0, x1, y1)


def _side(x: float, y: float, die: Rect) -> str:
    x0, y0, x1, y1 = die
    gaps = {"W": x - x0, "E": x1 - x, "S": y - y0, "N": y1 - y}
    return min(gaps, key=gaps.__getitem__)


def _pin(body: str, dbu: int, die: Rect) -> Pin:
    head = _HEAD.match(body)
    if head is None:
        raise ValueError(f"unreadable PINS statement: {body[:80]}")
    name, net, rest = head.groups()
    direction = re.search(r"\+ DIRECTION (\S+)", body)
    use = re.search(r"\+ USE (\S+)", body)
    rects = _RECT.findall(body)
    origin = _ORIGIN.search(body)
    x = y = side = layer = None
    special = "+ SPECIAL" in rest
    if rects and not (special and len(rects) > 1):
        layer, *coords = rects[0]
        rx0, ry0, rx1, ry1 = (int(c) for c in coords)
        ox, oy = (int(v) for v in origin.groups()) if origin else (0, 0)
        x = (ox + (rx0 + rx1) / 2) / dbu
        y = (oy + (ry0 + ry1) / 2) / dbu
        side = _side(x, y, die)
    return Pin(name=name, net=net,
               direction=direction.group(1) if direction else "",
               use=use.group(1) if use else "SIGNAL",
               layer=layer, x_um=x, y_um=y, side=side, shapes=len(rects))


def parse_def_pins(path: Path) -> DefPins:
    """Every pin in `path`'s PINS section. Raises ValueError if the file lacks
    UNITS, DIEAREA or a PINS section: an empty result would read as "this
    design has no IO", which is never true of a DEF we would parse."""
    text = Path(path).read_text()
    area = die_area(text)
    section = _PINS.search(text)
    if area is None or section is None:
        raise ValueError(f"{path}: no UNITS/DIEAREA/PINS section")
    dbu, die = area
    # A statement ends at ';' -- DEF lets a line break fall anywhere, so a pin
    # whose `+ NET` sits on the next line must not be dropped.
    blocks = [" ".join(stmt.split()) for stmt in section.group(1).split(";")]
    blocks = [b for b in blocks if b.startswith("- ")]
    return DefPins(path=str(path), dbu=dbu, die_um=die,
                   pins=tuple(_pin(b, dbu, die) for b in blocks))
