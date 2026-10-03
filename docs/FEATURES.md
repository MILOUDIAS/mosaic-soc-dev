# MOSAIC-SoC — feature catalogue

One place that says what this project can do today, and what we want it to do
next. Written 2026-09-14.

It deliberately does not duplicate the four documents that already have a job:

| document                                                                                 | its job                                                       |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| [README.md](../README.md)                                                                | the practical user guide: install, generate, simulate, harden |
| [AGENTS.md](../AGENTS.md)                                                                | the canonical architecture and agent reference                |
| [project_board.md](project_board.md)                                                     | the live work queue, with the evidence behind each decision   |
| [general_multicore_soc_generator_roadmap.md](general_multicore_soc_generator_roadmap.md) | the v2 compiler vision (IR, backends, catalogs)               |

**Status marks used below.** ✅ proven, with the evidence named · ◐ works but
unproven in some stated dimension · ⚠ known gap, deliberately recorded.

**Scale, for orientation:** 29,764 lines of Python across the generator and the
harness, 1,657 RTL sources and templates under `hw/`, 33 shipped configs, 21
registered flows, 1,558 tests in 61 files.

---

# Part I — What exists today

## 1. One config file becomes a whole SoC

- ✅ **A single YAML describes the chip**: cores and counts, roles, memory, bus
  fabric, dispatch, peripherals, and physical objectives.
  `make mosaic-gen MOSAIC_CFG=configs/<name>.yaml`. 33 configs ship, from
  `mosaic_sim.yaml` to the tapeout candidate `mosaic_tapeout_ultra.yaml`.
- ✅ **Mako templates render the RTL** (`hw/**/*.sv.tpl`, driven by
  `util/mosaic_gen/mcu_gen.py`): per-core master indices, hart IDs, interrupt
  routing and a multi-master `system_bus` all follow from the config.
- ✅ **Content-addressed build bundles** (`util/mosaic_gen/build_manifest.py`).
  Every bundle is named by a hash of the whole generator closure (`hw/`, `tb/`,
  `util/`, `configs/`, `sw/`, `flow/`, `scripts/`), and every physical run
  records the bundle it consumed, so no run can silently harden stale RTL.
- ✅ **Fail-closed validation before anything is generated**:
  `harness/core.py: validate_config`, plus a topology check (`topo-viz`) that
  `mosaic-gen-config` refuses to run without.
- ✅ **FuseSoC** is the build system; a generated bundle carries its own
  resolved file list, so a fresh clone hardens without hand-edited paths.
- ✅ **One derived description of any SoC, and diagrams of it** (2026-09-24,
  `harness/socview.py`, `harness/diagram.py`). `build_view(config)` reads the
  same `XHeep` object the templates render from and renders
  `core_v_mini_mcu_pkg.sv.tpl` in memory, which is byte-identical to the
  generated file. It works from YAML alone, before any RTL exists, and it is
  checked against every bundle on disk: crossbar masters and slaves, RAM banks
  and PLIC sources all match. It builds for all 248 covering-array configs.
  The two diagrams, `topo-viz render` (HTML holds both; `--svg --view chip|logical`):
  - **Logical view:** harts, then SCI wrappers with each core's native bus
    (`CoreSpec.native_bus`, checked against the wrapper header), then the
    interconnect, the crossbar targets and both peripheral buses. Blocks that
    keep a port or window but are not instantiated are drawn dashed.
  - **Chip view:** the die to scale, with IO on its real edges. Pin positions
    come from the run's final DEF (`harness/physical/defpins.py`), with pad
    cells from the integrator's pin plan: 167 terminals for D15. Otherwise
    the view uses the wrapper ports, or says "IO not assigned".

  `soc-from-prompt` writes `<config>.diagram.html` for every config it
  generates. This replaced topo-viz's own YAML digest, which drew 13 crossbar
  masters and 2 banks for Block C; the RTL has 11 and 1.

## 2. Fourteen cores behind one interface

