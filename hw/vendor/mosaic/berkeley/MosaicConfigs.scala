package chipyard

import org.chipsalliance.cde.config.{Config}

// ---------------------------------------------------------------------------
// MOSAIC-SoC extraction config (github.com/MILOUDIAS/MOSAIC-SoC)
//
// One small BOOM (v3) and one standard Rocket in a SINGLE design, so both
// tile closures are uniquified by one firtool run and can be vendored into
// one Verilog tree with no cross-elaboration module-name collisions
// (plusarg_reader, EICG_wrapper, shared TL widgets, ...).
//
// Deliberately NO WithSystemBusWidth(128) (unlike the Large hetero configs):
// the default 64-bit system bus matches MOSAIC's TileLink->OBI bridge, which
// walks 64-bit TL beats into 2x 32-bit OBI transactions.
//
// Elaborated with: make verilog CONFIG=MosaicRocketBoomConfig
// The RocketTile / BoomTile module subtrees are then extracted from
// gen-collateral and vendored into MOSAIC's hw/vendor/mosaic/berkeley/.
// ---------------------------------------------------------------------------
class MosaicRocketBoomConfig extends Config(
  new boom.v3.common.WithNSmallBooms(1) ++                 // 1x small BOOM v3
  new freechips.rocketchip.rocket.WithNHugeCores(1) ++     // 1x standard Rocket
  new chipyard.config.AbstractConfig)
