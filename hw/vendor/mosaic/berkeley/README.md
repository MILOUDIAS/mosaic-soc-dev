# hw/vendor/mosaic/berkeley — Rocket + BOOM v3 tile closures (SIM-ONLY)

Extracted RocketTile and BoomTile (SmallBoomV3) Verilog closures from a
**chipyard 1.14.0** elaboration, behind the MOSAIC `xheep_tilelink_to_obi`
window bridge (`hw/vendor/mosaic/tl_obi/`). **Both cores are EXCLUDED from
the GF180MCU tapeout** — RV64 + caches do not fit the PoC area budget; this
integration exists for simulation/architecture exploration, like CVA6.

## Provenance (fully reproducible)

| What | Value |
|---|---|
| chipyard | tag `1.14.0` (`0acc1e1de2d3`), github.com/ucb-bar/chipyard |
| config | `MosaicRocketBoomConfig` (see `MosaicConfigs.scala` here) |
| = | `boom.v3.common.WithNSmallBooms(1) ++ freechips.rocketchip.rocket.WithNHugeCores(1) ++ chipyard.config.AbstractConfig` |
| firtool | CIRCT 1.75.0 release (`circt-full-static-linux-x64`) |
| JDK | Temurin 17 (chipyard's sbt 1.8.2 cannot parse JDK 21+ classfiles) |
| licenses | rocket-chip: Apache-2.0/BSD (LICENSE.SiFive, LICENSE.Berkeley); BOOM: BSD-3-Clause |

Reproduction:

```bash
git clone --branch 1.14.0 --depth 1 https://github.com/ucb-bar/chipyard.git
cd chipyard && ./scripts/init-submodules-no-riscv-tools.sh
cp <this dir>/MosaicConfigs.scala generators/chipyard/src/main/scala/config/
export JAVA_HOME=<jdk17> PATH=$JAVA_HOME/bin:$PATH
make -C sims/verilator verilog CONFIG=MosaicRocketBoomConfig FIRTOOL_BIN=<firtool-1.75.0>
python3 <this dir>/extract_tile_closure.py \
    --build-dir sims/verilator/generated-src/chipyard.harness.TestHarness.MosaicRocketBoomConfig \
    --out <this dir>/rtl --tiles RocketTile BoomTile
```

## Why ONE elaboration for both tiles

Separate elaborations of RocketConfig and a BOOM config emit identically
named modules with different contents (`plusarg_reader`, `EICG_wrapper`,
shared TileLink widgets) — a combined MOSAIC config (`mosaic_berkeley.yaml`)
would then have module-name collisions in one Verilator build. A single
hetero design gets uniquified by one firtool run: collision-free by
construction. Deliberately **64-bit system bus** (no `WithSystemBusWidth(128)`
like the upstream Large hetero configs) to match the TL→OBI bridge.

## The window trick (coherence without coherence hardware)

The extracted tiles keep the chipyard memory map they were elaborated with;
their PMAs decide what is cacheable. The SCI wrappers' bridge translates:

| Tile view | PMA | MOSAIC target |
|---|---|---|
| `0x8000_0000 + x` (DRAM) | cacheable+executable | SRAM `x` (code, private data) |
| `0x0200_0000 + x` (CLINT) | uncached device | `0x3000 + x` (TDU sentinels) |
| `0x0C00_0000 + x` (PLIC) | uncached device | `0x200A_0000 + x` (the TDU) |

Shared sentinel/TDU traffic is therefore uncached BY CONSTRUCTION (the same
trick as CVA6's NrCachedRegionRules=0) — no L1 flush, no probes: the bridge
never sends a TileLink Probe (single coherent client per tile port).
Worker programs for these cores store sentinels via the CLINT-range alias
(`tb/mosaic_soc/prog/{atlas_tl,nano_tl}.S`); everything else in the demo flow
is unchanged, including the TB write monitor (it sees translated addresses).

Known cosmetic limitation: the tiles' `mhartid` comes from the chipyard
elaboration (0/1), not the MOSAIC hart index — the wake-demo firmware never
reads `mhartid` on workers.

## Files

- `MosaicConfigs.scala` — the chipyard config (copy into a chipyard clone)
- `extract_tile_closure.py` — closure extraction (writes `rtl/` + `berkeley.f`)
- `berkeley.f` — ordered filelist consumed by `tb/mosaic_soc/gen_filelist.py`,
  config-gated on `rocket_sci`/`boom_sci` in the generated cpu_subsystem
- `berkeley.core` — FuseSoC shim (`mosaic:ip:berkeley`, depends `mosaic:ip:tl_obi`)
- `rtl/` — the extracted module closure (one file per module)
