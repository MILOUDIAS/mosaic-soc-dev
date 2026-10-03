#!/bin/bash
# MOSAIC all-TITAN 4-core SMP full-SoC simulation (Verilator).
#
# Builds the COMPLETE SoC for the all-TITAN config (2x cv32e20 + 2x cv32e40x,
# every role titan -> all four cores boot free-running at the shared
# BOOT_ADDR) and runs ONE program (prog_titan/titan_smp.S) that branches on
# mhartid: hart 0 queues 3 task descriptors in the TDU FIFO, harts 1-3
# poll-pop them (atomic dequeue), compute, and report per-slot sentinels;
# hart 0 verifies each slot's exact value and signals EXIT SUCCESS.
#
# Defaults to the bus:log fabric; run the same demo over the FlooNoC with:
#   MOSAIC_CFG=configs/mosaic_titan_floonoc.yaml tb/mosaic_soc/run_titan.sh
#
# Needs: Verilator + a RISC-V GCC (compile-only; ld-linked, no multilib).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_riscv_tc
mosaic_need_verilator
MOSAIC_CFG="${MOSAIC_CFG:-configs/mosaic_titan_log.yaml}"
OBJ="$HERE/obj_dir_titan"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

echo "### [1/4] generating RTL ($MOSAIC_CFG: 2x cv32e20 + 2x cv32e40x, all TITAN) ..."
TPLS=$(mosaic_templates)
"$PY" util/mosaic_gen/mcu_gen.py --mosaic_config "$MOSAIC_CFG" --base_config configs/general.hjson \
    --pads_cfg configs/pad_cfg.py --output-root build/mosaic --outtpl "$TPLS" --externaltpl "" >/dev/null 2>&1 || { echo "RTL gen failed"; exit 1; }
MANIFEST="$("$PY" util/mosaic_gen/build_manifest.py locate --config "$MOSAIC_CFG" \
    --base-config configs/general.hjson --pads-cfg configs/pad_cfg.py --repo-root "$REPO")" || exit 1
echo "###       running FuseSoC setup (register generators + filelist) ..."
RISCV_XHEEP="${RISCV_XHEEP:-$(dirname "$(dirname "$TC")")}" \
COMPILER_PREFIX="${COMPILER_PREFIX:-$(basename "$TC" | sed 's/elf$//')}" \
    scripts/fusesoc-setup.sh --manifest "$MANIFEST" > "$HERE/fusesoc-setup.log" 2>&1 \
    || { echo "FuseSoC setup failed — see $HERE/fusesoc-setup.log"; exit 1; }
BUILD_ROOT="$(sed -n 's/^FUSESOC_BUILD_ROOT=//p' "$HERE/fusesoc-setup.log" | tail -1)"
[ -n "$BUILD_ROOT" ] && [ -d "$BUILD_ROOT" ] || { echo "FuseSoC build root missing"; exit 1; }

echo "### [2/4] assembling the SMP program (one .S, mhartid-branched, rv32i) ..."
# rv32i_zicsr: the program reads mhartid (csrr); modern binutils requires the
# Zicsr extension to be named explicitly. Encodings remain plain rv32i + CSR.
( cd "$HERE/prog_titan"
  $TC-gcc -march=rv32i_zicsr -mabi=ilp32 -nostdlib -ffreestanding -c titan_smp.S -o titan_smp.o || exit 1
  $TC-ld -T link.ld titan_smp.o -o titan_smp.elf || exit 1
  $TC-objcopy -O verilog titan_smp.elf titan_smp.hex || exit 1 ) || { echo "program build failed"; exit 1; }
echo "    firmware: $HERE/prog_titan/titan_smp.hex"

echo "### [3/4] building the full-SoC Verilator model (this takes a few minutes) ..."
rm -rf "$OBJ"
"$PY" "$HERE/gen_filelist.py" "$REPO" --manifest "$MANIFEST" --build-root "$BUILD_ROOT" \
    > "$HERE/soc.f" || { echo "filelist gen failed"; exit 1; }
verilator --binary -j 0 --top-module tb_top \
    --Mdir "$OBJ" -o Vtb_top --timescale 1ns/1ps \
    -GUSE_EXTERNAL_DEVICE_EXAMPLE=1 -GJTAG_DPI=0 \
    -f "$HERE/soc.f" > "$HERE/build-titan.log" 2>&1
[ -x "$OBJ/Vtb_top" ] || { echo "### BUILD FAILED (no Vtb_top) — see $HERE/build-titan.log"; grep -iE "%Error|error:|undefined" "$HERE/build-titan.log" | head; exit 1; }

echo "### [4/4] running the simulation ..."
"$OBJ/Vtb_top" +firmware="$HERE/prog_titan/titan_smp.hex" +boot_sel=0 +maxcycles=300000 +verbose 2>&1 | tee "$HERE/sim-titan.log" | tail -30
SIM_RC=${PIPESTATUS[0]}
echo ""
if [ "$SIM_RC" -eq 0 ] && grep -q "EXIT SUCCESS" "$HERE/sim-titan.log"; then
  echo "### RESULT: EXIT SUCCESS — 4 TITAN cores cooperated over the TDU ($MOSAIC_CFG) ✓"
else
  echo "### RESULT: simulation failed or no EXIT SUCCESS (see $HERE/sim-titan.log)"
  exit 1
fi
