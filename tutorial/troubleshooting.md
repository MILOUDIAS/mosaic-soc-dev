# Troubleshooting the tutorial

Always run tutorial commands from the repository root. When a stage fails,
fix that stage and rerun it; do not skip ahead to a later PASS marker.

## Quick failure table

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError`, missing `fusesoc`, or no `.venv` | Python environment not created or activated | Run `make venv`, then `source .venv/bin/activate`, and retry from the repo root. |
| `riscv32-unknown-elf-gcc: not found` | Bare-metal toolchain missing or at a different path | Export `RISCV_TC=/path/to/bin/riscv32-unknown-elf`; verify with `"${RISCV_TC}-gcc" --version`. |
| `verilator: command not found` | Verilator is not on `PATH` | Install/source Verilator 5.x or export `VERILATOR_PIN` for the pinned bundle. |
| `[FAIL] ... is invalid` | YAML violates the authoritative schema | Read every reported error; fix the YAML, then rerun `config-author validate`. |
| Topology check reports findings | Unsupported fabric, bank count, or semantic combination | Correct the config before RTL generation. Do not treat the diagram as approval. |
| `RTL gen failed` | Invalid config or template/generator error | Check the immediately preceding error and rerun validation first. |
| `FuseSoC setup failed` | Dependency generator or toolchain issue | Read `tb/mosaic_soc/fusesoc-setup-generic.log` or the path printed by `make mosaic-gen`. |
| `BUILD FAILED` | Verilator compile/elaboration error | Read `tb/mosaic_soc/build-generic.log`; search from the first `%Error`. |
| Simulation exits without `EXIT SUCCESS` | One or more harts did not reach the exact sentinel | Read `tb/mosaic_soc/sim-generic.log`; the flow is a failure even if the process exit code is zero. |
| `OPENCODE_API_KEY is not set` | Key was not exported in this shell | Use the hidden zsh `read -rs` sequence in `03-opencode-go.md`. |
| API setup succeeds but the request fails | Missing/expired key, subscription, quota, or model access | Check `./oh-my-soc setup show`, then verify the account in OpenCode Go. Never paste the key into logs. |
| `raw model ID` error | TUI-prefixed model name used | Use `kimi-k2.7-code`, not `opencode-go/kimi-k2.7-code`. |

## The build hash changed

This is normal. MOSAIC builds are content-addressed, so a config or source
change produces a new directory such as:

```text
build/mosaic/tutorial_soc-<new-hash>/
```

Locate the active manifest instead of hard-coding the hash:

```bash
./.venv/bin/python util/xheep_gen/build_manifest.py locate \
  --config tutorial/configs/tutorial_soc.yaml \
  --base-config configs/general.hjson \
  --pads-cfg configs/pad_cfg.py \
  --repo-root "$PWD" \
  --output-root build/mosaic
```

## Expected warnings versus failures

FuseSoC may print warnings about legacy or unknown metadata in vendored cores.
Use the hard end markers:

```text
FuseSoC setup completed successfully.
### MOSAIC manifest: .../manifest.json
```

For simulation, only this is completion evidence:

```text
### RESULT: EXIT SUCCESS — all <N> configured harts executed ✓
```

## Useful log and artifact locations

```text
build/mosaic/<soc>-<hash>/manifest.json
build/mosaic/<soc>-<hash>/generated/
build/agent/sessions/
tb/mosaic_soc/fusesoc-setup-generic.log
tb/mosaic_soc/build-generic.log
tb/mosaic_soc/sim-generic.log
```

## Reset the tutorial's generated outputs

Everything created under `build/tutorial/` and `build/mosaic/` is generated and
ignored by Git. You may remove the relevant tutorial build directories and
rerun the stages. Do not delete or edit files under `hw/vendor/` or `refs/`.

If you ran the optional deterministic one-command example, it deliberately
created `configs/tutorial_agent.yaml`; remove that file only if you do not want
to keep the generated example config.

## Still stuck?

Capture these items without secrets:

```bash
git status --short
verilator --version
"${RISCV_TC}-gcc" --version
./oh-my-soc config-author validate tutorial/configs/tutorial_soc.yaml
```

Then include the first actual error from the relevant log, not only its final
summary line. Never include `OPENCODE_API_KEY` or a plaintext provider config.
