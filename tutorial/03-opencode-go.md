# 3. Use the OpenCode Go API agent

This stage is optional. All correctness gates in chapters 1 and 2 work without
an API key. The API agent helps translate intent and react to tool observations;
it cannot replace schema, generation, simulation, or physical signoff gates.

Run every command from the repository root. The examples use zsh.

Prepare the project environment before invoking the harness:

```zsh
make venv
source .venv/bin/activate
```

The verified-simulation example later in this chapter also needs the Verilator
and RISC-V GCC prerequisites from
[Chapter 1, Stage 0](01-generator.md#stage-0--prepare-the-tools).

## Stage 1 — load the key without putting it in shell history

Subscribe and copy a key using the official
[OpenCode Go setup](https://opencode.ai/docs/go/), then run:

```zsh
read -rs "OPENCODE_API_KEY?OpenCode Go API key: "
printf '\n'
export OPENCODE_API_KEY
```

Confirm only that it is present—never print its value:

```zsh
[[ -n "$OPENCODE_API_KEY" ]] && echo "OPENCODE_API_KEY is set"
```

Expected:

```text
OPENCODE_API_KEY is set
```

## Stage 2 — select the built-in API driver

```bash
./oh-my-soc setup \
  --driver api \
  --api-kind opencode-go \
  --non-interactive
./oh-my-soc setup show
```

Expected key lines:

```text
[OK] driver 'api' saved to ~/.config/oh-my-soc/config.json
[OK] driver: api
```

The displayed config should contain only provider metadata:

```json
{
  "driver": "api",
  "api": {
    "kind": "opencode-go",
    "model": "kimi-k2.7-code",
    "base_url": "https://opencode.ai/zen/go/v1",
    "env_key": "OPENCODE_API_KEY"
  }
}
```

Expected detection:

```json
"opencode_key": true
```

The plaintext key is never stored. The harness reads the named environment
variable at request time and removes known/configured model credentials from
EDA, build, parser, and test subprocess environments.

An OpenCode `/connect` login is separate: the harness does not import
`~/.local/share/opencode/auth.json`.

## Stage 3 — run a read-only API-agent request

Start with an analysis-only boundary:

```bash
./oh-my-soc agent \
  "inspect tutorial/configs/tutorial_soc.yaml and explain its topology" \
  --driver api \
  --require-evidence analysis
```

Model wording varies, but the event shape should resemble:

```text
╭─ oh-my-soc agent
◇ model turn 1: choosing the next evidence step
→ request_scope {"scope":"analysis", ...}
✓ request_scope: request scope: analysis
→ config_validate {"path":"tutorial/configs/tutorial_soc.yaml"}
✓ config_validate: tutorial_soc.yaml is valid ...
→ topology_check {"path":"tutorial/configs/tutorial_soc.yaml"}
✓ topology_check: clean
╰─ completed · ...
```

What this proves: the provider can call registered typed tools and receive
observations while the explicit analysis ceiling prevents generation or other
writes.

No tutorial transcript can be byte-for-byte golden because model prose and
turn counts vary. Tool names, scope enforcement, and deterministic gate results
are the normative parts.

## Stage 4 — request a verified SoC

When you want the API agent to author and simulate a new configuration, say so
explicitly and lock the evidence level:

```bash
./oh-my-soc agent \
  "build and verify an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, a tdu and a uart" \
  --driver api \
  --require-evidence simulation
```

The successful ending must still contain deterministic evidence:

```text
✓ flow_run: Flow 'mosaic-gen-config' PASS ...
  │ ### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓
✓ flow_run: Flow 'tb-soc-generic' PASS ...
╰─ verified · ... EXIT SUCCESS ...
```

If the model stops without the all-hart gate, the request is not complete.

## Model selection rules

The preset defaults to raw API model ID `kimi-k2.7-code`. To choose another
documented OpenAI-compatible Go model:

```bash
./oh-my-soc setup \
  --driver api \
  --api-kind opencode-go \
  --model glm-5.2 \
  --non-interactive
```

Use raw IDs such as `glm-5.2`, not the OpenCode TUI form
`opencode-go/glm-5.2`. Models documented only on the Anthropic Messages
endpoint require the generic `--api-kind anthropic` configuration described in
[`harness/README.md`](../harness/README.md).

## End the shell session

```zsh
unset OPENCODE_API_KEY
```

Confirm without printing a secret:

```zsh
[[ -z "${OPENCODE_API_KEY:-}" ]] && echo "OPENCODE_API_KEY is unset"
```

Expected:

```text
OPENCODE_API_KEY is unset
```
