# Supported cores

The table below is derived from `CORE_SPECS` in
`util/mosaic_gen/core_registry.py`, which is the single source of the supported
cores. `./mosaic tb-matrix axes` prints the live list.

## The cores

"Native bus" is the bus the core itself speaks. "Wrapper" is the Standard Core
Interface (SCI) wrapper that converts it to OBI; a dash means the core speaks
OBI already and is instantiated directly. "Evidence" names the strongest check
in this repository that exercises the core, in the order explained under the
table.

| Core | ISA options | Native bus | Wrapper (`hw/sci/`) | Evidence | Simulation only |
|---|---|---|---|---|---|
| `cv32e20` | rv32ec, rv32emc, rv32ic, rv32imc | OBI | - | full SoC | no |
| `cv32e40x` | rv32imc | OBI | - | full SoC (four-hart `titan` configurations) | no |
| `cv32e40p` | rv32imc | OBI | - | generator only | no |
| `cv32e40px` | rv32imc | OBI | - | generator only | no |
| `serv` | rv32i, rv32ic, rv32im, rv32imc | Wishbone (lite) | `serv_sci.sv` | full SoC, wrapper testbench, hardened on GF180MCU | no |
| `qerv` | rv32i, rv32ic, rv32im, rv32imc | Wishbone (lite) | `serv_sci.sv` with `W=4` | CPU subsystem testbench | no |
| `fazyrv` | rv32i, rv32ic | Wishbone (classic) | `fazyrv_sci.sv` | full SoC, wrapper testbench | no |
| `ibex` | rv32ic, rv32imc, rv32ec, rv32emc | request/grant | `ibex_sci.sv` | generator only (`mosaic_all_cores.yaml` renders) | no |
| `picorv32` | rv32i, rv32im, rv32imc | native valid/ready memory port | `picorv32_sci.sv` | full SoC, wrapper testbench | no |
| `snitch` | rv32i | request/response | `snitch_sci.sv` | full SoC | no |
| `hazard3` | rv32imc | AHB-Lite | `hazard3_sci.sv` | full SoC, wrapper testbench | no |
| `cva6` | rv32imc | AXI4 | `cva6_sci.sv` | full SoC | yes |
| `rocket` | rv64imc | TileLink (TL-C) | `rocket_sci.sv` | full SoC | yes |
| `boom` | rv64imc | TileLink (TL-C) | `boom_sci.sv` | full SoC | yes |

Evidence levels, strongest first:

1. **Hardened on GF180MCU.** The core is in a design that passed the signoff
   flow ([physical-flow.md](physical-flow.md)). Only `serv` is.
2. **Full SoC.** A shipped configuration containing the core is a step of the
   regression sweep (`scripts/run_sweep.sh`) that runs the complete generated
   SoC in Verilator and requires the `EXIT SUCCESS` marker
   ([verification.md](verification.md)).
3. **Wrapper testbench.** A single-hart testbench under `tb/sci/<core>/` checks
   the wrapper alone.
4. **CPU subsystem testbench.** `tb/mosaic` runs the core inside the generated
   `cpu_subsystem` against memory models, without the rest of the SoC.
5. **Generator only.** The configuration validates and the templates render. No
   simulation of this core ships. Treat such a core as unproven.

The simulation-only cores require `profile: testbench` and are refused by
`target: tapeout`. Rocket and BOOM are 64-bit tiles extracted from a Chipyard
build (`hw/vendor/mosaic/berkeley/README.md`); they reach the 32-bit OBI fabric
through address-window translation in `mosaic_tilelink_to_obi`.

The per-core configuration keys are listed in
[configuration.md](configuration.md#core-specific-keys).

## Adding a core

A new core needs three things.

1. **A registry entry.** Add a `CoreSpec` to `CORE_SPECS` in
   `util/mosaic_gen/core_registry.py` (name, instruction sets, parameters,
   capabilities, and its native bus in `native_bus`), and add the name to
   `AVAILABLE_CPUS` in `util/mosaic_gen/cpu/cpu.py`. Every list of valid cores
   elsewhere is derived from the registry.
2. **An SCI wrapper.** Write `hw/sci/<core>_sci.sv`, list it in
   `hw/sci/sci.core`, and vendor the core's RTL with a FuseSoC `.core` file
   under `hw/vendor/mosaic/<core>/`. The wrapper converts the native bus to OBI,
   holds the core dormant while `fetch_enable_i` is low, and maps the interrupt
   inputs.
3. **A template branch.** Add `% elif group.name == "<core>":` to
   `hw/core-v-mini-mcu/cpu_subsystem.sv.tpl`, instantiating the wrapper with its
   parameters.

The `wrapper-smith` command does the mechanical part and marks what is left:

```bash
./mosaic wrapper-smith families                      # the bus families it knows
./mosaic wrapper-smith fetch <git-url>@<commit> --subdir <rtl-dir>
./mosaic wrapper-smith analyze <rtl-dir> --top <module> -o analysis.json
./mosaic wrapper-smith scaffold <core> --from analysis.json \
    --vendor-from <rtl-dir>            # dry run: stages the changes under build/
./mosaic wrapper-smith scaffold <core> --from analysis.json \
    --vendor-from <rtl-dir> --apply    # writes them into the tree
```

`fetch` clones at the exact commit and records the licence. `analyze` parses the
ports and classifies the bus against nine known families, with a confidence
value; below 0.5 it reports `unknown` and you choose `--family` yourself.
`scaffold` writes the wrapper from the family's template, the registry entry,
the template branch, the FuseSoC entries and a bring-up configuration
`configs/mosaic_<core>.yaml`. It leaves `TODO(wrapper-smith)` markers where the
core's port names and handshake details must be filled in by hand.

Then prove it:

```bash
./mosaic tb-smith generate <core>     # writes tb/sci/<core>/
./mosaic tb-smith run <core>          # must print TB PASS
./mosaic tb-smith wake-demo <core>    # full SoC, must reach EXIT SUCCESS
```

The generated testbench checks that the parked core makes no bus request, that
it starts after a wake, and that it executes a short program to a known store.
A core is not integrated until both commands pass.
