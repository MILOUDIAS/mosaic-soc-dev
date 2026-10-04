#!/bin/bash
# MOSAIC full-SoC functional simulation (Verilator) — TDU wake-and-run demo.
#
# Builds the COMPLETE multi-core SoC (x-heep testharness wrapping the generated
# mosaic_soc) for the 3-core wake demo (configs/mosaic_wake_demo.yaml:
# 1 cv32e20 TITAN + 1 fazyrv ATLAS + 1 serv NANO + TDU + system_bus + peripherals
# + debug) and runs three programs:
#   - TITAN boots from the boot ROM, writes its sentinel, then WAKES the workers
#     via the TDU (store 0x6 to WAKE_REQ @ 0x200A000C), waits for both worker
#     sentinels, and signals EXIT SUCCESS.
#   - ATLAS (hart 1) and NANO (hart 2) boot DORMANT and run their own programs
#     (at boot addresses 0x1000 / 0x2000) only once woken, each writing a unique
#     sentinel through the shared system bus.
# So "EXIT SUCCESS" proves the full wake-and-run loop: TITAN -> TDU -> core_wake ->
# worker fetch -> execute, all in the real SoC.
#
# Needs: Verilator + a RISC-V GCC (compile-only; ld-linked, no multilib).
# Usage: tb/mosaic_soc/run.sh   (MOSAIC_CFG overrides the config)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_riscv_tc
mosaic_need_verilator
MOSAIC_CFG="${MOSAIC_CFG:-configs/mosaic_wake_demo.yaml}"
OBJ="$HERE/obj_dir"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

echo "### [1/4] generating RTL ($MOSAIC_CFG: 1 TITAN + 1 ATLAS + 1 NANO) ..."
TPLS=$(mosaic_templates)
"$PY" util/mosaic_gen/mcu_gen.py --mosaic_config "$MOSAIC_CFG" --base_config configs/general.hjson \
    --pads_cfg configs/pad_cfg.py --output-root build/mosaic --outtpl "$TPLS" --externaltpl "" >/dev/null 2>&1 || { echo "RTL gen failed"; exit 1; }
MANIFEST="$("$PY" util/mosaic_gen/build_manifest.py locate --config "$MOSAIC_CFG" \
    --base-config configs/general.hjson --pads-cfg configs/pad_cfg.py --repo-root "$REPO")" || exit 1
MOSAIC_GENERATED_ROOT="$("$PY" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' \
    "$MANIFEST")" || exit 1
MOSAIC_SW_INCLUDE="$MOSAIC_GENERATED_ROOT/sw/include"
[ -f "$MOSAIC_SW_INCLUDE/mosaic_memory_map.inc" ] \
    || { echo "generated software contract missing"; exit 1; }
# The FuseSoC setup regenerates the regtool outputs (power_manager_reg_* etc.)
# and the sim-verilator filelist for THIS config. Skipping it leaves stale
# per-bank register RTL behind: e.g. a power manager generated for fewer RAM
# banks silently leaves the extra banks power-gated (reads return 0). The
# boot_rom generator inside it needs the RISC-V toolchain root + prefix.
echo "###       running FuseSoC setup (register generators + filelist) ..."
RISCV_XHEEP="${RISCV_XHEEP:-$(dirname "$(dirname "$TC")")}" \
COMPILER_PREFIX="${COMPILER_PREFIX:-$(basename "$TC" | sed 's/elf$//')}" \
    scripts/fusesoc-setup.sh --manifest "$MANIFEST" > "$HERE/fusesoc-setup.log" 2>&1 \
    || { echo "FuseSoC setup failed — see $HERE/fusesoc-setup.log"; exit 1; }
BUILD_ROOT="$(sed -n 's/^FUSESOC_BUILD_ROOT=//p' "$HERE/fusesoc-setup.log" | tail -1)"
[ -n "$BUILD_ROOT" ] && [ -d "$BUILD_ROOT" ] || { echo "FuseSoC build root missing"; exit 1; }

