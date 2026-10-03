#!/bin/bash
# tb/tl_obi/run.sh — self-checking unit TB for the TileLink->OBI bridge
# (mosaic_tilelink_to_obi, used by the Rocket/BOOM SCI wrappers).
#
# Pass criterion: "ALL TESTS PASSED" on stdout.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)

cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_verilator
rm -rf build/tl_obi_tb_obj
verilator --binary -j 0 --timescale 1ns/1ps --top-module tl_obi_tb \
  --Mdir build/tl_obi_tb_obj -o Vtl_obi_tb \
  hw/core-v-mini-mcu/include/obi_pkg.sv \
  hw/vendor/mosaic/tl_obi/mosaic_tilelink_to_obi.sv \
  tb/tl_obi/tl_obi_tb.sv

build/tl_obi_tb_obj/Vtl_obi_tb
