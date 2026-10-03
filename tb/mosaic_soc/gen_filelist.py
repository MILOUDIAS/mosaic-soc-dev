#!/usr/bin/env python3
"""Assemble a Verilator build filelist for the MOSAIC full-SoC functional sim.

Manifest mode starts from the config-specific FuseSoC export and trusts that
resolved graph directly.  It adds only the pure-SV test top, the functional
CVE2 clock gate, and the one ordered Berkeley closure not yet represented by
berkeley.core.  No live-tree remapping or broad ``-y`` repair is performed.

The positional-only invocation is retained as a legacy compatibility path. It
starts from the old shared sim-verilator .vc and applies the historical live
source remaps/fixes:
  - core-v-mini-mcu/hw/* and the tb/* build-copies -> live hw/* and tb/* sources
  - drop the stale idma build copies + pulp obi_pkg.sv (duplicate-package fix)
  - add -y for sci / serv / fazyrv / live-idma / cv32e40x (if_xif) / ams, plus
    idma_reg_top.sv (module name != filename) and the obi/idma include roots
Emits a verilator command file on stdout (incdirs, CFLAGS, waivers, .sv/.v, .cpp).
"""

import argparse
import json
import os
from pathlib import Path
import sys

_WARNINGS = [
    "-Wno-fatal",
    "-Wno-WIDTH",
    "-Wno-UNUSEDSIGNAL",
    "-Wno-UNDRIVEN",
    "-Wno-UNUSEDPARAM",
    "-Wno-DECLFILENAME",
    "-Wno-TIMESCALEMOD",
    "-Wno-PINMISSING",
    "-Wno-CASEINCOMPLETE",
    "-Wno-SYMRSVDWORD",
    "-Wno-GENUNNAMED",
    "-Wno-WIDTHEXPAND",
    "-Wno-WIDTHTRUNC",
    "-Wno-UNOPTFLAT",
    "-Wno-MULTIDRIVEN",
    "-Wno-LATCH",
    "-Wno-BLKANDNBLK",
    "-Wno-IMPLICIT",
    "-Wno-REALCVT",
    "-Wno-COMBDLY",
    "-Wno-STMTDLY",
    "-Wno-INITIALDLY",
    "-Wno-MISINDENT",
]


def _unique(items):
    return list(dict.fromkeys(items))


def _find_vc(build_root: Path) -> Path:
    candidates = sorted(build_root.rglob("*.vc"))
    preferred = [
        path
        for path in candidates
        if "core-v-mini-mcu" in path.name and "sim-verilator" in str(path.parent)
    ]
    if len(preferred) == 1:
        return preferred[0]
    if not preferred and len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(
        f"expected one core-v-mini-mcu sim .vc below {build_root}, found: "
        + ", ".join(str(path) for path in candidates)
    )


