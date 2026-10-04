#!/bin/bash
# UART bring-up verification for a config whose only peripheral is the UART.
#
# Closes a verification gap in Block A (its evidence is in docs/status.md): the
# frozen Block A part
# carries hand-modified vendored UART RTL (both FIFOs cut 32 -> 4 entries for
# 0.066 mm2) and nothing exercised it. See tb/mosaic_soc/prog_uart/uart.S for
# what the three phases check.
#
# Usage: MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml tb/mosaic_soc/run_uart.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_riscv_tc
mosaic_need_verilator
MOSAIC_CFG="${MOSAIC_CFG:-configs/mosaic_tapeout_ultra.yaml}"
OBJ="$HERE/obj_dir_uart"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

# The string prog_uart/uart.S transmits. Kept here and asserted against the DPI
# log rather than eyeballed in the transcript.
EXPECT="MOSAIC BLOCK A UART OK"
# The testbench clocks the SoC at CLK_FREQUENCY KHz and sets the UART DPI baud
# to CLK_FREQUENCY*1000/20 (tb/testharness.sv.tpl). The OpenTitan NCO divides
# the same way: baud = NCO * f_clk / 2^20, so NCO = 2^20 / 20 independently of
# the actual frequency. Fixing the RATIO, not a baud number, is what keeps this
# correct if CLK_FREQUENCY changes.
TB_BAUD_DIVISOR=20
NCO=$(( (1 << 20) / TB_BAUD_DIVISOR ))

echo "### [1/4] generating RTL ($MOSAIC_CFG) ..."
TPLS=$(mosaic_templates)
"$PY" util/mosaic_gen/mcu_gen.py --mosaic_config "$MOSAIC_CFG" \
    --base_config configs/general.hjson --pads_cfg configs/pad_cfg.py \
    --output-root build/mosaic --outtpl "$TPLS" --externaltpl "" >/dev/null 2>&1 \
    || { echo "RTL gen failed"; exit 1; }
MANIFEST="$("$PY" util/mosaic_gen/build_manifest.py locate --config "$MOSAIC_CFG" \
    --base-config configs/general.hjson --pads-cfg configs/pad_cfg.py \
    --repo-root "$REPO")" || exit 1
GEN_ROOT="$("$PY" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' \
    "$MANIFEST")" || exit 1
MOSAIC_SW="$GEN_ROOT/sw"
BOOT_MANIFEST="$MOSAIC_SW/boot_images.json"
[ -f "$BOOT_MANIFEST" ] || { echo "generated boot manifest missing"; exit 1; }

# Refuse early and clearly on a config with no UART, rather than building a
# Verilator model for a test that cannot mean anything.
if ! "$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if "uart" in d["resolved"]["declared_peripherals"] else 1)' \
      "$MANIFEST"; then
  echo "### $MOSAIC_CFG declares no UART peripheral — nothing for this test to check."
  exit 2
fi
UART_BASE="$("$PY" -c '
import re, sys, pathlib
pkg = pathlib.Path(sys.argv[1]) / "hw/mosaic_soc/include/mosaic_soc_pkg.sv"
text = pkg.read_text()

def const(name):
    # (?<![A-Za-z0-9_]) matters: without it PERIPHERAL_START_ADDRESS also
    # matches AO_PERIPHERAL_START_ADDRESS, which resolved the UART to
    # 0x20080000 (pad-control space) instead of 0x30080000.
    m = re.search(r"(?<![A-Za-z0-9_])%s\s*=\s*32.h([0-9a-fA-F_]+)" % name, text)
    return int(m.group(1).replace("_", ""), 16) if m else None

base = const("PERIPHERAL_START_ADDRESS")
size = const("PERIPHERAL_SIZE")
m = re.search(r"(?<![A-Za-z0-9_])UART_START_ADDRESS\s*=\s*PERIPHERAL_START_ADDRESS\s*\+\s*32.h([0-9a-fA-F_]+)", text)
if base is None or not m:
    sys.exit("cannot resolve UART_START_ADDRESS from the generated package")
