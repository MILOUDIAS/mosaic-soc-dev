# AGENTS.md — MOSAIC-SoC Agent Instructions

> **Read this file before starting any work on MOSAIC-SoC.**
> For the full project description, see [README.md](./README.md).

## 1. Project Overview

MOSAIC-SoC is a **configuration-driven multi-core SoC generator** based on x-heep. It turns a single declarative config file into a synthesizable, tapeout-ready heterogeneous RISC-V SoC using entirely open-source EDA.

**Two phases:**
- **Phase 1** — Modify x-heep into a single-config-file multi-core generator. A YAML config drives the entire flow — core selection, counts, memory size, bus fabric, scheduler, peripherals. Support heterogeneous cores (SERV, QERV, FazyRV, Ibex, CVA6) and multiple NoC/bus options. PoC: 1× Ibex (TITAN) + 2× FazyRV-CHUNK8 (ATLAS) + 4× SERV (NANO).
- **Phase 2** — Build an agentic harness (mosaic, based on oh-my-pi) with skills for RTL-to-GDS automation, config authoring, DRC triage, and documentation generation.

**Context:** IEEE SSCS Chipathon 2026, Track D (AI/LLM for Circuits). Target PDK: GF180MCU. Target area: 1.249 mm². All EDA is open-source (Librelane, Yosys, OpenROAD, Verilator).

## 2. Architecture Summary

- **Base platform:** x-heep — single-core MCU from EPFL, built on PULP-platform IPs and OpenTitan peripherals. Uses FuseSoC (CAPI=2 `.core` files) and Mako templates (`.sv.tpl`) rendered by a Python generator.
- **Core taxonomy (Big.LITTLE):**
  - **TITAN** (big): 25k–500k GE, IPC 0.5–2.0 — RTOS/orchestration (Ibex, CV32E40P, CVA6)
  - **ATLAS** (little): 2k–25k GE, IPC 0.1–0.5 — signal conditioning/protocol tasks (FazyRV-CHUNK8)
  - **NANO** (tiny): <2k GE, IPC 0.01–0.1 — always-on sensor polling (SERV)
