// Copyright MOSAIC-SoC
// SPDX-License-Identifier: SHL-0.51
//
// boom_sci.sv — Standard Core Interface wrapper for BOOM v3 (RV64 OoO, SIM-ONLY).
//
// Wraps the SmallBoomV3 BoomTile extracted from chipyard 1.14.0 (CONFIG=
// MosaicRocketBoomConfig, see hw/vendor/mosaic/berkeley/) and converts its
// TileLink-C master port to a unified OBI master through the
// xheep_tilelink_to_obi window bridge:
//
//   code/data  0x8000_0000|x (cacheable DRAM in the tile's PMAs) -> SRAM x
//   sentinels  0x0200_0000+x (CLINT range, uncached device)      -> generated
//                                                                  shared base+x
//   soc_ctrl   0x0200_1000+x (CLINT range, uncached device)      -> 0x2000_0000+x
//   TDU        0x0C00_0000+x (PLIC range,  uncached device)      -> 0x200A_0000+x
//
// The tile keeps its elaborated chipyard memory map; the windows make the
// shared sentinel/TDU state uncached BY CONSTRUCTION (same coherence trick
// as the CVA6 integration). The tile resets at 0x8000_0000|BOOT_ADDR via the
// MOSAIC-patched RESET_VECTOR parameter (upstream folds it to the bootrom
// hang address — see extract_tile_closure.py), so the hart program is
// linked & preloaded at the LOW address and fetched through the alias.
//
// BOOM is EXCLUDED from the GF180MCU tapeout — simulation only. The tile
// has no debug module here: debug_req_i accepted but tied off, debug
// interrupt tied 0. mhartid is cosmetic (chipyard-elaborated hartid width is
// 1 bit); only a singleton Berkeley TITAN is accepted by the config schema.

