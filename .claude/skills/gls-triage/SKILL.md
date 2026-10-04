---
name: gls-triage
description: Read a gate-level simulation run and report what its verdict can and cannot support. Use after any GLS failure, before attributing one to a netlist difference, and whenever a GLS pass is about to be quoted as evidence.
---

# gls-triage — what this verdict is capable of meaning

```bash
python3 -m harness --json gls-triage <run-dir>
python3 -m harness --json gls-triage <run-dir> --control <booting-run-dir>
```

It reads a run; it does not simulate one. Running GLS is the `gls` flow, which
costs minutes and gates on its own marker.

## The incident it is shaped by

A GLS failure here was tracked across four netlists and localised to a missing
`dlya_2` cell. It was a cell **swap**: instance `_28773_` was `dlya_2` in every
booting netlist and `clkbuf_1` in the failing one, same nets, both
non-inverting buffers. In zero delay that substitution is a no-op and cannot
cause anything. The real cause was a race between zero-delay evaluation and
UDP-based flops.

A tool that had confidently named a cell would have been confidently wrong. So
this one reports the oracle's competence **before** any finding.

## What the oracle is, and is blind to

| | |
|---|---|
| simulator | iverilog, zero delay |
| cell models | `-DFUNCTIONAL`, specify blocks stripped |
| flops | UDP sequential primitives |
| SDF | refused by design — `ifnone` edge paths, which iverilog rejects |
| timing authority | STA at nine corners, **not** this simulation |
| **blind to** | **buffer, clock, fill, physical, antenna cells** |

Two netlists differing only in those are the same netlist to this oracle. A
differential result across them measures the simulator.

Printed on every run including passes. If an oracle produces false failures,
its passes are weak too.

## Verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | booted and reported success |
| `PASS_WITH_UNCHECKED_ASSERTIONS` | booted, but a pad self-check failed |
| `WRONG_RUN` | the log is evidence about a different netlist |
| `NOT_RUN` / `NO_LOG` | no verdict exists yet |
| `ORACLE_ARTEFACT_SUSPECTED` | the netlists differ only in ways it cannot see |
| `UNDECIDABLE_NO_CONTROL` | a failure with nothing to compare against |
| `DESIGN_SUSPECT` | a failure whose netlist difference reaches logic |

`UNDECIDABLE_NO_CONTROL` is deliberate. A failing run alone cannot separate a
design fault from an oracle artefact, and saying so is more useful than a
guess dressed as a localisation.

## Two things it surfaces that nothing else did

**Pad self-checks.** `mosaic_block_a_padwrap.sv` prints `[PADWRAP] FAIL ...`
when a pad control is wrong. Nothing under `harness/` grepped for it, so a run
with a wrong pad configuration read as a clean pass.

**Attribution before verdict.** A log that names another run is refused rather
than reported. Logs written before the run header reached the log file carry no
identity at all, and those are noted as unattributable rather than trusted.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

No MCP tool exposes this skill yet. In a gated session (plugin or `mosaic agent --driver claude|omp`) do not run it through a shell: ask the user to run the CLI command shown above and paste the JSON back.
