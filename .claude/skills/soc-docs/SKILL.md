---
name: soc-docs
description: >
  Generate MOSAIC-SoC documentation deterministically: config summaries,
  the SoC memory map + TDU register reference, dashboard metrics. Use when
  the user asks "document this SoC" or after a successful pipeline run.
---

# soc-docs — doc-gen skill card

## Commands

```bash
python3 -m harness doc-gen config configs/<name>.yaml   # markdown summary
python3 -m harness doc-gen memory-map                   # memory map + TDU regs
python3 -m harness doc-gen dashboard                    # DASHBOARD.md metrics
```

`details.markdown` carries the rendered document — paste it into reports or
save it under docs/. Combine with `topo-viz render` for the diagram.

## Notes

The memory-map tables are the harness's curated reference (TDU at
0x200A0000: CORE_STATUS/SCHED_MODE/WAKE_MASK/WAKE_REQ/TASK_PUSH/TASK_POP/
TASK_STATUS/ENERGY/CPI_EST). For per-config generated addresses, prefer
`doc-gen config <yaml>` which reads the actual file.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| summarise a config | `doc_config {path}` |
| dashboard / status | `doc_dashboard {path?}` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