- **Standard Core Interface (SCI):** Every core is wrapped with a thin adaptor (~100–200 lines SV) presenting identical OBI 1.3 instruction + data ports, utilization metrics, clock-gate handshake, and debug stub. Adding a new core = writing one SCI wrapper + a FuseSoC `.core` descriptor.
- **Task Dispatch Unit (TDU):** <100 GE memory-mapped hardware block. Registers: `CORE_STATUS`, `CORE_CPI_EST`, `TASK_QUEUE` (8-deep FIFO), `WAKE_MASK`, `ACTIVE_HART_CYCLES`, `SCHED_MODE` (static/dynamic/power-aware). The TITAN core runs FreeRTOS and migrates tasks based on CPI estimates.
- **DMA engine: iDMA** (pulp-platform/iDMA) — replaces x-heep's existing simple DMA. iDMA is a modular frontend/midend/backend DMA with native OBI backend, ND transfer support (for FazyRV signal processing), multi-port midend (for serving multiple core clusters), error handling (continue/abort/replay), and hardware burst legalization. Silicon-proven in MemPool, Occamy, Snitch cluster. Integrates via register frontend (matches x-heep's `reg_pkg`), ND midend, and OBI backend — no protocol conversion needed, no new external dependencies (its `Bender.yml` deps — `axi`, `common_cells`, `register_interface`, `obi` — are already in x-heep's dependency tree).
- **Bus fabric:** Parameterized OBI N×M crossbar (from `pulp-platform/obi`), sized automatically by the generator. Banked L1 SRAM with per-bank clock-gating. APB bridge for low-speed peripherals.
- **PoC target:** 1× cv32e20 (TITAN) + 2× FazyRV-CHUNK8 (ATLAS) + 4× SERV (NANO), 32 KB SRAM, 2 KB boot ROM, UART + GPIO + timer + SPI, TDU scheduler, full RTL-to-GDSII with DRC/LVS clean.

### Config-Driven Flow

A single `mosaic.yaml` drives the entire generation flow:

```yaml
# mosaic.yaml — Phase 1 config format
soc:
  name: mosaic_poc_alpha
  pdk: gf180mcu
  target: tapeout       # strict qualified physical matrix; default is rtl

  cores:
    - ip: cv32e20        # big core — CVE2, 2-stage, RV32E/M
      isa: rv32emc
      count: 1
      role: titan         # orchestrator tier

    - ip: fazyrv          # little cores — chunk-serial datapath
      isa: rv32i
      chunksize: 8        # per-core-type parameter
      count: 2
      role: atlas         # signal-processing tier

    - ip: serv            # tiny cores — bit-serial, ~200 GE each
      isa: rv32i
      count: 4
      role: nano          # always-on sensor polling tier

  memory:
    sram_kb: 32           # banked; each bank independently clock-gated
    boot_rom_kb: 2

  bus: obi                # Open Bus Interface — native to Ibex/x-heep

  dma: idma               # idma | none  (default idma; see below)

  scheduler:
    tdu: true             # Task Dispatch Unit: <100 GE hardware assist
    mode: dynamic         # static | dynamic | power-aware

  peripherals:
    - uart
    - gpio
    - timer
    - spi
```

**What the config drives:**
- `cores[].ip` selects the core IP (from the catalog in §4), instantiates its SCI wrapper, and maps to the corresponding FuseSoC dependency
- `cores[].count` controls how many copies of that core type are instantiated. Each gets its own OBI master ports (instr + data), hart_id, and debug channel
- `cores[].role` sets the core tier and determines interrupt routing, clock gating policy, and TDU scheduling priority
- `cores[].*` — any additional fields (like `chunksize`) are passed as per-core params to the core's FuseSoC configuration and template
- `bus` selects the interconnect fabric (OBI, AXI, FlooNoC, etc.) — determines which crossbar/interconnect modules are instantiated
- `target` separates broad `rtl`/`simulation` generation from a `tapeout` claim; tapeout is gated to the exact canonical GF180/OBI 7-hart PoC topology, memory, dynamic TDU policy, and UART/GPIO/timer/SPI set
- `scheduler.tdu` enables the Task Dispatch Unit hardware block; `mode` sets scheduling policy
- `peripherals` enables/disables specific peripheral IPs in the memory map

## 3. x-heep Architecture (Critical Reference)

Agents MUST understand these files before modifying x-heep. All paths are relative to `refs/IP_SoCs_Catalog/x-heep/`.

### Generation Pipeline

| File | Purpose |
|------|---------|
| `util/mosaic_gen/mcu_gen.py` | Entry point — renders Mako `.sv.tpl` templates into `.sv` files using the `XHeep` config object |
| `util/mosaic_gen/xheep.py` | `XHeep` class — central config: single `_cpu`, `_bus_type`, `_memory_ss`, peripheral domains, pad ring |
| `util/mosaic_gen/load_config.py` | Loads HJSON or Python config files; `load_cpu_config()` creates the right CPU subclass |
| `util/mosaic_gen/cpu/cpu.py` | Base `CPU` class with `AVAILABLE_CPUS` set |
| `util/mosaic_gen/cpu/cv32e20.py` | CVE2 CPU class (params: `rv32e`, `rv32m`) |
| `util/mosaic_gen/cpu/cv32e40p.py` | CV32E40P CPU class (params: `fpu`, `zfinx`, `corev_pulp`, `num_mhpmcounters`) |
| `util/mosaic_gen/cpu/cv32e40px.py` | CV32E40P+XIF (inherits cv32e40p) |
| `util/mosaic_gen/cpu/cv32e40x.py` | CV32E40X CPU class (params: `num_mhpmcounters`) |
| `util/mosaic_gen/bus_type.py` | `BusType` enum: `onetoM` or `NtoM` |
| `util/mosaic_gen/cv_x_if.py` | `CvXIf` class for CV-X-IF extension interface config |
| `configs/*.hjson` / `configs/*.py` | Configuration files (Python takes precedence over HJSON) |

### RTL Templates (`hw/core-v-mini-mcu/`)

| Template | Purpose |
|----------|---------|
| `core_v_mini_mcu.sv.tpl` | Top-level SoC — instantiates 5 subsystems: cpu, debug, system_bus, memory, peripherals |
| `cpu_subsystem.sv.tpl` | CPU instantiation — Mako conditionals per CPU type (`% if cpu.name == "cv32e20"` etc.) |
| `system_bus.sv.tpl` | Bus wrapper — each master has a 1-to-2 demux crossbar (internal vs external slave) |
| `system_xbar.sv.tpl` | OBI crossbar — `NtoM` (direct N-to-M) or `onetoM` (N→1 neck→1→N). Uses vendored `xbar_varlat` |
| `memory_subsystem.sv.tpl` | RAM bank instantiation (1–16 banks, 1–32 KB each) |
| `core_v_mini_mcu_pkg.sv.tpl` | Central package — memory map, master/slave indices, address decode rules |
| `ao_peripheral_subsystem.sv.tpl` | Always-on peripherals (soc_ctrl, boot_rom, SPI flash, DMA, power_manager, timer, GPIO) |
| `peripheral_subsystem.sv.tpl` | User peripherals (PLIC, UART, SPI, I2C, GPIO, timer, serial_link) |
| `debug_subsystem.sv` | Debug module — JTAG/SPI-slave, `dm_obi_top`, already supports `NRHARTS` parameter |
| `xbar_varlat_n_to_one.sv` | N-to-1 OBI crossbar primitive |
| `xbar_varlat_one_to_n.sv` | 1-to-N OBI crossbar primitive |

### Bus Architecture

- **Protocol:** OBI (Open Bus Interface) — 32-bit addr, 32-bit data, `obi_req_t`/`obi_resp_t` structs
- **Two modes:** `onetoM` (N masters → N-to-1 neck → 1-to-N crossbar → slaves) or `NtoM` (direct N-to-M crossbar)
- **Master indices (hardcoded):** `CORE_INSTR_IDX=0`, `CORE_DATA_IDX=1`, then `DEBUG_MASTER=2`, then DMA ports (3 per DMA master port)
- **Slave indices:** `ERROR=0`, `RAM0..N`, `DEBUG`, `AO_PERIPHERAL`, `PERIPHERAL`, `FLASH_MEM`
- **Address decoding:** Uses `addr_decode` from pulp-platform `common_cells`, with `addr_map_rule_t` array
- **Interleaved memory:** Supported in `NtoM` mode via NAPOT-based address decoding

### Memory Map

| Region | Start Address | Size |
|--------|--------------|------|
| RAM banks | 0x00000000 | Variable (1–16 banks × 1–32 KB) |
| DEBUG | 0x10000000 | 1 MB |
| AO_PERIPHERAL | 0x20000000 | 1 MB |
| PERIPHERAL | 0x30000000 | 1 MB |
| FLASH_MEM | 0x40000000 | 16 MB |
| EXT_SLAVES | 0xF0000000 | 16 MB |

### What Must Change for Multi-Core

| Layer | Current (single-core) | Required (multi-core) |
|-------|----------------------|----------------------|
| `XHeep` class (`xheep.py`) | `_cpu: CPU` (single instance) | `_cpus: List[CPU]` or `CpuCluster` abstraction |
| `load_config.py` | Parses single `cpu_type` | Parse list of CPU configs with per-core params, hart_id, boot addr |
| `cpu_subsystem.sv.tpl` | Single CPU, Mako conditional on `cpu.name` | Loop over multiple heterogeneous cores, each with own SCI wrapper |
| `core_v_mini_mcu_pkg.sv.tpl` | `CORE_INSTR_IDX=0`, `CORE_DATA_IDX=1` hardcoded | Per-core indices: `CORE0_INSTR_IDX`, `CORE0_DATA_IDX`, `CORE1_INSTR_IDX`, ... |
| `system_bus.sv.tpl` / `system_xbar.sv.tpl` | 2 CPU master ports + simple DMA | 2× master ports per additional core + iDMA multi-port masters (read, write per channel) |
| DMA engine | x-heep's simple DMA (`x-heep:ip:dma_subsystem`) | **iDMA** (register frontend + ND midend + OBI backend) — handles ND transfers, multi-port distribution, error handling, burst legalization |
| `NRHARTS` | `EXT_HARTS + 1` | Total core count |
| PLIC | Single target (`NumTarget=1`) | Multi-target (`NumTarget=N`) or per-core PLICs |
| Interrupt routing | Single `intr` vector | Per-core interrupt vectors, per-core CLINT/timer targeting |
| Power manager | Single `cpu_subsystem_pwr_ctrl` | Per-core power domains |

### Existing Multi-Core Hooks (Leverage These)

- **`EXT_HARTS` parameter** — external harts share the debug module. `debug_subsystem.sv` already routes `debug_req[0]` to internal core and `debug_req[1..N]` to `ext_debug_req_o`.
- **`EXT_XBAR_NMASTER` parameter** — external masters connect directly to the system crossbar via `ext_xbar_master_req_i` ports.
- **`AO_SPC_NUM` parameter** — external modules access the AO peripheral bus via a `reg_mux`.
- **`debug_subsystem.sv`** — already parameterized with `NRHARTS`, `debug_core_req_o` is an array `[NRHARTS-1:0]`.

## 4. Reference IP Catalog (`refs/`)

All reference IPs are in `refs/` for study and potential integration. **Do not modify files in `refs/`.**

### Cores (`refs/IP_Cores_Catalog/`)

| Core | ISA | Bits/cycle | Area (min) | Bus | Config System | Tier |
|------|-----|-----------|-----------|-----|---------------|------|
| SERV | RV32I(+M,C) | 1 (bit-serial) | ~200 GE | Wishbone-lite | FuseSoC params: `W`, `WITH_CSR`, `COMPRESSED`, `MDU`, `PRE_REGISTER` | NANO | ✅ `hw/sci/serv_sci.sv` + `hw/vendor/mosaic/serv/` |
| QERV | RV32I(+M,C) | 4 (nibble-serial) | ~3k GE | Wishbone-lite | FuseSoC (depends on SERV, overrides `qerv_immdec.v`) | NANO | ❌ Not yet |
| FazyRV | RV32I(+C) | 1/2/4/8 (chunk) | ~2-5k GE | Wishbone | FuseSoC: `CHUNKSIZE`, `CONF`(MIN/INT/CSR), `RFTYPE`, `RVC`, `MEMDLY1` | NANO/ATLAS | ✅ `hw/sci/fazyrv_sci.sv` + `hw/vendor/mosaic/fazyrv/` |
| cv32e20 (CVE2) | RV32E/M | 32 | ~14k GE | Simple req/gnt (→OBI via wrapper) | FuseSoC params: `RV32E`, `RV32M` | TITAN | ✅ Native (x-heep) |
| Ibex | RV32I/E(+M,C,B) | 32 | 16.85k GE | Simple req/gnt | FuseSoC + `ibex_configs.yaml` | TITAN | ❌ Not yet |

**Bus compatibility notes:**
- SERV, QERV, FazyRV use **Wishbone** — SCI wrapper must convert Wishbone ↔ OBI
- Ibex uses **simple req/gnt** interface — SCI wrapper must convert to OBI
- CVA6 uses **AXI4** — use `axi_obi` converter from `refs/IP_Interconnect_Catalog/axi_obi/`
- CVA6 uses **Bender** (not FuseSoC) — use `bender fusesoc` to generate `.core` files, or vendor the RTL

### Interconnects (`refs/IP_Interconnect_Catalog/`)

| IP | Type | Protocol | Config Mechanism | Key Feature |
|----|------|----------|-----------------|-------------|
| FlooNoC | NoC generator | AXI4+ATOPs | FlooGen YAML (topology) + SV params (per-IP) | Wide physical channels, XY/Source/Table routing, multicast, reduction |
| TeraNoC | Hybrid mesh-xbar | AXI4 + 32b TCDM + OBI | Makefile `.mk` + FlooGen YAML + SV params | 1024-core scalable, branched from MemPool, router remapper |
| axi_obi | Protocol converter | AXI4 ↔ OBI | SV params only | `axi_to_obi` (multi-bank), `obi_to_axi`, `axi_to_detailed_mem_user` |
| iDMA | DMA engine **← primary DMA for MOSAIC-SoC** | OBI/AXI4/AXI4-Lite/AXI-Stream/TileLink | YAML db + SV params + hjson | Frontend/Midend/Backend architecture, 1D+ND transfers, multi-port, error handling (continue/abort/replay), burst legalization. Replaces x-heep's simple DMA — register FE + ND midend + OBI BE = direct fit, no new deps. |

### SoC Reference Designs (`refs/IP_SoCs_Catalog/`)

| SoC | Cores | Multi-core Pattern | Key Takeaway |
|-----|-------|-------------------|--------------|
| `pulp/` | FC + 8-core cluster | FC + Cluster decomposition, AXI CDC between domains | How to separate control core from compute cluster |
| `pulp_cluster/` | 8 cores | `pulp_cluster_cfg_t` struct config, HCI logarithmic interconnect, TCDM | Reusable cluster with config struct — model for multi-core config |
| `mempool/` | 256–1024 cores | Hierarchical: system→cluster→group→tile→core, TCDM interconnect | How to scale to many cores with hierarchical interconnect |
| `x-heep/` | 1 core | Single-core MCU, OBI bus, Mako templates, FuseSoC | **The base for MOSAIC-SoC** — see §3 above |
| `pulpissimo/` | 1 core | FC-only SoC controller, AXI interconnect | SoC controller pattern (without cluster) |
| `pulpino/` | 1 core | Early single-core, APB peripherals | Predecessor, simpler architecture |
| `chipyard/` | Configurable | Chisel/FIRRTL, TileLink, diplomacy | Different paradigm (Berkeley, not PULP) — reference only |
| `adam/` | 1 core + LPCPU | AXI-Lite fabric, OBI, power domains | Modular MCU with power management patterns |

### Tools (`refs/IP_Tools/`)

| Tool | Language | Purpose | Used by x-heep? |
|------|----------|---------|-----------------|
| `bender/` | Rust | HDL dependency manager + EDA script generator (PULP ecosystem) | No — but needed for CVA6, FlooNoC |
| `fusesoc/` | Python | HDL package manager + full build system (setup→build→run) | **Yes** — x-heep's primary build system |

**Bender ↔ FuseSoC interop:** `bender fusesoc` command generates FuseSoC `.core` files from Bender manifests. Use this when integrating Bender-based IPs (CVA6, FlooNoC) into the FuseSoC-based x-heep flow.

## 5. How to Add a New Core

1. **Study the core** — examine its RTL in `refs/IP_Cores_Catalog/<core>/`, note its bus interface, config parameters, and HDL (Verilog vs SystemVerilog)
2. **Write the SCI wrapper** — create `hw/sci/<core_name>_sci.sv` that presents OBI 1.3 instruction + data ports. Handle bus protocol conversion (Wishbone→OBI for SERV/FazyRV, req/gnt→OBI for Ibex, AXI→OBI for CVA6)
3. **Add Python CPU class** — create `util/mosaic_gen/cpu/<core_name>.py` inheriting from `CPU` base class. Define parameters, validate config
4. **Register the CPU** — add to `AVAILABLE_CPUS` set in `util/mosaic_gen/cpu/cpu.py`
5. **Add Mako conditional** — add a branch in `cpu_subsystem.sv.tpl` for the new core type
6. **Add FuseSoC dependency** — add the core IP to the `.core` file's `depend` list. If the core uses Bender, run `bender fusesoc` to generate `.core` files first
7. **Write a config file** — create `configs/<name>.hjson` or `configs/<name>.py` using the new core
8. **Test** — run `make mcu-gen` to generate RTL, then `make verilate` for simulation

## 6. How to Add a New Interconnect/NoC

1. **Study the interconnect IP** — examine `refs/IP_Interconnect_Catalog/<ip>/`, note protocols, config mechanism, and topology options
2. **Protocol bridging** — if the interconnect uses AXI and x-heep uses OBI, use `axi_obi` converters (`axi_to_obi`, `obi_to_axi`) from `refs/IP_Interconnect_Catalog/axi_obi/`
3. **Replace system crossbar** — modify `system_xbar.sv.tpl` and `system_bus.sv.tpl` to instantiate the new interconnect instead of `xbar_varlat`
4. **FlooNoC integration** — use FlooGen YAML (`floogen/config/` examples) to generate topology RTL, then integrate the generated `floo_<name>_noc.sv` and `floo_<name>_noc_pkg.sv` into the build
5. **Update package** — modify `core_v_mini_mcu_pkg.sv.tpl` with new address rules and master/slave indices
6. **Update FuseSoC** — add interconnect IP dependencies to the `.core` file

## 7. Build System

- **FuseSoC** (CAPI=2 `.core` files) — primary build system. x-heep uses this exclusively, NOT Bender
- **Mako templates** (`.sv.tpl`) — rendered by `util/mosaic_gen/mcu_gen.py` into `.sv` files. The `XHeep` config object is passed to all templates
- **Makefile** — `make mcu-gen` generates RTL from templates, `make verilate` runs Verilator simulation
- **Config files** — HJSON (declarative, `configs/*.hjson`) or Python (programmatic, `configs/*.py`). Python takes precedence
- **Bender** — available in `refs/IP_Tools/bender/` but NOT used by x-heep. Required when integrating PULP IPs that use `Bender.yml` (CVA6, FlooNoC). Use `bender fusesoc` to bridge

### Build Flow
```
config (.hjson/.py) → mcu_gen.py → .sv.tpl → .sv files → FuseSoC → EDA tool
```

## 8. EDA Flow (Phase 1 Target)

| Stage | Tool | Notes |
|-------|------|-------|
| Synthesis | Yosys + ABC | Via Librelane |
| Place & Route | OpenROAD | Via Librelane |
| PDK | GF180MCU | SkyWater-equivalent open PDK |
| Environment | Nix: `nix develop .#sim` / `.#physical` (reference) | LibreLane 3.0.0's nix-eda; IIC-OSIC-TOOLS 2026.09 via `tools/iic-osic.sh` is a labelled convenience runtime. `mosaic doctor` checks. See docs/reproducing.md |
| Simulation | Verilator **5.050** + cocotb | Pre-synthesis RTL sim; runners refuse any other Verilator (tb/tools.sh) |
| Formal verification | SymbiYosys + riscv-formal | For SCI wrapper validation |
| Signoff | DRC + LVS clean | STA closure at 50 MHz |
| Target area | 1.249 mm² | GF180MCU |

## 9. Phase 2: Agentic Harness — IMPLEMENTED (2026-07-12)

- **Harness:** mosaic (`harness/` — typed deterministic tools plus a bounded
  model/tool/observation loop and live terminal/JSONL event stream;
  `./mosaic` executable, `pip install -e .` console script, omp-style
  first-run driver picker: deterministic/claude/omp/api via `mosaic setup`,
  one-line dispatch via `mosaic agent "<request>"`).
  Based on **oh-my-pi** (vendored at `refs/IP_Tools/oh-my-pi`,
  can1357/oh-my-pi): we **drive it, we don't fork it** — skill cards in
  `.claude/skills/` are discovered by BOTH Claude Code and omp (its `claude`
  skill provider). omp reaches the harness through the same MCP server
  (`.omp/mcp.json`); the old ungated `.omp/tools/mosaic.ts` shim is gone.
  TTY omp launches use its full native TUI rather than `--print`.
- **Design principle:** The agent *assists and is checked by* deterministic
  tooling. It never replaces signoff. Every pipeline stage is a hard gate
  (registry-synced schema validation → topo-viz semantic checks → mcu-gen
  render → TB PASS → topology-generic all-hart liveness EXIT SUCCESS).
- **Agent runtime:** `harness/agent.py` returns each typed tool result to the
  model for bounded replanning; a user-text policy classifier derives a
  non-escalatable ceiling that `request_scope` must confirm, gate evidence is SHA-256-bound to config contents,
  prerequisites prevent bypass, repeated calls and turns are capped, and
  physical/integration actions need explicit approval. Child output is live;
  each session has a private append-only journal with a bounded in-memory tail.
- **Core registries are single-sourced**: `harness/core.py` AST-reads
  `AVAILABLE_CPUS`/`SCI_CORES` from `util/mosaic_gen` (sync enforced by
  `test_harness_core.py`; every shipped config must validate).
- **The external drivers are gated too** (2026-08-09). `--driver claude`/`omp`
  used to be a `subprocess.call` with a prompt: every rule above existed in
  `AgentRunner` and applied to nothing either driver did. The gates now live in
  `harness/gates.py` and are called by the built-in loop *and* by
  `harness/mcp_server.py`, a session-scoped MCP stdio server holding one
  `AgentState` with the ceiling locked before the client connects — so a
  refusal over MCP is the identical string produced in-process, not a second
  implementation. `--driver claude` launches with `--mcp-config`,
  `--strict-mcp-config`, an `--allowedTools` list of only the harness's MCP
  tools, and `Bash`/`Write`/`Edit` disallowed (with Bash the model reaches the
  same flows ungated, so the allowlist alone is decoration). `--driver omp`
  runs omp against the same server from `.omp/mcp.json` with its shell tools
  removed. (It used to refuse: the claim that omp speaks no MCP was out of
  date.)
- **Plugin mode** (2026-09-24). A host that installs the harness as a plugin
  starts the server before any request exists, so there is no request to
  derive a ceiling from. Without `--request` the server runs in plugin mode:
  - the ceiling is a standing allowlist the USER writes into the host config
    (`MOSAIC_SCOPES`; the default excludes `integration` and `physical`);
  - the model binds each request with `session_new` and picks a scope inside
    the allowlist;
  - `physical`, `integration`, any flow whose `FlowSpec` declares approval, a
    tb-matrix run above validate, and wrapper apply also need a person's
    time-limited token from `mosaic approve <scope>`. That command refuses
    without a TTY on stdin and stdout, so an agent's shell cannot run it.
    This applies to locked sessions too.

  Packaging: a Claude Code plugin with a marketplace entry and a PreToolUse
  hook that denies the ungated Bash paths (`plugins/mosaic`);
  `mosaic install --host codex|opencode|omp|claude` prints or merges the
  host config. Smoke results: `docs/plugin_smoke.md`.

### Skills (all implemented; cards in `.claude/skills/`)

| Skill | Purpose | Input → Output |
|-------|---------|---------------|
| `config-author` | Author/validate `mosaic.yaml` (presets, per-core wake-demo shape) | params/preset → valid YAML |
| `soc-from-prompt` | Deterministic NL→SoC pipeline (no LLM needed; the agent path uses the same gates) | prompt → config → RTL → all-hart liveness EXIT SUCCESS |
| `flow-runner` | 19 EDA flows with parsing + hard gates (EXIT SUCCESS required for tb-soc-*) | flow → structured result |
| `wrapper-smith` | Wrap ANY core/IP: port parse (verible→yosys→regex), bus classification vs 9 families, scaffold of all 8 touchpoints | RTL → analysis.json → staged integration |
| `tb-smith` | Per-core verification: single-hart SCI TB (dormancy/wake/liveness/sentinel) + wake demo | core → TB PASS / EXIT SUCCESS |
| `tb-matrix` | Combination coverage of the integration SPACE: registry-derived axes → pairwise covering array (+ curated sim corners) → validate/render/sim tiers, resumable report; blocked pairs always reported with a reason | axes → tested/blocked/failing combinations |
| `drc-triage` | Read DRC/LVS reports, propose targeted fixes | report → classified violations |
| `doc-gen` | Config summaries, memory map, run reports | artifacts → markdown |
| `topo-viz` | Semantic config checks + interactive topology SVG | config → checks + diagram |

### The wrap-any-core triangle (proven on Hazard3)

**scaffold (deterministic) → agent-fill (marked TODOs) → TB-verified
(deterministic).** Ground truth: the classifier identifies all integrated
cores' families at ≥0.94 confidence (pytest corpus); Hazard3 (RP2350's core,
AHB-Lite — a previously unproven family) was analyzed at 1.00 confidence,
scaffolded (45 files + 5 idempotent edits), filled, and passed its generated
TB in 229 cycles. Demos: `demo/01_soc_from_prompt.sh`,
`demo/02_wrap_new_core.sh`.

