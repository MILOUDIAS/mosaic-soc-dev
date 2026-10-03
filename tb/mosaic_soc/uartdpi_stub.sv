// uartdpi_stub.sv — DPI-free stub of the lowRISC uartdpi model, for the Icarus
// (event-driven) full-SoC sim. The wake-and-run demo doesn't use the UART (cores
// communicate via RAM sentinels + soc_ctrl exit), so we just present an idle TX
// line and ignore RX — avoiding Icarus DPI-C setup. Same ports as the real model.
module uartdpi #(
    parameter BAUD = 0,
    parameter FREQ = 0,
    parameter string NAME = "uart0"
) (
    input  logic clk_i,
    input  logic rst_ni,
    output logic tx_o,
    input  logic rx_i
);
  assign tx_o = 1'b1;  // UART line idles high
endmodule
