# oh-my-soc agentic-harness evaluation

Date: 2026-07-13

## Original verdict

The previous harness was an agent-facing deterministic skill library, not an
agent runtime:

- `agent` routed every request to `soc-from-prompt`.
- The API backend made one non-streaming JSON translation request and could
  not choose tools or react to results.
- EDA subprocesses used `capture_output=True`, leaving the terminal silent
  until completion.
- omp and Claude were forced into final-only print modes; the omp startup
  `/skill:` string was ordinary prompt text rather than an expanded skill.
- There was no event contract, session journal, recovery loop, repeated-call
  limit, gate prerequisite enforcement, or side-effect approval tier.

## Implemented runtime contract

`harness/agent.py` now implements the bounded loop:

```text
provider text/tool fragments
        -> normalized event stream
        -> validated typed tool call
        -> deterministic SkillResult observation
        -> provider chooses/revises next action
        -> evidence-backed final response
```

The model can select config, topology, flow, documentation, DRC, wrapper, and
testbench tools. Unknown tools and malformed arguments become error
observations; they never reach a shell. Calls and turns are bounded, duplicate
calls are guarded, and a build request cannot finish until the full-SoC
verification evidence exists.

Before the API loop, a conservative policy classifier derives an outcome
ceiling from the user's text, including explicit negation. The model must
confirm that exact value in a typed `request_scope` call and cannot widen it.
`--require-evidence` provides an explicit override. A tool-specific policy
matrix separates config, RTL, simulation, physical, integration, testbench,
documentation, and DRC operations. Gate evidence stores the canonical config
path plus SHA-256; overwriting or deleting that path invalidates downstream
evidence. Explicit inspection and execution-negation language dominates action
nouns (for example, asking whether a simulation passes while saying not to run
it remains analysis-only). Analysis completion must come from a request-relevant
tool; listing unrelated flows is not accepted as evidence.

Wrapper inspection can run in memory without creating an analysis artifact.
An actual integration request completes only after applied wrapper files are
still fingerprint-current and the post-apply FuseSoC smoke passed; an explicit
scaffold/stage request may stop at a current staged tree. Testbench requests
similarly distinguish generation from execution, and completion is invalidated
when the recorded testbench inputs change. RTL, simulation, integration,
testbench, and physical evidence is also bound to the requested config/core and
the same complete source-closure digest used by the MOSAIC build manifest; a
pass for another target or a later RTL, firmware, flow, or generator edit does
not count. Natural-language SoC requests also bind completion to the exact
`soc_plan` interpretation of core type, count, and role. Existing-config and
existing-testbench verification preserves the initial artifact digest and does
not authorize regeneration; creating or changing those sources requires
explicit write intent in the user's request.

The deterministic prompt contract covers negative peripheral intent, boot-ROM
size, explicit TDU disablement, nearby ISA declarations, and per-FazyRV-group
chunk sizes. Architecturally impossible requests such as worker harts with an
explicitly disabled TDU fail at planning instead of being silently repaired to
the opposite design.

Gate prerequisites are executable policy, not prompt prose:

- `mosaic-gen-config` is blocked until `topology_check` passes for that config.
- `tb-soc-*` is blocked until `mosaic-gen-config` passes.
- physical flows require `--allow-physical`.
- applying wrapper integration requires `--allow-integration`.
- `--dry-run` denies all write and execute tools.

The `deterministic` driver remains available, but is labelled as a visible
fixed workflow rather than an LLM. The `api` driver is the in-process agent
loop. TTY `omp` and `claude` runs now hand over to their real interactive UIs;
omp is no longer launched with `--print` or a nonfunctional startup `/skill:`
command.

## Observable terminal contract

`harness/events.py` defines append-only, monotonically sequenced events for
session, plan, model text, decision, tool start/progress/end, gate, recovery,
error, and final status. The same events have three consumers:

- compact live terminal rendering with ANSI only on TTYs;
- `--events-jsonl` for machine-readable live sessions;
- an append-only journal under `build/agent/sessions/`.

