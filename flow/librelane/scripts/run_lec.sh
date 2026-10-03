#!/usr/bin/env bash
# Sequential equivalence check: the routed netlist against the RTL it came from.
#
#   LEC_RUN=<runs/tag> ./run_lec.sh
#
# WHY SEC AND NOT LEC
# -------------------
# kepler-formal's `-v lec` is gate-level combinational. Here design1 is
# SystemVerilog RTL and design2 is a gate netlist, and the tool refuses that
# combination outright:
#   "SystemVerilog input formats require SEC verification (-v sec ...)"
# which is correct -- comparing RTL to gates is a sequential problem.
#
# STATUS ON A REAL DESIGN: BLOCKED UPSTREAM, 2026-08-13
# ------------------------------------------------------
# This script had never been run on an actual design -- only on a toy pair
# built to measure the exit-code trap below. Pointed at mosaic_block_a it hit
# four load failures in sequence. Three are fixed here (one liberty corner, the
# wrapper's PDK cell models, +define+FUNCTIONAL, and slang flags forwarded
# through the flist). The fourth is not ours to fix:
#
#   Netlist loading failed: Unsupported SystemVerilog elements encountered (106)
#     gf180mcu_fd_sc_mcu7t5v0.v:5226:6  PrimitiveInstance 'MGM_BG_0'
#         -- the bufz cell is built from Verilog gate primitives
#     servile_mux.v:96:3                initial block, target not lowered as a
#         -- register
#
# naja's SystemVerilog importer does not lower gate primitives or these initial
# blocks. So SEC cannot currently answer anything about this design, and the
# `lec` gate will report INCONCLUSIVE -- which is correct, and blocks, exactly
# as a formal run that did not finish should.
#
# THE VERDICT IS THE MARKER, NOT THE EXIT STATUS
# ----------------------------------------------
# kepler-formal exits 0 whether it proves equivalence or finds a
# counterexample. Measured, on a deliberately inequivalent pair. This script
# therefore does not gate on $?; harness/evidence/lec.py reads the log.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
RUN="${LEC_RUN:-$REPO/flow/librelane/experimental/runs/blocka_signoff}"
TAG="$(basename "$RUN")"
OUT="$REPO/build/lec"
mkdir -p "$OUT"
FLOW_PDK="${LEC_PDK_REF:-$REPO/flow/librelane/gf180mcu/gf180mcuD/libs.ref}"

NETLIST="${LEC_NETLIST:-$RUN/final/nl/$(ls "$RUN/final/nl" 2>/dev/null | head -1)}"
[ -f "$NETLIST" ] || { echo "ERROR: no netlist under $RUN/final/nl" >&2; exit 2; }

DESIGN="$(python3 -c "
import json,sys
print((json.load(open('$RUN/resolved.json')) or {}).get('DESIGN_NAME',''))" 2>/dev/null)"
[ -n "$DESIGN" ] || { echo "ERROR: no DESIGN_NAME in $RUN/resolved.json" >&2; exit 2; }

# The RTL the run actually consumed, from its own resolved config -- not a
# filelist regenerated now, which could describe different sources than the
# ones that were synthesised.
FLIST="$OUT/$TAG.rtl.f"
# The delivery wrapper instantiates PDK cells directly -- pad drivers, e.g.
# `gf180mcu_fd_sc_mcu7t5v0__bufz_4 u_pad_drv` in mosaic_block_a.sv -- so the
# RTL side does not elaborate from the design sources alone:
#
#   mosaic_block_a.sv:97:5: error: unknown module 'gf180mcu_fd_sc_mcu7t5v0__bufz_4'
#
# The netlist side gets those cells from liberty; the RTL side needs their
# Verilog. This is the same model set tb/gls compiles, and -DFUNCTIONAL matches
# how GLS reads them: specify blocks dropped, which for an equivalence question
# is irrelevant anyway since timing arcs are not logic.
#
# Standard cells only. The IO models are NOT included: nothing in the wrappers
# instantiates one (`grep -oE 'gf180mcu_[a-z0-9_]+' mosaic_block_a.sv` yields
# exactly `gf180mcu_fd_sc_mcu7t5v0__bufz_4`), and gf180mcu_fd_io.v opens with
# Verilog-XL directives slang rejects outright:
#
#   gf180mcu_fd_io.v:17:1: error: unknown macro or compiler directive
#                                 '`suppress_faults'
#
# Adding them to fix an unknown-module error would trade one load failure for
# a thousand parse errors.
CELL_MODELS="$(ls "$FLOW_PDK"/gf180mcu_fd_sc_mcu7t5v0/verilog/gf180mcu_fd_sc_mcu7t5v0.v \
                  "$FLOW_PDK"/gf180mcu_fd_sc_mcu7t5v0/verilog/primitives.v 2>/dev/null || true)"
