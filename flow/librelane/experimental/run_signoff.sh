#!/usr/bin/env bash
# End-to-end signoff run for the Chipathon Block A macro.
#
# The point of this script is what it does NOT contain: there is no --skip.
# Every other run in experimental/ passes a long list of them to shorten the
# loop, which is how "0 routing DRC" got mistaken for "DRC clean". Here Magic
# DRC, KLayout DRC, Magic SPICE extraction, Netgen LVS, KLayout XOR, the
# antenna checks and the IR-drop/power-grid analysis all run, and any of them
# can fail the flow.
#
# Runtime is hours. Expect DRC and LVS to find things on the first attempt --
# that is the purpose.
#
# Usage:  ./run_signoff.sh [run-tag] [config]
#         MOSAIC_CFG=configs/<design>.yaml ./run_signoff.sh <tag> <config>
#
# The config defaults to Block A. MOSAIC_CFG selects which generated RTL bundle
# is hardened and already had to be set separately, so the two must agree: the
# hardening config names the top module, MOSAIC_CFG decides what is inside it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLOW="$(cd "$HERE/.." && pwd)"

# Where this run WRITES: the derived config, the resolved config, the file list,
# runs/ and the saved views. Defaults to the script's own directory, which is
# what every experimental run has always used.
#
# MOSAIC_WORK_DIR=flow/librelane/integration puts a tapeout run beside the
# integrator's DEF instead of among the experiments. The distinction is worth a
# directory: experimental/ holds runs we made up, integration/ holds the one we
# ship and the files someone else gave us to ship it against.
WORK="${MOSAIC_WORK_DIR:-$HERE}"
case "$WORK" in /*) ;; *) WORK="$(cd "$FLOW/../.." && pwd)/$WORK" ;; esac
mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd)"
TAG="${1:-blocka_signoff}"
CONFIG="${2:-${SIGNOFF_CONFIG:-experimental/config_blocka_signoff.yaml}}"

# MOSAIC_HARDEN_FROM_SOC=<mosaic.yaml> derives the hardening config instead of
# reading a hand-written one, closing the loop: SoC config -> floorplan -> flow.
# The derived file is written into experimental/ (so `dir::` still resolves) and
# named after the design, and it is a build product -- the closure ignore rules
# cover .resolved_*/.filelist_*, and this one is regenerated every run.
#
# MOSAIC_HARDEN_UTIL, _CLOCK_NS and _MARGIN override the utilisation, the clock
# period and the slew repair margin of that derivation -- one run at a time,
# without editing the SoC config. That is how `physical-intent optimize` moves
# one knob per run. They mean nothing without MOSAIC_HARDEN_FROM_SOC, and
# silently ignoring one would make the run not the run someone configured.
if [ -z "${MOSAIC_HARDEN_FROM_SOC:-}" ] && \
   [ -n "${MOSAIC_HARDEN_UTIL:-}${MOSAIC_HARDEN_CLOCK_NS:-}${MOSAIC_HARDEN_MARGIN:-}" ]; then
  echo "ERROR: MOSAIC_HARDEN_UTIL/_CLOCK_NS/_MARGIN need MOSAIC_HARDEN_FROM_SOC" >&2
  exit 2
