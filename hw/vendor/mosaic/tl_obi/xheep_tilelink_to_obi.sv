// Copyright 2026 MOSAIC-SoC contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// xheep_tilelink_to_obi.sv — TileLink-C (TL-C/TL-UH) manager -> x-heep OBI
// master bridge (64-bit TL data beats, 32-bit OBI), written for the extracted
// Rocket/BOOM tiles (chipyard 1.14.0).
//
// The tile keeps the memory map it was elaborated with (chipyard defaults),
// so this bridge also performs WINDOW TRANSLATION into the MOSAIC map:
//
//   window 0 "code"     0x8000_0000 (DRAM, cacheable in the tile's PMAs)
//                       -> OBI 0x0000_0000 (shared SRAM; worker code+data)
//   window 1 "sentinel" 0x0200_0000 (CLINT range, UNCACHED device)
//                       -> OBI 0x0000_3000 (TDU sentinel/result region)
//   window 2 "soc_ctrl" 0x0200_1000 (CLINT range, UNCACHED device)
//                       -> OBI 0x2000_0000 (x-heep soc_ctrl)
//   window 3 "tdu"      0x0C00_0000 (PLIC range, UNCACHED device)
//                       -> OBI 0x200A_0000 (the TDU itself)
//
// Cacheability is decided by the tile's elaborated PMAs, so control traffic
// through windows 1/2/3 is uncached BY CONSTRUCTION — the same
// coherence-by-address-map trick as the CVA6 integration (uncached D-side).
// Accesses outside all windows get a DENIED TileLink response.
//
// Message support (single outstanding transaction, C has priority over A):
//   A: Get, PutFullData, PutPartialData          (uncached loads/stores)
//      AcquireBlock, AcquirePerm                 (cacheline refills)
//      Intent                                    (answered HintAck, no-op)
//   C: Release, ReleaseData                      (voluntary writebacks)
//   D: AccessAck(Data), Grant(Data), ReleaseAck, HintAck
//   E: GrantAck                                  (always accepted)
//   B: never used — this manager NEVER probes (single coherent client).
//
// Arithmetic/Logical (uncached AMOs) are unsupported and assert-fatal in sim:
// nothing in the MOSAIC firmware performs AMOs to uncached windows.

