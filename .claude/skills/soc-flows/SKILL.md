---
name: soc-flows
description: >
  Run MOSAIC-SoC EDA flows (RTL generation, Verilator sims, full-SoC TDU
  wake demos, LibreLane hardening, firmware builds, pytest) with structured
  results and hard pass gates. Use instead of calling make or tb scripts
  directly.
---

# soc-flows — flow-runner skill card

## Contract

Every flow runs with timeout protection and structured log parsing. Full-SoC
sims REQUIRE the `EXIT SUCCESS` marker — exit code 0 alone is NOT a pass (the
tb runners exit 0 on sim failure). Trust `ok` in the SkillResult, not rc.

## Commands

```bash
python3 -m harness --json flow-runner list
python3 -m harness --json flow-runner run <flow> [--config <yaml>]
```

Key flows:

| flow | what | config? |
|---|---|---|
| mosaic-gen-config | render RTL from a specific config | argv |
| tb-soc-generic | all-hart liveness for ANY config: every configured hart must report (EXIT SUCCESS gate) | MOSAIC_CFG env — pass --config |
| tb-soc-wake | full-SoC TDU wake demo (EXIT SUCCESS gate) | MOSAIC_CFG env — pass --config |
| tb-soc-titan | all-TITAN SMP demo | --config |
| tb-soc-fw | production C firmware on the full SoC | --config |
| tb-multicore / tb-tdu / tb-idma / tb-tl-obi / tb-log-xbar / tb-floonoc | subsystem TBs | no |
| verilator-lint / verilator-run | x-heep build/run | no |
| firmware-build / firmware-demo | sw/firmware | no |
| harden-classic / harden-chip | LibreLane GF180 chip-level targets (hours; need Nix, the PDK and a PHYSICAL_BUNDLE; approval required) | no |
| gls | gate-level sim of a hardened run (set GLS_RUN in the environment) | no |
| lec | netlist-vs-RTL equivalence (approval required) | no |
| pytest | test/test_mosaic_gen suites | no |

## Failure playbook

- FAIL with rc 0 + no EXIT SUCCESS → read `details.stdout_tail` and
  `tb/mosaic_soc/sim.log` (`sim-generic.log` for tb-soc-generic); a worker that never wrote its sentinel usually
  means wrong boot_addr or a stale generated cpu_subsystem (rerun
  mosaic-gen-config for THIS config first — order matters).
- "does not accept a config override" → only mosaic-gen-config and the
  tb-soc-* flows take --config.
- Timeouts return ok=false with details.timeout — hardening flows are hours.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| list flows | `flow_list {}` |
| run a flow | `flow_run {flow, config?}  (approval-declared flows need the scope's approval token)` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
