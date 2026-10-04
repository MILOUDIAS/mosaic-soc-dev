# MOSAIC demos

Three scripts that exercise the harness end to end. The harness is the
`./mosaic` command at the repository root (equivalent to `python3 -m harness`;
see [`harness/README.md`](../harness/README.md)). It exposes each generator and
verification step as a typed tool that validates its input, runs the real
check, and returns a structured result.

Each script changes to the repository root itself, so it can be started from
any directory. Demos 1 and 2 run Verilator simulations. They need the toolchain
from `nix develop .#sim` (Verilator 5.050 and a bare-metal RISC-V GCC) and the
Python environment from `make venv`; see
[`tutorial/01-generator.md`](../tutorial/01-generator.md#stage-0--prepare-the-tools).

| Script | What it shows | Runs a simulation |
|---|---|---|
| `01_soc_from_prompt.sh` | One English sentence becomes a simulated SoC, with no model involved | yes |
| `02_wrap_new_core.sh` | The steps that integrated the Hazard3 core, replayed on the committed tree | yes |
| `03_blocka_from_prompt.sh` | One English sentence reproduces the Block A configuration, and two false claims are refused | no |

## How an agent reaches the tools

An agent program (Claude Code, or [oh-my-pi](https://github.com/can1357/oh-my-pi),
whose command is `omp`) does not edit YAML or call `make`. It reaches the same
typed tools in one of two ways:

- **Skill cards.** Each directory under [`.claude/skills/`](../.claude/skills/)
  holds a `SKILL.md` that tells the agent which `./mosaic` command to call for
  a task.
- **MCP server.** MCP (Model Context Protocol) is the interface an agent
  program uses to call external tools. `./mosaic mcp-server` serves the gated
  tool set over it. [`.omp/mcp.json`](../.omp/mcp.json) registers that server
  for oh-my-pi through `plugins/mosaic/launch_mcp.py`.

In both cases the agent only chooses arguments. Schema validation, semantic
topology checks, RTL generation, and the simulation verdict are decided by the
deterministic tools.

## 1. Prompt to verified SoC

Without a model:

```bash
./demo/01_soc_from_prompt.sh
```

The script passes one sentence to `./mosaic agent --driver deterministic`. A
fixed grammar parses the sentence, the config author writes
`configs/prompted_demo.yaml`, and the ordered gates run: topology check, RTL
generation, then the full-SoC simulation in which every configured hart must
report in. The run passes only if the simulation prints `EXIT SUCCESS`. The
grammar reports what it matched, what it did not recognize, and every repair
it applied.

With Claude Code, open the repository and ask, for example, *"build me an SoC
with one cv32e20 controller, two picorv32 workers, 64KB sram, a tdu and a
uart"*. The `soc-from-prompt` skill card routes the agent through the same
gates.

With oh-my-pi, from an interactive terminal:

```bash
./mosaic setup --driver omp
./mosaic agent "an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, tdu, a uart"
```

`./mosaic agent` starts `omp` with its built-in tools disabled, so the MCP
server from `.omp/mcp.json` is the only way it can act on the repository.

## 2. Wrap a new core

```bash
./demo/02_wrap_new_core.sh
```

Hazard3 is a RISC-V core with an AHB-Lite bus. Its RTL is vendored under
`hw/vendor/mosaic/hazard3/` and its wrapper is `hw/sci/hazard3_sci.sv`. The
script replays the deterministic steps of that integration against the
committed tree:

1. **analyze.** `wrapper-smith analyze` parses the top module's ports and
   classifies the bus. For `hazard3_cpu_2port` it reports family `ahb_split`
   at confidence 1.00 over 63 ports.
2. **scaffold.** `wrapper-smith scaffold` stages the wrapper and every
   integration edit. Because the integration is already committed, every item
   is reported as already present and nothing is written.
3. **single-hart testbench.** `tb-smith generate` and `tb-smith run` build and
   run a testbench that checks that the wrapped core stays dormant, wakes,
   executes, and writes its sentinel.
4. **full-SoC wake demo.** `tb-smith wake-demo` builds an SoC with the core as
   a worker and requires `EXIT SUCCESS`.

The part of the integration that was written by hand is the port map inside
`hw/sci/hazard3_sci.sv`: `irq_i[3]`, `irq_i[7]` and `irq_i[11]` drive the
software, timer and external interrupt inputs, and the boot address is passed
as the `RESET_VECTOR` and `MTVEC_INIT` parameters.

For a core that is not in the tree yet, the sequence is:

```bash
./mosaic wrapper-smith fetch https://github.com/Wren6991/Hazard3@8af99293 --subdir hdl
#   pinned clone; the licence is detected and GPL-family licences are flagged
./mosaic wrapper-smith analyze <rtl_root> --top hazard3_cpu_2port -o a.json
./mosaic wrapper-smith scaffold hazard3 --from a.json --vendor-from <rtl_root> --apply
#   wrapper, registries, template branch, FuseSoC core file, bring-up config
# ... fill the TODO(wrapper-smith) markers in the wrapper ...
./mosaic tb-smith generate hazard3 && ./mosaic tb-smith run hazard3
./mosaic tb-smith wake-demo hazard3     # EXIT SUCCESS = done
```

To see what the scaffold produces without network access, regenerate the
picorv32 wrapper under a different name and compare it with the shipped one.
The difference is the generated header and one `TODO` comment:

```bash
./mosaic wrapper-smith analyze hw/vendor/mosaic/picorv32/picorv32.v --top picorv32 -o /tmp/a.json
./mosaic wrapper-smith scaffold pico2 --from /tmp/a.json    # dry run, staged under build/
diff <(sed 's/pico2/picorv32/g' build/wrapper_smith/pico2/stage/hw/sci/pico2_sci.sv) hw/sci/picorv32_sci.sv
```

## 3. Block A from one prompt

```bash
./demo/03_blocka_from_prompt.sh
```

Block A is the GF180MCU reference design. Its configuration is
`configs/mosaic_tapeout_ultra.yaml`. This demo runs no simulation and has two
separate halves:

| Steps | Uses a model | What they check |
|---|---|---|
| 2 to 5 | no | The fixed grammar reproduces `configs/mosaic_tapeout_ultra.yaml` field for field. The same prompt with `no debug` removed is refused, and so is a tapeout request for a simulation-only core. These steps decide the exit status. |
| 6 | yes | Claude Code, if `claude` is on `PATH`, receives the same request and must reach the same configuration through typed `config-author generate` flags. It is told not to use the grammar. |

Step 6 is reported and never changes the exit status. If the model's
configuration differs, the differing fields are printed.

```bash
MOSAIC_DEMO_AGENT=off ./demo/03_blocka_from_prompt.sh   # skip step 6
```

Step 6 gives the model a shell restricted to `python3 -m harness` commands. It
measures whether a model can translate a request into correct typed flags. It
is not the gated session: `./mosaic agent --driver claude` removes the shell
and leaves only the MCP tools.
