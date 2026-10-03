# Signoff SDC: identical to LibreLane's base.sdc except that the blanket
# `set_max_transition` is NOT applied, so each pin is checked against its own
# liberty max_transition at its own corner.
#
# WHY. max_transition in the GF180 library is declared per input pin AND per
# corner -- 4.0 ns at tt_025C_5v00, 7.0 ns at ss_125C_4v50, 2.6 ns at
# ff_n40C_5v50, 836 declarations per library file. MAX_TRANSITION_CONSTRAINT: 4
# emits `set_max_transition 4.0 [current_design]`, applying the TYPICAL
# corner's number at all nine. Every max-slew violation this project has
# counted is at an ss_125C_4v50 corner, where the pins are rated to 7.0 ns:
#
#     shipped SDC        _56916_/ZN  limit 4.00  slew 5.664  -1.664  VIOLATED
#     library per-pin    _56916_/ZN  limit 7.00  slew 5.664  +1.34   MET
#
# This file is used for SIGNOFF ONLY. PNR_SDC_FILE stays unset, so every
# implementation step still optimises against 4.0 ns. That split is the whole
# point and it is ordinary guardbanding: over-constrain the optimiser, sign off
# against the limit the cells are actually qualified to.
#
# IT IS NOT A WEAKER GATE. Measured: with MAX_TRANSITION_CONSTRAINT set to null
# so that PnR also stopped targeting 4.0, runs/blocka_libtran degraded and this
# same library-limit check caught it -- 10 max-slew violations at 7.1998 ns
# against the 7.0 ns limit, plus 5 max-capacitance violations against per-cell
# liberty limits. The oracle has teeth; it just stops reporting non-defects.
#
# HOW. Not a copy of base.sdc -- a copy would silently drift from the SDC that
# PnR uses, which is exactly the class of bug this whole thread is about. It
# unsets the variable base.sdc guards on, then sources base.sdc, so every other
# constraint is whatever LibreLane says it is.
#
# The two tokens on the next line are LOAD-BEARING, not decoration.
# openroad/common/io.tcl greps THIS file -- not the file it sources -- with
# `string_in_file $::env(_SDC_IN) "set_propagated_clock"` and
# "unset_propagated_clock", and applies its own clock propagation if neither is
# found. base.sdc handles propagation itself, so both names must appear here or
# propagation would be applied twice:
#     set_propagated_clock / unset_propagated_clock  <- keep this line
#
# For the same reason, this file must NOT contain a Tcl env reference to
# SYNTH_DRIVING_CELL_PIN -- spelled out, it would be the six characters
# `$::env(` followed by that name. io.tcl's `env_var_used` greps for exactly
# that literal and, on finding it, splits SYNTH_DRIVING_CELL before the SDC is
# read; base.sdc does the split itself, so the rewrite must not be triggered.
# (Writing the literal here even inside a comment is enough to trigger it. It
# was, on the first draft of this file, and the check below caught it.)

unset -nocomplain ::env(MAX_TRANSITION_CONSTRAINT)

if { [info exists ::env(FALLBACK_SDC)] } {
    set _mosaic_base_sdc $::env(FALLBACK_SDC)
} else {
    set _mosaic_base_sdc [file join $::env(SCRIPTS_DIR) base.sdc]
}
puts "\[INFO\] MOSAIC signoff SDC: sourcing $_mosaic_base_sdc without a blanket max_transition…"
source $_mosaic_base_sdc
