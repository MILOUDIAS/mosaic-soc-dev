---
name: tb-matrix
description: >
  Combination-coverage testing of the whole SoC integration space: derive
  every axis (cores x roles x counts x fabrics x ISA/parameter variants x
  scheduler x memory x peripherals) from the core registry, generate a
  pairwise covering array plus curated sim corners, and gate every config
  through validate -> mosaic-gen render -> the all-hart liveness sim. Use it to
  prove ANY generated MOSAIC SoC works, not just the shipped demo configs.
---

# tb-matrix — test every integration combination

tb-smith proves ONE core; tb-matrix proves the SPACE. Axes come live from
`util/mosaic_gen/core_registry.py`, so a core integrated through
wrapper-smith automatically enters the matrix — never edit the axes by hand.

## Commands

```bash
python3 -m harness --json tb-matrix axes                  # show the derived axes
python3 -m harness --json tb-matrix plan --tier sim       # enumerate, no execution
python3 -m harness --json tb-matrix run  --tier validate  # oracle: all 248 configs, seconds
python3 -m harness --json tb-matrix run  --tier render [--limit N]   # mosaic-gen gate
python3 -m harness --json tb-matrix run  --tier sim    [--limit N]   # EXIT SUCCESS gate
python3 -m harness --json tb-matrix report                # cumulative results
```

## Tiers (cheap first — never start with sim)

1. **validate** — in-process `validate_soc_config` on the full pairwise
   covering array (248 configs, milliseconds each). Run it after ANY
   registry or generator change.
2. **render** — `make mosaic-gen` per config: templates + software gen must
   succeed. ~10–60 s per config; use `--limit` to bound a session.
3. **sim** — `tb/mosaic_soc/run_generic.sh` on each of the 30 curated
   configs: EVERY configured hart must report before EXIT SUCCESS. Minutes per config — this is a
   campaign, not a smoke test. Results persist to
   `build/tb_matrix/report.json`; re-running resumes past configs that
   already passed (`--no-resume` to force).

## Coverage contract

Every legal value PAIR of every two axes appears in at least one generated
config, or is listed in `details.blocked` with the constraint that blocks it
(e.g. "serv lacks mhartid — SMP image cannot self-identify"). Nothing is
silently dropped. A `fail` entry in the report is a FINDING about the
platform (an untested combination that breaks), not noise — triage it or
file it; do not delete the report to make the summary green.

## Failure playbook

- `invalid` at plan/validate time: the synthesizer and the registry disagree
  — fix `contract_params`/`synth_config` in `harness/skills/tb_matrix.py`
  (mirror of core_registry's cross-field rules), never the oracle.
- `fail` at render: reproduce with
  `make mosaic-gen MOSAIC_CFG=build/tb_matrix/configs/<name>.yaml`.
- `fail` at sim: reproduce with
  `MOSAIC_CFG=build/tb_matrix/configs/<name>.yaml tb/mosaic_soc/run_generic.sh`,
  then debug like any wake-demo failure (boot manifest, wake mask, sentinel
  window). The config file names are stable hashes of the axis assignment.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| plan a tier | `tb_matrix_plan {tier}` |
| run a tier | `tb_matrix_run {tier, limit?}  (above `validate` needs an approval token)` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
