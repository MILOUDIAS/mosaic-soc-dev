# flow/librelane: the GF180MCU hardening flow

This directory turns generated MOSAIC RTL into a layout on GF180MCU with
LibreLane 3.0.0 and the wafer-space `gf180mcu` PDK at tag 1.8.0 (`gf180mcuD`).
The description of the flow, its gates and the measured results is in
[docs/physical-flow.md](../../docs/physical-flow.md). This page is the map of the
directory and the exact commands.

Third-party files in this directory and their licences are listed in `NOTICE`.

## Status

Checked against `util/mosaic_gen/core_registry.py` and the tracked run metrics.

- **GF180MCU is the only technology with a hardened design.** sky130 and IHP
  sg13g2 configurations are RTL and simulation only. `signoff_template_ihp.yaml`
  exists for experiments on IHP sg13g2 and is marked unqualified in its header.
- **The block-level signoff flow works.** `experimental/run_signoff.sh` hardens a
  delivery wrapper (`mosaic_block_a`, `mosaic_block_b`, `mosaic_block_c`) with no
  step skipped. Block A, the GF180MCU reference design, has a run that passes
  every hard check with two recorded waivers
  (`integration/runs/blocka_d15_rstsync`). Blocks B and C have runs that pass
  every hard check and are not tapeout-qualified.
- **`soc.target: tapeout` accepts exactly the Block A configuration**: two SERV
  harts on the `obi` fabric, no on-chip SRAM pool, a 128-byte scratchpad, a 1 KB
  boot ROM, no DMA, no debug module, no PLIC, one UART. Every other combination
  is refused for `tapeout` and remains valid for `rtl` and `simulation`.
- **The chip-level flow with a pad ring is not usable from this repository.**
  The `harden`, `classic` and `padring` Makefile targets require a
  `PHYSICAL_BUNDLE` containing a flattened netlist, a bound `mosaic_soc_core`
  adapter and SRAM macro views. None of these ship: `src/mosaic_soc_core.sv` is a
  placeholder that the preflight rejects, and no layout has been produced
  through this path.
- **Run directories are not in version control.** Only the `final/metrics.json`
  of the runs cited by the area model and the waivers is tracked, plus one
  disconnected-pin table.

## Directory map

| Path | Purpose |
|---|---|
| `experimental/run_signoff.sh` | the signoff runner; no skip option |
| `signoff_template.yaml` | the design-independent hardening settings shared by all blocks |
| `signoff_template_slewonly.yaml`, `signoff_template_ihp.yaml` | variants used for single-setting experiments and for IHP sg13g2 |
| `experimental/signoff_library_limits.sdc` | signoff constraints: each pin is checked against its own liberty limit |
| `signoff_waivers.yaml` | recorded waivers, pinned to their violators |
| `experimental/mosaic_block_{a,b,c}.sv` | delivery wrappers; the file name equals the top module name |
| `experimental/mosaic_synth_top.sv` | a measurement-only wrapper for synthesis area studies |
| `experimental/config_blocka_signoff.yaml`, `config_blockb_signoff.yaml` | hand-written signoff configurations that predate the derived ones |
| `experimental/config_blocka_25mhz.yaml` | Block A with a 40 ns clock, kept for one recorded experiment |
| `experimental/config_blocka.yaml` | a development configuration meant to be run with checks skipped; never a result |
| `experimental/resources_lowmem.yaml` | thread limits for machines with little memory |
| `experimental/padframe/` | generators for the pad control ports of a block hardened against an external padframe DEF |
| `experimental/runs/`, `integration/runs/` | run directories; `integration/` holds the run hardened against an external padframe DEF |
| `scripts/gen_filelist.py` | resolves the source list from a bundle manifest |
| `scripts/preflight.py` | validates a `PHYSICAL_BUNDLE` for the chip-level targets |
| `scripts/run_lec.sh` | the equivalence-check flow |
| `scripts/power_from_activity.tcl` | power from a simulated activity file |
| `scripts/padring.py`, `scripts/lay2img.py` | pad ring build and layout rendering for the chip-level flow |
| `config.yaml`, `slots/`, `src/`, `chip_top.sdc`, `pdn_cfg.tcl` | the chip-level flow with a pad ring |
| `config_classic.yaml`, `core_classic.sdc` | the chip-level flow's core-only variant |
| `flake.nix`, `flake.lock`, `shell.nix` | the pinned LibreLane 3.0.0 environment |
| `Makefile` | `clone-pdk`, `mosaic-gen`, and the chip-level targets |

