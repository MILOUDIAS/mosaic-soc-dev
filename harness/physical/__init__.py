"""Physical intent: derive the hand-fitted part of a hardening config.

Phase 1 measured that the floorplan is the only design-dependent part -- PDN,
timing and check configuration carried between two designs untouched. This
package derives the floorplan and leaves the rest as the template it is.
"""

from harness.physical.floorplan import (
    CALIBRATION,
    UTILISATION_OBSERVATIONS,
    DEFAULT_MARGIN_UM,
    DEFAULT_TARGET_UTILISATION,
    AreaEstimate,
    AreaMeasurement,
    Floorplan,
    clock_period_ns,
    derive_floorplan,
    estimate_logic_area,
    margin_multiples,
)
from harness.physical.sram import (
    DENSEST as DENSEST_SRAM_MACRO,
    SRAM_MACROS,
    SramCost,
    SramMacro,
    largest_sram_that_fits,
    sram_cost,
)
from harness.physical.routability import (
    ROUTABILITY_OBSERVATIONS,
    RoutabilityObservation,
    RoutabilityVerdict,
    UtilisationAdvice,
    assess,
    assess_log,
    parse_drt_passes,
    parse_drt_trajectory,
    recommended_utilisation,
)

__all__ = [
    "SRAM_MACROS", "DENSEST_SRAM_MACRO", "SramCost", "SramMacro",
    "sram_cost", "largest_sram_that_fits",
    "ROUTABILITY_OBSERVATIONS", "RoutabilityObservation", "RoutabilityVerdict",
    "UtilisationAdvice", "assess", "assess_log", "parse_drt_passes",
    "parse_drt_trajectory", "recommended_utilisation",
    "CALIBRATION", "UTILISATION_OBSERVATIONS",
    "DEFAULT_MARGIN_UM", "DEFAULT_TARGET_UTILISATION",
    "AreaEstimate", "AreaMeasurement", "Floorplan",
    "clock_period_ns", "derive_floorplan", "estimate_logic_area",
    "margin_multiples",
]
