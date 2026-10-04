---
name: setup-wizard
description: Choose which driver owns the planning and tool loop, and where the API key comes from. Deliberately CLI-only — no agent can invoke it. Read this before wiring it into an agent surface.
---

# setup-wizard — CLI-only, and that is the design

```bash
python3 -m harness setup                 # interactive, first run
python3 -m harness setup --driver api
```

Writes one file, outside the repository:

```text
~/.config/mosaic/config.json
```

Nothing else. No repository state, no secrets: an API key is read from a named
environment variable at call time and never stored.

## Why no agent can run it

Whether an agent may re-run environment setup is a real gating question, not
an oversight. The answer is that it cannot, and this card is the record of why
that should stay true.

The file it writes selects the **driver that owns the agent loop**:

| driver | where the gates live |
|---|---|
| `deterministic` | in-process — sets `required_scope`, no model, CI-safe |
| `api` | in-process — sets `required_scope` **and locks it** |
| `claude` | the mosaic MCP server the session is started with, plus the external tool's own permission model |
| `omp` | the mosaic MCP server from `.omp/mcp.json`, plus the external tool's own permission model |

`harness/agent.py` branches on that value. The first two enforce scope in
Python; the other two delegate enforcement outward. So a component able to
rewrite this file can move gate enforcement out of the process that performs
the gating.

An agent that could do that could widen its own authority, which is a different
class of act from anything else in `harness/skills/`. Hence: reachable from the
CLI, absent from `agent_tools.py`, `mcp_server.py` and `agent.py`.

Verified, not assumed — the string appears zero times in all three, and a test
asserts it, so exposing it later means confronting this question rather than
walking past it.

## Its policy declaration

`effect: write`, `cost: seconds`, `scopes: [config]`, no approval.

Under `harness/skill_policy` that is correct for what it writes: a user-level
preference file is not evidence, and it costs a second. The rule is about what a
skill does when run; what keeps this one safe is that **nothing but a person can
run it**. Those are separate protections and the card exists so the second is
not mistaken for an accident.

## If you do want to expose it

Then the approval question is live, and the honest shape is probably not a
skill at all: split the read (report the current driver) from the write (change
it), expose only the read, and leave the write to a person at a terminal.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `mosaic approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

No MCP tool exposes this skill yet. In a gated session (plugin or `mosaic agent --driver claude|omp`) do not run it through a shell: ask the user to run the CLI command shown above and paste the JSON back.
