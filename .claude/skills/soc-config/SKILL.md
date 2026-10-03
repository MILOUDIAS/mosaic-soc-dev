---
name: soc-config
description: >
  Author or validate MOSAIC-SoC mosaic.yaml configs deterministically
  (cores/roles/memory/bus/TDU/peripherals, presets, per-core wake-demo
  shape). Use whenever a config file must be created or checked — never
  hand-write mosaic.yaml.
---

# soc-config — config-author skill card

## Contract

`mosaic.yaml` drives the ENTIRE flow (core selection → GDSII). The harness
fills per-core defaults, strips harness-only metadata, and validates against
the LIVE registries (single-sourced from `util/xheep_gen` — core lists in the
harness can never drift). Registered cores: run `presets`/errors list them.
Sim-only cores (cva6, rocket, boom) are rejected for tapeout presets.

## Commands

```bash
python3 -m harness --json config-author presets            # list presets
python3 -m harness --json config-author generate --preset poc --name my_soc
python3 -m harness --json config-author generate --name my_soc \
    --core cv32e20:1:titan --core serv:4:nano \
    --sram 32 --tdu --mode dynamic --peripheral uart,gpio \
    --target-clock-mhz 25          # design intent; see below
python3 -m harness --json config-author validate configs/my_soc.yaml
python3 -m harness --json config-author wake-demo <core>   # canonical 3-hart bring-up
```

`wake-demo <core>` emits the proven bring-up shape (cv32e20 titan + 2×core
workers @0x1000/0x2000, TDU dynamic) — use it for any newly wrapped core.

`--target-clock-mhz N` sets `soc.objectives.target_clock_mhz`. **Set it on any
config that will be hardened**: `physical-intent harden` derives `CLOCK_PERIOD`
from it and REFUSES without one, deliberately — "a clock nobody chose is the kind
of number that ends up in a datasheet". It is a request, not a result; STA decides
whether it was met. Put it here rather than hand-editing a LibreLane config, or
the frequency ends up living in two places.

## Failure playbook

- "ip 'X' not in [...]" → the core isn't integrated; use `wrapper-smith` first.
- "SIMULATION-ONLY" → cva6/rocket/boom cannot enter tapeout configs.
- "no clock period: set soc.objectives.target_clock_mhz" from `physical-intent
  harden` → the config was authored without `--target-clock-mhz`. Add it to the
  config; do not pass an override to the harden step, which only moves the number
  somewhere the SoC config cannot see it.
- boot_addr errors → int or hex string (`0x1000`), inside RAM, outside 0x3000
  sentinel window and linker sections.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `oh-my-soc approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| draft from prose | `soc_plan {request}` |
| author a config | `config_generate {name, cores, sram_kb, boot_rom_kb, bus, tdu, mode, peripherals, target, output}` |
| validate a file | `config_validate {path}` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