fi
if [ -n "${MOSAIC_HARDEN_FROM_SOC:-}" ]; then
  DERIVED_DESIGN="$(sed -nE 's/^DESIGN_NAME:[[:space:]]*([A-Za-z_][A-Za-z0-9_]*).*/\1/p' \
      "$FLOW/$CONFIG" 2>/dev/null | head -1)"
  DERIVED_DESIGN="${MOSAIC_HARDEN_DESIGN:-$DERIVED_DESIGN}"
  if [ -z "$DERIVED_DESIGN" ]; then
    echo "ERROR: set MOSAIC_HARDEN_DESIGN to the top module name" >&2
    exit 2
  fi
  # Derived beside the run, so `dir::` in it resolves against the same
  # directory the flow reads the resolved config from.
  CONFIG="${WORK#$FLOW/}/.generated_${DERIVED_DESIGN}.yaml"
  echo "### deriving $CONFIG from $MOSAIC_HARDEN_FROM_SOC"
  # `python3 -m harness` only resolves from the repo root: harness.core imports
  # util.xheep_gen, which is deliberately not a package, so cwd is what puts it
  # on sys.path. This script has already cd'd to $FLOW.
  ( cd "$(cd "$FLOW/../.." && pwd)" && python3 -m harness physical-intent harden \
      --config "$MOSAIC_HARDEN_FROM_SOC" --design "$DERIVED_DESIGN" \
      --output "$FLOW/$CONFIG" \
      ${MOSAIC_HARDEN_UTIL:+--utilisation "$MOSAIC_HARDEN_UTIL"} \
      ${MOSAIC_HARDEN_CLOCK_NS:+--clock-period "$MOSAIC_HARDEN_CLOCK_NS"} \
      ${MOSAIC_HARDEN_MARGIN:+--repair-margin-pct "$MOSAIC_HARDEN_MARGIN"} \
      ${MOSAIC_PIN_TEMPLATE:+--pin-template "$MOSAIC_PIN_TEMPLATE"} \
      ${MOSAIC_HARDEN_TEMPLATE:+--template "$MOSAIC_HARDEN_TEMPLATE"} ) \
      || { echo "ERROR: could not derive a hardening config" >&2; exit 2; }
fi
LOG="/tmp/ll_${TAG}.log"

cd "$FLOW"

if [ ! -d "$FLOW/gf180mcu" ]; then
  echo "ERROR: PDK missing. Run 'make clone-pdk' in $FLOW first." >&2
  exit 2
fi

if [ ! -f "$FLOW/$CONFIG" ]; then
  echo "ERROR: no such hardening config: $CONFIG" >&2
  exit 2
fi

# Refuse to run if anyone has added a skip to the config itself.
# Match a STEP substitution only -- `substituting_steps`, or a LibreLane step id
# (Namespace.Step) nulled out. An earlier version rejected ANY `: null`, which
# also caught legitimate config values such as MAX_CAPACITANCE_CONSTRAINT: null
# (used to fall back to per-cell liberty limits). That would have blocked a
# correct signoff config for looking superficially like a skip.
if grep -qE '^\s*(substituting_steps\b|[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9]*\s*: *null)' \
     "$FLOW/$CONFIG"; then
  echo "ERROR: $CONFIG substitutes or nulls a flow step." >&2
  echo "       A signoff run must not skip steps. Aborting." >&2
  exit 3
fi

# ── resolve the source list for THIS machine ──────────────────────────────
# The config deliberately carries no absolute paths (see the note in it). The
# file list is derived from the FuseSoC manifest at run time, so a fresh clone
# hardens without hand-editing 526 paths, and a stale bundle cannot silently
# feed old RTL into the flow.
REPO="$(cd "$FLOW/../.." && pwd)"
MANIFEST="${MOSAIC_MANIFEST:-}"
if [ -z "$MANIFEST" ]; then
  # Absolute paths: this script runs from flow/librelane, not the repo root.
  MANIFEST="$("$REPO/.venv/bin/python" "$REPO/util/xheep_gen/build_manifest.py" locate \
      --config "${MOSAIC_CFG:-$REPO/configs/mosaic_tapeout_ultra.yaml}" \
      --base-config "$REPO/configs/general.hjson" \
      --pads-cfg "$REPO/configs/pad_cfg.py" \
      --repo-root "$REPO" 2>/dev/null)" || MANIFEST=""
fi
if [ -z "$MANIFEST" ] || [ ! -f "$MANIFEST" ]; then
  echo "ERROR: no MOSAIC manifest. Generate the RTL first:" >&2
  echo "  make mosaic-gen MOSAIC_CFG=${MOSAIC_CFG:-configs/mosaic_tapeout_ultra.yaml}" >&2
  exit 2
fi
BUILD_ROOT="$(ls -dt "$(dirname "$MANIFEST")"/runs/fusesoc.*/build 2>/dev/null | head -1)"
if [ -z "$BUILD_ROOT" ] || [ ! -d "$BUILD_ROOT" ]; then
  echo "ERROR: no FuseSoC build root under $(dirname "$MANIFEST")/runs/" >&2
  echo "  make mosaic-gen MOSAIC_CFG=${MOSAIC_CFG:-configs/mosaic_tapeout_ultra.yaml}" >&2
  exit 2
