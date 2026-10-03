# GF180 area study: FazyRV + 2×SERV against a 1.25 mm² budget

> **Date:** 2026-07-28
> **Question asked:** can a SoC of 1× FazyRV (1-bit config) + 2× SERV, no
> on-chip memory, with UART/SPI/GPIO, fit in ≤ 1.25 mm² on GF180MCU?
> **Short answer:** the cores fit easily. **On-chip SRAM does not.** At GF180's
> SRAM macro density, 16 KB of SRAM alone is 6.7 mm² — 5.4× the entire budget.
> The instinct to drop on-chip memory is not an optimization, it is a
> requirement.
> **Follow-up questions (2026-07-28):** is flash-XIP feasible, can FazyRV be a
> TITAN, and can the config drop on-chip SRAM entirely in favour of external
> boot ROM + external SRAM? Answers in §6 and §7: **XIP exists but is
> TITAN-only** — workers are copied into SRAM and their `boot_addr` is
> schema-validated to be inside it, so a no-SRAM config is not expressible
> today. FazyRV-as-TITAN fails for a documented reason and is unproven.
> **Status of numbers:** synthesis-level (yosys + GF180 liberty) and macro LEF
> geometry. **No place-and-route was run.** See §5.

## 1. What was actually run

| Stage | Result |
|---|---|
| `config-author validate` | PASS (after two repairs, §3) |
| `topo-viz check` | clean |
| `flow-runner mosaic-gen-config` | PASS, 117.8 s |
| `flow-runner tb-soc-generic` | **`EXIT SUCCESS — all 4 configured harts executed ✓`**, 88.7 s |
| Area | yosys synthesis to `gf180mcu_fd_sc_mcu7t5v0`, tt/25 °C/5.00 V + SRAM macro LEF |
| LibreLane hardening | **not run** — `librelane` is not installed on this machine |

The working config is `build/area_study/mosaic_area_prod.yaml`.

## 2. Measured GF180 areas

Standard-cell area from `yosys stat -liberty`, after `synth -flatten`,
`dfflibmap`, `abc`. This is the **sum of cell areas before placement**.

| Block | Cell area | mm² |
|---|---:|---:|
| SERV (`serv_top`) | 21,151 µm² | 0.021 |
| FazyRV `CHUNKSIZE=1` | 182,654 µm² | 0.183 |
| FazyRV `CHUNKSIZE=8` | 188,366 µm² | 0.188 |
| cv32e20 / cve2 | not measured | — |

SRAM macro geometry, from `gf180mcu_fd_ip_sram/lef` `SIZE` statements:

| Macro | Capacity | Size | Area |
|---|---:|---|---:|
| `sram64x8m8wm1` | 64 B | 431.9 × 232.9 µm | 0.101 mm² |
| `sram128x8m8wm1` | 128 B | 431.9 × 268.9 µm | 0.116 mm² |
| `sram256x8m8wm1` | 256 B | 431.9 × 340.9 µm | 0.147 mm² |
| `sram512x8m8wm1` | **512 B** | 431.9 × 484.9 µm | **0.209 mm²** |

**The largest available GF180 SRAM macro holds 512 bytes and costs 0.209 mm².**
That is the single fact that decides this design.

## 3. Two findings that stopped the build

### 3.1 FazyRV cannot be the TITAN — and the validator accepts it anyway

The requested topology (FazyRV controller + 2 SERV) was authored, **passed
`config-author validate` and `topo-viz check`**, generated RTL, and then failed
in simulation two different ways:

- `chunksize: 1, conf: MIN` → `Out of bound memory access 0x80100000` at
  1290 ns. That address maps to no region at all (RAM0 `0x0`, DEBUG
  `0x10000000`, AO peripherals `0x20000000`, peripherals `0x30000000`,
  EXT slave `0xF0000000`) — the core jumped to a wild PC.
- `chunksize: 8`, default `conf` → no wild access, but the SoC hangs to the
  20 ms cycle limit and never reports.

Cause: the TITAN is the leading hart that boots and drives the TDU, and

```
fazyrv   capabilities = ['split_obi', 'timer_interrupt']
serv     capabilities = ['unified_obi', 'timer_interrupt']
cv32e20  capabilities = ['debug', 'interrupts', 'mhartid', 'split_obi']
```

FazyRV has **neither `mhartid` nor `interrupts`**. Every shipped config uses an
mhartid-capable core as TITAN and FazyRV/SERV only as workers. Nothing in
`validate_soc_config` enforces this: `cores[0].role: titan` is accepted for any
IP. **This is a validator gap and should become a cross-field rule** — a TITAN
in an AMP topology must declare `mhartid` and `interrupts`. `tb-matrix`'s
`titan_ip` axis should be constrained the same way (it currently checks
`mhartid` only for the multi-TITAN SMP shape).

`conf: MIN` was also accepted without complaint despite removing CSR support
that the boot path needs — a second cross-field contract worth adding.

### 3.2 SRAM sizing is gated on boot-image fit, and it bites early

`sram_kb: 8` was rejected:

```
boot images plus shared-control and minimum stack do not fit SRAM:
need through 0x00002600, SRAM ends at 0x00002000
```

With four harts at 4 KB boot spacing, 16 KB was also rejected (`need through
0x4600`). Packing the worker slots to `0x1000 / 0x1800 / 0x2000` fits 16 KB.
**`memory.sram_kb` has a hard floor of 8 and must be a power of two, so
"no on-chip memory" is not currently expressible in the schema at all.**

## 4. The budget arithmetic

Working configuration: cv32e20 TITAN + FazyRV(chunk 1) + 2× SERV, 16 KB SRAM,
1 KB boot ROM, OBI, TDU dynamic, UART/SPI/GPIO.

