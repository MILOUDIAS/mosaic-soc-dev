#!/bin/bash
# Build + run the SoC-level TDU cocotb test (cocotb + Verilator).
# Runs the correct tap (SUB=1, TDU sees register offsets). Pass `bug` as an
# argument to also run the original buggy tap (SUB=0, full address) which the
# test is designed to catch.
# Usage: tb/tdu/soc/cocotb/run.sh [bug]

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
source "$HERE/../../../tools.sh"
mosaic_need_verilator

subs="1"
[ "${1:-}" = "bug" ] && subs="0 1"

rc=0
for sub in $subs; do
  echo "### ===== TDU SoC-level: tap SUB=$sub (1=offset/correct, 0=full-addr/bug) ====="
  rm -rf sim_build results.xml __pycache__
  make SIM=verilator SUB="$sub" || rc=1
done
exit $rc