echo "### [2/4] assembling the 3 programs (TITAN + ATLAS + NANO, rv32i, ld-linked) ..."
# All three assembled as base rv32i (no compressed / no M): the serial cores
# (fazyrv RVC=NONE, serv COMPRESSED=0) can't decode compressed insns, and cve2
# runs rv32i fine. Linked into one ELF (sections at 0x180/0x1000/0x2000).
#
# Berkeley RV64 tiles (rocket/boom, SIM-ONLY) cache the SRAM region, so their
# workers store sentinels through the tile's UNCACHED CLINT-range window
# (0x0200_0000+off, bridge-translated back to 0x3000+off) — swap in the *_tl
# worker programs when the generated cpu_subsystem instantiates those cores.
# (rv32i encodings are valid RV64I for these programs.) NOTE: the swap is
# all-or-nothing; a config mixing a berkeley worker with a non-berkeley
# worker would need per-hart program selection here.
ATLAS_S=atlas; NANO_S=nano
CPU_SUBSYSTEM="$("$PY" util/mosaic_gen/build_manifest.py generated-path --manifest "$MANIFEST" \
    --logical-path hw/mosaic_soc/cpu_subsystem.sv)" || exit 1
if grep -qE "rocket_sci|boom_sci" "$CPU_SUBSYSTEM" 2>/dev/null; then
  ATLAS_S=atlas_tl; NANO_S=nano_tl
  echo "    (berkeley tiles detected: using CLINT-window worker programs)"
fi
( cd "$HERE/prog"
  for s in start "$ATLAS_S" "$NANO_S"; do
    $TC-gcc -march=rv32i -mabi=ilp32 -nostdlib -ffreestanding \
      -DMOSAIC_USE_BUILD_GENERATED_HEADERS -I "$MOSAIC_SW_INCLUDE" \
      -c "$s.S" -o "$s.o" || exit 1
  done
  $TC-ld -T "$MOSAIC_GENERATED_ROOT/sw/linker/mosaic_link.ld" \
    start.o "$ATLAS_S.o" "$NANO_S.o" -o prog.elf || exit 1
  $TC-objcopy -O verilog prog.elf prog.hex || exit 1 ) || { echo "program build failed"; exit 1; }
echo "    firmware: $HERE/prog/prog.hex"

echo "### [3/4] building the full-SoC Verilator model (this takes a few minutes) ..."
rm -rf "$OBJ"
"$PY" "$HERE/gen_filelist.py" "$REPO" --manifest "$MANIFEST" --build-root "$BUILD_ROOT" \
    > "$HERE/soc.f" || { echo "filelist gen failed"; exit 1; }
# Pure-SV tb_top top + --binary (Verilator's own main); uartdpi.c is the only
# C model (DPI import). The DPI-export-free tb_util shadow dodges the codegen bug.
verilator --binary -j 0 --top-module tb_top \
    --Mdir "$OBJ" -o Vtb_top --timescale 1ns/1ps \
    -GUSE_EXTERNAL_DEVICE_EXAMPLE=1 -GJTAG_DPI=0 \
    -f "$HERE/soc.f" > "$HERE/build.log" 2>&1
[ -x "$OBJ/Vtb_top" ] || { echo "### BUILD FAILED (no Vtb_top) — see $HERE/build.log"; grep -iE "%Error|error:|undefined" "$HERE/build.log" | head; exit 1; }

echo "### [4/4] running the simulation ..."
"$OBJ/Vtb_top" +firmware="$HERE/prog/prog.hex" +boot_sel=0 +maxcycles=300000 +verbose 2>&1 | tee "$HERE/sim.log" | tail -30
SIM_RC=${PIPESTATUS[0]}
echo ""
if [ "$SIM_RC" -eq 0 ] && grep -q "EXIT SUCCESS" "$HERE/sim.log"; then
  echo "### RESULT: EXIT SUCCESS — full multi-core SoC executed the program ✓"
else
  echo "### RESULT: simulation failed or no EXIT SUCCESS (see $HERE/sim.log)"
  exit 1
fi