## 10. Coding Conventions

- **SystemVerilog:** Follow PULP-platform style — `lowercase_snake_case`, explicit types, packed structs, `typedef` before use
- **Python:** Follow x-heep's `util/mosaic_gen/` patterns — class-based config, type hints, dataclasses where appropriate
- **Config files:** HJSON for declarative configs, Python for programmatic configs (multi-core, conditional logic)
- **Mako templates:** Use `% if`/`% endif` for conditionals, `${expr}` for substitution, `<% %>` for Python blocks
- **File naming:** `snake_case.sv` for RTL, `snake_case.sv.tpl` for templates, `snake_case.py` for Python
- **Comments:** `//` for SV, `#` for Python. Document non-obvious design decisions inline

## 11. Common Pitfalls

- **Don't use Bender for x-heep dependencies** — x-heep uses FuseSoC exclusively. Use Bender only for PULP IPs that require it (CVA6, FlooNoC), bridging with `bender fusesoc`
- **Don't hardcode master/slave indices** — they're parameterized in `core_v_mini_mcu_pkg.sv.tpl`. Use the named constants
- **Don't forget to regenerate RTL** — after editing `.tpl` files, run `make mcu-gen` before testing
- **Don't mix OBI and AXI without conversion** — use `axi_obi` converters from `refs/IP_Interconnect_Catalog/axi_obi/`
- **SERV/QERV use Wishbone, not OBI** — SCI wrapper must handle Wishbone ↔ OBI protocol conversion
- **FazyRV also uses Wishbone** — same conversion needed. FazyRV also supports `MEMDLY1` mode (fixed-delay, no handshake)
- **CVA6 uses AXI4 and Bender** — integration path differs from OBI/FuseSoC cores. Needs `bender fusesoc` bridge
- **Memory map addresses are in the package template** — update `core_v_mini_mcu_pkg.sv.tpl` carefully, all address rules are there
- **Debug subsystem supports `NRHARTS`** but the rest of the SoC assumes single-core — don't assume multi-core debug "just works"
- **Don't keep x-heep's simple DMA** — it must be replaced with iDMA. The old `x-heep:ip:dma_subsystem` lacks ND transfers, error handling, multi-port support, and burst legalization. iDMA's register frontend + ND midend + OBI backend is the direct replacement with zero protocol conversion and no new external dependencies.
- **Don't commit generated `.sv` files** — only `.sv.tpl` templates should be version-controlled
- **Don't modify files in `refs/`** — these are read-only reference IPs for study