| Component | mm² (cell area) |
|---|---:|
| FazyRV chunk 1 | 0.183 |
| SERV × 2 | 0.042 |
| cv32e20 TITAN | not measured |
| bus + TDU + 3 peripherals + boot ROM | not measured |
| **16 KB SRAM (32 × sram512x8)** | **6.701** |
| **Total** | **≥ 6.93** |

Against a 1.25 mm² budget that is **5.5× over, from SRAM alone**, before
placement overhead and before the unmeasured blocks.

SRAM cost is linear and brutal — **0.419 mm² per KB**:

| SRAM | Macros | Area |
|---:|---:|---:|
| 1 KB | 2 | 0.419 mm² |
| 2 KB | 4 | 0.838 mm² |
| 4 KB | 8 | 1.675 mm² |
| 8 KB | 16 | 3.350 mm² |
| 16 KB | 32 | 6.701 mm² |
| 32 KB (required by `target: tapeout`) | 64 | 13.402 mm² |

**A 1.25 mm² die can hold at most 5 SRAM macros — 2.5 KB — with zero logic.**

Two further consequences:

- **`target: tapeout` currently forces `sram_kb: 32`**
  (`core_registry.py:365`), i.e. 13.4 mm² of SRAM. The tapeout target and a
  1.25 mm² budget are mutually exclusive in the schema as written.
- **The 1-bit FazyRV config buys almost nothing.** `CHUNKSIZE=1` is only 3%
  smaller than `CHUNKSIZE=8` (0.183 vs 0.188 mm²), because the register file
  dominates and is synthesized to flip-flops either way. If area is the goal,
  `rftype` and the RF implementation matter far more than `chunksize` — and
  note that SERV is **8.7× smaller than FazyRV** at 0.021 mm². *If you want
  the smallest cores, use SERV for all three.*

## 5. What these numbers are not

Per the roadmap's own truth rules (§12.3, §14), fidelity is stated explicitly:

- **Quality: `post_synthesis_estimate`.** `yosys stat` reports the sum of
  standard-cell areas. Real die area after floorplanning and routing is
  larger — at a typical 50–60 % core utilization, roughly **1.6–2×** the cell
  area, plus the pad ring for a full chip.
- **No place-and-route, no DRC, no LVS, no STA.** `librelane` is not installed
  here and the flow needs a hashed `PHYSICAL_BUNDLE`. Nothing in this document
  is physical qualification.
- **The TITAN and the platform periphery are unmeasured** (yosys cannot parse
  the lowrisc/cve2 SystemVerilog; LibreLane uses a pre-elaborated netlist).
  The totals above are therefore **lower bounds**.
- SRAM macro areas are LEF geometry, which is exact, but the number of macros
  assumes a simple 512 B × N banking with no routing channel overhead between
  them.

## 6. Flash-XIP feasibility

**XIP already exists, and it is TITAN-only. That is the whole answer.**

`util/xheep_gen/pack_flash.py` packs a bootable SPI-flash image and emits

```json
"boot_mode": "spi-memio-xip-titan-load-workers",
"boot_straps": {"boot_select": 1, "execute_from_flash": 1}
```

The testbench already honours it (`+boot_sel`, `+execute_from_flash` plusargs
in `tb/tb_top.sv`). The model is:

| Hart | Where its code lives | Needs on-chip SRAM? |
|---|---|---|
| TITAN (hart 0) | **executes in place** from the memory-mapped flash window at `0x4000_0180` | **No** |
| Workers | **copied into SRAM** by the TITAN cold-boot loader, CRC-checked, then TDU-woken | **Yes** |

`pack_flash.py:69` is explicit — a worker image that does not fit its SRAM slot
is an error: *"image {id} is {n} bytes, exceeds SRAM slot {max_size}"*.

### 6.1 Why workers cannot XIP today

`core_registry.py:626-631`:

```python
if type(sram_kb) is int and 8 <= sram_kb <= 512:
    if address < 0 or address >= sram_kb * 1024:
        errors.append(
            f"cores[{index}].boot_addr 0x{address:08x} must select SRAM "
            f"[0x00000000, 0x{sram_kb * 1024:08x})")
```

A worker's `boot_addr` is **validated to lie inside on-chip SRAM**. Pointing a
worker at the flash window (`0x4000_xxxx`) or the external-slave window
(`0xF000_0000`, 16 MB, already present in the address map) is rejected at
schema time. And `memory.sram_kb` has a floor of 8 and must be a power of two,
so there is no way to ask for zero.

**Conclusion: "no on-chip SRAM" is not expressible today for any AMP topology
that has workers.** The best currently reachable configuration is
XIP-TITAN + 8 KB SRAM = **3.35 mm² of SRAM**, still 2.7× the 1.25 mm² budget.

### 6.2 What a no-SRAM profile would require

Five changes, in dependency order:

1. **Allow `memory.sram_kb: 0`** and stop generating SRAM banks — currently a
   hard floor of 8 (`core_registry.py:586`).
2. **Widen the `boot_addr` window** so a worker may boot from the flash window
   or the external-slave window, with the check becoming "must select a
   declared executable region" rather than "must select SRAM".
3. **Route worker instruction fetch to the flash window.** The SPI memory-mapped
   window is single-ported and slow; N bit-serial workers all fetching through
   it will serialize. Needs a bus-fabric decision, not just a config flag.
4. **Give the TITAN loader a no-copy path** (or a copy-to-external-SRAM path),
   since its current job is precisely to stage workers into SRAM.
5. **Linker scripts and `software_gen` load slots** must target the external
   region. `boot_slots` resolution in `validate_soc_config` assumes SRAM
   addresses throughout.

Steps 1, 2 and 5 are schema/generator work. Step 3 is an architecture decision.
Step 4 is firmware.

**Writable memory is still required regardless.** Flash is read-only, so stack,
`.data` and `.bss` must live somewhere. With no on-chip SRAM that means an
external SRAM on the `EXT_SLAVE` window (`0xF000_0000`, size `0x0100_0000`),
which the address map already provides but which nothing in the config schema
currently targets. This is the configuration you described — external boot ROM
*and* external SRAM — and it is a coherent design; it is simply not one the
generator can currently emit.