- ✅ **Standard Core Interface (SCI)**: every foreign core is wrapped to OBI 1.3
  (`hw/sci/*_sci.sv`, nine wrappers), so adding a core is one wrapper plus one
  `.core` descriptor plus one `cpu_subsystem.sv.tpl` branch.
- ✅ **Proven in the full-SoC testbench (10 cores)**: cv32e20, cv32e40x,
  FazyRV, SERV, PicoRV32, Snitch, CVA6, Hazard3, Rocket, BOOM v3 — each reaches
  `EXIT SUCCESS`, including mixed topologies, an all-TITAN 4-core SMP demo
  across all three fabrics, and the Berkeley RV64 tiles behind the TL→OBI
  bridge.
- ◐ **QERV** is proven one level down, in the `cpu_subsystem` and LIC fabric
  testbenches, not through the full-SoC top.
- ⚠ **Ibex** appears only in a render acceptance test; **cv32e40p** and
  **cv32e40px** have no shipped config. Generator-supported, unproven.
- ⚠ **CVA6, Rocket and BOOM are simulation-only** and excluded from GF180
  tapeout by the physical preflight, not by convention.
- ✅ **Roles (Big.LITTLE)**: TITAN orchestrates and runs free; ATLAS and NANO
  are workers, dormant until a TDU wake. Unstated roles are assigned
  deterministically and the repair is reported, never silently applied.

## 3. Dispatch, DMA, interconnect, memory, peripherals

- ✅ **Task Dispatch Unit** (`hw/tdu/`): an 8-deep descriptor FIFO, per-hart
  wake, CPI and energy proxies. TITAN software picks the target hart and the
  hardware preserves that `core_hint`. Unit and SoC tests pass.
- ✅ **iDMA** (register frontend + ND midend + OBI backend) is the default
  engine. `soc.dma: none` is permitted for area-critical parts and saves a
  measured 0.355 mm² in GF180, 9.1% of the minimum-area SoC. The x-heep DMA is
  reserved and refused on multi-core configs (different port geometry).
- ✅ **Three bus fabrics**: OBI (default), a logarithmic crossbar, and FlooNoC;
  the full-SoC wake demo passes on all three. A TL→OBI window bridge brings in
  the Berkeley tiles (with the CLINT/PLIC alias trick).
- ✅ **Memory options**: an SRAM pool, boot ROM, execute-in-place from external
  flash, a flip-flop scratchpad, and a legal no-SRAM profile (the Block A part
  has no on-chip RAM pool and runs XIP).
- ✅ **Peripherals**: uart, gpio, i2c, spi, timer, serial_link, with PLIC and
  timer generation (`util/mosaic_gen/plic_gen.py`).

## 4. Verification

- ✅ **The full-SoC testbench is topology-generic** (`tb/mosaic_soc/`): it
  consumes generated `boot_images.json`, builds one ABI-correct RV32E/RV32/RV64
  liveness image per boot slot, and withholds `EXIT SUCCESS` until _every_
  configured hart has written its sentinel. Exit code 0 alone is never a pass.
- ✅ **Subsystem testbenches**: `tb/tdu/`, `tb/idma/`, `tb/log_xbar/`,
  `tb/floonoc/`, `tb/tl_obi/`, `tb/sci/` (cocotb), `tb/systemc_tb/`.
- ✅ **21 registered flows** behind one runner with timeout protection and
  structured log parsing: `mosaic flow-runner list`.
- ✅ **Generated testbenches**: `tb-smith` writes per-core TBs; `tb-matrix`
  reports which core × fabric combinations exist.
- ✅ **Software**: `sw/` carries applications, production firmware, FreeRTOS and
  linker scripts; a production-firmware demo runs on the full 7-hart SoC.
