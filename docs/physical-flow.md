# Physical flow

The physical flow turns the generated RTL into a layout (GDSII) and checks it.
It uses [LibreLane](https://github.com/librelane/librelane) 3.0.0, an open
source flow that drives Yosys for synthesis, OpenROAD for placement, clock-tree
synthesis and routing, and Magic, KLayout and Netgen for the final checks.

**Which technologies have a hardened design.** Only GF180MCU (the wafer-space
`gf180mcuD` PDK, tag 1.8.0, with the 7-track 5 V standard-cell library). Designs
that name `pdk: sky130` or `pdk: ihp-sg13g2` are RTL and simulation only: the
validator refuses `target: tapeout` for them, and no layout has been signed off
on either. See [status.md](status.md).

Layouts are not in version control. The repository tracks only the
`final/metrics.json` of the runs that the area model and the waivers cite, and
one disconnected-pin table. Everything else under a run directory is produced
by running the flow.

## What a run does

A run hardens a *delivery wrapper*: a small top module that instantiates the
generated SoC and exposes only the pins the block needs. Three ship, in
`flow/librelane/experimental/`:

| Wrapper | SoC configuration | Harts |
|---|---|---|
| `mosaic_block_a.sv` | `configs/mosaic_tapeout_ultra.yaml` | 2 (Block A, the GF180MCU reference design) |
| `mosaic_block_b.sv` | `configs/mosaic_blockb_3hart.yaml` | 3 |
| `mosaic_block_c.sv` | `configs/mosaic_blockc_4hart.yaml` | 4 |

All three are SERV-only designs that execute in place from external flash and
have no on-chip SRAM pool.

The flow is LibreLane's `Classic` flow with the settings in
`flow/librelane/signoff_template.yaml`. After place and route it runs Magic
design-rule checking (DRC), KLayout DRC, layout-versus-schematic comparison
(LVS) with Netgen, a KLayout XOR between the two layout streams, antenna
checks, an IR-drop report, and static timing analysis at the PDK's nine
corners. Any of them can fail the run.

Two constraint files are used on purpose. Place and route targets a 4.0 ns
maximum transition everywhere. Signoff checks each pin against the limit its
own liberty file declares for that corner
(`experimental/signoff_library_limits.sdc`).

## Launching a signoff run

Requirements: Nix with flakes, and free disk. The preflight refuses to start a
hardening run with less than 12 GB free and an RTL generation with less than
30 GB free. A run takes hours.

```bash
# 1. Generate the RTL bundle for the design.
make mosaic-gen MOSAIC_CFG=configs/mosaic_blockb_3hart.yaml

# 2. Fetch the PDK once.
make -C flow/librelane clone-pdk

# 3. Harden. The hardening configuration is derived from the SoC configuration.
cd flow/librelane
MOSAIC_CFG=configs/mosaic_blockb_3hart.yaml \
MOSAIC_HARDEN_FROM_SOC=configs/mosaic_blockb_3hart.yaml \
MOSAIC_HARDEN_DESIGN=mosaic_block_b \
  ./experimental/run_signoff.sh my_run_tag
```

Start a long run detached from the terminal session (for example with `setsid`
or `nohup`), otherwise closing the session kills it.

`run_signoff.sh` does the following, in order:

1. Derives the hardening configuration with `mosaic physical-intent harden`:
   the die and core area from the area model and a target utilisation, the clock
   period from `soc.objectives.target_clock_mhz`, and the repair margin. It
   refuses to derive one for a design with no stated clock.
2. Refuses a configuration that substitutes or nulls a flow step. The script
   has no skip option.
3. Resolves the source file list from the bundle's manifest, so the
   configuration carries no absolute paths. It stops if no bundle exists for the
   current sources.
4. Runs `mosaic flow-preflight harden`: free disk, a complete configuration, a
   present bundle, an unused run tag, and routing rules that do not name
   synthesis-generated nets.
5. Launches LibreLane inside the pinned Nix shell of `flow/librelane/`.
6. Prints the signoff evidence through the metrics parser, which reports a
   missing metric as unknown rather than as clean.

Environment variables the script reads:

| Variable | Purpose |
|---|---|
| `MOSAIC_CFG` | the SoC configuration whose bundle is hardened |
| `MOSAIC_HARDEN_FROM_SOC` | derive the hardening configuration from this SoC configuration |
| `MOSAIC_HARDEN_DESIGN` | the top module name, which is also the wrapper file name |
| `MOSAIC_HARDEN_UTIL`, `MOSAIC_HARDEN_CLOCK_NS`, `MOSAIC_HARDEN_MARGIN` | override the utilisation, clock period or repair margin for one run |
| `MOSAIC_PIN_TEMPLATE` | an external padframe DEF: a file that fixes the die and the pin positions the block must match |
| `MOSAIC_WORK_DIR` | where the run is written; default `flow/librelane/experimental`, and `flow/librelane/integration` for a run hardened against an external padframe DEF |
| `MOSAIC_MANIFEST` | pin the RTL bundle explicitly, so that a series of runs hardens the same RTL |
| `MOSAIC_RESOURCE_CONFIG` | a file of `*_THREADS` keys only, for machines with little memory (`experimental/resources_lowmem.yaml`) |
| `MOSAIC_WATCH_ROUTING=1` | enable the routing guard described below |

The `Makefile` in `flow/librelane/` also has `harden` and `classic` targets for
a chip-level flow with a pad ring. They require a `PHYSICAL_BUNDLE` that the
repository does not provide; see `flow/librelane/README.md`.

## The gates

### Hard checks

These metrics must be zero: Magic DRC errors, KLayout DRC errors, illegal
overlaps, LVS errors and unmatched nets, pins and devices, XOR differences,
routing DRC errors, antenna violations, disconnected pins and power-grid
violations. A metric that is absent from the run fails its check.

### Waivers pinned by violator identity

A waiver accepts a known violation at its measured size, for one design, until
a review date. Waivers live in `flow/librelane/signoff_waivers.yaml`. Each one
states:

- the metric, the design and a ceiling (`accepted_max`);
- the run that is its evidence;
- `expected_violators`: the names of the nets or pins it accepts. The waiver
  applies only when the run's violators are exactly this set, so a different
  violation at the same count still fails;
- `preconditions`: other metrics that must hold on the raw run before the waiver
  is considered.

Two waivers are recorded, both for `mosaic_block_a` on run `blocka_d15_rstsync`:
two clock-tree root buffers that exceed the project's fan-out limit of 10 (the
GF180MCU libraries declare no fan-out limit, and maximum transition and maximum
capacitance are both at zero violations), and eleven disconnected input
terminals of output-only pads that an external padframe defines.