fi

RESOLVED="$WORK/.resolved_${TAG}.yaml"
FRAGMENT="$WORK/.filelist_${TAG}.yaml"
# The delivery wrapper is named after the design: DESIGN_NAME must equal the
# top module name, so mosaic_block_b lives in experimental/mosaic_block_b.sv.
# Deriving it here keeps the config the single place the design is named.
DESIGN="$(sed -nE 's/^DESIGN_NAME:[[:space:]]*([A-Za-z_][A-Za-z0-9_]*).*/\1/p' "$FLOW/$CONFIG" | head -1)"
if [ -z "$DESIGN" ]; then
  echo "ERROR: $CONFIG declares no DESIGN_NAME" >&2
  exit 2
fi
WRAPPER="flow/librelane/experimental/${DESIGN}.sv"
if [ ! -f "$REPO/$WRAPPER" ]; then
  echo "ERROR: no delivery wrapper for design '$DESIGN' at $WRAPPER" >&2
  echo "       DESIGN_NAME must equal the top module name, and the wrapper" >&2
  echo "       file is named after it." >&2
  exit 2
fi

python3 "$FLOW/scripts/gen_filelist.py" "$REPO" \
    --manifest "$MANIFEST" --build-root "$BUILD_ROOT" --output "$FRAGMENT" \
    --wrapper "$WRAPPER" \
    || { echo "ERROR: could not resolve the source list" >&2; exit 2; }
# LibreLane takes one config file, so the template and the resolved lists are
# concatenated into a run-local copy. It lives beside the template so any
# `dir::` references keep resolving.
cat "$FLOW/$CONFIG" "$FRAGMENT" > "$RESOLVED"
echo "### sources : $(grep -c '^- /' "$FRAGMENT") files + include dirs, resolved from $(basename "$(dirname "$MANIFEST")")"

# ── preflight ─────────────────────────────────────────────────────────────
# One second, against a flow that runs for hours. Every check here was bought
# with a wasted run on 2026-09-09/10: a disk that filled at step 53 of 60
# three hours in, a config that was a fragment and silently took LibreLane's
# defaults, and a run tag whose stale step directories were read as the live
# run. It checks the RESOLVED config, because that is what LibreLane gets.
#
# No bypass, deliberately -- the same reason this script has no --skip. A
# gate with an escape hatch is a gate that gets escaped at 2am.
( cd "$(cd "$FLOW/../.." && pwd)" && python3 -m harness flow-preflight harden \
      --config "$RESOLVED" --tag "$TAG" ) \
  || { echo "ERROR: preflight failed; fix the above before spending hours" >&2; exit 2; }

# ── machine resource limits, opt-in ───────────────────────────────────────
# Thread counts belong to the MACHINE, not the design, so they have no place
# in signoff_template.yaml (shared by every design) or in the derived
# per-design section. They cannot be left unset either: KLayout DRC on
# mosaic_block_c was SIGKILLed twice by the OOM killer at the deck's default
# thread count (Etc.nprocessors, 8 here) on a machine with 11 GB whose /tmp is
# a tmpfs competing for the same RAM.
#
# The first time, the fix was appending a key by hand to .resolved_<tag>.yaml.
# That works and is unreproducible: .resolved_* is a regenerated build product,
# so the run that produced the evidence could not be repeated from the repo.
#
# ONLY keys ending in _THREADS are accepted, and that restriction is the point.
# A thread count changes how long a check takes, never whether it passes, so
# this cannot become the --skip the rest of this script refuses to have.
if [ -n "${MOSAIC_RESOURCE_CONFIG:-}" ]; then
  RES="$MOSAIC_RESOURCE_CONFIG"
  [ -f "$RES" ] || RES="$FLOW/$MOSAIC_RESOURCE_CONFIG"
  if [ ! -f "$RES" ]; then
    echo "ERROR: no such resource config: $MOSAIC_RESOURCE_CONFIG" >&2
    exit 2
  fi
  # Reject the whole file on the first key that is not a thread count, rather
  # than filtering: silently dropping a line the caller meant to take effect is
  # how a run ends up not being the run someone thinks they configured.
  if BAD="$(grep -vE '^\s*(#.*)?$' "$RES" | grep -vE '^[A-Z][A-Z0-9_]*_THREADS: *[0-9]+ *(#.*)?$' | head -3)" \
     && [ -n "$BAD" ]; then
    echo "ERROR: $RES may only set *_THREADS keys. Rejected:" >&2
    printf '  %s\n' "$BAD" >&2
    exit 3
  fi
  cat "$RES" >> "$RESOLVED"
  echo "### limits : $(grep -cE '^[A-Z]' "$RES") resource keys from $(basename "$RES")"
