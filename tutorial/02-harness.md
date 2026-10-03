# 2. Use the deterministic harness

`oh-my-soc` wraps the generator and verification commands in typed tools with
structured results and hard gates. Start with the deterministic driver: it is
CI-safe, uses no model, and exercises the same validation and EDA tools as the
agent path.

Run every command from the repository root.

First prepare the project environment. The simulation stages also need
Verilator 5.x and a bare-metal RISC-V GCC toolchain, as described in
[Chapter 1, Stage 0](01-generator.md#stage-0--prepare-the-tools).

```bash
make venv
source .venv/bin/activate
```

## Stage 1 — select the deterministic driver

```bash
./oh-my-soc setup --driver deterministic --non-interactive
./oh-my-soc setup show
```

Expected key lines:

```text
[OK] driver 'deterministic' saved to ~/.config/oh-my-soc/config.json
[OK] driver: deterministic
```

Artifact created: `~/.config/oh-my-soc/config.json`. It contains the driver
choice, not project RTL or credentials.

## Stage 2 — author a config without hand-editing YAML

Write generated tutorial output under the ignored `build/` tree:

```bash
mkdir -p build/tutorial
./oh-my-soc config-author generate \
  --name tutorial_authored \
  --core cv32e20:1:titan \
  --core fazyrv:1:atlas \
  --core serv:1:nano \
  --sram 32 \
  --boot-rom 2 \
  --bus obi \
  --target simulation \
  --tdu \
  --mode dynamic \
  --peripheral uart,gpio,timer,spi \
  --output build/tutorial/tutorial_authored.yaml
```

Expected:

```text
[OK] Generated valid config 'tutorial_authored' -> build/tutorial/tutorial_authored.yaml
...
"core_count": 3,
"peripheral_count": 4,
"target": "simulation"
```

The author fills registered ISA defaults, FazyRV's `chunksize`, and distinct
worker boot slots (`0x1000`, `0x2000`). It fails rather than emitting an invalid
configuration.

## Stage 3 — run the config and topology gates

```bash
./oh-my-soc config-author validate build/tutorial/tutorial_authored.yaml
./oh-my-soc topo-viz check build/tutorial/tutorial_authored.yaml
./oh-my-soc topo-viz render build/tutorial/tutorial_authored.yaml \
  -o build/tutorial/tutorial_authored_topology.html
```

Expected:

```text
[OK] tutorial_authored.yaml is valid (3 cores, 4 peripherals)
[OK] tutorial_authored.yaml: clean
[OK] rendered obi topology -> build/tutorial/tutorial_authored_topology.html
```

If validation or topology checking fails, stop there. Later stages are not
allowed to turn an invalid config into a PASS.

## Stage 4 — generate through `flow-runner`

```bash
./oh-my-soc flow-runner run mosaic-gen-config \
  --config build/tutorial/tutorial_authored.yaml
```

Expected final marker:

```text
[OK] Flow 'mosaic-gen-config' PASS (<seconds>s, exit=0)
```

The detailed output still contains the same `MOSAIC_BUILD_KEY`, template, PLIC,
software-contract, FuseSoC, and manifest markers shown in the direct tutorial.

## Stage 5 — run the all-hart completion gate

```bash
./oh-my-soc flow-runner run tb-soc-generic \
  --config build/tutorial/tutorial_authored.yaml
```

Expected key lines:

```text
### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓
[OK] Flow 'tb-soc-generic' PASS (<seconds>s, exit=0)
```

`flow-runner` does not accept a simulator exit code by itself. The
`tb-soc-generic` parser requires the underlying `EXIT SUCCESS` evidence for the
requested config.

## Stage 6 — preview natural-language intent

The deterministic grammar reports what it matched, what it did not understand,
and every repair before writing anything:

```bash
./oh-my-soc soc-from-prompt plan \
  "an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, a tdu and a uart"
```

Expected summary:

```text
[OK] parsed: 1x cv32e20 (titan); 2x picorv32 (atlas); sram 64 KB; tdu on; peripheral uart | repairs: flattened 2x picorv32 workers to per-hart groups; assigned worker boot addresses (0x1000, 0x2000, ...)
```

The detailed result must include:

```text
"unrecognized": [],
"repairs": [
  "flattened 2x picorv32 workers to per-hart groups",
  "assigned worker boot addresses (0x1000, 0x2000, ...)"
]
```

## Stage 7 — optional one-command deterministic workflow

This command creates `configs/tutorial_agent.yaml`, then runs the ordered gates
through a visible event stream:

```bash
./oh-my-soc agent \
  "build and verify an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, a tdu and a uart" \
  --driver deterministic \
  --name tutorial_agent \
  --require-evidence simulation
```

Expected stable event sequence (individual tool output is abbreviated):

```text
╭─ oh-my-soc agent
→ soc_plan
✓ soc_plan: parsed ...
→ soc_generate
✓ soc_generate: config written ...
→ topology_check
✓ topology_check: ... clean
→ flow_run
✓ flow_run: Flow 'mosaic-gen-config' PASS ...
→ flow_run
  │ EXIT SUCCESS
✓ flow_run: Flow 'tb-soc-generic' PASS ...
╰─ verified · verified SoC from request; EXIT SUCCESS ...
```

Every session writes a private mode-`0600` journal under
`build/agent/sessions/`. Use `--dry-run` when you want planning only; it denies
project writes and execution but still records the audit journal.

## Stage 8 — beyond one SoC: test the combination space

The stages above prove one configuration. `tb-matrix` proves the space the
generator can produce: it derives every axis (cores × roles × counts × bus
fabric × ISA/parameter variants × scheduler mode × memory × peripherals ×
topology shape) live from the core registry and generates a pairwise covering
array — every legal value pair of every two axes appears in at least one
config, and pairs no legal config can realize are reported as blocked with
the constraint that forbids them.

Start with the free tier; it validates the entire array in seconds:

```bash
./oh-my-soc tb-matrix run --tier validate
```

Expected summary:

```text
tier validate: ran 248 ({'pass': 248}), resumed past 0 already-passing;
cumulative 248/248 pass, 68 pairs blocked
```

The render tier requires each config to survive full RTL + software
generation, and the sim tier runs the same all-hart completion gate as
Stage 5 on curated corner configs (every core as a woken worker, each fabric,
SMP, worker-only, mixed-ABI). These cost minutes per config, so bound them
and let the resume mechanism carry a campaign across sessions:

```bash
./oh-my-soc tb-matrix run --tier render --limit 10
./oh-my-soc tb-matrix run --tier sim --limit 2
./oh-my-soc tb-matrix report
```

Results accumulate in `build/tb_matrix/report.json`; a re-run skips configs
that already passed. A sim `fail` is a finding about the platform — an
untested combination that actually breaks — reproduce it directly with:

```bash
MOSAIC_CFG=build/tb_matrix/configs/<name>.yaml tb/mosaic_soc/run_generic.sh
```

## Machine-readable results

Put the global `--json` flag before the skill:

```bash
./oh-my-soc --json config-author validate tutorial/configs/tutorial_soc.yaml
```

Expected shape:

```text
{
  "ok": true,
  "skill": "config-author",
  "summary": "tutorial_soc.yaml is valid (3 cores, 4 peripherals)",
  "details": {
    "config": { ... },
    "total_cores": 3
  },
  "errors": []
}
```

For a model-backed loop, continue with [03-opencode-go.md](03-opencode-go.md).
