#!/bin/bash
# demo/02_wrap_new_core.sh — replay the wrap-any-core mechanism on Hazard3.
# The heavy lifting (vendor + agent-fill) is already committed; this script
# re-runs the DETERMINISTIC stages against the committed tree and re-verifies.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "### [1/4] analyze the vendored Hazard3 top (expect ahb_split @ 1.00):"
python3 -m harness wrapper-smith analyze \
    hw/vendor/mosaic/hazard3/rtl/hazard3_cpu_2port.v \
    --top hazard3_cpu_2port -o build/wrapper_smith/hazard3.analysis.json

echo "### [2/4] scaffold (idempotent: everything reports already-present):"
python3 -m harness wrapper-smith scaffold hazard3 \
    --from build/wrapper_smith/hazard3.analysis.json

echo "### [3/4] single-hart SCI TB (dormancy/wake/liveness/sentinel):"
python3 -m harness tb-smith generate hazard3
python3 -m harness tb-smith run hazard3

echo "### [4/4] full-SoC TDU wake demo (EXIT SUCCESS gate):"
python3 -m harness tb-smith wake-demo hazard3
