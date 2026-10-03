"""Protocol-family heuristics for wrapper-smith — the knowledge distilled from
the SCI wrappers proven in hw/sci/ (spellings verified against the actual
vendored tops: servile.v, serv_top.v, fazyrv_top.sv, ibex_top.sv, picorv32.v,
snitch.sv, cva6.sv, RocketTile.sv).

Each family entry:
  signatures: {(alt1, alt2, ...): weight} — a GROUP of alternative spellings;
      the group scores its weight if ANY alternative is a substring of a
      normalized port name (lowercased, io_/i_/o_ prefix and _i/_o/_n/_ni/_no
      suffix stripped). Groups keep families with several dialect spellings
      (wb_imem_* vs ibus_*) from punishing their own score.
  anti_signatures: substrings that disqualify (scale score by 0.3) — e.g. a
      split ibus/dbus core must not classify as unified wishbone.
  requires_any: at least one of these substrings must appear somewhere.
  min_matches: at least N distinct groups must hit.
  template: harness/templates/wrapper/<template>.sv.tpl
  port_shape: unified | split — drives the cpu_subsystem branch fragment and
      the tb-smith memory count.
  proven_by: the in-tree wrapper this family's template was distilled from.

Scoring: score = Σ weight(groups hit) / Σ weight(all groups).
Classify when best >= CLASSIFY_THRESHOLD; ALWAYS report top-2 with evidence.
Below threshold → family "unknown" (never a silent wrong template).
"""

CLASSIFY_THRESHOLD = 0.5

