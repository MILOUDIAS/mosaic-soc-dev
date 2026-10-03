# MOSAIC-SoC GF180MCU timing constraints.
#
# Adapted from the padring template. The MOSAIC PoC targets STA closure at
# 50 MHz (20 ns). The clock enters on the clk pad and is buffered by the
# Schmitt input cell clk_pad (output net clk_pad/Y).
#
# NOTE (multi-clock follow-up): the PoC instantiates several cores
# (cv32e20 + 2×fazyrv + 4×serv + TDU). If the generator introduces derived or
# gated clocks, add the corresponding create_generated_clock / set_clock_groups
# entries here. For the single-clock bring-up this constrains the one pad clock.

set clk_period 20.0
set clk_port   [get_ports {clk_PAD}]
set clk_net    clk_pad/Y

create_clock -name clk -period $clk_period $clk_net

# IO timing budget: charge inputs/outputs at IO_DELAY_CONSTRAINT % of the period.
if { ![info exists ::env(IO_DELAY_CONSTRAINT)] } { set ::env(IO_DELAY_CONSTRAINT) 20 }
set io_delay [expr $clk_period * $::env(IO_DELAY_CONSTRAINT) / 100.0]

set in_ports  [get_ports {input_PAD[*] bidir_PAD[*] rst_n_PAD}]
set out_ports [get_ports {bidir_PAD[*]}]

set_input_delay  -clock clk $io_delay $in_ports
set_output_delay -clock clk $io_delay $out_ports

# Reasonable transition/load assumptions for the pad-bounded design.
set_clock_transition 0.15 [get_clocks clk]
set_clock_uncertainty 0.25 [get_clocks clk]