## 12. Key File Reference

| Path | Purpose |
|------|---------|
| `README.md` | Full project description, architecture, timeline, team |
| `docs/architecture.jpg` | Architecture diagram |
| `docs/init_arch_by_phase.svg` | Phase-by-phase architecture diagram |
| `mosaic.yaml` | MOSAIC-SoC Phase 1 config — drives the entire multi-core generation flow |
| `hw/sci/fazyrv_sci.sv` | FazyRV SCI wrapper — Wishbone Classic → OBI (separate I+D ports) |
| `hw/sci/serv_sci.sv` | SERV via servile SCI wrapper — Wishbone Lite → OBI (unified port) |
| `hw/sci/sci.core` | FuseSoC core for both SCI wrappers |
| `hw/tdu/rtl/tdu.sv` | Task Dispatch Unit — 8-deep task FIFO, per-core wake, CPI array, energy counter |
| `hw/tdu/rtl/tdu_pkg.sv` | TDU package — register map, scheduling modes, task descriptor format |
| `hw/tdu/tb/tdu_tb.sv` | TDU self-checking Verilator testbench (22/22 checks pass, incl. targeted auto-wake) |
| `hw/tdu/tdu.core` | FuseSoC core for the TDU |
| `hw/vendor/mosaic/fazyrv/` | Vendored FazyRV RTL (from refs/) |
| `hw/vendor/mosaic/serv/` | Vendored SERV + servile RTL (from refs/) |
| `hw/vendor/mosaic/idma/` | Vendored iDMA RTL (generated + static, OBI backend + reg frontend + x-heep wrapper) |
| `util/mosaic_gen/mosaic_config.py` | MOSAIC YAML parser → XHeep multi-core config (overlays on base HJSON) |
| `scripts/fusesoc-setup.sh` | FuseSoC build setup helper (resolves deps + generates files, excludes refs/) |
| `core-v-mini-mcu.core` | Top-level FuseSoC core — includes TDU + SCI fileset deps |
| `refs/IP_Cores_Catalog/` | 5 RISC-V cores: serv, qerv, FazyRV, ibex, cva6 |
| `refs/IP_Interconnect_Catalog/` | 4 IPs: FlooNoC, TeraNoC, axi_obi, iDMA |
| `refs/IP_SoCs_Catalog/` | 8 SoC designs: pulp, pulp_cluster, pulpissimo, pulpino, mempool, chipyard, adam, x-heep |
| `refs/IP_Tools/` | bender, fusesoc |
| `refs/IP_SoCs_Catalog/x-heep/util/mosaic_gen/` | x-heep Python generator (mcu_gen.py, xheep.py, cpu/, load_config.py) |
| `refs/IP_SoCs_Catalog/x-heep/hw/core-v-mini-mcu/` | x-heep RTL templates (.sv.tpl) and static RTL |
| `refs/IP_SoCs_Catalog/x-heep/configs/` | x-heep configuration files (.hjson, .py) |

