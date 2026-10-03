---
name: soc-from-prompt
description: >
  Generate, verify and document a MOSAIC SoC from a natural-language request.
  Use when the user asks for "an SoC with ..." / "build me a chip that ...".
  Deterministic gated pipeline: parse -> config-author -> topo-viz check ->
  mosaic-gen -> full-SoC TDU wake demo (EXIT SUCCESS) -> docs. You translate
  intent; the harness validates and executes — never hand-write mosaic.yaml.
---

# soc-from-prompt — prompt → verified SoC

## When to use / when not

Use for any "make me an SoC ..." request. Do NOT hand-write `mosaic.yaml` or
call `make` directly — every step below is a deterministic CLI that validates
before executing and returns structured JSON (`--json`).

## Contract

You translate the user's intent into CLI arguments; the harness guarantees a
valid, generation-ready config and runs the real checks. If your reading of
the prompt differs from the deterministic parser's, prefer YOURS — but express
it through `config-author generate` flags, never by editing YAML by hand.

**A frequency in the request is design intent, not a peripheral.** The grammar
reads `25 MHz`, `25MHz`, `clocked at 25 MHz` and `clock at 25 mhz` into
`soc.objectives.target_clock_mhz`; `--target-clock-mhz` is the typed equivalent.
Set it on anything that will be hardened — `physical-intent harden` derives
`CLOCK_PERIOD` from it and refuses without one.

Note the collision it resolves: bare `clock` means the **timer peripheral** in
this grammar, so "with a 25 MHz clock" would otherwise request a timer as well,
which a tapeout config rejects for not being uart-only. The frequency rule
swallows an adjacent `clock`; the bare word still means a timer everywhere else.

## Interactive agent behavior

When an agent drives this skill (Claude Code, Codex, opencode or omp with the
oh-my-soc MCP server), create a visible multi-step plan before acting and call
each gate as a separate **MCP tool** — the table under "MCP tools" below maps
every step. Do not run the `python3 -m harness` commands through a shell: that
path has no session gates, and the plugin's hook refuses it. The CLI pipeline
below is the human path. Feed a failed result back into the next decision;
never continue to simulation after a failed topology or generation gate, and
never write a final success message without a successful `tb-soc-generic`
`EXIT SUCCESS` observation and a `session_status` that reports done.

## Pipeline (run the gates IN ORDER; stop at the first failure)

```bash
# 1. See how the deterministic grammar reads the request (writes nothing):
python3 -m harness --json soc-from-prompt plan "<user text>"
#    -> inspect details.intent.unrecognized: tokens the grammar could not
#       place. If they matter (e.g. a core name it missed), fix via step 2.

# 2a. Accept the plan (writes configs/<name>.yaml):
python3 -m harness --json soc-from-prompt run "<user text>" --name <name>
# 2b. OR author explicitly when your reading differs:
python3 -m harness --json config-author generate --name <name> \
    --core cv32e20:1:titan --core picorv32:2:atlas \
    --sram 64 --tdu --mode dynamic --peripheral uart,gpio \
    --target-clock-mhz 25

# 3. GATE — semantic checks (bank constraints, address overlaps, roles):
python3 -m harness --json topo-viz check configs/<name>.yaml

# 4. GATE — generate the RTL:
python3 -m harness --json flow-runner run mosaic-gen-config --config configs/<name>.yaml

# 5. GATE — topology-generic liveness must exercise every configured hart:
python3 -m harness --json flow-runner run tb-soc-generic --config configs/<name>.yaml

# 6. Document:
python3 -m harness --json doc-gen config configs/<name>.yaml
python3 -m harness topo-viz render configs/<name>.yaml -o build/<name>_topo.html
```

One-shot alternative (steps 2a–5 chained with the same gates):

```bash
python3 -m harness --json soc-from-prompt run "<user text>" --run
```

## Output contract

Every command prints a SkillResult: `{ok, skill, summary, details, errors}`.
`--json` gives the raw object and exit code 1 on failure. Key details:
`plan` → `details.intent` (core_groups/matched/unrecognized/repairs);
`run --run` → `details.{config,topo_check,mosaic_gen,generic_liveness,doc}` per-stage.

## Failure playbook

| Failure | Next deterministic step |
|---|---|
| plan: "nothing recognized" | List cores for the user: `python3 -m harness config-author presets`; re-ask or use `config-author generate` directly |
| plan: sim-only + tapeout error | cva6/rocket/boom are SIMULATION-ONLY (GF180 exclusion) — drop the core or the tapeout requirement |
| topo-viz check fails (num_banks/log bus) | Re-generate with corrected `bus_opts` via config-author; check the error's suggested constraint |
| mosaic-gen fails | Read stderr_tail in details; usually a config field typo — regenerate, don't patch generated .sv |
| generic liveness: no EXIT SUCCESS | Inspect `tb/mosaic_soc/sim-generic.log`; if a worker never wrote its sentinel, check its generated boot slot and TDU dispatch |

## Notes

- Roles: titan = free-running orchestrator (boots via boot ROM); atlas/nano =
  dormant workers woken by the TDU. TDU demos need worker `boot_addr`s
  (0x1000/0x2000 — the repair adds them automatically).
- Multi-titan (SMP) is legal; worker-only configs are legal for subsystem TBs.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `oh-my-soc approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| 1. bind the request | `session_new {request}` |
| 2. set the ceiling | `request_scope {scope, rationale}` |
| 3. read the request | `soc_plan {request}` |
| 4. write the config | `soc_generate {request, name?}  or  config_generate {...} when your reading differs` |
| 5. semantic gate | `topology_check {path}` |
| 6. generate RTL | `flow_run {flow: mosaic-gen-config, config}` |
| 7. liveness gate | `flow_run {flow: tb-soc-generic, config}` |
| 8. document | `doc_config {path}; topology_render {path, output}` |
| 9. verdict | `session_status {}` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
