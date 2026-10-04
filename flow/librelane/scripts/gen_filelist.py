#!/usr/bin/env python3
"""Resolve VERILOG_FILES / VERILOG_INCLUDE_DIRS for a LibreLane run.

WHY THIS EXISTS
---------------
The Block A configs used to carry ~526 ABSOLUTE paths of the shape

    build/mosaic/mosaic_tapeout_ultra-<hash>/runs/fusesoc.<random>/build/src/...

Both variable parts move on their own: <hash> is content-addressed over the
config AND the whole generator source closure, so editing a template or even a
comment changes it, and fusesoc.<random> is a fresh mktemp directory on every
`scripts/fusesoc-setup.sh` invocation ("every invocation gets a unique run
directory"). The list therefore went stale constantly -- it was rewritten five
times in a single day's work -- and, worse, on any other machine every path
pointed at a directory that does not exist, so the committed signoff was
unreproducible by anyone else.

The dangerous failure was never "path missing". It was a path that still
RESOLVED, to an older bundle, which would have hardened stale RTL and reported
clean results for the wrong design.

The simulation flow already solved this: tb/mosaic_soc/gen_filelist.py derives
its list from the manifest at run time. This is the same idea for LibreLane.

WHAT IT EMITS
-------------
A YAML fragment with VERILOG_FILES and VERILOG_INCLUDE_DIRS resolved from the
FuseSoC .vc of the CURRENT build, plus the ASIC-side sources that are not part
of the FuseSoC graph. run_signoff.sh merges it into the config it hands to
LibreLane, so the checked-in config carries no absolute paths at all.

WHAT IT LEAVES OUT, AND WHY
---------------------------
Not everything in the simulation .vc belongs in a synthesis run:

  * DPI/C-backed models (uartdpi, remote_bitbang) cannot be synthesised.
  * tech_cells_generic's tc_clk collides by module name with the GF180
    replacement in hw/asic/gf180/tc_clk.sv (GF180 has no latch cell).
  * x-heep's example IPs and the peripherals this config does not instantiate
    (pdm2pcm, i2s, the x-heep DMA) are dead weight in front of the elaborator.

The exclusions are declared below as data, and `--verify` checks the result
against a known-good list so the policy cannot drift silently.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# FuseSoC core directories (build/src/<VLNV>) whose sources never belong in a
# synthesis run for this design. Matched against the WHOLE directory name, and
# anchored: "x-heep_ip_dma_0" is the DMA IP and is excluded, while
# "x-heep_ip_dma_subsystem_0" is the bus plumbing that survives soc.dma: none
# and must stay. A prefix match would silently drop it.
EXCLUDED_CORE_PATTERNS = (
    r"lowrisc_dv_dpi_uartdpi.*",                    # DPI: C-backed UART model
    r"pulp-platform\.org__pulpissimo_remote_bitbang.*",  # DPI: JTAG bitbang
    r"x-heep_ip_pdm2pcm.*",                         # peripheral not in this config
    r"x-heep_ip_i2s.*",                             # peripheral not in this config
    r"x-heep_ip_dma_\d.*",                          # the DMA IP (soc.dma: none)
    r"example_ip_.*",                               # x-heep external-device examples
)
_EXCLUDED_CORE_RE = re.compile("|".join(f"(?:{p})" for p in EXCLUDED_CORE_PATTERNS))

# Paths under the staged core-root that are examples or unused peripherals.
EXCLUDED_PATH_PARTS = (
    "/hw/ip_examples/",
    "/hw/ip/pdm2pcm/",
    # ONE file from tech_cells_generic, not the whole core: its tc_clk collides
    # by module name with hw/asic/gf180/tc_clk.sv (GF180 has no latch cell, so
    # the generic behavioural clock gate leaves an unmappable $_DLATCH_N_).
    # The core's other sources (tc_sram, tc_pwr, the deprecated clk
    # cells) are still needed.
    "/tech_cells_generic/src/rtl/tc_clk.sv",
)

# Sources outside the FuseSoC graph that the macro needs, whatever the design.
EXTRA_SOURCES = (
    "tb/mosaic_soc/cve2_clock_gate.sv",               # clock-gate shim
)

# The integrated clock gate is a TECHNOLOGY CELL, so it is per-PDK -- the one
# genuinely process-specific file in the RTL. hw/asic/ already anticipated
# this (gf180/, sky130/, generic_example/), but this list hardcoded GF180's,
# so the first IHP run failed at Verilator lint with
#     Can't resolve module reference: 'gf180mcu_fd_sc_mcu7t5v0__icgtp_1'
# after the whole RTL had elaborated -- late, and blaming the wrong thing.
#
# A PDK with no entry is REFUSED rather than given GF180's cell. sky130 has a
# directory but no tc_clk.sv, which is exactly the case that would otherwise
# fail the same confusing way.
# Two cells, both found by hardening on a second PDK and reading what broke:
# the integrated clock gate (tc_clk) and the tristate pad driver (tc_pad).
# The pad driver used to be instantiated directly in the DELIVERY WRAPPERS,
# which are per-design -- so it cost designs x PDKs until it moved here.
TECH_CELLS_BY_PDK = {
    "gf180mcu": ("hw/asic/gf180/tc_clk.sv", "hw/asic/gf180/tc_pad.sv"),
    "ihp-sg13g2": ("hw/asic/ihp-sg13g2/tc_clk.sv",
                   "hw/asic/ihp-sg13g2/tc_pad.sv"),
}
DEFAULT_PDK = "gf180mcu"

# The delivery wrapper is per-DESIGN, because LibreLane's DESIGN_NAME must equal
# the top module name and signoff waivers are scoped by design name -- two
# designs cannot share one module. This was hardcoded to Block A's wrapper,
# which meant hardening any other design failed late, at Verilator lint, with
# "Specified --top-module was not found in design" rather than at the point the
# wrong file list was built.
DEFAULT_WRAPPER = "flow/librelane/experimental/mosaic_block_a.sv"


def find_vc(build_root: pathlib.Path) -> pathlib.Path:
    candidates = sorted(build_root.rglob("*.vc"))
    hits = [c for c in candidates if "sim-verilator" in str(c)]
    if not hits:
        raise SystemExit(f"no sim-verilator .vc found below {build_root}")
    if len(hits) > 1:
        raise SystemExit(f"ambiguous .vc below {build_root}: {hits}")
    return hits[0]


def excluded(path: str) -> bool:
    m = re.search(r"/build/src/([^/]+)/", path)
    if m and _EXCLUDED_CORE_RE.fullmatch(m.group(1)):
        return True
    return any(part in path for part in EXCLUDED_PATH_PARTS)


def tech_cells(pdk: str) -> tuple[str, ...]:
    """This process's technology cells, or a refusal naming what is missing."""
    try:
        return TECH_CELLS_BY_PDK[pdk]
    except KeyError:
        raise SystemExit(
            f"no technology cells for pdk {pdk!r}. The generic behavioural "
            "clock gate leaves an unmappable $_DLATCH_N_, and another "
            "process's cells will not resolve at all. Add "
            "hw/asic/<pdk>/tc_clk.sv and hw/asic/<pdk>/tc_pad.sv binding that "
            "library's ICG and tristate, and register them in "
            f"TECH_CELLS_BY_PDK. Known: {sorted(TECH_CELLS_BY_PDK)}"
        ) from None


