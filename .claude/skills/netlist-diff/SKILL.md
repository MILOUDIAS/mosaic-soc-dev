---
name: netlist-diff
description: Compare two hardened runs' netlists and say what the tools did differently. Use when a config change moved the metrics and you need to know why, when confirming two runs produced the same silicon, or before attributing a gate-level difference to a netlist one.
---

# netlist-diff — what the tools actually did

```bash
python3 -m harness --json netlist-diff <run-a> <run-b>
python3 -m harness --json netlist-diff <run-a> <run-b> --stage nl
```

## What it answers

Metrics say a count changed. They do not say what changed to cause it.

Turning `MAX_FANOUT_CONSTRAINT` from 10 to 16 took max-fanout violations
5 → 12 and buffer area 31,137 → 23,594 µm². The netlists say why in one line:

```
-628 buf_1   +463 buf_4
```

CTS used bigger buffers and fewer of them, which is the shallower clock tree
the metrics only hinted at.

## The stage matters, and the default was wrong

| Stage | Path | What it is |
|---|---|---|
| **`pnl`** (default) | `final/pnl/*.pnl.v` | post-place-and-route — becomes the GDS, and what GLS simulates |
| `nl` | `final/nl/*.nl.v` | post-synthesis — before placement, repair buffers, fill, antenna cells |

`summarise_run` globs `nl/`, so a diff run to explain a gate-level result was
comparing the stage that had not been through placement. The stage is named in
every result because the two answer different questions.

## Saying "identical" convincingly

Two independent runs of one design produce byte-identical netlists. That is a
result, not an empty table, so md5 short-circuits it:

```
identical at pnl (73,158 instances)
byte-identical netlists. The runs differ in nothing the tools wrote, so any
behavioural difference between them is not in the design.
```

## The bridge to gls-triage

`explains_a_gls_difference` is false when the delta is confined to buffers,
clock cells, fill, ties and antenna diodes. A zero-delay simulation cannot
distinguish those, so such a difference cannot explain a differing gate-level
verdict. Both skills share the vocabulary deliberately; a test asserts they
agree, or they would contradict each other on the same pair of runs.

## Degradation

najaeda gives connectivity and is optional. Without it, cell instantiations are
counted straight out of the Verilog: 2.4 s for two 12 MB netlists, instance
totals matching `metrics.json` exactly, per-cell-type deltas. The result says
what it lost — no connectivity, so it cannot tell a rewire from a resize.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `oh-my-soc approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

No MCP tool exposes this skill yet. In a gated session (plugin or `oh-my-soc agent --driver claude|omp`) do not run it through a shell: ask the user to run the CLI below and paste the JSON back.
