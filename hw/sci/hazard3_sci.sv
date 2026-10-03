// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// hazard3_sci.sv — Standard Core Interface wrapper for Hazard3 (AHB-Lite).
//
// SCAFFOLDED by wrapper-smith (family ahb_split, confidence 1.00) from
// hazard3_cpu_2port and completed per the analysis TODO queue:
//   * port map wired to the real hazard3_cpu_2port header (63 ports)
//   * irq mapping: irq_i[11] -> irq (meip), irq_i[3] -> soft_irq (msip),
//     irq_i[7] -> timer_irq (mtip)  [NUM_IRQS=1]
//   * boot address: RESET_VECTOR/MTVEC_INIT parameters (hazard3_config.vh)
//   * EXTENSION_A=0 (no exclusives: d_hexokay tied 1, d_hexcl unused)
//   * reset is native active-low -> reset-hold dormancy needs no inversion
//
// Hazard3 (github.com/Wren6991/Hazard3 @ 8af99293, Apache-2.0) is the RP2350
// core: RV32IMC, 2-port AHB-Lite. TAPEOUT-ELIGIBLE (no sim-only marking).
//
// AHB-Lite -> OBI conversion (per port, single outstanding):
//   address phase : HTRANS[1] (NONSEQ/SEQ) accepted when our HREADY is high;
//                   burst beats are treated as independent accesses (legal —
//                   this subordinate controls HREADY)
//   data phase    : HREADY held LOW while the OBI transaction is in flight;
//                   HWDATA is stable for the whole stalled data phase, so it
//                   feeds OBI wdata directly (no capture register)
//   completion    : OBI rvalid -> one HREADY-high cycle with HRDATA = rdata;
//                   that same cycle carries the master's next address phase
//   byte enables  : HSIZE + HADDR[1:0] -> OBI be (writes; reads use 4'hF)
//   HRESP         : tied OKAY (OBI has no error channel — same policy as the
//                   AXI/TileLink bridges)