## Running a block-level signoff

```bash
# from the repository root
make mosaic-gen MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml
make -C flow/librelane clone-pdk

cd flow/librelane
MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml \
MOSAIC_HARDEN_FROM_SOC=configs/mosaic_tapeout_ultra.yaml \
MOSAIC_HARDEN_DESIGN=mosaic_block_a \
  ./experimental/run_signoff.sh <run-tag>
```

The runner derives the hardening configuration from the SoC configuration,
resolves the source list from the bundle, runs a preflight, launches LibreLane in
the Nix shell of this directory, and prints the signoff evidence. It takes
hours; start it detached from the terminal session. The environment variables
it reads are listed in [docs/physical-flow.md](../../docs/physical-flow.md).

To harden against an external padframe DEF, which fixes the die and the pin
positions:

```bash
MOSAIC_WORK_DIR=flow/librelane/integration \
MOSAIC_PIN_TEMPLATE=dir::<padframe>.def \
MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml \
MOSAIC_HARDEN_FROM_SOC=configs/mosaic_tapeout_ultra.yaml \
MOSAIC_HARDEN_DESIGN=mosaic_block_a \
  ./experimental/run_signoff.sh <run-tag>
```

The padframe DEF is supplied by whoever integrates the block. None is shipped.

A hand-written configuration can be used instead of a derived one:

```bash
MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml \
  ./experimental/run_signoff.sh <run-tag> experimental/config_blocka_signoff.yaml
```

After a run:

```bash
cd ../..
./mosaic physical-intent metrics --run-dir flow/librelane/experimental/runs/<run-tag>
./mosaic physical-intent ppa     --run-dir flow/librelane/experimental/runs/<run-tag>
GLS_RUN=flow/librelane/experimental/runs/<run-tag> tb/gls/run_gls.sh
```

## Technology notes for GF180MCU

Three properties of the `gf180mcu_fd_sc_mcu7t5v0` library shaped the wrappers in
`hw/asic/gf180/` and `experimental/`:

- **The library has no latch cell.** A generic latch-based clock gate survives
  synthesis as an unmapped cell. `hw/asic/gf180/tc_clk.sv` binds the clock gate
  to the library's integrated clock-gating cell.
- **The PDK's SRAM black-box views carry no `(* blackbox *)` attribute**, so
  Yosys reports their outputs as undriven.
  `hw/asic/gf180/gf180_sram_blackbox.sv` declares them properly.
- **Tristate drivers are not technology-mapped.** A behavioural tristate leaves
  unmapped cells. The Block B and Block C wrappers therefore instantiate the
  library's tristate buffer (`bufz_4`) explicitly. The Block A wrapper exposes
  the pad data and output-enable signals separately instead, because an
  external padframe supplies the pad cells.

Power delivery: the signoff template keeps a core ring (`PDN_CORE_RING: true`)
and leaves the ring layers at their defaults, which are the strap layers above
the signal routing range. A ring on the lower routing layers collides with the
detailed router. `PDN_CFG` is left unset, so LibreLane's own power-grid script
is used.

## The chip-level targets

`make harden`, `make classic`, `make padring` and their variants run a flow with
a pad ring (`src/chip_top.sv`) around the SoC. Each requires
`PHYSICAL_BUNDLE=/absolute/path` to a directory with a `physical_bundle.json`
that lists, with SHA-256 digests, the build manifest, the flattened RTL, the
bound core adapter and the SRAM macro views (GDS, LEF, liberty, Verilog).
`scripts/preflight.py` checks the bundle against the manifest and refuses a
manifest whose resolved target is not `tapeout`.

```bash
make preflight-chip    PHYSICAL_BUNDLE=/abs/path/to/bundle
make harden            PHYSICAL_BUNDLE=/abs/path/to/bundle
make preflight-classic PHYSICAL_BUNDLE=/abs/path/to/bundle
make classic           PHYSICAL_BUNDLE=/abs/path/to/bundle
```

No such bundle can be assembled from this repository today, and the
`*-nodrc` targets skip checks and are for development only. Use the block-level
flow above for any result.
