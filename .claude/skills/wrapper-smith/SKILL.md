---
name: wrapper-smith
description: >
  Wrap ANY open-source RISC-V core or IP for the MOSAIC SCI: parse its ports,
  classify the native bus against 9 proven protocol families, and scaffold
  the wrapper plus ALL 8 integration touchpoints. Use whenever the user asks
  to "add core X" / "integrate this IP". You fill the marked semantic gaps;
  tb-smith verifies the result.
---

# wrapper-smith — core/IP integration mechanism

## The triangle contract

1. **analyze** (deterministic) — port parse + bus classification + control
   extraction. NEVER guess a core's protocol yourself; run analyze first.
2. **scaffold** (deterministic) — stages the wrapper (cloned from the proven
   family wrapper, or the real ahb_split template) + registry edits + tpl
   branch + sci.core + gen_filelist + bring-up config. Dry-run by default.
3. **You fill `TODO(wrapper-smith)` markers** — port-name mapping, irq
   wiring, handshake quirks. The proven wrapper for each family is in
   `hw/sci/` — copy its idioms.
4. **tb-smith** closes the loop (generate + run the TB; then the wake demo).

## Commands

```bash
python3 -m harness --json wrapper-smith families
python3 -m harness --json wrapper-smith fetch <url>[@commit] [--subdir hdl] [--name X]
python3 -m harness --json wrapper-smith analyze <rtl-file-or-dir> [--top M] [-o a.json]
python3 -m harness --json wrapper-smith scaffold <core> --from a.json          # dry-run
python3 -m harness --json wrapper-smith scaffold <core> --from a.json \
        --vendor-from <fetched rtl_root> --apply                               # to tree
```

## Workflow for "integrate core X from GitHub" (4 commands + your fill)

1. `fetch <url>[@commit] [--subdir hdl]` → clones, pins the exact commit,
   detects the license (GPL-family is flagged — stop and review), and writes
   provenance that scaffold folds into the vendored `.core` header. Use
   `details.rtl_root` for the next steps.
2. `analyze <rtl_root> --top <module>` → family, confidence, evidence,
   runner_up, TODO queue. Below 0.5 → "unknown": inspect the port list,
   pick `--family` yourself.
3. `scaffold <core> --from a.json --vendor-from <rtl_root>` (dry-run) →
   review the staged diff; then rerun with `--apply`. With a vendor tree the
   sci.core `depend:` edge is added automatically and a full FuseSoC
   resolution smoke runs post-apply (a broken graph fails the scaffold).
4. Fill every `TODO(wrapper-smith)` in the wrapper: the core instantiation
   port map is YOUR main job (analysis lists every port).
5. `tb-smith generate <core>` + `tb-smith run <core>` → `TB PASS` required.
6. `tb-smith wake-demo <core>` → EXIT SUCCESS = integration done.

## Failure playbook

- family unknown / wrong: `--family` override; the families table lists the
  proven reference wrapper for each.
- mcu_gen render fails after apply: the tpl branch was cloned from the
  family's proven branch — check params passed to <core>_sci exist in the
  wrapper's #() header.
- Verilator MODMISSING: vendor tree not visible — check gen_filelist -y
  entry; packages/filename≠module need explicit entries (snitch/cva6 blocks
  are the reference).
- Reset polarity: SCI is active-low reset-hold (`rst_ni & fetch_enable_i`);
  active-high cores get an inversion — verify, this is the #1 wrapper bug.

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `oh-my-soc approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| analyse RTL | `wrapper_analyze {path, top?, output?}` |
| stage a wrapper | `wrapper_scaffold {core, analysis, vendor_from?, family?}` |
| apply it | `wrapper_scaffold {..., apply: true}  (needs the `integration` approval token)` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