`./mosaic waiver-author` audits every waiver against the run it cites.

### The PPA gate

PPA stands for power, performance and area. `mosaic physical-intent ppa` decides
whether a run may be compared with another at all. A run is accepted only if
every hard check passes (after waivers), setup and hold slack are not negative,
and there are no maximum-transition or maximum-capacitance violations. Only then
are the objectives compared: die area, logic cell area, energy per cycle and
maximum frequency. Two runs of different designs are never compared.

```bash
./mosaic physical-intent ppa     --run-dir <run> [--baseline <other run>]
./mosaic physical-intent metrics --run-dir <run> [--compare <other run>]
./mosaic physical-intent ledger          # every run, and which pairs differ in one setting
```

The power figure comes from the timing tool's default switching activity, not
from a simulated workload. It is comparable between runs of one design and is
not a prediction of what the chip draws.

### The routing guard

Detailed routing sometimes stops converging and runs for many hours.
`mosaic physical-intent watch --run-dir <run>` reads the routing trajectory and
says whether it will converge; with `--fail-on-plateau` it exits with status 3
when the trajectory has plateaued. With `MOSAIC_WATCH_ROUTING=1` the runner
polls it and stops the run on a plateau. It is off by default.

## Derived floorplan and the area model

```bash
./mosaic physical-intent floorplan --config configs/mosaic_blockc_4hart.yaml
./mosaic physical-intent harden    --config configs/mosaic_blockc_4hart.yaml \
    --design mosaic_block_c --output out.yaml
```

The die size is computed from an area model calibrated on hardened runs of the
SERV-only design family with 2 to 4 harts. Outside that family the model refuses
rather than extrapolating. The default utilisation is the densest target that a
design of that hart count has been shown to route at, recorded in
`harness/physical/routability.py`: 0.813 for 2 harts, 0.792 for 3 and 0.74 for
4. A 4-hart run at 0.75 failed to route.

## Automatic improvement

`mosaic physical-intent optimize` is a line search over one setting at a time
(`clock_period_ns`, `repair_margin_pct` or `target_utilisation`). It launches
signoff runs through `run_signoff.sh`, stops if any other setting moved between
runs, and resumes from a journal. `mosaic physical-intent screen` gives an early
verdict on a run in progress.

