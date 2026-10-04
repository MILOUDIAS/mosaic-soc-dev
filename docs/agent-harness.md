# The mosaic tool and agent harness

`mosaic` is a command-line tool that wraps every operation in this repository
(authoring a configuration, generating RTL, running a testbench, hardening,
reading reports) behind commands that validate their inputs and return a
structured result. The same commands are offered to AI coding agents as typed
tools with enforced ordering, so that an agent can drive the flow without being
able to skip a check.

The design rule is that a language model may translate a request and choose the
next step, and deterministic code decides whether a step was valid and whether
it passed. A model's statement is never evidence.

## Running it

```bash
./mosaic <command> ...             # from the repository root, no install
python3 -m harness <command> ...   # the same thing as a module
.venv/bin/python -m pip install -e . && mosaic <command> ...   # console script
```

Every command prints a result with the fields `ok`, `skill`, `summary`,
`details` and `errors`. Put `--json` before the command to get that object alone
on standard output; the exit status is 1 when `ok` is false.

```bash
./mosaic --json config-author validate mosaic.yaml
```

## Commands

Each command is a *skill*: a deterministic Python module under
`harness/skills/` or `harness/physical/`.

| Command | Purpose |
|---|---|
| `config-author` | `generate`, `validate`, `presets`, `wake-demo <core>`, `eval`: write and check configurations |
| `soc-from-prompt` | `plan` and `run`: turn a text request into a configuration, and with `--run` generate and simulate it |
| `topo-viz` | `check` (semantic checks on a configuration) and `render` (logical and chip diagrams as HTML or SVG) |
| `flow-runner` | `list` and `run <flow>`: the 21 registered flows, with timeouts and pass markers |
| `tb-smith` | `generate`, `run`, `wake-demo`: the testbench for one wrapped core |
| `tb-matrix` | `axes`, `plan`, `run`, `report`: coverage of the configuration space |
| `wrapper-smith` | `families`, `fetch`, `analyze`, `scaffold`: integrate a new core |
| `physical-intent` | `floorplan`, `harden`, `watch`, `metrics`, `ppa`, `ledger`, `power`, `screen`, `optimize`, `netlist-diff`, `evidence`: the physical flow's models, gates and records |
| `flow-preflight` | check the preconditions of a long run |
| `drc-triage` | `analyze` and `scan`: classify DRC and LVS reports |
| `gls-triage` | state what a gate-level simulation verdict supports |
| `netlist-diff` | compare the netlists of two runs |
| `waiver-author` | audit the signoff waivers against their evidence |
| `pdk-port` | list what a technology must supply before the tools can size for it |
| `doc-gen` | `config`, `memory-map`: Markdown summaries; `dashboard --file <path>` summarises a status file you supply |
| `web` | `build` and `serve`: a read-only viewer of configurations, runs and waivers, on `127.0.0.1:8765` |
| `doctor` | check this machine's tools against the pinned versions |
| `setup` | choose the driver (below) |
| `agent` | run a request through the chosen driver |
| `mcp-server` | serve the gated tools to an agent host |
| `approve` | grant or revoke a time-limited approval |
| `install` | print or write the configuration that connects an agent host |

Fifteen skill cards in `.claude/skills/<name>/SKILL.md` describe when to use a
command, its exact invocation, its output and what to do on each failure. They
are written for agents and are also the shortest human reference.

## From a text request to a verified SoC

```bash
./mosaic soc-from-prompt plan "one cv32e20 controller, two picorv32 workers, 64KB sram, tdu, a uart"
./mosaic soc-from-prompt run  "<the same text>" --name my_soc          # writes configs/my_soc.yaml
./mosaic soc-from-prompt run  "<the same text>" --name my_soc --run    # generates and simulates
```

`plan` uses a fixed grammar, not a language model. It reports what it
recognised, what it could not place, and every repair it made (for example,
assigning boot addresses to workers). `--run` executes the gates in order and
stops at the first failure: configuration validation, the topology check, RTL
generation, and the all-hart liveness simulation that must reach `EXIT SUCCESS`.

The optional `--llm` flag asks a language model to do the translation step. Its
output goes through the same repair and validation code.
`./mosaic config-author eval` scores a translator against 26 fixed requests; the
built-in grammar scores 26 of 26.

## Drivers

`mosaic agent "<request>"` hands a request to one of four drivers, chosen with
`mosaic setup` or `--driver`:

| Driver | Who decides the next step | Where the gates are enforced |
|---|---|---|
| `deterministic` | a fixed workflow, no model; the default | in process |
| `api` | a built-in loop that calls an Anthropic or OpenAI-compatible model with typed tools | in process |
| `claude` | Claude Code, in its own interface | the mosaic MCP server; shell and file-editing tools are disallowed for the session |
| `omp` | oh-my-pi, in its own interface | the mosaic MCP server from `.omp/mcp.json` |

`mosaic setup` writes `~/.config/mosaic/config.json`. It stores the name of the
environment variable that holds an API key, never the key. No agent tool can run
`setup`, because the driver choice decides where enforcement happens.

Useful flags of `mosaic agent`: `--dry-run` (plan only, every write and
execution denied), `--require-evidence <scope>`, `--events-jsonl` (one event per
line), `--max-turns`, `--allow-physical`, `--allow-integration`.

Each session writes an append-only journal under `build/agent/sessions/`,
readable only by its owner. It contains the request and the tool output.

## Scope and approval

A *scope* names the furthest kind of action a request may take. The scopes are
`analysis`, `config`, `documentation`, `drc`, `rtl`, `simulation`, `testbench`,
`integration` and `physical`.