## 13. Phase 1 Progress

### Completed
- ✅ **Config-driven generation**: `mosaic.yaml` → `make mosaic-gen` → 37 RTL templates
- ✅ **Multi-core XHeep API**: `XHeep.set_cpus()`, `num_harts()`, `is_multi_core()`, `CpuConfig` dataclass
- ✅ **Mosaic YAML parser**: `util/mosaic_gen/mosaic_config.py` — overlays multi-core topology on base HJSON
- ✅ **Per-core master indices**: `CORE0..N_{INSTR,DATA}_IDX` in `core_v_mini_mcu_pkg.sv.tpl`
- ✅ **Multi-core cpu_subsystem**: generate-loop over heterogeneous cores (cv32e20 + fazyrv_sci + serv_sci)
- ✅ **Multi-master system_bus**: per-core OBI master ports, DMA index fix, ext slave fix
- ✅ **Per-hart interrupt routing**: TITAN gets full vector, ATLAS/NANO get timer-only + TDU wake
- ✅ **Per-core hart IDs**: `hart_id_array` with sequential IDs
- ✅ **TDU (Task Dispatch Unit)**: `hw/tdu/` — 8-deep task FIFO, wake pulses, CPI array, energy counter — Verilator lint-clean, 22/22 checks pass
- ✅ **TDU instantiation**: reg-bus tap in `ao_peripheral_subsystem.sv.tpl`, wired through top-level
- ✅ **SCI wrappers**: `hw/sci/fazyrv_sci.sv` + `hw/sci/serv_sci.sv` — Verilator lint-clean with vendored cores
- ✅ **Vendored cores**: `hw/vendor/mosaic/fazyrv/` + `hw/vendor/mosaic/serv/` with FuseSoC `.core` files
- ✅ **iDMA (pulp-platform 0.6.5, functionally verified)**: `hw/vendor/mosaic/idma/` — rw_obi backend + ND midend + `idma_reg32_3d` reg frontend + `idma_mosaic_wrapper` matching x-heep's DMA interface. Conditionally instantiated in `ao_peripheral_subsystem.sv.tpl` for multi-core mode (active in the PoC). **The wrapper was rewritten** against the latest iDMA module interfaces (the original was version-skewed and did not elaborate): it builds the 1D/ND request + OBI meta-channel types, wires reg-fe → id-gen → `idma_nd_midend` → `idma_backend_rw_obi`, and converts the backend's pulp-platform OBI masters to x-heep's `obi_pkg` bus. Required vendoring the **OBI package** (`hw/vendor/pulp_platform/obi` v0.1.2 + `obi.core`) and fixing `idma.core` (missing `idma/typedef.svh` include dir; dropped `*_synth` wrappers that pull an un-vendored AXI backend). **Tested with cocotb+Verilator** (`tb/idma/`): a mem-to-mem copy programmed via the register frontend completes correctly at **per-block** (dual-port memory) AND **SoC level** (shared, arbitrated memory) — `TESTS=1 PASS=1` each. `mosaic:ip:idma` + `pulp-platform.org::obi` resolve in FuseSoC; `make mosaic-gen` is EXIT=0.
- ✅ **FuseSoC integration**: TDU + SCI + iDMA filesets in `core-v-mini-mcu.core`

