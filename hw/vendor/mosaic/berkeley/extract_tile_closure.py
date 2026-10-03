#!/usr/bin/env python3
"""extract_tile_closure.py — vendor the Rocket/BOOM tile closures out of a
chipyard split-verilog elaboration into hw/vendor/mosaic/berkeley/rtl/.

Both tiles come from ONE elaboration (CONFIG=MosaicRocketBoomConfig, see the
vendored MosaicConfigs.scala alongside this script) so every module name is
uniquified by a single firtool run — no cross-tree collisions when a MOSAIC
config instantiates both tiles in one Verilator build.

Method: seed with the RocketTile/BoomTile module definitions found in
gen-collateral, then chase instantiations to a fixpoint across
  * gen-collateral/*.sv          (firrtl modules, one per file)
  * <LONG>.top.mems.v            (synflop SRAM modules from MacroCompiler)
  * firrtl_black_box_resource_files.f  (plusarg_reader.v, EICG_wrapper.v, ...)

Usage:
  python3 extract_tile_closure.py \
      --build-dir  ~/chipyard-mosaic/sims/verilator/generated-src/chipyard.harness.TestHarness.MosaicRocketBoomConfig \
      --out        hw/vendor/mosaic/berkeley/rtl \
      --tiles      RocketTile BoomTile

Writes: one .sv per closure module into --out, and berkeley.f (this dir's
ordered filelist: EXT/blackbox modules first, then leaf-to-root closure).
"""

import argparse
import os
import re
import shutil
import sys

