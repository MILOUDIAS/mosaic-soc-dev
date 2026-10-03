# oh-my-soc demos — prompt→SoC and wrap-any-core

Phase 2 deliverable walkthroughs. The harness (`python -m harness`, see
[`harness/README.md`](../harness/README.md)) provides typed deterministic
tools plus a built-in API agent loop and a visible deterministic workflow.
Claude Code and oh-my-pi can drive the same tools through the shared skill
cards in [`.claude/skills/`](../.claude/skills/).

**oh-my-pi provenance:** vendored for study at `refs/IP_Tools/oh-my-pi`
([can1357/oh-my-pi](https://github.com/can1357/oh-my-pi)). What we adapted
and why — **drive, don't fork**:

| oh-my-pi mechanism | oh-my-soc use |
|---|---|
| skills = `SKILL.md` dirs; its `claude` provider reads `.claude/skills/` | one set of cards serves omp AND Claude Code (zero duplication) |
| custom tools in `.omp/tools/` (`CustomToolFactory`, `pi.zod`) | [`.omp/tools/oh-my-soc.ts`](../.omp/tools/oh-my-soc.ts): one schema-validated tool → `python -m harness <skill> <cmd> --json` |
| full TUI + event plumbing | native interactive omp runs; normalized headless streams use the built-in API driver |
| "agent assists, deterministic tooling checks" | every pipeline stage is a hard gate (schema validation, semantic checks, mcu-gen render, TB PASS, wake-demo EXIT SUCCESS) |

## 1. Prompt → verified SoC

**Agent path (Claude Code):** open this repo in Claude Code and ask
*"build me an SoC with one cv32e20 controller, two picorv32 workers, 64KB
sram, a tdu and a uart"* — the `soc-from-prompt` card routes the agent
through the gated pipeline.

**Agent path (oh-my-pi):** launch its full TUI (no print-mode suppression):
```bash
./oh-my-soc setup --driver omp
./oh-my-soc agent "an SoC with one cv32e20 controller, two picorv32 workers, 64KB sram, tdu, a uart"
```

**No-LLM visible workflow (CI-able):**
```bash
./demo/01_soc_from_prompt.sh
```
Deterministic grammar → config → topo check → mosaic-gen → full-SoC TDU wake
demo → **EXIT SUCCESS**. Every parse decision is reported (`matched` /
`unrecognized` / `repairs` — nothing silent).

## 1a. The tapeout part, and where the model actually is

```bash
./demo/03_blocka_from_prompt.sh
```

The same claim on the design being taped out rather than on a toy — and it
separates the two halves rather than blurring them:

| | |
|---|---|
| **steps 2–5** | no model. The regex grammar reproduces `configs/mosaic_tapeout_ultra.yaml` field-for-field, then the capability gate **refuses** the same prompt with `no debug` removed, and refuses a sim-only core asked to tape out. CI-able; evidence about the *guardrails*. |
| **step 6** | the model. Gated on an agent harness on PATH (`claude` / `omp`), **not** an API key. It gets the framing `oh-my-soc agent` sends — imported from the harness, not retyped — and must reach the frozen config through typed `config-author` flags, with `soc-from-prompt` explicitly off the table. |

Step 6 is *reported*, not asserted: a model run is evidence, so the exit status
stays governed by the deterministic steps and a divergence is printed in full.

```bash
MOSAIC_DEMO_AGENT=off ./demo/03_blocka_from_prompt.sh   # skip the model step
MOSAIC_DEMO_AGENT=omp ./demo/03_blocka_from_prompt.sh   # force a driver
```

## 2. Wrap a NEW core from GitHub — the 4-command story

```bash
python3 -m harness wrapper-smith fetch https://github.com/Wren6991/Hazard3@8af99293 --subdir hdl
#   → pinned clone, license detected (Apache-2.0; GPL-family is FLAGGED),
#     provenance recorded for the vendored .core header
python3 -m harness wrapper-smith analyze <rtl_root> --top hazard3_cpu_2port
python3 -m harness wrapper-smith scaffold hazard3 --from a.json --vendor-from <rtl_root> --apply
#   → wrapper + registries + tpl branch + sci.core file AND depend edge +
#     gen_filelist + bring-up config; post-apply FuseSoC-graph smoke must pass
# ... fill the TODO(wrapper-smith) markers (the agent step) ...
python3 -m harness tb-smith generate hazard3 && python3 -m harness tb-smith run hazard3
python3 -m harness tb-smith wake-demo hazard3     # EXIT SUCCESS = done
```

## The Hazard3 record (how it actually went)

```bash
./demo/02_wrap_new_core.sh
```

The scaffold → agent-fill → TB-verified triangle, demonstrated on a core
this repo had never seen, on a bus family (AHB-Lite) no proven wrapper
covered:

1. **analyze** — `wrapper-smith analyze hdl/hazard3_cpu_2port.v` classified
   `ahb_split` at **1.00 confidence** (63 ports, evidence reported) and
   emitted the TODO queue (parameterized irq width, config-include boot
   parameter).
2. **scaffold** — 45 files staged + 5 idempotent edits (registries, tpl
   branch at the anchor, sci.core, gen_filelist, vendor tree + .core stub).
3. **agent-fill** — the marked gaps: real 63-port map, irq_i[3/7/11] →
   soft/timer/ext irq, `RESET_VECTOR/MTVEC_INIT`, power/debug tie-offs,
   snitch-branch params dropped (the flagged review item).
4. **verify** — `tb-smith run hazard3` → **TB PASS (229 cycles)**;
   `tb-smith wake-demo hazard3` → full-SoC **EXIT SUCCESS**.

Offline fallback (no network): regenerate the picorv32 wrapper into staging
and diff against the shipped one — the diff is only the provenance banner:
```bash
python3 -m harness wrapper-smith analyze hw/vendor/mosaic/picorv32/picorv32.v --top picorv32 -o /tmp/a.json
python3 -m harness wrapper-smith scaffold pico2 --from /tmp/a.json    # dry-run
diff <(sed 's/pico2/picorv32/g' build/wrapper_smith/pico2/stage/hw/sci/pico2_sci.sv) hw/sci/picorv32_sci.sv
```