### Remaining
- ✅ **QERV**: reuses the W-parameterized `serv_sci.sv` at `W=4` (no new wrapper/vendor) — `qerv` branch in `cpu_subsystem.sv.tpl`. Elaborates Verilator-clean.
- ✅ **Ibex**: `hw/sci/ibex_sci.sv` (req/gnt→OBI) + self-contained vendored core `hw/vendor/mosaic/ibex/` (`mosaic:ip:ibex`) + `ibex` branch + `AVAILABLE_CPUS`/`sci.core` entries. Wrapper + full Ibex hierarchy Verilator lint-clean. NOTE: the vendored core bundles its own lowRISC prim closure; co-building with cv32e20 in a full SoC needs a prim de-dup step (see `ibex.core` header) — generation (template render) is unaffected.
- ✅ **CVA6 (SIM-ONLY, 2026-07-11)**: integrated as a **32-bit** cv32a65x derivative — vendored WT-cache subset at `hw/vendor/mosaic/cva6/` with a MOSAIC config package (`cv32a6_mosaic_config_pkg.sv`: CvxifEn=0, data side fully uncached for sentinel-polling coherence, NonIdempotent PMA over `0x2000_0000+`, DCacheType=WT), `hw/sci/cva6_sci.sv` (folds the burst-capable `mosaic_axi_burst_to_obi` bridge, 64-bit AXI → 32-bit OBI), and a `cva6` branch in `cpu_subsystem.sv.tpl` (unified OBI port). Verified: `configs/mosaic_cva6.yaml` (cva6 TITAN + fazyrv + serv) and `configs/mosaic_new_cores.yaml` (cva6 + snitch + picorv32) reach EXIT SUCCESS in the full-SoC TDU wake demo. **The GF180 tapeout exclusion still stands** — ~80 kGE + caches does not fit the 1.249 mm² PoC budget; do not add cva6 to tapeout configs.
- ✅ **PicoRV32 (2026-07-10)**: `hw/vendor/mosaic/picorv32/` (YosysHQ picorv32.v @ f00a88c, the spimemio vendoring pin) + `hw/sci/picorv32_sci.sv` (native mem port → unified OBI, serv-style single-outstanding + reset-hold dormancy). Verified: `configs/mosaic_picorv32.yaml` wake demo EXIT SUCCESS (both workers picorv32).
- ✅ **Snitch (2026-07-10)**: bare mempool-flavor integer core vendored at `hw/vendor/mosaic/snitch/` (local divergences: extension `ifdef` defaults 1'bX→0; fork-only fpnew config blobs removed; one perf-`ifdef` guard) + `hw/sci/snitch_sci.sv` (instr refill + TCDM reqrsp → split OBI; TCDM writes get no p-channel response — handled). RV32I (acc port tied; RVM needs `snitch_shared_muldiv` first). Verified: `configs/mosaic_snitch.yaml` wake demo EXIT SUCCESS (both workers snitch).
- ✅ **Rocket + BOOM v3 (RV64, SIM-ONLY, 2026-07-12)**: extracted **RocketTile** and **SmallBoomV3 BoomTile** closures from ONE chipyard 1.14.0 elaboration (`MosaicRocketBoomConfig`, 64-bit sbus — single firtool namespace, so both tiles co-exist in one Verilator build), vendored at `hw/vendor/mosaic/berkeley/` (299 modules; reproducible via `extract_tile_closure.py`, incl. the RESET_VECTOR re-parameterization — upstream folds the tile boot address to the bootrom hang 0x10000). Bridged by `hw/vendor/mosaic/tl_obi/mosaic_tilelink_to_obi.sv` (TL-C→OBI: Acquire/GrantData/GrantAck refills, Release(Data) writebacks, uncached Get/Put; unit TB `tb/tl_obi/run.sh`, 21 checks) with **window translation**: code via the tile-cacheable DRAM alias `0x8000_0000|addr`→SRAM, sentinels via the uncached CLINT range `0x0200_0000+off`→`0x3000+off`, TDU via the uncached PLIC range `0x0C00_0000+off`→`0x200A_0000+off` — shared state is uncached BY CONSTRUCTION (the CVA6 coherence trick, generalized). Workers use `prog/{atlas_tl,nano_tl}.S` (CLINT-window sentinel stores; rv32i encodings are valid RV64I). Wrappers `hw/sci/{rocket,boom}_sci.sv` (unified OBI port, reset-hold dormancy inverts to the tiles' active-high reset). **Never part of the GF180 tapeout.**

### Bug fixes & flow (later additions)
- ✅ **`make mosaic-gen`/`mcu-gen` FuseSoC fix**: the register-gen step now routes through `scripts/fusesoc-setup.sh` (refs-excluding cores-root). A bare `--cores-root .` made FuseSoC recurse into `refs/` and crash on the 0-byte `refs/IP_Tools/fusesoc/tests/capi2_cores/misc/empty.core`. (`.fusesoc.conf` is inert — FuseSoC only auto-loads `fusesoc.conf` without the dot.)
- ✅ **`.gitignore`**: generated `hw/core-v-mini-mcu/core_v_mini_mcu.sv` is now ignored (was the only generated `.sv` output that escaped the ignore list).
- ✅ **Librelane GF180 flow is fail-closed**: `flow/librelane/` has the config, slot, pad frame, PDN/SDC, and a strict physical-bundle preflight. The checked-in `mosaic_soc_core.sv` remains an unbound placeholder and is rejected; hardening requires hash-checked bound/flattened RTL plus 32-KiB SRAM GDS/LEF/LIB/RTL views. No DRC/LVS-clean result is currently claimed (see `flow/librelane/README.md`).
- ✅ **All-cores acceptance config**: `configs/mosaic_all_cores.yaml` (cv32e20 + ibex + fazyrv + qerv + serv) — `make mosaic-gen MOSAIC_CFG=configs/mosaic_all_cores.yaml` renders all 5 SCI branches (`NUM_HARTS=5`).
- ✅ **Multi-core simulation harness**: `tb/mosaic/` (`run.sh` + `mosaic_multicore_tb.sv` + `tb_obi_mem.sv`, config `configs/mosaic_sim.yaml`). Verilates the **real generated `cpu_subsystem`** and runs the SCI-wrapped serial cores against per-hart OBI memories with a hand-assembled RV32I program (Verilator only — no cocotb / GCC needed, though both are available). Result: **3/3 cores PASS — SERV, QERV and FazyRV all boot, fetch, execute and complete the store.** cv32e20 is excluded (its CV-X-IF interfaces are left unconnected by `cpu_subsystem`, so it only elaborates via the full `core_v_mini_mcu` testharness + boot_rom + RISC-V GCC).
- ✅ **TDU SoC-level test + integration bug fix**: `tb/tdu/soc/` (cocotb+Verilator) reproduces the ao-peripheral reg-bus tap and drives the TDU at its real SoC address (`0x200A0000`). It **caught a real integration bug**: the tap passed the *full* SoC address to the TDU, which decodes by bare *offset* (`TDU_*_OFFSET=0x00..`), so the TDU was **unreachable through the bus** (every task/wake/mode access silently returned 0). Fixed in `ao_peripheral_subsystem.sv.tpl` — the tap now subtracts `TDU_START_ADDRESS`. With the fix, SCHED_MODE RW, the 8-deep task FIFO (push/status/pop), `WAKE_REQ`→`core_wake` pulse, and `CORE_STATUS`-reflects-`core_sleep` all pass (`TESTS=1 PASS=1`).
- ✅ **TDU wake→core loop closed**: `cpu_subsystem` now has a per-hart `core_wake_i [NUM_HARTS]` input wired from `TDU.core_wake_o` in `core_v_mini_mcu.sv`. Each **worker** core (role ≠ `titan`) boots **dormant** — a per-hart run-enable latch (set by a `core_wake_i` pulse) gates its `fetch_enable`; **TITAN** boots immediately (`fetch_enable=1`). The serial SCI wrappers (`serv_sci`/`fazyrv_sci`), which have no native fetch-enable, emulate dormancy by holding the core in reset and masking their OBI request strobes until woken, and report `core_sleep_o=~fetch_enable` so the TDU's `CORE_STATUS` shows which workers are parked. Verified end-to-end by `tb/mosaic/cocotb` (`test_mosaic.py`): no-wake → all parked; wake hart 0 only → only hart 0 runs (per-hart, not global); wake the rest → all execute (`TESTS=1 PASS=1`). The cv32e20/cv32e40px/ibex branches gate via their native `fetch_enable_i`. Remaining work is the **policy** layer (TITAN FreeRTOS firmware that programs `WAKE_REQ`/`TASK_PUSH`).
- 🐛 **Packed↔unpacked port mismatch in the multi-core top (full-SoC elaboration blocker, found via adversarial review + Verilator lint)**: `core_v_mini_mcu.sv` declared `debug_req`/`core_sleep`/`core_wake` as **packed** `[NRHARTS-1:0]` but the multi-core `cpu_subsystem` declares `debug_req_i`/`core_sleep_o`/`core_wake_i` as **unpacked** `[NUM_HARTS]` arrays. Connecting a packed vector to an unpacked array port is illegal (IEEE 1800 §7.6) — Verilator: *"Illegal port connection … mismatch between port which is an array, and expression which is not an array."* This was latent because the standalone harnesses (`tb/mosaic`) drive `cpu_subsystem` with *unpacked* arrays, and the full `core_v_mini_mcu.sv` had never been elaborated (needs the FuseSoC build + toolchain). `debug_req_i`/`core_sleep_o` were **pre-existing**; `core_wake_i` was newly added by the wake fix above. **Fixed** in `core_v_mini_mcu.sv.tpl` (is_mc path) by adding unpacked adaptation arrays (`debug_req_arr`/`core_wake_arr`/`core_sleep_arr`) with an `always_comb` packed↔unpacked conversion at the integration boundary; the SoC keeps packed signals for the TDU's packed ports + `core_running = ~core_sleep`. Verified by linting a faithful replica of the top's full instantiation (all per-hart ports) → **no port-mismatch errors**; both `tb/mosaic` harnesses still pass (no regression).
- 🐛 **Inline Mako directive in `core_v_mini_mcu.sv.tpl` → multi-core top was literally uncompilable (found via a full-SoC Verilator lint)**: the `cpu_subsystem` param list had `.DM_HALTADDRESS(DM_HALTADDRESS)% if is_mc:,` / `.NUM_HARTS(NRHARTS)% endif` with the `%` control directives **inline** (mid-line). Mako only treats `%` as a control statement when it is the **first non-whitespace char on a line**, so these passed through as **literal text** — the generated multi-core `core_v_mini_mcu.sv` contained `...(DM_HALTADDRESS)% if is_mc:,` verbatim → Verilator: *"syntax error, unexpected '%'"*. This is why the multi-core SoC top had **never compiled** (consistent with the stale FuseSoC build dir having no `obj_dir`/no verilation). **Fixed** by moving the directives to line-leading form (`% if is_mc:` / `% else:` / `% endif`). Swept all `*.tpl` for the same pattern — the only other hits are inside Mako comments (`## % for`) in a vendored OpenTitan template (harmless). A reusable full-SoC lint (remap FuseSoC `.vc` filelist → live sources) now elaborates the top + `cpu_subsystem` + SCI wrappers + serv/fazyrv + TDU **clean**; the only residual lint friction is the iDMA wrapper's OBI-typedef include packaging (separate subsystem, has its own passing `tb/idma` tests). See [[full-soc-never-elaborated]].
- 🐛 **Three bugs found+fixed via the harness:** (1) **FazyRV reset polarity** — `hw/sci/fazyrv_sci.sv` connected `.rst_in(~rst_ni)`, but FazyRV's `rst_in` is active-low (like `rst_ni`); the inversion held FazyRV in reset during operation (PC pinned at `BOOTADR`, regfile writes gated off). Fixed to `.rst_in(rst_ni)` (now `.rst_in(rst_ni & fetch_enable_i)` after the wake-gating change) — a real PoC bug (any FazyRV/ATLAS core would never execute). (2) `core_v_mini_mcu_pkg.sv.tpl` emitted `CpuType = <first-core-name>`, invalid when the first core is an SCI core (not in `cpu_type_e`) — now falls back to a valid enum value. (3) the `cpu_subsystem.sv.tpl` FazyRV branch defaulted `rftype=LOGIC` while `conf=CSR`, an invalid FazyRV combo (asserts at elaboration) that also affected the PoC — `rftype` now defaults to `BRAM_DP_BP`.

### Multi-fabric system bus (bus: obi | log | floonoc)
- ✅ **Config seam**: `bus:` in mosaic.yaml is no longer cosmetic. `obi`→`BusType.NtoM` (existing crossbar), `log`→`BusType.LOG`, `floonoc`→`BusType.FLOONOC`; `axi` is now a hard error. Optional `bus_opts:` mapping (validated, typo-rejecting): `log: {topology: lic|bfly2|bfly4, num_banks: auto|N}`, `floonoc: {route_algo, endpoints}`. SV `bus_type_e` widened to 2 bits. Pytest: `test/test_mosaic_gen/test_bus_types.py` (10 tests).
- ✅ **`bus: log` — two-tier logarithmic interconnect**: per-master 1-rule demux splits `[0, MEM_SIZE)` (memory tier) from the rest; memory tier = classic fixed-latency `tcdm_interconnect` (already vendored under `hw/vendor/xheep/cluster_interconnect/rtl/tcdm_interconnect/`, now compiled via a new `files_rtl_tcdm` fileset) over word-interleaved banks with per-bank RR arbitration; non-memory tier = the varlat crossbar over ERROR/DEBUG/AO/PERIPHERAL/FLASH. The generator rebuilds RAM as ONE interleaved group (`num_banks` auto = next_pow2(bus masters); LIC hard-requires banks >= masters — `tcdm_interconnect.sv:318` is sim-only, the Python `XHeep._validate_log_bus()` is the authoritative gate; bank cap raised to 32 for LOG). Configs: `mosaic_log.yaml` (3 harts, 16 banks), `mosaic_log_poc.yaml` (PoC, 32x2KB), `mosaic_wake_demo_log.yaml` (32 banks — the TESTHARNESS adds EXT_XBAR_NMASTER=8 masters). Verified: `tb/log_xbar/run.sh` (T1 interleave sweep, T2 same-cycle parallel-bank grants, T3 same-bank RR, T4 mid-stream peripheral, T5 unmapped→ERROR — all PASS) and the full-SoC TDU wake demo **EXIT SUCCESS** on the LOG fabric.
- ✅ **`bus: floonoc` — FlooNoC AXI NoC**: floogen (installed into .venv from `refs/IP_Interconnect_Catalog/FlooNoC`, see util/python-requirements.txt) generates `hw/ip/floonoc_fabric/floo_mosaic_noc{_pkg,}.sv` from the topology emitted by `util/mosaic_gen/floonoc_gen.py` (compact: per-hart I+D merged → one AXI manager endpoint each, debug+DMA+EXT → `shared`, two subordinates `mem`/`periph`; single router, ID-table routing; stubs are written for non-floonoc configs so `mosaic:ip:floonoc_fabric` always resolves). MOSAIC-owned bridges `hw/vendor/mosaic/axi_obi/xheep_{obi_to_axi,axi_to_obi}.sv` (x-heep obi structs as type params — pulp obi_pkg is never compiled, idma-wrapper pattern; no atomics, 32/32). FlooNoC RTL vendored (`mosaic:ip:floonoc`, single-AXI subset) with 3 documented local patches for the common_cells 1.38-vs-1.39 skew (addr_decode NoIndices; 4-arg `ASSERT`/2-arg `ASSERT_INIT` vs OpenTitan prim_assert). Verified: bridge loopback + NoC smoke cocotb (`tb/floonoc/cocotb/run.sh [stage2]`, PASS) and the full-SoC TDU wake demo **EXIT SUCCESS** over the NoC.
- 🐛 **floogen router-table gap (found via the wake demo)**: floogen's `gen_router_tables` emits ID-table rules only for SUBORDINATE endpoints, so response flits to manager-only endpoints (our harts) missed the table and the router's default-less decode sent them all to port 0 — hart 0 accidentally worked, every other hart hung on its first fetch. `floonoc_gen.py::_patch_router_map` now rewrites the generated map to the full identity map (router port i == endpoint id i in the single-router topology).
- 🐛 **Stale-generated-RTL hazards in the full-SoC sim flow (found via the LOG wake demo)**: (1) `tb/mosaic_soc/run.sh` used to skip the FuseSoC register-gen pass — a power manager generated for FEWER banks leaves the extra banks power-gated (reads return 0 while the backdoor-loaded data is visibly present). run.sh now re-runs `scripts/fusesoc-setup.sh` (with RISCV_XHEEP/COMPILER_PREFIX for the boot-ROM generator). (2) `gen_filelist.py` now remaps `floo_mosaic_noc{_pkg,}.sv` to the live generated sources (same config-dependence hazard as `core_v_mini_mcu_pkg.sv`). (3) `tb/mosaic_soc/tb_util.svh` is now GENERATED from `tb_util.svh.tpl` (bank count/size/interleave are config-dependent; the old static copy hardcoded 2x32KB banks).
- ✅ **mosaic `topo-viz` skill** (soc-topgen-ui-inspired): `python -m harness topo-viz check <cfg>` (semantic checks beyond schema: LOG bank constraint, derived address-map overlap, inert/unknown bus_opts) and `topo-viz render <cfg> -o topology.html` (self-contained interactive HTML/SVG, per-fabric layered diagram + memory-map table). Pytest: `test_topo_viz.py`.

### FuseSoC Build Verification
- ✅ **FuseSoC resolves all MOSAIC-SoC cores**: `mosaic:ip:tdu`, `mosaic:ip:idma`, `mosaic:ip:sci`, `mosaic:ip:fazyrv`, `mosaic:ip:serv`, `mosaic:ip:servile` all successfully prepared.
- ✅ **All x-heep dependencies resolve** (cv32e20, peripherals, OpenTitan IPs, PULP IPs).
- ⚠️ **Full build requires external tools**: RISCV `elf-gcc` toolchain (for boot_rom) and `regtool`/`periph_structs_gen` tools. Use `scripts/fusesoc-setup.sh` to reproduce.
