# MOSAIC-SoC hands-on tutorial

This tutorial takes one small heterogeneous SoC from YAML to generated RTL and
then proves that every configured hart executes. It also shows the same flow
through `oh-my-soc`, first without an LLM and then with the optional OpenCode Go
API driver.

The tutorial SoC is deliberately small:

```text
hart 0: cv32e20  TITAN  ─┐
hart 1: FazyRV   ATLAS  ─┼─ OBI fabric ─ 32 KiB SRAM + TDU + peripherals
hart 2: SERV     NANO   ─┘
```

All commands are run from the repository root. Expected-output blocks show the
stable success markers; hashes, absolute paths, cycle counts, and elapsed times
are written as `<hash>`, `<cycles>`, or `<seconds>` because they vary by machine.

Before starting any path, create and activate the project environment:

```bash
make venv
source .venv/bin/activate
```

Generation plus simulation also requires Verilator 5.x and a bare-metal
RISC-V GCC toolchain. [Chapter 1, Stage 0](01-generator.md#stage-0--prepare-the-tools)
shows the complete prerequisite check.

## Choose a path

| Goal | Start here | Typical first-run time |
|---|---|---:|
| Understand the generator directly | [01-generator.md](01-generator.md) | 5–15 minutes |
| Use the deterministic harness | [02-harness.md](02-harness.md) | 10–20 minutes |
| Configure the OpenCode Go API agent | [03-opencode-go.md](03-opencode-go.md) | 5 minutes plus model time |
| Diagnose a failure | [troubleshooting.md](troubleshooting.md) | as needed |

For the shortest verified path, run:

```bash
./tutorial/run_all.sh
```

Expected final lines:

```text
### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓
### Tutorial complete
### Topology: build/tutorial/tutorial_soc_topology.html
```

The script performs schema validation, semantic topology checks, topology
rendering, direct RTL generation, manifest inspection, and the topology-generic
full-SoC simulation. It stops at the first failed stage.

## What success means

The final `EXIT SUCCESS` proves that the generated full-SoC model booted the
TITAN, dispatched the two dormant workers through the TDU, and observed the
exact per-hart liveness sentinels. It is stronger than template rendering or a
zero simulator return code alone.

It does **not** claim physical signoff. Synthesis, place-and-route, STA, DRC, and
LVS remain separate hard gates; see [`flow/librelane/README.md`](../flow/librelane/README.md)
after completing this tutorial.

## Files used by the tutorial

- [`configs/tutorial_soc.yaml`](configs/tutorial_soc.yaml) — checked-in golden config.
- [`inspect_manifest.py`](inspect_manifest.py) — prints a readable summary of a generated build.
- [`run_all.sh`](run_all.sh) — executable local golden path.
- [`../demo/README.md`](../demo/README.md) — advanced prompt-to-SoC and new-core demos.
- [`../tb/mosaic_soc/README.md`](../tb/mosaic_soc/README.md) — full-SoC testbench details.