## 7. FazyRV as a TITAN

**It does not work today, and the reason is documented in the generator
itself.**

`hw/core-v-mini-mcu/cpu_subsystem.sv.tpl:99-106`:

> *TITANs without an override enter the platform `BOOT_ADDR` (normally the boot
> ROM). Reset-held workers instead enter the generated SRAM image default
> directly; **this matches `software_gen._boot_address` and avoids requiring
> tiny SCI cores to implement the boot-ROM execution contract**.*

A production TITAN's reset vector **must** be the boot ROM
(`core_registry.py:612-616` rejects `boot_addr` on a TITAN outright), so as
hart 0 FazyRV would have to execute the boot ROM sequence — which the design
deliberately does not require of the tiny SCI cores.

Two things that are *not* the blocker, checked directly:

- **Not the ISA.** `hw/ip/boot_rom/boot_rom.S` contains no CSR, multiply or
  divide instructions — it is plain RV32I, which FazyRV supports.
- **Not missing CSRs.** The template already defaults FazyRV to
  `CONF_STR("CSR")` (`cpu_subsystem.sv.tpl:590`), and `fazyrv_top.sv:173`
  shows `CONF == "CSR"` instantiates 8 CSRs, with interrupt support present for
  both `INT` and `CSR`. **The hanging run already had CSRs.**

**Not simulation slowness either.** A bit-serial core is genuinely slower, so
the 2 M-cycle default watchdog was a plausible false alarm. It was ruled out
empirically: rebuilt and rerun at `+maxcycles=20000000` (200 ms of simulated
time, 10× the default), the FazyRV TITAN **still never completes** —

```
[200000510ns] %Fatal: tb_top.sv:202: Simulation aborted due to maximum cycle limit
```

The cv32e20 reference topology finishes at 8 µs (≈800 cycles). 20 M cycles is
**25,000×** that, far beyond FazyRV's ~5× serial penalty at `CHUNKSIZE=8`.
This is a genuine hang.

So the registry's capability table is *wrong but not load-bearing here*:
`fazyrv` is declared `['split_obi', 'timer_interrupt']` even though the RTL
provides CSRs and interrupts at `CONF=CSR`. **Capabilities are static per core
when they are actually a function of the core's parameters** — that is a
registry modelling bug worth fixing independently.

### 7.1 Waveform evidence (2026-07-28)

A traced Verilator build (`--trace --trace-depth 14`) of the
`CONF=CSR, CHUNKSIZE=1, RVC=COMB, memdly1=1` TITAN, dumping VCD to the
3380 ns fault.

**The SCI wrapper is not at fault.** FazyRV's `wb_imem_adr_o` and
`wb_dmem_adr_o` are *serially rotated* registers — they carry garbage while a
transfer is being assembled and are valid only while the matching strobe is
asserted. The data address in the 22 cycles before the fault:

```
0xff140800 → 0xff8a0400 → 0xffc50200 → 0xffe28100 → 0xfff14080 →
0xfff8a040 → 0xfffc5020 → 0xfffe2810 → 0xffff1408 → 0xffff8a04 →
0xffffc500 → 0xffffe280 → 0xfffff140 → 0xfffff8a0 → 0xfffffc50  ← stb=1
```

One bit per cycle, ones filling from the top. `wb_dmem_stb` asserts only on
the last of these, and `fazyrv_sci.sv` gates `data_req_o.req` with that strobe
while driving `addr` combinationally — which is the correct contract. The OBI
request at 3370 ns is well formed: `req=1 we=0 be=1111 addr=0xfffffc50`,
`data_outstanding_q=0`.

**So `0xfffffc50` is a genuinely computed address, not a sampling artifact.**
The hart-0 liveness image is bare — `image_0.elf` `_start`:

```
180: li   t0,0
188: lui  t2,0x3        # t2 = 0x00003000
18c: add  t2,t2,t1
194: sw   t1,0(t2)      # sentinel store -> 0x3000
```

Its first store must target `0x3000`. It targeted `0xfffffc50`, with the
address register filling with ones — a datapath/register-file value
corruption inside the core, not a bus-protocol failure.

**Three sweeps narrow it further:**

| Variant | Result |
|---|---|
| `memdly1=0` (template default) | fault at 1290 ns, `0x80100000` |
| `memdly1=1`, `CHUNKSIZE` 1 / 2 / 4 / 8 | fault at 3380 ns, `0xfffffc50` — **identical at every chunk size** |
| `memdly1=1`, `CONF=INT` + `RFTYPE=LOGIC` | regresses to 1290 ns, `0x80100000` |
| `memdly1=1` on a FazyRV **worker** | **breaks a worker that passes with the default** |

Two conclusions follow.

1. **Chunk size is irrelevant** — the failure is identical from 1 to 8, so this
   is not a serial-datapath timing bug and `CHUNKSIZE=1` is not the problem.
2. **`memdly1` is not simply "wrong by default".** It is a genuine trade-off:
   `memdly1=0` is required for the worker path (SRAM, effectively zero
   latency) and `memdly1=1` gets the TITAN path (boot ROM + AO peripherals)
   substantially further. **Neither value is correct for both roles**, which
   means `fazyrv_sci.sv`'s handshake does not faithfully implement either of
   FazyRV's two memory-timing modes; it compensates in a way that happens to
   suit the low-latency worker case. That is the defect to fix, and it is in
   our wrapper, not in the vendored core.

### 7.2 What it would take

1. Make declared capabilities **parameter-dependent** (`fazyrv` at `CONF=CSR`
   gains `mhartid` + `interrupts`; at `MIN` it has neither).
