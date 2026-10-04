# 1. Use the generator directly

This chapter shows the underlying deterministic path:

```text
tutorial_soc.yaml → schema → topology checks → Mako RTL generation
                  → build manifest → Verilator all-hart execution
```

Run every command from the repository root.

## Stage 0 — prepare the tools

Enter the pinned simulation shell, create the Python environment, and check
the three tools:

```bash
nix develop .#sim
make venv
source .venv/bin/activate
python --version
verilator --version
"${RISCV_TC}-gcc" --version
```

Expected key lines:

```text
Detected Python interpreter: <path-or-command>
Python 3.<minor>.<patch>
Verilator 5.050 2026-07-01 rev v5.050
riscv32-none-elf-gcc (GCC) 14.3.0
```

`nix develop .#sim` is the simulation shell defined in `flake.nix`. It puts
Verilator 5.050 and a bare-metal RISC-V GCC on `PATH` and exports three
variables: `RISCV_TC` (the compiler prefix before `-gcc`, `-ld`, and
`-objcopy`), `RISCV_XHEEP` (the toolchain root, the directory that contains
`bin/`), and `COMPILER_PREFIX` (`riscv32-none-`). The first entry builds
Verilator locally; [`docs/reproducing.md`](../docs/reproducing.md) lists every
pinned version.

Python 3.10 or newer is required for `.venv`. If auto-detection selects an
older Python, recreate the environment with an explicit interpreter, for
example `make clean-venv && PY=python3.10 make venv`.

### Without Nix

The simulation runners source `tb/tools.sh`, which refuses to continue unless
it finds exactly Verilator 5.050 and a RISC-V compiler. Provide both yourself:

```bash
export VERILATOR_PIN=/path/to/verilator-5.050      # contains bin/verilator or usr/bin/verilator
export RISCV_TC=/path/to/toolchain/bin/riscv32-unknown-elf
export RISCV_XHEEP=/path/to/toolchain              # needed by `make mosaic-gen` in Stage 3
"${RISCV_TC}-gcc" --version
```

`VERILATOR_PIN` is optional when the `verilator` on `PATH` already reports
5.050. `RISCV_TC` is optional when a `riscv32-*-elf-gcc` is on `PATH`.
`./mosaic doctor` compares the machine against the pinned versions and names
what to fix.

## Stage 1 — inspect and validate the config

Open [`configs/tutorial_soc.yaml`](configs/tutorial_soc.yaml). Its important
contract is:

- exactly one leading TITAN, which boots immediately;
- two worker images at distinct SRAM addresses;
- a TDU, because ATLAS/NANO harts boot dormant;
- an OBI bus, 32 KiB SRAM, and four basic peripherals.

Validate it with the authoritative schema:

```bash
./mosaic config-author validate tutorial/configs/tutorial_soc.yaml
```

Expected first line:

```text
[OK] tutorial_soc.yaml is valid (3 cores, 4 peripherals)
```

A JSON dump of the parsed configuration follows it and ends with
`"total_cores": 3`.

Artifacts created: none.

What this proves: field types, core capabilities, role ordering, boot-image
layout, SRAM capacity, scheduler policy, and simulation/tapeout restrictions
are internally consistent.

## Stage 2 — check and render the topology

Run semantic checks that are easier to understand before RTL generation:

```bash
./mosaic topo-viz check tutorial/configs/tutorial_soc.yaml
mkdir -p build/tutorial
./mosaic topo-viz render tutorial/configs/tutorial_soc.yaml \
  -o build/tutorial/tutorial_soc_topology.html
```

Expected:

```text
[OK] tutorial_soc.yaml: clean
{
  "findings": [],
  "schema_errors": [],
  "notes": []
}
[OK] rendered obi topology -> build/tutorial/tutorial_soc_topology.html
```

The render command then prints a JSON summary of the diagram (output path,
bus, views, crossbar master count, RAM bank count).

Artifact created: `build/tutorial/tutorial_soc_topology.html`.

What this proves: the resolved masters, fabric, memory, TDU, and peripherals
form a semantically supported topology. The HTML is an explanation aid; it is
not the generated RTL.

## Stage 3 — generate RTL and the software contract

```bash
make mosaic-gen MOSAIC_CFG=tutorial/configs/tutorial_soc.yaml
```

