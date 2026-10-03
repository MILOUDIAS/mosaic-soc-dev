#!/bin/bash
# Topology-generic full-SoC liveness verification.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"
source "$REPO/tb/tools.sh"
mosaic_need_riscv_tc
mosaic_need_verilator
MOSAIC_CFG="${MOSAIC_CFG:-mosaic.yaml}"
OBJ="$HERE/obj_dir_generic"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

echo "### [1/4] generating topology-generic RTL ($MOSAIC_CFG) ..."
TPLS=$(mosaic_templates)
"$PY" util/mosaic_gen/mcu_gen.py --mosaic_config "$MOSAIC_CFG" \
    --base_config configs/general.hjson --pads_cfg configs/pad_cfg.py \
    --output-root build/mosaic --outtpl "$TPLS" --externaltpl "" >/dev/null 2>&1 \
    || { echo "RTL gen failed"; exit 1; }
# MOSAIC_MANIFEST pins the bundle instead of deriving it, the same override
# run_signoff.sh already accepts. `locate` hashes the whole generator closure,
# and `tb` and `flow` are both closure roots -- so adding a testbench file or a
# delivery wrapper moves the key and locate reports "no manifest" for a bundle
# that is sitting right there and is still correct for the RTL. Regenerating
# 18 GB to rebuild firmware that would come out byte-identical is not a check,
# it is a tax. Pin it deliberately when you know the RTL inputs are unchanged.
if [ -n "${MOSAIC_MANIFEST:-}" ]; then
  MANIFEST="$MOSAIC_MANIFEST"
  [ -f "$MANIFEST" ] || { echo "MOSAIC_MANIFEST does not exist: $MANIFEST"; exit 1; }
  echo "###       manifest pinned: $MANIFEST"
else
MANIFEST="$("$PY" util/mosaic_gen/build_manifest.py locate --config "$MOSAIC_CFG" \
    --base-config configs/general.hjson --pads-cfg configs/pad_cfg.py \
    --repo-root "$REPO")" || exit 1
fi
MOSAIC_GENERATED_ROOT="$("$PY" -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' \
    "$MANIFEST")" || exit 1
MOSAIC_SW="$MOSAIC_GENERATED_ROOT/sw"
BOOT_MANIFEST="$MOSAIC_SW/boot_images.json"
[ -f "$BOOT_MANIFEST" ] || { echo "generated boot manifest missing"; exit 1; }

# GENERIC_FW_ONLY=1 stops after the firmware image. Step [2/4] needs only the
# toolchain and the bundle's generated/sw (linker scripts + boot_images.json);
# the FuseSoC staging below exists for the Verilator sim in [3/4] and [4/4].
# Splitting them lets GLS -- which wants the hex and nothing else -- avoid a
# staging step that refuses whenever the source closure has moved for reasons
# that cannot touch the firmware, such as adding a testbench file.
if [ "${GENERIC_FW_ONLY:-0}" = "1" ]; then
  echo "###       FW-ONLY: skipping FuseSoC setup and the Verilator sim"
  BUILD_ROOT=""
else
echo "###       running FuseSoC setup (register generators + filelist) ..."
RISCV_XHEEP="${RISCV_XHEEP:-$(dirname "$(dirname "$TC")")}" \
COMPILER_PREFIX="${COMPILER_PREFIX:-$(basename "$TC" | sed 's/elf$//')}" \
    scripts/fusesoc-setup.sh --manifest "$MANIFEST" \
    > "$HERE/fusesoc-setup-generic.log" 2>&1 \
    || { echo "FuseSoC setup failed — see $HERE/fusesoc-setup-generic.log"; exit 1; }
BUILD_ROOT="$(sed -n 's/^FUSESOC_BUILD_ROOT=//p' \
    "$HERE/fusesoc-setup-generic.log" | tail -1)"
[ -n "$BUILD_ROOT" ] && [ -d "$BUILD_ROOT" ] \
    || { echo "FuseSoC build root missing"; exit 1; }
fi

echo "### [2/4] assembling one liveness image per generated boot slot ..."
FW_DIR="$MOSAIC_GENERATED_ROOT/generic_fw"
rm -rf "$FW_DIR"
mkdir -p "$FW_DIR"
FILELIST="$FW_DIR/soc.f"
NUM_HARTS="$("$PY" -c \
    'import json,sys; print(len(json.load(open(sys.argv[1]))["harts"]))' \
    "$BOOT_MANIFEST")" || exit 1
XIP="$("$PY" -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(int(bool(d["images"]) and all(i.get("execute_in_place") for i in d["images"])))' \
    "$BOOT_MANIFEST")" || exit 1
TESTBENCH_BOOTSTRAP="$("$PY" -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(int(bool(d.get("boot_policy",{}).get("testbench_hart0_bootstrap",False))))' \
    "$BOOT_MANIFEST")" || exit 1
