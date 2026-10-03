class CPU:
    """
    Represents a CPU configuration.
    """

    # NOTE: cva6 is supported for SIMULATION ONLY (32-bit cv32a65x-derived
    # config, AXI->OBI bridged). It remains excluded from the GF180MCU tapeout:
    # ~80 kGE + caches does not fit the PoC area budget. See AGENTS.md §13.
    # NOTE: rocket and boom (Berkeley, chipyard 1.14.0, RV64!) are likewise
    # SIMULATION ONLY: extracted RocketTile/BoomTile closures, TileLink->OBI
    # bridged with window translation (code via 0x8000_0000, sentinels via the
    # CLINT range, TDU via the PLIC range). Never part of the GF180MCU tapeout.
    AVAILABLE_CPUS = {
        "hazard3",
        "boom",
        "cv32e20",
        "cv32e40p",
        "cv32e40px",
        "cv32e40x",
        "cva6",
        "fazyrv",
        "picorv32",
        "rocket",
        "serv",
        "snitch",
        "qerv",
        "ibex",
    }

    def __init__(self, name: str):
        if name not in self.AVAILABLE_CPUS:
            raise ValueError(
                f"Invalid CPU name '{name}'. Must be one of: {', '.join(self.AVAILABLE_CPUS)}"
            )
        self.name = name

        # Dictionary to hold optional parameter values
        self.params = {}

    def get_name(self) -> str:
        """
        Get the name of the CPU.
        :return: Name of the CPU.
        """
        return self.name

    def is_defined(self, param_name: str) -> bool:
        """
        Check if a given parameter is defined.
        :param param_name: Name of the parameter to check.
        :return: True if the parameter is defined, False otherwise.
        """
        return param_name in self.params

    def get_param(self, param_name: str):
        """
        Get the value of a given parameter.
        :param param_name: Name of the parameter to get.
        :return: Value of the parameter or None if not defined.
        """
        return self.params.get(param_name, None)

    def set_param(self, param_name: str, value):
        """
        Set a parameter value.
        :param param_name: Name of the parameter to set.
        :param value: Value to assign.
        """
        self.params[param_name] = value