uart = base + int(m.group(1).replace("_", ""), 16)
ao = const("AO_PERIPHERAL_START_ADDRESS")
if ao is not None and base == ao:
    sys.exit("resolved PERIPHERAL_START_ADDRESS to the AO window -- regex is wrong")
if size is not None and not (base <= uart < base + size):
    sys.exit("UART 0x%08X falls outside the peripheral window" % uart)
print("0x%08X" % uart)
' "$GEN_ROOT")" || exit 1
echo "    UART at $UART_BASE, NCO $NCO (baud = f/$TB_BAUD_DIVISOR)"

echo "###       running FuseSoC setup ..."
RISCV_XHEEP="${RISCV_XHEEP:-$(dirname "$(dirname "$TC")")}" \
COMPILER_PREFIX="${COMPILER_PREFIX:-$(basename "$TC" | sed 's/elf$//')}" \
    scripts/fusesoc-setup.sh --manifest "$MANIFEST" \
    > "$HERE/fusesoc-setup-uart.log" 2>&1 \
    || { echo "FuseSoC setup failed — see $HERE/fusesoc-setup-uart.log"; exit 1; }
BUILD_ROOT="$(sed -n 's/^FUSESOC_BUILD_ROOT=//p' "$HERE/fusesoc-setup-uart.log" | tail -1)"
[ -n "$BUILD_ROOT" ] && [ -d "$BUILD_ROOT" ] || { echo "FuseSoC build root missing"; exit 1; }

echo "### [2/4] assembling the UART bring-up image per boot slot ..."
FW_DIR="$GEN_ROOT/uart_fw"
rm -rf "$FW_DIR"; mkdir -p "$FW_DIR"
FILELIST="$FW_DIR/soc.f"
XIP="$("$PY" -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(int(bool(d["images"]) and all(i.get("execute_in_place") for i in d["images"])))' \
    "$BOOT_MANIFEST")" || exit 1

while read -r IMAGE_ID PRIMARY XLEN ABI; do
  [ -n "$IMAGE_ID" ] || continue
  case "$XLEN:$ABI" in
    32:ilp32e) MARCH=rv32e_zicsr ;;
    32:ilp32)  MARCH=rv32i_zicsr ;;
    64:lp64)   MARCH=rv64i_zicsr ;;
    *) echo "unsupported image ISA contract: xlen=$XLEN abi=$ABI"; exit 1 ;;
  esac
  "$TC"-gcc -march="$MARCH" -mabi="$ABI" -nostdlib -ffreestanding \
      -DMOSAIC_USE_BUILD_GENERATED_HEADERS \
      -DMOSAIC_UART_PRIMARY="$PRIMARY" \
      -DMOSAIC_UART_BASE="$UART_BASE" \
      -DMOSAIC_UART_NCO="$NCO" \
      -I "$MOSAIC_SW/include" -c "$HERE/prog_uart/uart.S" \
      -o "$FW_DIR/image_${IMAGE_ID}.o" || exit 1
  "$TC"-ld -T "$MOSAIC_SW/linker/image_${IMAGE_ID}.ld" \
      "$FW_DIR/image_${IMAGE_ID}.o" -o "$FW_DIR/image_${IMAGE_ID}.elf" || exit 1
  "$TC"-objcopy -O verilog "$FW_DIR/image_${IMAGE_ID}.elf" \
      "$FW_DIR/image_${IMAGE_ID}.hex" || exit 1
done < <("$PY" -c \
  'import json,sys; d=json.load(open(sys.argv[1])); hs={h["hart_id"]:h for h in d["harts"]}; [print(i["image_id"], int(any(hs[h]["role"] == "titan" for h in i["harts"])), i["xlens"][0], "ilp32e" if "ilp32e" in i.get("abis",[]) else "ilp32" if set(i.get("abis",[]))=={"ilp32"} else "lp64") for i in d["images"]]' \
  "$BOOT_MANIFEST") || { echo "firmware build failed"; exit 1; }