2. Add the missing cross-field rule so a TITAN must declare `mhartid` and
   `interrupts` — today `fazyrv:titan` passes every static gate and fails only
   in simulation, after a 2-minute RTL build.
3. Establish whether the FazyRV SCI wrapper can actually execute the boot-ROM
   contract (fetch from the boot ROM region, poll the `soc_ctrl` exit register,
   jump to SRAM/flash). This is the real engineering work and it is unproven.
4. Verify the TDU driver path the TITAN runs is correct on a bit-serial core.

Until (3) is answered, FazyRV-as-TITAN is an open question, not a config
option.

## 8. External RAM: QSPI PSRAM vs HyperBus

**Verdict: yes, and QSPI PSRAM first.** This is the right way to solve the
read-only-flash problem, and the economics are not close.

### 8.1 Why it is the right move

The whole difficulty in §6 is that flash is read-only, so stack, `.data` and
`.bss` force on-chip SRAM, and on-chip SRAM costs 0.419 mm²/KB. An external
*RAM* makes the memory-mapped window **writable** and the requirement
disappears.

The area trade is lopsided:

| | GF180 area |
|---|---:|
| 8 KB on-chip SRAM (the old floor) | **3.350 mm²** |
| QSPI PSRAM controller (~5–10 k gates, est.) | ~0.05–0.10 mm² |
| HyperBus controller (~15–20 k gates, est.) | ~0.15–0.20 mm² |

Roughly **30× less die area for more memory** — an external QSPI PSRAM such as
an APS6404L is 8 MB. (Controller gate counts are engineering estimates, not
measured; the SRAM figure is measured from the macro LEF.)

### 8.2 Latency, and why bit-serial cores make this work

Bandwidth is not the constraint. QSPI at 100 MHz DDR is ~50–100 MB/s and
HyperBus ~200 MB/s, while a SERV hart retires roughly one instruction per
32 cycles — a few MB/s of fetch bandwidth each. Three of them do not
saturate QSPI.

**Latency is the constraint, and this is where the design is unusually
favourable.** Every cache-less fetch pays the full access latency, perhaps
8–10 cycles including command overhead. The relative penalty depends entirely
on how fast the core is:

| Core | Cycles/instruction | With ~10-cycle memory | Slowdown |
|---|---:|---:|---:|
| SERV / FazyRV (bit-serial) | ~32 | ~42 | **~1.3×** |
| cv32e20 (single-issue) | ~1–2 | ~11–12 | **~8×** |

**Bit-serial cores are uniquely well matched to slow external memory**,
because their own execution latency already dominates. This is a real
architectural synergy with what MOSAIC generates, and it is the strongest
technical argument for the whole direction. The corollary is that the
**TITAN** — the one fast core — is the one that suffers, which argues for
keeping TITAN code in XIP flash (already supported, §6) or giving it a small
on-chip scratchpad, while the workers run from PSRAM.

### 8.3 QSPI vs HyperBus

