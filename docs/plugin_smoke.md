# Agent-host plugin smoke tests

Measured 2026-09-24 on this machine. Each host was pointed at the harness
through a **temporary** config (never the user's real one), and checked
without starting a model session wherever the host allows it. "Connected"
below means the host itself spawned the server and completed the MCP
handshake.

| Host | Version | Config | What was checked | Result |
|---|---|---|---|---|
| Claude Code | 2.1.281 | `plugins/mosaic` (plugin) + `.claude-plugin/marketplace.json` | `claude plugin validate --strict` on the plugin and on the marketplace; `claude --plugin-dir plugins/mosaic mcp list` | both manifests pass strict validation; server `plugin:mosaic:mosaic` **✔ Connected** |
| Codex | codex-cli 0.155.1 | block from `mosaic install --host codex`, in a temp `CODEX_HOME` | `codex mcp list`, `codex mcp get mosaic` | parsed as an enabled stdio server with the command, env and the 60 s / 4 h timeouts. Codex's `list` does not start servers, so the handshake is not host-verified |
| opencode | v2.0.14 | block from `mosaic install --host opencode` | compared against the file `opencode mcp add` itself writes | identical shape (`mcp.servers.<name>`, `type: local`, `command`, `environment`). `opencode mcp list` answers "No MCP servers configured" even for the file opencode just wrote, so it cannot be used as a check; not host-verified |
| omp | 16.4.6 | committed `.omp/mcp.json` (`timeout: 0`) | omp has no MCP list command; the exact launch line from the file was run from the repo root and driven by hand | `initialize` negotiated protocol 2025-06-18; `tools/list` returned all 22 tools in plugin mode |

Beyond the hosts, the test suite drives the real server process:
`test_mcp_plugin_mode.py` (plugin mode, scope allowlist, approval tokens,
per-request sessions) and, marked slow, the chain
`session_new → soc_plan → soc_generate → config_validate → flow_run
tb-soc-generic` reaching a passing full-SoC simulation over MCP alone.
`test_a_built_wheel_actually_runs` installs the wheel into a fresh venv and
serves a checkout named by `MOSAIC_REPO`.

**Not yet verified:** a model in Codex, opencode or omp actually calling a
tool. opencode v2 runs models through a "Code Mode" tool catalog, and whether
MCP tools reach it needs a live session. Record the transcript here when one
is run.
