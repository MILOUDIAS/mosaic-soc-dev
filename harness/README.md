# harness: the mosaic command-line tool

This directory holds `mosaic`, the tool that wraps every operation of the
repository behind validated commands with structured results, and offers the
same operations to AI coding agents as gated tools.

The user-facing description (commands, drivers, scopes, approvals, the MCP
server and host plugins) is in [docs/agent-harness.md](../docs/agent-harness.md).
This page is the developer's view: how to invoke it, worked examples, and where
the code is. [EVALUATION.md](EVALUATION.md) records what the runtime guarantees
and how that is tested.

## Invoking it

```bash
./mosaic <command> [args]              # launcher at the repository root
python3 -m harness <command> [args]    # module form, from the repository root
.venv/bin/python -m pip install -e .   # optional: installs the `mosaic` console script
```

`--json` before the command prints the raw result object
(`{ok, skill, summary, details, errors}`) and nothing else; the exit status is 1
on failure.

## Worked example 1: a text request to a verified SoC

```console
$ ./mosaic soc-from-prompt plan \
      "an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, tdu, a uart"
```

`plan` writes nothing. It prints how the fixed grammar read the request. Check
`details.intent.unrecognized` for words it could not place, and
`details.intent.repairs` for the changes it made: worker groups are split into
one group per hart, and each worker receives a boot address.

```console
$ ./mosaic soc-from-prompt run "<same text>" --name my_soc          # writes configs/my_soc.yaml
$ ./mosaic soc-from-prompt run "<same text>" --name my_soc --run    # generates and verifies
```

With `--run` the stages execute in order and stop at the first failure. The
result reports each stage under `details`: `config`, `topo_check`, `mosaic_gen`,
`generic_liveness`, `doc`. The liveness stage is the `tb-soc-generic` flow: it
passes only when every configured hart has reported and the log contains
`EXIT SUCCESS`.

The same steps one at a time:

```bash
./mosaic config-author generate --name my_soc \
    --core cv32e20:1:titan --core picorv32:2:atlas \
    --sram 64 --tdu --mode dynamic --peripheral uart \
    --output configs/my_soc.yaml
./mosaic topo-viz check configs/my_soc.yaml
./mosaic flow-runner run mosaic-gen-config --config configs/my_soc.yaml
./mosaic flow-runner run tb-soc-generic    --config configs/my_soc.yaml
./mosaic doc-gen config configs/my_soc.yaml
./mosaic topo-viz render configs/my_soc.yaml -o build/my_soc_topo.html
```

## Worked example 2: integrating a core

```bash
./mosaic wrapper-smith fetch https://github.com/<org>/<core>@<commit> --subdir <rtl-dir>
./mosaic wrapper-smith analyze build/wrapper_smith/fetch/<core>/<rtl-dir> --top <module> -o analysis.json
./mosaic wrapper-smith scaffold <core> --from analysis.json --vendor-from <rtl-root>          # dry run
./mosaic wrapper-smith scaffold <core> --from analysis.json --vendor-from <rtl-root> --apply
```

- `fetch` clones at the exact commit, detects the licence (a GPL-family licence
  is reported as an error to review before vendoring) and records the
  provenance.
- `analyze` parses the ports and classifies the native bus against nine
  families, with a confidence and the runner-up. Below 0.5 it reports `unknown`;
  choose `--family` yourself.
- `scaffold` stages, and with `--apply` writes, the wrapper, the registry
  entries, the `cpu_subsystem.sv.tpl` branch, the FuseSoC entries, the file-list
  visibility, the vendored tree and a bring-up configuration. After applying it
  resolves the FuseSoC graph and fails if the graph is broken. Running it again
  changes nothing.

Open `hw/sci/<core>_sci.sv` and resolve each `TODO(wrapper-smith)` marker: the
port map of the core instance, the interrupt wiring (`irq_i[3]` software,
`irq_i[7]` timer, `irq_i[11]` external) and the tie-offs. Use the existing
wrapper of the same family as the reference. Then:

