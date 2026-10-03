---
name: soc-topology
description: >
  Semantic checks + interactive topology diagram for a MOSAIC config: LOG-bus
  bank constraints, address-window overlaps, role sanity, bus-fabric shape
  (OBI xbar / logarithmic interconnect / FlooNoC). Use as the GATE after
  authoring any config and to visualize the SoC for the user.
---

# soc-topology — topo-viz skill card

## Commands

```bash
python3 -m harness --json topo-viz check configs/<name>.yaml    # GATE
python3 -m harness topo-viz render configs/<name>.yaml -o build/<name>_topo.html
python3 -m harness topo-viz render configs/<name>.yaml --svg -o build/<name>.svg
```

## What check catches (beyond schema validation)

- `bus: log` constraints: banks power-of-two, banks >= bus masters,
  sram_kb divisible by banks (mirrors the generator's own validation).
- Inert `bus_opts` (options that the chosen fabric ignores).
- Derived RAM address windows overlapping base-config windows.
- Master-count arithmetic: n_masters = 2*harts + debug + 3*dma_ports.

## Failure playbook

- num_banks violations → adjust `bus_opts.log.num_banks` (power of two ≥
  masters) or sram_kb; re-author via config-author, then re-check.
- Window overlap → change sram_kb or move boot addresses; never edit
  generated packages by hand.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `oh-my-soc approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| semantic checks | `topology_check {path}` |
| diagram | `topology_render {path, output, svg?}` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