- ✅ **Gate-level simulation**: the post-place-and-route netlist boots XIP
  through only the 22 bonded pins (`harness/evidence/gls.py`). A "GLS failure"
  in September was root-caused to the _testbench_ releasing reset on a capture
  edge, which exonerated repair margin 45. Proven on two designs: Block A
  (12,399 cycles) and **Block C at its new 0.74 density** (35,698 cycles,
  status `0x00`). Functional only — the runner refuses `--sdf` rather than
  running zero-delay and calling it timing-annotated, because the GF180 models
  compile `-DFUNCTIONAL` and carry no annotatable paths.
  **Each design needs its own power-up deposit list**
  (`tb/gls/gen_powerup_init.py`): without one every flop starts X, X propagates,
  and the netlist never fetches — which looks like a slow simulation, not a
  broken one.
- ◐ **Equivalence checking** is wired (`harness/evidence/lec.py`); the
  kepler-formal SEC path cannot answer anything useful yet.
- ✅ **Verilator pinned to 5.050**: an oss-cad-suite nightly miscompiles
  cv32e40x's DFG, which was root-caused and pinned around.
- ✅ **1,558 tests** in 61 files, run in four chunks so peak memory fits an
  11 GB machine.

## 5. Physical implementation, RTL to GDSII

- ✅ **LibreLane 3.0 Classic with nothing skipped**:
  `flow/librelane/experimental/run_signoff.sh` has no `--skip` and no bypass,
  and refuses a config that nulls or substitutes a step. Magic DRC, KLayout
  DRC, Netgen LVS, XOR, antenna checks and IR drop all run and can all fail the
  flow.
- ✅ **A one-second preflight guards a multi-hour run** (`flow-preflight`):
  disk headroom, config completeness, bundle presence, stale run tags, and
  synthesis-named NDR nets. Every check was bought with a wasted run.
- ✅ **Machine limits are opt-in and narrow**: a resource config may set only
  `*_THREADS` keys, because a thread count changes how long a check takes and
  never whether it passes.
- ✅ **The routing guard** watches detailed routing and kills a run that has
  plateaued (≥25% improvement required over 5 iterations). On the 11-hour Block
  C failure it would have called it at 1.15 h. Opt-in, with a retry ladder that
  jumps to a demonstrated-clean utilisation.
- ✅ **Non-default routing rules (NDR)**, per-design repair margins, and a
  waiver store with `waiver-author`.
- ✅ **Physical models**, each honest about its basis: the area model
  (`floorplan.py`, ±0.4% interpolating, ~3% one hart beyond calibration), the
  routability ceiling (demonstrated utilisations: 81% at 2 harts, 79% at 3, 65%
  at 4, refusing to extrapolate), an SRAM macro catalogue, and a per-technology
  store.
- ✅ **The hardening config is derived, not hand-written**
  (`physical-intent harden`): die, core area, clock period and repair margin all
  follow from the SoC config and its objectives.
- ✅ **Three shipped blocks**: **Block A** (`mosaic_tapeout_ultra`, the
  Chipathon 1117.5 µm slot) is DRC/LVS clean with GLS passing and ships at
  repair margin 32, re-hardened 2026-09-24 against the D15 padframe with a
  reset synchronizer (`runs/blocka_d15_rstsync`: setup +2.199 ns, GLS 12,404
  cycles); **Block B** (3 harts) now targets 20 MHz; **Block C** (4
  harts) is clean at a 65% utilisation target.
- ◐ **Second PDK, IHP sg13g2**: declared in the technology store with its site
  and corner names, a probe config exists, and LibreLane has run on it. It is
  not a signoff, and the area model is calibrated for GF180 only.

## 6. Automatic PPA improvement

The newest layer, and the one that makes "improve the design" mean something a
machine can execute. Five parts, all measured:

- ✅ **An objective as code** (`harness/physical/ppa.py`,
  `physical-intent ppa`). Hard gates never traded: every LibreLane hard check
  at zero, setup and hold slack ≥ 0, zero max-slew and max-cap under per-pin
  liberty limits. A missing metric fails its gate. Objectives: **die area**,
  cell area, **energy per cycle** and fmax. Energy rather than power, because
  power rises with the clock and a power objective rewards slowing the chip
  down. Both area terms, because die area is the silicon a wafer is charged for
  while cell area says whether the tools had to buffer their way out of
  trouble — and they disagree exactly where a floorplan knob operates.
