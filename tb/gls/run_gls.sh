#!/bin/bash
# Gate-level simulation of a hardened MOSAIC block macro -- any of them.
#
# Runs the POST-PLACE-AND-ROUTE netlist -- the gates that are in the GDS -- with
# the PDK's own cell models, booting XIP from a behavioural QSPI flash and
# reporting only through the 22 bonded pins. See gls_tb.sv.
#
# Icarus is used rather than Verilator: these models are UDP-based, which
# Verilator cannot simulate at all.
#
# SDF NOTE: timing-annotated GLS is NOT possible with these models under Icarus.
# Compiling them with -gspecify fails --
#     sorry: ifnone with an edge-sensitive path is not supported
# -- so the models are compiled -DFUNCTIONAL, which drops the specify blocks and
# with them the paths SDF would annotate. Timing-annotated GLS on GF180 needs a
# simulator that supports ifnone edge paths. What runs here is a functional
# check of the routed netlist, which is still the thing that catches
# synthesis/P&R mis-implementation; it is not a timing check. STA already covers
# timing at nine corners.
#
# SEQUENTIAL CLOCK-TO-Q. -DFUNCTIONAL leaves the flops as UDP primitives with
# ZERO clock-to-Q, and zero-delay UDP flops are the textbook simulation race:
# what a flop samples depends on event ordering, and inserting a buffer changes
# ordering without changing logic. That race produced a real false alarm -- two
# netlists whose 36,572 logic instances are identical, differing only in
# buffer/inverter/delay/fill cells, disagreed on whether the part boots. So the
# sequential UDPs are given a nonzero clock-to-Q (GLS_SEQ_DELAY, default 1 ns
# against a 100 ns clock) in a DERIVED copy of the cell models. The PDK file is
# never written. Combinational settling is then instantaneous relative to
# clock-to-Q, so a buffer-only netlist difference can no longer flip a capture.
# GLS_SEQ_DELAY=0 restores the old zero-delay model for comparison.
#
# Usage:
#   GLS_RUN=<runs/tag> ./run_gls.sh     functional GLS on that run
#   GLS_SEQ_DELAY=<ns>  ./run_gls.sh    sequential clock-to-Q; 0 = zero-delay
#   GLS_NETLIST=<nl.v> ./run_gls.sh     post-synthesis netlist instead
#   GLS_FIRMWARE=<hex> ./run_gls.sh     a different flash image
#   GLS_DESIGN=<top>   ./run_gls.sh     override the top module name
#   GLS_MAXCYCLES=<n>  ./run_gls.sh     watchdog (default 2,000,000)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
cd "$REPO"

RUN="${GLS_RUN:-$REPO/flow/librelane/experimental/runs/blocka_signoff}"
PDK="$REPO/flow/librelane/gf180mcu/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog"
# Post-PnR netlist by default: that is what the GDS contains, fill and antenna
# cells included. NL (post-synthesis) is available via GLS_NETLIST for
# comparison, but it is not what gets manufactured.
# The design name comes from the RUN, not from a hardcoded string. It was
# `mosaic_block_a` here, which is why Block B and C had "never been gate-level
# simulated" -- not a decision, just a path that could only ever resolve for
# one design. All three delivery wrappers carry an IDENTICAL 11-port list, so
# the testbench needs no other change; the module name is passed as GLS_DUT.
DESIGN="${GLS_DESIGN:-$(python3 -c "
import json,sys
try:
    print((json.load(open('$RUN/resolved.json')) or {}).get('DESIGN_NAME','') or '')
except Exception:
    print('')" 2>/dev/null)}"
