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
# tb/tools.sh is the check every simulation runner applies: Verilator 5.050
# only (from VERILATOR_PIN, else PATH) and the RISC-V prefix from RISCV_TC,
# else the first riscv32-*-elf-gcc on PATH. `nix develop .#sim` provides both.
# Running it here stops before generation instead of at stage [6/6].
if [ -z "${VERILATOR_PIN:-}" ] && ! command -v verilator >/dev/null 2>&1; then
  echo "ERROR: no Verilator on PATH; run inside 'nix develop .#sim' or set VERILATOR_PIN" >&2
  exit 1
fi
source "$REPO_ROOT/tb/tools.sh"
mosaic_need_verilator
mosaic_need_riscv_tc
RISCV_TC="$TC"
export RISCV_TC
export RISCV_XHEEP="${RISCV_XHEEP:-$(dirname "$(dirname "$RISCV_TC")")}"
tc_name="${RISCV_TC##*/}"
export COMPILER_PREFIX="${COMPILER_PREFIX:-${tc_name%elf}}"
"${RISCV_TC}-gcc" --version | sed -n '1p'

echo "### [3/6] validating config and semantic topology"
./mosaic config-author validate "$CFG"
./mosaic topo-viz check "$CFG"
mkdir -p build/tutorial
./mosaic topo-viz render "$CFG" -o "$TOPOLOGY"

echo "### [4/6] generating RTL and software contracts"
make mosaic-gen MOSAIC_CFG="$CFG"

echo "### [5/6] inspecting the generated content-addressed build"
MANIFEST="$(./.venv/bin/python util/mosaic_gen/build_manifest.py locate \
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