- ✅ **An experiment ledger** (`physical-intent ledger`). A run's settings are
  its resolved config minus machine paths and checker thread counts, plus the
  RTL bundle. A comparison names a cause only when exactly one setting moved.
  On the runs then on disk, only **5 of 86 same-design pairs** were real
  single-variable experiments, and neither shipped margin decision was one.
- ✅ **Cheap stages that prune only on proof** (`physical-intent screen`): the
  models (seconds), synthesis (2–4 min, predicts final logic area within
  1.087–1.178× of the synthesised area), and the timing check just before
  detailed routing (18–33 min, where signoff slack was never better in 22 of 22
  runs). A screen keeps a candidate when data is missing, the opposite of a
  gate.
- ✅ **A line-search optimizer** (`physical-intent optimize`). One setting per
  run, so every run is a single-variable experiment; it stops if anything else
  moved, kills a run the post-routing screen prunes, and resumes from a JSONL
  journal. It may set only whitelisted knobs through the ordinary derivation
  path, so it cannot switch a check off. **Proven on Block B: +79.3% fmax
  (12.64 → 22.66 MHz) in five runs and about ten hours, for 0.010% more area
  and 0.032% more energy, with no person in the loop.** A sixth run bounded the
  edge: at a 40 ns target the design misses setup by −3.067 ns and its achieved
  path improves only 44.13 → 43.07 ns, so 50 ns is the answer and going faster
  is RTL or floorplan work rather than a clock knob.
- ✅ **A small-model evaluation** (`config-author eval`): 26 fixed requests, the
  prompt the harness really sends, scored by code. Sonnet 5 at medium effort —
  a declared stand-in, not a small local model — reaches **26/26 verdicts**
  with zero fallbacks, and on 25 of those it also
  read the request the way the grammar does (agreement 0.955); the
  deterministic grammar is 26/26. The eval has found **ten** faults so far,
  every one fixed: a non-nullable TDU field, role guessing, grammar vocabulary
  gaps, a role word leaking across "and" (ten harts for a nine-hart request),
  worker topologies accepted with the TDU off, worker harts with no boot slot,
  peripheral wording nobody had taught it, four design fields the contract
  could not express at all, a deliberate no-SRAM part it could not express
  either, and the alias spellings it had to be told were not inventions.
- ✅ **Two measurements that make the rest trustworthy**: the flow's
  run-to-run noise floor is **zero** (two identical runs, identical metrics to
  every digit), and a series can pin its RTL bundle with `--manifest` so a
  flow-script edit cannot move the design under a search.
- ✅ **A content-addressed evidence store** keyed on the RTL bundle, config,
  PDK, standard-cell library, tool image, parser version and the PDK view files
  a run actually consumed. Invalidation is not a mechanism: a changed input
  simply produces a different key.

## 7. The agentic harness

- ✅ **15 skill cards** (`.claude/skills/`) over deterministic Python skills:
  `soc-from-prompt`, `soc-config`, `soc-topology`, `soc-flows`, `soc-docs`,
  `soc-drc-triage`, `gls-triage`, `flow-preflight`, `netlist-diff`, `pdk-port`,
  `tb-matrix`, `tb-smith`, `waiver-author`, `wrapper-smith`, `setup-wizard`.
- ✅ **A closed tool surface**: 20 typed tools (`harness/agent_tools.py`).
  Unknown tools and malformed arguments become error observations; they never
  reach a shell.
- ✅ **Four drivers**: `deterministic` (a visible fixed workflow, the default),
  `api` (the in-process bounded agent loop), and handovers to `claude` and
  `omp` interactive UIs.
- ✅ **Gates as executable policy** (`harness/gates.py`, `skill_policy.py`):
  `mosaic-gen-config` is blocked until the topology check passes, `tb-soc-*`
  until generation passes, physical flows need `--allow-physical`, integration
  needs `--allow-integration`, and `--dry-run` denies every write and execute
  tool. A build request cannot finish until full-SoC evidence exists.
