# External-memory boot: how others do it, and what MOSAIC should do

> **Status:** Design note for review — **no implementation yet**, by request
> **Date:** 2026-07-28
> **Question:** the external-memory profile (`memory.sram_kb: 0`) generates RTL
> but cannot lay out software, because external RAM is uninitialised at reset.
> Who brings it up, and where do worker images come from?
> **Recommendation:** **Option C — workers XIP their code from flash, external
> RAM carries only writable data, plus a minimal on-chip scratchpad.** This
> dissolves the bootstrapping problem rather than solving it, but it means
> abandoning "zero on-chip SRAM" as the target. See §4 and §6.

## 1. The problem, stated precisely

On-chip SRAM is pre-loaded by the testbench (and, on silicon, is simply
addressable from reset). External RAM is neither: at reset a PSRAM or HyperRAM
is uninitialised, and its controller has to be configured before the first
access. So the current staging model —

```
TITAN cold-boot loader copies worker images from flash into SRAM,
CRC-checks them, then the TDU wakes the workers
```

— has nowhere to copy *into*, and the copier itself has nowhere to run.

This is not a MOSAIC quirk. Every SoC that boots to external memory faces it,
and the field has converged on a small number of answers.

## 2. What others actually do

### 2.1 Cheshire (PULP platform) — stage into an on-chip scratchpad

Cheshire's boot ROM, on reset:

1. resets all integer registers and disables the FPU;
2. **pauses all non-boot harts**;
3. *"completes the LLC's self test (if present) and switches it to SPM"*;
4. invokes the Platform ROM if present.

The last-level cache is reconfigured as a **scratchpad (SPM)** specifically to
give boot code deterministic writable storage *before* DRAM exists — the
documentation calls this a critical measure because the immutable boot ROM
cannot use dynamic memory during initial execution.

Four boot modes are selected by `boot_mode_i` pins:

| Mode | Medium | Interface |
|---|---|---|
| `0b00` | Passive preload | JTAG / serial / UART |
| `0b01` | SD card | SPI |
| `0b10` | NOR flash | SPI |
| `0b11` | EEPROM | I²C |

Autonomous modes load a **≤48 KiB** baremetal program into SPM and run it: the
**zero-stage loader (ZSL)**. The ZSL then loads the device tree and OpenSBI
into DRAM and jumps to it.

One detail worth stealing outright: the ROM hands the ZSL a **function pointer
to its own device-read routine**, passed through scratch registers
(`CHS_REGS->scratch[0..3]` carry `read`, `priv`, and a global pointer), so the
mutable loader reuses the ROM's SD/SPI driver instead of duplicating it
(`sw/boot/zsl.c`).

**Lesson: you need writable memory that works before external memory does, and
Cheshire pays for it with an on-chip SPM.**

### 2.2 NEORV32 — map external serial memory and execute in place

NEORV32 ships an optional **SMC (serial memory controller)** that maps external
serial PSRAM/flash into the processor address space and **supports XIP**. Its
base (`SMC_BASE`) must be 256 MB-aligned; pins are `smc_sck_o`, `smc_csn_o`
(2-bit chip select), `smc_sdo_o`/`smc_sdi_i`, with `smc_ioen_o` for IO muxing.
Code executes **directly from external serial memory without being copied to
internal RAM first**, with the boot source selected by `BOOT_MODE_SELECT`.

**Lesson: for a small SoC, XIP from external serial memory is proven and
removes the copy step entirely — for code.**

### 2.3 RP2350 — PSRAM as a second chip select on the XIP bus

RP2350 connects external PSRAM through the **QMI** interface, "supported by a
QMI memory interface using the QSPI.XIP bus" — i.e. PSRAM sits alongside flash
on the same QSPI XIP bus on a different chip select, both mapped into the
address space and cached. (The public silicon page does not detail the PSRAM
init sequence or chip-select assignment; that is in the datasheet and was not
read for this note.)

**Lesson: flash and PSRAM can share pins and a memory-mapped window, which is
what makes the pin cost acceptable.**

### 2.4 PULPissimo — copy from QSPI flash into L2

The classic small-SoC flow: initialise the SPI controller, read section
information from flash, copy the application into L2 memory, jump. Same shape
as Cheshire minus the OS stages.

**Lesson: the copy-based model is the default, and it always presumes an
on-chip memory to copy into.**

### 2.5 The general RISC-V picture

Multi-stage boot is the norm — ZSBL/BROM → FSBL → OpenSBI → payload — and the
platform-initialisation literature is explicit that *early platform firmware
brings up DRAM and other controllers before loading an OS*. Nobody boots
straight into uninitialised external RAM.

## 3. The three options for MOSAIC

### Option A — Copy-based staging (Cheshire / PULPissimo)

Boot ROM or TITAN initialises the external memory controller, copies worker
images from flash into external RAM, CRC-checks them, then the TDU wakes
workers. This is the smallest change to the *existing*
`spi-memio-xip-titan-load-workers` model — only the destination changes.

- **Needs** writable memory for the loader's stack and variables before
  external RAM is up.
