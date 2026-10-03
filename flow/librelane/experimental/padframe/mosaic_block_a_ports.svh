// ---------------------------------------------------------------------------
// GENERATED PORT LIST -- do not hand-edit.
//
//   source : D15_A_interface.yaml
//   variant: A   die 1110 x 1110 um   167 terminals
//
// Regenerate with padframe/gen_wrapper_ports.py when the integrator reissues
// the DEF. The port names must match D15_A.def exactly, because
// Odb.ApplyDEFTemplate runs in strict mode and requires identical pin sets.
// ---------------------------------------------------------------------------

// --- ports ---
    inout  wire          VDD,
    inout  wire          VSS,
    input  logic         boot_select_i,
    output logic         boot_select_i_PD,
    output logic         boot_select_i_PU,
    input  logic         clk_i,
    output logic         clk_i_PD,
    output logic         clk_i_PU,
    input  logic         execute_from_flash_i,
    output logic         execute_from_flash_i_PD,
    output logic         execute_from_flash_i_PU,
    input  logic         rst_ni,
    output logic         rst_ni_PD,
    output logic         rst_ni_PU,
    output logic         spi_flash_cs_o_CS,
    output logic         spi_flash_cs_o_IE,
    input  logic         spi_flash_cs_o_IN,
    output logic         spi_flash_cs_o_OE,
    output logic         spi_flash_cs_o_OUT,
    output logic         spi_flash_cs_o_PD,
    output logic         spi_flash_cs_o_PDRV0,
    output logic         spi_flash_cs_o_PDRV1,
    output logic         spi_flash_cs_o_PU,
    output logic         spi_flash_cs_o_SL,
    output logic         spi_flash_sck_o_CS,
    output logic         spi_flash_sck_o_IE,
    input  logic         spi_flash_sck_o_IN,
    output logic         spi_flash_sck_o_OE,
    output logic         spi_flash_sck_o_OUT,
    output logic         spi_flash_sck_o_PD,
    output logic         spi_flash_sck_o_PDRV0,
    output logic         spi_flash_sck_o_PDRV1,
    output logic         spi_flash_sck_o_PU,
    output logic         spi_flash_sck_o_SL,
    output logic   [3:0] spi_flash_sd_io_CS,
    output logic   [3:0] spi_flash_sd_io_IE,
    input  logic   [3:0] spi_flash_sd_io_IN,
    output logic   [3:0] spi_flash_sd_io_OE,
    output logic   [3:0] spi_flash_sd_io_OUT,
    output logic   [3:0] spi_flash_sd_io_PD,
    output logic   [3:0] spi_flash_sd_io_PDRV0,
    output logic   [3:0] spi_flash_sd_io_PDRV1,
    output logic   [3:0] spi_flash_sd_io_PU,
    output logic   [3:0] spi_flash_sd_io_SL,
    output logic   [6:0] status_o_CS,
    output logic   [6:0] status_o_IE,
    input  logic   [6:0] status_o_IN,
    output logic   [6:0] status_o_OE,
    output logic   [6:0] status_o_OUT,
    output logic   [6:0] status_o_PD,
    output logic   [6:0] status_o_PDRV0,
    output logic   [6:0] status_o_PDRV1,
    output logic   [6:0] status_o_PU,
    output logic   [6:0] status_o_SL,
    output logic         status_valid_o_CS,
    output logic         status_valid_o_IE,
    input  logic         status_valid_o_IN,
    output logic         status_valid_o_OE,
    output logic         status_valid_o_OUT,
    output logic         status_valid_o_PD,
    output logic         status_valid_o_PDRV0,
    output logic         status_valid_o_PDRV1,
    output logic         status_valid_o_PU,
    output logic         status_valid_o_SL,
    input  logic         uart_rx_i,
    output logic         uart_rx_i_PD,
    output logic         uart_rx_i_PU,
    output logic         uart_tx_o_CS,
    output logic         uart_tx_o_IE,
    input  logic         uart_tx_o_IN,
    output logic         uart_tx_o_OE,
    output logic         uart_tx_o_OUT,
    output logic         uart_tx_o_PD,
    output logic         uart_tx_o_PDRV0,
    output logic         uart_tx_o_PDRV1,
    output logic         uart_tx_o_PU,
    output logic         uart_tx_o_SL,

