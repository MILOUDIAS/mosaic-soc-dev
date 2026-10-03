#!/bin/bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE/../.."
source tb/tools.sh
mosaic_need_verilator
rm -rf "$HERE/obj_diag"
verilator --binary -j 0 --top-module mosaic_tb --Mdir "$HERE/obj_diag" -o Vmosaic_tb \
    --timescale 1ns/1ps \
    -f "$HERE/soc_diag.f" > "$HERE/build_diag.log" 2>&1
if [ -x "$HERE/obj_diag/Vmosaic_tb" ]; then
  echo "BUILD OK; running..."
  timeout 120 "$HERE/obj_diag/Vmosaic_tb" +firmware="$HERE/prog/prog.hex" > "$HERE/sim_diag.log" 2>&1
  grep -E "trc c|mosaic_tb|SRAM|TITAN|reached|wrote|EXIT|core_sleep|fetch_enable" "$HERE/sim_diag.log" | head -60
else
  echo "BUILD FAILED"; grep -iE "%Error|error:|undefined" "$HERE/build_diag.log" | head
fi
