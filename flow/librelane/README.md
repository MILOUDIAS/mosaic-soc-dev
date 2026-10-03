# MOSAIC-SoC — LibreLane GF180MCU hardening flow

RTL→GDSII flow for the MOSAIC SoC on **GF180MCU**, using **LibreLane 3.0.0**
(`meta.flow: Chip`) and the **wafer-space gf180mcu 1.8.0** PDK. Adapted from
[chipathon-2026-gf180mcu-padring](https://github.com/Mauricio-xx/chipathon-2026-gf180mcu-padring)
(Apache-2.0, © Leo Moser / wafer-space — see `NOTICE`).

## Layout

| Path | Purpose |
|------|---------|
| `config.yaml` | **Chip flow** config (`DESIGN_NAME: mosaic_chip_top`, GF180 DRC waivers, PDN, clock) — full chip with pad ring + sealring. |
| `slots/slot_mosaic.yaml` | Floorplan + pad map (`DIE_AREA`, `PAD_{N,S,E,W}`), merged on top of `config.yaml`. |
| `config_classic.yaml` | **Classic flow** config (`DESIGN_NAME: core_v_mini_mcu`) — hardens the SoC core only, **no pad ring**. Self-contained (no slot). |
| `core_classic.sdc` | Timing constraints for the Classic flow (clock on `clk_i`, no pad cell). |
| `src/chip_top.sv` | GF180 physical pad frame (in_s/in_c/bi_24t/dvdd/dvss cells). **Complete.** |
| `src/mosaic_soc_core.sv` | Non-signoff adapter placeholder. Physical runs reject it and require a bound version in `PHYSICAL_BUNDLE`. |
| `src/slot_defines.svh` | `SLOT_MOSAIC` pad counts (from `configs/pad_cfg.py`). |
| `pdn_cfg.tcl` | OpenROAD PDN generator (stdcell grid + core ring). |
| `chip_top.sdc` | Timing constraints (50 MHz / 20 ns). |
| `scripts/` | `padring.py` (fast pad-only build), `lay2img.py`, `run_native.sh`, `run_docker_iic.sh`. |
| `scripts/preflight.py` | Fail-closed capability, hash, RTL-binding, and SRAM-view gate. |
| `Makefile` | `clone-pdk`, standalone `mosaic-gen`, bundle preflights, hardening, GUIs, `render-image`. |
| `flake.nix`/`shell.nix`/`flake.lock` | Pinned toolchain (LibreLane 3.0.0 + FOSSi cache). |

## Current physical-flow status

RTL generation is operational, but this repository does **not** currently ship a bound
`mosaic_soc_core`, a flattened physical SoC source, or qualified 32-KiB SRAM views. No
DRC/LVS-clean GDS is claimed. The checked-in `src/mosaic_soc_core.sv` is deliberately a
placeholder and is never accepted by the hardening targets.

The public schema separates these states with `soc.target`. `rtl` (the default) and
`simulation` permit all generator-supported PDK/bus/memory combinations. `tapeout` is
currently qualified only for the canonical PoC declaration: `gf180mcu`, `obi`, 32-KiB
SRAM, 2-KiB boot ROM, 1x cv32e20 TITAN, 2x FazyRV-8 ATLAS at `0x1000`, 4x SERV NANO at
`0x2000`, dynamic TDU scheduling, and UART/GPIO/timer/SPI. LOG, FlooNoC, Sky130,
alternate memory/core/scheduler/peripheral combinations, CVA6, Rocket, and BOOM remain
valid RTL/simulation work but cannot enter this flow.

## Experimental Block A run (2026-07-31)

Separate from the qualified path above, `experimental/` holds a **Chipathon MPW Block A**
hardening that runs deliberately OUTSIDE the `PHYSICAL_BUNDLE` preflight gate. It exists
to explore area/timing quickly; nothing it produces is attested or signed off.

The MPW plan is [`docs/padrinrg/padring_proposal.jpg`](../../docs/padrinrg/padring_proposal.jpg):
88 pins over a 2235 × 2235 µm shared die, 5 block sizes. MOSAIC targets **Block A** — a
quarter of the area (1117.5 µm square = 1.2488 mm²) with a 22-pin budget. There is no pad
ring in this flow: for an MPW block the macro *is* the deliverable.

```
experimental/
  mosaic_block_a.sv        22-pin delivery wrapper (the submission candidate)
  mosaic_synth_top.sv      measurement-only wrapper, ties the 6 XIF interfaces
  config_blocka.yaml       Block A: absolute 1117.5 um die, decks skipped (fast loop)
  config_blocka_signoff.yaml  same die, NOTHING skipped -- DRC/LVS/XOR/IR all run
  run_signoff.sh           launches the signoff config; refuses to run if the
                           config contains a step substitution
  config_{l123,pathb,nocsr}.yaml   area-reduction steps, see docs/area_study §8f-8g
```

```bash
cd flow/librelane
nix develop --command librelane experimental/config_blocka.yaml \
  --pdk gf180mcuD --pdk-root "$PWD/gf180mcu" --manual-pdk --run-tag blocka \
  --skip Magic.DRC --skip KLayout.DRC --skip Checker.MagicDRC --skip Checker.KLayoutDRC \
  --skip Magic.SpiceExtraction --skip Netgen.LVS --skip Checker.LVS \
  --skip KLayout.XOR --skip Checker.XOR \
  --skip OpenROAD.IRDropReport --skip Checker.PowerGridViolations
```

Result: die exactly 1117.5 × 1117.5 µm, **0 routing DRC**, **0 antenna**, setup +20.67 ns
and hold +0.075 ns worst-corner, 81.2% utilization, 44 684 cells.

**What the skips mean.** DRC, LVS, XOR and IR drop are *not run* — their status is
UNKNOWN, not clean. `Checker.TrDRC` and `Checker.YosysUnmappedCells` are deliberately
left ENABLED: an unmapped cell has no physical master and a routing violation is a real
short, so neither may be waived. The IR-drop skip was forced by `PDN_CORE_RING: false`,
which leaves PSM without a power source.

### The signoff run

```bash
make mosaic-gen MOSAIC_CFG=configs/mosaic_tapeout_ultra.yaml   # generate the RTL
cd flow/librelane
./experimental/run_signoff.sh            # no --skip anywhere; hours, not minutes
```

The config carries **no absolute paths**. `scripts/gen_filelist.py` resolves
`VERILOG_FILES`/`VERILOG_INCLUDE_DIRS` from the FuseSoC manifest at run time and
`run_signoff.sh` merges them into a run-local copy, so a fresh clone can harden
without editing anything. If the RTL has not been generated, the runner stops
and says so rather than using whatever bundle is lying around — a stale path
that still resolves would harden the wrong design and report it clean.

The skip list above is what this config exists to delete, so `run_signoff.sh` greps the
config for `substituting_steps` / `: null` and aborts rather than let a signoff run
quietly become a partial one. Metrics are reported through `harness/evidence/librelane.py`,
which will not call a missing report clean.

Two things had to be fixed before it could reach the decks:

- **Power delivery.** `PDN_CORE_RING: false` gave OpenROAD PSM no source
  (`PSM-0069`, 101 354 grid violations); a ring on Metal2/Metal3 collides with the
  router (bug 27) and one placed on Metal4/Metal5 *explicitly* hits `PDN-0186`, because
  the PDN script emits three extra `add_pdn_connect` calls guarded by
  `info exists PDN_CORE_*_LAYER` that then duplicate the strap grid's own connect. The
  answer is to keep the ring and **not set those variables at all** — they default to the
  M4/M5 strap layers, above the router, and the guarded calls stay dormant. Result:
  `PSM-0040` on both nets, **0 power-grid violations**.
- **`PDN_CFG` is left unset.** Pointing it at `pdn_cfg.tcl` fails with `[PDN-1028]`: that
  file is LibreLane's own script plus a MOSAIC `sram_grid` block whose
  `define_pdn_grid -macro` named neither `-cells`, `-instances` nor `-default` (bug 30).
  Fixed, but Block A binds no macro, so the built-in script is the simpler choice here.

Three GF180 findings from this work, all in the wrappers under `../../hw/asic/gf180/`:

- **No latch cell exists in `gf180mcu_fd_sc_mcu7t5v0`.** The generic pulp `tc_clk_gating`
  latch survives synthesis as an unmapped `$_DLATCH_N_`. `tc_clk.sv` rebinds it to the
  library's integrated clock gate `icgtp_1`.
- **The PDK's `__blackbox.v` SRAM views carry no `(* blackbox *)` attribute**, so yosys
  reports every `Q` bit as undriven. `gf180_sram_blackbox.sv` declares them properly.
- **Tristates are not mapped by `dfflibmap`/`abc`.** An `assign pad = oe ? d : 1'bz`
  leaves unmapped `$_TBUF_` cells; `mosaic_block_a.sv` instantiates `bufz_4` explicitly.

Also: a PDN core ring on Metal2/Metal3 sits inside the router's own layer range
(`RT_MIN_LAYER: Metal2`, `RT_MAX_LAYER: Metal5`). Every detailed-routing violation in an
early attempt was a signal-to-VDD short on Metal3 along the ring, and routing failed to
converge for hours. The experimental configs set `PDN_CORE_RING: false`.

## Physical bundle contract

Every physical command requires `PHYSICAL_BUNDLE=/absolute/path`. The directory must
contain `physical_bundle.json`; every input is relative to that directory and protected
by its lowercase SHA-256 digest:

```json
{
  "schema_version": 1,
  "build_key": "<manifest build_key>",
  "artifacts": {
    "manifest":       {"path": "manifest.json", "sha256": "<64 hex>"},
    "flattened_rtl":  {"path": "design.v", "sha256": "<64 hex>"},
    "bound_core_rtl": {"path": "mosaic_soc_core.sv", "sha256": "<64 hex>"},
    "sram_gds":       {"path": "sram/mosaic_sram.gds", "sha256": "<64 hex>"},
    "sram_lef":       {"path": "sram/mosaic_sram.lef", "sha256": "<64 hex>"},
    "sram_lib":       {"path": "sram/mosaic_sram.lib", "sha256": "<64 hex>"},
    "sram_verilog":   {"path": "sram/mosaic_sram.v", "sha256": "<64 hex>"}
  }
}
```

`manifest.json` must be a current-schema MOSAIC manifest whose `resolved.target` is
`tapeout` and whose resolved PDK/bus/memory/core combination passes the authoritative
capability matrix. It must also contain a `physical_attestation` object with the same
`build_key` and one `<artifact>_sha256` value for every physical artifact. This binds the
flattened closure, adapter, and SRAM views back to the exact generated build rather than
merely hashing an arbitrary collection of files. The flattened RTL must be a nontrivial
SoC closure defining `core_v_mini_mcu` or `x_heep_system`; the bound adapter must actually
instantiate `x_heep_system`; the GDS must be a structurally complete GDSII library; and
the SRAM LEF/LIB/RTL views must define the same `mosaic_sram` macro used by the flow.
Classic core hardening does not need `bound_core_rtl`, but all other artifacts remain
mandatory.

## How to run

Requires Nix (flakes) + ~20 GB disk; first `nix-shell` pulls the toolchain from
the FOSSi binary cache. A real run is multi-hour.

```bash
cd flow/librelane
nix-shell ../../flow/librelane/shell.nix      # LibreLane 3.0.0 + EDA tools
make clone-pdk                                # wafer-space gf180mcu @ 1.8.0

# RTL generation is still independent of physical collateral:
make mosaic-gen MOSAIC_CFG=mosaic.yaml

# Fail before launching LibreLane if any input is missing, stale, or unsupported:
make preflight-chip PHYSICAL_BUNDLE=/abs/path/to/bundle

# Chip flow — full chip with pad ring + sealring:
make harden SLOT=mosaic PHYSICAL_BUNDLE=/abs/path/to/bundle
make harden-nodrc PHYSICAL_BUNDLE=/abs/path/to/bundle  # development only

# Classic flow — SoC core only, no pad ring (early synth/PnR/area exploration):
make preflight-classic PHYSICAL_BUNDLE=/abs/path/to/bundle
make classic PHYSICAL_BUNDLE=/abs/path/to/bundle
```

`make harden` does not generate or discover sources implicitly. It validates the bundle,
exports its exact artifact paths, and only then launches LibreLane.

## Remaining authoring steps (before a real tapeout)

1. **Bind the SoC pins** in a bundle-owned `mosaic_soc_core.sv` — instantiate
   `x_heep_system` and map each pad bus bit to its `pad_cfg.py` pin, keeping the
   bit indices aligned with `slots/slot_mosaic.yaml`. (The pad frame itself is
   done; this adapter is the only RTL gap.)
2. **Finalize the pad map** — confirm `NUM_*_PADS` in `slot_defines.svh` and the
   `PAD_{N,S,E,W}` order in `slot_mosaic.yaml` against the bonding diagram.
3. **SRAM macros** — map the 32 KB SRAM to a GF180 `mosaic_sram` macro and package
   its GDS/LEF/LIB/Verilog views in the bundle.
4. **Flatten** — create the bundle's `design.v` from the resolved FuseSoC filelist;
   `make flatten` is now only a validator and will not bless an ad-hoc stale file.
5. **Multi-clock SDC** — add derived/gated-clock constraints if the multi-core
   PoC needs them (`chip_top.sdc` currently constrains the single pad clock).
6. **DRC posture** — review inherited waiver globs and obtain clean DRC/LVS/STA
   evidence. `ERROR_ON_MAGIC_DRC` is enabled; `*-nodrc` targets are development-only.

> Inspection-only Docker (`scripts/run_docker_iic.sh`, hpretl/iic-osic-tools) is
> for viewing a finished GDS, **not** for signoff — its LibreLane version is not
> guaranteed to match the 3.0.0 pin.
