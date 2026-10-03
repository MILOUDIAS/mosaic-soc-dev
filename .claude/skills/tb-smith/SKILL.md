---
name: tb-smith
description: >
  Generate and run the verification for a wrapped core: a self-checking
  single-hart SCI testbench (dormancy, wake, liveness, sentinel) plus the
  full-SoC TDU wake demo. Use after wrapper-smith (or any wrapper edit) to
  PROVE the core actually works — a wrapper without a TB PASS is not done.
---

# tb-smith — per-core verification generator

## Commands

```bash
python3 -m harness --json tb-smith generate <core> [--boot-addr 0x180] [--watchdog N]
python3 -m harness --json tb-smith run <core>
python3 -m harness --json tb-smith wake-demo <core>
```

## What the generated TB proves (tb/sci/<core>/)

Phases: (1) reset; (2) DORMANCY — fetch_enable low: zero bus requests and
core_sleep_o==1 (a core that fetches while parked breaks the TDU model);
(3) wake; (4) LIVENESS — bus request counters; (5) SENTINEL — the baked
4-word program at 0x180 writes 0x55 to byte 0x40 of the tb_obi_mem;
(6) watchdog (default 200k cycles — bit-serial cores are slow, don't lower
it). Markers: `TB PASS` / `TB FAIL reason=...` + request/cycle counts,
parsed by `run` into details.metrics.

## Order matters

1. `tb-smith run <core>` — catches wrapper handshake bugs in seconds.
2. `tb-smith wake-demo <core>` — the full-SoC gate (EXIT SUCCESS): TDU wake
   path, bus fabric, per-hart boot addresses.

## Failure playbook

| Symptom | Likely cause |
|---|---|
| TB FAIL reason=dormancy | request masking missing: gate reqs with fetch_enable_i |
| TB FAIL reason=liveness (0 requests) | reset polarity inverted, or clock-stall adapter needed (combinational-memory cores — see fazyrv_sci) |
| alive but no sentinel | ack/rvalid timing: OBI rvalid is 1 cycle, one txn outstanding; check the family's proven wrapper |
| TB PASS but wake-demo fails | boot_addr plumbing in the tpl branch, or gen_filelist visibility of the vendor tree |

## MCP tools

Every MCP session starts with `session_new` (the user's request, verbatim) and `request_scope` (a scope the installation allows; `physical`/`integration` also need a person to run `oh-my-soc approve <scope>` in a terminal). Before claiming success, call `session_status` and report its verdict.

| step | MCP tool |
|---|---|
| generate a TB | `tb_generate {core, watchdog?}` |
| run it | `tb_run {core, timeout?}` |
| wake demo | `tb_wake_demo {core, execute?}` |

The `python3 -m harness ...` commands in this card are the human path; a gated agent uses these tools instead.