| | QSPI PSRAM | HyperBus |
|---|---|---|
| Pins | **6** (CS, CLK, IO0–3) | 12 (CS, CK, CK#, DQ0–7, RWDS) |
| Bandwidth @100 MHz | ~50–100 MB/s | ~200 MB/s |
| Reuses existing SPI pads/IP | **yes** — second chip select on the flash bus | no |
| x-heep precedent | `spi_memio`, `w25q128jw_controller`, `spi_host` | none |
| Controller complexity | lower | higher (DDR, RWDS latency handshake) |

**Recommendation: QSPI PSRAM.** It halves the pin count — which matters on a
small die with a Chipathon pad budget — reuses the SPI infrastructure and the
memory-mapped-window pattern x-heep already has in `spi_memio`, and its
bandwidth is ample for bit-serial harts. HyperBus's advantage is bandwidth,
which only pays off with cached, fast cores; that is a different design point
belonging to the roadmap's `embedded_cluster` / `coherent_application`
backends. Add it later as a second `memory.external.kind` when a core exists
that can use it.

### 8.4 What it needs in MOSAIC

1. `memory.external.kind: qspi_psram | hyperbus | sram_async` extending the
   §6.2 prototype schema (which currently describes only base and size).
2. A PSRAM controller IP plus an SCI-style wrapper — a natural `wrapper-smith`
   target, and close kin to the existing `w25q128jw_controller`, differing
   mainly in being read/write with no erase/program semantics.
3. A **writable** memory-mapped window, following the `spi_memio` pattern that
   already produces the read-only flash window at `0x4000_0000`.
4. The zero-bank MemorySS work from §6.2 — still the blocking prerequisite.
5. Linker sections and `software_gen` load slots targeting the window.

### 8.5 Honest caveats

- **Boot sequencing changes.** PSRAM must be initialised before any core can
  use RAM, so the on-chip boot ROM (or the XIP TITAN) has to bring the
  controller up *before* workers are woken. The TDU wake sequence must be
  ordered behind that.
- **On-chip ROM is still required.** Small and dense, so this is not an area
  problem — but "no on-chip memory at all" remains impossible.
- **Contention.** N cores through one QSPI port serialise. Tolerable with
  bit-serial harts; a per-core line buffer would help and costs far less than
  SRAM.
- **tCEM.** PSRAM self-refreshes but bounds how long CS may stay low, so the
  controller must break long bursts. A real design detail, not a footnote.
- **Simulation.** The TB needs a PSRAM behavioural model before any of this
  can be gated; open models exist. Without one there is no way to reach
  `EXIT SUCCESS` for an external-RAM config, and by this project's own rules
  that means it cannot be claimed.

## 8b. Full-SoC synthesis and per-subsystem breakdown (2026-07-29)

First whole-chip number, for `configs/mosaic_pico_serv_xip.yaml` (picoRV32
TITAN + 2× SERV, XIP from flash, 512 B scratchpad). Measured with the pinned
`nix develop` toolchain — LibreLane 3.0.0, yosys 0.62 with `slang.so` — against
`gf180mcu_fd_sc_mcu7t5v0` tt/25 °C/5.00 V.

**Total: 3,903,055 µm² = 3.903 mm²**, 50.7% of it sequential.

Elaboration needed a wrapper: slang refuses a top-level module with unconnected
SystemVerilog interface ports, and `core_v_mini_mcu` exposes six (the eXtension
interface). `mosaic_synth_top` instantiates one `if_xif`, ties all six to it,
and forwards the other 251 ports and 8 parameters verbatim, so what is
synthesised is the SoC. It is a measurement artifact and is deliberately not
committed as design RTL.

| Subsystem | mm² | % of SoC |
|---|---:|---:|
| `ao_peripheral_subsystem` | 1.338 | 34.3% |
| `peripheral_subsystem` | 1.025 | 26.3% |
| `cpu_subsystem` | 0.621 | 15.9% |
| `memory_subsystem` | 0.510 | 13.1% |
| `system_bus` | 0.169 | 4.3% |
| `debug_subsystem` | 0.154 | 3.9% |
| *sum* | *3.816* | *97.8%* |

Blocks measured inside those:

| Block | mm² | % of SoC |
|---|---:|---:|
| **`spi_subsystem`** (AO) | **0.717** | **18.4%** |
| `rv_plic` (peripheral) | 0.155 | 4.0% |
| `tdu` (AO) | 0.068 | 1.7% |
| `rv_timer` (AO) | 0.062 | 1.6% |
| `mosaic_clint` (AO) | 0.023 | 0.6% |
| `boot_rom` (AO) | 0.006 | 0.1% |
| AO remainder — iDMA, power manager, GPIO, fast-intr-ctrl, soc_ctrl, reg glue | 0.463 | 11.9% |

### What this changes

**The cores are 9.2% of the chip.** picoRV32 is 8.1%, both SERVs together 1.1%,
and the SCI wrappers plus wake logic cost 0.261 mm² — *six times the two SERV
cores they wrap*.

**`spi_subsystem` alone is 0.717 mm², twice all three cores combined.** It is
the single largest block in the design, and it is there because the XIP boot
path needs a memory-mapped SPI window. Execute-in-place bought us the removal
of a 3.35 mm² SRAM pool at the cost of a 0.72 mm² SPI subsystem — still a large
net win, but the SPI cost was invisible until now.

Consequently, **core selection barely moves the die**. Every swap measured in
this study is a few percent of the whole:

| Change | Saving | % of SoC |
|---|---:|---:|
| picoRV32 instead of cv32e20 | 0.149 mm² | 3.8% |
| SERV instead of a FazyRV worker | 0.161 mm² | 4.1% |
| FazyRV chunksize 1 instead of 8 | 0.006 mm² | 0.15% |

The levers that matter are the peripheral set and the platform: the SPI
subsystem, the PLIC, the debug module, the DMA, and the SCI wrapper overhead —
not which RISC-V core occupies the TITAN slot.

### Fidelity

`post_synthesis_estimate`, per roadmap §12.3. Cell area only: no
place-and-route, no DRC/LVS, no timing. At a typical 50–60% GF180 utilisation
the placed die would be roughly **6.5–7.8 mm²**, before a pad ring — far above
the 1.25 mm² target. The 512 B scratchpad is synthesised to flip-flops here
because no SRAM macro is bound. Subsystem figures come from synthesising each
one standalone, which is why they sum to 97.8% rather than exactly 100%: the
2.2% difference is top-level glue and cross-boundary optimisation the flat run
can do and the per-subsystem runs cannot.

## 8c. Removing the DMA: `soc.dma` (2026-07-29)

§8b showed the iDMA at 0.319 mm², 8.2% of the die, in a config whose workers
XIP their code from flash and whose firmware never programs a transfer. The
DMA engine is now selectable rather than hard-wired.

```yaml
soc:
  dma: idma   # default -- pulp-platform iDMA, unchanged behaviour
  dma: none   # no engine instantiated
```

`idma` is the default precisely so that omitting the key cannot silently
change an existing design; all 25 other shipped configs now state `dma: idma`
explicitly so a reviewer never has to know what the default is.

### Measured result

`mosaic_pico_serv_xip`, GF180 `gf180mcu_fd_sc_mcu7t5v0`, tt/25C/5v00,
yosys 0.62 + slang, `synth -flatten`:

| | Cell area | Sequential | `SYSTEM_XBAR_NMASTER` |
|---|---:|---:|---:|
| `dma: idma` (§8b) | 3.903 mm² | 50.7% | 13 |
| `dma: none` | **3.548 mm²** | 51.6% | 9 |
| **Saving** | **0.355 mm²** | | −4 masters |

**0.355 mm² = 9.1% of the die** — larger than the 0.319 mm² iDMA block itself,
because removing the engine also removes four crossbar master ports and the
arbitration behind them. It is very close to the area of all three CPU cores
combined (0.359 mm²): *deleting one unused peripheral saved as much silicon as
every processor in the design occupies.*

The `dma: none` build still passes the full-SoC gate —
`### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓`.

### What is not done

**`dma: xheep` is refused on multi-core configs, not silently ignored.**
`ao_peripheral_subsystem.sv.tpl` keys the DMA flavour off `is_mc`, and the two
flavours have different port geometry: x-heep's simple DMA adds a third
`dma_addr_*` master per stream (`DMA_OBI_PORTS_PER_STREAM` 3 vs the iDMA's 2),
which changes the module port list, `core_v_mini_mcu` wiring, `system_bus`, and
the `SYSTEM_XBAR_NMASTER` index map. Accepting the value and instantiating the
iDMA anyway would be a lying knob, so the validator rejects it with that
explanation. Since x-heep's DMA is 0.190 mm² against the iDMA's 0.319 mm², the
work would be worth roughly 0.13 mm² — a third of what `none` already delivers.

**`none` leaves two crossbar masters behind.** `core_v_mini_mcu_pkg.sv.tpl`
computes `SYSTEM_XBAR_NMASTER` from `get_num_master_ports()` without consulting
`get_is_included()`, so the stubbed DMA's single port still occupies two master
slots. Fixing it means changing that template *and* `XHeep.num_bus_masters()`
in the same commit — the latter feeds the LOG-bus bank check, and the two
disagreeing would be worse than two unused ports. `test_dma_selection.py` pins
the current coupling so it cannot be half-fixed.

**The filelist still carries x-heep's DMA RTL.** `core-v-mini-mcu.core` depends
on `x-heep:ip:dma` unconditionally, so 12 uninstantiated DMA sources remain in
the generated filelist. Simulation is unaffected (nothing references them), but
they do not compile against this config's own generated register package —
`dma_reg2hw_t` loses its `pad_*` fields when the DMA is excluded, and slang
reports 20 errors on `dma_processing_unit.sv`. The synthesis above excluded
those dead files explicitly. **Gating that FuseSoC dependency behind a flag is
required before `dma: none` can go through the LibreLane harden flow.**

## 8d. Removing the debug subsystem and the PLIC (2026-07-29)

Two more blocks that are pure area when a design does not use them. Both are
now config-selectable and both default to present:

```yaml
soc:
  debug: false   # no JTAG DTM, no RISC-V debug module
  plic: false    # no platform interrupt controller
```

`plic: false` drops rv_plic from `MANDATORY_USER_PERIPHERALS`;
`peripheral_subsystem.sv.tpl` already carried a complete tie-off branch for its
absence, so no RTL change was needed there. `debug: false` needed a new gate in
`core_v_mini_mcu.sv.tpl` plus tie-offs — including `debug_reset_n = 1'b1`,
which matters because that net gates the entire system-bus reset.

### Cumulative measurement

`mosaic_pico_serv_xip`, same toolchain and corner as §8b:

| Config | Cell area | Δ | Cumulative |
|---|---:|---:|---:|
| iDMA + debug + PLIC (§8b) | 3.903 mm² | — | — |
| `dma: none` (§8c) | 3.548 mm² | −0.355 | −9.1% |
| `+ debug: false, plic: false` | **3.036 mm²** | **−0.512** | **−22.2%** |

The last step removes more than the two blocks measured standalone
(0.154 + 0.155 = 0.309 mm²). The extra ~0.2 mm² is their glue: the PLIC drags
in a `reg_to_tlul` bridge and the interrupt-vector fan-in, and the debug module
drags in the JTAG DTM and its bus adapters. **Blocks are cheaper to measure
than to remove, and more expensive to keep than they look.**

All three removals together hold the functional gate:
`### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓`.

### What is given up

- **No JTAG.** No halt, step, resume, or external memory access. Bring-up is
  boot-ROM-only, and a part that will not boot cannot be interrogated. This is
  the single most consequential item in this document for a first tapeout.
- **No PLIC.** Nothing routes a UART/SPI/GPIO/I2C interrupt to any hart. CLINT
  timer and software interrupts are untouched, which is why a TDU-driven wake
  design still works.

## 8e. Can a SERV-only SoC reach 1.25 mm²? (2026-07-29)

Direct test of the question "make it 3× SERV and nothing else."

### SERV can be a TITAN — the blocker was the C extension, not capabilities

`configs/mosaic_serv_only.yaml` (3× SERV: one TITAN, two ATLAS) first failed
with `Out of bound memory access 0x00000200`. The cause was **not** a missing
core capability: `picorv32` declares only `unified_obi` — no `mhartid`, no
`interrupts` — and works as TITAN today. The SERV TITAN was declared `rv32i`,
and **the boot ROM contains compressed instructions**. With `isa: rv32ic` +
`compressed: 1` the same config reaches
`### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓`.

This corrects the §7 implication that the TITAN role needs a "big" core. It
needs a core that can *decode the boot ROM*. FazyRV's §7 failure was a
different mechanism and still stands.

### Measured

All rows: `dma: none`, `debug: false`, `plic: false`, XIP, 512 B scratchpad.

| Config | Cell area | vs 1.25 mm² |
|---|---:|---:|
| picoRV32 TITAN + 2× SERV | 3.036 mm² | 2.4× over |
| **3× SERV** | **2.910 mm²** | **2.3× over** |
| Saving from dropping picoRV32 | 0.126 mm² | 4.1% |

**Swapping the TITAN for a SERV saves 0.126 mm² — 4.1% of the die.** The core
cells differ by 0.295 mm² (picoRV32 0.316 vs SERV 0.021), so roughly 0.17 mm²
comes back: the third SERV needs its own SCI wrapper and wake logic, and a
`rv32ic` SERV carries a compressed decoder. (Not separately measured — the
subsystem split for this variant was not re-run.)

### The answer is no, and core choice is not why

At 2.910 mm² the three CPU cores together are **~0.063 mm², about 2% of the
die**. Even reducing every core to zero area leaves ~2.85 mm². The remaining
1.66 mm² that would have to disappear lives in, in order:

| Where | ~mm² | Why it is there |
|---|---:|---|
| `spi_subsystem` | 0.717 | OpenTitan `spi_host` + `spimemio` — the XIP boot path |
| `memory_subsystem` | 0.510 | 512 B scratchpad synthesised to **flip-flops**, no macro bound |
| `peripheral_subsystem` | ~0.87 | UART, GPIO, timer, reg glue |
| `system_bus` | 0.169 | crossbar |

The levers that would actually move it, ranked by measured or bounded impact:

1. **Bind the GF180 `sram512x8m8wm1` macro** for the scratchpad: 0.510 → 0.209,
   **−0.30 mm²**. Highest confidence, no design risk — the macro is exactly
   512 B.
2. **Replace `spi_host` + `spimemio` with a read-only XIP QSPI reader.**
   Bounded by 0.717; a minimal reader should be ~0.1. **≈ −0.6 mm²**, but it is
   new RTL and it sits on the boot path.
3. **Cut the peripheral set to UART only.** Order **−0.4 mm²**, not measured.
4. Core choice: −0.126 mm².

All four together land near **1.5 mm² of cell area** — still short, and that is
the optimistic reading.

### The harder problem: cell area is not die area

Per §5, at 50–60% core utilization real die area is 1.6–2× cell area. So:

| | Cell area | Implied die |
|---|---:|---:|
| 3× SERV as measured | 2.910 mm² | 4.7–5.8 mm² |
| After all four levers | ~1.5 mm² | 2.4–3.0 mm² |
| **To hit a 1.25 mm² die** | **0.63–0.78 mm²** | 1.25 mm² |

**A 1.25 mm² die needs a cell area smaller than the SPI subsystem alone.**
If the 1.25 mm² budget means die area — which is what a shuttle slot means —
then no arrangement of cores reaches it while the design keeps a memory-mapped
SPI XIP path, a UART, and a crossbar.

### Recommendation

Do not spend effort on core selection for area. Spend it, in order, on: the
SRAM macro binding, the XIP controller, and the peripheral set. And settle
first whether 1.25 mm² is a cell-area or die-area budget — the two answers differ
by a factor of two and change which of the above are even worth attempting.

## 8f. Driving to 1.25 mm²: UART-only, XIP-only SPI, SRAM macro (2026-07-29)

Three more cuts applied to the 3× SERV design of §8e, each measured.

### The XIP reader was already there

"Write a read-only XIP reader" turned out to need no new RTL. `spi_subsystem`
already instantiates **`obi_spimemio`** — the YosysHQ memory-mapped read-only
XIP engine — alongside the OpenTitan `spi_host`. Measured standalone:

| Block | mm² | share of the 0.724 mm² subsystem |
|---|---:|---:|
| `spi_subsystem` (whole) | 0.724 | 100% |
| `obi_spimemio` (the XIP reader) | **0.030** | 4.1% |

So the boot path costs 0.030 mm² and the general-purpose SPI host costs the
other 0.69. The new `soc.spi_mode: xip_only` gates the host out and drives the
flash pins from `obi_spimemio` permanently, ignoring `use_spimemio_i`.

### UART-only

`peripheral_subsystem` measured 0.889 mm², and a hand-stripped UART-only
version of the same generated file measured **0.120 mm²**. The contents:

| Block | mm² |
|---|---:|
| `spi_host` (user domain, a *second* SPI host) | 0.635 |
| `uart` | 0.099 |
| `gpio` | 0.067 |
| `rv_timer` | 0.062 |

The user-domain `spi_host` alone is 71% of the peripheral subsystem. Note
`rv_timer` is **not** in the config's peripheral list — it is force-added for
multi-core configs by `MULTICORE_USER_PERIPHERALS`.

### Measured result

`configs/mosaic_tapeout_min.yaml`: 3× SERV, `dma: none`, `debug: false`,
`plic: false`, `spi_mode: xip_only`, `peripherals: [uart]`, 512 B scratchpad.

| Step | Cell area | Δ |
|---|---:|---:|
| §8b starting point | 3.903 mm² | — |
| `dma: none` | 3.548 | −0.355 |
| `debug: false`, `plic: false` | 3.036 | −0.512 |
| 3× SERV | 2.910 | −0.126 |
| **UART-only + XIP-only SPI** | **1.562 mm²** | **−1.348** |

**Total reduction: 3.903 → 1.562 mm², −60%.** And it still passes the gate:
`### RESULT: EXIT SUCCESS — all 3 configured harts executed ✓`.

The last step is by far the largest, and it is entirely peripherals — two SPI
hosts and a GPIO block that this design never used. That is the same lesson as
§8b, now quantified end to end: **core selection moved 0.126 mm²; deleting
unused peripherals moved 1.348 mm², more than ten times as much.**

### The SRAM macro: the requested part is the wrong part

`memory_subsystem` measures 0.5098 mm² with the 512 B scratchpad inferred as
4096 flip-flops. Binding real GF180 cuts is constrained by a fact that decides
the whole question: **every GF180 SRAM macro is 8 bits wide**, so a 32-bit bank
is always four cuts in parallel, one per byte lane.

| Cut | per cut | ×4 = 32-bit bank | capacity | vs 0.5098 FF |
|---|---:|---:|---:|---:|
| `sram64x8` | 0.1006 | 0.4023 | 256 B | — (too small) |
| **`sram128x8`** | 0.1161 | **0.4645** | **512 B** | **−0.045** |
| `sram256x8` | 0.1472 | 0.5888 | 1 KiB | +0.079 |
| `sram512x8` | 0.2094 | 0.8376 | 2 KiB | **+0.328** |

Sizes are from the PDK LEF `SIZE` lines, not estimates.

**Using `sram512x8` as asked would make the chip 0.328 mm² bigger.** The design
needs 128 words × 32 bits; four `sram512x8` deliver 512 words × 32 bits, so
three quarters of every cut is unaddressable silicon you still pay for. The
correct part for a 512 B bank is **`sram128x8` ×4** — an exact fit.

`hw/asic/gf180/sram_wrapper.sv` therefore binds the cut *by depth*: 64/128/256/512
words each select their matching macro, with the byte-lane striping and
active-low `CEN`/`GWEN`/`WEN` handling shared.

Two honest caveats on the macro:

- **Cell area barely moves** (−0.045 mm²), because a hard macro contributes
  zero to `yosys stat` while the flip-flops it replaces were only ~0.51.
  *Die* area is where it pays: standard cells place at 50–60% utilization while
  a macro is ~100% dense, so 0.4645 mm² of macro displaces roughly 0.8–0.9 mm²
  of placed flip-flops.
- **The macro is slow.** The vendor model specifies `Tcyc = 55.6 ns` — about
  18 MHz, against the 100 MHz the testbench assumes. Binding these cuts is a
  geometry decision that forces a frequency decision.

## 8g. 1.25 mm² reached — and exactly what it cost (2026-07-29)

`configs/mosaic_tapeout_ultra.yaml`, measured, gate-passing:

| | µm² | mm² |
|---|---:|---:|
| standard cells (`yosys stat`) | 809 239 | 0.8092 |
| 4× `gf180mcu_fd_ip_sram__sram64x8m8wm1` (LEF) | 402 288 | 0.4023 |
| **total silicon** | **1 211 527** | **1.2115** |

**Against the 1.25 mm² budget: 3.1% under.** The design reaches
`### RESULT: EXIT SUCCESS — all 2 configured harts executed ✓`.

### Read this before quoting 1.2115 mm²

That figure is **standard-cell area plus macro area**, which is the basis this
whole study has used since §8b. It is *not* die area. Standard cells place at
50–60% utilization while the macros are already physical:

| Cell utilization | Implied die |
|---|---:|
| 50% | 2.02 mm² |
| 55% | 1.87 mm² |
| 60% | 1.75 mm² |

**So: 1.25 mm² of silicon — yes. A 1.25 mm² die — no, roughly 1.9 mm², before
a pad ring.** If the budget is a shuttle slot, the honest status is still ~50%
over, and closing that needs floorplanning work (and a LibreLane run to
measure), not more logic deletion.

### The full ledger

| Step | Total | Δ | What it cost |
|---|---:|---:|---|
| §8b starting point | 3.903 | — | — |
| `dma: none` | 3.548 | −0.355 | no bulk copies |
| `debug: false`, `plic: false` | 3.036 | −0.512 | **no JTAG**, no peripheral IRQs |
| 3× SERV | 2.910 | −0.126 | no 32-bit TITAN |
| UART-only + `spi_mode: xip_only` | 1.562 | −1.348 | no GPIO, no SPI host, flash is read-only |
| SRAM macro (512 B, 4× sram128x8) | 1.529 | −0.033 | ~18 MHz memory |
| 2 cores, 256 B scratchpad | 1.278 | −0.251 | one fewer worker, half the RAM |
| `multicore_timer: false` | **1.2115** | −0.067 | no rv_timer; CLINT only |

**−69% overall.** Note the shape of it: one step (peripherals) is 58% of the
total saving, and every core-related step together is under 10%.

### The finding that matters most for future work

`serv_sci` measures **0.162 mm² per instance — 7.7× the 0.021 mm² SERV core it
wraps**, and `cpu_subsystem` is 0.493 mm² of the 1.562 mm² design (32%).

The cause is `serv_rf_ram`: SERV's 1152-bit register file, inferred as
flip-flops with a 1152-deep read mux because no RAM is bound to it. **SERV's
famous "200 gates" assumes its register file lives in a RAM block.** On GF180
with no macro behind it, the register file costs eight times the processor.

That makes the largest untouched lever in this design *binding a macro behind
`serv_rf_ram`*, not deleting anything further. It is also why "use more, smaller
cores" does not scale here the way the core areas suggest: every added SERV
brings another 0.16 mm² register file with it.

Two supporting measurements, taken on the 1.562 mm² design:

- `cpu_subsystem` = 0.493 mm², of which the three SERV instances are 98.4%.
  Inside one core, yosys reports the register file as 1152 flip-flops plus a
  1023-cell read mux — 84% of that core's flops and 80% of its muxes, about
  0.308 mm² across the three cores, **20% of the entire SoC**.
- `peripheral_subsystem` = 0.181 mm², of which `uart` is 0.099 and `rv_timer`
  0.062. Inside the UART, the two `prim_fifo_sync` buffers are hard-coded
  32 entries deep (`uart_core.sv:175`) and cost **0.066 mm² — 61% of the UART
  and 31% of the peripheral subsystem**, for a design that prints status lines.

So the two remaining levers, both untaken here, are a RAM macro behind
`serv_rf_ram` (order 0.3 mm² on a 3-core part) and a UART FIFO depth of 4
instead of 32 (order 0.057 mm²). Neither is free: the first adds register-file
latency, the second cuts UART throughput under interrupt latency.

## 9. Recommendation

1. **On-chip SRAM is the only lever that matters.** At 0.419 mm²/KB everything
   else is noise. The 1-bit FazyRV config, the core mix, the peripheral list —
   all of it is rounding error next to one SRAM decision.
2. **The design you want is coherent but the generator cannot emit it.**
   XIP exists for the TITAN; workers are copied to SRAM and their `boot_addr`
   is schema-validated to be inside on-chip SRAM (§6.1). External SRAM on the
   `EXT_SLAVE` window is in the address map but not reachable from the config
   schema. Closing this is the five-step change in §6.2 — a new backend
   profile, as the roadmap's M1/M2 anticipates, not a parameter tweak.
3. **Best reachable today: XIP-TITAN + 8 KB SRAM ≈ 3.35 mm² of SRAM**, still
   2.7× over budget. There is no config that meets 1.25 mm² without generator
   work.
4. **Use SERV, not FazyRV, if area is the objective** — 0.021 vs 0.183 mm², an
   8.7× difference that dwarfs the 3% `chunksize` effect.
5. **FazyRV as TITAN is unproven, not merely unconfigured** (§7). The missing
   capability declarations are easy; whether a tiny SCI core can execute the
   boot-ROM contract is the real question and it is unanswered.
6. **Three validator gaps to close** so this class of config fails in
   milliseconds instead of after a 2-minute build plus a 90-second sim:
   a TITAN must declare `mhartid` + `interrupts` (§3.1); `conf: MIN` must be
   rejected where CSRs are required (§3.1); and core capabilities must become
   parameter-dependent rather than static (§7).
