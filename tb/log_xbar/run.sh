#!/bin/bash
# Build + run the LOG-bus (logarithmic interconnect) system_xbar testbench.
#
#   1. Generate the RTL for configs/mosaic_log.yaml (RTL step only).
#   2. Verilate the generated system_xbar + the self-checking testbench.
#   3. Run it (T1..T5: interleave sweep, parallel banks, same-bank RR,
#      mid-stream peripheral, unmapped->ERROR).
#   4. Regenerate the default PoC config so the working tree is left as found.
#
# Requires: verilator. No cocotb / RISC-V GCC needed.
# Usage: tb/log_xbar/run.sh

set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_verilator
PY="${PYTHON:-python3}"
TB="tb/log_xbar"
OBJ="${OBJDIR:-$REPO/build/log_xbar_obj}"

INC=hw/core-v-mini-mcu/include
CC=hw/vendor/pulp_platform/common_cells
XCI=hw/vendor/xheep/cluster_interconnect/rtl

TPLS=$(mosaic_templates)

gen () {  # $1 = mosaic config
  $PY util/mosaic_gen/mcu_gen.py --mosaic_config "$1" \
      --base_config configs/general.hjson --pads_cfg configs/pad_cfg.py \
      --outtpl "$TPLS" --externaltpl "" >/dev/null
  MANIFEST=$($PY util/mosaic_gen/build_manifest.py locate --config "$1" \
      --base-config configs/general.hjson --pads-cfg configs/pad_cfg.py \
      --repo-root "$REPO")
  GENERATED_ROOT=$($PY -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' \
      "$MANIFEST")
}

echo "### [1/4] generating RTL for configs/mosaic_log.yaml ..."
gen configs/mosaic_log.yaml

echo "### [2/4] verilating LOG system_xbar + testbench ..."
rm -rf "$OBJ"
verilator --binary -j 0 --timing --top-module tb_log_xbar --Mdir "$OBJ" \
  --timescale 1ns/1ps \
  -Wno-fatal -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-DECLFILENAME \
  -Wno-PINCONNECTEMPTY -Wno-GENUNNAMED -Wno-UNSIGNED -Wno-SYNCASYNCNET \
  -Wno-WIDTHTRUNC -Wno-TIMESCALEMOD \
  -I$CC/include -I$GENERATED_ROOT/hw/core-v-mini-mcu/include -I$INC \
  $CC/src/cf_math_pkg.sv \
  $INC/addr_map_rule_pkg.sv \
  $INC/power_manager_pkg.sv \
  $INC/obi_pkg.sv \
  $GENERATED_ROOT/hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv \
  $CC/src/addr_decode_dync.sv \
  $CC/src/addr_decode.sv \
  $CC/src/lzc.sv \
  $CC/src/rr_arb_tree.sv \
  $CC/src/lfsr.sv \
  $XCI/tcdm_variable_latency_interconnect/xbar_varlat.sv \
  $XCI/tcdm_variable_latency_interconnect/addr_dec_resp_mux_varlat.sv \
  $XCI/tcdm_interconnect/tcdm_interconnect_pkg.sv \
  $XCI/tcdm_interconnect/tcdm_interconnect.sv \
  $XCI/tcdm_interconnect/xbar.sv \
  $XCI/tcdm_interconnect/addr_dec_resp_mux.sv \
  $XCI/tcdm_interconnect/bfly_net.sv \
  $XCI/tcdm_interconnect/clos_net.sv \
  hw/core-v-mini-mcu/xbar_varlat_one_to_n.sv \
  $GENERATED_ROOT/hw/core-v-mini-mcu/system_xbar.sv \
  $TB/tb_log_xbar.sv

echo "### [3/4] running simulation ..."
set +e
"$OBJ/Vtb_log_xbar" "$@"
RC=$?
set -e

echo "### [4/4] restoring default PoC config (mosaic.yaml) ..."
gen mosaic.yaml

exit $RC
