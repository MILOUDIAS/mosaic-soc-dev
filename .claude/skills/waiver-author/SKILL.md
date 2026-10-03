---
name: waiver-author
description: Check a signoff waiver against the run it cites. Use before recording a waiver, after any re-harden that moves a waived count, and whenever a reviewer asks whether a waiver still holds. It audits; it does not write waivers.
---

# waiver-author — audit a waiver against its evidence

## What it is for

A waiver accepts a known defect in silicon. `parse_waivers` checks its SHAPE
at load time and never opens the `evidence` field, so a waiver can pass every
structural check while no longer describing the chip.

That is not hypothetical. The waiver in this repository has a ceiling of 1,
cites `runs/blocka_sdc`, passes every existing check, and the design measures
5 on the run being submitted. Its cited run is a 1117.5 um macro, the die size
the integrator rejected.

```bash
python3 -m harness --json waiver-author
python3 -m harness --json waiver-author --file path/to/waivers.yaml
```

`ok: false` means at least one waiver no longer matches its evidence.

## What it checks

| Check | Why it exists |
|---|---|
| `evidence` resolves | nothing has ever opened this field |
| the metric is in that run | a waiver can name a metric the run never measured |
| ceiling == measured | a higher ceiling waives headroom nobody observed |
| declared violators match | a count is not an argument |
| preconditions hold | justifications state conditions in prose that nothing enforces |
| not expired | `review_by` is a date nobody rereads |
| not superseded | a waiver can be self-consistent and obsolete |

The last one is the subtle case. A waiver whose ceiling matches its own
evidence run is internally perfect and still wrong if the design has moved to a
newer run measuring something else.

## The two optional fields

A count is not an argument. `accepted_max: 5` says five of something are
tolerated, not which five or how bad. The same ceiling accepts five clock-buffer
roots at fanout 16 and five combinational nets at fanout 40.

```yaml
- metric: design__max_fanout_violation__count
  design: mosaic_block_a
  accepted_max: 5
  expected_violators:          # WHICH five. Checked against the STA report.
    - clkbuf_0_clk_i_regs/Z
    - _52521_/Z
  preconditions:              # what the argument rests on, as data
    design__max_slew_violation__count: 0
    design__max_capacitance_violation__count: 0
  ...
```

`preconditions` are evaluated against the run's RAW metrics before any waiver is
applied, so no waiver can satisfy another's precondition.

Both are optional. Omitting them is reported as a note, not an error: an
unpinned waiver accepts any N violations rather than the ones it describes.

## Why it does not write waivers

Recording a waiver is a decision to accept a defect in manufactured silicon.
Under `harness/skill_policy`, a skill that wrote one would write evidence and
need approval at any cost, because the rule is
`approval == (cost is HOURS) or evidence`.

That gate would be correct. The better answer is that the decision stays with a
person and the tool reports whether it still matches the measurement. So this
skill is declared `effect: read`.

## When it fires in real use

Run it after any re-harden that moves a waived count. The failure it exists to
catch is raising a ceiling while leaving a justification that no longer
describes the data: when this project moved a ceiling 4 to 5, the prose still
said every violator was a 16-way clock root, and the fifth was a combinational
buffer at fanout 11.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

No MCP tool exposes this skill yet. In a gated session (plugin or `mosaic agent --driver claude|omp`) do not run it through a shell: ask the user to run the CLI below and paste the JSON back.