BOOTSTRAP_DISPATCH="$((TESTBENCH_BOOTSTRAP && NUM_HARTS > 1))"
WAKE_MASK="$("$PY" -c \
    'import json,sys; d=json.load(open(sys.argv[1])); b=bool(int(sys.argv[2])); print(sum(1 << int(h["hart_id"]) for h in d["harts"] if h["role"] != "titan" and not (b and int(h["hart_id"]) == 0)))' \
    "$BOOT_MANIFEST" "$TESTBENCH_BOOTSTRAP")" || exit 1
DISPATCH_MASK="$("$PY" -c \
    'import json,sys; d=json.load(open(sys.argv[1])); b=bool(int(sys.argv[2])); hs={h["hart_id"]:h for h in d["harts"]}; imgs={i["image_id"]:i for i in d["images"]}; print(sum(1 << int(h["hart_id"]) for h in d["harts"] if h["role"] != "titan" and not (b and int(h["hart_id"]) == 0) and not any(hs[x]["role"] == "titan" for x in imgs[h["image_id"]]["harts"])))' \
    "$BOOT_MANIFEST" "$TESTBENCH_BOOTSTRAP")" || exit 1
TDU_ENABLED="$("$PY" -c \
    'import json,sys; print(int(bool(json.load(open(sys.argv[1]))["scheduler"]["tdu"])))' \
    "$BOOT_MANIFEST")" || exit 1
"$PY" -c \
    'import json,sys; d=json.load(open(sys.argv[1])); hs={h["hart_id"]:h for h in d["harts"]}; h0=hs[0]; b=bool(d.get("boot_policy",{}).get("testbench_hart0_bootstrap",False)); t=bool(d["scheduler"]["tdu"]); free=[i for i in d["images"] if any(hs[h]["role"] == "titan" for h in i["harts"]) or (b and 0 in i["harts"])]; workers=any(h["role"] != "titan" for h in d["harts"]); shared_bad=any(len(i["harts"]) > 1 and not (b and 0 in i["harts"]) and any("mhartid" not in hs[h].get("capabilities",[]) for h in i["harts"]) for i in free); bad=not (h0["role"] == "titan" or b) or (workers and len(hs) > 1 and not t) or shared_bad; sys.exit(1 if bad else 0)' \
    "$BOOT_MANIFEST" \
    || { echo "generic liveness needs a free hart 0, a TDU for additional dormant workers, and mhartid on other shared free-running images"; exit 1; }

while read -r IMAGE_ID PRIMARY TL_WINDOW PRIMARY_SHARED FIXED_HART XLEN ABI; do
  [ -n "$IMAGE_ID" ] || continue
  case "$XLEN:$ABI" in
    32:ilp32e) MARCH=rv32e_zicsr ;;
    32:ilp32)  MARCH=rv32i_zicsr ;;
    64:lp64)   MARCH=rv64i_zicsr ;;
    *) echo "unsupported generic image ISA contract: xlen=$XLEN abi=$ABI"; exit 1 ;;
  esac
  "$TC"-gcc -march="$MARCH" -mabi="$ABI" -nostdlib -ffreestanding \
      -DMOSAIC_USE_BUILD_GENERATED_HEADERS \
      -DMOSAIC_GENERIC_PRIMARY="$PRIMARY" \
      -DMOSAIC_GENERIC_NUM_HARTS="$NUM_HARTS" \
      -DMOSAIC_GENERIC_WAKE_MASK="$WAKE_MASK" \
      -DMOSAIC_GENERIC_DISPATCH_MASK="$DISPATCH_MASK" \
      -DMOSAIC_GENERIC_TDU="$TDU_ENABLED" \
      -DMOSAIC_GENERIC_TL_WINDOW="$TL_WINDOW" \
      -DMOSAIC_GENERIC_PRIMARY_SHARED="$PRIMARY_SHARED" \
      -DMOSAIC_GENERIC_FIXED_HART="$FIXED_HART" \
      -DMOSAIC_GENERIC_TESTBENCH_BOOTSTRAP="$BOOTSTRAP_DISPATCH" \
      -I "$MOSAIC_SW/include" -c "$HERE/prog_generic/generic.S" \
      -o "$FW_DIR/image_${IMAGE_ID}.o" || exit 1
  "$TC"-ld -T "$MOSAIC_SW/linker/image_${IMAGE_ID}.ld" \
      "$FW_DIR/image_${IMAGE_ID}.o" -o "$FW_DIR/image_${IMAGE_ID}.elf" || exit 1
  "$TC"-objcopy -O verilog "$FW_DIR/image_${IMAGE_ID}.elf" \
      "$FW_DIR/image_${IMAGE_ID}.hex" || exit 1
