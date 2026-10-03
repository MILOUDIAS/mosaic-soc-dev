---
name: soc-drc-triage
description: >
  Parse and classify DRC/LVS reports (Magic, KLayout, Netgen) from the
  LibreLane GF180 flow: violation types, severity, targeted fix suggestions.
  Use after any hardening run instead of reading raw report files.
---

# soc-drc-triage — drc-triage skill card

## Commands

```bash
python3 -m harness --json drc-triage analyze build/reports/magic_drc.rpt
python3 -m harness --json drc-triage analyze report.rpt --format klayout
python3 -m harness --json drc-triage scan flow/librelane/runs/<run>/reports/
```

Format auto-detected (magic `Violation: <rule> (count: n)`, klayout
`<rule>: n violations`, netgen `Incorrect: ...`); override with --format.

## Output contract

`details.violations`: list of {rule, type, count, severity, suggestion}.
Types: short/open/spacing/width/enclosure/area/antenna/lvs_mismatch/
pin_access/other. Severity: clean/low/medium/high/critical.

## Contract

The skill NEVER modifies RTL — suggestions are advisory; signoff stays with
the deterministic DRC/LVS tools. Never bypass checks (no --no-verify, no
harden-nodrc for deliverables).

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| one report | `drc_analyze {path, format?}` |
| a run directory | `drc_scan {path}` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
