#!/bin/bash
# demo/01_soc_from_prompt.sh — visible prompt→verified-SoC agent workflow.
# Exits non-zero unless the full gated pipeline reaches EXIT SUCCESS.
set -euo pipefail
cd "$(dirname "$0")/.."

PROMPT="an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, tdu, a uart"

echo "### prompt: $PROMPT"
echo "### live agent workflow (plan -> typed tools -> gates -> EXIT SUCCESS):"
./oh-my-soc agent "$PROMPT" --driver deterministic --name prompted_demo