// --- constant pad controls ---
//
// The QSPI OE/IE are absent here on purpose: they carry the core's own
// output enable and its inverse. IE=1 with OE=1 is Disallowed in the PDK
// control table, so IE follows ~OE rather than being tied high.
  assign boot_select_i_PD = 1'b0;
  assign boot_select_i_PU = 1'b0;
  assign clk_i_PD = 1'b0;
  assign clk_i_PU = 1'b0;
  assign execute_from_flash_i_PD = 1'b0;
  assign execute_from_flash_i_PU = 1'b0;
  assign rst_ni_PD = 1'b1;
  assign rst_ni_PU = 1'b0;
  assign spi_flash_cs_o_CS = 1'b0;
  assign spi_flash_cs_o_IE = 1'b0;
  assign spi_flash_cs_o_OE = 1'b1;
  assign spi_flash_cs_o_PD = 1'b0;
  assign spi_flash_cs_o_PDRV0 = 1'b0;
  assign spi_flash_cs_o_PDRV1 = 1'b1;
  assign spi_flash_cs_o_PU = 1'b0;
  assign spi_flash_cs_o_SL = 1'b0;
  assign spi_flash_sck_o_CS = 1'b0;
  assign spi_flash_sck_o_IE = 1'b0;
  assign spi_flash_sck_o_OE = 1'b1;
  assign spi_flash_sck_o_PD = 1'b0;
  assign spi_flash_sck_o_PDRV0 = 1'b0;
  assign spi_flash_sck_o_PDRV1 = 1'b1;
  assign spi_flash_sck_o_PU = 1'b0;
  assign spi_flash_sck_o_SL = 1'b0;
  assign spi_flash_sd_io_CS = {4{1'b0}};
  assign spi_flash_sd_io_PD = {4{1'b0}};
  assign spi_flash_sd_io_PDRV0 = {4{1'b0}};
  assign spi_flash_sd_io_PDRV1 = {4{1'b1}};
  assign spi_flash_sd_io_PU = {4{1'b0}};
  assign spi_flash_sd_io_SL = {4{1'b0}};
  assign status_o_CS = {7{1'b0}};
  assign status_o_IE = {7{1'b0}};
  assign status_o_OE = {7{1'b1}};
  assign status_o_PD = {7{1'b0}};
  assign status_o_PDRV0 = {7{1'b0}};
  assign status_o_PDRV1 = {7{1'b1}};
  assign status_o_PU = {7{1'b0}};
  assign status_o_SL = {7{1'b0}};
  assign status_valid_o_CS = 1'b0;
  assign status_valid_o_IE = 1'b0;
  assign status_valid_o_OE = 1'b1;
  assign status_valid_o_PD = 1'b0;
  assign status_valid_o_PDRV0 = 1'b0;
  assign status_valid_o_PDRV1 = 1'b1;
  assign status_valid_o_PU = 1'b0;
  assign status_valid_o_SL = 1'b0;
  assign uart_rx_i_PD = 1'b0;
  assign uart_rx_i_PU = 1'b0;
  assign uart_tx_o_CS = 1'b0;
  assign uart_tx_o_IE = 1'b0;
  assign uart_tx_o_OE = 1'b1;
  assign uart_tx_o_PD = 1'b0;
  assign uart_tx_o_PDRV0 = 1'b0;
  assign uart_tx_o_PDRV1 = 1'b1;
  assign uart_tx_o_PU = 1'b0;
  assign uart_tx_o_SL = 1'b0;
