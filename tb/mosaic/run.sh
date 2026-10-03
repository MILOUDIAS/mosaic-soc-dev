#!/bin/bash
# Build + run the MOSAIC multi-core Verilator harness.
#
#   1. Generate the multi-core SoC RTL for configs/mosaic_sim.yaml (RTL step only
#      — skips the FuseSoC register-gen pass that needs a RISC-V toolchain).
#   2. Verilate the generated cpu_subsystem + the testbench + OBI memories.
#   3. Run the self-checking testbench.
#   4. Regenerate the default PoC config so the working tree is left as found.
#
# Requires: verilator (installed). No cocotb / RISC-V GCC needed.
# Usage: tb/mosaic/run.sh

set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_verilator
PY="${PYTHON:-python3}"
TB="tb/mosaic"
OBJ="${OBJDIR:-$REPO/build/mosaic_sim_obj}"

INC=hw/core-v-mini-mcu/include
SERV=hw/vendor/mosaic/serv
FAZ=hw/vendor/mosaic/fazyrv
CC=hw/vendor/pulp_platform/common_cells/include
TC=hw/vendor/pulp_platform/tech_cells_generic/src/rtl/tc_clk.sv

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

echo "### [1/4] generating RTL for configs/mosaic_sim.yaml ..."
gen configs/mosaic_sim.yaml

echo "### [2/4] verilating multi-core cpu_subsystem + testbench ..."
rm -rf "$OBJ"
verilator --binary -j 0 --top-module mosaic_multicore_tb --Mdir "$OBJ" \
  --timescale 1ns/1ps \
  -Wno-fatal -Wno-WIDTH -Wno-UNUSEDSIGNAL -Wno-UNDRIVEN -Wno-UNUSEDPARAM \
  -Wno-DECLFILENAME -Wno-TIMESCALEMOD -Wno-PINMISSING -Wno-CASEINCOMPLETE \
  -Wno-SYMRSVDWORD -Wno-GENUNNAMED -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC \
  -I$CC -I$GENERATED_ROOT/hw/core-v-mini-mcu/include -I$INC \
  -y $INC -y $SERV/rtl -y $SERV/servile -y $FAZ/rtl -y hw/sci -y hw/core-v-mini-mcu \
  $INC/obi_pkg.sv $INC/reg_pkg.sv $INC/fifo_pkg.sv $INC/addr_map_rule_pkg.sv \
  $GENERATED_ROOT/hw/core-v-mini-mcu/include/core_v_mini_mcu_pkg.sv \
  $TC \
  $GENERATED_ROOT/hw/core-v-mini-mcu/cpu_subsystem.sv \
  $TB/tb_obi_mem.sv $TB/mosaic_multicore_tb.sv

echo "### [3/4] running simulation ..."
set +e
"$OBJ/Vmosaic_multicore_tb"
RC=$?
set -e

echo "### [4/4] restoring default PoC config (mosaic.yaml) ..."
gen mosaic.yaml

exit $RC