```bash
./mosaic tb-smith generate <core>
./mosaic tb-smith run <core>           # TB PASS
./mosaic tb-smith wake-demo <core>     # EXIT SUCCESS
```

Common failures of the generated testbench: `reason=dormancy` means the wrapper
does not mask bus requests while parked; `reason=liveness` with no requests
means the reset polarity is wrong or the core needs its clock stalled while a
fetch is outstanding; a core that runs but never writes the sentinel usually has
a response-timing error in the wrapper.

## Worked example 3: the physical commands

```bash
./mosaic physical-intent floorplan --config configs/mosaic_blockc_4hart.yaml
./mosaic physical-intent harden    --config configs/mosaic_blockc_4hart.yaml \
    --design mosaic_block_c --output out.yaml
./mosaic physical-intent watch     --run-dir flow/librelane/experimental/runs/<tag>
./mosaic physical-intent metrics   --run-dir <run> [--compare <other>] [--record]
./mosaic physical-intent ppa       --run-dir <run> [--baseline <other>]
./mosaic physical-intent evidence  --run-dir <run>
./mosaic physical-intent ledger
```

- `floorplan` and `harden` validate the configuration, then size the die from
  the area model. Without `--utilisation` they use the densest target that a
  design of that hart count has been shown to route at.
- `metrics` prints a finished run's signoff numbers with units, corner and PDK,
  and the difference from a second run.
- `metrics --record` stores the run in `build/evidence/` under a key computed
  from everything that decides what its numbers mean: the RTL bundle, the
  hardening configuration without machine paths, the PDK, the cell library, the
  tool versions and a digest of the parsing code. `evidence --run-dir` then
  answers whether a record exists for today's inputs. Changing any input changes
  the key, so there is no separate invalidation step.
- `ppa` applies the gates and, between two accepted runs of one design, compares
  the objectives.

[docs/physical-flow.md](../docs/physical-flow.md) describes the flow these
commands serve.

## Registry single-sourcing

The lists of valid cores, buses, PDKs and peripherals are derived from
`util/mosaic_gen/core_registry.py`. Do not copy them into the harness.
`test/test_mosaic_gen/test_harness_core.py` checks that the harness and the
generator agree and that every shipped `configs/mosaic_*.yaml` validates.

## Code map

```
harness/
  __main__.py        argument parsing and command dispatch
  core.py            SkillResult, validate_config, run_cmd (streams child output,
                     enforces timeouts, kills the process group)
  agent.py           the bounded model/tool loop and the scope classifier
  agent_tools.py     the typed tool registry (22 tools)
  gates.py           gate prerequisites, shared by the loop and the MCP server
  skill_policy.py    per-skill effect, cost, scope and approval declarations
  approvals.py       time-limited approval tokens
  mcp_server.py      the MCP stdio server
  install.py         host configuration for claude, codex, opencode, omp
  events.py          session events: terminal, JSON lines, journal
  llm.py             Anthropic and OpenAI-compatible streaming adapters
  prompt_eval.py     the 26-request evaluation of text-to-configuration
  intent.py          a typed view of a validated configuration
  socview.py         one derived description of a SoC, read by the diagrams
  diagram.py         logical and chip diagrams
  web.py             the static viewer
  toolchain.py       the tool pins checked by `doctor`
  skills/            one module per command
  physical/          area model, floorplan, hardening config, routability,
                     PPA gate, line search, progress, technology store
  evidence/          metrics and report parsers, waivers, GLS and LEC verdicts,
                     the content-addressed evidence store
  templates/         wrapper family table and templates, testbench templates
```

Adding a command means: a module under `skills/`, a parser entry and dispatch in
`__main__.py`, a declaration in `skill_policy.py`, a card under
`.claude/skills/`, and tests. Expose it to agents only by adding a typed tool in
`agent_tools.py`; never by giving an agent a shell.