- ✅ **An observable terminal contract** (`harness/events.py`): append-only
  sequenced events with three consumers — live rendering, `--events-jsonl`, and
  an append-only session journal under `build/agent/sessions/` at mode 0600.
- ✅ **An MCP server** (`harness/mcp_server.py`) sharing the same gates, so the
  enforcement does not depend on which client is driving.
- ✅ **Prompt → SoC without an LLM**: an ordered regex grammar extracts cores,
  roles, memory, bus, scheduler and peripherals, with deterministic repairs.
  The optional LLM path only _translates_; its output feeds the same repair and
  validation gates. API keys live in the environment, never in config.

- ✅ **Installable as a plugin in four agent hosts** (2026-09-24,
  `harness/mcp_server.py`, `harness/approvals.py`, `harness/install.py`,
  `plugins/mosaic/`).
  - **Plugin mode:** the server starts without a request. The ceiling is a
    user-set allowlist (`MOSAIC_SCOPES`), and each request is bound with
    `session_new`.
  - **Approval:** `physical`/`integration` and approval-gated flows need a
    person's time-limited token (`mosaic approve`, TTY only).
  - **Tools:** `soc_generate`, `config_generate`, `tb_generate` and
    `tb_wake_demo`, which were dead over MCP, now work. The slow test runs
    plan → generate → sim to a passing simulation over MCP alone.
  - **Hosts:** the Claude Code plugin connects; Codex and opencode configs
    match what those hosts write; omp is served from `.omp/mcp.json`. See
    `docs/plugin_smoke.md`.

## 8. Checking any of this yourself

Run these from the repo root. `./mosaic <args>` is the same CLI and works
from any directory; `pip install` does **not** work yet, which is the packaging
gap in Part II E.

```bash
python3 -m harness flow-runner list                    # the 21 flows
python3 -m harness config-author eval                  # the no-LLM prompt baseline
python3 -m harness config-author eval --print-prompts  # drive any model, then --answers
python3 -m harness physical-intent ledger              # every run, and the real experiments
python3 -m harness physical-intent screen --run-dir R  # the cheap-stage verdict on a run
python3 -m harness physical-intent ppa --run-dir R --baseline B
python3 -m harness physical-intent optimize --config C --design D \
    --knob clock_period_ns=80,66.7,50 --max-runs 4 --dry-run
python3 -m harness topo-viz render C --run-dir R          # logical + chip diagrams
python3 -m harness web build                           # the viewer, into build/web/
python3 -m harness web serve                           # the same, live, on 127.0.0.1:8765
```

- ✅ **A web viewer** (2026-09-24, `harness/web.py`). `web build` writes
  self-contained pages from what is on disk:
  - every config's diagrams, cores, memory map and IO;
  - every hardening run's gate verdicts, signoff counts, GLS and LEC, power
    per corner, detailed-routing trajectory and layout render;
  - each technology's port, calibration and tapeout status;
  - the active waivers.

  Every verdict comes from the function `physical-intent ppa` uses, and a
  test holds the accepted set equal to `ledger()`. A run's config is found
  through the bundle it hardened, or inferred from the bundle name when
  pruning deleted it, and labelled as inferred. `web serve` binds loopback
  only, is read-only, and adds `/api/progress/<tag>`, which run pages poll
  every 10 s.

  Progress (`harness/physical/progress.py`) reads `flow.log` and the current
  step's files. A failed run leaves no marker in `flow.log`, so a quiet,
  unfinished run is reported as "stopped", never as a pass.
