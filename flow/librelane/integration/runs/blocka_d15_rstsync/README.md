# blocka_d15_rstsync

This directory holds the measured evidence of the `blocka_d15_rstsync` run:
Block A, the GF180MCU reference design, hardened against an external padframe
DEF. `final/metrics.json` holds the signoff metrics and the disconnected-pin
table is the one the waiver in `flow/librelane/signoff_waivers.yaml` is pinned
to. `resolved.json` is the run's resolved LibreLane configuration with the 33
path-valued keys removed. Layout data is not in the repository.

The run predates the renaming of the SoC module from `core_v_mini_mcu` to
`mosaic_soc`. Its netlist, and the violator names in the fan-out waiver, carry
the instance name `i_core_v_mini_mcu`; the wrapper now names that instance
`i_mosaic_soc`.