module hazard3_sci #(
    parameter logic [31:0] BOOT_ADDR = 32'h00000180
) (
    input logic clk_i,
    input logic rst_ni,

    // Core control
    input  logic [31:0] hart_id_i,
    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Interrupts (RISC-V mip bit layout: 3=MSIP, 7=MTIP, 11=MEIP)
    input  logic [31:0] irq_i,

    // Debug
    input  logic        debug_req_i,  // unused — DEBUG_SUPPORT=0

    // OBI split master ports
    output obi_pkg::obi_req_t  instr_req_o,
    input  obi_pkg::obi_resp_t instr_resp_i,
    output obi_pkg::obi_req_t  data_req_o,
    input  obi_pkg::obi_resp_t data_resp_i
);

    // Reset-hold dormancy: the core is held in reset until the TDU wake
    // (fetch_enable_i) — identical policy to every other SCI wrapper.
    // Hazard3's rst_n is native active-low: no inversion.
    logic core_rst_n;
    assign core_rst_n = rst_ni & fetch_enable_i;

    // ── AHB wires ────────────────────────────────────────────────────
    logic [31:0] i_haddr, d_haddr;
    logic        i_hwrite, d_hwrite;
    logic [1:0]  i_htrans, d_htrans;
    logic [2:0]  i_hsize, d_hsize;
    logic [31:0] i_hwdata, d_hwdata;
    logic [31:0] i_hrdata, d_hrdata;
    logic        i_hready, d_hready;
    logic        i_hresp, d_hresp;
    logic        pwrup_req;

    hazard3_cpu_2port #(
        .RESET_VECTOR (BOOT_ADDR),
        .MTVEC_INIT   (BOOT_ADDR),
        .EXTENSION_A  (0),          // no exclusives on the single-outstanding OBI
        .DEBUG_SUPPORT(0),
        .NUM_IRQS     (1)
    ) i_core (
        .clk                        (clk_i),
        .clk_always_on              (clk_i),
        .rst_n                      (core_rst_n),

        // power control: grant power-up immediately, ignore clock request
        .pwrup_req                  (pwrup_req),
        .pwrup_ack                  (pwrup_req),
        .clk_en                     (),
        .unblock_out                (),
        .unblock_in                 (1'b0),

        // I-port AHB
        .i_haddr                    (i_haddr),
        .i_hwrite                   (i_hwrite),
        .i_htrans                   (i_htrans),
        .i_hsize                    (i_hsize),
        .i_hburst                   (),
        .i_hprot                    (),
        .i_hmastlock                (),
        .i_hmaster                  (),
        .i_hready                   (i_hready),
        .i_hresp                    (i_hresp),
        .i_hwdata                   (i_hwdata),
        .i_hrdata                   (i_hrdata),

        // D-port AHB
        .d_haddr                    (d_haddr),
        .d_hwrite                   (d_hwrite),
        .d_htrans                   (d_htrans),
        .d_hsize                    (d_hsize),
        .d_hburst                   (),
        .d_hprot                    (),
        .d_hmastlock                (),
        .d_hmaster                  (),
        .d_hexcl                    (),
        .d_hready                   (d_hready),
        .d_hresp                    (d_hresp),
        .d_hexokay                  (1'b1),
        .d_hwdata                   (d_hwdata),
        .d_hrdata                   (d_hrdata),

        // fences complete immediately (no cache to sync)
        .fence_i_vld                (),
        .fence_d_vld                (),
        .fence_rdy                  (1'b1),

        // debug tied off (DEBUG_SUPPORT=0)
        .dbg_req_halt               (1'b0),
        .dbg_req_halt_on_reset      (1'b0),
        .dbg_req_resume             (1'b0),
        .dbg_halted                 (),
        .dbg_running                (),
        .dbg_data0_rdata            (32'b0),
        .dbg_data0_wdata            (),
        .dbg_data0_wen              (),
        .dbg_instr_data             (32'b0),
        .dbg_instr_data_vld         (1'b0),
        .dbg_instr_data_rdy         (),
        .dbg_instr_caught_exception (),
        .dbg_instr_caught_ebreak    (),
        .dbg_sbus_addr              (32'b0),
        .dbg_sbus_write             (1'b0),
        .dbg_sbus_size              (2'b0),
        .dbg_sbus_vld               (1'b0),
        .dbg_sbus_rdy               (),
        .dbg_sbus_err               (),
        .dbg_sbus_wdata             (32'b0),
        .dbg_sbus_rdata             (),

        .mhartid_val                (hart_id_i),
        .eco_version                (4'b0),

        // irq mapping per the SCI contract
        .irq                        (irq_i[11]),   // meip
        .soft_irq                   (irq_i[3]),    // msip
        .timer_irq                  (irq_i[7])     // mtip
    );

    // ── AHB-Lite -> OBI, one converter per port ──────────────────────

    function automatic logic [3:0] ahb_be(input logic [2:0] size,
                                          input logic [1:0] a);
        unique case (size[1:0])
            2'b00:   return 4'b0001 << a;                 // byte
            2'b01:   return a[1] ? 4'b1100 : 4'b0011;     // half
            default: return 4'b1111;                      // word
        endcase
    endfunction

    // ---- instruction port ----
    logic        i_busy_q, i_granted_q;
    logic [31:0] i_addr_q;
    logic        i_write_q;
    logic [2:0]  i_size_q;

    assign i_hready = ~i_busy_q | instr_resp_i.rvalid;
    assign i_hresp  = 1'b0;
    assign i_hrdata = instr_resp_i.rdata;

    assign instr_req_o.req   = i_busy_q & ~i_granted_q & fetch_enable_i;
    assign instr_req_o.addr  = {i_addr_q[31:2], 2'b00};
    assign instr_req_o.we    = i_write_q;
    assign instr_req_o.be    = i_write_q ? ahb_be(i_size_q, i_addr_q[1:0]) : 4'hF;
    assign instr_req_o.wdata = i_hwdata;  // stable during the stalled data phase

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            i_busy_q    <= 1'b0;
            i_granted_q <= 1'b0;
            i_addr_q    <= '0;
            i_write_q   <= 1'b0;
            i_size_q    <= '0;
        end else begin
            if (instr_req_o.req && instr_resp_i.gnt) i_granted_q <= 1'b1;
            if (instr_resp_i.rvalid) begin
                i_busy_q    <= 1'b0;
                i_granted_q <= 1'b0;
            end
            // accept an address phase whenever HREADY is high
            if (i_hready && i_htrans[1] && core_rst_n) begin
                i_busy_q  <= 1'b1;
                i_addr_q  <= i_haddr;
                i_write_q <= i_hwrite;
                i_size_q  <= i_hsize;
            end
        end
    end

    // ---- data port ----
    logic        d_busy_q, d_granted_q;
    logic [31:0] d_addr_q;
    logic        d_write_q;
    logic [2:0]  d_size_q;

    assign d_hready = ~d_busy_q | data_resp_i.rvalid;
    assign d_hresp  = 1'b0;
    assign d_hrdata = data_resp_i.rdata;

    assign data_req_o.req   = d_busy_q & ~d_granted_q & fetch_enable_i;
    assign data_req_o.addr  = {d_addr_q[31:2], 2'b00};
    assign data_req_o.we    = d_write_q;
    assign data_req_o.be    = d_write_q ? ahb_be(d_size_q, d_addr_q[1:0]) : 4'hF;
    assign data_req_o.wdata = d_hwdata;

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            d_busy_q    <= 1'b0;
            d_granted_q <= 1'b0;
            d_addr_q    <= '0;
            d_write_q   <= 1'b0;
            d_size_q    <= '0;
        end else begin
            if (data_req_o.req && data_resp_i.gnt) d_granted_q <= 1'b1;
            if (data_resp_i.rvalid) begin
                d_busy_q    <= 1'b0;
                d_granted_q <= 1'b0;
            end
            if (d_hready && d_htrans[1] && core_rst_n) begin
                d_busy_q  <= 1'b1;
                d_addr_q  <= d_haddr;
                d_write_q <= d_hwrite;
                d_size_q  <= d_hsize;
            end
        end
    end

    // No exported sleep state used: report "asleep" while parked so the
    // TDU's CORE_STATUS reflects un-woken workers.
    assign core_sleep_o = ~fetch_enable_i;

endmodule : hazard3_sci