- ✅ **A pinned toolchain** (2026-09-24, root `flake.nix`, `tb/tools.sh`,
  `harness/toolchain.py`). `nix develop .#sim` gives:
  - Verilator 5.050;
  - the RISC-V GCC;
  - Icarus;
  - cocotb;
  - a pinned kepler-formal.

  These come from LibreLane 3.0.0's own nix-eda and nixpkgs, the revisions the
  signoff evidence was produced with, and a test holds the two locks equal.
  `nix develop .#physical` is the LibreLane shell, kept separate so the
  signoff flow keeps its own Verilator. Every testbench runner refuses a
  Verilator other than 5.050 instead of falling back to `PATH`, where this
  machine's is the 5.047-devel build behind bug 21. Under this toolchain,
  `tb-soc-titan` (the bug-21 regression) and `tb-soc-generic` on Block C reach
  EXIT SUCCESS. Block A's boot ROM also comes out byte-identical to the one
  the old `/opt` toolchain built.

  `mosaic doctor` checks a machine and flags an exported `PDK_ROOT` that
  overrides the LibreLane Makefile. IIC-OSIC-TOOLS 2026.09 runs through
  `tools/iic-osic.sh`, and evidence produced there is labelled `iic:<tag>`.
  See [reproducing.md](reproducing.md).

  ```bash
  nix develop .#sim
  python3 -m harness doctor
  ```

---

# Part II — What we want next

Grouped by theme. Each item says why it matters and where it is tracked.

## A. Widen the design family

