# tb/tools.sh -- sourced by every testbench runner; resolves Verilator and the
# RISC-V toolchain, and REFUSES instead of falling back to whatever is on PATH.
#
# A silent fallback to PATH is unsafe: a Verilator 5.047 development build
# miscompiles cv32e40x, and nothing in the simulation log says which Verilator
# produced the model.
#
# The reference toolchain is the root flake: `nix develop .#sim` provides both
# tools and sets RISCV_TC. harness/toolchain.py PINS must name the same version
# (test_toolchain_pin.py checks it).

MOSAIC_VERILATOR_VERSION="5.050"

# Verilator: $VERILATOR_PIN (an install prefix, bin/ or usr/bin/) if set;
# else PATH's verilator, only when it IS the pinned release.
mosaic_need_verilator() {
  if [ -n "${VERILATOR_PIN:-}" ]; then
    local bin
    for bin in "$VERILATOR_PIN/usr/bin" "$VERILATOR_PIN/bin"; do
      if [ -x "$bin/verilator" ]; then
        export PATH="$bin:$PATH"
        [ -d "$bin/../share/verilator" ] && export VERILATOR_ROOT="$(cd "$bin/../share/verilator" && pwd)"
        echo "verilator: $bin/verilator [$(verilator --version)] (VERILATOR_PIN)"
        return 0
      fi
    done
    echo "ERROR: VERILATOR_PIN=$VERILATOR_PIN has no bin/verilator or usr/bin/verilator" >&2
    exit 2
  fi
  local found
  found="$(verilator --version 2>/dev/null)"
  case "$found" in
    "Verilator $MOSAIC_VERILATOR_VERSION "*)
      echo "verilator: $(command -v verilator) [$found]"
      return 0 ;;
  esac
  echo "ERROR: Verilator $MOSAIC_VERILATOR_VERSION required; PATH has: ${found:-none}" >&2
  echo "  fix: nix develop .#sim          (the pinned toolchain)" >&2
  echo "   or: export VERILATOR_PIN=<install prefix of a $MOSAIC_VERILATOR_VERSION build>" >&2
  exit 2
}

# RISC-V GCC: sets TC to the tool prefix (".../bin/riscv32-<vendor>-elf").
# $RISCV_TC if set; else the first riscv32-*-elf-gcc on PATH.
mosaic_need_riscv_tc() {
  if [ -n "${RISCV_TC:-}" ]; then
    TC="$RISCV_TC"
  else
    local dir gcc
    TC=""
    IFS=: read -ra _mosaic_path <<< "$PATH"
    for dir in "${_mosaic_path[@]}"; do
      for gcc in "$dir"/riscv32-*-elf-gcc; do
        if [ -x "$gcc" ]; then TC="${gcc%-gcc}"; break 2; fi
      done
    done
  fi
  if [ -z "$TC" ] || [ ! -x "$TC-gcc" ]; then
    echo "ERROR: no RISC-V toolchain (RISCV_TC=${RISCV_TC:-unset}, no riscv32-*-elf-gcc on PATH)" >&2
    echo "  fix: nix develop .#sim          (the pinned toolchain)" >&2
    echo "   or: export RISCV_TC=<dir>/bin/riscv32-unknown-elf" >&2
    exit 2
  fi
}

# The Mako templates mcu_gen renders, one path per line. The runners share this
# one copy; MCU_GEN_TEMPLATES in the Makefile must select the same files (the
# two find expressions are kept equivalent). ./.claude holds agent
# worktrees -- whole repository copies -- whose templates would otherwise be
# rendered too, and whose count pushes the list past the kernel's 128 KiB
# per-argument limit ("Argument list too long").
mosaic_templates() {
  find . \( -path './build/*' -o -path './.claude/*' \
    -o -path './hw/vendor/*' ! -path './hw/vendor/xheep' ! -path './hw/vendor/xheep/*' \
    -o -path './util/*' \
    -o -path './test/*' -o -path './refs/*' \) -prune -o -name '*.tpl' -print
}