`harness.core.run_cmd()` streams child output while retaining a bounded parser
tail. It concurrently drains merged stdout/stderr, enforces timeouts, starts a
new process group, and terminates descendants on timeout/cancellation.
`--json --progress-jsonl` preserves the one-object stdout contract while
streaming progress JSONL on stderr.

The omp custom tool now uses incremental `Bun.spawn` reads and repeated
`onUpdate` calls, allowing oh-my-pi's native tool card/spinner to display EDA
progress before completion.

Normalized JSONL and Python approval flags are intentionally limited to the
in-process drivers. Claude/omp are interactive-only native TTY handoffs; they
use their own permission model, and unsupported headless/policy flags fail
closed instead of implying enforcement that the wrapper cannot provide.

## Provider protocol

`harness/llm.py` retains the legacy intent-translation helper for explicit
`soc-from-prompt --llm` compatibility, and adds real agent adapters:

- OpenAI-compatible SSE text and fragmented function-call arguments;
- Anthropic SSE text, `tool_use`, and fragmented `input_json_delta` blocks;
- provider-neutral events returned to the agent loop.

API keys remain environment-only. New and loaded user configs are repaired to
mode `0600`; session journals are also `0600`, with a `0700` directory. Journals
contain prompts and tool output (not provider request envelopes), so users must
apply an appropriate retention policy. The in-memory event tail is bounded.
Agent-created outputs are restricted to purpose-specific `build/`, `configs/`,
or `docs/` roots. Read-only wrapper analysis does not launch external parsers;
when approved, the Yosys fallback copies untrusted source filenames to generated
safe names and invokes a script rather than interpolating paths into `-p`.
Persisted wrapper analyses fingerprint their HDL input tree, and staged wrapper
completion links back to that still-current analysis as well as the repository
source digest.
Applied wrapper integration additionally requires matching vendored RTL and
FuseSoC dependency closure, no pending infrastructure TODO, a passing FuseSoC
setup smoke, a canonical generated unit test with a current pass, and current
`tb-soc-generic` full-SoC evidence for that core. Analysis/apply,
test-generation/test-run, and staged/applied evidence are recorded as separate
operation snapshots; a later analysis, dry scaffold, or test run cannot refresh
an older apply smoke or generated test. Staging alone is reported only as
staging.

The completion flow is topology-driven rather than tied to the historical
three-hart wake-demo shape. `tb-soc-generic` consumes generated
`boot_images.json`, builds one ABI-correct RV32E, RV32, or RV64 liveness image
for every boot slot, dispatches a unique descriptor to each dormant worker,
and withholds `EXIT SUCCESS` until every configured hart has written its
sentinel. It fails closed when a legal testbench-only topology lacks the
platform windows required for a primary hart to terminate.

## Verification scope and remaining environmental limits

The regression suite exercises multi-turn tool/result correlation, gate
bypass prevention, bounded recovery, premature-final rejection, duplicate and
unknown-tool guards, OpenAI/Anthropic fragmented SSE calls, live child output,
timeout descendant cleanup, TTY/plain rendering, JSON/JSONL contracts,
external omp command selection, approval gates, setup permissions, and the
incremental omp bridge.

Claude and omp executables are installed, but their native interactive TUIs and
a paid remote-provider session were not automated in this non-user-interactive
validation. Their command construction and adapters are checked with scripted
streams, wire-format fixtures, and the vendored oh-my-pi contracts. The local
deterministic acceptance run did exercise the real generation and full-SoC
simulation: generation passed in 43.5 s, `tb-soc-wake` emitted `EXIT SUCCESS`
and passed in 82.1 s, and the journal ended `verified` in about 126 s.

The topology-generic gate was separately exercised live against materially
different configurations: the shipped cv32e20 + FazyRV + SERV TDU system
reported all 3 harts before `EXIT SUCCESS`, and a temporary single-cv32e20,
TDU-disabled system reported its sole hart before `EXIT SUCCESS`. The mixed
RV32/RV64 Rocket-worker topology reported all 3 harts, and the shared-image,
mixed-ABI cv32e20 + cv32e40x SMP topology reported all 4 harts. The latter
selects the common RV32E instruction/ABI subset while retaining per-hart
identity through `mhartid`.