[ -n "$DESIGN" ] || { echo "ERROR: no DESIGN_NAME in $RUN/resolved.json -- set GLS_DESIGN" >&2; exit 2; }
NETLIST="${GLS_NETLIST:-$RUN/final/pnl/$DESIGN.pnl.v}"
# The top module the testbench instantiates is NOT always the macro. With a
# padframe model in between, the tb binds the wrapper and the wrapper binds the
# macro. DESIGN still names the netlist file and the module inside it; GLS_TOP
# names what the tb should instantiate. They were the same variable, so asking
# for a wrapper silently changed which netlist file was looked up.
TOP="${GLS_TOP:-$DESIGN}"
CORNER="${2:-max_ss_125C_4v50}"
MAXCYCLES="${GLS_MAXCYCLES:-2000000}"

SDF_ARG=""

# GLS_VCD=<path> captures switching activity for workload power. The window is
# bounded because this netlist is ~200k cells and a full-boot dump runs to tens
# of gigabytes: GLS_VCD_START/GLS_VCD_CYCLES select the region of interest.
# `mosaic physical-intent power` then feeds the VCD to OpenSTA.
VCD_ARG=""
if [ -n "${GLS_VCD:-}" ]; then
  VCD_ARG="+vcd=${GLS_VCD} +vcd_start=${GLS_VCD_START:-100} +vcd_cycles=${GLS_VCD_CYCLES:-1000}"
  echo "### activity  : ${GLS_VCD} (cycles ${GLS_VCD_START:-100}..$(( ${GLS_VCD_START:-100} + ${GLS_VCD_CYCLES:-1000} )))"
fi
if [ "${1:-}" = "--sdf" ]; then
  echo "ERROR: SDF annotation is not supported with these models under Icarus." >&2
  echo "       The GF180 cell models use ifnone with edge-sensitive paths, which" >&2
  echo "       iverilog rejects (-gspecify), so they are compiled -DFUNCTIONAL and" >&2
  echo "       carry no annotatable timing paths. Refusing rather than running a" >&2
  echo "       zero-delay simulation and calling it timing-annotated." >&2
  exit 2
fi

[ -f "$NETLIST" ] || { echo "ERROR: netlist missing: $NETLIST" >&2; exit 2; }

# Firmware: the topology-generic liveness image. It boots the TITAN from the
# boot ROM, executes XIP from flash, wakes the worker through the TDU and writes
# the exit register -- i.e. it exercises the whole part through the pins.
FW="${GLS_FIRMWARE:-}"
if [ -z "$FW" ]; then
  # The image must come from THIS DESIGN's bundle. This used to glob
  # mosaic_tapeout_ultra-* whatever run it was handed, so a Block C netlist
  # would have been driven by Block A's liveness image -- a different hart
  # topology at different boot addresses -- and a pass would have meant nothing.
  # The run names its own bundle in VERILOG_FILES; the hash may differ from the
  # bundle that built the firmware, but the design must not.
  FAMILY="$(python3 -c "
import json, re, sys
try:
    resolved = json.load(open('$RUN/resolved.json'))
except Exception:
    sys.exit(0)
for path in resolved.get('VERILOG_FILES') or []:
    found = re.search(r'/build/mosaic/([A-Za-z0-9_]+)-[0-9a-f]{12}/', str(path))
    if found:
        print(found.group(1))
        break
" 2>/dev/null)"
  [ -n "$FAMILY" ] && FW="$(ls -t \
      "$REPO"/build/mosaic/"$FAMILY"-*/generated/generic_fw/generic.hex \
      2>/dev/null | head -1)"
fi
[ -n "$FW" ] && [ -f "$FW" ] || { echo "ERROR: no firmware hex for this design (${FAMILY:-unknown bundle}). Generate it with MOSAIC_CFG=<its config> tb/mosaic_soc/run_generic.sh, or set GLS_FIRMWARE." >&2; exit 2; }

# The flash model is vendored and design-independent (x-heep tb-utils), so any
# bundle's staging tree will do -- it need not be this design's.
FLASH="$(ls -t "$REPO"/build/mosaic/*/runs/fusesoc.*/build/src/x-heep__tb-utils_0/yosys_spiflash.sv 2>/dev/null | head -1)"
[ -n "$FLASH" ] && [ -f "$FLASH" ] || { echo "ERROR: spiflash model not found; run a FuseSoC setup first." >&2; exit 2; }