if [ "$XIP" = "1" ]; then
  "$PY" "$HERE/pack_xip_hex.py" --manifest "$BOOT_MANIFEST" \
      --output "$FW_DIR/uart.hex" "$FW_DIR"/image_*.hex || exit 1
else
  "$PY" -c \
    'import json,sys; from pathlib import Path; d=json.load(open(sys.argv[1])); out=Path(sys.argv[2]); images=[Path(p) for p in sys.argv[3:]]; base=int(d["memory"]["shared_control_base"],0); size=int(d["memory"]["shared_control_size"]); rows=[" ".join(["00"]*min(16,size-off)) for off in range(0,size,16)]; out.write_text("".join(p.read_text() for p in images)+f"\n@{base:08X}\n"+"\n".join(rows)+"\n")' \
    "$BOOT_MANIFEST" "$FW_DIR/uart.hex" "$FW_DIR"/image_*.hex || exit 1
fi

echo "### [3/4] building the full-SoC Verilator model ..."
rm -rf "$OBJ"
"$PY" "$HERE/gen_filelist.py" "$REPO" --manifest "$MANIFEST" \
    --build-root "$BUILD_ROOT" > "$FILELIST" || { echo "filelist gen failed"; exit 1; }
verilator --binary -j 0 --top-module tb_top --Mdir "$OBJ" -o Vtb_top \
    --timescale 1ns/1ps -GUSE_EXTERNAL_DEVICE_EXAMPLE=1 -GJTAG_DPI=0 \
    -f "$FILELIST" > "$HERE/build-uart.log" 2>&1
[ -x "$OBJ/Vtb_top" ] || { echo "### BUILD FAILED — see $HERE/build-uart.log"; exit 1; }

echo "### [4/4] running UART bring-up firmware ..."
UART_LOG="$HERE/uart0.log"
rm -f "$UART_LOG"
if [ "$XIP" = "1" ]; then BOOT_ARGS="+boot_sel=1 +execute_from_flash=1"; else BOOT_ARGS="+boot_sel=0"; fi
# shellcheck disable=SC2086
"$OBJ/Vtb_top" +firmware="$FW_DIR/uart.hex" $BOOT_ARGS \
    "+UARTDPI_LOG_uart0=$UART_LOG" \
    +maxcycles=2000000 2>&1 | tee "$HERE/sim-uart.log" | tail -20
SIM_RC=${PIPESTATUS[0]}

echo
FAIL=0
if [ "$SIM_RC" -ne 0 ] || ! grep -q "EXIT SUCCESS" "$HERE/sim-uart.log"; then
  echo "  [FAIL] firmware did not reach EXIT SUCCESS"
  echo "         (uart.S parks without writing the exit register on a failed"
  echo "          phase, so a timeout here means a phase assertion failed)"
  FAIL=1
else
  echo "  [ ok ] phases 1-3 passed in firmware (FIFO depth, polled TX, RX loopback)"
fi
if [ -f "$UART_LOG" ] && grep -q "$EXPECT" "$UART_LOG"; then
  echo "  [ ok ] UART DPI received: $(tr -d '\r' < "$UART_LOG" | tr '\n' ' ')"
else
  echo "  [FAIL] expected '$EXPECT' on the UART DPI; log holds:"
  echo "         $( [ -f "$UART_LOG" ] && tr -d '\r\n' < "$UART_LOG" || echo '<no log>' )"
  FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
  echo "### RESULT: EXIT SUCCESS — UART verified on $MOSAIC_CFG ✓"
else
  echo "### RESULT: UART verification FAILED (see $HERE/sim-uart.log)"
  exit 1
fi