FAMILIES = {
    "wishbone_unified": {
        "description": "single Wishbone (Lite/Classic) master, I+D arbitrated in-core",
        "signatures": {
            ("wb_mem_stb", "wb_stb"): 2,
            ("wb_mem_ack", "wb_ack"): 2,
            ("wb_mem_adr", "wb_adr"): 1,
            ("wb_mem_sel", "wb_sel"): 1,
            ("wb_mem_we", "wb_we"): 1,
            ("wb_mem_dat", "wb_dat", "wb_mem_rdt", "wb_rdt"): 1,
            ("wb_cyc", "wb_mem_cyc"): 0.5,   # Wishbone Lite (servile) has no cyc
        },
        "anti_signatures": ["ibus", "dbus", "i_wb_", "d_wb_", "wb_imem", "wb_dmem"],
        "template": "wishbone_unified",
        "port_shape": "unified",
        "proven_by": "hw/sci/serv_sci.sv (servile: wb_mem_*; serv W=1, qerv W=4)",
    },
    "wishbone_split": {
        "description": "two Wishbone masters (separate I-fetch and D-access)",
        "signatures": {
            ("wb_imem", "ibus_adr", "i_wb_adr"): 3,
            ("wb_dmem", "dbus_adr", "d_wb_adr"): 3,
            ("ack",): 2,
            ("stb", "cyc"): 1,
            ("adr",): 1,
        },
        "requires_any": ["ibus", "dbus", "i_wb", "d_wb", "imem", "dmem"],
        "template": "wishbone_split",
        "port_shape": "split",
        "proven_by": "hw/sci/fazyrv_sci.sv (wb_imem_*/wb_dmem_*; incl. clock-stall "
                     "adapter for combinational-memory cores); serv_top-level "
                     "ibus_*/dbus_* also lands here",
    },
    "reqgnt_split": {
        "description": "OBI-like instr/data req+gnt+rvalid (near-direct OBI)",
        "signatures": {
            ("instr_req",): 3,
            ("instr_gnt",): 3,
            ("instr_rvalid",): 2,
            ("data_req",): 3,
            ("data_gnt",): 3,
            ("data_rvalid",): 2,
            ("data_be", "data_we"): 1,
        },
        "template": "reqgnt_split",
        "port_shape": "split",
        "proven_by": "hw/sci/ibex_sci.sv",
    },
    "unified_native": {
        "description": "single native memory port (valid/ready, wstrb encodes writes)",
        "signatures": {
            ("mem_valid",): 3,
            ("mem_ready", "mem_rbusy", "mem_busy"): 3,
            ("mem_wstrb", "mem_wmask"): 2,
            ("mem_rdata",): 1,
            ("mem_wdata",): 1,
            ("mem_instr", "mem_rstrb"): 1,
            ("mem_addr",): 1,
        },
        "template": "unified_native",
        "port_shape": "unified",
        "proven_by": "hw/sci/picorv32_sci.sv (femtorv32-style mem_rbusy/mem_wmask "
                     "spellings also land here)",
    },
    "reqrsp_split": {
        "description": "q/p request-response data port + instruction refill port",
        "signatures": {
            ("qvalid",): 3,
            ("qready",): 3,
            ("pvalid",): 2,
            ("pready",): 2,
            ("qaddr",): 1,
            ("qwrite",): 1,
            ("qdata", "pdata"): 1,
            ("inst_valid", "inst_ready", "inst_addr"): 1,
        },
        "template": "reqrsp_split",
        "port_shape": "split",
        "proven_by": "hw/sci/snitch_sci.sv (writes get no p-response — template handles)",
    },
    "axi4_unified": {
        "description": "flattened AXI4 master signals (bridged via xheep_axi_burst_to_obi)",
        "signatures": {
            ("awvalid", "aw_valid"): 1,
            ("awready", "aw_ready"): 1,
            ("arvalid", "ar_valid"): 1,
            ("arready", "ar_ready"): 1,
            ("wvalid", "w_valid"): 1,
            ("rvalid", "r_valid"): 1,
            ("bresp", "b_resp"): 1,
            ("rlast", "r_last"): 1,
            ("awaddr", "aw_addr"): 1,
            ("araddr", "ar_addr"): 1,
        },
        "min_matches": 5,
        "template": "axi4_unified",
        "port_shape": "unified",
        "proven_by": "hw/vendor/mosaic/axi_obi/xheep_axi_burst_to_obi.sv (cva6 path)",
    },
    "axi4_struct": {
        "description": "struct-port AXI4 master (noc_req/noc_resp, cva6-style)",
        "signatures": {
            ("noc_req",): 1,
            ("noc_resp",): 1,
        },
        "template": "axi4_unified",
        "port_shape": "unified",
        "proven_by": "hw/sci/cva6_sci.sv (mirrored channel typedefs + burst bridge)",
    },
    "tilelink_unified": {
        "description": "TileLink(-C) master (window-bridged via xheep_tilelink_to_obi)",
        "signatures": {
            ("a_opcode", "a_bits_opcode"): 3,
            ("a_valid",): 1,
            ("a_ready",): 1,
            ("d_opcode", "d_bits_opcode"): 2,
            ("d_valid",): 1,
            ("d_denied", "d_bits_denied"): 1,
            ("e_valid",): 1,
            ("c_opcode", "c_bits_opcode"): 0.5,
            ("b_ready",): 0.5,
        },
        "template": "tilelink_unified",
        "port_shape": "unified",
        "proven_by": "hw/sci/rocket_sci.sv, hw/sci/boom_sci.sv (auto_buffer_out_* "
                     "spellings; DRAM-alias + uncached CLINT/PLIC windows)",
    },
    "ahb_split": {
        "description": "AHB-Lite manager port(s) (haddr/htrans/hready; hazard3 family)",
        "signatures": {
            ("haddr",): 2,
            ("htrans",): 2,
            ("hready",): 2,
            ("hrdata",): 1,
            ("hwrite",): 1,
            ("hwdata",): 1,
            ("hsize",): 1,
            ("hburst", "hresp"): 1,
        },
        "template": "ahb_split",
        "port_shape": "split",
        "proven_by": "NEW family (Hazard3 bring-up target): NONSEQ+HREADY accept -> "
                     "OBI req; HREADY stalls until rvalid; HSIZE+HADDR[1:0] -> "
                     "byte enables",
    },
}

# Control-signal extraction patterns (family-independent), matched against
# normalized names. Reset polarity: active_low if the RAW name ends in
# n/_n/_ni or contains resetn/rst_n; else active_high (template inverts).
CONTROL_PATTERNS = {
    "clk": ["clk", "clock"],
    "rst": ["rst", "reset", "resetn", "rst_n"],
    "irq": ["irq", "interrupt", "int_local", "meip", "mip"],
    "boot": ["boot_addr", "progaddr_reset", "reset_pc", "reset_vector",
             "bootadr", "boot_address"],
}