- **Therefore does not achieve zero on-chip SRAM.** It reproduces Cheshire's
  SPM requirement.
- Worker images end up in volatile memory, so any reset repeats the whole copy.

### Option B — Everything XIP from flash, no external RAM at all

Both TITAN and workers execute in place from the flash window; there is no
writable memory anywhere.

- **Impossible in general.** Flash is read-only. `.data`, `.bss` and every
  stack need writable storage. Only a program with no stack and no mutable
  state could run, which excludes the C runtime and the TDU protocol (workers
  write sentinels).

### Option C — Workers XIP their *code* from flash; external RAM holds only *data* ✅

The observation that dissolves the problem: **code is read-only, and it is
already non-volatile in flash.** Nothing has to stage it anywhere.

- Worker reset vectors point into the **flash XIP window**, which is valid at
  reset with no initialisation beyond enabling the memory-mapped window — which
  the existing boot ROM already does (`_execute_from_flash` sets
  `OBI_SPIMEMIO_START_SPIMEM` then jumps to `FLASH_MEM + 0x180`).
- External RAM carries only `.data`, `.bss` and stacks. It needs **no
  pre-loading**: `.bss` is zeroed by each hart's `crt0`, and `.data` is copied
  from flash by `crt0` — completely standard embedded practice, not a special
  boot stage.
- The TITAN still initialises the external memory controller, and TDU wake is
  gated behind a "external memory ready" status bit so no worker touches RAM
  before it exists.

**This removes the staging step, the CRC check, the image table, and the
volatile-copy-on-every-reset problem in one move.**

## 4. Recommendation, and the honest cost

**Adopt Option C — but drop "zero on-chip SRAM" as the goal.**

Option C removes the need to *stage code*, but it does not remove Cheshire's
lesson: the TITAN must configure the external memory controller, and that code
needs a stack before external RAM works. Two ways out:

1. **Register-only initialisation.** Write the controller-init sequence with no
   stack, in registers only. Feasible for a fixed sequence — ROM bootloaders do
   this — but fragile, hard to write in C, and untestable in the normal way.
2. **A minimal on-chip scratchpad.** A few hundred bytes of SRAM used only by
   the init path and then available as extra stack. This is Cheshire's
   LLC-as-SPM, scaled down.

**Recommend (2).** Using the foundry macro measured for this project,
`gf180mcu_fd_ip_sram__sram512x8m8wm1` is **512 B for 0.209 mm²**. Against the
1.25 mm² budget that is ~17% — versus **3.35 mm²** for the 8 KiB the schema
used to demand, which was 2.7× the entire budget. So:

| Configuration | On-chip SRAM | Area | Verdict |
|---|---:|---:|---|
| Old floor (8 KiB) | 8 KiB | 3.350 mm² | 2.7× over budget |
| Option A (staging SPM) | ≥4 KiB | ≥1.675 mm² | over budget |
| **Option C (scratchpad only)** | **512 B** | **0.209 mm²** | **fits** |
| Option B (nothing) | 0 | 0 | cannot run C |

The engineering claim to make is therefore **"external-memory SoC with a
minimal on-chip scratchpad"**, not "no on-chip memory". That is both achievable
and honest, and it is exactly what every comparable design does.

## 5. Why this suits MOSAIC specifically

Workers XIP-ing code from a single QSPI flash serialise on that port. From the
latency analysis in `area_study_gf180_min_soc.md` §8.2, that is unusually
cheap here: a bit-serial SERV already spends ~32 cycles per instruction, so a
~10-cycle memory costs it roughly **1.3×**, where a single-issue cv32e20 would
pay ~8×. The TITAN — the one latency-sensitive core — keeps its own XIP path
and is the natural owner of the controller bring-up.

## 6. What this changes in the plan

Supersedes the five-step sketch in `area_study_gf180_min_soc.md` §6.2, which
assumed workers would be staged into external RAM:

1. `memory.sram_kb` gains a **scratchpad** meaning rather than only 0 or ≥8:
   allow a small on-chip SRAM (e.g. 512 B–2 KiB) alongside `memory.external`.
2. Worker `boot_addr` must be allowed in the **flash XIP window**, not only in
   external RAM — the widened check from the prototype needs a third region.
3. `software_gen` emits per-hart linker scripts placing `.text` in flash,
   `.data`/`.bss`/stack in external RAM, and keeps the existing `crt0`
   copy/zero behaviour. **The image table and CRC staging in `pack_flash.py`
   are no longer needed for workers** — their code is simply resident.
4. A QSPI PSRAM controller IP plus SCI-style wrapper (a `wrapper-smith` target,
   close kin to `w25q128jw_controller` but read/write, no erase/program).
5. TDU wake gated behind an "external memory ready" bit set by the TITAN.
6. A PSRAM behavioural model in the testbench — **without it none of this can
   reach `EXIT SUCCESS`, and by this project's rules it therefore cannot be
   claimed.**

## 6a. Finding from attempting the XIP-only bring-up (2026-07-28)

§7 question 2 asked whether a first bring-up could run workers from flash with
no external RAM at all. **It was attempted. The schema and RTL support it; the
software cannot, and the reason is structural rather than a missing feature.**