done < <("$PY" -c \
  'import json,sys; d=json.load(open(sys.argv[1])); hs={h["hart_id"]:h for h in d["harts"]}; b=bool(d.get("boot_policy",{}).get("testbench_hart0_bootstrap",False)); choose=lambda i: "ilp32e" if set(i.get("abis",[])) <= {"ilp32", "ilp32e"} and "ilp32e" in i.get("abis",[]) else "ilp32" if set(i.get("abis",[])) == {"ilp32"} else "lp64" if set(i.get("abis",[])) == {"lp64"} else ""; free=lambda i: any(hs[h]["role"] == "titan" for h in i["harts"]) or (b and 0 in i["harts"]); shared=lambda i: free(i) and len(i["harts"]) > 1 and not (b and 0 in i["harts"]); good=all(len(i.get("xlens",[]))==1 and bool(choose(i)) for i in d["images"]); [print(i["image_id"], int(free(i)), int(any(hs[h]["ip"] in {"rocket", "boom"} for h in i["harts"])), int(shared(i)), i["harts"][0], i["xlens"][0], choose(i)) for i in d["images"]] if good else None; sys.exit(0 if good else 1)' \
  "$BOOT_MANIFEST") || { echo "generic firmware build failed"; exit 1; }
if [ "$XIP" = "1" ]; then
  # Execute-in-place: images are linked at flash addresses and nothing is
  # staged into RAM, so the flash image is the images themselves rebased to
  # flash offsets. The shared-control window lives in the on-chip scratchpad
  # and is zeroed by reset, not preloaded.
  "$PY" "$HERE/pack_xip_hex.py" --manifest "$BOOT_MANIFEST" \
      --output "$FW_DIR/generic.hex" "$FW_DIR"/image_*.hex || exit 1
  echo "    flash image: $FW_DIR/generic.hex ($NUM_HARTS harts, wake mask $WAKE_MASK, XIP)"
else
  "$PY" -c \
    'import json,sys; from pathlib import Path; d=json.load(open(sys.argv[1])); out=Path(sys.argv[2]); images=[Path(p) for p in sys.argv[3:]]; base=int(d["memory"]["shared_control_base"],0); size=int(d["memory"]["shared_control_size"]); rows=[" ".join(["00"]*min(16,size-off)) for off in range(0,size,16)]; out.write_text("".join(p.read_text() for p in images)+f"\n@{base:08X}\n"+"\n".join(rows)+"\n")' \
    "$BOOT_MANIFEST" "$FW_DIR/generic.hex" "$FW_DIR"/image_*.hex || exit 1
  echo "    firmware: $FW_DIR/generic.hex ($NUM_HARTS harts, wake mask $WAKE_MASK)"
fi

if [ "${GENERIC_FW_ONLY:-0}" = "1" ]; then
  echo "### FW-ONLY: firmware image built, stopping before the Verilator sim"
  exit 0
fi

echo "### [3/4] building the full-SoC Verilator model ..."
rm -rf "$OBJ"
"$PY" "$HERE/gen_filelist.py" "$REPO" --manifest "$MANIFEST" \
    --build-root "$BUILD_ROOT" > "$FILELIST" \
    || { echo "filelist gen failed"; exit 1; }
verilator --binary -j 0 --top-module tb_top --Mdir "$OBJ" -o Vtb_top \
    --timescale 1ns/1ps -GUSE_EXTERNAL_DEVICE_EXAMPLE=1 -GJTAG_DPI=0 \
    -f "$FILELIST" > "$HERE/build-generic.log" 2>&1
[ -x "$OBJ/Vtb_top" ] \
    || { echo "### BUILD FAILED — see $HERE/build-generic.log"; exit 1; }

echo "### [4/4] running topology-generic liveness firmware ..."
if [ "$XIP" = "1" ]; then
  # boot_sel=1 selects flash; execute_from_flash=1 selects the memory-mapped
  # SPI (spimemio) window the boot ROM enables in _execute_from_flash.
  BOOT_ARGS="+boot_sel=1 +execute_from_flash=1"
else
  BOOT_ARGS="+boot_sel=0"
fi
# shellcheck disable=SC2086
"$OBJ/Vtb_top" +firmware="$FW_DIR/generic.hex" $BOOT_ARGS \
    +maxcycles=2000000 +verbose 2>&1 | tee "$HERE/sim-generic.log" | tail -30
SIM_RC=${PIPESTATUS[0]}
if [ "$SIM_RC" -eq 0 ] && grep -q "EXIT SUCCESS" "$HERE/sim-generic.log"; then
  echo "### RESULT: EXIT SUCCESS — all $NUM_HARTS configured harts executed ✓"
else
  echo "### RESULT: simulation failed or no EXIT SUCCESS"
  exit 1
fi