OBJ="$HERE/obj"
mkdir -p "$OBJ"

# Sequential clock-to-Q. See the note at the top: -DFUNCTIONAL strips the
# specify blocks, leaving the sequential UDPs with zero clock-to-Q, and that is
# what let a buffer-only netlist difference change the verdict. Patch a delay
# onto every sequential UDP INSTANCE in a derived copy -- the PDK models are
# read-only inputs and stay untouched. Delay goes on the UDP rather than the
# cell wrapper so combinational cells stay zero-delay: only the clock-to-Q arc
# needs to be nonzero to break the race.
SEQ_DELAY="${GLS_SEQ_DELAY:-1}"
CELLS="$PDK/gf180mcu_fd_sc_mcu7t5v0.v"
SEQ_NOTE="zero (races: buffering can flip a capture)"
if [ "$SEQ_DELAY" != "0" ]; then
  DERIVED="$OBJ/$(basename "${CELLS%.v}").seq${SEQ_DELAY}.v"
  # `<prefix>__udp_<name>(` -> `<prefix>__udp_<name> #<delay> (`. Anchored on
  # the instantiation shape, not on a cell-name list, so a PDK that names its
  # sequential primitives the same way needs no edit here.
  UDP_RE='^([[:space:]]*[A-Za-z_][A-Za-z0-9_]*__udp_[a-z0-9_]+)\('
  want=$(grep -cE "$UDP_RE" "$CELLS")
  sed -E "s|$UDP_RE|\1 #${SEQ_DELAY} (|" "$CELLS" > "$DERIVED"
  got=$(grep -cE "__udp_[a-z0-9_]+ #${SEQ_DELAY} \(" "$DERIVED")
  # If the models are ever reorganised so the pattern stops matching, the sed
  # is a silent no-op and the race comes back looking like a fix. Refuse.
  if [ "$want" -eq 0 ] || [ "$want" -ne "$got" ]; then
    echo "ERROR: sequential-delay patch matched $got of $want UDP instances in" >&2
    echo "       $CELLS -- refusing to run a simulation that claims a nonzero" >&2
    echo "       clock-to-Q it does not have. Set GLS_SEQ_DELAY=0 to opt out." >&2
    exit 2
  fi
  CELLS="$DERIVED"
  SEQ_NOTE="${SEQ_DELAY} ns on $got sequential UDPs"
fi
# Record WHICH hardening run this netlist came from. Without it the log is
# not attributable: tb/gls writes one log in a fixed place, so a stale result
# from another run reads as evidence about whichever run you happen to ask
# about. harness/evidence/gls.py checks for this line.
# ...and it has to go into the LOG, not only onto stdout. `tee` below used to
# OVERWRITE sim-gls.log with vvp's output alone, so the run tag never reached
# the file gls.py greps. Measured: every completed run returned NOT_RUN, "the
# gate-level log does not mention <tag>", including runs that had just printed
# EXIT SUCCESS. The identity check was reporting a passing simulation as one
# that never happened. Seed the log here, append the simulation to it.
{
echo "### run     : $RUN"
echo "### design  : $DESIGN"
[ "$TOP" != "$DESIGN" ] && echo "### tb top  : $TOP"
echo "### netlist : $(basename "$NETLIST") ($(du -h "$NETLIST" | cut -f1))"
echo "### firmware: $FW"
echo "### models  : $(basename "$PDK")/$(basename "$CELLS") + primitives.v"
echo "### seq c2q : $SEQ_NOTE"
} | tee "$HERE/sim-gls.log"