`configs/mosaic_xip_bringup.yaml` (cv32e20 TITAN + 2× SERV booting at
`0x40010000` / `0x40011000`) validates, and `mosaic-gen-config` renders all
38 templates. Generation then stops in software layout, because the profile has
**no writable memory anywhere**:

1. **No stack, therefore no C runtime — for *every* hart, including the
   TITAN.** All existing firmware would have to be rewritten in assembly. That
   is not a bring-up, it is a parallel software stack.
2. **No shared-control window.** The TDU liveness protocol works by each hart
   writing a sentinel to SRAM (`0x3000 + hart*4`), which the testbench watches.
   With no RAM there is nowhere to write.

Problem 2 has a clean answer that needs no new hardware: the TDU already
exposes a **per-hart read/write `CPI_EST` array** (`0x20 + hart*4`). A hart can
write its liveness sentinel there — a per-hart writable word in a peripheral,
requiring no memory at all. The generator now names this in its error message.

Problem 1 has no comparable answer. A stack is not optional for the C
runtime, and rewriting the TITAN's TDU driver, the boot path and the liveness
firmware in assembly to save 0.209 mm² is a bad trade.

**Conclusion: XIP-only is a research curiosity, not the bring-up path.** It is
kept schema-legal (it is a real point in the design space, and it costs one
branch) but the engineering route is the minimal-scratchpad profile below.

## 6b. Byte-granular scratchpad (`memory.scratchpad_bytes`)

`memory.sram_kb` is integer KiB, but the size that matters here is below 1 KiB.
The GF180 sub-KiB macros are 64/128/256/512 B, and
`gf180mcu_fd_ip_sram__sram512x8m8wm1` is **0.209 mm² for 512 B** against
**0.419 mm² for a 1 KiB pair** — 17% of a 1.25 mm² die. Rounding the schema up
to the nearest KiB would quietly spend that.

So `memory.scratchpad_bytes` accepts 64/128/256/512, must be a power of two,
must be under 1 KiB (at or above, use `sram_kb` — the field exists only for
what KiB cannot express), and requires `sram_kb: 0`, since a design with an
on-chip SRAM pool should size that pool rather than carry a second writable
memory. It may coexist with `memory.external`: scratchpad for the early stack,
external RAM for the bulk. `configs/mosaic_scratchpad.yaml` is the reference.

**Not yet realisable, and the generator says so rather than rounding.**
x-heep's `Bank(size_k)` takes integer kiB and requires a positive power of two
(`memory_ss/ram_bank.py`), so the RAM bank pool has no representation for a
sub-KiB memory. Building it needs a scratchpad instance *outside* the bank pool
with its own address-map entry and bus attachment — which is architecturally
right anyway, because this is data-only scratchpad rather than the code+data
RAM0 pool. Generation fails with exactly that reason and names the area a
silent round-up would have cost.

## 7. Open questions for the team

1. **Scratchpad size.** 512 B (0.209 mm², one macro) is the minimum that buys a
   real stack. Is that the right trade against the 1.25 mm² budget, or is
   register-only init worth the fragility to save it?
2. ~~**Do workers need writable memory at all in the first slice?**~~
   **Answered by attempting it — see §6a.** Workers alone could, but the TITAN
   cannot: no stack means no C runtime anywhere, so the whole firmware base
   would have to be rewritten in assembly to save 0.209 mm². XIP-only stays
   schema-legal but is not the path.
3. **Shared or per-hart external memory regions?** Per-hart stacks must not
   overlap; the current `__mosaic_stack_stride` scheme extends naturally, but
   the address map needs deciding.
4. **QSPI pin sharing.** RP2350 puts PSRAM on a second chip select of the same
   QSPI bus. Do we have the pad budget for a dedicated PSRAM port, or must it
   share with flash (and therefore contend with TITAN instruction fetch)?

## Sources

- [pulp-platform/cheshire](https://github.com/pulp-platform/cheshire) —
  `docs/um/sw.md` (boot flow, LLC→SPM, four boot modes) and `sw/boot/zsl.c`
  (zero-stage loader, scratch-register handoff)
- [NEORV32 data sheet](https://stnolting.github.io/neorv32/) — SMC serial
  memory controller, XIP, `SMC_BASE`, bootloader boot sources
- [RP2350 / Raspberry Pi silicon documentation](https://www.raspberrypi.com/documentation/microcontrollers/silicon.html)
  — QMI interface, external PSRAM on the QSPI XIP bus
- [Booting PULPissimo from QSPI Flash](https://medium.com/@vinayy232/booting-pulpissimo-from-qspi-flash-on-zcu104-493411c32593)
  — copy-from-flash-into-L2 model
- [Platform Initialization — Platform System Interface Specification](https://platform-system-interface.github.io/psi-spec/platform-initialization.html)
  — early firmware brings up DRAM before loading an OS
- [An Introduction to RISC-V Boot flow](https://crvf2019.github.io/pdf/43.pdf)
  — ZSBL/FSBL/OpenSBI staging
