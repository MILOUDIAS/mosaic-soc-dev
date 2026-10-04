from enum import Enum


class BusType(Enum):
    """Enumeration of all supported bus types.

    The enum value is interpolated verbatim into the SystemVerilog
    ``bus_type_e`` enum literal in ``mosaic_soc_pkg.sv.tpl`` — the two
    must stay in sync.
    """

    onetoM = "onetoM"
    NtoM = "NtoM"
    # MOSAIC multi-fabric options
    LOG = "LOG"  # two-tier: tcdm_interconnect (LIC) over RAM + varlat periph tier
    FLOONOC = "FLOONOC"  # floogen-generated AXI NoC bridged to OBI
