#!/bin/bash
# TIMING-ANNOTATED gate-level simulation of the Block A macro, using CVC.
#
# Why CVC and not Icarus: the GF180 cell models use `ifnone` with edge-sensitive
# specify paths. iverilog rejects them outright ("sorry: ifnone with an
# edge-sensitive path is not supported"), so under Icarus the models must be
# compiled -DFUNCTIONAL -- which strips the very paths SDF would annotate. CVC
# (OSS CVC 7.00b, IEEE 1364-2005) compiles them, so this run has real delays AND
# live setup/hold timing checks in the cell models.
#
# Two things this gives that STA does not: the design is exercised FUNCTIONALLY
# with delays in place, and a timing-check violation shows up as an X through
# the model's notifier, which then propagates and fails the run.
#
# Power-up is modelled with +random_2state=<seed>: all state starts at a random
# but definite 0/1, which is what silicon does. A different seed is a different
# power-up state, so re-running with several seeds is a real test of reset
# adequacy -- worth doing on a design where 4 081 of 5 587 flops have no reset.
#
# Usage:
#   ./run_gls_cvc.sh                        slow corner (max_ss_125C_4v50)
#   ./run_gls_cvc.sh min_tt_025C_5v00       another corner
#   GLS_SEED=12345 ./run_gls_cvc.sh         a different power-up state
#   GLS_NO_SDF=1 ./run_gls_cvc.sh           delays but no back-annotation
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"

RUN="${GLS_RUN:-$REPO/flow/librelane/experimental/runs/blocka_signoff}"
PDK="$REPO/flow/librelane/gf180mcu/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog"
NETLIST="${GLS_NETLIST:-$RUN/final/pnl/mosaic_block_a.pnl.v}"
CORNER="${1:-max_ss_125C_4v50}"
SEED="${GLS_SEED:-1}"
MAXCYCLES="${GLS_MAXCYCLES:-200000}"

command -v cvc >/dev/null || { echo "ERROR: cvc not found on PATH" >&2; exit 2; }
[ -f "$NETLIST" ] || { echo "ERROR: netlist missing: $NETLIST" >&2; exit 2; }

SDF="$RUN/final/sdf/$CORNER/mosaic_block_a__$CORNER.sdf"
SDF_ARG=""
DELAY_SEL="+maxdelays"
if [ "${GLS_NO_SDF:-0}" = "1" ]; then
  echo "### no SDF back-annotation (cell delays only)"
else
  [ -f "$SDF" ] || { echo "ERROR: no SDF at $SDF" >&2; ls "$RUN/final/sdf" >&2; exit 2; }
  SDF_ARG="+sdf=$SDF"
  # min corners carry the fast delays used for hold; pick the matching triplet
  # value so a min-corner run is not silently simulated with max delays.
  case "$CORNER" in
    min_*) DELAY_SEL="+mindelays" ;;
    nom_*) DELAY_SEL="+typdelays" ;;
    *)     DELAY_SEL="+maxdelays" ;;
  esac
  echo "### SDF: $CORNER  ($DELAY_SEL)"
fi

FW="${GLS_FIRMWARE:-}"
if [ -z "$FW" ]; then
  FW="$(ls -t "$REPO"/build/mosaic/mosaic_tapeout_ultra-*/generated/generic_fw/generic.hex 2>/dev/null | head -1)"
fi
[ -n "$FW" ] && [ -f "$FW" ] || { echo "ERROR: no firmware hex; set GLS_FIRMWARE." >&2; exit 2; }

echo "### netlist : $(basename "$NETLIST")"
echo "### firmware: $FW"
echo "### power-up: +random_2state=$SEED"

# -v marks the CELL library so its specify paths are USED: compiled as ordinary
# source they are discarded ("specify paths in top level module ... ignored").
# primitives.v must NOT be -v -- CVC's library scan indexes modules, not UDPs,
# and reports the eight udp_*_ff/latch primitives as unresolved.
# +notimingchecks is deliberately NOT passed: the checks are the point.
# shellcheck disable=SC2086
cvc +define+USE_POWER_PINS \
    +random_2state=$SEED \
    $DELAY_SEL \
    +sdfverbose \
    -o "$HERE/gls_cvc.exe" \
    "$HERE/gls_tb_cvc.v" \
    "$NETLIST" \
    "$HERE/spiflash_v2001.v" \
    "$PDK/primitives.v" \
    -v "$HERE/gf180mcu_cells_cvc.v" \
    > "$HERE/compile-cvc.log" 2>&1
if [ ! -x "$HERE/gls_cvc.exe" ]; then
  echo "### COMPILE FAILED — see $HERE/compile-cvc.log"
  grep -E "ERROR" "$HERE/compile-cvc.log" | head -15
  exit 1
fi
echo "### compiled ($(grep -c 'ERROR' "$HERE/compile-cvc.log") errors, $(grep -c 'WARN' "$HERE/compile-cvc.log") warnings)"

# shellcheck disable=SC2086
"$HERE/gls_cvc.exe" +firmware="$FW" +maxcycles="$MAXCYCLES" $SDF_ARG \
    2>&1 | tee "$HERE/sim-gls-cvc.log" | grep -vE "^\[GLS-CVC\] [0-9]+ cycles" | tail -25
SIM_RC=${PIPESTATUS[0]}

echo
# Timing-check violations are the reason this run exists, so surface them
# whether or not the firmware reached its exit code.
VIOL=$(grep -cE "Timing violation|timing violation" "$HERE/sim-gls-cvc.log" 2>/dev/null || echo 0)
echo "### timing-check violations reported: $VIOL"
[ "$VIOL" -gt 0 ] && grep -E "Timing violation|timing violation" "$HERE/sim-gls-cvc.log" | head -5

if [ "$SIM_RC" -eq 0 ] && grep -q "EXIT SUCCESS" "$HERE/sim-gls-cvc.log" && [ "$VIOL" -eq 0 ]; then
  echo "### RESULT: timing-annotated GLS PASSED ($CORNER)"
else
  echo "### RESULT: timing-annotated GLS FAILED (see $HERE/sim-gls-cvc.log)"
  exit 1
fi
