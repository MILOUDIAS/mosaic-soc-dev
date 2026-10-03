---
name: flow-preflight
description: Check a long flow's preconditions before it spends hours failing on one
---

# flow-preflight

Run this before `harden-classic`, `harden-chip`, or any `make mosaic-gen`.
It takes about a second and each check was bought with a wasted run.

```bash
python3 -m harness --json flow-preflight harden \
    --config flow/librelane/experimental/.generated_<design>.yaml \
    --soc-config configs/<design>.yaml \
    --tag <run-tag>

python3 -m harness --json flow-preflight regen --soc-config configs/<design>.yaml
```

`ok=false` means the flow will fail or produce something that cannot be
trusted. Warnings mean it will run and you should know something first.

## What it checks, and what each one cost

| check | blocking | the run that paid for it |
|---|---|---|
| free disk vs the stage (harden 12 GB, regen 30 GB) | yes | died at step 53 of 60 with `OSError 28`, 3 h 06 in, after routing had already reached 0 DRT and 0 antenna violations |
| the config is whole, not a fragment | yes | `run_signoff.sh` does `cat CONFIG FRAGMENT` — it does **not** merge the template. Design keys alone parse, run, and take LibreLane's defaults; `USE_SLANG` went False and the symptom was a yosys syntax error in a vendored lowrisc file |
| an RTL bundle exists for the current closure | yes | 3 × ~30 min regeneration, reported as *"no MOSAIC manifest. Generate the RTL first"* — a missing **input**, when the cause was a previous run's **output** moving the hash |
| the run tag is unused | warn | step directories from an earlier attempt survive beside the new ones; reading a stale `config.json` as the live run cost ~15 min |
| routing rules do not name synthesis nets | warn | `_NNNNN_` names do not survive a re-synthesis. Twice: `_28773_` looked like a smoking gun in the GLS work, and a `DRT_ASSIGN_NDR` on `_06890_` quietly stopped applying |

## Reading the result

- **blocking** — do not start the flow. The message names the fix.
- **warnings** — the flow will run. A stale tag is recoverable; a
  synthesis-named rule means the result may not reproduce.
- **checked** — printed even when clean, because silence is
  indistinguishable from "the check did not run".

## What it will not do

It never repairs anything. A preflight that fixes state hides the thing it
exists to surface — you would stop seeing that runs keep invalidating each
other's bundles.

It says nothing about whether the design is good. A clean preflight means the
flow will produce a result, not that you will like it.

## Habits it does not cover

- **Run `git status` after a full test suite.** A test writing to `REPO_ROOT`
  can revert a pushed commit and stay green.
- **Prove a fix cheaply first.** `librelane <cfg> --to Yosys.Synthesis` is
  ~5 min against a ~30 min regeneration for the same answer.
- **Never `pgrep -f` / `pkill -f` a pattern your own shell contains** — it
  matches itself. Use a recorded PID.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `oh-my-soc approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

No MCP tool exposes this skill yet. In a gated session (plugin or `oh-my-soc agent --driver claude|omp`) do not run it through a shell: ask the user to run the CLI below and paste the JSON back.