1. **Ceiling.** Before anything runs, the harness derives a ceiling from the
   words of the request (`harness/agent.py`). "Does this configuration
   validate?" is `analysis`; "build and simulate" is `simulation`. An ambiguous
   request is `analysis`. `--require-evidence` sets the ceiling explicitly.
2. **Confirmation.** The model's first tool call must be `request_scope` with a
   scope inside the ceiling. It cannot widen it.
3. **Ordering.** Gates in `harness/gates.py` enforce prerequisites: RTL
   generation for a configuration is refused until the topology check has passed
   for that file, a full-SoC simulation is refused until generation has passed,
   and a build request cannot be reported complete until the all-hart
   simulation has passed.
4. **Evidence binding.** A passed gate is recorded with the path and SHA-256 of
   the configuration and a digest of the sources. Editing the file or the
   sources afterwards invalidates the evidence.
5. **Bounds.** Unknown tools and malformed arguments are returned to the model
   as errors and never reach a shell. Turns and repeated calls are capped.
6. **Approval.** Actions that cost hours or write lasting evidence need a
   person. In process this is `--allow-physical` or `--allow-integration`. Over
   MCP it is a token created by:

   ```bash
   ./mosaic approve physical --minutes 60
   ./mosaic approve physical --revoke
   ```

   The command refuses to run unless both its input and output are a terminal,
   so an agent's shell cannot grant its own approval. A token is needed for the
   `physical` and `integration` scopes, for flows that declare approval
   (`harden-classic`, `harden-chip`, `lec`), for a `tb-matrix` run above the
   `validate` tier, and for applying a scaffolded wrapper.

## The MCP server

The Model Context Protocol (MCP) is the interface through which an agent host
calls external tools. `mosaic mcp-server` serves the harness's tools over
standard input and output. The server calls the same gate functions as the
in-process loop, so a refusal reads the same either way.

It has two modes:

- **Locked.** `mosaic mcp-server --request "<text>"` derives the ceiling from
  that request and fixes it before any client connects. `mosaic agent --driver
  claude` and `--driver omp` start the server this way.
- **Plugin.** Without `--request`, the server starts before any request exists.
  The ceiling is then an allowlist that the user writes into the host's
  configuration as `MOSAIC_SCOPES`. The default allowlist is every scope except
  `integration` and `physical`. Each request begins with the `session_new` tool,
  which binds the user's words to a new session.

The server offers 22 tools:

| Group | Tools |
|---|---|
| Session | `session_new`, `session_status`, `request_scope` |
| Configuration | `soc_plan`, `soc_generate`, `config_generate`, `config_validate` |
| Topology | `topology_check`, `topology_render` |
| Flows | `flow_list`, `flow_run` |
| Testbenches | `tb_generate`, `tb_run`, `tb_wake_demo`, `tb_matrix_plan`, `tb_matrix_run` |
| Core integration | `wrapper_analyze`, `wrapper_scaffold` |
| Reports | `drc_analyze`, `drc_scan`, `doc_config`, `doc_dashboard` |

`flow-preflight`, `gls-triage`, `netlist-diff`, `waiver-author` and `pdk-port`
have no tool yet; in a gated session the agent asks the user to run them.

If `naja-scope-mcp` is installed, a `--driver claude` session also receives its
read-only design-query tools. Their answers are facts about the design and are
never counted as gate evidence.

## Host plugins

`mosaic install --host <claude|codex|opencode|omp>` prints the configuration
block for that host; `--write` merges it into the host's configuration file
after making a timestamped backup, and `--scopes` sets the allowlist.

| Host | How it connects |
|---|---|
| Claude Code | the plugin in `plugins/mosaic/` (listed in `.claude-plugin/marketplace.json`). It starts the server and installs a pre-tool hook that denies shell and edit calls which would bypass the gates. |
| Codex | a `[mcp_servers.mosaic]` block in its `config.toml` |
| opencode | an `mcp` entry of type `local` in its configuration |
| oh-my-pi (omp) | the committed `.omp/mcp.json` |

The server finds the repository through the `MOSAIC_REPO` environment variable
when it is not started from the checkout.

### Checked on 2026-09-24

Each host was pointed at the harness through a temporary configuration and
checked without starting a model session where the host allows it.

| Host | Version | Check | Result |
|---|---|---|---|
| Claude Code | 2.1.281 | `claude plugin validate --strict` on the plugin and the marketplace; `claude --plugin-dir plugins/mosaic mcp list` | both manifests valid; server connected |
| Codex | codex-cli 0.155.1 | `codex mcp list`, `codex mcp get mosaic` | parsed as an enabled server with its command, environment and timeouts. Codex does not start servers for these commands, so the handshake is not verified by the host. |
| opencode | 2.0.14 | compared with the file that `opencode mcp add` writes | same shape. `opencode mcp list` cannot be used as a check; not verified by the host. |
| omp | 16.4.6 | the launch line from `.omp/mcp.json`, driven by hand | protocol negotiated; `tools/list` returned all 22 tools |

The test suite also drives the real server process
(`test/test_mosaic_gen/test_mcp_plugin_mode.py`): the allowlist, approval
tokens, per-request sessions, and a slow test that goes from `session_new`
through planning, generation and validation to a passing full-SoC simulation
using MCP calls only.

Not yet verified: a model inside Codex, opencode or omp calling a tool in a live
session.

## Checking the environment

```bash
./mosaic doctor
```

reports each required tool, its version against the pin, and where each PDK is
installed. See [reproducing.md](reproducing.md).
