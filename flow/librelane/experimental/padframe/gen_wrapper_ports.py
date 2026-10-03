#!/usr/bin/env python3
"""Emit the padframe-facing port list for mosaic_block_a from the interface file.

The macro must present one port per DEF pin: 167 of them, because the padframe
expects it to drive each pad's control terminals as well as its data. Writing that
list by hand invites a mismatch that Odb.ApplyDEFTemplate would only catch in
strict mode, after a synthesis run. Generating it from D15_A_interface.yaml makes
the two agree by construction.

Emits the port declarations and the constant drives. The functional connections
to core_v_mini_mcu stay hand-written in the wrapper, because they carry design
intent this file cannot know.
"""
from __future__ import annotations
import argparse, collections, pathlib, re, sys
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from pad_policy import classify, is_constant, sv_literal, value  # noqa: E402

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()

    data = yaml.safe_load(args.interface.read_text())
    pins = data["pins"]

    # group DEF pin names into scalars and vectors, remembering class + direction
    width: dict[str, set[int]] = collections.defaultdict(set)
    meta: dict[str, tuple[str, str, str]] = {}
    user_of: dict[str, str] = {}
    for p in pins:
        name = p["project_pin"]
        base, idx = name, None
        m = re.match(r"^(.*)\[(\d+)\]$", name)
        if m:
            base, idx = m.group(1), int(m.group(2))
            width[base].add(idx)
        else:
            width.setdefault(base, set())
        meta[base] = (classify(p["user_pin_name"], p["cell"]),
                      p["cell_terminal"], p["direction"])
        user_of[base] = p["user_pin_name"]

    # DEF DIRECTION is from the padring's view; invert for the macro boundary.
    def sv_dir(term: str, direction: str, klass: str) -> str:
        if klass == "power":
            return "inout"
        if term in ("Y",):        # pad drives the core
            return "input"
        return "output"           # everything else the macro drives

    decls, ties = [], []
    for base in sorted(width):
        klass, term, direction = meta[base]
        d = sv_dir(term, direction, klass)
        bits = width[base]
        rng = f" [{max(bits)}:{min(bits)}]" if bits else ""
        kind = "wire " if klass == "power" else "logic"
        decls.append(f"    {d:6s} {kind}{rng:>8s} {base},")
        token = value(user_of[base], klass, term)
        if is_constant(token):
            ties.append(f"  assign {base} = {sv_literal(token, len(bits) if bits else None)};")

    body = [
        "// ---------------------------------------------------------------------------",
        "// GENERATED PORT LIST -- do not hand-edit.",
        "//",
        f"//   source : {args.interface.name}",
        f"//   variant: {data['variant']}   die {data['size_microns'][0]} x "
        f"{data['size_microns'][1]} um   {len(pins)} terminals",
        "//",
        "// Regenerate with padframe/gen_wrapper_ports.py when the integrator reissues",
        "// the DEF. The port names must match D15_A.def exactly, because",
        "// Odb.ApplyDEFTemplate runs in strict mode and requires identical pin sets.",
        "// ---------------------------------------------------------------------------",
        "",
        "// --- ports ---",
        *decls,
        "",
        "// --- constant pad controls ---",
        "//",
        "// The QSPI OE/IE are absent here on purpose: they carry the core's own",
        "// output enable and its inverse. IE=1 with OE=1 is Disallowed in the PDK",
        "// control table, so IE follows ~OE rather than being tied high.",
        *ties,
    ]
    args.out.write_text("\n".join(body) + "\n")
    n_bits = sum(max(b) - min(b) + 1 if b else 1 for b in width.values())
    if n_bits != len(pins):
        print(f"ERROR: {n_bits} port bits but {len(pins)} DEF pins", file=sys.stderr)
        return 1
    print(f"{args.out}: {len(decls)} declarations = {n_bits} bits, {len(ties)} tied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