The first run is verbose because FuseSoC prepares dependency generators. Look
for these stable markers:

```text
MOSAIC_BUILD_KEY=tutorial_soc-<hash>
[MCU-GEN] Processing <N> templates...
[MCU-GEN] All templates processed successfully
[MCU-GEN] Generated 3-target PLIC
[MCU-GEN] Generated topology-specific software contract
MOSAIC_MANIFEST=.../build/mosaic/tutorial_soc-<hash>/manifest.json
### MOSAIC-GEN completed! Running FuseSoC register generators...
FuseSoC setup completed successfully.
### MOSAIC manifest: .../build/mosaic/tutorial_soc-<hash>/manifest.json
```

Warnings from vendored FuseSoC metadata may appear. They are not the success
criterion; the final setup and manifest lines are.

What this creates:

```text
build/mosaic/tutorial_soc-<hash>/
├── manifest.json
├── generated/hw/mosaic_soc/         # generated SoC RTL
├── generated/sw/boot_images.json    # per-hart boot contract
├── generated/sw/include/            # topology and memory-map headers
├── generated/sw/linker/             # one linker script per image
└── runs/                             # isolated FuseSoC setup/build closures
```

Generated files live under `build/`; do not edit or commit them. Change the
YAML or a source `.sv.tpl` file and regenerate instead.

## Stage 4 — inspect the content-addressed build

Do not guess the `<hash>`. Ask the manifest locator:

```bash
MANIFEST="$(./.venv/bin/python util/mosaic_gen/build_manifest.py locate \
  --config tutorial/configs/tutorial_soc.yaml \
  --base-config configs/general.hjson \
  --pads-cfg configs/pad_cfg.py \
  --repo-root "$PWD" \
  --output-root build/mosaic)"
python3 tutorial/inspect_manifest.py "$MANIFEST"
```

Expected:

```text
MOSAIC build summary
manifest: build/mosaic/tutorial_soc-<hash>/manifest.json
build key: tutorial_soc-<hash>
generated root: build/mosaic/tutorial_soc-<hash>/generated
harts: 3
  hart 0: cv32e20  role=titan  isa=rv32emc  boot=0x00000180 image=0
  hart 1: fazyrv   role=atlas  isa=rv32i    boot=0x00001000 image=1
  hart 2: serv     role=nano   isa=rv32i    boot=0x00002000 image=2
RTL package: build/mosaic/tutorial_soc-<hash>/generated/hw/mosaic_soc/include/mosaic_soc_pkg.sv
CPU RTL: build/mosaic/tutorial_soc-<hash>/generated/hw/mosaic_soc/cpu_subsystem.sv
boot contract: build/mosaic/tutorial_soc-<hash>/generated/sw/boot_images.json
```

What this proves: the generated hardware and generated software agree on hart
IDs, roles, ISAs, boot addresses, and image ownership.

## Stage 5 — prove all configured harts execute

```bash
MOSAIC_CFG=tutorial/configs/tutorial_soc.yaml \
  tb/mosaic_soc/run_generic.sh
```

This runner intentionally regenerates the configuration, builds one liveness
image per boot slot, builds the full SoC with Verilator, and checks exact
sentinels for all three harts.

Expected key lines:

```text
verilator: <path>/verilator [Verilator 5.050 2026-07-01 rev v5.050]
### [1/4] generating topology-generic RTL (tutorial/configs/tutorial_soc.yaml) ...
###       running FuseSoC setup (register generators + filelist) ...
### [2/4] assembling one liveness image per generated boot slot ...
    firmware: .../generic.hex (3 harts, wake mask 6)
### [3/4] building the full-SoC Verilator model ...
### [4/4] running topology-generic liveness firmware ...
EXIT SUCCESS
### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓
```

The linker prints `-z relro ignored` and `LOAD segment with RWX permissions`
warnings in step 2, and the simulation prints the last 30 lines of its bus
trace before `EXIT SUCCESS`. Neither is a failure.

Logs created:

- `tb/mosaic_soc/fusesoc-setup-generic.log`
- `tb/mosaic_soc/build-generic.log`
- `tb/mosaic_soc/sim-generic.log`

What this proves: generated RTL elaborates, firmware matches the generated boot
contract, the TITAN can dispatch workers through the TDU, and every configured
hart reaches its exact liveness result.

Next: run the same gates through [the harness](02-harness.md).