module xheep_tilelink_to_obi #(
    parameter type obi_req_t  = logic,  // x-heep obi_pkg::obi_req_t
    parameter type obi_resp_t = logic,  // x-heep obi_pkg::obi_resp_t
    parameter int unsigned TL_AW    = 32,  // A/C address width of the tile port
    parameter int unsigned TL_SZW   = 4,   // size field width
    parameter int unsigned TL_SRCW  = 4,   // source id width
    parameter int unsigned TL_SINKW = 4,   // sink id width
    // Address windows: tile-view base/size -> OBI destination base
    parameter logic [63:0] WIN_CODE_BASE = 64'h8000_0000,
    parameter logic [63:0] WIN_CODE_SIZE = 64'h0000_8000,   // 32 KB SRAM
    parameter logic [31:0] WIN_CODE_DEST = 32'h0000_0000,
    parameter logic [63:0] WIN_SENT_BASE = 64'h0200_0000,   // CLINT range
    parameter logic [63:0] WIN_SENT_SIZE = 64'h0000_1000,
    parameter logic [31:0] WIN_SENT_DEST = 32'h0000_3000,
    parameter logic [63:0] WIN_SOC_BASE  = 64'h0200_1000,   // CLINT range
    parameter logic [63:0] WIN_SOC_SIZE  = 64'h0000_1000,
    parameter logic [31:0] WIN_SOC_DEST  = 32'h2000_0000,
    parameter logic [63:0] WIN_TDU_BASE  = 64'h0C00_0000,   // PLIC range
    parameter logic [63:0] WIN_TDU_SIZE  = 64'h0001_0000,
    parameter logic [31:0] WIN_TDU_DEST  = 32'h200A_0000
) (
    input  logic clk_i,
    input  logic rst_ni,

    // ── TileLink slave side (the tile's master port) ────────────────────
    // A channel (client -> us)
    input  logic                 tl_a_valid_i,
    output logic                 tl_a_ready_o,
    input  logic [2:0]           tl_a_opcode_i,
    input  logic [2:0]           tl_a_param_i,
    input  logic [TL_SZW-1:0]    tl_a_size_i,
    input  logic [TL_SRCW-1:0]   tl_a_source_i,
    input  logic [TL_AW-1:0]     tl_a_address_i,
    input  logic [7:0]           tl_a_mask_i,
    input  logic [63:0]          tl_a_data_i,
    input  logic                 tl_a_corrupt_i,
    // B channel (us -> client) — never probes, permanently idle
    output logic                 tl_b_valid_o,
    input  logic                 tl_b_ready_i,
    output logic [2:0]           tl_b_opcode_o,
    output logic [1:0]           tl_b_param_o,
    output logic [TL_SZW-1:0]    tl_b_size_o,
    output logic [TL_SRCW-1:0]   tl_b_source_o,
    output logic [TL_AW-1:0]     tl_b_address_o,
    // C channel (client -> us)
    input  logic                 tl_c_valid_i,
    output logic                 tl_c_ready_o,
    input  logic [2:0]           tl_c_opcode_i,
    input  logic [2:0]           tl_c_param_i,
    input  logic [TL_SZW-1:0]    tl_c_size_i,
    input  logic [TL_SRCW-1:0]   tl_c_source_i,
    input  logic [TL_AW-1:0]     tl_c_address_i,
    input  logic [63:0]          tl_c_data_i,
    input  logic                 tl_c_corrupt_i,
    // D channel (us -> client)
    output logic                 tl_d_valid_o,
    input  logic                 tl_d_ready_i,
    output logic [2:0]           tl_d_opcode_o,
    output logic [1:0]           tl_d_param_o,
    output logic [TL_SZW-1:0]    tl_d_size_o,
    output logic [TL_SRCW-1:0]   tl_d_source_o,
    output logic [TL_SINKW-1:0]  tl_d_sink_o,
    output logic                 tl_d_denied_o,
    output logic [63:0]          tl_d_data_o,
    output logic                 tl_d_corrupt_o,
    // E channel (client -> us)
    input  logic                 tl_e_valid_i,
    output logic                 tl_e_ready_o,
    input  logic [TL_SINKW-1:0]  tl_e_sink_i,

    // ── OBI master side ─────────────────────────────────────────────────
    output obi_req_t  obi_req_o,
    input  obi_resp_t obi_resp_i
);

  // TileLink opcodes
  localparam logic [2:0] A_PUTFULL      = 3'd0;
  localparam logic [2:0] A_PUTPARTIAL   = 3'd1;
  localparam logic [2:0] A_ARITHMETIC   = 3'd2;
  localparam logic [2:0] A_LOGICAL      = 3'd3;
  localparam logic [2:0] A_GET          = 3'd4;
  localparam logic [2:0] A_INTENT       = 3'd5;
  localparam logic [2:0] A_ACQUIREBLOCK = 3'd6;
  localparam logic [2:0] A_ACQUIREPERM  = 3'd7;
  localparam logic [2:0] C_RELEASE      = 3'd6;
  localparam logic [2:0] C_RELEASEDATA  = 3'd7;
  localparam logic [2:0] D_ACCESSACK     = 3'd0;
  localparam logic [2:0] D_ACCESSACKDATA = 3'd1;
  localparam logic [2:0] D_HINTACK       = 3'd2;
  localparam logic [2:0] D_GRANT         = 3'd4;
  localparam logic [2:0] D_GRANTDATA     = 3'd5;
  localparam logic [2:0] D_RELEASEACK    = 3'd6;
  // Acquire grow params / grant cap params
  localparam logic [2:0] GROW_NTOB = 3'd0;
  localparam logic [1:0] CAP_TOT   = 2'd0;
  localparam logic [1:0] CAP_TOB   = 2'd1;

  typedef enum logic [3:0] {
    IDLE,
    RD_OBI,    // issue the OBI read(s) of the current D data beat
    RD_SEND,   // drive the D data beat until d_ready
    WR_OBI,    // issue the OBI write(s) of the current A put beat
    WR_GETW,   // await the next A put beat of a multi-beat Put
    REL_OBI,   // issue the OBI writes of the current C ReleaseData beat
    REL_GETW,  // await the next C ReleaseData beat
    SEND_ACK,  // drive the no-data D response until d_ready
    WAIT_E     // await GrantAck after a Grant/GrantData
  } state_e;

  state_e state_q, state_d;

  logic [2:0]          a_op_q;      // accepted A opcode (or C, see is_rel_q)
  logic [TL_SZW-1:0]   size_q;
  logic [TL_SRCW-1:0]  source_q;
  logic [1:0]          cap_q;       // grant permission cap
  logic [31:0]         word_q;      // translated OBI address of current beat
  logic [3:0]          beats_q;     // beats remaining including current
  logic                two_lanes_q; // beat spans both 32-bit lanes (size>=3)
  logic                sub_q;       // current 32-bit lane (0 = low)
  logic                granted_q;   // OBI request accepted, awaiting rvalid
  logic                denied_q;    // window miss -> denied response
  logic                is_acq_q;    // response is Grant/GrantData (+WAIT_E)
  logic                is_rel_q;    // response is ReleaseAck
  logic                is_hint_q;   // response is HintAck
  logic                has_data_q;  // D response carries data beats
  logic [63:0]         rdata_q;     // D beat assembly
  logic [63:0]         wdata_q;     // current write beat
  logic [7:0]          wmask_q;

  // ── window translation ────────────────────────────────────────────────
  function automatic logic [32:0] xlate(input logic [TL_AW-1:0] a);
    logic [63:0] a64;
    a64 = 64'(a);
    if (a64 >= WIN_CODE_BASE && a64 < WIN_CODE_BASE + WIN_CODE_SIZE)
      return {1'b1, WIN_CODE_DEST + 32'(a64 - WIN_CODE_BASE)};
    if (a64 >= WIN_SENT_BASE && a64 < WIN_SENT_BASE + WIN_SENT_SIZE)
      return {1'b1, WIN_SENT_DEST + 32'(a64 - WIN_SENT_BASE)};
    if (a64 >= WIN_SOC_BASE && a64 < WIN_SOC_BASE + WIN_SOC_SIZE)
      return {1'b1, WIN_SOC_DEST + 32'(a64 - WIN_SOC_BASE)};
    if (a64 >= WIN_TDU_BASE && a64 < WIN_TDU_BASE + WIN_TDU_SIZE)
      return {1'b1, WIN_TDU_DEST + 32'(a64 - WIN_TDU_BASE)};
    return 33'd0;
  endfunction

  logic        a_hit;
  logic [31:0] a_dest;
  assign {a_hit, a_dest} = xlate(tl_a_address_i);
  logic        c_hit;
  logic [31:0] c_dest;
  assign {c_hit, c_dest} = xlate(tl_c_address_i);

  // beats in a transaction of size s: max(1, 2^s / 8)
  function automatic logic [3:0] n_beats(input logic [TL_SZW-1:0] s);
    return (s >= 3) ? 4'd1 << (s - 3) : 4'd1;
  endfunction

  // ── per-beat OBI lane bookkeeping (mirrors xheep_axi_burst_to_obi) ────
  logic [31:0] lane_addr;
  assign lane_addr = two_lanes_q ? ({word_q[31:3], 3'b000} | (sub_q ? 32'h4 : 32'h0))
                                 : {word_q[31:2], 2'b00};
  logic lane_sel;
  assign lane_sel = lane_addr[2];

  logic [3:0] strb_lo, strb_hi;
  assign strb_lo = wmask_q[3:0];
  assign strb_hi = wmask_q[7:4];
  // Write lane currently being issued: low half first if nonzero.
  logic wr_lane;
  assign wr_lane = (sub_q == 1'b0) ? ~|strb_lo : 1'b1;

  // Put/Release beats with two active halves need two OBI writes.
  logic wr_more;  // another OBI write pending in this beat after this one
  assign wr_more = !wr_lane && |strb_hi;

  always_comb begin
    state_d = state_q;

    tl_a_ready_o = 1'b0;
    tl_c_ready_o = 1'b0;
    tl_e_ready_o = 1'b1;

    tl_b_valid_o   = 1'b0;
    tl_b_opcode_o  = '0;
    tl_b_param_o   = '0;
    tl_b_size_o    = '0;
    tl_b_source_o  = '0;
    tl_b_address_o = '0;

    tl_d_valid_o   = 1'b0;
    tl_d_opcode_o  = D_ACCESSACK;
    tl_d_param_o   = '0;
    tl_d_size_o    = size_q;
    tl_d_source_o  = source_q;
    tl_d_sink_o    = '0;
    tl_d_denied_o  = denied_q;
    tl_d_data_o    = rdata_q;
    tl_d_corrupt_o = 1'b0;

    obi_req_o       = '0;
    obi_req_o.addr  = '0;
    obi_req_o.we    = 1'b0;
    obi_req_o.be    = 4'hF;
    obi_req_o.wdata = '0;

    unique case (state_q)
      IDLE: begin
        // C (writebacks/releases) must make progress ahead of new A requests.
        if (tl_c_valid_i) begin
          tl_c_ready_o = 1'b1;
          state_d = (tl_c_opcode_i == C_RELEASEDATA) ? REL_OBI : SEND_ACK;
        end else if (tl_a_valid_i) begin
          tl_a_ready_o = 1'b1;
          unique case (tl_a_opcode_i)
            A_GET, A_ACQUIREBLOCK:      state_d = RD_OBI;
            A_ACQUIREPERM, A_INTENT:    state_d = SEND_ACK;
            A_PUTFULL, A_PUTPARTIAL:    state_d = (tl_a_mask_i == '0)
                                                  ? ((n_beats(tl_a_size_i) == 4'd1) ? SEND_ACK : WR_GETW)
                                                  : WR_OBI;
            default:                    state_d = SEND_ACK;  // asserted below
          endcase
        end
      end

      // ── read path (Get / AcquireBlock) ──────────────────────────────
      RD_OBI: begin
        if (denied_q) begin
          state_d = RD_SEND;
        end else begin
          obi_req_o.req  = ~granted_q;
          obi_req_o.addr = lane_addr;
          if (obi_resp_i.rvalid) begin
            if (two_lanes_q && !sub_q) state_d = RD_OBI;  // high lane next
            else                       state_d = RD_SEND;
          end
        end
      end

      RD_SEND: begin
        tl_d_valid_o   = 1'b1;
        tl_d_opcode_o  = is_acq_q ? D_GRANTDATA : D_ACCESSACKDATA;
        tl_d_param_o   = is_acq_q ? cap_q : 2'b00;
        tl_d_corrupt_o = denied_q;  // denied data beats must be corrupt
        if (tl_d_ready_i)
          state_d = (beats_q != 4'd1) ? RD_OBI
                                      : (is_acq_q ? WAIT_E : IDLE);
      end

      // ── write path (PutFull / PutPartial) ───────────────────────────
      WR_OBI: begin
        if (denied_q) begin
          state_d = (beats_q != 4'd1) ? WR_GETW : SEND_ACK;
        end else begin
          obi_req_o.req   = ~granted_q;
          obi_req_o.we    = 1'b1;
          obi_req_o.addr  = {word_q[31:3], 3'b000} | (wr_lane ? 32'h4 : 32'h0);
          obi_req_o.be    = wr_lane ? strb_hi : strb_lo;
          obi_req_o.wdata = wr_lane ? wdata_q[63:32] : wdata_q[31:0];
          if (obi_resp_i.rvalid) begin
            if (wr_more)                state_d = WR_OBI;
            else if (beats_q != 4'd1)   state_d = WR_GETW;
            else                        state_d = SEND_ACK;
          end
        end
      end

      WR_GETW: begin
        tl_a_ready_o = 1'b1;  // burst beats: A is locked to this message
        if (tl_a_valid_i) begin
          if (tl_a_mask_i == '0)
            state_d = (beats_q != 4'd1) ? WR_GETW : SEND_ACK;
          else
            state_d = WR_OBI;
        end
      end

      // ── release path (ReleaseData writeback) ────────────────────────
      REL_OBI: begin
        if (denied_q) begin
          state_d = (beats_q != 4'd1) ? REL_GETW : SEND_ACK;
        end else begin
          obi_req_o.req   = ~granted_q;
          obi_req_o.we    = 1'b1;
          obi_req_o.addr  = {word_q[31:3], 3'b000} | (sub_q ? 32'h4 : 32'h0);
          obi_req_o.be    = 4'hF;  // C data beats are always full-width
          obi_req_o.wdata = sub_q ? wdata_q[63:32] : wdata_q[31:0];
          if (obi_resp_i.rvalid) begin
            if (!sub_q)                 state_d = REL_OBI;  // high half next
            else if (beats_q != 4'd1)   state_d = REL_GETW;
            else                        state_d = SEND_ACK;
          end
        end
      end

      REL_GETW: begin
        tl_c_ready_o = 1'b1;
        if (tl_c_valid_i) state_d = REL_OBI;
      end

      // ── no-data responses ────────────────────────────────────────────
      SEND_ACK: begin
        tl_d_valid_o  = 1'b1;
        tl_d_opcode_o = is_rel_q  ? D_RELEASEACK
                      : is_hint_q ? D_HINTACK
                      : is_acq_q  ? D_GRANT
                                  : D_ACCESSACK;
        tl_d_param_o  = is_acq_q ? cap_q : 2'b00;
        // ReleaseAck may not be denied by spec; harmless either way.
        if (tl_d_ready_i) state_d = is_acq_q ? WAIT_E : IDLE;
      end

      WAIT_E: begin
        if (tl_e_valid_i) state_d = IDLE;
      end

      default: state_d = IDLE;
    endcase
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q     <= IDLE;
      a_op_q      <= '0;
      size_q      <= '0;
      source_q    <= '0;
      cap_q       <= CAP_TOB;
      word_q      <= '0;
      beats_q     <= 4'd1;
      two_lanes_q <= 1'b0;
      sub_q       <= 1'b0;
      granted_q   <= 1'b0;
      denied_q    <= 1'b0;
      is_acq_q    <= 1'b0;
      is_rel_q    <= 1'b0;
      is_hint_q   <= 1'b0;
      has_data_q  <= 1'b0;
      rdata_q     <= '0;
      wdata_q     <= '0;
      wmask_q     <= '0;
    end else begin
      state_q <= state_d;

      // ── message accept ────────────────────────────────────────────
      if (state_q == IDLE) begin
        if (tl_c_ready_o && tl_c_valid_i) begin
          a_op_q      <= tl_c_opcode_i;
          size_q      <= tl_c_size_i;
          source_q    <= tl_c_source_i;
          word_q      <= c_dest;
          beats_q     <= n_beats(tl_c_size_i);
          two_lanes_q <= (tl_c_size_i >= 3);
          sub_q       <= 1'b0;
          denied_q    <= ~c_hit && (tl_c_opcode_i == C_RELEASEDATA);
          is_acq_q    <= 1'b0;
          is_rel_q    <= 1'b1;
          is_hint_q   <= 1'b0;
          has_data_q  <= 1'b0;
          wdata_q     <= tl_c_data_i;   // first ReleaseData beat rides accept
          wmask_q     <= 8'hFF;
        end else if (tl_a_ready_o && tl_a_valid_i) begin
          a_op_q      <= tl_a_opcode_i;
          size_q      <= tl_a_size_i;
          source_q    <= tl_a_source_i;
          word_q      <= a_dest;
          beats_q     <= n_beats(tl_a_size_i);
          two_lanes_q <= (tl_a_size_i >= 3);
          sub_q       <= 1'b0;
          denied_q    <= ~a_hit && (tl_a_opcode_i != A_INTENT);
          is_acq_q    <= (tl_a_opcode_i == A_ACQUIREBLOCK)
                       || (tl_a_opcode_i == A_ACQUIREPERM);
          is_rel_q    <= 1'b0;
          is_hint_q   <= (tl_a_opcode_i == A_INTENT);
          has_data_q  <= (tl_a_opcode_i == A_GET)
                       || (tl_a_opcode_i == A_ACQUIREBLOCK);
          cap_q       <= (tl_a_param_i == GROW_NTOB) ? CAP_TOB : CAP_TOT;
          wdata_q     <= tl_a_data_i;   // first Put beat rides accept
          wmask_q     <= tl_a_mask_i;
          // a strobeless FIRST Put beat is already consumed by this accept:
          // advance past it so WR_GETW awaits beat 2 with correct bookkeeping
          if ((tl_a_opcode_i == A_PUTFULL || tl_a_opcode_i == A_PUTPARTIAL)
              && tl_a_mask_i == '0 && n_beats(tl_a_size_i) != 4'd1) begin
            word_q  <= a_dest + 32'd8;
            beats_q <= n_beats(tl_a_size_i) - 4'd1;
          end
        end
      end

      // ── burst-beat accept ─────────────────────────────────────────
      if (state_q == WR_GETW && tl_a_valid_i) begin
        wdata_q <= tl_a_data_i;
        wmask_q <= tl_a_mask_i;
        sub_q   <= 1'b0;
        // a strobeless beat consumes no OBI access but advances the burst
        if (tl_a_mask_i == '0 && beats_q != 4'd1) begin
          word_q  <= word_q + 32'd8;
          beats_q <= beats_q - 4'd1;
        end
      end
      if (state_q == REL_GETW && tl_c_valid_i) begin
        wdata_q <= tl_c_data_i;
        sub_q   <= 1'b0;
      end

      // ── OBI transaction bookkeeping ───────────────────────────────
      if (state_q == RD_OBI || state_q == WR_OBI || state_q == REL_OBI) begin
        if (obi_req_o.req && obi_resp_i.gnt) granted_q <= 1'b1;
        if (obi_resp_i.rvalid) begin
          granted_q <= 1'b0;
          unique case (state_q)
            RD_OBI: begin
              if (lane_sel) rdata_q[63:32] <= obi_resp_i.rdata;
              else          rdata_q[31:0]  <= obi_resp_i.rdata;
              // narrow reads: make both lanes carry the addressed word
              if (!two_lanes_q) begin
                rdata_q[63:32] <= obi_resp_i.rdata;
                rdata_q[31:0]  <= obi_resp_i.rdata;
              end
              if (two_lanes_q && !sub_q) sub_q <= 1'b1;
            end
            WR_OBI:  if (wr_more) sub_q <= 1'b1;
            default: if (!sub_q)  sub_q <= 1'b1;  // REL_OBI low -> high half
          endcase
        end
      end

      // denied beats produce no OBI traffic — fabricate poisoned data
      if (state_q == RD_OBI && denied_q) rdata_q <= 64'hDEAD_BEEF_DEAD_BEEF;

      // ── beat advance ──────────────────────────────────────────────
      if (state_q == RD_SEND && tl_d_ready_i && beats_q != 4'd1) begin
        word_q  <= word_q + 32'd8;
        beats_q <= beats_q - 4'd1;
        sub_q   <= 1'b0;
      end
      if (state_q == WR_OBI && beats_q != 4'd1
          && ((denied_q) || (obi_resp_i.rvalid && !wr_more))) begin
        word_q  <= word_q + 32'd8;
        beats_q <= beats_q - 4'd1;
      end
      if (state_q == REL_OBI && beats_q != 4'd1
          && ((denied_q) || (obi_resp_i.rvalid && sub_q))) begin
        word_q  <= word_q + 32'd8;
        beats_q <= beats_q - 4'd1;
      end
    end
  end

`ifndef SYNTHESIS
  // pragma translate_off
  no_amo :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (tl_a_valid_i |-> (tl_a_opcode_i != A_ARITHMETIC
                         && tl_a_opcode_i != A_LOGICAL)))
  else $fatal(1, "xheep_tilelink_to_obi: uncached AMOs are not supported");
  no_probe_ack :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (tl_c_valid_i |-> (tl_c_opcode_i == C_RELEASE
                         || tl_c_opcode_i == C_RELEASEDATA)))
  else $fatal(1, "xheep_tilelink_to_obi: unexpected C opcode (we never probe)");
  max_size :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (tl_a_valid_i |-> (tl_a_size_i <= 6)))
  else $fatal(1, "xheep_tilelink_to_obi: transfers above 64 B are unsupported");
  no_corrupt_a :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      (tl_a_valid_i |-> !tl_a_corrupt_i))
  else $fatal(1, "xheep_tilelink_to_obi: corrupt A beats are unsupported");
  always @(posedge clk_i)
    if (rst_ni && tl_a_valid_i && tl_a_ready_o && tl_a_opcode_i == A_INTENT)
      $display("[%0t] xheep_tilelink_to_obi: Intent (prefetch hint) answered as no-op", $time);
  // pragma translate_on
`endif

endmodule : xheep_tilelink_to_obi
