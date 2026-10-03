#!/usr/bin/env bash
# Run the tutorial's deterministic golden path from any working directory.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

CFG="tutorial/configs/tutorial_soc.yaml"
TOPOLOGY="build/tutorial/tutorial_soc_topology.html"

echo "### [1/6] preparing the Python environment"
if [ ! -x .venv/bin/python ] || \
   ! .venv/bin/python -c 'import fusesoc, hjson, mako, yaml' >/dev/null 2>&1; then
  make venv
fi
export PATH="$REPO_ROOT/.venv/bin:$PATH"
./.venv/bin/python --version

echo "### [2/6] checking required simulation tools"
if [ -n "${VERILATOR_PIN:-}" ] && [ -x "$VERILATOR_PIN/usr/bin/verilator" ]; then
  export PATH="$VERILATOR_PIN/usr/bin:$PATH"
  export VERILATOR_ROOT="$VERILATOR_PIN/usr/share/verilator"
fi
if ! command -v verilator >/dev/null 2>&1; then
  echo "ERROR: Verilator is not on PATH; install/source Verilator 5.x" >&2
  exit 1
fi
verilator --version

if [ -z "${RISCV_TC:-}" ]; then
  if riscv_gcc="$(command -v riscv32-unknown-elf-gcc 2>/dev/null)"; then
    RISCV_TC="${riscv_gcc%-gcc}"
  elif [ -x /opt/riscv32-gnu-toolchain-elf-bin/bin/riscv32-unknown-elf-gcc ]; then
    RISCV_TC=/opt/riscv32-gnu-toolchain-elf-bin/bin/riscv32-unknown-elf
  else
    echo "ERROR: set RISCV_TC to the prefix before -gcc/-ld/-objcopy" >&2
    exit 1
  fi
fi
export RISCV_TC
export RISCV_XHEEP="${RISCV_XHEEP:-$(dirname "$(dirname "$RISCV_TC")")}"
tc_name="${RISCV_TC##*/}"
export COMPILER_PREFIX="${COMPILER_PREFIX:-${tc_name%elf}}"
"${RISCV_TC}-gcc" --version | sed -n '1p'

echo "### [3/6] validating config and semantic topology"
./oh-my-soc config-author validate "$CFG"
./oh-my-soc topo-viz check "$CFG"
mkdir -p build/tutorial
./oh-my-soc topo-viz render "$CFG" -o "$TOPOLOGY"

echo "### [4/6] generating RTL and software contracts"
make mosaic-gen MOSAIC_CFG="$CFG"

echo "### [5/6] inspecting the generated content-addressed build"
MANIFEST="$(./.venv/bin/python util/xheep_gen/build_manifest.py locate \
  --config "$CFG" \
  --base-config configs/general.hjson \
  --pads-cfg configs/pad_cfg.py \
  --repo-root "$REPO_ROOT" \
  --output-root build/mosaic)"
python3 tutorial/inspect_manifest.py "$MANIFEST"

echo "### [6/6] proving all configured harts execute"
MOSAIC_CFG="$CFG" tb/mosaic_soc/run_generic.sh

echo "### Tutorial complete"
echo "### Topology: $TOPOLOGY"