# module instantiation:  <ModuleName> <instname> ( ...  — SV keywords excluded.
# Also matches parameterized blackbox forms `<ModuleName> #(` (plusarg_reader):
# firtool monomorphizes firrtl modules but keeps verilog blackboxes generic.
INST_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+(?:[A-Za-z_][A-Za-z0-9_$]*\s*\(|#\()", re.M)
MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)", re.M)
SV_KEYWORDS = {
    "module", "input", "output", "inout", "wire", "reg", "logic", "assign",
    "always", "always_ff", "always_comb", "always_latch", "initial", "if",
    "else", "case", "casez", "casex", "for", "while", "begin", "end",
    "function", "task", "generate", "endgenerate", "genvar", "typedef",
    "struct", "enum", "union", "localparam", "parameter", "int", "integer",
    "bit", "byte", "posedge", "negedge", "or", "and", "not", "buf", "assert",
    "property", "endmodule", "return", "unique", "priority", "signed",
    "unsigned", "automatic", "else_if",
}


def split_verilog_modules(text):
    """Yield (module_name, module_text) for each module in a .v/.sv blob."""
    starts = [(m.start(), m.group(1)) for m in MODULE_RE.finditer(text)]
    for pos, name in starts:
        endpos = text.find("endmodule", pos)
        # take through the next 'endmodule' (synflop mems have no nesting)
        if endpos < 0:
            continue
        endpos += len("endmodule")
        yield name, text[pos:endpos]


# ── MOSAIC divergences from the raw firtool output ──────────────────────────
# chipyard folds the tile reset vector to the bootrom hang address (0x10000)
# at elaboration — the extracted tiles have NO reset-vector port. MOSAIC needs
# per-instance boot addresses (two workers, different programs), so the folded
# constant is re-parameterized: RESET_VECTOR threads tile -> frontend, and the
# SCI wrappers drive it with 0x8000_0000|BOOT_ADDR (the cacheable DRAM alias).
# Defaults keep the upstream value => zero divergence unless overridden.
MOSAIC_PATCHES = {
    "Frontend.sv": [
        ("module Frontend(",
         "module Frontend #(\n"
         "  // MOSAIC PATCH: re-parameterized boot address (folded 40'h10000 upstream)\n"
         "  parameter [39:0] RESET_VECTOR = 40'h10000\n"
         ") ("),
        ("s2_pc <= 40'h10000;",
         "s2_pc <= RESET_VECTOR;  // MOSAIC PATCH (was 40'h10000)"),
    ],
    "RocketTile.sv": [
        ("module RocketTile(",
         "module RocketTile #(\n"
         "  // MOSAIC PATCH: boot address, threaded to Frontend (see Frontend.sv)\n"
         "  parameter [39:0] RESET_VECTOR = 40'h10000\n"
         ") ("),
        ("  Frontend frontend (",
         "  Frontend #(.RESET_VECTOR(RESET_VECTOR)) frontend (  // MOSAIC PATCH"),
    ],
    "BoomFrontend.sv": [
        ("module BoomFrontend(",
         "module BoomFrontend #(\n"
         "  // MOSAIC PATCH: re-parameterized boot address (upstream encodes the\n"
         "  // first post-reset fetch PC as {23'h0, _GEN, 16'h0} == 0x10000)\n"
         "  parameter [39:0] RESET_VECTOR = 40'h10000\n"
         ") ("),
        # appears twice on the one-line s0_vpc mux (duplicated subexpression);
        # both ARE the boot PC
        ("{23'h0, _GEN, 16'h0}",
         "(_GEN ? RESET_VECTOR : 40'h0)", 2),
    ],
    "BoomTile.sv": [
        ("module BoomTile(",
         "module BoomTile #(\n"
         "  // MOSAIC PATCH: boot address, threaded to BoomFrontend\n"
         "  parameter [39:0] RESET_VECTOR = 40'h10000\n"
         ") ("),
        ("  BoomFrontend frontend (",
         "  BoomFrontend #(.RESET_VECTOR(RESET_VECTOR)) frontend (  // MOSAIC PATCH"),
    ],
}


def apply_mosaic_patches(out_dir):
    for fn, subs in MOSAIC_PATCHES.items():
        p = os.path.join(out_dir, fn)
        if not os.path.exists(p):
            sys.exit(f"ERROR: patch target {fn} not in closure")
        text = open(p).read()
        for sub in subs:
            old, new = sub[0], sub[1]
            want = sub[2] if len(sub) > 2 else 1
            n = text.count(old)
            if n != want:
                sys.exit(f"ERROR: patch anchor {old!r} found {n}x in {fn} (want {want})")
            text = text.replace(old, new)
        open(p, "w").write(text)
        print(f"patched: {fn} ({len(subs)} sites)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tiles", nargs="+", required=True,
                    help="tile module names (prefix match, e.g. RocketTile BoomTile)")
    args = ap.parse_args()

    bdir = os.path.expanduser(args.build_dir)
    gc = os.path.join(bdir, "gen-collateral")
    long_name = os.path.basename(bdir.rstrip("/"))

    # 1. index every module we could pull from
    mod_src = {}     # module name -> ('file', path) or ('blob', text)
    for fn in os.listdir(gc):
        if fn.endswith(".sv") or fn.endswith(".v"):
            mod_src.setdefault(os.path.splitext(fn)[0], ("file", os.path.join(gc, fn)))

    mems_v = os.path.join(bdir, f"{long_name}.top.mems.v")
    if not os.path.exists(mems_v):  # chipyard 1.14 emits it into gen-collateral
        mems_v = os.path.join(gc, f"{long_name}.top.mems.v")
    mem_mods = {}
    if os.path.exists(mems_v):
        for name, text in split_verilog_modules(open(mems_v).read()):
            mem_mods[name] = text
            mod_src.setdefault(name, ("blob", text))

    bb_f = os.path.join(gc, "firrtl_black_box_resource_files.f")
    bb_files = {}
    if os.path.exists(bb_f):
        for line in open(bb_f):
            line = line.strip()
            if not line:
                continue
            p = line if os.path.isabs(line) else os.path.join(gc, line)
            if os.path.exists(p):
                for name, _ in split_verilog_modules(open(p).read()):
                    bb_files[name] = p
                    mod_src.setdefault(name, ("file", p))

    # 2. seed: tile modules (exact or uniquified-prefix match)
    seeds = []
    for want in args.tiles:
        cands = [m for m in mod_src if m == want or m.startswith(want)]
        if not cands:
            sys.exit(f"ERROR: no module matching '{want}' in {gc}")
        # exact name wins; otherwise shortest prefixed (RocketTile over RocketTileWrap?)
        cands.sort(key=lambda m: (m != want, len(m)))
        seeds.append(cands[0])
        print(f"seed: {want} -> {cands[0]}")

    # 3. fixpoint closure over instantiations
    closure, work = set(), list(seeds)
    while work:
        mod = work.pop()
        if mod in closure or mod not in mod_src:
            continue
        closure.add(mod)
        kind, src = mod_src[mod]
        text = open(src).read() if kind == "file" else src
        for m in INST_RE.finditer(text):
            child = m.group(1)
            if child in SV_KEYWORDS or child == mod:
                continue
            if child in mod_src and child not in closure:
                work.append(child)

    print(f"closure: {len(closure)} modules")

    # 4. emit
    out = os.path.expanduser(args.out)
    os.makedirs(out, exist_ok=True)
    emitted = []
    for mod in sorted(closure):
        kind, src = mod_src[mod]
        dst = os.path.join(out, mod + ".sv")
        if kind == "file":
            shutil.copyfile(src, dst)
        else:
            with open(dst, "w") as f:
                f.write("// extracted from " + os.path.basename(mems_v) + "\n")
                f.write(src + "\n")
        emitted.append(mod + ".sv")

    apply_mosaic_patches(out)

    # 5. ordered filelist: blackboxes first (plusarg_reader etc.), then the rest
    bbs = sorted(m + ".sv" for m in closure if m in bb_files)
    rest = sorted(f for f in emitted if f not in bbs)
    with open(os.path.join(out, "..", "berkeley.f"), "w") as f:
        f.write("# hw/vendor/mosaic/berkeley/berkeley.f — generated by "
                "extract_tile_closure.py\n")
        f.write(f"# source: chipyard 1.14.0 CONFIG={long_name.split('.')[-1]}\n")
        f.write(f"# tiles: {' '.join(args.tiles)}  modules: {len(closure)}\n")
        for fn in bbs + rest:
            f.write(f"hw/vendor/mosaic/berkeley/rtl/{fn}\n")
    print(f"wrote {len(emitted)} files to {out} + berkeley.f")


if __name__ == "__main__":
    main()