def _emit_manifest_filelist(repo: Path, manifest_path: Path, build_root: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    vc = _find_vc(build_root)
    work = vc.parent

    incs, cflags, waivers, vfiles, cppfiles = [], [], [], [], []

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (work / path).resolve()

    for raw in vc.read_text().splitlines():
        value = raw.strip()
        if not value or value.startswith(("--", "-G", "-D")):
            continue
        if value.startswith("-CFLAGS"):
            tokens = value.split()
            fixed = []
            for token in tokens:
                if token.startswith("-I") and not Path(token[2:]).is_absolute():
                    fixed.append("-I" + str(resolve(token[2:])))
                else:
                    fixed.append(token)
            cflags.append(" ".join(fixed))
            continue
        if value.startswith("+incdir+"):
            incs.append(str(resolve(value[len("+incdir+") :])))
            continue
        if value.startswith("-I"):
            incs.append(str(resolve(value[2:])))
            continue

        source = resolve(value)
        if not source.exists():
            raise FileNotFoundError(f"FuseSoC .vc references missing source: {source}")
        if source.name == "cve2_clock_gate.sv":
            continue
        if source.suffix == ".vlt":
            waivers.append(str(source))
        elif source.suffix in (".cpp", ".cc", ".c"):
            if source.name not in ("tb_top.cpp", "XHEEP_CmdLineOptions.cpp"):
                cppfiles.append(str(source))
        elif source.suffix in (".sv", ".v"):
            vfiles.append(str(source))

    output = list(_WARNINGS)
    # Keep the config-rendered, DPI-export-free tb_util.svh first. Its SRAM
    # hierarchy is topology dependent; using the live-tree copy can reference
    # banks that do not exist in this isolated bundle.
    generated_root = Path(manifest["generated_root"])
    output.extend(
        [
            "-I" + str(generated_root / "tb/mosaic_soc"),
            "-I" + str(repo / "tb/mosaic_soc"),
            "-I" + str(repo / "tb"),
        ]
    )
    for include_root in manifest["build"].get("include_roots", []):
        output.append("-I" + str((repo / include_root).resolve()))
    output.extend("-I" + path for path in _unique(incs))
    output.extend(_unique(cflags))
    output.extend(_unique(waivers))

    # This is the sole graph escape hatch.  Berkeley's one-elaboration closure
    # must retain its generated order until berkeley.core lists the 299 files.
    for fragment in manifest["build"].get("ordered_filelist_fallbacks", []):
        fragment_path = (repo / fragment).resolve()
        for raw in fragment_path.read_text().splitlines():
            entry = raw.strip()
            if entry and not entry.startswith("#"):
                source = Path(entry)
                if not source.is_absolute():
                    source = repo / source
                if not source.is_file():
                    raise FileNotFoundError(
                        f"ordered filelist {fragment_path} references missing {source}"
                    )
                output.append(str(source.resolve()))

    flags = set(manifest["build"].get("flags", []))
    if "mosaic_cv32e20" in flags:
        output.append(str((repo / "tb/mosaic_soc/cve2_clock_gate.sv").resolve()))
    output.append(str((repo / "tb/tb_top.sv").resolve()))
    output.extend(_unique(vfiles))
    output.extend(_unique(cppfiles))

    sys.stdout.write("\n".join(_unique(output)) + "\n")
    sys.stderr.write(
        f"[gen_filelist] manifest={manifest['build_key']} vc={vc} "
        f"verilog={len(_unique(vfiles))} cpp={len(_unique(cppfiles))} "
        f"waivers={len(_unique(waivers))} incs={len(_unique(incs))}\n"
    )


if "--manifest" in sys.argv or "--build-root" in sys.argv:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--build-root", required=True)
    options = parser.parse_args()
    _emit_manifest_filelist(
        Path(options.repo).resolve(),
        Path(options.manifest).resolve(),
        Path(options.build_root).resolve(),
    )
    raise SystemExit(0)

REPO = sys.argv[1]
SIMV = os.path.join(
    REPO, "build/openhwgroup.org_systems_core-v-mini-mcu_1.0.5/sim-verilator"
)
VC = os.path.join(SIMV, "openhwgroup.org_systems_core-v-mini-mcu_1.0.5.vc")
CMM = "openhwgroup.org_systems_core-v-mini-mcu_1.0.5/hw/"
CMM_TB = "openhwgroup.org_systems_core-v-mini-mcu_1.0.5/tb/"
TBUTILS = "x-heep__tb-utils_0/"


def res(p):
    return os.path.normpath(os.path.join(SIMV, p))


incs, cflags, waivers, vfiles, cppfiles = [], [], [], [], []
for ln in open(VC):
    s = ln.strip()
    if not s or s.startswith("--") or s.startswith("-G") or s.startswith("-D"):
        continue
    if s.startswith("-CFLAGS"):
        # Resolve relative '-I../...' include dirs to absolute (they are relative
        # to the sim-verilator dir in the .vc, but the C++ compiler runs from
        # obj_dir, where '../src' would not exist) so DPI C models find their
        # cross-directory headers (e.g. sim_jtag.c -> remote_bitbang.h).
        toks = s.split()
        fixed = []
        for t in toks:
            if t.startswith("-I") and not os.path.isabs(t[2:]):
                fixed.append("-I" + res(t[2:]))
            else:
                fixed.append(t)
        cflags.append(" ".join(fixed))
        continue
    if s.startswith("+incdir+"):
        incs.append(res(s[len("+incdir+") :]))
        continue
    ap = res(s)
    # obi_fifo lives under its own VLNV (x-heep_ip_obi_fifo_0), so the CMM remap
    # misses it -> point at the live (fixed) source explicitly.
    if os.path.basename(ap) == "obi_fifo.sv":
        ap = os.path.join(REPO, "hw/ip/obi_fifo/obi_fifo.sv")
    # core_v_mini_mcu_pkg.sv is a CONFIG-DEPENDENT generated package (NUM_HARTS,
    # CORE*_IDX) shipped under a separate VLNV (x-heep__packages_0) as a STALE
    # build-copy. The CMM remap misses it, so the build would use the old
    # NUM_HARTS while the live hw/ files use the regenerated value -> array-size
    # mismatch. Point at the freshly-generated live package.
    elif os.path.basename(ap) == "core_v_mini_mcu_pkg.sv":
        ap = os.path.join(REPO, "hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv")
    # The FlooNoC fabric top + pkg are CONFIG-DEPENDENT generated files
    # (floogen via floonoc_gen.py, incl. the router-map patch) — same staleness
    # hazard as core_v_mini_mcu_pkg.sv: point at the live generated sources.
    elif os.path.basename(ap) in ("floo_mosaic_noc.sv", "floo_mosaic_noc_pkg.sv"):
        ap = os.path.join(REPO, "hw/ip/floonoc_fabric", os.path.basename(ap))
    # remap build-copies -> live
    elif CMM in ap:
        ap = os.path.normpath(os.path.join(REPO, "hw", ap.split(CMM, 1)[1]))
    elif CMM_TB in ap:
        ap = os.path.normpath(os.path.join(REPO, "tb", ap.split(CMM_TB, 1)[1]))
    elif TBUTILS in ap:  # x-heep__tb-utils_0/* -> live tb/*
        ap = os.path.normpath(os.path.join(REPO, "tb", ap.split(TBUTILS, 1)[1]))
    # drop stale idma copies + pulp obi_pkg (collision)
    if "mosaic_ip_idma_0" in ap:
        continue
    if ap.replace("\\", "/").endswith("__obi_0.1.2/src/obi_pkg.sv"):
        continue
    # drop the vendored latch-based cve2 clock gate — replaced by the always-on
    # functional-sim override below (the latch gate keeps TITAN asleep at POR).
    if ap.replace("\\", "/").endswith("cve2_clock_gate.sv"):
        continue
    if not os.path.exists(ap):
        continue
    if ap.endswith(".vlt"):
        waivers.append(ap)
    elif ap.endswith((".cpp", ".cc", ".c")):
        # Keep the DPI-import C models (uartdpi + the JTAG DPI, whose `jtag_tick`
        # is imported by testharness unconditionally). Drop ONLY the C++ main
        # (tb_top.cpp / XHEEP_CmdLineOptions.cpp) — we drive the sim from the
        # pure-SV tb_top.sv with --binary (Verilator generates its own main).
        if os.path.basename(ap) not in ("tb_top.cpp", "XHEEP_CmdLineOptions.cpp"):
            cppfiles.append(ap)
    elif ap.endswith((".sv", ".v")):
        vfiles.append(ap)


def dd(xs):
    return list(dict.fromkeys(xs))


incs = [i for i in dd(incs) if "mosaic_ip_idma_0" not in i]
waivers, vfiles, cppfiles = dd(waivers), dd(vfiles), dd(cppfiles)

out = []
# warnings (match the proven clean-elaboration set; UNOPTFLAT for FazyRV carry chains)
for w in [
    "-Wno-fatal",
    "-Wno-WIDTH",
    "-Wno-UNUSEDSIGNAL",
    "-Wno-UNDRIVEN",
    "-Wno-UNUSEDPARAM",
    "-Wno-DECLFILENAME",
    "-Wno-TIMESCALEMOD",
    "-Wno-PINMISSING",
    "-Wno-CASEINCOMPLETE",
    "-Wno-SYMRSVDWORD",
    "-Wno-GENUNNAMED",
    "-Wno-WIDTHEXPAND",
    "-Wno-WIDTHTRUNC",
    "-Wno-UNOPTFLAT",
    "-Wno-MULTIDRIVEN",
    "-Wno-LATCH",
    "-Wno-BLKANDNBLK",
    "-Wno-IMPLICIT",
    "-Wno-REALCVT",
    "-Wno-COMBDLY",
    "-Wno-STMTDLY",
    "-Wno-INITIALDLY",
    "-Wno-MISINDENT",
]:
    out.append(w)
# -y search dirs for modules the stale .vc omits / that we remapped to live
for y in [
    "hw/sci",
    "hw/vendor/mosaic/hazard3/rtl",
    "hw/vendor/mosaic/hazard3/rtl/arith",
    "hw/vendor/mosaic/hazard3/rtl/debug",
    "hw/vendor/mosaic/serv/rtl",
    "hw/vendor/mosaic/serv/servile",
    "hw/vendor/mosaic/fazyrv/rtl",
    "hw/vendor/mosaic/picorv32",
    "hw/vendor/mosaic/snitch/rtl",
    "hw/vendor/mosaic/idma/rtl",
    "hw/vendor/mosaic/idma/rtl/backend",
    "hw/vendor/mosaic/idma/rtl/midend",
    "hw/vendor/mosaic/idma/rtl/frontend",
    "hw/vendor/mosaic/idma",
    "hw/ip_examples/ams/rtl",
    "hw/vendor/openhwgroup/cv32e40x/rtl",
    # classic fixed-latency logarithmic interconnect (bus: log configs)
    "hw/vendor/xheep/cluster_interconnect/rtl/tcdm_interconnect",
]:
    out.append("-y " + os.path.join(REPO, y))
out.append("-I" + os.path.join(REPO, "hw/vendor/pulp_platform/obi/include"))
out.append("-I" + os.path.join(REPO, "hw/vendor/mosaic/idma/rtl/include"))
# tb/mosaic_soc FIRST so testharness's `include "tb_util.svh"` resolves to the
# DPI-export-free shadow (dodges the Verilator tb_loadHEX codegen bug), then tb/.
out.append("-I" + os.path.join(REPO, "tb/mosaic_soc"))
out.append("-I" + os.path.join(REPO, "tb"))
for i in incs:
    out.append("-I" + i)
out += cflags
for w in waivers:
    out.append(w)
# module idma_reg32_3d lives in idma_reg_top.sv (filename != module) -> add explicitly
out.append(os.path.join(REPO, "hw/vendor/mosaic/idma/rtl/idma_reg_top.sv"))
# tcdm_interconnect_pkg: packages are not resolved via -y -> add explicitly
out.append(
    os.path.join(
        REPO,
        "hw/vendor/xheep/cluster_interconnect/rtl/tcdm_interconnect/tcdm_interconnect_pkg.sv",
    )
)
# snitch: packages (riscv_instr, snitch_pkg) + module snitch_regfile lives in
# snitch_regfile_ff.sv (filename != module) -> add explicitly
out.append(os.path.join(REPO, "hw/vendor/mosaic/snitch/rtl/riscv_instr.sv"))
out.append(os.path.join(REPO, "hw/vendor/mosaic/snitch/rtl/snitch_pkg.sv"))
out.append(os.path.join(REPO, "hw/vendor/mosaic/snitch/rtl/snitch_regfile_ff.sv"))
# cva6 (mosaic:ip:cva6): package-heavy — explicit ordered fragment, appended
# ONLY when the generated cpu_subsystem actually instantiates cva6_sci (keeps
# ~80 files out of non-cva6 builds; sim-only core, excluded from tapeout)
_cpu_ss = os.path.join(REPO, "hw/core-v-mini-mcu/cpu_subsystem.sv")
_cpu_ss_txt = open(_cpu_ss).read() if os.path.exists(_cpu_ss) else ""
if "cva6_sci" in _cpu_ss_txt:
    out.append("-I" + os.path.join(REPO, "hw/vendor/mosaic/cva6/core/include"))
    with open(os.path.join(REPO, "hw/vendor/mosaic/cva6/cva6.f")) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#"):
                out.append(os.path.join(REPO, _line))
# rocket/boom (mosaic:ip:berkeley): extracted chipyard tile closures — same
# config-gating; both tiles share one vendored tree (single elaboration,
# collision-free namespace). The TL->OBI bridge is appended here too: the
# sci wrappers reach Verilator via `-y hw/sci` (mosaic:ip:sci is NOT in the
# top-level FuseSoC graph — verified: the .vc stages no *_sci files), so the
# sci.core -> berkeley -> tl_obi dep chain never delivers it.
if ("rocket_sci" in _cpu_ss_txt) or ("boom_sci" in _cpu_ss_txt):
    out.append(os.path.join(REPO, "hw/vendor/mosaic/tl_obi/xheep_tilelink_to_obi.sv"))
    with open(os.path.join(REPO, "hw/vendor/mosaic/berkeley/berkeley.f")) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#"):
                out.append(os.path.join(REPO, _line))
# functional-sim cve2 clock-gate override (replaces the dropped vendored latch gate)
out.append(os.path.join(REPO, "tb/mosaic_soc/cve2_clock_gate.sv"))
# the pure-SV testbench top (the .vc uses tb_top.cpp instead, so add it explicitly)
out.append(os.path.join(REPO, "tb/tb_top.sv"))
for f in vfiles:
    out.append(f)
for c in cppfiles:
    out.append(c)

sys.stdout.write("\n".join(out) + "\n")
sys.stderr.write(
    f"[gen_filelist] verilog={len(vfiles)} cpp={len(cppfiles)} waivers={len(waivers)} "
    f"incs={len(incs)} cflags={len(cflags)}\n"
)