- **More peripherals.** `VALID_PERIPHERALS` is six entries; the generator can
  carry more than the prompt vocabulary admits. _(board: "Widen
  `VALID_PERIPHERALS`")_
- **Qualify a second bus, or say plainly that OBI is the only one.** Three
  fabrics simulate; one is qualified physically. Ambiguity here is worse than
  either answer. _(board: "Qualify a second bus…")_
- **Prove Ibex and the cv32e40p/px variants** in the full-SoC testbench, or
  mark them unsupported. Generator-supported and unproven is the weakest state
  a core can be in.
- **Caches** — needed before the coherent-application class, not before that.
  _(board: "Do we need caches before M5?")_
- **Accelerators as first-class config entries**, kept separate from CPU
  topology. _(board and roadmap §8)_

## B. PDK portability, the "any SoC, any PDK" half of the goal

- **Finish IHP sg13g2**: area calibration from a real run, pad frame, corner
  names end to end, then one full signoff. Today it is a probe.
- **A third process** (sky130A and sky130B are installed under `~/.ciel`,
  with SRAM macros) to prove the technology store generalises rather than
  parameterises GF180.
- **Routability observations are GF180-only** and do not travel: the ceiling
  follows the metal stack, so each process needs its own measurements.

## C. Power and energy evidence

- **Make workload power the objective's basis.** Energy per cycle currently
  comes from OpenSTA's default toggle model. A workload VCD exists
  (`harness/evidence/workload.py`); wiring it in removes the last proxy from
  the objective. _(board: "M2: power parsers and workload-bound power
  evidence")_
- **A memory compiler or catalogue** so SRAM choices are searched rather than
  fixed. _(roadmap §11.3)_

## D. Deepen the PPA loop

- **Widen the density knob's reach.** It has run once, on Block C: **11.9% less
  silicon in four runs** (2.2658 → 1.9959 mm²), every candidate gate-clean, and
  the 4-hart routability ceiling narrowed from (0.65, 0.75) to (0.74, 0.75) so
  `recommended_utilisation` now hands any 4-hart design the denser die. Block B
  and Block A's slot-mates have not been walked, and the ceiling above 0.74 is
  still one unmeasured point wide.
- **Re-validate the clock after a density move.** A tighter die means longer
  wires, so the two knobs interact and the line search treats them as
  independent. Either walk density first and the clock after, or teach the
  search to re-check the incumbent.
- **More knobs**: the cap repair margin, antenna repair iterations, and the
  synthesis fanout constraint.
- **Multi-knob search**, once enough single-variable pairs exist to know which
  knobs interact.
- **Staged evaluation for structure candidates**: synthesis already predicts
  any design's area within ±4%, which is the cheap screen for LLM-proposed
  SoCs, not for physical knobs.
- **Housekeeping the loop needs**: timestamps in the optimizer journal, disk
  pruning policy (a run costs ~4 GB and the volume sits near full), and a way
  to re-establish the noise floor after a tool change.
- **Immediate runs worth doing**: Block B at 40 ns (untried, and the achieved
  path was still falling), the same clock search on Block C (it still asks for
  10 MHz), and GLS at Block B's new 20 MHz.

## E. Small models, and the agentic surface

- **Real small-model numbers.** Sonnet 5 is a stand-in, and the eval can drive
  a provider itself now: `config-author eval --provider opencode-go --model
  <id>` runs the set through `translate_intent` and keeps the replies as
  evidence. What is missing is access rather than code —
  `muse-spark-1.3-contributor` wants a data-collection opt-in (it shares prompt
  data), `mimo-v2.5` wants credits, `deepseek-v4-flash` wants a region opt-in —
  or a local endpoint to point at.
- **Grow the set past 25**, ideally phrased by someone who did not write the
  grammar. The 25 there now found seven faults; the phrasings are still mine.
- **Keep measuring each contract change rather than reasoning about it.** Two
  of the three contract edits so far improved one axis and broke another, and
  only the re-run showed it: making the TDU nullable started the model
  guessing roles, and forbidding invented cores made it refuse the `pico`
  alias. Both took one further rule to settle.
- **Expose more peripherals.** `VALID_PERIPHERALS` is six names, and that is
  exactly what `configs/general.hjson` declares. The x-heep template can
  instantiate `i2s` and `pdm2pcm` as well, but they have no address-map entry
  or interrupt route, so exposing them changes the generated memory map — a
  decision, not a tidy-up.
- **JSON-schema constrained output** for providers that support it — noting
  that valid-JSON rate was already 1.0, so this is insurance, not a fix.
- **Decide what the TUI is for**, and whether one is justified yet.
  _(board: "What is the TUI actually for?", capability survey axis 2)_
- **Distribution**: MCP is the convergent answer, and the packaging is
  reproducibly broken today (`pip install` does not work). _(capability survey
  §2.3–2.4, axis 1)_
- **Skills the flow now needs but does not have.** _(board)_

## F. Verification depth

- **Make GLS a hard signoff check** rather than a reported one. It is a policy
  decision nobody has taken, and the gates deliberately do not take it
  silently.
- **Make the SEC path answer something.** kepler-formal is wired and mute.
- **Longer GLS windows** than the current 12,400-cycle proof.

## G. Flow robustness

- **`tb-*` flow timeouts are shorter than the RTL generation they trigger**, so
  a cold cache looks like a failure. _(board)_
- **Recover the eleven hours a plateau still costs on first sight** — the guard
  helps only once a design has been seen. _(board)_

## H. The v2 compiler (the roadmap's vision)

Tracked in full in
[general_multicore_soc_generator_roadmap.md](general_multicore_soc_generator_roadmap.md);
listed here so this catalogue is not silent about the larger direction:

- A **design-intent IR** and a **resolved SoC IR**, with the compiler stages
  between them made explicit. The intent boundary already exists
  (`harness/intent.py`); `ResolvedSoCIR` is deliberately unbuilt.
- **Platform backends** — `xheep_mcu_amp`, `embedded_cluster`,
  `coherent_application` — so the generator is not one MCU shape wearing
  different core lists.
- **Capability, characterization and qualification catalogs**, so "this core is
  qualified on this process at this corner" is data rather than prose.
- **A typed evidence graph**, generalising today's content-addressed store.
- **Generator invariants** stated per backend and checked.

## I. Process and publication

- **Paper 1** Need to complet this on this.
- **The ten roadmap decisions** in §19, and the team split across the three of
  us. _(board)_
- ~~**Retire or reinstate Block A's suspended waiver** before tapeout.~~
  Re-based onto `blocka_d15_rstsync` (2026-09-24); `waiver-author` passes.

---

## How this file stays true

Every ✅ above names the file or the measurement behind it, and every ⚠ is a
gap we chose to write down rather than round up. When a feature lands, it moves
from Part II to Part I with its evidence; when a measurement is superseded, the
number changes here too. The board remains the queue, and this remains the
inventory.
