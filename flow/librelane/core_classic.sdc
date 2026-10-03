# MOSAIC SoC core — Classic-flow timing constraints.
#
# Hardens core_v_mini_mcu as a standalone macro: the clock arrives directly on
# the clk_i pin (no pad cell, unlike the Chip flow's clk_PAD/clk_pad/Y).
# Target STA closure: 50 MHz (20 ns).
#
# Multi-clock note: the PoC has several cores (cv32e20 + 2×fazyrv + 4×serv + TDU)
# on one clk_i. If the generator introduces derived/gated clocks, add the
# matching create_generated_clock / set_clock_groups entries here.

set clk_period 20.0
set clk_port   [get_ports {clk_i}]

create_clock -name clk -period $clk_period $clk_port

if { ![info exists ::env(IO_DELAY_CONSTRAINT)] } { set ::env(IO_DELAY_CONSTRAINT) 20 }
set io_delay [expr $clk_period * $::env(IO_DELAY_CONSTRAINT) / 100.0]

set all_in  [remove_from_collection [all_inputs]  [get_ports clk_i]]
set all_out [all_outputs]

set_input_delay  -clock clk $io_delay $all_in
set_output_delay -clock clk $io_delay $all_out

set_clock_transition 0.15 [get_clocks clk]
set_clock_uncertainty 0.25 [get_clocks clk]
