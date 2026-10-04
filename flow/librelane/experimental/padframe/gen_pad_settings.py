#!/usr/bin/env python3
"""Emit the Block A pad-settings table from the padframe interface file.

Every value comes from pad_policy, which the wrapper generator also reads, so the
table and the netlist cannot disagree about a pad setting. They did once: the
table said rst_ni carries a pull-down while the wrapper tied it to 0, because
each script held its own copy of the policy.

Regenerate whenever the padframe DEF is reissued. Do not hand-edit the output.
"""
from __future__ import annotations
import argparse, collections, pathlib, sys
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from pad_policy import classify, value  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()

    data = yaml.safe_load(args.interface.read_text())
    by_user: dict[str, dict] = collections.defaultdict(dict)
    for pin in data["pins"]:
        by_user[pin["user_pin_name"]][pin["cell_terminal"]] = pin

    counts: collections.Counter = collections.Counter()
    rows = []
    for user, terms in by_user.items():
        kind = classify(user, next(iter(terms.values()))["cell"])
        counts[kind] += 1
        for terminal, pin in sorted(terms.items()):
            rows.append((user, kind, terminal, pin["project_pin"],
                         pin["padring_instance"], value(user, kind, terminal)))

    if len(rows) != len(data["pins"]):
        print(f"ERROR: {len(data['pins'])} terminals but {len(rows)} rows", file=sys.stderr)
        return 1

    w, h = data["size_microns"]
    per = {k: sorted({t for _, kk, t, *_ in rows if kk == k}) for k in counts}
    out = [
        "# Block A pad settings",
        "",
        f"Generated from `{args.interface.name}` by `gen_pad_settings.py`, with every",
        "value taken from `pad_policy.py`. Do not hand-edit: regenerate when the",
        "padframe DEF is reissued.",
        "",
        f"Variant `{data['variant']}` · **{len(rows)} terminals** across "
        f"**{sum(counts.values())} user pins** · die {w} × {h} µm",
        "",
        "| class | user pins | terminals each |",
        "|---|---:|---|",
    ]
    for k in ("power", "input", "output", "qspi"):
        if k in counts:
            out.append(f"| {k} | {counts[k]} | {', '.join(f'`{t}`' for t in per[k])} |")
    out += [
        "",
        "`oe` is the core's own `spi_flash_sd_*_oe_o`. `IE = ~oe` because the PDK",
        "control table marks `IE=1, OE=1` **Disallowed**, so IE cannot be tied high.",
        "`rst_ni` is the one input carrying a pull, and it is a pull-down: an undriven",
        "reset then holds the part in reset, which is the diagnosable failure on a block",
        "whose only observability is `status_o`.",
        "",
        "**Drive strength is 12 mA and rests on an estimate, not a measurement.**",
        "No house default exists: the padframe does not prescribe a drive strength,",
        "and each block sets its own. The number",
        "comes from QSPI at 20 MHz into an assumed 15-25 pF of bond pad, package and",
        "short trace, where ~20 pF through a 10 ns edge needs about 10 mA. If a real",
        "board load ever lands well under 20 pF, 8 mA is the better choice.",
        "",
        "## Every terminal",
        "",
        "| user pin | class | terminal | DEF pin | slot | driven to |",
        "|---|---|---|---|---|---|",
    ]
    for user, kind, terminal, defpin, slot, val in sorted(rows, key=lambda r: (r[1], r[0], r[2])):
        out.append(f"| `{user}` | {kind} | `{terminal}` | `{defpin}` | {slot} | {val} |")

    args.out.write_text("\n".join(out) + "\n")
    print(f"{args.out}: {len(rows)} terminals, {sum(counts.values())} user pins, {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
