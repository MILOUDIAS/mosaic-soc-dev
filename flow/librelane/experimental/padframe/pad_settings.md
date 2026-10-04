# Block A pad settings

Generated from `<padframe>_interface.yaml` by `gen_pad_settings.py`, with every
value taken from `pad_policy.py`. Do not hand-edit: regenerate when the
padframe DEF is reissued.

Variant `A` · **167 terminals** across **22 user pins** · die 1110 × 1110 µm

| class | user pins | terminals each |
|---|---:|---|
| power | 2 | `DVDD`, `DVSS` |
| input | 5 | `PD`, `PU`, `Y` |
| output | 11 | `A`, `CS`, `IE`, `OE`, `PD`, `PDRV0`, `PDRV1`, `PU`, `SL`, `Y` |
| qspi | 4 | `A`, `CS`, `IE`, `OE`, `PD`, `PDRV0`, `PDRV1`, `PU`, `SL`, `Y` |

`oe` is the core's own `spi_flash_sd_*_oe_o`. `IE = ~oe` because the PDK
control table marks `IE=1, OE=1` **Disallowed**, so IE cannot be tied high.
`rst_ni` is the one input carrying a pull, and it is a pull-down: an undriven
reset then holds the part in reset, which is the diagnosable failure on a block
whose only observability is `status_o`.

**Drive strength is 12 mA and rests on an estimate, not a measurement.**
No house default exists: the padframe does not prescribe a drive strength,
and each block sets its own. The number
comes from QSPI at 20 MHz into an assumed 15-25 pF of bond pad, package and
short trace, where ~20 pF through a 10 ns edge needs about 10 mA. If a real
board load ever lands well under 20 pF, 8 mA is the better choice.

## Every terminal