module boom_sci #(
    parameter logic [31:0] BOOT_ADDR        = 32'h00000180,
    parameter logic [63:0] CODE_WINDOW_SIZE = 64'h0000_8000,
    parameter logic [31:0] SENTINEL_DEST    = 32'h0000_3000
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
    input  logic        debug_req_i,  // unused — no debug module in the tile

    // OBI unified master port (all tile traffic via the TL bridge)
    output obi_pkg::obi_req_t  mem_req_o,
    input  obi_pkg::obi_resp_t mem_resp_i
);

    // TileLink port geometry of the extracted BoomTile (from the generated
    // port list: a32 d64 s3 k3 z4).
    localparam int unsigned TL_AW    = 32;
    localparam int unsigned TL_SZW   = 4;
    localparam int unsigned TL_SRCW  = 3;
    localparam int unsigned TL_SINKW = 3;

    // Reset-hold dormancy covers the tile AND the bridge, so a parked hart
    // holds no partial TileLink transaction when it wakes. The Chisel tile
    // uses an ACTIVE-HIGH synchronous reset — inverted here.
    logic core_rst_n;
    assign core_rst_n = rst_ni & fetch_enable_i;

    // ── TileLink wires between the tile and the bridge ──────────────────
    logic                 tl_a_valid, tl_a_ready;
    logic [2:0]           tl_a_opcode;
    logic [2:0]           tl_a_param;
    logic [TL_SZW-1:0]    tl_a_size;
    logic [TL_SRCW-1:0]   tl_a_source;
    logic [TL_AW-1:0]     tl_a_address;
    logic [7:0]           tl_a_mask;
    logic [63:0]          tl_a_data;
    logic                 tl_c_valid, tl_c_ready;
    logic [2:0]           tl_c_opcode;
    logic [2:0]           tl_c_param;
    logic [TL_SZW-1:0]    tl_c_size;
    logic [TL_SRCW-1:0]   tl_c_source;
    logic [TL_AW-1:0]     tl_c_address;
    logic [63:0]          tl_c_data;
    logic                 tl_d_valid, tl_d_ready;
    logic [2:0]           tl_d_opcode;
    logic [1:0]           tl_d_param;
    logic [TL_SZW-1:0]    tl_d_size;
    logic [TL_SRCW-1:0]   tl_d_source;
    logic [TL_SINKW-1:0]  tl_d_sink;
    logic                 tl_d_denied;
    logic [63:0]          tl_d_data;
    logic                 tl_d_corrupt;
    logic                 tl_e_valid, tl_e_ready;
    logic [TL_SINKW-1:0]  tl_e_sink;

    // ── the extracted tile (port names from the generated BoomTile.sv) ─
    BoomTile #(
        // MOSAIC-patched parameter: boot from the cacheable DRAM alias
        .RESET_VECTOR({8'h00, 32'h8000_0000 | BOOT_ADDR})
    ) i_tile (
        .clock                           (clk_i),
        .reset                           (~core_rst_n),
        // TL-C master port (A/C/D/E; B unused — the bridge never probes)
        .auto_buffer_out_a_ready         (tl_a_ready),
        .auto_buffer_out_a_valid         (tl_a_valid),
        .auto_buffer_out_a_bits_opcode   (tl_a_opcode),
        .auto_buffer_out_a_bits_param    (tl_a_param),
        .auto_buffer_out_a_bits_size     (tl_a_size),
        .auto_buffer_out_a_bits_source   (tl_a_source),
        .auto_buffer_out_a_bits_address  (tl_a_address),
        .auto_buffer_out_a_bits_mask     (tl_a_mask),
        .auto_buffer_out_a_bits_data     (tl_a_data),
        .auto_buffer_out_b_ready         (),
        .auto_buffer_out_b_valid         (1'b0),
        .auto_buffer_out_b_bits_opcode   (3'b0),
        .auto_buffer_out_b_bits_param    (2'b0),
        .auto_buffer_out_b_bits_size     ({TL_SZW{1'b0}}),
        .auto_buffer_out_b_bits_source   ({TL_SRCW{1'b0}}),
        .auto_buffer_out_b_bits_address  ({TL_AW{1'b0}}),
        .auto_buffer_out_b_bits_mask     (8'b0),
        .auto_buffer_out_b_bits_corrupt  (1'b0),
        .auto_buffer_out_c_ready         (tl_c_ready),
        .auto_buffer_out_c_valid         (tl_c_valid),
        .auto_buffer_out_c_bits_opcode   (tl_c_opcode),
        .auto_buffer_out_c_bits_param    (tl_c_param),
        .auto_buffer_out_c_bits_size     (tl_c_size),
        .auto_buffer_out_c_bits_source   (tl_c_source),
        .auto_buffer_out_c_bits_address  (tl_c_address),
        .auto_buffer_out_c_bits_data     (tl_c_data),
        .auto_buffer_out_d_ready         (tl_d_ready),
        .auto_buffer_out_d_valid         (tl_d_valid),
        .auto_buffer_out_d_bits_opcode   (tl_d_opcode),
        .auto_buffer_out_d_bits_param    (tl_d_param),
        .auto_buffer_out_d_bits_size     (tl_d_size),
        .auto_buffer_out_d_bits_source   (tl_d_source),
        .auto_buffer_out_d_bits_sink     (tl_d_sink),
        .auto_buffer_out_d_bits_denied   (tl_d_denied),
        .auto_buffer_out_d_bits_data     (tl_d_data),
        .auto_buffer_out_d_bits_corrupt  (tl_d_corrupt),
        .auto_buffer_out_e_ready         (tl_e_ready),
        .auto_buffer_out_e_valid         (tl_e_valid),
        .auto_buffer_out_e_bits_sink     (tl_e_sink),
        // interrupts (wiring verified in the generated IntXbar/core pins:
        // 0_0=debug, 1_0=msip, 1_1=mtip, 2_0=meip, 3_0=seip)
        .auto_int_local_in_0_0           (1'b0),
        .auto_int_local_in_1_0           (irq_i[3]),
        .auto_int_local_in_1_1           (irq_i[7]),
        .auto_int_local_in_2_0           (irq_i[11]),
        .auto_int_local_in_3_0           (1'b0),
        .auto_hartid_in                  (hart_id_i[0])
    );

    // ── TileLink -> OBI window bridge ────────────────────────────────────
    xheep_tilelink_to_obi #(
        .obi_req_t (obi_pkg::obi_req_t),
        .obi_resp_t(obi_pkg::obi_resp_t),
        .TL_AW     (TL_AW),
        .TL_SZW    (TL_SZW),
        .TL_SRCW   (TL_SRCW),
        .TL_SINKW  (TL_SINKW),
        .WIN_CODE_SIZE(CODE_WINDOW_SIZE),
        .WIN_SENT_DEST(SENTINEL_DEST)
    ) i_bridge (
        .clk_i         (clk_i),
        .rst_ni        (core_rst_n),
        .tl_a_valid_i  (tl_a_valid),
        .tl_a_ready_o  (tl_a_ready),
        .tl_a_opcode_i (tl_a_opcode),
        .tl_a_param_i  (tl_a_param),
        .tl_a_size_i   (tl_a_size),
        .tl_a_source_i (tl_a_source),
        .tl_a_address_i(tl_a_address),
        .tl_a_mask_i   (tl_a_mask),
        .tl_a_data_i   (tl_a_data),
        .tl_a_corrupt_i(1'b0),          // tile emits no a.corrupt
        .tl_b_valid_o  (),              // never probes; tile B tied off above
        .tl_b_ready_i  (1'b1),
        .tl_b_opcode_o (),
        .tl_b_param_o  (),
        .tl_b_size_o   (),
        .tl_b_source_o (),
        .tl_b_address_o(),
        .tl_c_valid_i  (tl_c_valid),
        .tl_c_ready_o  (tl_c_ready),
        .tl_c_opcode_i (tl_c_opcode),
        .tl_c_param_i  (tl_c_param),
        .tl_c_size_i   (tl_c_size),
        .tl_c_source_i (tl_c_source),
        .tl_c_address_i(tl_c_address),
        .tl_c_data_i   (tl_c_data),
        .tl_c_corrupt_i(1'b0),          // tile emits no c.corrupt
        .tl_d_valid_o  (tl_d_valid),
        .tl_d_ready_i  (tl_d_ready),
        .tl_d_opcode_o (tl_d_opcode),
        .tl_d_param_o  (tl_d_param),
        .tl_d_size_o   (tl_d_size),
        .tl_d_source_o (tl_d_source),
        .tl_d_sink_o   (tl_d_sink),
        .tl_d_denied_o (tl_d_denied),
        .tl_d_data_o   (tl_d_data),
        .tl_d_corrupt_o(tl_d_corrupt),
        .tl_e_valid_i  (tl_e_valid),
        .tl_e_ready_o  (tl_e_ready),
        .tl_e_sink_i   (tl_e_sink),
        .obi_req_o     (mem_req_o),
        .obi_resp_i    (mem_resp_i)
    );

    // Report "asleep" while parked so the TDU's CORE_STATUS reflects
    // un-woken workers (titan role: fetch_enable=1 -> never asleep).
    assign core_sleep_o = ~fetch_enable_i;

endmodule : boom_sci