fi

echo "### SIGNOFF — no steps skipped"
echo "### design : $DESIGN  (wrapper $WRAPPER)"
echo "### config : $CONFIG"
echo "### rtl    : ${MOSAIC_CFG:-configs/mosaic_tapeout_ultra.yaml}"
echo "### tag    : $TAG"
echo "### log    : $LOG"
echo

# ── the routing guard, opt-in ─────────────────────────────────────────
# MOSAIC_WATCH_ROUTING=1 polls the detailed-routing trajectory and kills the
# run once it has plateaued. Block C at a 75% target spent ELEVEN HOURS not
# converging; the guard would have called it at 1.15 h.
#
# Opt-in, and it stays that way until it has watched runs it did not also
# provide the training data for. It is a kill switch on multi-hour jobs, and
# the cost of a false positive is a wasted afternoon.
#
# It kills the librelane process group, not the shell's, so `set -e` and the
# evidence section below still run.
if [ "${MOSAIC_WATCH_ROUTING:-0}" != "1" ]; then
  # The proven path, unchanged. The guard must not alter how a run behaves
  # when it is switched off.
  nix develop --command librelane "$RESOLVED" \
      --pdk gf180mcuD --pdk-root "$FLOW/gf180mcu" --manual-pdk \
      --run-tag "$TAG" \
      --save-views-to "$WORK/final_${TAG}" \
      2>&1 | tee "$LOG"
else
  echo "### routing guard: on (polling every ${MOSAIC_WATCH_POLL:-300}s)"
  # `setsid` makes librelane a process-group leader so the whole tree can be
  # signalled with `kill -- -PID`. Backgrounding a PIPELINE would not do:
  # bash sets $! to the LAST command in it, so `... | tee log &` yields tee's
  # pid and the kill would hit the logger while OpenROAD carried on.
  setsid nix develop --command librelane "$RESOLVED" \
      --pdk gf180mcuD --pdk-root "$FLOW/gf180mcu" --manual-pdk \
      --run-tag "$TAG" \
      --save-views-to "$WORK/final_${TAG}" \
      > "$LOG" 2>&1 &
  LIBRELANE_PID=$!
  tail -f "$LOG" & TAIL_PID=$!

  (
    # `set -e` is inherited into this subshell, and exit status 3 is precisely
    # what the watcher is looking for -- under -e the poll would kill the
    # watcher instead of reporting. Turn it off deliberately.
    set +e
    while kill -0 "$LIBRELANE_PID" 2>/dev/null; do
      sleep "${MOSAIC_WATCH_POLL:-300}"
      ( cd "$REPO" && python3 -m harness physical-intent watch \
          --run-dir "$WORK/runs/$TAG" --fail-on-plateau ) >/dev/null 2>&1
      rc=$?
      # ONLY 3 means "plateaued". Anything else is the watcher itself failing
      # -- routing not started, no log yet, a bad import -- and a guard that
      # kills multi-hour runs when it cannot tell is worse than no guard.
      if [ "$rc" -eq 3 ]; then
        {
          echo "### ROUTING GUARD: plateau detected — killing the run"
          ( cd "$REPO" && python3 -m harness physical-intent watch \
              --run-dir "$WORK/runs/$TAG" 2>&1 )
        } >> "$LOG"
        touch "$WORK/runs/$TAG/.plateau_abort"
        kill -TERM -- "-$LIBRELANE_PID" 2>/dev/null
        exit 0
      fi
    done
  ) &
  WATCHER_PID=$!

  set +e
  wait "$LIBRELANE_PID"
  set -e
  kill "$WATCHER_PID" 2>/dev/null || true
  kill "$TAIL_PID" 2>/dev/null || true