# USE_POWER_PINS: the netlist connects .VDD/.VNW/.VPW/.VSS on every instance, so
# the models must expose them or every instantiation is a port mismatch.
# GLS_PADWRAP=<file.sv> inserts a padframe model between the testbench and the
# macro. Needed once the macro stops driving its own pads: against an
# external padframe DEF it exposes 167 control terminals instead of 22 pins, and the
# testbench binds the 13-port face. The wrapper presents that face and speaks
# the control protocol inward. Set GLS_TOP to the WRAPPER (the netlist stays
# GLS_DESIGN, the macro), e.g. GLS_TOP=mosaic_block_a_padwrap.
PADWRAP="${GLS_PADWRAP:-}"
if [ -n "$PADWRAP" ]; then
  [ -f "$PADWRAP" ] || { echo "ERROR: no padframe model at $PADWRAP" >&2; exit 2; }
  echo "### padwrap : $(basename "$PADWRAP")"
fi

# The deposit list is scope-specific and is not tracked: generate it from the
# run's netlist with gen_powerup_init.py, for the hierarchy in use.
INITDEF=""
if [ -n "${GLS_POWERUP_INIT:-}" ]; then
  [ -f "$HERE/$GLS_POWERUP_INIT" ] || { echo "ERROR: no deposit list $GLS_POWERUP_INIT" >&2; exit 2; }
  INITDEF="-DGLS_POWERUP_INIT=\"$GLS_POWERUP_INIT\""
  echo "### deposits: $GLS_POWERUP_INIT"
fi

# shellcheck disable=SC2086
iverilog -g2012 -DUSE_POWER_PINS -DFUNCTIONAL -DGLS_DUT="$TOP" $INITDEF -I"$HERE" \
    -o "$OBJ/gls.vvp" \
    -s gls_tb \
    "$HERE/gls_tb.sv" \
    ${PADWRAP:+"$PADWRAP"} \
    "$NETLIST" \
    "$FLASH" \
    "$PDK/primitives.v" \
    "$CELLS" \
    2> "$HERE/compile.log"
RC=$?
if [ $RC -ne 0 ] || [ ! -f "$OBJ/gls.vvp" ]; then
  echo "### COMPILE FAILED — see $HERE/compile.log"
  tail -20 "$HERE/compile.log"
  exit 1
fi
echo "### compiled ($(grep -c . "$HERE/compile.log" 2>/dev/null) warnings)"

# Record the plusargs in the log. The watchdog bound is how a reader tells a
# finished run from a truncated one, and harness/evidence/workload.py looks for
# `+maxcycles=` there -- it was passed on the command line and never written
# down, so every parsed run reported an unknown bound.
# tee, not echo: the header block above is piped into sim-gls.log, and a bare
# echo here reaches the terminal only -- which is how the bound stayed out of
# the log that gets copied into the run.
echo "### plusargs : +firmware=$(basename "$FW") +maxcycles=$MAXCYCLES${VCD_ARG:+ $VCD_ARG}" \
  | tee -a "$HERE/sim-gls.log"

# shellcheck disable=SC2086
vvp "$OBJ/gls.vvp" +firmware="$FW" +maxcycles="$MAXCYCLES" $SDF_ARG $VCD_ARG \
    2>&1 | tee -a "$HERE/sim-gls.log" | grep -vE "^\[GLS\] [0-9]+ cycles" | tail -25
SIM_RC=${PIPESTATUS[0]}

# Keep a copy inside the run. The shared log holds only the LAST gate-level
# run, so one design's verdict erased every other's: simulating Block C reset
# Block A to NOT_RUN, and nothing else recorded it. A copy here belongs to this
# netlist and no other run can overwrite it. Copied before the verdict is
# printed so a FAILING run keeps its evidence too.
cp "$HERE/sim-gls.log" "$RUN/gls.log" 2>/dev/null \
  && echo "### recorded : $RUN/gls.log" \
  || echo "WARNING: could not record the log into $RUN" >&2

echo
if [ "$SIM_RC" -eq 0 ] && grep -q "EXIT SUCCESS" "$HERE/sim-gls.log"; then
  echo "### RESULT: gate-level simulation PASSED"
else
  echo "### RESULT: gate-level simulation FAILED (see $HERE/sim-gls.log)"
  exit 1
fi
