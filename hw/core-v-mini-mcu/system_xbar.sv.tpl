// Copyright 2022 OpenHW Group
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

<%
    memory_ss = xheep.memory_ss()
    is_log = xheep.bus_type().value == "LOG"
    is_floonoc = xheep.bus_type().value == "FLOONOC"
    if is_log:
        _opts = xheep.get_extension("bus_opts") or {}
        log_topo = {"lic": "LIC", "bfly2": "BFLY2", "bfly4": "BFLY4"}[
            _opts.get("log", {}).get("topology", "lic")
        ]
        _first_bank = next(iter(memory_ss.iter_ram_banks()))
        # word-address bits per bank (bank sizes are uniform for the LOG bus)
        bank_addr_width = (_first_bank.size() // 4 - 1).bit_length()
    if is_floonoc:
        nh = xheep.num_harts()
%>

module system_xbar
  import obi_pkg::*;
  import addr_map_rule_pkg::*;
  import core_v_mini_mcu_pkg::*;
#(
    parameter core_v_mini_mcu_pkg::bus_type_e BUS_TYPE = core_v_mini_mcu_pkg::BusType,
    parameter XBAR_NMASTER = 3,
    parameter XBAR_NSLAVE = 6,
    localparam int unsigned IdxWidth = cf_math_pkg::idx_width(XBAR_NSLAVE)
) (
    input logic clk_i,
    input logic rst_ni,

    // Address map
    input addr_map_rule_pkg::addr_map_rule_t [XBAR_NSLAVE-1:0] addr_map_i,

    // Default slave index
    input logic [IdxWidth-1:0] default_idx_i,

    input  obi_req_t  [XBAR_NMASTER-1:0] master_req_i,
    output obi_resp_t [XBAR_NMASTER-1:0] master_resp_o,

    output obi_req_t  [XBAR_NSLAVE-1:0] slave_req_o,
    input  obi_resp_t [XBAR_NSLAVE-1:0] slave_resp_i

);

  localparam int unsigned LOG_XBAR_NMASTER = XBAR_NMASTER > 1 ? $clog2(XBAR_NMASTER) : 32'd1;
  localparam int unsigned LOG_XBAR_NSLAVE = XBAR_NSLAVE > 1 ? $clog2(XBAR_NSLAVE) : 32'd1;

  //Aggregated Request Data (from Master -> slaves)
  //WE + BE + ADDR + WDATA
  localparam int unsigned REQ_AGG_DATA_WIDTH = 1 + 4 + 32 + 32;
  localparam int unsigned RESP_AGG_DATA_WIDTH = 32;

