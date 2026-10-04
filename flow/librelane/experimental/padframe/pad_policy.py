"""The pad-control policy for Block A, in ONE place.

Both generators read this. They used to carry their own copies, and the copies
drifted immediately: the settings table said rst_ni carries a pull-down while the
wrapper tied it to 0. A table and a netlist that disagree about a pad setting is
worse than either alone, because each looks authoritative.

Decisions and why:

  IE = ~OE on bidirectional pads
      The PDK control table marks IE=1 with OE=1 "Disallowed". Tying IE high, as
      an earlier draft proposed, would have put every driving pad in that state.

  rst_ni carries a pull-DOWN
      An undriven reset then holds the part in reset, which is the diagnosable
      failure on a block whose only observability is status_o. Assumes a
      push-pull reset driver; an open-drain driver would fight it and the part
      would never leave reset.

  PDRV1/PDRV0 = 1/0, i.e. 12 mA
      The fastest output is QSPI at 20 MHz, a 25 ns half period. Estimating bond
      pad, package and a short trace at 15-25 pF, driving ~20 pF through a 10 ns
      edge needs about 10 mA, so 8 mA does not cover it and 12 does.

      THE LOAD FIGURE IS OURS, NOT MEASURED, AND NOBODY ELSE OWNS THIS NUMBER.
      The padframe does not prescribe a drive strength: each block decides
      its own pad configuration according to its needs. So there
      is no house default and no characterised package/board load to design to.
      This value rests on the arithmetic above and nothing else.

      What that means for anyone changing it: if a real load figure ever
      appears and lands well under 20 pF, 8 mA is the better choice. The
      asymmetry is why we sit high rather than low -- too little drive degrades
      the QSPI edge and shows up as a setup failure reading flash during XIP
      boot, which is the only way this part boots, while too much costs supply
      noise and overshoot. Zero-delay GLS sees neither failure, so do not expect
      a simulation to catch a wrong choice here.

      gf180mcu_fd_io__bi_t spans 4/8/12/16 mA, so this moves without changing
      the pad cell. 24 mA would need bi_24t and a padring change, which 20 MHz
      does not justify.

  SL = 0 (fast), CS = 0 (CMOS), no pulls elsewhere
      Nothing in this block needs slew limiting or a Schmitt input except clk_i,
      whose pad is already the Schmitt variant in_s and therefore has no CS.
"""
from __future__ import annotations

QSPI_BASE = "spi_flash_sd_io"

#: Pads that carry a pull, and which way. Everything else gets PU=PD=0.
PULLS: dict[str, str] = {"rst_ni": "PD"}

#: Constant value per control terminal, by pad class. 'oe' and '~oe' are not
#: constants: they mean the core's own output enable and its inverse.
SETTINGS: dict[str, dict[str, str]] = {
    "input":  {"PU": "0", "PD": "0"},
    "output": {"OE": "1", "IE": "0", "CS": "0", "SL": "0",
               "PU": "0", "PD": "0", "PDRV0": "0", "PDRV1": "1"},
    "qspi":   {"OE": "oe", "IE": "~oe", "CS": "0", "SL": "0",
               "PU": "0", "PD": "0", "PDRV0": "0", "PDRV1": "1"},
}

DATA_TERMINALS = {"A", "Y"}


def classify(user_pin: str, cell: str) -> str:
    """Which policy class a pad belongs to, from its cell and signal name."""
    if cell.endswith(("dvdd", "dvss")):
        return "power"
    if cell.endswith(("in_c", "in_s")):
        return "input"
    return "qspi" if user_pin.startswith(QSPI_BASE) else "output"


def value(user_pin: str, klass: str, terminal: str) -> str:
    """The value a terminal is driven to, as a policy token.

    Returns 'oe'/'~oe' for the QSPI enables, 'from core'/'to core' for data, '-'
    for power, and '0'/'1' for constants. The pull override is applied here so
    every caller sees the same answer.
    """
    if klass == "power":
        return "-"
    if terminal == "A":
        return "from core"
    if terminal == "Y":
        return "to core"
    pull = PULLS.get(user_pin)
    if pull is not None and terminal in ("PU", "PD"):
        return "1" if terminal == pull else "0"
    return SETTINGS.get(klass, {}).get(terminal, "?")


def is_constant(token: str) -> bool:
    """True when `value()` returned something the wrapper can tie off."""
    return token in ("0", "1")


def sv_literal(token: str, width: int | None) -> str:
    """Render a constant token as SystemVerilog, replicated across a bus."""
    lit = f"1'b{token}"
    return f"{{{width}{{{lit}}}}}" if width else lit