fi

RUN="$WORK/runs/$TAG"

if [ -f "$RUN/.plateau_abort" ]; then
  echo
  echo "### ABORTED BY THE ROUTING GUARD"
  echo "### This run was killed because detailed routing plateaued, NOT"
  echo "### because any check failed. There is no signoff evidence below."
  HARTS="$(cd "$REPO" && python3 -c "
import yaml
soc = (yaml.safe_load(open('${MOSAIC_CFG:-configs/mosaic_tapeout_ultra.yaml}')) or {}).get('soc') or {}
print(sum(int(g.get('count', 1)) for g in (soc.get('cores') or [])))
" 2>/dev/null)"
  UTIL="$(sed -nE 's/^# utilisation ([0-9.]+)%.*/\1/p' "$FLOW/$CONFIG" | head -1)"
  if [ -n "$HARTS" ] && [ -n "$UTIL" ]; then
    ( cd "$REPO" && python3 -c "
from harness.physical.retry import next_utilisation
d = next_utilisation($HARTS, $UTIL / 100.0, 1)
print('### retry at MOSAIC_HARDEN_UTIL=%.2f -- %s' % (d.utilisation, d.reason)
      if d.retry else '### do not retry: ' + d.reason)
" )
  fi
  exit 4
fi
echo
echo "### SIGNOFF EVIDENCE"
# Report names as LibreLane 3.0 actually writes them. An earlier version of
# this list guessed, matched nothing, and printed an empty evidence section
# under a passing run -- which is the exact failure this script exists to stop.
for name in \
    "*-magic-drc/reports/drc.magic.rpt" \
    "*-klayout-drc/reports/*.rpt" \
    "*-netgen-lvs/reports/lvs.netgen.rpt" \
    "*-klayout-xor/reports/*.rpt" \
    "*-openroad-checkantennas-*/reports/antenna_summary.rpt" \
    "*-openroad-irdropreport/*.rpt"
do
    for f in $RUN/$name; do
        [ -f "$f" ] && printf '  %-58s %s\n' "${f#$RUN/}" "$(wc -l < "$f") lines"
    done
done

# Reported through the evidence parser rather than by eye: it will not call a
# missing metrics file clean.
cd "$FLOW/../.."
python3 - "$RUN" <<'PY'
import sys, pathlib
sys.path.insert(0, ".")
from harness.evidence import librelane as L
run = pathlib.Path(sys.argv[1])
# load_metrics returns (data, source_path). The second value is NOT an error --
# treating it as one made this script report "metrics unavailable" for a run
# that had completed cleanly. Absence shows up as an empty dict.
metrics, source = L.load_metrics(run)
if not metrics:
    print(f"  METRICS UNAVAILABLE under {run}/final -- verdict is UNKNOWN, not clean")
    raise SystemExit(1)
print(f"  source: {source}\n")
for k in ("magic__drc_error__count", "klayout__drc_error__count",
          "magic__illegal_overlap__count", "design__lvs_error__count",
          "design__lvs_unmatched_device__count", "design__lvs_unmatched_net__count",
          "design__lvs_unmatched_pin__count", "design__xor_difference__count",
          "route__drc_errors", "route__antenna_violation__count",
          "design__disconnected_pin__count"):
    print(f"  {k:42s} {metrics.get(k, '(NOT REPORTED)')}")

# Setup/hold closing says nothing about slew/cap/fanout, and those are the ones
# this design actually violates.
print()
for k in ("timing__setup__ws", "timing__hold__ws",
          "design__max_slew_violation__count",
          "design__max_cap_violation__count",
          "design__max_fanout_violation__count"):
    print(f"  {k:42s} {metrics.get(k, '(NOT REPORTED)')}")

adverse = L.adverse_metrics(metrics)
per_corner = [kv for kv in adverse if "__corner:" not in kv[0]]
print(f"\n  adverse metrics flagged: {len(adverse)} ({len(per_corner)} excluding per-corner duplicates)")
for k, v in per_corner[:12]:
    print(f"    {k:52s} {v}")
PY
