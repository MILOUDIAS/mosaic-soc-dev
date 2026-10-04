# Configuration reference

A MOSAIC configuration is a YAML file with a single top-level key, `soc`. This
page lists every key the generator accepts under it. The rules come from
`validate_soc_config` in `util/mosaic_gen/core_registry.py`, which is the only
validator: the generator and the `mosaic` tool both call it. An unknown key at
any level is an error, so a misspelt key is never silently ignored.

Check a file without generating anything:

```bash
./mosaic config-author validate configs/mosaic_wake_demo.yaml
./mosaic topo-viz check configs/mosaic_wake_demo.yaml
```

The first command applies the rules on this page. The second adds checks on the
derived design, such as address-window overlaps.

## Example

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
  bus: obi
  scheduler:
    tdu: true
    mode: dynamic
  peripherals: [uart, gpio, timer, spi]
```

## Top-level keys

| Key | Allowed values | Default | Notes |
|---|---|---|---|
| `schema` | `mosaic/v1` | `mosaic/v1` | The version of this format. An unknown version is refused by name. |
| `name` | lowercase letter, then lowercase letters, digits or underscores; at most 64 characters | `mosaic_soc` | Names the bundle directory. |
| `pdk` | `gf180mcu`, `sky130`, `ihp-sg13g2` | `gf180mcu` | The process design kit (PDK) is the foundry's technology data. Only `gf180mcu` can be combined with `target: tapeout`. |
| `profile` | `soc`, `testbench` | `soc` | `testbench` relaxes the role rules and is required for simulation-only cores. |
| `target` | `rtl`, `simulation`, `tapeout` | `rtl` | `tapeout` restricts the design to the one physically qualified combination; see below. |
| `cores` | list of core groups, at least one | required | See [Core groups](#core-groups). |
| `memory` | mapping | see below | See [Memory](#memory). |
| `bus` | `obi`, `log`, `floonoc` | `obi` | The bus fabric. |
| `bus_opts` | mapping | empty | See [Bus options](#bus-options). |
| `scheduler` | mapping | TDU off, `static` | See [Scheduler](#scheduler). |
| `peripherals` | list of `uart`, `gpio`, `timer`, `spi`, `i2c`, `serial_link`; no duplicates | empty | User-domain peripherals. |
| `dma` | `idma`, `xheep`, `none` | `idma` | `xheep` is refused when the design has more than one hart. |
| `debug` | boolean | `true` | `false` removes the JTAG debug module. |
| `plic` | boolean | `true` | `false` removes the platform-level interrupt controller; peripheral interrupts then reach no hart. |
| `spi_mode` | `full`, `xip_only` | `full` | `xip_only` keeps only the read-only flash reader. |
| `multicore_timer` | boolean | `true` | `false` stops a multi-hart design from adding the user-domain timer automatically. Listing `timer` in `peripherals` still adds it. |
| `gpio_ao` | boolean | `true` | The always-on GPIO block. |
| `ao_rv_timer` | boolean | `true` | The always-on timer. |
| `ao_fast_intr` | boolean | `true` | The always-on fast interrupt controller. |
| `objectives` | mapping | absent | Physical-design intent. See [Objectives](#objectives). |

The total number of harts across all groups is at most 16.

## Core groups

Each entry of `cores` is a mapping.

| Key | Allowed values | Notes |
|---|---|---|
| `ip` | one of the 14 registered cores ([cores.md](cores.md)) | required |
| `isa` | an instruction set the chosen core supports | required |
| `count` | integer, at least 1 | default 1 |
| `role` | `titan`, `atlas`, `nano` | required |
| `boot_addr` | integer or a string such as `0x1000`; 32-bit, a multiple of 4 | the reset address of this group |
| core-specific keys | see below | |

### Role and topology rules

- With `profile: soc`, a design that has any worker (`atlas` or `nano`) must
  have exactly one `titan` hart, as the first group with `count: 1`, and must
  set `scheduler.tdu: true`. `atlas` groups come before `nano` groups.
- A design of `titan` harts only is legal with or without the TDU.
- With `profile: testbench`, a design without a `titan` is legal. If it has a
  `titan` group, that group is still first. Workers still require the TDU when
  there is more than one hart.
- `rocket` and `boom` may be a `titan` only as a single hart in the first group.

### Boot address rules

- With `profile: soc`, every worker group must give `boot_addr`, and the `titan`
  group must not: its reset vector is the boot ROM.
- `rocket` and `boom` always need `boot_addr`.
- When `memory.sram_kb` is 8 or more, `boot_addr` must lie inside the SRAM.
- When `memory.sram_kb` is 0, `boot_addr` must lie in the flash window
  (`0x4000_0000` to `0x4100_0000`) or in the declared `memory.external` region.
- Groups that share a `boot_addr` share a boot image, so with `profile: soc`
  they must use the same application binary interface (RV32E, RV32 and RV64
  images cannot be mixed in one image).
- With on-chip SRAM, distinct boot images need at least 1024 bytes each, and the
  images, the shared control block and a minimum stack must fit in the SRAM.

### Core-specific keys

| Core | Key | Allowed values |
|---|---|---|
| `cv32e20` | `rv32e` | boolean; must agree with the `isa` |
| | `rv32m` | `RV32MNone`, `RV32MSlow`, `RV32MFast`, `RV32MSingleCycle`; must agree with the `isa` |
| `cv32e40p`, `cv32e40px`, `cv32e40x` | `num_mhpmcounters` | 0 to 29 |
| `fazyrv` | `chunksize` | 1, 2, 4, 8 (datapath bits processed per cycle) |
| | `conf` | `MIN`, `INT`, `CSR` |
| | `rftype` | `LOGIC`, `BRAM`, `BRAM_BP`, `BRAM_DP`, `BRAM_DP_BP`; `conf: CSR` cannot use `LOGIC` |
| | `rvc` | `NONE`, `COMB`, `REG`, `HYBR`; anything other than `NONE` requires a `c` in the `isa` |
| | `memdly1` | must be false; the wrapper cannot support the fixed-latency mode |
| `serv` | `w` | 1 |
| `qerv` | `w` | 4 |
| `serv`, `qerv` | `with_csr`, `pre_register` | boolean |
| | `compressed`, `mdu` | boolean; must agree with the `c` and `m` letters of the `isa` |
| `ibex` | `rv32e` | boolean; must agree with the `isa` |
| | `mhpmcounters` | 0 to 29 |
| `picorv32` | `counters`, `barrel_shifter` | boolean |
| | `compressed` | boolean; must agree with the `isa` |
| | `mul`, `div` | boolean; both true exactly when the `isa` has `m` |
| `snitch` | `rve`, `rvm` | must be false; the integration is RV32I only |
| `hazard3`, `cva6`, `rocket`, `boom` | (none beyond `boot_addr`) | |

`cva6`, `rocket` and `boom` are simulation-only: they require
`profile: testbench` and are refused with `target: tapeout`.

## Memory

| Key | Allowed values | Default | Notes |
|---|---|---|---|
| `sram_kb` | 0, or a power of two from 8 to 512 | 32 | 0 means no on-chip SRAM pool. |
| `boot_rom_kb` | a power of two from 1 to 64 | 2 | |
| `scratchpad_bytes` | a power of two from 64 to 512 | absent | A small on-chip data memory. Only with `sram_kb: 0`. |
| `external` | mapping with `base` and `size_kb` | absent | An off-chip writable region. |
| `external.base` | an address inside `0xF000_0000` to `0xF100_0000` | `0xF000_0000` | |
| `external.size_kb` | 1 to 16384 | 16384 | |

## Bus options

`bus_opts` may contain `log` and `floonoc` mappings.

| Key | Allowed values | Default | Notes |
|---|---|---|---|
| `log.topology` | `lic` | `lic` | The logarithmic interconnect variant. |
| `log.num_banks` | `auto` or an integer of at least 1 | `auto` | With `bus: log` the bank count must be a power of two, at least the number of bus masters and at most 32, and `sram_kb` must be divisible by it with at least 1 KB per bank. `auto` picks the next power of two at or above the master count. |
| `floonoc.route_algo` | `ID` | `ID` | |
| `floonoc.endpoints` | `compact` | `compact` | |

The number of bus masters is `2 x harts + 1 + DMA ports`, where the DMA
contributes 4 ports for `idma`, 6 for `xheep` and 0 for `none`.

## Scheduler

| Key | Allowed values | Default |
|---|---|---|
| `tdu` | boolean | `false` |
| `mode` | `static`, `dynamic`, `power-aware` | `static` |

## Objectives

`objectives` states what the physical implementation must achieve. It is
optional for simulation and required before a hardening configuration can be
derived (see [physical-flow.md](physical-flow.md)). Every value is a positive
number.

| Key | Meaning |
|---|---|
| `target_clock_mhz` | The requested clock frequency. Static timing analysis decides whether it was met. |
| `die_um` | A mandated square die side, in micrometres. The block must be exactly this size. |
| `max_die_um` | An upper bound on the die side. |
| `max_area_mm2` | An upper bound on the die area. |
| `repair_margin_pct` | A whole number from 1 to 99 that overrides the slew repair margin of the signoff template for this design. |
| `target_utilisation` | The placement density the die is sized for, as a fraction below 1 (0.82, not 82). |

## The `tapeout` target

`target: tapeout` is a claim that the design is covered by a completed physical
implementation. The validator therefore accepts exactly one combination, the one
that was hardened as Block A, the GF180MCU reference design:

| Key | Required value |
|---|---|
| `profile` | `soc` |
| `pdk` | `gf180mcu` |
| `bus` | `obi` |
| `cores` | one `serv` `titan` with `isa: rv32ic`, `with_csr: 1`, `compressed: 1`; one `serv` `atlas` with `isa: rv32i`, `boot_addr: 0x40010000`, `with_csr: 0` |
| `memory` | `sram_kb: 0`, `boot_rom_kb: 1`, `scratchpad_bytes: 128` |
| `dma` | `none` |
| `debug`, `plic`, `multicore_timer`, `gpio_ao`, `ao_rv_timer`, `ao_fast_intr` | `false` |
| `spi_mode` | `xip_only` |
| `scheduler` | `tdu: true`, `mode: dynamic` |
| `peripherals` | `uart` only |

`configs/mosaic_tapeout_ultra.yaml` is that configuration. Every other
combination remains valid for `rtl` and `simulation`. Widening this matrix
requires a hardened design and tests, not a schema edit.

## Shipped configurations

The files in `configs/` and the root `mosaic.yaml`. "Workers" are `atlas` or
`nano` harts. All use the `obi` fabric and the GF180MCU PDK unless stated.

| File | Cores | Fabric | What it demonstrates |
|---|---|---|---|
| `mosaic.yaml` (root) | 1 cv32e20, 2 fazyrv, 4 serv | obi | The default seven-hart design with 32 KB SRAM. |
| `mosaic_wake_demo.yaml` | 1 cv32e20, 1 fazyrv, 1 serv | obi | The three-hart TDU wake demonstration. |
| `mosaic_wake_demo_log.yaml` | 1 cv32e20, 1 fazyrv, 1 serv | log | The same demonstration on the logarithmic fabric. |
| `mosaic_floonoc.yaml` | 1 cv32e20, 1 fazyrv, 1 serv | floonoc | The same demonstration on the FlooNoC fabric. |
| `mosaic_log_poc.yaml` | 1 cv32e20, 2 fazyrv, 4 serv | log | The seven-hart design on the logarithmic fabric, 64 KB SRAM. |
| `mosaic_picorv32.yaml` | 1 cv32e20, 2 picorv32 | obi | PicoRV32 as a woken worker. |
| `mosaic_snitch.yaml` | 1 cv32e20, 2 snitch | obi | Snitch as a woken worker. |
| `mosaic_hazard3.yaml` | 1 cv32e20, 2 hazard3 | obi | Hazard3 as a woken worker. |
| `mosaic_cva6.yaml` | 1 cva6, 1 fazyrv, 1 serv | obi | CVA6 as the orchestrator. Simulation only. |
| `mosaic_new_cores.yaml` | 1 cva6, 1 snitch, 1 picorv32 | obi | Three wrapped cores together. Simulation only. |
| `mosaic_rocket.yaml` | 1 cv32e20, 2 rocket | obi | 64-bit Rocket tiles as workers. Simulation only. |
| `mosaic_boom.yaml` | 1 cv32e20, 2 boom | obi | 64-bit BOOM tiles as workers. Simulation only. |
| `mosaic_berkeley.yaml` | 1 cv32e20, 1 rocket, 1 boom | obi | Both Berkeley tiles in one build. Simulation only. |
| `mosaic_rocket_titan.yaml` | 1 rocket, 1 serv | obi | A Rocket tile as the orchestrator. Simulation only. |
| `mosaic_boom_titan.yaml` | 1 boom, 1 serv | obi | A BOOM tile as the orchestrator. Simulation only. |
| `mosaic_titan_obi.yaml` | 2 cv32e20, 2 cv32e40x | obi | Four `titan` harts as a symmetric multiprocessor. |
| `mosaic_titan_log.yaml` | 2 cv32e20, 2 cv32e40x | log | The same on the logarithmic fabric. |
| `mosaic_titan_floonoc.yaml` | 2 cv32e20, 2 cv32e40x | floonoc | The same on the FlooNoC fabric. |
| `mosaic_sim.yaml` | 1 serv, 1 qerv, 1 fazyrv (workers only) | obi | The input of the `tb/mosaic` CPU-subsystem testbench. |
| `mosaic_log.yaml` | 1 serv, 1 qerv, 1 fazyrv (workers only) | log | The same subset on the logarithmic fabric. |
| `mosaic_all_cores.yaml` | cv32e20, ibex, fazyrv, qerv, serv | obi | Renders every wrapper branch of that set; a generation test, with 64 KB SRAM. |
| `mosaic_min_area.yaml` | 1 cv32e20, 1 fazyrv, 2 serv | obi | The starting point of the area study, 16 KB SRAM. |
| `mosaic_xip_bringup.yaml` | 1 cv32e20, 2 serv | obi | No SRAM and no writable memory; every hart executes in place. |
| `mosaic_scratchpad.yaml` | 1 cv32e20, 2 serv | obi | No SRAM, a 512-byte scratchpad. |
| `mosaic_nosram_prototype.yaml` | 1 cv32e20, 2 serv | obi | No SRAM, external RAM declared. |
| `mosaic_fazyrv_serv_xip.yaml` | 1 cv32e20, 1 fazyrv, 2 serv | obi | Execute in place with a 512-byte scratchpad. |
| `mosaic_pico_serv_xip.yaml` | 1 picorv32, 2 serv | obi | PicoRV32 as the orchestrator, execute in place, no DMA. |
| `mosaic_serv_only.yaml` | 3 serv | obi | SERV as the orchestrator, execute in place, no DMA. |
| `mosaic_tapeout_min.yaml` | 3 serv | obi | The minimum-area three-hart design of the area study. |
| `mosaic_tapeout_ultra.yaml` | 2 serv | obi | Block A, the GF180MCU reference design. The only `target: tapeout` file. Requests 20 MHz. |
| `mosaic_blockb_3hart.yaml` | 3 serv | obi | Block B: Block A with a second worker. Requests 20 MHz. |
| `mosaic_blockc_4hart.yaml` | 4 serv | obi | Block C: Block A with three workers. Requests 10 MHz. |
| `mosaic_ihp_probe.yaml` | 1 serv | obi | The smallest design on `pdk: ihp-sg13g2`, used to exercise the second technology. |
| `llm_demo.yaml` | 1 cv32e20, 1 picorv32, 1 serv | obi | An example of a configuration produced from a text request. |
| `titan_nomem.yaml` | 1 cv32e20 | obi | Does not pass validation (`sram_kb: 4` is below the 8 KB minimum). |

The `.hjson` and `.py` files in `configs/` are X-HEEP's base files. The
generator reads `configs/general.hjson` for the peripheral address map and
`configs/pad_cfg.py` for the pads.
