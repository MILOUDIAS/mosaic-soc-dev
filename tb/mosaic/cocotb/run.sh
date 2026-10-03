#!/bin/bash
# Build + run the MOSAIC multi-core cocotb harness (cocotb + Verilator).
#
#   1. Generate the multi-core SoC RTL for configs/mosaic_sim.yaml (RTL step only).
#   2. Run the cocotb testbench (test_mosaic.py) against the generated cpu_subsystem.
#   3. Regenerate the default PoC config so the working tree is left as found.
#
# Requires: cocotb + verilator (both in oss-cad-suite). No RISC-V GCC needed.
# Usage: tb/mosaic/cocotb/run.sh

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_verilator
PY="${PYTHON:-python3}"

TPLS=$(mosaic_templates)

gen () {  # $1 = mosaic config
  $PY util/xheep_gen/mcu_gen.py --mosaic_config "$1" \
      --base_config configs/general.hjson --pads_cfg configs/pad_cfg.py \
      --outtpl "$TPLS" --externaltpl "" >/dev/null
  MANIFEST=$($PY util/xheep_gen/build_manifest.py locate --config "$1" \
      --base-config configs/general.hjson --pads-cfg configs/pad_cfg.py \
      --repo-root "$REPO")
  GENERATED_ROOT=$($PY -c \
      'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' \
      "$MANIFEST")
}

echo "### [1/3] generating RTL for configs/mosaic_sim.yaml ..."
gen configs/mosaic_sim.yaml

echo "### [2/3] running cocotb (SIM=verilator) ..."
set +e
make -C "$HERE" SIM=verilator MOSAIC_GENERATED_ROOT="$GENERATED_ROOT"
RC=$?
set -e

echo "### [3/3] restoring default PoC config (mosaic.yaml) ..."
gen mosaic.yaml

exit $RC
