# oh-my-soc — Agentic Harness for MOSAIC-SoC

> Based on [oh-my-pi](https://github.com/can1357/oh-my-pi) (vendored for study at
> `refs/IP_Tools/oh-my-pi`), adapted for MOSAIC-SoC EDA flows.

The before/after runtime audit and verification boundary are recorded in
[`EVALUATION.md`](EVALUATION.md).

oh-my-soc is the Phase 2 agentic harness for MOSAIC-SoC. It combines a
**bounded model/tool/observation loop** with deterministic skills for SoC
generation, core integration, verification, DRC triage, and documentation.
The built-in API driver can select tools and react to their results; omp and
Claude can own the same loop through the skill cards; the deterministic driver
is an explicitly labelled CI-safe workflow rather than a pretend LLM.

**Design principle:** The agent *assists and is checked by* deterministic
tooling. It never replaces signoff. The agent translates intent; the harness
chooses typed operations and reacts to observations; deterministic gates retain
authority over validity and run the real EDA checks.

## How the agent connects (the oh-my-pi adaptation)

We **drive oh-my-pi, we don't fork it** — the same layout serves Claude Code:

- **`.claude/skills/<name>/SKILL.md`** — skill cards discovered by BOTH
  Claude Code and omp (omp's `claude` skill provider). Each card teaches the
  agent when to use a skill, the exact CLI, the output contract, and a
  failure playbook.
- **`.omp/tools/oh-my-soc.ts`** — a thin omp custom tool that shells to
  `python -m harness ... --json` and returns the parsed `SkillResult`.
- **`python -m harness <skill> <cmd> [--json]`** — the CLI both agents (and
  CI) call. `--json` emits the raw `SkillResult`; exit code 1 on failure.
- **`harness/agent.py` + `agent_tools.py`** — the in-process, bounded API
  agent loop and typed tool registry. A conservative policy classifier derives
  a non-escalatable outcome ceiling from the user's words; the model confirms
  it in a visible `request_scope` call before action. Models cannot invoke arbitrary shell;
  failed topology/generation/simulation gates are returned as observations and
  cannot be skipped. Evidence is bound to the requested config/core, planned
  core counts and roles, initial artifact SHA-256, and the complete MOSAIC
  source-closure digest—not merely a successful tool family or path. Verifying
  an existing config/testbench never implies permission to regenerate it.
- **`harness/events.py`** — normalized session/decision/tool/progress/gate
  events rendered live in a terminal or emitted as JSON Lines. Every run also
  records a durable journal under `build/agent/sessions/`.

## Quick start

Three equivalent ways to invoke — pick by context:

```bash
./oh-my-soc <skill> <command> [args...]        # zero-install launcher (any cwd)
oh-my-soc <skill> <command> [args...]          # after: .venv/bin/python -m pip install -e .
python -m harness <skill> <command> [args...]  # module form (used by the agent cards)

oh-my-soc setup                                # choose the intent driver (first run does this)
oh-my-soc agent "an SoC with ..."              # one-liner: dispatch to the configured driver
oh-my-soc agent "..." --events-jsonl           # in-process normalized live event stream
oh-my-soc agent "..." --dry-run                # read/plan only; writes and execution denied
oh-my-soc agent "..." --require-evidence analysis # explicit side-effect/evidence ceiling

python -m harness config-author generate --preset poc --name my_soc
python -m harness config-author validate mosaic.yaml
python -m harness config-author wake-demo picorv32     # canonical 3-hart bring-up config
python -m harness soc-from-prompt run "an SoC with one cv32e20 controller, \
    two picorv32 workers, 64KB sram, tdu, a uart" --run
python -m harness flow-runner run tb-soc-wake --config configs/mosaic_picorv32.yaml
python -m harness wrapper-smith fetch https://github.com/<org>/<core>@<commit> --subdir hdl
python -m harness wrapper-smith analyze hw/vendor/mosaic/picorv32 --top picorv32
python -m harness tb-smith generate picorv32
python -m harness tb-matrix run --tier validate    # combination coverage (248-config array)
python -m harness drc-triage analyze build/reports/magic_drc.rpt
python -m harness doc-gen memory-map
python -m harness topo-viz render configs/mosaic_picorv32.yaml -o topo.html
```

## First run: choosing your driver (omp-style)

A bare interactive `./oh-my-soc` with no saved config launches the picker
(never in pipes/CI — TTY only); rerun any time with `oh-my-soc setup`:

```console
$ ./oh-my-soc
First run — no driver configured yet.
  1) deterministic   visible scope-aware workflow; no model or keys (CI-safe)
  2) claude          Claude Code interactive agent        [detected]
  3) omp             oh-my-pi full interactive TUI        [status detected live]
  4) api             built-in multi-turn tool-calling agent
```

oh-my-pi's picker chooses the model for its loop; oh-my-soc's picker chooses
who owns the loop. The Python API agent enforces gate ordering itself. omp and
Claude run their real interactive UIs and compose the same deterministic gates
through the omp tool or visible CLI calls, so activity is not hidden by print
mode.

| driver | what happens on `oh-my-soc agent "<request>"` |
|---|---|
| `deterministic` | visible, scope-aware evidence workflow (default; CI-safe; no model) |
| `claude` | hands an interactive TTY to Claude Code, which invokes the documented harness CLI through visible Bash calls |
| `omp` | launches omp's full TUI with the `oh_my_soc` tool—never `--print` or a fake startup `/skill` command |
| `api` | built-in Anthropic/OpenAI-compatible streaming tool loop; tool results return to the model for bounded recovery/replanning |

Normalized `--events-jsonl`, `--dry-run`, and harness approval flags belong to
the in-process `api`/`deterministic` policy boundary. External Claude/omp
drivers deliberately support interactive TTY handoff only and use their native
permission model; unsupported policy/headless flags fail closed. Use `api` for
machine-readable headless sessions.

`--require-evidence` overrides automatic scope derivation. Supported outcomes
are `analysis`, `config`, `rtl`, `simulation`, `physical`, `integration`,
`testbench`, `documentation`, and `drc`. Ambiguous automatic requests remain
analysis-only, and the model cannot widen the derived ceiling.

Non-interactive: `oh-my-soc setup --driver api --api-kind anthropic
[--model M] [--base-url URL] [--env-key VAR] --non-interactive`.
Config: `~/.config/oh-my-soc/config.json` — the API key is **never stored**,
only the env-var name that holds it. `oh-my-soc setup show` prints the
current state + what's detected on this machine.

### OpenCode Go

Subscribe and copy the key using the official
[OpenCode Go setup](https://opencode.ai/docs/go/). Load it into the shell
without putting it in command history, then select the preset:

```zsh
read -rs "OPENCODE_API_KEY?OpenCode Go API key: "
printf '\n'
export OPENCODE_API_KEY
oh-my-soc setup --driver api --api-kind opencode-go --non-interactive
oh-my-soc agent "inspect mosaic.yaml" --driver api
```

The preset stores only `env_key: OPENCODE_API_KEY`; it defaults to raw API
model ID `kimi-k2.7-code` and API root `https://opencode.ai/zen/go/v1`.
Do not use the OpenCode TUI model prefix (`opencode-go/kimi-k2.7-code`) here.
The harness API driver reads this environment variable directly; it does not
import an existing OpenCode `/connect` login from
`~/.local/share/opencode/auth.json`.
The preset intentionally supports only Go models documented on the
OpenAI-compatible `/chat/completions` endpoint. For a Go model documented on
the Anthropic Messages endpoint (currently MiniMax and Qwen), use the generic
form instead:

```bash
oh-my-soc setup --driver api --api-kind anthropic --model qwen3.7-plus \
  --base-url https://opencode.ai/zen/go --env-key OPENCODE_API_KEY \
  --non-interactive
```

## What a live agent run looks like

The terminal is an event renderer, not delayed log decoration. Decisions and
tool output are printed while the subprocess is still running:

```console
$ oh-my-soc agent "build one cv32e20 titan and two serv workers with tdu"
╭─ oh-my-soc agent
│  driver=api · session=... · build one cv32e20 ...
◇ model turn 1: choosing the next evidence step
  │ I will classify the requested outcome before taking action.
→ request_scope {"scope":"simulation","rationale":"the user asked to build and verify"}
✓ request_scope: request scope: simulation
→ soc_plan {"request":"..."}
✓ soc_plan: parsed ... repairs: assigned worker boot addresses
→ topology_check {"path":"configs/mosaic_prompted.yaml"}
✓ topology_check: clean
→ flow_run {"flow":"mosaic-gen-config", ...}
  │ [MCU-GEN] Processing cpu_subsystem.sv.tpl ...
✓ flow_run: Flow 'mosaic-gen-config' PASS
→ flow_run {"flow":"tb-soc-generic", ...}
  │ ### [3/4] building the full-SoC Verilator model ...
  │ EXIT SUCCESS
✓ flow_run: Flow 'tb-soc-generic' PASS
╰─ verified · full-SoC gate proven
```

Human mode uses compact ANSI styling only on a TTY. `--events-jsonl` emits the
same lifecycle as stable newline-delimited JSON with monotonic sequence
numbers. `--json` remains the single final `SkillResult` contract. Long flow
processes run in their own process groups, so timeout/cancellation cleans up
simulator descendants. The in-memory event tail is bounded; the complete local
journal is mode `0600` under `build/agent/sessions/`. Journals contain the user
prompt and tool output, so their retention should follow the project's data
handling policy.

## Step-by-step walkthroughs

Real commands with real outputs (from the runs that validated the harness).
Every command accepts a global `--json` flag → raw `SkillResult`
(`{ok, skill, summary, details, errors}`) and exit code 1 on failure.

### Walkthrough 1: prompt → verified SoC

**Step 1 — parse only.** See how the deterministic grammar reads the request
before anything is written:

```console
$ python3 -m harness soc-from-prompt plan \
      "an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, tdu, a uart"
[OK] parsed: 1x cv32e20 (titan); 2x picorv32 (atlas); sram 64 KB; tdu on;
     peripheral uart | repairs: flattened 2x picorv32 workers to per-hart
     groups; assigned worker boot addresses (0x1000, 0x2000, ...)
```

Check `details.intent.unrecognized` — tokens the grammar could not place.
Nothing is silently guessed: multi-count worker groups are *flattened* (each
worker hart needs its own boot address + demo program) and every repair is
reported. If no orchestrator is named, a `cv32e20` titan is added — reported.

**Step 2 — write the config only** (no `--run`): produces
`configs/<name>.yaml`, validated against the LIVE core registries.

```console
$ python3 -m harness soc-from-prompt run "<same text>" --name my_soc
[OK] config written: .../configs/my_soc.yaml (use --run to generate + verify)
```

**Step 3 — the full gated pipeline** (`--run`). Stops at the FIRST failed gate:

```console
$ python3 -m harness soc-from-prompt run "<same text>" --run --name my_soc
[OK] SoC 'my_soc' generated AND verified (all-hart liveness EXIT SUCCESS) from prompt
  config       ok=True  Generated valid config 'my_soc'
  topo_check   ok=True  my_soc.yaml: clean            ← semantic gate
  mosaic_gen   ok=True  Flow 'mosaic-gen-config' PASS ← RTL renders
  generic_liveness ok=True  Flow 'tb-soc-generic' PASS ← every configured hart required
  doc          ok=True  Generated config summary
```

The generic liveness gate is strict: it consumes the generated boot-image
manifest, builds one liveness image per boot slot, wakes and dispatches unique
work to every non-TITAN hart, and permits `EXIT SUCCESS` only after every
configured hart reports its sentinel. Return code alone never passes. The
other full-SoC flows remain workload-specific regressions.

**Step 4 — document + visualize:**

```console
$ python3 -m harness doc-gen config configs/my_soc.yaml
$ python3 -m harness topo-viz render configs/my_soc.yaml -o build/my_soc_topo.html
```

### Walkthrough 2: integrate a core from GitHub (the Hazard3 record)

**Step 1 — fetch** (pinned clone + license gate + provenance):

```console
$ python3 -m harness wrapper-smith fetch \
      https://github.com/Wren6991/Hazard3@8af99293 --subdir hdl
[OK] fetched hazard3 @ 8af992930f71 (license: Apache-2.0)
     -> build/wrapper_smith/fetch/hazard3/hdl; next: wrapper-smith analyze ...
```

GPL-family licenses are flagged as errors — stop and review before vendoring.
The provenance (URL, exact commit, license) is recorded and later folded into
the vendored `.core` header automatically.

**Step 2 — analyze** (port parse + protocol classification):

```console
$ python3 -m harness wrapper-smith analyze \
      build/wrapper_smith/fetch/hazard3/hdl --top hazard3_cpu_2port
[OK] hazard3_cpu_2port: family=ahb_split (confidence 1.00, parser verible,
     63 ports; runner-up wishbone_unified 0.00) -> ...analysis.json
```

Read `details.analysis`: `classification` (family/confidence/evidence/
runner_up), `control` (clk, reset **polarity** — active-high cores get an
inversion note, irq width, boot parameter) and `todos` — your work queue.
Confidence < 0.5 → family `unknown` + the full port inventory; pick
`--family` yourself (never a silent wrong template).

**Step 3 — scaffold** (dry-run, review, then apply):

```console
$ python3 -m harness wrapper-smith scaffold hazard3 --from analysis.json \
      --vendor-from build/wrapper_smith/fetch/hazard3/hdl          # DRY-RUN
[OK] scaffolded 'hazard3' as ahb_split: 45 written, 5 edited, 0 already-present
     — staged (dry-run) in build/wrapper_smith/hazard3/stage; 7 TODO(s)
$ # review the staged diff, then:
$ python3 -m harness wrapper-smith scaffold hazard3 --from analysis.json \
      --vendor-from build/wrapper_smith/fetch/hazard3/hdl --apply
```

`--apply` covers all 8 touchpoints — wrapper, `AVAILABLE_CPUS`, the typed
`CORE_SPECS` registry (from which `SCI_CORES` is derived),
the `cpu_subsystem.sv.tpl` branch (guard-wrapped at the anchor), `sci.core`
file **and** `depend:` edge (only when the vendor `.core` exists — no
dangling VLNVs), `gen_filelist.py` visibility, vendor tree + `.core`,
`configs/mosaic_<core>.yaml` — then runs a **full FuseSoC-graph resolution
smoke**: if the graph breaks, the scaffold fails loudly. Rerunning is
idempotent (`already-present`).

**Step 4 — agent-fill.** Open `hw/sci/<core>_sci.sv` and resolve every
`TODO(wrapper-smith)` marker: the core-instantiation port map (the analysis
lists every port), irq wiring onto the SCI contract (`irq_i[3]`=msip,
`[7]`=mtip, `[11]`=meip), tie-offs. The proven wrapper for the family in
`hw/sci/` is the reference. This step is irreducible for arbitrary IP — and
it cannot go silently wrong, because:

**Step 5 — verify** (the gates):

```console
$ python3 -m harness tb-smith generate hazard3
[OK] generated tb/sci/hazard3/ (split ports, watchdog 200000 cycles).
$ python3 -m harness tb-smith run hazard3
[OK] tb/sci/hazard3: TB PASS — 229 cycles, 8+1 reqs
$ python3 -m harness tb-smith wake-demo hazard3
[OK] wake-demo(hazard3): Flow 'tb-soc-wake' PASS
```

The generated TB checks dormancy (a parked core must not touch the bus),
wake, liveness, and the sentinel write; the wake demo proves the core inside
the full SoC under the TDU. `TB FAIL reason=dormancy_bus_activity` → missing
request masking; `reason=liveness_no_requests` → reset polarity or
clock-stall; alive-but-no-sentinel → ack/rvalid timing (see the tb-smith
skill card's playbook).

## Skills

| Skill | Purpose | Input → Output |
|-------|---------|---------------|
| **config-author** | Generate/validate `mosaic.yaml` | Structured params / preset / wake-demo shape → valid YAML |
| **soc-from-prompt** | Deterministic prompt→SoC pipeline (no LLM needed) | NL text → config → generate → wake demo → report |
| **flow-runner** | Run EDA flows with structured reporting | Flow name → timed, parsed result (EXIT SUCCESS gates) |
| **wrapper-smith** | Wrap any open-source core/IP for the SCI | RTL → bus classification → scaffolded wrapper + all integration touchpoints |
| **tb-smith** | Generate + run per-core verification | Core name → single-hart SCI TB + full-SoC wake demo |
| **tb-matrix** | Combination-coverage testing of the whole integration space | Registry axes → pairwise covering array + sim corners → tiered gates (validate/render/sim) |
| **drc-triage** | Parse DRC/LVS reports, propose fixes | Report file → classified violations + suggestions |
| **doc-gen** | Generate documentation | Config/artifacts → markdown docs |
| **topo-viz** | Semantic config checks + topology diagram | Config → checks + interactive SVG/HTML |
| **setup** | omp-style driver picker (deterministic/claude/omp/api) | choice → `~/.config/oh-my-soc/config.json` (keys never stored) |
| **physical-intent** | Derive the floorplan/hardening config, and watch routing | Config → die size + LibreLane config; run dir → routability verdict |
| **mcp-server** | Serve the gated registry to an MCP client over stdio | Request → locked scope ceiling + 20 gated tools |

### mcp-server: the same gates, for an external agent

`--driver claude` used to be a `subprocess.call` with a prompt: the scope
ceiling, evidence binding and completion gate existed in `AgentRunner` and
applied to nothing the external driver did.

```
oh-my-soc mcp-server --request "simulate a two-core SoC and run the wake demo"
```

Derives the ceiling from the request with the same `classify_request_scope` the
built-in loop uses, **locks it before any client connects**, and holds one
`AgentState` for the session so evidence recorded by one call gates the next.
The gates live in `harness/gates.py` and are called by both paths, so a refusal
over MCP is the *identical* string produced in-process — not a second
implementation that agrees today.

`oh-my-soc agent --driver claude` now wires this up automatically:
`--mcp-config` + `--strict-mcp-config` + an `--allowedTools` list naming only
the `mcp__oh-my-soc__*` tools, with `Bash`/`Write`/`Edit` disallowed. That last
part is load-bearing — with Bash available the model can call
`python3 -m harness ...` directly and the gate is decoration.

**Design questions, from a second server.** If `naja-scope-mcp`
(najaeda/naja-scope) is installed, the enforced session also gets it: what
drives a net, hierarchy under an instance, combinational cones, and the source
line an object came from — answered without pasting 507 files at the model.

The boundary is deliberate and visible in the config. **Our server produces
evidence** (scope-ceilinged, digest-bound, gated); **naja-scope produces
facts**. An agent that knows the connectivity has not proven anything, and a
naja-scope answer is never a gate result.

Its `save_snapshot` tool is **excluded** — it writes to disk, and the enforced
session may not write outside the gated tools. The allowlist is enumerated
rather than wildcarded, so a tool added by a future release is not admitted
silently; a test probes the running server and fails if the lists diverge.

`--driver omp` **refuses to launch**: oh-my-pi speaks no MCP, so the gates
cannot travel with it. `--unenforced` proceeds after saying plainly that no
authorization ceiling applies.

### physical-intent: sizing a die, and knowing when to stop

```
oh-my-soc physical-intent floorplan --config configs/mosaic_blockc_4hart.yaml
oh-my-soc physical-intent harden    --config ... --design mosaic_block_c -o out.yaml
oh-my-soc physical-intent watch     --run-dir flow/librelane/experimental/runs/<tag>
```

The config is **validated, not just parsed**. This used to be a bare
`.get("soc")`, so a typo'd key or an illegal topology produced a die size
anyway and the first sign of trouble was hours into a flow.

Internally the physical lowering reads a typed `DesignIntent`
(`harness/intent.py`) rather than raw YAML — roadmap M1's fourth exit
criterion. `DesignIntent` does not re-validate; `from_config` delegates to
`validate_soc_config`, so there is one notion of a legal config, not two.

`--utilisation` now defaults to **the densest target that design size has been
shown to route at** (81% at 2 harts, 79% at 3, 65% at 4) rather than a flat
0.75. A flat default is what sized a 4-hart die that spent eleven hours failing
to route. Pass the flag explicitly to override; the config records a
`# routability` note either way.

```
oh-my-soc physical-intent metrics --run-dir runs/<tag> [--compare runs/<other>]
```

`metrics` reports a finished run's signoff numbers **typed** — units, corner
and PDK on every value, area converted to mm² by the type rather than by hand,
and a delta against a second run. Every experiment in this project previously
ended with someone opening two `metrics.json` files and writing the comparison
into prose; a confounded experiment survived a full run that way, because the
die had moved and nothing printed the die.

```
oh-my-soc physical-intent metrics  --run-dir runs/<tag> --record
oh-my-soc physical-intent evidence --run-dir runs/<tag>     # still current?
oh-my-soc physical-intent evidence --pdk gf180mcuD          # what a PDK swap hits
```

`--record` stores the run in a **content-addressed evidence store** under
`build/evidence/`, keyed on everything that decides what its numbers mean: the
RTL bundle, the hardening config (minus machine-specific paths), the PDK, the
standard-cell library, the tool image (librelane version **and** the flake
lock's hash) and a digest of the modules that parsed it.

Invalidation is not a mechanism — the key *is* the digest of the inputs, so
`evidence --run-dir` answers "is this still current?" by asking whether a
record exists under the key today's inputs produce. Touch a parser and the key
moves:

```console
$ oh-my-soc physical-intent evidence --run-dir runs/blocka_reharden
[OK] evidence is current
$ printf '\n# touch\n' >> harness/evidence/metric.py
$ oh-my-soc physical-intent evidence --run-dir runs/blocka_reharden
[OK] no stored evidence for these inputs — something changed
```

Writing *different* content under an existing key raises rather than
overwriting: the inputs claim to determine the output, so two answers means an
input is not being captured, and that is a bug to find rather than hide.

`watch` reads a run's detailed-routing trajectory and answers whether it is
going to converge — exit **3** means plateaued. It is sticky: a run that
plateaued early and drifted later still reports the plateau, because assessing
only the tail called that eleven-hour failure "converging".

Set `MOSAIC_WATCH_ROUTING=1` on `run_signoff.sh` to poll during the run and
kill it on a plateau (~1.15 h instead of 10.66 h on the one real failure).
Opt-in on purpose: it is a kill switch on multi-hour jobs, and only exit
status 3 triggers it — a watcher that cannot tell never kills anything.

### Registry single-sourcing

Core lists (`VALID_CORE_IPS`, `SCI_CORES`) are derived from the typed
`util/xheep_gen/core_registry.py::CORE_SPECS` mapping — never edit them in
`mosaic_config.py` or `harness/core.py`.
`test/test_x_heep_gen/test_harness_core.py` enforces sync and validates every
shipped `configs/mosaic_*.yaml`. Simulation-only cores (cva6, rocket, boom —
excluded from the GF180 tapeout) are rejected in tapeout presets.

### flow-runner

Wraps 19 flows (make targets + tb runners) with timeout protection, structured
log parsing and hard result gates. Full-SoC sims (`tb-soc-generic`,
`tb-soc-wake`, `tb-soc-titan`, `tb-soc-fw`) take their config from the
`MOSAIC_CFG` **environment** (pass
`--config`) and REQUIRE the `EXIT SUCCESS` marker — a sim that exits 0 without
it is a FAIL.

```bash
python -m harness flow-runner list
python -m harness flow-runner run mosaic-gen-config --config configs/mosaic_boom.yaml
python -m harness flow-runner run tb-soc-wake --config configs/mosaic_boom.yaml
```

`tb-soc-generic` is the agent completion gate. It consumes the generated
`boot_images.json`, builds per-slot liveness firmware, and requires every
configured hart's exact sentinel before `EXIT SUCCESS`. An explicit worker-only
`profile: testbench` releases hart 0 solely as a bootstrap and uses targeted TDU
dispatch for the other harts; this does not weaken the production requirement
for a leading TITAN. Singleton Rocket/BOOM hart-0 controllers use PMA-uncached
translated sentinel/TDU/`soc_ctrl` windows and remain simulation-only. The other
`tb-soc-*` flows retain their workload-specific roles.

### wrapper-smith + tb-smith: the scaffold → fill → verify triangle

Fully-deterministic wrapping of *any* IP is impossible (bus semantics vary),
so the mechanism is honest about the split:

1. **wrapper-smith analyze** parses the core's ports (verible → yosys →
   regex ladder) and classifies its native bus against the 8 protocol
   families proven in `hw/sci/` (wishbone unified/split, req/gnt, picorv32
   unified-native, snitch reqrsp, AXI4-bridge, TileLink-bridge, AHB-Lite).
2. **wrapper-smith scaffold** stages `hw/sci/<core>_sci.sv` from the family
   template plus ALL 8 integration touchpoints, with `TODO(wrapper-smith)`
   markers at every semantic gap — the agent fills those, guided by the
   skill card checklist.
3. **tb-smith** generates and runs the self-checking single-hart TB; applied
   integration also requires current full-SoC generic-liveness evidence. Its
   canonical TDU wake demo remains the workload-specific bring-up regression.

### tb-matrix: proving the SPACE, not just the shipped configs

One core passing its TB says nothing about the *combinations* — a split-OBI
worker behind a FlooNoC fabric next to an RV64 tile is its own integration
risk. tb-matrix derives every axis (cores × roles × counts × second-worker
heterogeneity × ISA/parameter variants × bus × scheduler mode × SRAM size ×
peripheral set × topology shape) live from `core_registry.py` and generates:

- a **pairwise covering array** (~250 configs): every legal value pair of
  every two axes appears in at least one config; pairs no legal config can
  realize are reported *blocked with a reason* (e.g. SMP images need
  `mhartid`), never silently dropped;
- a **curated sim boundary set** (~30 configs): every core as a woken
  worker, each fabric × port-shape class, SMP / worker-only / mixed-ABI /
  max-count / alt-parameter corners.

Each config passes through the same gates the shipped demos use — schema
oracle → `make mosaic-gen` render → `run_generic.sh` all-hart liveness
(EXIT SUCCESS) — with crash-safe resume in `build/tb_matrix/report.json`:

```bash
python3 -m harness tb-matrix run --tier validate   # seconds — run always
python3 -m harness tb-matrix run --tier render --limit 20
python3 -m harness tb-matrix run --tier sim --limit 5   # a campaign, resumable
python3 -m harness tb-matrix report
```

A `fail` in the report is a *finding about the platform* — an untested
combination that actually breaks — which is exactly what the matrix exists
to surface before a user's prompt generates that SoC.

## Architecture

```
harness/
├── __main__.py              # CLI entry point (--json machine mode, first-run wizard)
├── agent.py                 # bounded model -> tool -> observation/recovery loop
├── agent_tools.py           # typed tools, gate ordering, side-effect approvals
├── events.py                # terminal/JSONL events + durable session journal
├── core.py                  # SkillResult, validate_config, AST registries, run_cmd
├── llm.py                   # Anthropic/OpenAI SSE + fragmented tool-call adapters
│                            #   (keys read from env at call time — never stored)
├── skills/
│   ├── config_author.py     # mosaic.yaml generate/validate/presets/wake-demo
│   ├── soc_from_prompt.py   # deterministic NL grammar + pipeline (--llm optional)
│   ├── flow_runner.py       # EDA flows, structured parsing, EXIT SUCCESS gates
│   ├── wrapper_smith.py     # fetch → port parse → classify → scaffold
│   ├── tb_smith.py          # per-core TB generate/run + wake demo
│   ├── tb_matrix.py         # combination coverage: pairwise array + tiered gates
│   ├── setup_wizard.py      # driver picker (deterministic/claude/omp/api)
│   ├── drc_triage.py        # Magic/KLayout/Netgen parsers, fix suggestions
│   ├── doc_gen.py           # Config summary, memory map, run reports
│   └── topo_viz.py          # semantic checks + SVG topology
└── templates/
    ├── wrapper/             # family heuristics + wrapper templates (*.mako)
    └── tb/                  # single-hart TB + run.sh templates (*.mako)
```

## How it fits the project

```
User request
    │
    ▼
Agent loop (API in-process / omp TUI / Claude UI / deterministic workflow)
    │  decision -> typed tool call
    ▼
oh-my-soc deterministic skills
    │  live progress + SkillResult observation
    ▼
Agent loop reacts/replans ────────────────┐
    │                                    │ failed safe gate
    └─ successful required evidence ─────┘
                   │
                   ▼
             final verified summary
```

The agent never runs raw `make` or parses raw DRC output, and never
hand-writes `mosaic.yaml` or `*_sci.sv` from scratch — it always starts from
harness output and fills marked gaps.