python3 - "$RUN/resolved.json" "$FLIST" "$CELL_MODELS" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
files = config.get("VERILOG_FILES") or []
models = [m for m in (sys.argv[3] if len(sys.argv) > 3 else "").split() if m]
with open(sys.argv[2], "w") as handle:
    # The same PDK quirk that stops timing-annotated GLS under Icarus stops
    # slang parsing the models here, with the same message:
    #   error: ifnone specify path cannot be an edge-sensitive path
    # and it has the same fix. FUNCTIONAL drops the specify blocks. For an
    # equivalence check that costs nothing -- timing arcs are not logic.
    if models:
        handle.write("+define+FUNCTIONAL\n")
    # The flist forwards unknown tokens to slang, which is how LibreLane's own
    # SLANG_ARGUMENTS get applied here. Without them the x-heep RTL will not
    # parse under kepler-formal's stricter defaults:
    #   ao_peripheral_subsystem.sv:290:15: error: identifier 'ao_periph_req'
    #                                             used before its declaration
    # `--ignore-assertions` is deliberately absent: LibreLane passes it, this
    # slang build rejects it as an unknown argument.
    for flag in ("--allow-use-before-declare", "--relax-enum-conversions",
                 "--relax-string-conversions", "--allow-hierarchical-const",
                 "--compat", "vcs"):
        handle.write(f"{flag}\n")
    for entry in config.get("VERILOG_INCLUDE_DIRS") or []:
        handle.write(f"+incdir+{entry}\n")
    for entry in models:
        handle.write(f"{entry}\n")
    for entry in files:
        handle.write(f"{entry}\n")
print(f"### rtl     : {len(files)} files + {len(models)} cell models -> {sys.argv[2]}")
PY

# ONE corner only. `liberty_files` returns all nine (three libraries x three
# corners) because that is what power analysis wants; feeding all of them to
# kepler-formal fails outright:
#
#   Netlist loading failed: NLLibrary gf180mcu_fd_sc_mcu7t5v0__ff_n40C_5v50
#   contains already a SNLDesign named: gf180mcu_fd_sc_mcu7t5v0__addf_1
#
# Equivalence is a question about LOGIC, so the corner is irrelevant to the
# answer -- the liberty is here only to tell the loader what the cells do. The
# same one-corner filter is in harness/physical/netlist.py for najaeda, which
# rejects the duplicates the same way.
LIBS=$(python3 -c "
import sys; sys.path.insert(0, '$REPO')
from harness.physical.power import liberty_files
from pathlib import Path
libs = [p for p in liberty_files(Path('$RUN')) if '${LEC_CORNER:-tt_025C}' in p.name]
if not libs:
    raise SystemExit('no liberty matching ${LEC_CORNER:-tt_025C} under $RUN')
print(' '.join(str(p) for p in libs))")
[ -n "$LIBS" ] || { echo "ERROR: no liberty for LEC" >&2; exit 2; }

echo "### run     : $RUN"
echo "### design  : $DESIGN"
echo "### netlist : $NETLIST"
echo "### engine  : ${LEC_ENGINE:-pdr}, k<=${LEC_K:-10}"
echo "### log     : $OUT/$TAG.log"

# shellcheck disable=SC2086
timeout "${LEC_TIMEOUT:-7200}" nix shell github:fossi-foundation/nix-eda#kepler-formal \
    --command kepler-formal -sv -v sec \
    --sv_design1_flist "$FLIST" --sv_design1_top "$DESIGN" \
    --design2 "$NETLIST" \
    --liberty $LIBS \
    -k "${LEC_K:-10}" --sec-engine "${LEC_ENGINE:-pdr}" \
    > "$OUT/$TAG.log" 2>&1 || true

echo
PYTHONPATH="$REPO" python3 - "$OUT/$TAG.log" <<'PY'
import sys
from harness.evidence.lec import parse_lec_log
result = parse_lec_log(open(sys.argv[1]).read())
print(f"### RESULT: {result.status.value.upper()}"
      + (f"  coverage {result.coverage_pct:.2f}% "
         f"({result.outputs_checked}/{result.outputs_total})"
         if result.coverage_pct is not None else ""))
for reason in result.reasons:
    print(f"###   {reason}")
# EXIT SUCCESS is the marker the flow gate greps for, matching every other
# testbench flow in this project.
if result.status.value == "proven":
    print("### RESULT: EXIT SUCCESS - netlist proved equivalent to its RTL")
PY
