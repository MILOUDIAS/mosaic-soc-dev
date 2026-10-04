# MOSAIC-SoC

MOSAIC-SoC is a generator that reads one YAML file and writes a heterogeneous
multi-core RISC-V system on chip (SoC): its RTL, the linker scripts and boot
images for its software, a testbench, and the inputs of a GF180MCU physical
design flow.

It is a fork of [X-HEEP](https://github.com/x-heep/x-heep), a single-core
microcontroller platform, extended to several cores of different kinds on one
bus.

## What it does

You describe the SoC in a configuration file: which cores, how many of each,
the memory, the bus fabric, the dispatch service and the peripherals. The
generator validates the file, refuses it if it breaks a rule, and otherwise
produces a design that can be simulated and, for a small family of designs,
hardened into a layout.

This is `configs/mosaic_wake_demo.yaml` with its comments removed. It describes
three cores of three different kinds:

```yaml
soc:
  name: mosaic_wake_demo
  pdk: gf180mcu
  cores:
    - ip: cv32e20
      isa: rv32emc
      count: 1
      role: titan
    - ip: fazyrv
      isa: rv32i
      chunksize: 8
      count: 1
      role: atlas
      boot_addr: 0x1000
    - ip: serv
      isa: rv32i
      count: 1
      role: nano
      boot_addr: 0x2000
  memory:
    sram_kb: 32
    boot_rom_kb: 2
  dma: idma
  bus: obi
  scheduler:
    tdu: true
    mode: dynamic
  peripherals:
    - uart
    - gpio
    - timer
    - spi
```

A *hart* is one hardware thread; every core here has exactly one. The `role`
says how a hart leaves reset. The `titan` is the orchestrator: it runs from
reset and boots from the boot ROM. `atlas` and `nano` harts are workers: they
stay dormant until the orchestrator wakes them.

Everything generated goes into one directory, called a bundle:

```
build/mosaic/<name>-<hash>/
    manifest.json            the inputs this bundle was generated from
    generated/hw/            the rendered RTL
    generated/sw/            linker scripts, start-up code, memory-map headers,
                             and boot_images.json (which image each hart runs)
    generated/tb/            the testbench
```

The hash covers the configuration and the contents of the source directories,
so the directory name identifies its inputs and two configurations never
overwrite each other. `build/` is not version controlled.

## Status at a glance

Every design the generator can produce is at one of three levels. No
fabricated silicon is claimed at any level: the strongest result in this
repository is a layout that passes the signoff checks.

| Level | Designs | Evidence |
|---|---|---|
| Signoff-clean on GF180MCU | Block A, the GF180MCU reference design: two SERV harts, no on-chip SRAM pool, code executed in place from external flash (`configs/mosaic_tapeout_ultra.yaml`) | Run `blocka_d15_rstsync`: a 1.2321 mm2 die at 82.9% utilisation, with design-rule, layout-versus-schematic, XOR, antenna and power-grid checks at zero and positive setup and hold slack on a 50 ns clock. Two waivers, recorded on 2026-09-24 and due for review on 2026-11-30. |
| Hardened but not tapeout-qualified | Block B (three SERV harts, `configs/mosaic_blockb_3hart.yaml`) and Block C (four SERV harts, `configs/mosaic_blockc_4hart.yaml`) | Runs `blockb_sdc`, `blockb_m45`, `blockc_sdc` and `blockc_m45` pass the hard checks and timing. They report fan-out violations that no waiver accepts, and `target: tapeout` refuses both designs. |
| RTL and simulation only | Everything else: every core other than SERV, the `log` and `floonoc` bus fabrics, any design with an on-chip SRAM pool, external RAM, the DMA, the debug module or the interrupt controller, and any design on `pdk: sky130` or `pdk: ihp-sg13g2` | Verilator simulation of the complete SoC. No layout. |

Layout files are not in version control. The repository tracks only the
`final/metrics.json` of the runs it cites. [docs/status.md](docs/status.md)
lists what is proven, how each claim is checked, and the known defects.
Status as of 2026-10-03.

## Supported cores

Fourteen cores are registered in `util/mosaic_gen/core_registry.py`. Cores that
do not speak OBI (Open Bus Interface, the bus X-HEEP uses) are wrapped by a
Standard Core Interface (SCI) wrapper in `hw/sci/`, which converts the core's
own bus to OBI and adds the dormant-until-woken behaviour.

| Core | ISA options | Native bus | Strongest evidence |
|---|---|---|---|
| `cv32e20` | rv32ec, rv32emc, rv32ic, rv32imc | OBI | full SoC simulation |
| `cv32e40x` | rv32imc | OBI | full SoC simulation |
| `cv32e40p` | rv32imc | OBI | generator only |
| `cv32e40px` | rv32imc | OBI | generator only |
| `serv` | rv32i, rv32ic, rv32im, rv32imc | Wishbone | full SoC simulation; hardened on GF180MCU |
| `qerv` | rv32i, rv32ic, rv32im, rv32imc | Wishbone | CPU subsystem testbench |
| `fazyrv` | rv32i, rv32ic | Wishbone | full SoC simulation |
| `ibex` | rv32ic, rv32imc, rv32ec, rv32emc | request/grant | generator only |
| `picorv32` | rv32i, rv32im, rv32imc | valid/ready memory port | full SoC simulation |
| `snitch` | rv32i | request/response | full SoC simulation |
| `hazard3` | rv32imc | AHB-Lite | full SoC simulation |
| `cva6` | rv32imc | AXI4 | full SoC simulation (simulation only) |
| `rocket` | rv64imc | TileLink | full SoC simulation (simulation only) |
| `boom` | rv64imc | TileLink | full SoC simulation (simulation only) |

"Generator only" means the configuration validates and the templates render,
but the core is in no step of the regression sweep and no passing simulation
result is recorded for it; treat it as unproven. "Simulation only"
cores cannot enter a layout. [docs/cores.md](docs/cores.md) has the full table
and the procedure for adding a core.

## Interconnect and services

**Bus fabric.** `soc.bus` selects one of three interconnects with the same
ports: `obi`, X-HEEP's OBI crossbar and the default; `log`, a logarithmic
interconnect over word-interleaved RAM banks; and `floonoc`, a FlooNoC AXI
network on chip with OBI bridges at the endpoints. Only `obi` has been through
the physical flow.

**Task Dispatch Unit (TDU).** A memory-mapped block (`hw/tdu/rtl/tdu.sv`) that
the orchestrator uses to start work on the other harts. It holds a queue of
eight task descriptors and one wake line per hart. It is required whenever the
design has workers.

**DMA.** `soc.dma` selects the direct memory access engine: `idma`, the
pulp-platform iDMA and the default; `none`, for area-critical designs that
never copy bulk data; or `xheep`, X-HEEP's simple DMA, accepted only for
single-core designs.

**Interrupt controllers.** A CLINT (`hw/tdu/rtl/mosaic_clint.sv`) gives each
hart a software interrupt and a 64-bit timer comparator. The platform-level
interrupt controller is OpenTitan's `rv_plic`, regenerated for the number of
harts in the configuration; `soc.plic: false` removes it. The platform services
support at most 16 harts.

**Memory.** Three profiles: an on-chip SRAM pool of 8 to 512 KB; no on-chip
SRAM, with every hart executing in place from memory-mapped SPI flash and an
optional scratchpad of 64 to 512 bytes; or no on-chip SRAM with an off-chip RAM
region.

**Peripherals.** `soc.peripherals` accepts `uart`, `gpio`, `timer`, `spi`,
`i2c` and `serial_link`. The blocks come from X-HEEP and OpenTitan.

[docs/architecture.md](docs/architecture.md) describes each of these, with the
memory map and the TDU register table.

## Requirements and quick start

You need:

- Linux and git;
- [Nix](https://nixos.org/) with flakes enabled, which provides the pinned
  simulation tools (Verilator 5.050, a bare-metal RISC-V GCC, Icarus Verilog,
  cocotb);
- Python 3.10 or later, for the virtual environment that holds the generator's
  dependencies.

The simulation runners accept Verilator 5.050 and refuse any other version, so
a system Verilator is not a substitute. The first `nix develop .#sim` builds
Verilator, which takes about ten minutes. Working without Nix is described in
[tutorial/01-generator.md](tutorial/01-generator.md) and
[docs/reproducing.md](docs/reproducing.md).

From a clone to a simulated SoC:

```bash
git clone https://github.com/mosaiclab-org/mosaic-soc
cd mosaic-soc
nix develop .#sim
make venv
./tutorial/run_all.sh
```

The script validates the tutorial configuration
(`tutorial/configs/tutorial_soc.yaml`, three harts), renders its topology
diagram, generates the RTL, and simulates the complete SoC. It stops at the
first stage that fails. A successful run ends with these lines:

```text
### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓
### Tutorial complete
### Topology: build/tutorial/tutorial_soc_topology.html
```

`EXIT SUCCESS` means that the orchestrator booted, woke both workers through
the TDU, and every hart wrote its own value to its sentinel word. It says
nothing about the physical flow. The [tutorial](tutorial/README.md) walks
through the same steps one at a time.

## Generate your own SoC

Copy a shipped configuration from `configs/`, edit it, and run two commands
inside the simulation shell:

```bash
make mosaic-gen MOSAIC_CFG=configs/mosaic_wake_demo.yaml
MOSAIC_CFG=configs/mosaic_wake_demo.yaml tb/mosaic_soc/run_generic.sh
```

The first writes the bundle to `build/mosaic/mosaic_wake_demo-<hash>/`. The
second builds one small program per boot image, builds a Verilator model of the
whole SoC, wakes every worker, and prints `EXIT SUCCESS` only after every
configured hart has reported. A run passes only if that line is present; the
exit code alone is not the verdict.

`configs/` holds 32 MOSAIC configurations, and `mosaic.yaml` at the root is the
default seven-hart design. [docs/configuration.md](docs/configuration.md) lists
every key and its allowed values.

## The mosaic command line

`./mosaic` wraps the operations of this repository behind commands that
validate their inputs and return a structured result. It runs from the
repository root with no install.

```bash
./mosaic config-author validate configs/mosaic_wake_demo.yaml   # check a configuration
./mosaic topo-viz check configs/mosaic_wake_demo.yaml           # checks on the derived design
./mosaic topo-viz render configs/mosaic_wake_demo.yaml -o build/topology.html
./mosaic flow-runner list                                       # the 21 registered flows
./mosaic flow-runner run tb-soc-generic --config configs/mosaic_wake_demo.yaml
./mosaic doctor                                                 # tools against the pinned versions
```

Put `--json` before the command to get the result as a JSON object; the exit
status is 1 when the result is not ok. `.venv/bin/python -m pip install -e .`
installs the same tool as the console script `mosaic`.

There is also a path from a text request to a configuration. It uses a fixed
grammar, not a language model:

```bash
./mosaic soc-from-prompt plan "one cv32e20 controller, two picorv32 workers, 64KB sram, tdu, a uart"
```

[docs/agent-harness.md](docs/agent-harness.md) lists every command.

## Web viewer

```bash
./mosaic web serve
```

builds a read-only site showing the configurations, the hardening runs and the
waivers, and serves it on `127.0.0.1:8765`. `./mosaic web build` writes the
site without serving it.

## Use from an AI coding agent

The same commands are offered to AI coding agents as typed tools through a
Model Context Protocol (MCP) server. MCP is the interface an agent host uses to
call external tools.

```bash
./mosaic install --host claude        # prints the host configuration; also codex, opencode, omp
./mosaic install --host claude --write
./mosaic mcp-server                   # the server itself, on standard input and output
./mosaic approve physical --minutes 60
```

Each request has a *scope*, which names the furthest kind of action it may
take (for example `analysis`, `config`, `simulation` or `physical`); the user
sets the allowed scopes in the host configuration as `MOSAIC_SCOPES`, and the
default leaves out `integration` and `physical`. Within a scope, the tools
enforce an order: RTL generation is refused until the configuration has passed
the topology check, and a simulation is refused until generation has passed.
Actions that cost hours or write lasting evidence need an approval token from
`./mosaic approve`, which runs only from a terminal, so an agent cannot grant
its own approval.

The agent contract is [AGENTS.md](AGENTS.md). The skill cards in
`.claude/skills/` describe each command, and the Claude Code plugin is in
`plugins/mosaic/`.

## Physical implementation

The physical flow uses [LibreLane](https://github.com/librelane/librelane)
3.0.0 on the GF180MCU `gf180mcuD` PDK. It hardens a delivery wrapper, a small
top module that instantiates the generated SoC and exposes only the pins the
block needs, and then runs design-rule checks, layout-versus-schematic
comparison, an XOR between two layout streams, antenna checks and static timing
analysis at nine corners. The signoff runner has no option to skip a step, and
a missing metric is reported as unknown, not as clean.

```bash
make mosaic-gen MOSAIC_CFG=configs/mosaic_blockb_3hart.yaml
make -C flow/librelane clone-pdk
cd flow/librelane
MOSAIC_CFG=configs/mosaic_blockb_3hart.yaml \
MOSAIC_HARDEN_FROM_SOC=configs/mosaic_blockb_3hart.yaml \
MOSAIC_HARDEN_DESIGN=mosaic_block_b \
  ./experimental/run_signoff.sh my_run_tag
```

A run takes hours and needs at least 12 GB of free disk.

Its limits:

- Only SERV-only designs of two to four harts, on the `obi` fabric, with no
  on-chip SRAM pool, have been hardened. The area model refuses any other
  design.
- Only GF180MCU has a hardened design. `sky130` and `ihp-sg13g2` are accepted
  as PDK names for RTL and simulation only.
- The power figure comes from the timing tool's default switching activity, not
  from a simulated workload.
- Gate-level simulation is functional only and is not a signoff hard check.
- The chip-level targets with a pad ring (`make harden`, `make classic` in
  `flow/librelane/`) need inputs that the repository does not contain. A block
  can be hardened against an external padframe DEF supplied by the user.

[docs/physical-flow.md](docs/physical-flow.md) describes the gates, the
waivers and the measured results of every tracked run.

## Repository layout

| Path | Contents |
|---|---|
| `mosaic.yaml`, `configs/` | the default configuration and the shipped ones |
| `util/mosaic_gen/` | the generator; `core_registry.py` holds the supported cores and the validation rules |
| `hw/core-v-mini-mcu/` | the SoC templates (`*.sv.tpl`): top level, CPU subsystem, bus, memory, peripherals |
| `hw/sci/` | the SCI wrappers |
| `hw/tdu/` | the TDU and the CLINT |
| `hw/vendor/mosaic/` | vendored cores, iDMA, FlooNoC and the bus bridges |
| `hw/vendor/` (other) | upstream X-HEEP, OpenHW, pulp-platform and OpenTitan IP |
| `sw/` | device libraries, firmware and linker scripts |
| `tb/` | testbenches, from single wrappers to the complete SoC |
| `flow/librelane/` | the physical flow, the waivers and the tracked run metrics |
| `harness/`, `mosaic` | the command-line tool and its launcher |
| `.claude/skills/`, `plugins/mosaic/`, `.omp/` | agent skill cards and host integration |
| `test/test_mosaic_gen/` | the Python test suite (`make test`) |
| `tutorial/`, `demo/` | the hands-on tutorial and three demonstration scripts |
| `docs/` | the documentation listed below |

Generated RTL is a build product. Only the templates are committed.

## Documentation

| Page | Subject |
|---|---|
| [tutorial/README.md](tutorial/README.md) | a hands-on walk from YAML to a simulated SoC |
| [docs/architecture.md](docs/architecture.md) | the generated SoC and the generator pipeline |
| [docs/configuration.md](docs/configuration.md) | every configuration key |
| [docs/cores.md](docs/cores.md) | the supported cores and how to add one |
| [docs/verification.md](docs/verification.md) | the testbenches, the regression sweep and the configuration-space matrix |
| [docs/physical-flow.md](docs/physical-flow.md) | hardening, gates, waivers and measured results |
| [docs/agent-harness.md](docs/agent-harness.md) | the `mosaic` tool, scopes, approvals and agent hosts |
| [docs/reproducing.md](docs/reproducing.md) | the pinned toolchain |
| [docs/status.md](docs/status.md) | what is proven and what is known to be wrong or missing |
| [docs/roadmap.md](docs/roadmap.md) | what is planned and does not exist yet |
| [docs/design-notes/](docs/design-notes/) | two design studies: minimum area on GF180MCU, and booting without on-chip SRAM |

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) covers the environment, the checks to run
before sending a change, and what a pull request should contain.

## Licence and attribution

MOSAIC-SoC is derived from [X-HEEP](https://github.com/x-heep/x-heep) and from
its configuration generator, and it keeps X-HEEP's top-level module names
(`core_v_mini_mcu`, `x_heep_system`).

Files written for this project are licensed under the Solderpad Hardware
License v2.1, SPDX identifier `Apache-2.0 WITH SHL-2.1`; the text is in
[LICENSE](LICENSE). Files taken from X-HEEP and every vendored design keep the
licence stated in their own headers. [UPSTREAM.md](UPSTREAM.md) records the
source, revision and licence of each one, and [NOTICE](NOTICE) lists the
copyright holders.
