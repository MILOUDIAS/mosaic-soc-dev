# Harness runtime: guarantees and how they are tested

This page states what the agent runtime in `harness/` guarantees, where each
guarantee is implemented, and what the tests do and do not cover. For how to use
the tool, see [docs/agent-harness.md](../docs/agent-harness.md).

## The loop

`harness/agent.py` implements a bounded loop:

```text
model output (text and tool-call fragments)
    -> normalized event stream
    -> validated typed tool call
    -> deterministic skill result, returned to the model as an observation
    -> the model chooses or revises the next action
    -> a final response that the gates accept as complete
```

The model selects among the tools in `harness/agent_tools.py`. A tool name that
does not exist, or arguments that do not match the tool's schema, become an
error observation. Nothing the model writes is executed by a shell.

## Guarantees

**Scope ceiling.** Before the loop starts, `classify_request_scope` derives the
furthest outcome the user's words permit, taking explicit negation into account:
asking whether a simulation passes while saying not to run it is `analysis`. An
ambiguous request is `analysis`. The model must confirm the scope with a
`request_scope` call and cannot widen it. `--require-evidence` overrides the
derivation.

**Gate order.** Prerequisites are executable policy in `harness/gates.py`, not
instructions in a prompt:

- `mosaic-gen-config` is refused until `topology_check` has passed for that
  configuration;
- the `tb-soc-*` flows are refused until `mosaic-gen-config` has passed;
- physical flows need `--allow-physical`, and applying a scaffolded wrapper
  needs `--allow-integration` (over MCP, an approval token instead);
- `--dry-run` denies every tool that writes or executes.

**Evidence binding.** A gate result is stored with the canonical path and
SHA-256 of the configuration and with the digest of the generator sources that
the build manifest uses. Overwriting or deleting the file, or editing RTL,
firmware, flow or generator sources, invalidates the evidence that depended on
them. A pass for another configuration or core does not count.

**Completion.** A request to build a SoC cannot finish until the all-hart
full-SoC simulation (`tb-soc-generic`) has passed for the configuration the
plan produced, with the planned core types, counts and roles. An analysis
request must be completed by a tool relevant to it; listing unrelated flows is
not accepted. Verifying an existing configuration or testbench never authorises
regenerating it.

**Wrapper integration.** Analysis, staging and applying are recorded as separate
operations. An applied integration is complete only when the applied files still
match their recorded fingerprints, the FuseSoC dependency graph resolved after
the apply, the generated unit testbench has a current pass, and a current
`tb-soc-generic` pass exists for that core. Staging alone is reported as
staging.

**Bounds.** Turns and tool calls are capped and repeated identical calls are
refused.

**Child processes.** `harness.core.run_cmd` streams a child's output as it runs,
keeps a bounded tail for parsing, enforces the timeout, and starts the child in
its own process group so that a timeout or cancellation also stops the
simulator processes it spawned.

**Events.** `harness/events.py` defines append-only events with increasing
sequence numbers for session, plan, model text, decision, tool start, progress
and end, gate, recovery, error and final status. They have three consumers: the
terminal renderer (ANSI styling only on a terminal), `--events-jsonl`, and a
journal under `build/agent/sessions/`.

**Secrets and files.** API keys are read from the environment at call time and
are never stored. The user configuration and the session journals are created
with mode `0600`. Journals contain the request and tool output, so their
retention is the user's responsibility. Outputs created by an agent are confined
to purpose-specific directories under `build/`, `configs/` or `docs/`.

**External drivers.** `--driver claude` and `--driver omp` hand the terminal to
those programs and connect them to the MCP server (`harness/mcp_server.py`),
which holds one session state and calls the same gate functions. For Claude Code
the session is started with only the harness's MCP tools allowed and its shell
and file-editing tools disallowed, because a shell would reach the same flows
without the gates.

## The all-hart completion flow

`tb-soc-generic` is driven by the topology rather than a fixed design. It reads
the generated `boot_images.json`, builds one RV32E, RV32 or RV64 liveness image
per boot slot, dispatches a distinct descriptor to each dormant worker, and
prints `EXIT SUCCESS` only after every configured hart has written its sentinel.
It fails when a testbench-only topology lacks the address windows a primary hart
needs to terminate.

## The text-to-configuration evaluation

`harness/prompt_eval.py` holds 26 fixed requests with expected outcomes and
scores a translator by code.

```bash
./mosaic config-author eval                    # the built-in grammar: 26 of 26
./mosaic config-author eval --print-prompts    # the prompts, to run any model by hand
./mosaic config-author eval --answers <file>   # score recorded answers
./mosaic config-author eval --provider <kind> --model <id>   # drive a provider
```

The deterministic grammar handles negative peripheral intent, boot ROM size,
explicit TDU disablement, instruction-set declarations near a core name, and a
chunk size per FazyRV group. A request that is architecturally impossible, such
as worker harts with the TDU explicitly disabled, fails at planning instead of
being repaired into a different design.

## What the tests cover

`test/test_mosaic_gen/` exercises: multi-turn correlation of tool calls and
results, prevention of gate bypass, bounded recovery, rejection of a premature
final answer, duplicate and unknown tool guards, fragmented streaming tool calls
in both provider formats, live child output, cleanup of child processes on
timeout, terminal and plain rendering, the JSON and JSON-lines contracts,
external driver command construction, approval gates, configuration file
permissions, and the MCP server in locked and plugin modes.

## What they do not cover

- The interactive interfaces of Claude Code and oh-my-pi are not automated.
  Their command lines and adapters are checked with scripted streams and
  recorded wire formats.
- No test uses a paid remote model.
- A live model session in Codex, opencode or oh-my-pi calling an MCP tool has
  not been recorded.
