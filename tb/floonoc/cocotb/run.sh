#!/bin/bash
# Build + run the MOSAIC OBI<->AXI bridge cocotb tests (cocotb + Verilator):
#   stage 1: bridge<->bridge loopback            (TOPLEVEL=tb_bridge_top)
#   stage 2: loopback through the FlooNoC fabric (TOPLEVEL=tb_noc_top, once generated)
# Requires cocotb + verilator. No RTL generation needed (bridges are static RTL).
# Usage: tb/floonoc/cocotb/run.sh [stage2]

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$HERE"
source "$REPO/tb/tools.sh"
mosaic_need_verilator

TOPS=(tb_bridge_top)
MODS=(test_bridges)
if [ "${1:-}" = "stage2" ]; then
  TOPS+=(tb_noc_top)
  MODS+=(test_noc)
  echo "### generating the FlooNoC fabric (configs/mosaic_floonoc.yaml) ..."
  ( cd "$REPO"
    TPLS=$(mosaic_templates)
    python3 util/mosaic_gen/mcu_gen.py --mosaic_config configs/mosaic_floonoc.yaml \
        --base_config configs/general.hjson --pads_cfg configs/pad_cfg.py \
        --outtpl "$TPLS" --externaltpl "" >/dev/null ) || { echo "fabric gen failed"; exit 1; }
  MANIFEST=$(cd "$REPO" && python3 util/mosaic_gen/build_manifest.py locate \
      --config configs/mosaic_floonoc.yaml --base-config configs/general.hjson \
      --pads-cfg configs/pad_cfg.py --repo-root "$REPO") || exit 1
  GENERATED_ROOT=$(python3 -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' \
      "$MANIFEST") || exit 1
fi

rc=0
for i in "${!TOPS[@]}"; do
  echo "### ===== bridge cocotb: TOPLEVEL=${TOPS[$i]} ====="
  rm -rf sim_build results.xml __pycache__
  make SIM=verilator TOPLEVEL="${TOPS[$i]}" MODULE="${MODS[$i]}" \
    MOSAIC_GENERATED_ROOT="${GENERATED_ROOT:-}" || rc=1
done
exit $rc
