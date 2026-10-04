# Upstream sources

This file records where the code in this repository comes from, which upstream
files this project changed, and which licence each included design declares.
It states what the files say. It is not legal advice.

Terms used below:

- **Upstream** means the project a file was copied from.
- **Vendored** means copied into this repository instead of being fetched at
  build time.
- **SHL** is the Solderpad Hardware License. "Apache-2.0 WITH SHL-2.1" is the
  SPDX identifier of version 2.1, which is a set of additions to the Apache
  License 2.0. SHL-0.51 is an older, standalone version.

Licence texts kept at the root of the repository:

| File | Licence |
|---|---|
| `LICENSE` | Solderpad Hardware License v2.1. It applies to the files written for MOSAIC-SoC. |
| `LICENSES/Apache-2.0.txt` | Apache License 2.0, which SHL-2.1 refers to. |
| `LICENSES/SHL-0.51.txt` | Solderpad Hardware License 0.51, the licence file that X-HEEP ships. |

## 1. X-HEEP

MOSAIC-SoC is a fork of [X-HEEP](https://github.com/x-heep/x-heep), a
single-core RISC-V microcontroller generator.

- Base commit: `ee2b40ae3cee6c48e16fc3d71c31bc23f1ca4790`
  (12 June 2026, 30 commits after tag `v1.0.5`).
- How the base was checked: of the 85 files that this commit changed upstream,
  64 are byte-identical here and 3 are files this project modified; those 3 are
  closer to this commit than to its parent. For each of the other modified
  files listed in section 3, the upstream version that matches best is the one
  at this commit.
- Licence: the `LICENSE` file of X-HEEP is the Solderpad Hardware License 0.51.
  The headers of X-HEEP's own files declare `Apache-2.0 WITH SHL-2.1`,
  `Apache-2.0` or `SHL-0.51`. Each file keeps the licence in its header.

Upstream identifiers are kept so that the origin of the code stays visible:
`x_heep_system`, `core_v_mini_mcu`, `x-heep.h`, the `x-heep:` names of the
upstream FuseSoC cores, and the `RISCV_XHEEP` and `X_HEEP_CFG` variables.

## 2. xheep_gen

`util/mosaic_gen` is the X-HEEP configuration generator
[xheep_gen](https://github.com/x-heep/xheep_gen), vendored at commit
`95efdefe2b8d38d9752fa9ba59666dbe5faf212e` and then extended in place. In
X-HEEP it lives in `util/xheep_gen`. Its upstream file headers declare
`Apache-2.0` with "Copyright 2026 EPFL". The files this project changed are
listed in section 3. The files this project added to the directory include
`core_registry.py`, `core_integration.py`, `mosaic_config.py`,
`floonoc_gen.py`, `build_manifest.py`, `plic_gen.py`, `software_gen.py` and
`pack_flash.py`.

## 3. Upstream X-HEEP files modified by this project

Paths are the paths in this repository. Two directories were renamed:
`util/xheep_gen` became `util/mosaic_gen`, and `test/test_x_heep_gen` became
`test/test_mosaic_gen`.

| Directory | Modified files |
|---|---|
| (repository root) | `.gitignore`, `Makefile`, `README.md`, `core-v-mini-mcu.core` |
| `configs/` | `pad_cfg.py` |
| `hw/core-v-mini-mcu/` | `ao_peripheral_subsystem.sv.tpl`, `core_v_mini_mcu.sv.tpl`, `cpu_subsystem.sv.tpl`, `debug_subsystem.sv`, `peripheral_subsystem.sv.tpl`, `system_bus.sv.tpl`, `system_xbar.sv.tpl` |
| `hw/core-v-mini-mcu/include/` | `core_v_mini_mcu_pkg.sv.tpl` |
| `hw/ip/obi_fifo/` | `obi_fifo.sv` |
| `hw/system/` | `x_heep_system.sv.tpl` |
| `hw/vendor/` | `xheep_cluster_interconnect.core`, `openhwgroup_cv32e40x.core` |
| `hw/vendor/lowrisc/opentitan/hw/ip/uart/rtl/` | `uart_core.sv` |
| `hw/vendor/xheep/spi/rtl/` | `spi_subsystem.sv.tpl` |
| `hw/vendor/openhwgroup/` | `cv32e40x.vendor.hjson`, `cv32e40x.lock.hjson`, and 58 files under `cv32e40x/` (see the note below) |
| `tb/` | `ext_bus.sv`, `tb_top.sv`, `testharness.sv.tpl` |
| `test/test_mosaic_gen/` | `test_peripherals.py` |
| `util/` | `python-requirements.txt` |
| `util/mosaic_gen/` | `bus_type.py`, `load_config.py`, `mcu_gen.py`, `xheep.py` |
| `util/mosaic_gen/cpu/` | `cpu.py` |
| `util/mosaic_gen/memory_ss/` | `memory_ss.py`, `ram_bank.py` |
| `util/mosaic_gen/peripherals/` | `base_peripherals_domain.py` |

Note on cv32e40x: the 58 files under `hw/vendor/openhwgroup/cv32e40x/` differ
from X-HEEP because this project vendors a newer revision of the core, not
because it edited them by hand. X-HEEP pins cv32e40x at
`f17028f2369373d9443e4636f2826218e8d54e0f` (release 0.9.0). This repository
pins it at `d952cd63bc1b4eb58cd893c28ef8283c781e345e`. 87 of the vendored
files are byte-identical to that upstream revision. One file,
`rtl/cv32e40x_id_stage.sv`, differs; the patch directory
`hw/vendor/patches/openhwgroup_cv32e40x/` holds one patch,
`0003-fix-xif-issue.patch`. `hw/vendor/openhwgroup/if_xif_compat.sv` is a
compatibility file added by this project.

## 4. Upstream X-HEEP features not carried over

The following parts of X-HEEP are not in this repository.

| Upstream path | What it is |
|---|---|
| `hw/fpga/` | FPGA targets and their board files |
| `hw/asic/sky130/`, `hw/asic/generic_example/` | The SkyWater 130 nm example and the generic technology example |
| `sw/applications/` | The X-HEEP example applications, CoreMark included |
| `sw/freertos/`, `sw/cmake/`, `sw/linker/`, `sw/Makefile`, `sw/CMakeLists.txt` | The X-HEEP software build system, linker script templates and FreeRTOS configuration |
| `sw/device/lib/crt/`, `sw/device/lib/sdk/`, `sw/device/bsp/`, `sw/device/target/` | The C runtime start-up files, the SDK layer, the flash board support package and the per-board headers |
| `sw/vendor/` | The vendored `iceprog` flash programmer |
| `docs/` | The X-HEEP documentation |
| `tb/systemc_tb/`, `tb/tb_sc_top.cpp`, `tb/*.cfg` | The SystemC testbench and the OpenOCD configuration files |
| `test/test_apps/`, `test/verifheep/` | The application regression runner and the VerifHEEP framework |
| `scripts/` | The Design Compiler synthesis scripts, the UPF power-intent templates and the simulator helper scripts. The files now in `scripts/` were written for MOSAIC-SoC. |
| `util/docker/`, `util/profile/`, `util/area-plot/`, `util/conda_environment.yml` | The container image, the profiling flow, the area plot tool and the Conda environment |
| `ides/`, `.github/`, `.readthedocs.yaml`, `external.mk` | IDE project files, continuous integration and the external-project Makefile hook |

## 5. Vendored cores and IP

"Licence declared" is what the file headers and the licence file say. A
revision is given in short form; the `.lock.hjson` file beside each upstream
directory, or the `UPSTREAM` file inside each `hw/vendor/mosaic` directory,
holds the full value.

### 5.1 Added by this project (`hw/vendor/mosaic/`)

Each directory has an `UPSTREAM` file that gives the commit, how it was
checked and what was changed locally.

| Directory | Upstream project | Revision | Licence declared | Licence file |
|---|---|---|---|---|
| `axi_obi/` | MOSAIC rewrite of [pulp-platform/axi_obi](https://github.com/pulp-platform/axi_obi) | `205c9bb338df` | MOSAIC files: Apache-2.0 WITH SHL-2.1. Upstream: SHL-0.51 | `LICENSE.axi_obi` |
| `berkeley/` | Rocket and BOOM v3 tiles generated with [Chipyard](https://github.com/ucb-bar/chipyard) | Chipyard 1.14.0, `0acc1e1de2d3` | Apache-2.0 and BSD-3-Clause | `LICENSE.rocket-chip.SiFive`, `LICENSE.rocket-chip.Berkeley`, `LICENSE.boom`, `LICENSE.hardfloat`, `LICENSE.chipyard` |
| `cva6/` | [openhwgroup/cva6](https://github.com/openhwgroup/cva6) | `8a2df2987ab3` | SHL-0.51; some headers Apache-2.0 WITH SHL-2.0 or SHL-2.1 | `LICENSE`, `LICENSE.SiFive`, `LICENSE.Berkeley` |
| `fazyrv/` | [meiniKi/FazyRV](https://github.com/meiniKi/FazyRV) | `c8d9c7971b91` | MIT | `LICENSE.txt` |
| `floonoc/` | [pulp-platform/FlooNoC](https://github.com/pulp-platform/FlooNoC) | `d534a2135cfb` | SHL-0.51 | `LICENSE-SHL` |
| `hazard3/` | [Wren6991/Hazard3](https://github.com/Wren6991/Hazard3) | `8af992930f71` | Apache-2.0 | `LICENSE` |
| `ibex/` | [lowRISC/ibex](https://github.com/lowRISC/ibex) | `022f084096ba` | Apache-2.0 | `LICENSE`, `NOTICE` |
| `idma/` | [pulp-platform/iDMA](https://github.com/pulp-platform/iDMA) | `3c0cc3fbaa8f` | SHL-0.51 | `LICENSE` |
| `picorv32/` | [YosysHQ/picorv32](https://github.com/YosysHQ/picorv32) | `f00a88c36eaa` | ISC | `COPYING` |
| `serv/` | [olofk/serv](https://github.com/olofk/serv) | `9baea86ff0e4` | ISC; five files Apache-2.0 | `LICENSE` |
| `snitch/` | Snitch, from [pulp-platform/mempool](https://github.com/pulp-platform/mempool) | MemPool `3855df6da189` | SHL-0.51 | `LICENSE` |
| `tl_obi/` | none, written for MOSAIC-SoC | not applicable | Apache-2.0 WITH SHL-2.1 | root `LICENSE` |

### 5.2 Inherited from X-HEEP (`hw/vendor/lowrisc`, `pulp_platform`, `openhwgroup`, `xheep`)

These directories come with X-HEEP and are unchanged except where section 3
says otherwise. The patches that X-HEEP applies to them are in
`hw/vendor/patches/`.

| Directory | Upstream project | Revision | Licence declared | Licence file |
|---|---|---|---|---|
| `lowrisc/opentitan/` | [lowRISC/opentitan](https://github.com/lowRISC/opentitan) | `47a0f4798feb` | Apache-2.0 | none in the directory; text in `LICENSES/Apache-2.0.txt` |
| `pulp_platform/axi/` | [pulp-platform/axi](https://github.com/pulp-platform/axi) | `78831b6feba2` | SHL-0.51 | `LICENSE` |
| `pulp_platform/axi_slice/` | [pulp-platform/axi_slice](https://github.com/pulp-platform/axi_slice) | `a4f72bc21ac4` | SHL-0.51 | `LICENSE` |
| `pulp_platform/common_cells/` | [pulp-platform/common_cells](https://github.com/pulp-platform/common_cells) | `9afda9abb565` | SHL-0.51 | `LICENSE` |
| `pulp_platform/fpnew/` | [pulp-platform/fpnew](https://github.com/pulp-platform/fpnew) | `79e453139072` | SHL-0.51 | `LICENSE.solderpad`, `LICENSE.apache` |
| `pulp_platform/fpu_ss/` | [pulp-platform/fpu_ss](https://github.com/pulp-platform/fpu_ss) | `809e9a63e3b6` | SHL-0.51 | `LICENSE.md` |
| `pulp_platform/gpio/` | [pulp-platform/gpio](https://github.com/pulp-platform/gpio) | `61b842818a2f` | headers Apache-2.0; licence file SHL-0.51 | `LICENSE` |
| `pulp_platform/obi/` | [pulp-platform/obi](https://github.com/pulp-platform/obi), added by this project for iDMA | commit not recorded (the core file says version 0.1.2) | SHL-0.51 | `LICENSE` |
| `pulp_platform/pulpissimo/` | [pulp-platform/pulpissimo](https://github.com/pulp-platform/pulpissimo) | `4c0f9e754b43` | see licence files | `rtl/tb/remote_bitbang/LICENSE.SiFive`, `rtl/tb/remote_bitbang/LICENSE.Berkeley` |
| `pulp_platform/quadrilatero/` | [pulp-platform/quadrilatero](https://github.com/pulp-platform/quadrilatero) | `ba58f85a496c` | Apache-2.0 WITH SHL-2.1 | `LICENSE.md` |
| `pulp_platform/register_interface/` | [pulp-platform/register_interface](https://github.com/pulp-platform/register_interface) | `8e8c209ea559` | headers Apache-2.0 and SHL-0.51; licence file SHL-0.51 | `LICENSE` |
| `pulp_platform/riscv_dbg/` | [pulp-platform/riscv-dbg](https://github.com/pulp-platform/riscv-dbg) | `618ee6e0e261` | SHL-0.51 | `LICENSE`, `LICENSE.SiFive` |
| `pulp_platform/serial_link/` | [pulp-platform/serial_link](https://github.com/pulp-platform/serial_link) | `c55df03a1da0` | SHL-0.51 | `LICENSE` |
| `pulp_platform/tech_cells_generic/` | [pulp-platform/tech_cells_generic](https://github.com/pulp-platform/tech_cells_generic) | `fca524a1174f` | SHL-0.51 | `LICENSE` |
| `openhwgroup/cv32e20/` | [openhwgroup/cve2](https://github.com/openhwgroup/cve2) | `b72358c73e39` | Apache-2.0 | none in the directory; text in `LICENSES/Apache-2.0.txt` |
| `openhwgroup/cv32e40p/` | [openhwgroup/cv32e40p](https://github.com/openhwgroup/cv32e40p) | `a43277c0dc64` | headers Apache-2.0 WITH SHL-2.1; licence file SHL-0.51 | `LICENSE` |
| `openhwgroup/cv32e40x/` | [openhwgroup/cv32e40x](https://github.com/openhwgroup/cv32e40x) | `d952cd63bc1b` (changed by this project) | headers SHL-0.51 and Apache-2.0 WITH SHL-2.0; licence file SHL-0.51 | `LICENSE` |
| `xheep/cluster_interconnect/` | [x-heep/cluster_interconnect](https://github.com/x-heep/cluster_interconnect) | `1bb8f5679d20` | SHL-0.51 | `LICENSE` |
| `xheep/common/` | [x-heep/x-heep-common](https://github.com/x-heep/x-heep-common) | `d32ba1ce395b` | SHL-0.51 | `LICENSE` |
| `xheep/cv32e40px/` | [x-heep/cv32e40px](https://github.com/x-heep/cv32e40px) | `1f75b23d2388` | headers Apache-2.0 WITH SHL-2.1; licence file SHL-0.51 | `LICENSE` |
| `xheep/dma/` | [x-heep/xheep_dma](https://github.com/x-heep/xheep_dma) | `1e7f540b9e53` | Apache-2.0 WITH SHL-2.1 | none in the directory; text in root `LICENSE` |
| `xheep/i2s/` | [x-heep/xheep_i2s](https://github.com/x-heep/xheep_i2s) | `b9ea3655da0a` | Apache-2.0 WITH SHL-2.1 | none in the directory; text in root `LICENSE` |
| `xheep/obi_spi_slave/` | [x-heep/obi_spi_slave](https://github.com/x-heep/obi_spi_slave) | `f08cf8c9fe00` | SHL-0.51 | `LICENSE` |
| `xheep/spi/` | [x-heep/xheep_spi](https://github.com/x-heep/xheep_spi) | `f990e2152343` | Apache-2.0 | none in the directory; text in `LICENSES/Apache-2.0.txt` |

`xheep/spi/vendor/` holds two further vendored projects: the OpenTitan SPI
host from [x-heep/xheep_opentitan_spi_host](https://github.com/x-heep/xheep_opentitan_spi_host)
at `91acf0f3bf8d`, and the `spimemio` flash controller from
[YosysHQ/picorv32](https://github.com/YosysHQ/picorv32) at `f00a88c36eaa`.

## 6. Physical design flow

`flow/librelane/` includes files from the wafer-space
[gf180mcu-project-template](https://github.com/wafer-space/gf180mcu-project-template)
(Apache-2.0). `flow/librelane/NOTICE` lists them.
