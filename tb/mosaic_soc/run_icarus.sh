#!/bin/bash
# MOSAIC full-SoC wake-and-run demo on ICARUS VERILOG (event-driven).
#
# Icarus is event-driven (delta-cycle settling), so the Verilator -Wno-UNOPTFLAT
# stale-read evaluation-order quirks (worker wake-latch, obi_fifo, cve2 clock gate)
# don't arise. Reuses the SV sources from gen_filelist.py (live hw/ + tb/), drops
# the Verilator-only flags/waivers/C++ DPI, converts -I -> +incdir+, and stubs the
# uartdpi model (the demo uses RAM sentinels + soc_ctrl, not UART). Top = mosaic_tb.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_riscv_tc
MOSAIC_CFG="${MOSAIC_CFG:-configs/mosaic_wake_demo.yaml}"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

echo "### [1/4] generating RTL ($MOSAIC_CFG) ..."
TPLS=$(mosaic_templates)
"$PY" util/xheep_gen/mcu_gen.py --mosaic_config "$MOSAIC_CFG" --base_config configs/general.hjson \
    --pads_cfg configs/pad_cfg.py --output-root build/mosaic --outtpl "$TPLS" --externaltpl "" >/dev/null 2>&1 || { echo "RTL gen failed"; exit 1; }
MANIFEST="$("$PY" util/xheep_gen/build_manifest.py locate --config "$MOSAIC_CFG" \
    --base-config configs/general.hjson --pads-cfg configs/pad_cfg.py --repo-root "$REPO")" || exit 1
MOSAIC_GENERATED_ROOT="$("$PY" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' \
    "$MANIFEST")" || exit 1
MOSAIC_SW_INCLUDE="$MOSAIC_GENERATED_ROOT/sw/include"
[ -f "$MOSAIC_SW_INCLUDE/mosaic_memory_map.inc" ] \
    || { echo "generated software contract missing"; exit 1; }
RISCV_XHEEP="${RISCV_XHEEP:-$(dirname "$(dirname "$TC")")}" \
COMPILER_PREFIX="${COMPILER_PREFIX:-$(basename "$TC" | sed 's/elf$//')}" \
    scripts/fusesoc-setup.sh --manifest "$MANIFEST" > "$HERE/fusesoc-setup-icarus.log" 2>&1 \
    || { echo "FuseSoC setup failed — see $HERE/fusesoc-setup-icarus.log"; exit 1; }
BUILD_ROOT="$(sed -n 's/^FUSESOC_BUILD_ROOT=//p' "$HERE/fusesoc-setup-icarus.log" | tail -1)"
[ -n "$BUILD_ROOT" ] && [ -d "$BUILD_ROOT" ] || { echo "FuseSoC build root missing"; exit 1; }

echo "### [2/4] assembling the 3 programs (rv32i) ..."
( cd "$HERE/prog"
  for s in start atlas nano; do
    $TC-gcc -march=rv32i -mabi=ilp32 -nostdlib -ffreestanding \
      -DMOSAIC_USE_BUILD_GENERATED_HEADERS -I "$MOSAIC_SW_INCLUDE" \
      -c "$s.S" -o "$s.o" || exit 1
  done
  $TC-ld -T "$MOSAIC_GENERATED_ROOT/sw/linker/mosaic_link.ld" \
    start.o atlas.o nano.o -o prog.elf || exit 1
  $TC-objcopy -O verilog prog.elf prog.hex || exit 1 ) || { echo "program build failed"; exit 1; }

echo "### [3/4] assembling the Icarus filelist + compiling ..."
"$PY" "$HERE/gen_filelist.py" "$REPO" --manifest "$MANIFEST" --build-root "$BUILD_ROOT" \
    > "$HERE/soc.f" || exit 1
# Verilator -f -> Icarus: +incdir+ from -I, keep -y, keep .sv/.v EXCEPT the real
# uartdpi.sv (stubbed); drop -Wno/-CFLAGS/-G/.vlt/.c/.cpp and tb_top.sv (we use mosaic_tb).
INCS=$(grep -E '^-I/' "$HERE/soc.f" | sed -E 's#^-I#+incdir+#' | tr '\n' ' ')
# Drop the iDMA -y dirs and the whole hw/ip_examples/ tree. The pulp iDMA uses
# package-function param defaults (cf_math_pkg::idx_width); the example peripherals
# (ams/dlc/iffifo/im2col_spc/...) use unpacked-array params in their *_reg_pkg.sv —
# both unsupported by Icarus. NONE of these are instantiated by the wake demo
# (peripherals = uart/gpio/timer/spi only), so iDMA -> idma_stub.sv and all of
# hw/ip_examples/ is excluded entirely.
YS=$(grep -E '^-y ' "$HERE/soc.f" | grep -vE '/mosaic/idma|/ip_examples/' | tr '\n' ' ')
# Exclude: the real uartdpi (DPI), tb_top (we use mosaic_tb), iDMA, ip_examples, and
# spi_device (the demo instantiates spi_host + spi_subsystem only; spi_device's
# *_reg_pkg.sv uses unpacked-array params Icarus can't parse and is never instantiated).
grep -E '\.sv$|\.v$' "$HERE/soc.f" \
  | grep -vE 'uartdpi\.sv$|/tb/tb_top\.sv$|/mosaic/idma/|/ip_examples/|spi_device' > "$HERE/icarus_files.txt"
echo "$HERE/uartdpi_stub.sv" >> "$HERE/icarus_files.txt"
echo "$HERE/idma_stub.sv"    >> "$HERE/icarus_files.txt"
echo "$HERE/mosaic_tb.sv"    >> "$HERE/icarus_files.txt"
echo "    $(wc -l < "$HERE/icarus_files.txt") sv files; compiling with iverilog -g2012 ..."
iverilog -g2012 -gsupported-assertions -s mosaic_tb -o "$HERE/sim.vvp" \
    $INCS $YS -c "$HERE/icarus_files.txt" 2>&1 | tee "$HERE/icarus_build.log" | grep -iE "error|warning: |sorry|cannot" | head -40
[ -f "$HERE/sim.vvp" ] || { echo "### ICARUS COMPILE FAILED (see icarus_build.log)"; exit 1; }

echo "### [4/4] running with vvp ..."
vvp "$HERE/sim.vvp" +firmware="$HERE/prog/prog.hex" +boot_sel=0 2>&1 | tee "$HERE/icarus_sim.log" | tail -25
SIM_RC=${PIPESTATUS[0]}
echo ""
if [ "$SIM_RC" -eq 0 ] && grep -q "EXIT SUCCESS" "$HERE/icarus_sim.log"; then
  echo "### RESULT: EXIT SUCCESS — multi-core wake-and-run works on Icarus ✓"
else
  echo "### RESULT: simulation failed or no EXIT SUCCESS (see $HERE/icarus_sim.log)"
  exit 1
fi
