---
name: pdk-port
description: Enumerate what a PDK and cell library must supply before this project can size a die for it, and what it would silently inherit from GF180. Use before adding a second PDK, when a config names a non-GF180 PDK, or when asked what porting would cost.
---

# pdk-port — the porting surface, made visible

```bash
python3 -m harness --json pdk-port                      # GF180 7-track
python3 -m harness --json pdk-port sky130
python3 -m harness --json pdk-port ihp-sg13g2:sg13g2_stdcell
```

`ok: false` means the technology is missing requirements. `errors` lists only
the **silent** ones: gaps that produce a plausible wrong answer rather than a
refusal.

## The problem it exists for

`pdk` is carried everywhere as a label and branched on almost nowhere. Before
the guard this skill motivated, taking a shipped config, changing one key to
`pdk: sky130`, and asking for a floorplan returned the **identical GF180 die**,
labelled `basis="measured"`.

A wrong number is recoverable. A wrong number wearing the word "measured" is
not, because that label is the only reason anyone would trust it.

## The unit is not the PDK

`SITE_HEIGHT_UM = 3.92` is not a GF180 constant. It is the 7-track library's:

| Library | Site | Height |
|---|---|---|
| `gf180mcu_fd_sc_mcu7t5v0` | `GF018hv5v_mcu_sc7` | 3.92 µm |
| `gf180mcu_fd_sc_mcu9t5v0` | `GF018hv5v_green_sc9` | 5.04 µm |

Same PDK, different geometry, and die arithmetic depends on it. So pass
`pdk:cell_library`. A PDK-only argument is accepted and reported as partial,
because it cannot see a library swap.

## What it checks

Eight requirements. Three announce themselves when missing; five do not, and
those are the ones that need a guard rather than a document:

| Requirement | Silent? |
|---|---|
| area calibration | no — `derive_floorplan` now refuses |
| signoff collateral | no — the flow cannot run |
| tapeout matrix entry | no — `core_registry` refuses `target: tapeout` |
| **site geometry** | **yes** |
| **routability ceiling** | **yes** |
| **cell library identity** | **yes** |
| **corner names** | **yes** |
| **SRAM macros** | **yes** |

For GF180 all eight pass once the PDK is cloned (`make -C flow/librelane
clone-pdk`; without it `signoff-collateral` is reported missing). The notes say
why that is weaker than it sounds: the area calibration rests on three hardened
runs of one design family (SERV-only, 2 to 4 harts, execute in place), and
outside that family the estimator refuses by name rather than extrapolating.
For `ihp-sg13g2` three pass (site geometry, cell library, corner names) and
five are missing; for `sky130` none pass.

## What it does not do

It does not port anything. Porting means hardening a design on the new
technology and recording its `AreaMeasurement`. No tool can shortcut a
measurement, and a model that extrapolated one would reintroduce exactly the
defect this skill exists to surface.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

No MCP tool exposes this skill yet. In a gated session (plugin or `mosaic agent --driver claude|omp`) do not run it through a shell: ask the user to run the CLI command shown above and paste the JSON back.