def collect(repo: pathlib.Path, manifest_path: pathlib.Path,
            build_root: pathlib.Path, wrapper: str = DEFAULT_WRAPPER,
            pdk: str = DEFAULT_PDK) -> tuple[list[str], list[str]]:
    manifest = json.loads(manifest_path.read_text())
    vc = find_vc(build_root)
    work = vc.parent

    def resolve(value: str) -> pathlib.Path:
        p = pathlib.Path(value)
        return p if p.is_absolute() else (work / p).resolve()

    files: list[str] = []
    incs: list[str] = []
    for raw in vc.read_text().splitlines():
        value = raw.strip()
        if not value or value.startswith(("--", "-G", "-D", "-CFLAGS")):
            continue
        if value.startswith("+incdir+"):
            incs.append(str(resolve(value[len("+incdir+"):])))
            continue
        if value.startswith("-I"):
            incs.append(str(resolve(value[2:])))
            continue
        source = resolve(value)
        if source.suffix not in (".sv", ".v"):
            continue
        if not source.exists():
            raise SystemExit(f".vc references a missing source: {source}")
        if excluded(str(source)):
            continue
        files.append(str(source))

    for extra in (*EXTRA_SOURCES, *tech_cells(pdk), wrapper):
        path = (repo / extra).resolve()
        if not path.is_file():
            raise SystemExit(f"required source missing: {path}")
        files.append(str(path))

    # The generated tb include dir carries the config-rendered tb_util.svh.
    generated_root = pathlib.Path(manifest["generated_root"])
    ordered_incs = [str(generated_root / "tb/mosaic_soc")]
    ordered_incs += [str((repo / r).resolve())
                     for r in manifest["build"].get("include_roots", [])]
    ordered_incs += incs

    def uniq(seq):
        seen, out = set(), []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return uniq(files), uniq(ordered_incs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--build-root", required=True)
    ap.add_argument("--output", help="YAML fragment to write (default: stdout)")
    ap.add_argument("--pdk", default=DEFAULT_PDK,
                    help=f"technology clock gate to include "
                         f"(default: {DEFAULT_PDK})")
    ap.add_argument("--wrapper", default=DEFAULT_WRAPPER,
                    help="per-design delivery wrapper (default: Block A)")
    ap.add_argument("--verify", metavar="CONFIG",
                    help="compare against an existing config's VERILOG_FILES "
                         "and fail on any difference")
    args = ap.parse_args()

    repo = pathlib.Path(args.repo).resolve()
    files, incs = collect(repo, pathlib.Path(args.manifest),
                          pathlib.Path(args.build_root), wrapper=args.wrapper,
                          pdk=args.pdk)

    if args.verify:
        text = pathlib.Path(args.verify).read_text()
        section = text.split("VERILOG_FILES:")[1].split("\nVERILOG_INCLUDE_DIRS:")[0]
        reference = {l.strip()[2:] for l in section.splitlines()
                     if l.strip().startswith("- /")}
        # Compare by repo-relative tail: bundle hash and fusesoc dir differ by
        # design, and that is the whole point of this script.
        def tail(p: str) -> str:
            return re.sub(r".*/(build/src/|core-root/|generated/)", r"\1", p)
        got, want = {tail(f) for f in files}, {tail(f) for f in reference}
        missing, extra = want - got, got - want
        if missing or extra:
            for m in sorted(missing):
                print(f"  MISSING vs reference: {m}", file=sys.stderr)
            for e in sorted(extra):
                print(f"  EXTRA vs reference:   {e}", file=sys.stderr)
            print(f"verify FAILED: {len(missing)} missing, {len(extra)} extra",
                  file=sys.stderr)
            return 1
        print(f"verify OK: {len(files)} files match the reference list")
        return 0

    lines = [
        "# GENERATED by flow/librelane/scripts/gen_filelist.py -- do not edit.",
        "# Resolved from the FuseSoC .vc of the current build. The checked-in",
        "# config carries no absolute paths; this fragment supplies them at run",
        "# time so a fresh clone can harden without hand-editing 526 paths.",
        "VERILOG_FILES:",
    ]
    lines += [f"- {f}" for f in files]
    lines.append("VERILOG_INCLUDE_DIRS:")
    lines += [f"- {i}" for i in incs]
    text = "\n".join(lines) + "\n"

    if args.output:
        pathlib.Path(args.output).write_text(text)
        print(f"wrote {args.output}: {len(files)} files, {len(incs)} include dirs")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