% if is_log:
  // ──────────────────────────────────────────────────────────────────
  // LOG bus: two-tier fabric
  //   memory tier:     tcdm_interconnect (logarithmic interconnect,
  //                    word-interleaved, fixed 1-cycle response, one
  //                    port per RAM bank, per-bank RR arbitration)
  //   non-memory tier: variable-latency crossbar over
  //                    ERROR/DEBUG/AO_PERIPHERAL/PERIPHERAL/FLASH
  // Each master is split between the tiers by a 1-rule demux on
  // [0, MEM_SIZE); the demux enforces one outstanding transaction per
  // master across tiers (same primitive as the int/ext demux in
  // system_bus).
  // ──────────────────────────────────────────────────────────────────

  localparam int unsigned NumBanksLog2 = $clog2(NUM_BANKS);
  localparam int unsigned BankAddrWidth = ${bank_addr_width};

  // addr_map_i/default_idx_i drive the single-crossbar fabrics only; the
  // LOG tiers use the package tier rules instead.
  logic unused_addr_map;
  assign unused_addr_map = ^{addr_map_i, default_idx_i, 1'b0};

  // Per-master tier demux (slave 1 = memory tier, slave 0 = non-memory)
  obi_req_t  [XBAR_NMASTER-1:0][1:0] tier_req;
  obi_resp_t [XBAR_NMASTER-1:0][1:0] tier_resp;

  for (genvar i = 0; unsigned'(i) < XBAR_NMASTER; i++) begin : gen_tier_demux
    xbar_varlat_one_to_n #(
        .XBAR_NSLAVE(32'd2),
        .NUM_RULES  (32'd1)
    ) tier_demux_i (
        .clk_i        (clk_i),
        .rst_ni       (rst_ni),
        .addr_map_i   (LOG_MEM_TIER_RULES),
        .default_idx_i(TIER_NONMEM_IDX[0:0]),
        .master_req_i (master_req_i[i]),
        .master_resp_o(master_resp_o[i]),
        .slave_req_o  (tier_req[i]),
        .slave_resp_i (tier_resp[i])
    );
  end

  // ── Memory tier: OBI -> TCDM shim (1:1 signal map) ──
  logic [XBAR_NMASTER-1:0]        tcdm_req;
  logic [XBAR_NMASTER-1:0][31:0]  tcdm_add;
  logic [XBAR_NMASTER-1:0]        tcdm_wen;
  logic [XBAR_NMASTER-1:0][31:0]  tcdm_wdata;
  logic [XBAR_NMASTER-1:0][ 3:0]  tcdm_be;
  logic [XBAR_NMASTER-1:0]        tcdm_gnt;
  logic [XBAR_NMASTER-1:0]        tcdm_vld;
  logic [XBAR_NMASTER-1:0][31:0]  tcdm_rdata;

  for (genvar i = 0; unsigned'(i) < XBAR_NMASTER; i++) begin : gen_mem_tier_shim
    assign tcdm_req[i]   = tier_req[i][TIER_MEM_IDX[0]].req;
    assign tcdm_add[i]   = tier_req[i][TIER_MEM_IDX[0]].addr;
    assign tcdm_wen[i]   = tier_req[i][TIER_MEM_IDX[0]].we;
    assign tcdm_be[i]    = tier_req[i][TIER_MEM_IDX[0]].be;
    assign tcdm_wdata[i] = tier_req[i][TIER_MEM_IDX[0]].wdata;
    assign tier_resp[i][TIER_MEM_IDX[0]].gnt    = tcdm_gnt[i];
    assign tier_resp[i][TIER_MEM_IDX[0]].rvalid = tcdm_vld[i];
    assign tier_resp[i][TIER_MEM_IDX[0]].rdata  = tcdm_rdata[i];
  end

  // Bank side of the logarithmic interconnect
  logic [NUM_BANKS-1:0]                    bank_req;
  logic [NUM_BANKS-1:0]                    bank_gnt;
  logic [NUM_BANKS-1:0][BankAddrWidth-1:0] bank_add;
  logic [NUM_BANKS-1:0]                    bank_wen;
  logic [NUM_BANKS-1:0][31:0]              bank_wdata;
  logic [NUM_BANKS-1:0][ 3:0]              bank_be;
  logic [NUM_BANKS-1:0][31:0]              bank_rdata;

  tcdm_interconnect #(
      .NumIn       (XBAR_NMASTER),
      .NumOut      (NUM_BANKS),
      .AddrWidth   (32),
      .DataWidth   (32),
      .AddrMemWidth(BankAddrWidth),
      .WriteRespOn (1'b1),
      .RespLat     (1),
      .Topology    (tcdm_interconnect_pkg::${log_topo})
  ) tcdm_interconnect_i (
      .clk_i  (clk_i),
      .rst_ni (rst_ni),
      .req_i  (tcdm_req),
      .add_i  (tcdm_add),
      .wen_i  (tcdm_wen),
      .wdata_i(tcdm_wdata),
      .be_i   (tcdm_be),
      .gnt_o  (tcdm_gnt),
      .vld_o  (tcdm_vld),
      .rdata_o(tcdm_rdata),
      .req_o  (bank_req),
      .gnt_i  (bank_gnt),
      .add_o  (bank_add),
      .wen_o  (bank_wen),
      .wdata_o(bank_wdata),
      .be_o   (bank_be),
      .rdata_i(bank_rdata)
  );

  // Reconstruct full OBI addresses for the banks: memory_subsystem extracts
  // the word address as addr[BankAddrWidth+NumBanksLog2+2-1 : NumBanksLog2+2]
  // for interleaved banks, which is exactly add_o shifted back up.
% for k, bank in enumerate(memory_ss.iter_ram_banks()):
  assign slave_req_o[RAM${bank.name()}_IDX].req   = bank_req[${k}];
  assign slave_req_o[RAM${bank.name()}_IDX].we    = bank_wen[${k}];
  assign slave_req_o[RAM${bank.name()}_IDX].be    = bank_be[${k}];
  assign slave_req_o[RAM${bank.name()}_IDX].addr  = 32'(bank_add[${k}]) << (NumBanksLog2 + 32'd2);
  assign slave_req_o[RAM${bank.name()}_IDX].wdata = bank_wdata[${k}];
  assign bank_gnt[${k}]   = slave_resp_i[RAM${bank.name()}_IDX].gnt;
  assign bank_rdata[${k}] = slave_resp_i[RAM${bank.name()}_IDX].rdata;
% endfor

  // ── Non-memory tier: addr_decode + variable-latency crossbar ──
  localparam int unsigned NonmemIdxWidth = cf_math_pkg::idx_width(NONMEM_TIER_NSLAVE);

  logic [XBAR_NMASTER-1:0][NonmemIdxWidth-1:0] nonmem_port_sel;
  logic [XBAR_NMASTER-1:0]                          nonmem_req;
  logic [XBAR_NMASTER-1:0][REQ_AGG_DATA_WIDTH-1:0]  nonmem_req_data;
  logic [XBAR_NMASTER-1:0]                          nonmem_gnt;
  logic [XBAR_NMASTER-1:0]                          nonmem_vld;
  logic [XBAR_NMASTER-1:0][31:0]                    nonmem_rdata;

  logic [NONMEM_TIER_NSLAVE-1:0]                         nonmem_out_req;
  logic [NONMEM_TIER_NSLAVE-1:0]                         nonmem_out_gnt;
  logic [NONMEM_TIER_NSLAVE-1:0]                         nonmem_out_vld;
  logic [NONMEM_TIER_NSLAVE-1:0][REQ_AGG_DATA_WIDTH-1:0] nonmem_out_data;
  logic [NONMEM_TIER_NSLAVE-1:0][31:0]                   nonmem_out_rdata;

  for (genvar i = 0; unsigned'(i) < XBAR_NMASTER; i++) begin : gen_nonmem_tier
    addr_decode #(
        .NoIndices(NONMEM_TIER_NSLAVE),
        .NoRules  (NONMEM_TIER_NSLAVE),
        .addr_t   (logic [31:0]),
        .rule_t   (addr_map_rule_pkg::addr_map_rule_t)
    ) nonmem_addr_decode_i (
        .addr_i          (tier_req[i][TIER_NONMEM_IDX[0]].addr),
        .addr_map_i      (NONMEM_TIER_RULES),
        .idx_o           (nonmem_port_sel[i]),
        .dec_valid_o     (),
        .dec_error_o     (),
        .en_default_idx_i(1'b1),
        .default_idx_i   (NONMEM_ERROR_IDX[NonmemIdxWidth-1:0])
    );

    assign nonmem_req[i] = tier_req[i][TIER_NONMEM_IDX[0]].req;
    assign nonmem_req_data[i] = {
      tier_req[i][TIER_NONMEM_IDX[0]].we,
      tier_req[i][TIER_NONMEM_IDX[0]].be,
      tier_req[i][TIER_NONMEM_IDX[0]].addr,
      tier_req[i][TIER_NONMEM_IDX[0]].wdata
    };
    assign tier_resp[i][TIER_NONMEM_IDX[0]].gnt    = nonmem_gnt[i];
    assign tier_resp[i][TIER_NONMEM_IDX[0]].rvalid = nonmem_vld[i];
    assign tier_resp[i][TIER_NONMEM_IDX[0]].rdata  = nonmem_rdata[i];
  end

  xbar_varlat #(
      .AggregateGnt (0),
      .NumIn        (XBAR_NMASTER),
      .NumOut       (NONMEM_TIER_NSLAVE),
      .ReqDataWidth (REQ_AGG_DATA_WIDTH),
      .RespDataWidth(RESP_AGG_DATA_WIDTH)
  ) nonmem_xbar_i (
      .clk_i  (clk_i),
      .rst_ni (rst_ni),
      .req_i  (nonmem_req),
      .add_i  (nonmem_port_sel),
      .wdata_i(nonmem_req_data),
      .gnt_o  (nonmem_gnt),
      .rdata_o(nonmem_rdata),
      .rr_i   ('0),
      .vld_o  (nonmem_vld),
      .gnt_i  (nonmem_out_gnt),
      .req_o  (nonmem_out_req),
      .vld_i  (nonmem_out_vld),
      .wdata_o(nonmem_out_data),
      .rdata_i(nonmem_out_rdata)
  );

  // Tier-local slave ports -> global slave indices
% for tier_idx, slave in enumerate(("ERROR", "DEBUG", "AO_PERIPHERAL", "PERIPHERAL", "FLASH_MEM")):
  assign slave_req_o[${slave}_IDX].req = nonmem_out_req[${tier_idx}];
  assign {slave_req_o[${slave}_IDX].we,
          slave_req_o[${slave}_IDX].be,
          slave_req_o[${slave}_IDX].addr,
          slave_req_o[${slave}_IDX].wdata} = nonmem_out_data[${tier_idx}];
  assign nonmem_out_gnt[${tier_idx}]   = slave_resp_i[${slave}_IDX].gnt;
  assign nonmem_out_vld[${tier_idx}]   = slave_resp_i[${slave}_IDX].rvalid;
  assign nonmem_out_rdata[${tier_idx}] = slave_resp_i[${slave}_IDX].rdata;
% endfor

% elif is_floonoc:
  // ──────────────────────────────────────────────────────────────────
  // FLOONOC bus: floogen-generated AXI NoC + OBI<->AXI bridges
  //   managers:     per-hart instr+data merged (n-to-one) -> obi_to_axi
  //                 -> hart<N> chimney; debug+DMA(+EXT) merged -> `shared`
  //   subordinates: `mem` chimney -> axi_to_obi -> 1-to-NUM_BANKS demux;
  //                 `periph` chimney -> axi_to_obi -> 1-to-5 demux
  // The fabric (floo_mosaic_noc) is generated by floogen from the same
  // topology (util/xheep_gen/floonoc_gen.py).
  // ──────────────────────────────────────────────────────────────────
  import floo_mosaic_noc_pkg::*;

  localparam int unsigned NumHarts = ${nh};
  localparam int unsigned NumSharedMasters = XBAR_NMASTER - 2 * NumHarts;

  // addr_map_i/default_idx_i drive the single-crossbar fabrics only; the
  // NoC uses its generated system address map (SAM) + the tier rules.
  logic unused_addr_map;
  assign unused_addr_map = ^{addr_map_i, default_idx_i, 1'b0};

  // ── Manager side: merge OBI ports, bridge to AXI, enter the NoC ──
  obi_req_t  [NumHarts:0] mgr_obi_req;   // [h] = hart h (I+D), [NumHarts] = shared
  obi_resp_t [NumHarts:0] mgr_obi_resp;

  floo_mosaic_noc_pkg::axi_axi_in_req_t [NumHarts:0] mgr_axi_req;
  floo_mosaic_noc_pkg::axi_axi_in_rsp_t [NumHarts:0] mgr_axi_rsp;

  for (genvar h = 0; unsigned'(h) < NumHarts; h++) begin : gen_hart_merge
    xbar_varlat_n_to_one #(
        .XBAR_NMASTER(32'd2)
    ) hart_merge_i (
        .clk_i        (clk_i),
        .rst_ni       (rst_ni),
        .master_req_i (master_req_i[2*h+:2]),
        .master_resp_o(master_resp_o[2*h+:2]),
        .slave_req_o  (mgr_obi_req[h]),
        .slave_resp_i (mgr_obi_resp[h])
    );
  end

  xbar_varlat_n_to_one #(
      .XBAR_NMASTER(NumSharedMasters)
  ) shared_merge_i (
      .clk_i        (clk_i),
      .rst_ni       (rst_ni),
      .master_req_i (master_req_i[XBAR_NMASTER-1:2*NumHarts]),
      .master_resp_o(master_resp_o[XBAR_NMASTER-1:2*NumHarts]),
      .slave_req_o  (mgr_obi_req[NumHarts]),
      .slave_resp_i (mgr_obi_resp[NumHarts])
  );

  for (genvar m = 0; unsigned'(m) <= NumHarts; m++) begin : gen_mgr_bridge
    xheep_obi_to_axi #(
        .obi_req_t (obi_pkg::obi_req_t),
        .obi_resp_t(obi_pkg::obi_resp_t),
        .axi_req_t (floo_mosaic_noc_pkg::axi_axi_in_req_t),
        .axi_resp_t(floo_mosaic_noc_pkg::axi_axi_in_rsp_t)
    ) obi_to_axi_i (
        .clk_i     (clk_i),
        .rst_ni    (rst_ni),
        .obi_req_i (mgr_obi_req[m]),
        .obi_resp_o(mgr_obi_resp[m]),
        .axi_req_o (mgr_axi_req[m]),
        .axi_resp_i(mgr_axi_rsp[m])
    );
  end

  // ── The NoC ──
  floo_mosaic_noc_pkg::axi_axi_out_req_t mem_axi_req, periph_axi_req;
  floo_mosaic_noc_pkg::axi_axi_out_rsp_t mem_axi_rsp, periph_axi_rsp;

  floo_mosaic_noc floo_noc_i (
      .clk_i               (clk_i),
      .rst_ni              (rst_ni),
      .test_enable_i       (1'b0),
% for h in range(nh):
      .hart${h}_axi_in_req_i  (mgr_axi_req[${h}]),
      .hart${h}_axi_in_rsp_o  (mgr_axi_rsp[${h}]),
% endfor
      .shared_axi_in_req_i (mgr_axi_req[NumHarts]),
      .shared_axi_in_rsp_o (mgr_axi_rsp[NumHarts]),
      .mem_axi_out_req_o   (mem_axi_req),
      .mem_axi_out_rsp_i   (mem_axi_rsp),
      .periph_axi_out_req_o(periph_axi_req),
      .periph_axi_out_rsp_i(periph_axi_rsp)
  );

  // ── Subordinate side: bridge back to OBI and demux onto the slaves ──
  obi_req_t mem_obi_req, periph_obi_req;
  obi_resp_t mem_obi_resp, periph_obi_resp;

  xheep_axi_to_obi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .axi_req_t (floo_mosaic_noc_pkg::axi_axi_out_req_t),
      .axi_resp_t(floo_mosaic_noc_pkg::axi_axi_out_rsp_t),
      .AxiIdWidth($bits(floo_mosaic_noc_pkg::axi_axi_out_id_t))
  ) axi_to_obi_mem_i (
      .clk_i     (clk_i),
      .rst_ni    (rst_ni),
      .axi_req_i (mem_axi_req),
      .axi_resp_o(mem_axi_rsp),
      .obi_req_o (mem_obi_req),
      .obi_resp_i(mem_obi_resp)
  );

  xheep_axi_to_obi #(
      .obi_req_t (obi_pkg::obi_req_t),
      .obi_resp_t(obi_pkg::obi_resp_t),
      .axi_req_t (floo_mosaic_noc_pkg::axi_axi_out_req_t),
      .axi_resp_t(floo_mosaic_noc_pkg::axi_axi_out_rsp_t),
      .AxiIdWidth($bits(floo_mosaic_noc_pkg::axi_axi_out_id_t))
  ) axi_to_obi_periph_i (
      .clk_i     (clk_i),
      .rst_ni    (rst_ni),
      .axi_req_i (periph_axi_req),
      .axi_resp_o(periph_axi_rsp),
      .obi_req_o (periph_obi_req),
      .obi_resp_i(periph_obi_resp)
  );

  // Memory endpoint -> per-bank demux (tier-local rules, contiguous banks)
  localparam addr_map_rule_t [NUM_BANKS-1:0] MEM_TIER_RULES = '{
% for k, bank in enumerate(memory_ss.iter_ram_banks()):
      '{ idx: 32'd${k}, start_addr: RAM${bank.name()}_START_ADDRESS, end_addr: RAM${bank.name()}_END_ADDRESS }${"," if k < memory_ss.ram_numbanks() - 1 else ""}
% endfor
  };

  obi_req_t  [NUM_BANKS-1:0] mem_tier_req;
  obi_resp_t [NUM_BANKS-1:0] mem_tier_resp;

  xbar_varlat_one_to_n #(
      .XBAR_NSLAVE(NUM_BANKS),
      .NUM_RULES  (NUM_BANKS)
  ) mem_demux_i (
      .clk_i        (clk_i),
      .rst_ni       (rst_ni),
      .addr_map_i   (MEM_TIER_RULES),
      .default_idx_i('0),
      .master_req_i (mem_obi_req),
      .master_resp_o(mem_obi_resp),
      .slave_req_o  (mem_tier_req),
      .slave_resp_i (mem_tier_resp)
  );

% for k, bank in enumerate(memory_ss.iter_ram_banks()):
  assign slave_req_o[RAM${bank.name()}_IDX] = mem_tier_req[${k}];
  assign mem_tier_resp[${k}] = slave_resp_i[RAM${bank.name()}_IDX];
% endfor

  // Peripheral endpoint -> 1-to-5 demux over the non-memory tier rules
  obi_req_t  [NONMEM_TIER_NSLAVE-1:0] nonmem_tier_req;
  obi_resp_t [NONMEM_TIER_NSLAVE-1:0] nonmem_tier_resp;

  xbar_varlat_one_to_n #(
      .XBAR_NSLAVE(NONMEM_TIER_NSLAVE),
      .NUM_RULES  (NONMEM_TIER_NSLAVE)
  ) periph_demux_i (
      .clk_i        (clk_i),
      .rst_ni       (rst_ni),
      .addr_map_i   (NONMEM_TIER_RULES),
      .default_idx_i(NONMEM_ERROR_IDX[cf_math_pkg::idx_width(NONMEM_TIER_NSLAVE)-1:0]),
      .master_req_i (periph_obi_req),
      .master_resp_o(periph_obi_resp),
      .slave_req_o  (nonmem_tier_req),
      .slave_resp_i (nonmem_tier_resp)
  );

% for tier_idx, slave in enumerate(("ERROR", "DEBUG", "AO_PERIPHERAL", "PERIPHERAL", "FLASH_MEM")):
  assign slave_req_o[${slave}_IDX] = nonmem_tier_req[${tier_idx}];
  assign nonmem_tier_resp[${tier_idx}] = slave_resp_i[${slave}_IDX];
% endfor

% else:
  //Address Decoder
% if not memory_ss.has_il_ram():
  logic [XBAR_NMASTER-1:0][LOG_XBAR_NSLAVE-1:0] port_sel;
% else:
  logic [XBAR_NMASTER-1:0][LOG_XBAR_NSLAVE-1:0] port_sel, pre_port_sel;
  logic [XBAR_NMASTER-1:0][31:0] post_master_req_addr;
% endif

  // Neck crossbar
  obi_req_t neck_req;
  obi_resp_t neck_resp;

  logic [XBAR_NMASTER-1:0] master_req_req;
  logic [XBAR_NMASTER-1:0] master_resp_gnt;
  logic [XBAR_NMASTER-1:0] master_resp_rvalid;
  logic [XBAR_NMASTER-1:0][31:0] master_resp_rdata;

  logic [XBAR_NSLAVE-1:0] slave_req_req;
  logic [XBAR_NSLAVE-1:0] slave_resp_gnt;
  logic [XBAR_NSLAVE-1:0] slave_resp_rvalid;
  logic [XBAR_NSLAVE-1:0][31:0] slave_resp_rdata;


  logic [XBAR_NMASTER-1:0][REQ_AGG_DATA_WIDTH-1:0] master_req_data;
  logic [XBAR_NSLAVE-1:0][REQ_AGG_DATA_WIDTH-1:0] slave_req_out_data;
  obi_req_t [XBAR_NMASTER-1:0] master_req;

  if (BUS_TYPE == NtoM) begin : gen_addr_decoders_NtoM
    for (genvar i = 0; i < XBAR_NMASTER; i++) begin : gen_addr_decoders
      addr_decode #(
          /// Highest index which can happen in a rule.
          .NoIndices(XBAR_NSLAVE),
          .NoRules(XBAR_NSLAVE),
          .addr_t(logic [31:0]),
          .rule_t(addr_map_rule_pkg::addr_map_rule_t)
      ) addr_decode_i (
          .addr_i(master_req_i[i].addr),
          .addr_map_i,
% if not memory_ss.has_il_ram():
          .idx_o(port_sel[i]),
% else:
          .idx_o(pre_port_sel[i]),
% endif          
          .dec_valid_o(),
          .dec_error_o(),
          .en_default_idx_i(1'b1),
          .default_idx_i
      );
    end
% if memory_ss.has_il_ram():
    for (genvar j = 0; j < XBAR_NMASTER; j++) begin : gen_addr_napot
      always_comb begin
        port_sel[j] = pre_port_sel[j];
        post_master_req_addr[j] = master_req_i[j].addr;
% for i, group in enumerate(memory_ss.iter_il_groups()):
        if (pre_port_sel[j] == RAM_IL${i}_IDX[LOG_XBAR_NSLAVE-1:0]) begin
          port_sel[j] = RAM_IL${i}_IDX[LOG_XBAR_NSLAVE-1:0] + $unsigned(master_req_i[j].addr[${group.n.bit_length()-1 +1}:2]);
          post_master_req_addr[j] = {master_req_i[j].addr[31:${2+group.n.bit_length()-1}], ${2+group.n.bit_length()-1}'h0};
        end
% endfor
      end
    end
% endif    
  end

  // Propagate interleaved address
  generate
    for (genvar i = 0; i < XBAR_NMASTER; i++) begin : gen_unroll_master
      assign master_req[i] = '{
        req: master_req_i[i].req,
        we: master_req_i[i].we,
        be: master_req_i[i].be,
  % if not memory_ss.has_il_ram():
        addr: master_req_i[i].addr,
  % else:
        addr: post_master_req_addr[i],
  % endif
        wdata: master_req_i[i].wdata
      };
    end
  endgenerate

  if (BUS_TYPE == NtoM) begin : gen_xbar_NtoM


    // Unroll OBI structs
    for (genvar i = 0; unsigned'(i) < XBAR_NMASTER; i++) begin: gen_unroll_master
      assign master_req_req[i] = master_req[i].req;
      assign master_req_data[i] = {
        master_req[i].we,
        master_req[i].be,
        master_req[i].addr,
        master_req[i].wdata
      };
      assign master_resp_o[i].gnt = master_resp_gnt[i];
      assign master_resp_o[i].rdata = master_resp_rdata[i];
      assign master_resp_o[i].rvalid = master_resp_rvalid[i];
    end

    for (genvar i = 0; i < XBAR_NSLAVE; i++) begin : gen_unroll_slave
      assign slave_req_o[i].req = slave_req_req[i];
      assign {slave_req_o[i].we, slave_req_o[i].be, slave_req_o[i].addr, slave_req_o[i].wdata} = slave_req_out_data[i];
      assign slave_resp_rdata[i] = slave_resp_i[i].rdata;
      assign slave_resp_gnt[i] = slave_resp_i[i].gnt;
      assign slave_resp_rvalid[i] = slave_resp_i[i].rvalid;
    end

    //Crossbar instantiation
    xbar_varlat #(
        .AggregateGnt(0),
        .NumIn(XBAR_NMASTER),
        .NumOut(XBAR_NSLAVE),
        .ReqDataWidth(REQ_AGG_DATA_WIDTH),
        .RespDataWidth(RESP_AGG_DATA_WIDTH)
    ) i_xbar (
        .clk_i,
        .rst_ni,
        .req_i  (master_req_req),
        .add_i  (port_sel),
        .wdata_i(master_req_data),
        .gnt_o  (master_resp_gnt),
        .rdata_o(master_resp_rdata),
        .rr_i   ('0),
        .vld_o  (master_resp_rvalid),
        .gnt_i  (slave_resp_gnt),
        .req_o  (slave_req_req),
        .vld_i  (slave_resp_rvalid),
        .wdata_o(slave_req_out_data),
        .rdata_i(slave_resp_rdata)
    );

  end else begin : gen_xbar_1toM

    // N-to-1 crossbar
    xbar_varlat_n_to_one #(
      .XBAR_NMASTER (XBAR_NMASTER)
    ) xbar_varlat_n_to_one_i (
      .clk_i         (clk_i),
      .rst_ni        (rst_ni),
      .master_req_i  (master_req),
      .master_resp_o (master_resp_o),
      .slave_req_o   (neck_req),
      .slave_resp_i  (neck_resp)
    );

    // 1-to-N crossbar
    // NOTE: AGGREGATE_GNT should be 0 when a single master is actually
    // aggregating multiple master requests. This is not needed when a
    // real-single master is used or multiple masters are used as the
    // rr_arb_tree dispatches the grant to each corresponding master.
    // Whereas, when the xbar_varlat is used with a single master, which is
    // shared among severals (as in this case as an output of another
    // xbar_varlat), the rr_arb_tree gives all the grant to the shared single
    // master, thus granting transactions that should not be granted.

      xbar_varlat_one_to_n #(
        .XBAR_NSLAVE   (XBAR_NSLAVE),
        .AGGREGATE_GNT (32'd0) // the neck request is aggregating all the input masters
      ) xbar_varlat_one_to_n_i (
        .clk_i         (clk_i),
        .rst_ni        (rst_ni),
        .addr_map_i,
        .default_idx_i,
        .master_req_i  (neck_req),
        .master_resp_o (neck_resp),
        .slave_req_o   (slave_req_o),
        .slave_resp_i  (slave_resp_i)
      );
  end
% endif

endmodule : system_xbar
