#!/bin/bash
# Full regression sweep — every suite the project claims passes.
#
# WHY THIS EXISTS
# ---------------
# The suite list lived only in DASHBOARD.md prose, so running "the sweep" meant
# copying ~30 commands out of a table by hand. Predictably it went unrun for
# three weeks, and when it was finally run again four steps failed — two of
# them masked by a sibling suite that shares a DASHBOARD row:
#
#   * tb/mosaic/run.sh checks "alive + executed" and never tests dormancy, so
#     it passed while the cocotb dormancy test failed.
#   * tb/sci/hazard3 passed because tb_smith adds Hazard3's include path
#     itself, while the full-SoC build could not find the same headers.
#
# A green row was hiding a red one. This script is the executable version of
# that table, so the claim and the check cannot drift apart again.
#
# Usage:
#   scripts/run_sweep.sh                 # everything (~40 min)
#   scripts/run_sweep.sh --list          # print the step names and exit
#   scripts/run_sweep.sh --only wake     # steps whose name matches a regex
#   SWEEP_LOGS=/path scripts/run_sweep.sh
#
# Exits NON-ZERO if any step fails, so it can gate a merge. Steps are never
# skipped silently: --only reports what it excluded.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

LOGS="${SWEEP_LOGS:-$REPO/build/sweep}"   # under build/, which is gitignored
ONLY=""
LIST_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST_ONLY=1; shift ;;
    --only) ONLY="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

EXIT_OK='EXIT SUCCESS|RESULT: EXIT SUCCESS'

# name | success-marker regex | command
#
# The marker matters as much as the exit status: several of these scripts exit
# 0 while printing a failure, so "did it exit 0" alone is not a pass.
STEPS=(
  "pytest|[0-9]+ passed|python3 -m pytest test/test_x_heep_gen -q"
  "tb_matrix_validate|report|./oh-my-soc tb-matrix run --tier validate"

  "tdu_soc_cocotb|PASS|passed|tb/tdu/soc/cocotb/run.sh"
  "mosaic_sci_wakeloop|PASS|SUCCESS|tb/mosaic/run.sh"
  "mosaic_sci_cocotb|TESTS=1 PASS=1 FAIL=0|tb/mosaic/cocotb/run.sh"
  "idma_cocotb|PASS|passed|tb/idma/cocotb/run.sh"
  "log_xbar|PASS|tb/log_xbar/run.sh"
  "tl_obi|PASS|tb/tl_obi/run.sh"
  "floonoc_bridges|PASS|passed|tb/floonoc/cocotb/run.sh"
  "floonoc_stage2|PASS|passed|tb/floonoc/cocotb/run.sh stage2"

  "sci_tb_serv|TB PASS|tb/sci/serv/run.sh"
  "sci_tb_fazyrv|TB PASS|tb/sci/fazyrv/run.sh"
  "sci_tb_picorv32|TB PASS|tb/sci/picorv32/run.sh"
  "sci_tb_hazard3|TB PASS|tb/sci/hazard3/run.sh"

  "wake_obi|$EXIT_OK|tb/mosaic_soc/run.sh"
  "wake_log|$EXIT_OK|MOSAIC_CFG=configs/mosaic_wake_demo_log.yaml tb/mosaic_soc/run.sh"
  "wake_floonoc|$EXIT_OK|MOSAIC_CFG=configs/mosaic_floonoc.yaml tb/mosaic_soc/run.sh"
  "wake_picorv32|$EXIT_OK|MOSAIC_CFG=configs/mosaic_picorv32.yaml tb/mosaic_soc/run.sh"
  "wake_snitch|$EXIT_OK|MOSAIC_CFG=configs/mosaic_snitch.yaml tb/mosaic_soc/run.sh"
  "wake_cva6|$EXIT_OK|MOSAIC_CFG=configs/mosaic_cva6.yaml tb/mosaic_soc/run.sh"
  "wake_new_cores|$EXIT_OK|MOSAIC_CFG=configs/mosaic_new_cores.yaml tb/mosaic_soc/run.sh"
  "wake_hazard3|$EXIT_OK|MOSAIC_CFG=configs/mosaic_hazard3.yaml tb/mosaic_soc/run.sh"
  "wake_rocket|$EXIT_OK|MOSAIC_CFG=configs/mosaic_rocket.yaml tb/mosaic_soc/run.sh"
  "wake_boom|$EXIT_OK|MOSAIC_CFG=configs/mosaic_boom.yaml tb/mosaic_soc/run.sh"
  "wake_berkeley|$EXIT_OK|MOSAIC_CFG=configs/mosaic_berkeley.yaml tb/mosaic_soc/run.sh"

  "titan_smp_obi|$EXIT_OK|MOSAIC_CFG=configs/mosaic_titan_obi.yaml tb/mosaic_soc/run_titan.sh"
  "titan_smp_log|$EXIT_OK|MOSAIC_CFG=configs/mosaic_titan_log.yaml tb/mosaic_soc/run_titan.sh"
  "titan_smp_floonoc|$EXIT_OK|MOSAIC_CFG=configs/mosaic_titan_floonoc.yaml tb/mosaic_soc/run_titan.sh"

  "firmware_7hart|$EXIT_OK|tb/mosaic_soc/run_fw.sh"
  "generic_boot_blocka|$EXIT_OK|MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml tb/mosaic_soc/run_generic.sh"
  "uart_bringup_blocka|$EXIT_OK|MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml tb/mosaic_soc/run_uart.sh"
  "gls_blocka|$EXIT_OK|tb/gls/run_gls.sh"
  "demo03_blocka_prompt|RESULT: prompt|MOSAIC_DEMO_AGENT=off ./demo/03_blocka_from_prompt.sh"
)