```bash
./mosaic physical-intent optimize --config configs/mosaic_blockb_3hart.yaml \
    --design mosaic_block_b --knob clock_period_ns=80,66.7,50 \
    --max-runs 4 --dry-run
```

## Measured results

Read from the tracked `final/metrics.json` files on 2026-10-03. "Accepted" is
the verdict of the PPA gate. Slack is the worst corner. Runs live under
`flow/librelane/experimental/runs/` unless marked.

| Run | Design | Die (mm2) | Utilisation | Setup slack (ns) | Hold slack (ns) | Max-transition violations | Hard checks | PPA gate |
|---|---|---|---|---|---|---|---|---|
| `blocka_d15_rstsync` (under `integration/runs/`) | Block A | 1.2321 | 82.9% | +2.199 | +0.054 | 0 | all zero except 11 waived disconnected pins | accepted with the waivers |
| `blocka_1110_ndr` | Block A | 1.2321 | 85.4% | +6.066 | +0.075 | 0 | all zero | accepted |
| `blocka_slewonly` | Block A | 1.2488 | 86.6% | +20.944 | +0.067 | 4 | all zero | rejected |
| `blocka_signoff` | Block A | 1.2488 | 84.4% | +20.861 | +0.066 | 591 | all zero | rejected |
| `blockb_sdc` | Block B | 1.5916 | 77.8% | +20.836 | +0.077 | 0 | all zero | accepted |
| `blockb_m45` | Block B | 1.5916 | 78.0% | +21.095 | +0.046 | 0 | all zero | accepted |
| `blockb_reharden` | Block B | 1.5916 | 79.3% | +20.867 | +0.077 | 5 | all zero | rejected |
| `blockb_generated` | Block B | 1.4702 | 82.2% | +20.891 | +0.075 | 1459 | all zero | rejected |
| `blockb_signoff` | Block B | 1.5917 | 77.0% | +20.476 | +0.037 | 845 | all zero | rejected |
| `blockc_sdc` | Block C | 2.1836 | 69.2% | +20.645 | +0.045 | 0 | all zero | accepted |
| `blockc_m45` | Block C | 2.1836 | 70.8% | +20.303 | +0.061 | 0 | all zero | accepted |
| `blockc_slew45` | Block C | 2.1836 | 70.6% | +20.657 | +0.044 | 12 | all zero | rejected |
| `blockc_u65` | Block C | 2.0584 | 72.4% | +20.494 | +0.071 | 1018 | all zero | rejected |

Notes on the table:

- `blocka_d15_rstsync` is the Block A reference run. It was hardened on a
  1110 um square die against an external padframe DEF, with a reset
  synchronizer, and its waivers were recorded on 2026-09-24. Its clock period is
  50 ns (20 MHz). The waiver record also states that gate-level simulation of
  this netlist reached `EXIT SUCCESS` after 12,404 cycles; that log is not in
  version control. The run predates the renaming of the SoC module to
  `mosaic_soc`: its netlist names the SoC instance `i_core_v_mini_mcu`, and so
  do the two violator names in its fan-out waiver. The wrapper now names the
  instance `i_mosaic_soc`, so the waiver has to be re-based on the next Block A
  run.
- The rejected runs fail only the maximum-transition gate. Most of them predate
  the split between the place-and-route constraint and the per-pin signoff
  limits described above, so their counts are measured against a single 4.0 ns
  limit. They are tracked because the area model and the routability records
  cite them, not as results to quote.
- The Block A reference run also tracks its resolved LibreLane configuration
  (`resolved.json`, with the keys that hold file paths removed). The gate reads
  the design name from it, so `./mosaic physical-intent ppa` and
  `./mosaic waiver-author` give the same verdict on a clone as on the full run
  directory. The other tracked runs hold only their metrics.

Results recorded in the source tree whose run directories are not tracked:

- Block C routes cleanly at a 0.74 utilisation target on a 1.9959 mm2 die (run
  `opt_blockc_density_03`, search of 2026-09-18, in
  `harness/physical/routability.py`).
- Block B closes timing at a 50 ns clock with +5.87 ns of setup slack (run
  `opt_blockb_clock_03`, adopted 2026-09-12, in the comment of
  `configs/mosaic_blockb_3hart.yaml`). Gate-level simulation has not been run at
  that clock.

No waiver is recorded for Blocks B and C, and each of their tracked runs
reports fan-out violations that nothing accepts. They are hardened designs, not
tapeout-qualified ones.