| user pin | class | terminal | DEF pin | slot | driven to |
|---|---|---|---|---|---|
| `boot_select_i` | input | `PD` | `boot_select_i_PD` | W15 | 0 |
| `boot_select_i` | input | `PU` | `boot_select_i_PU` | W15 | 0 |
| `boot_select_i` | input | `Y` | `boot_select_i` | W15 | to core |
| `clk_i` | input | `PD` | `clk_i_PD` | W13 | 0 |
| `clk_i` | input | `PU` | `clk_i_PU` | W13 | 0 |
| `clk_i` | input | `Y` | `clk_i` | W13 | to core |
| `execute_from_flash_i` | input | `PD` | `execute_from_flash_i_PD` | W16 | 0 |
| `execute_from_flash_i` | input | `PU` | `execute_from_flash_i_PU` | W16 | 0 |
| `execute_from_flash_i` | input | `Y` | `execute_from_flash_i` | W16 | to core |
| `rst_ni` | input | `PD` | `rst_ni_PD` | W14 | 1 |
| `rst_ni` | input | `PU` | `rst_ni_PU` | W14 | 0 |
| `rst_ni` | input | `Y` | `rst_ni` | W14 | to core |
| `uart_rx_i` | input | `PD` | `uart_rx_i_PD` | W17 | 0 |
| `uart_rx_i` | input | `PU` | `uart_rx_i_PU` | W17 | 0 |
| `uart_rx_i` | input | `Y` | `uart_rx_i` | W17 | to core |
| `spi_flash_cs_o` | output | `A` | `spi_flash_cs_o_OUT` | W20 | from core |
| `spi_flash_cs_o` | output | `CS` | `spi_flash_cs_o_CS` | W20 | 0 |
| `spi_flash_cs_o` | output | `IE` | `spi_flash_cs_o_IE` | W20 | 0 |
| `spi_flash_cs_o` | output | `OE` | `spi_flash_cs_o_OE` | W20 | 1 |
| `spi_flash_cs_o` | output | `PD` | `spi_flash_cs_o_PD` | W20 | 0 |
| `spi_flash_cs_o` | output | `PDRV0` | `spi_flash_cs_o_PDRV0` | W20 | 0 |
| `spi_flash_cs_o` | output | `PDRV1` | `spi_flash_cs_o_PDRV1` | W20 | 1 |
| `spi_flash_cs_o` | output | `PU` | `spi_flash_cs_o_PU` | W20 | 0 |
| `spi_flash_cs_o` | output | `SL` | `spi_flash_cs_o_SL` | W20 | 0 |
| `spi_flash_cs_o` | output | `Y` | `spi_flash_cs_o_IN` | W20 | to core |
| `spi_flash_sck_o` | output | `A` | `spi_flash_sck_o_OUT` | W19 | from core |
| `spi_flash_sck_o` | output | `CS` | `spi_flash_sck_o_CS` | W19 | 0 |
| `spi_flash_sck_o` | output | `IE` | `spi_flash_sck_o_IE` | W19 | 0 |
| `spi_flash_sck_o` | output | `OE` | `spi_flash_sck_o_OE` | W19 | 1 |
| `spi_flash_sck_o` | output | `PD` | `spi_flash_sck_o_PD` | W19 | 0 |
| `spi_flash_sck_o` | output | `PDRV0` | `spi_flash_sck_o_PDRV0` | W19 | 0 |
| `spi_flash_sck_o` | output | `PDRV1` | `spi_flash_sck_o_PDRV1` | W19 | 1 |
| `spi_flash_sck_o` | output | `PU` | `spi_flash_sck_o_PU` | W19 | 0 |
| `spi_flash_sck_o` | output | `SL` | `spi_flash_sck_o_SL` | W19 | 0 |
| `spi_flash_sck_o` | output | `Y` | `spi_flash_sck_o_IN` | W19 | to core |
| `status_o[0]` | output | `A` | `status_o_OUT[0]` | N04 | from core |
| `status_o[0]` | output | `CS` | `status_o_CS[0]` | N04 | 0 |
| `status_o[0]` | output | `IE` | `status_o_IE[0]` | N04 | 0 |
| `status_o[0]` | output | `OE` | `status_o_OE[0]` | N04 | 1 |
| `status_o[0]` | output | `PD` | `status_o_PD[0]` | N04 | 0 |
| `status_o[0]` | output | `PDRV0` | `status_o_PDRV0[0]` | N04 | 0 |
| `status_o[0]` | output | `PDRV1` | `status_o_PDRV1[0]` | N04 | 1 |
| `status_o[0]` | output | `PU` | `status_o_PU[0]` | N04 | 0 |
| `status_o[0]` | output | `SL` | `status_o_SL[0]` | N04 | 0 |
| `status_o[0]` | output | `Y` | `status_o_IN[0]` | N04 | to core |
| `status_o[1]` | output | `A` | `status_o_OUT[1]` | N05 | from core |
| `status_o[1]` | output | `CS` | `status_o_CS[1]` | N05 | 0 |
| `status_o[1]` | output | `IE` | `status_o_IE[1]` | N05 | 0 |
| `status_o[1]` | output | `OE` | `status_o_OE[1]` | N05 | 1 |
| `status_o[1]` | output | `PD` | `status_o_PD[1]` | N05 | 0 |
| `status_o[1]` | output | `PDRV0` | `status_o_PDRV0[1]` | N05 | 0 |
| `status_o[1]` | output | `PDRV1` | `status_o_PDRV1[1]` | N05 | 1 |
| `status_o[1]` | output | `PU` | `status_o_PU[1]` | N05 | 0 |
| `status_o[1]` | output | `SL` | `status_o_SL[1]` | N05 | 0 |
| `status_o[1]` | output | `Y` | `status_o_IN[1]` | N05 | to core |
| `status_o[2]` | output | `A` | `status_o_OUT[2]` | N06 | from core |
| `status_o[2]` | output | `CS` | `status_o_CS[2]` | N06 | 0 |
| `status_o[2]` | output | `IE` | `status_o_IE[2]` | N06 | 0 |
| `status_o[2]` | output | `OE` | `status_o_OE[2]` | N06 | 1 |
| `status_o[2]` | output | `PD` | `status_o_PD[2]` | N06 | 0 |
| `status_o[2]` | output | `PDRV0` | `status_o_PDRV0[2]` | N06 | 0 |
| `status_o[2]` | output | `PDRV1` | `status_o_PDRV1[2]` | N06 | 1 |
| `status_o[2]` | output | `PU` | `status_o_PU[2]` | N06 | 0 |
| `status_o[2]` | output | `SL` | `status_o_SL[2]` | N06 | 0 |
| `status_o[2]` | output | `Y` | `status_o_IN[2]` | N06 | to core |
| `status_o[3]` | output | `A` | `status_o_OUT[3]` | N07 | from core |
| `status_o[3]` | output | `CS` | `status_o_CS[3]` | N07 | 0 |
| `status_o[3]` | output | `IE` | `status_o_IE[3]` | N07 | 0 |
| `status_o[3]` | output | `OE` | `status_o_OE[3]` | N07 | 1 |
| `status_o[3]` | output | `PD` | `status_o_PD[3]` | N07 | 0 |
| `status_o[3]` | output | `PDRV0` | `status_o_PDRV0[3]` | N07 | 0 |
| `status_o[3]` | output | `PDRV1` | `status_o_PDRV1[3]` | N07 | 1 |
| `status_o[3]` | output | `PU` | `status_o_PU[3]` | N07 | 0 |
| `status_o[3]` | output | `SL` | `status_o_SL[3]` | N07 | 0 |
| `status_o[3]` | output | `Y` | `status_o_IN[3]` | N07 | to core |
| `status_o[4]` | output | `A` | `status_o_OUT[4]` | N08 | from core |
| `status_o[4]` | output | `CS` | `status_o_CS[4]` | N08 | 0 |
| `status_o[4]` | output | `IE` | `status_o_IE[4]` | N08 | 0 |
| `status_o[4]` | output | `OE` | `status_o_OE[4]` | N08 | 1 |
| `status_o[4]` | output | `PD` | `status_o_PD[4]` | N08 | 0 |
| `status_o[4]` | output | `PDRV0` | `status_o_PDRV0[4]` | N08 | 0 |
| `status_o[4]` | output | `PDRV1` | `status_o_PDRV1[4]` | N08 | 1 |
| `status_o[4]` | output | `PU` | `status_o_PU[4]` | N08 | 0 |
| `status_o[4]` | output | `SL` | `status_o_SL[4]` | N08 | 0 |
| `status_o[4]` | output | `Y` | `status_o_IN[4]` | N08 | to core |
| `status_o[5]` | output | `A` | `status_o_OUT[5]` | N09 | from core |
| `status_o[5]` | output | `CS` | `status_o_CS[5]` | N09 | 0 |
| `status_o[5]` | output | `IE` | `status_o_IE[5]` | N09 | 0 |
| `status_o[5]` | output | `OE` | `status_o_OE[5]` | N09 | 1 |
| `status_o[5]` | output | `PD` | `status_o_PD[5]` | N09 | 0 |
| `status_o[5]` | output | `PDRV0` | `status_o_PDRV0[5]` | N09 | 0 |
| `status_o[5]` | output | `PDRV1` | `status_o_PDRV1[5]` | N09 | 1 |
| `status_o[5]` | output | `PU` | `status_o_PU[5]` | N09 | 0 |
| `status_o[5]` | output | `SL` | `status_o_SL[5]` | N09 | 0 |
| `status_o[5]` | output | `Y` | `status_o_IN[5]` | N09 | to core |
| `status_o[6]` | output | `A` | `status_o_OUT[6]` | N10 | from core |
| `status_o[6]` | output | `CS` | `status_o_CS[6]` | N10 | 0 |
| `status_o[6]` | output | `IE` | `status_o_IE[6]` | N10 | 0 |
| `status_o[6]` | output | `OE` | `status_o_OE[6]` | N10 | 1 |
| `status_o[6]` | output | `PD` | `status_o_PD[6]` | N10 | 0 |
| `status_o[6]` | output | `PDRV0` | `status_o_PDRV0[6]` | N10 | 0 |
| `status_o[6]` | output | `PDRV1` | `status_o_PDRV1[6]` | N10 | 1 |
| `status_o[6]` | output | `PU` | `status_o_PU[6]` | N10 | 0 |
| `status_o[6]` | output | `SL` | `status_o_SL[6]` | N10 | 0 |
| `status_o[6]` | output | `Y` | `status_o_IN[6]` | N10 | to core |
| `status_valid_o` | output | `A` | `status_valid_o_OUT` | N03 | from core |
| `status_valid_o` | output | `CS` | `status_valid_o_CS` | N03 | 0 |
| `status_valid_o` | output | `IE` | `status_valid_o_IE` | N03 | 0 |
| `status_valid_o` | output | `OE` | `status_valid_o_OE` | N03 | 1 |
| `status_valid_o` | output | `PD` | `status_valid_o_PD` | N03 | 0 |
| `status_valid_o` | output | `PDRV0` | `status_valid_o_PDRV0` | N03 | 0 |
| `status_valid_o` | output | `PDRV1` | `status_valid_o_PDRV1` | N03 | 1 |
| `status_valid_o` | output | `PU` | `status_valid_o_PU` | N03 | 0 |
| `status_valid_o` | output | `SL` | `status_valid_o_SL` | N03 | 0 |
| `status_valid_o` | output | `Y` | `status_valid_o_IN` | N03 | to core |
| `uart_tx_o` | output | `A` | `uart_tx_o_OUT` | W18 | from core |
| `uart_tx_o` | output | `CS` | `uart_tx_o_CS` | W18 | 0 |
| `uart_tx_o` | output | `IE` | `uart_tx_o_IE` | W18 | 0 |
| `uart_tx_o` | output | `OE` | `uart_tx_o_OE` | W18 | 1 |
| `uart_tx_o` | output | `PD` | `uart_tx_o_PD` | W18 | 0 |
| `uart_tx_o` | output | `PDRV0` | `uart_tx_o_PDRV0` | W18 | 0 |
| `uart_tx_o` | output | `PDRV1` | `uart_tx_o_PDRV1` | W18 | 1 |
| `uart_tx_o` | output | `PU` | `uart_tx_o_PU` | W18 | 0 |
| `uart_tx_o` | output | `SL` | `uart_tx_o_SL` | W18 | 0 |
| `uart_tx_o` | output | `Y` | `uart_tx_o_IN` | W18 | to core |
| `vdd` | power | `DVDD` | `VDD` | N11 | - |
| `vss` | power | `DVSS` | `VSS` | W12 | - |
| `spi_flash_sd_io[0]` | qspi | `A` | `spi_flash_sd_io_OUT[0]` | W21 | from core |
| `spi_flash_sd_io[0]` | qspi | `CS` | `spi_flash_sd_io_CS[0]` | W21 | 0 |
| `spi_flash_sd_io[0]` | qspi | `IE` | `spi_flash_sd_io_IE[0]` | W21 | ~oe |
| `spi_flash_sd_io[0]` | qspi | `OE` | `spi_flash_sd_io_OE[0]` | W21 | oe |
| `spi_flash_sd_io[0]` | qspi | `PD` | `spi_flash_sd_io_PD[0]` | W21 | 0 |
| `spi_flash_sd_io[0]` | qspi | `PDRV0` | `spi_flash_sd_io_PDRV0[0]` | W21 | 0 |
| `spi_flash_sd_io[0]` | qspi | `PDRV1` | `spi_flash_sd_io_PDRV1[0]` | W21 | 1 |
| `spi_flash_sd_io[0]` | qspi | `PU` | `spi_flash_sd_io_PU[0]` | W21 | 0 |
| `spi_flash_sd_io[0]` | qspi | `SL` | `spi_flash_sd_io_SL[0]` | W21 | 0 |
| `spi_flash_sd_io[0]` | qspi | `Y` | `spi_flash_sd_io_IN[0]` | W21 | to core |
| `spi_flash_sd_io[1]` | qspi | `A` | `spi_flash_sd_io_OUT[1]` | W22 | from core |
| `spi_flash_sd_io[1]` | qspi | `CS` | `spi_flash_sd_io_CS[1]` | W22 | 0 |
| `spi_flash_sd_io[1]` | qspi | `IE` | `spi_flash_sd_io_IE[1]` | W22 | ~oe |
| `spi_flash_sd_io[1]` | qspi | `OE` | `spi_flash_sd_io_OE[1]` | W22 | oe |
| `spi_flash_sd_io[1]` | qspi | `PD` | `spi_flash_sd_io_PD[1]` | W22 | 0 |
| `spi_flash_sd_io[1]` | qspi | `PDRV0` | `spi_flash_sd_io_PDRV0[1]` | W22 | 0 |
| `spi_flash_sd_io[1]` | qspi | `PDRV1` | `spi_flash_sd_io_PDRV1[1]` | W22 | 1 |
| `spi_flash_sd_io[1]` | qspi | `PU` | `spi_flash_sd_io_PU[1]` | W22 | 0 |
| `spi_flash_sd_io[1]` | qspi | `SL` | `spi_flash_sd_io_SL[1]` | W22 | 0 |
| `spi_flash_sd_io[1]` | qspi | `Y` | `spi_flash_sd_io_IN[1]` | W22 | to core |
| `spi_flash_sd_io[2]` | qspi | `A` | `spi_flash_sd_io_OUT[2]` | N01 | from core |
| `spi_flash_sd_io[2]` | qspi | `CS` | `spi_flash_sd_io_CS[2]` | N01 | 0 |
| `spi_flash_sd_io[2]` | qspi | `IE` | `spi_flash_sd_io_IE[2]` | N01 | ~oe |
| `spi_flash_sd_io[2]` | qspi | `OE` | `spi_flash_sd_io_OE[2]` | N01 | oe |
| `spi_flash_sd_io[2]` | qspi | `PD` | `spi_flash_sd_io_PD[2]` | N01 | 0 |
| `spi_flash_sd_io[2]` | qspi | `PDRV0` | `spi_flash_sd_io_PDRV0[2]` | N01 | 0 |
| `spi_flash_sd_io[2]` | qspi | `PDRV1` | `spi_flash_sd_io_PDRV1[2]` | N01 | 1 |
| `spi_flash_sd_io[2]` | qspi | `PU` | `spi_flash_sd_io_PU[2]` | N01 | 0 |
| `spi_flash_sd_io[2]` | qspi | `SL` | `spi_flash_sd_io_SL[2]` | N01 | 0 |
| `spi_flash_sd_io[2]` | qspi | `Y` | `spi_flash_sd_io_IN[2]` | N01 | to core |
| `spi_flash_sd_io[3]` | qspi | `A` | `spi_flash_sd_io_OUT[3]` | N02 | from core |
| `spi_flash_sd_io[3]` | qspi | `CS` | `spi_flash_sd_io_CS[3]` | N02 | 0 |
| `spi_flash_sd_io[3]` | qspi | `IE` | `spi_flash_sd_io_IE[3]` | N02 | ~oe |
| `spi_flash_sd_io[3]` | qspi | `OE` | `spi_flash_sd_io_OE[3]` | N02 | oe |
| `spi_flash_sd_io[3]` | qspi | `PD` | `spi_flash_sd_io_PD[3]` | N02 | 0 |
| `spi_flash_sd_io[3]` | qspi | `PDRV0` | `spi_flash_sd_io_PDRV0[3]` | N02 | 0 |
| `spi_flash_sd_io[3]` | qspi | `PDRV1` | `spi_flash_sd_io_PDRV1[3]` | N02 | 1 |
| `spi_flash_sd_io[3]` | qspi | `PU` | `spi_flash_sd_io_PU[3]` | N02 | 0 |
| `spi_flash_sd_io[3]` | qspi | `SL` | `spi_flash_sd_io_SL[3]` | N02 | 0 |
| `spi_flash_sd_io[3]` | qspi | `Y` | `spi_flash_sd_io_IN[3]` | N02 | to core |
