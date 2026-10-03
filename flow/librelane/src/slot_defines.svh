// MOSAIC-SoC GF180MCU pad-count defines.
//
// Counts derived from configs/pad_cfg.py (the x-heep pad map). The pad_cfg
// TOP side lists ~60 signal pads: clk + rst are stock single-instance inputs
// (literals clk_pad/rst_n_pad, NOT counted below), ~8 are pure inputs, the
// remainder are bidirectional/output pads (bi_24t), and MOSAIC has no analog
// pads. Finalize these against the completed pad map in slot_mosaic.yaml.

`ifdef SLOT_MOSAIC

// Power/ground pads — starting point; size for the real current budget.
`define NUM_DVDD_PADS 4
`define NUM_DVSS_PADS 4

// Signal pads.
// Pure inputs (in_c): boot_select, execute_from_flash, jtag_tck/tms/trst/tdi,
// uart_rx, ddr_rcv_clk.
`define NUM_INPUT_PADS 8
// Bidir/output (bi_24t): jtag_tdo, uart_tx, exit_valid, ddr_snd_clk, the
// spi_flash/spi/spi2/i2c/i2s/pdm inout pads, and gpio_0..31 (several muxed
// with spi_slave_*/ddr_* per pad_cfg.py).
`define NUM_BIDIR_PADS 50
// MOSAIC is fully digital — no analog pads.
`define NUM_ANALOG_PADS 0

`endif