if [ "$LIST_ONLY" = 1 ]; then
  for entry in "${STEPS[@]}"; do echo "${entry%%|*}"; done
  exit 0
fi

mkdir -p "$LOGS"
RESULTS="$LOGS/results.tsv"
: > "$RESULTS"
EXCLUDED=0

for entry in "${STEPS[@]}"; do
  name="${entry%%|*}"
  rest="${entry#*|}"
  marker="${rest%|*}"
  cmd="${rest##*|}"

  if [ -n "$ONLY" ] && ! printf '%s' "$name" | grep -qE "$ONLY"; then
    EXCLUDED=$((EXCLUDED + 1))
    continue
  fi

  log="$LOGS/${name}.log"
  start=$SECONDS
  printf '=== [%s] %s\n' "$(date +%H:%M:%S)" "$name" >&2
  ( eval "$cmd" ) >"$log" 2>&1
  rc=$?
  dur=$((SECONDS - start))

  if [ "$rc" -ne 0 ]; then
    verdict="FAIL(exit=$rc)"
  elif ! grep -qE "$marker" "$log"; then
    verdict="FAIL(no-marker)"
  else
    verdict="PASS"
  fi
  printf '%s\t%s\t%ss\t%s\n' "$name" "$verdict" "$dur" "$log" >> "$RESULTS"
  printf '    -> %s (%ss)\n' "$verdict" "$dur" >&2
done

echo
echo "===================== SWEEP SUMMARY ====================="
awk -F'\t' '{printf "%-26s %-14s %6s\n", $1, $2, $3}' "$RESULTS"
echo "---------------------------------------------------------"
awk -F'\t' -v excluded="$EXCLUDED" '
  {total++; if ($2 ~ /^PASS/) pass++; else {fail++; failed = failed "\n  " $1 "  " $4}}
  END {
    printf "PASS %d   FAIL %d   (ran %d)\n", pass, fail, total
    if (excluded > 0) printf "NOT RUN: %d step(s) excluded by --only\n", excluded
    if (fail > 0) printf "failing steps:%s\n", failed
  }' "$RESULTS"
echo "logs: $LOGS"

# Non-zero if anything failed — without this the script is unusable as a gate,
# and "the sweep passed" would mean only "the sweep finished".
! grep -q $'\tFAIL' "$RESULTS"
