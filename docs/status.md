# Status

What works today, how each claim is checked, and what is known to be wrong or
missing. Last revised 2026-10-03.

Every design the generator can produce is at one of three levels.

## Level 1: signoff-clean on GF180MCU

One design: **Block A, the GF180MCU reference design**
(`configs/mosaic_tapeout_ultra.yaml`, wrapper `mosaic_block_a`).

- Two SERV harts (one orchestrator, one worker), no on-chip SRAM pool, a
  128-byte scratchpad, a 1 KB boot ROM, code executed in place from external
  flash, one UART, the TDU, no DMA, no debug module, no PLIC.
- Run `blocka_d15_rstsync`: a 1110 um square die (1.2321 mm2), 82.9%
  utilisation, hardened against an external padframe DEF. Magic DRC, KLayout
  DRC, LVS, XOR, routing DRC, antenna and power-grid checks are all at zero.
  Setup slack is +2.199 ns and hold slack +0.054 ns at the worst corner on a
  50 ns clock, with no maximum-transition or maximum-capacitance violations.
- Two waivers are recorded for it in `flow/librelane/signoff_waivers.yaml`: two
  clock-tree root buffers above the project's fan-out limit, and eleven unused
  input terminals of output-only pads. Both are pinned to the exact violators
  and are due for review on 2026-11-30.
- The waiver record states that gate-level simulation of this netlist passed.
  That log is not in version control.

This is the only combination that `soc.target: tapeout` accepts.

## Level 2: hardened but not tapeout-qualified

**Block B** (three SERV harts) and **Block C** (four SERV harts), the same
architecture as Block A with more workers.

- Both have runs that pass every hard check, timing and the transition and
  capacitance gates: `blockb_sdc`, `blockb_m45`, `blockc_sdc`, `blockc_m45`.
- Both report fan-out violations that no waiver accepts, and neither is in the
  tapeout matrix of `core_registry.py`, so `target: tapeout` refuses them.
- Later runs recorded in the source tree, but not tracked, show Block C routing
  at a 0.74 utilisation target on 1.9959 mm2 and Block B closing at 20 MHz.

The measured numbers and run names are in
[physical-flow.md](physical-flow.md#measured-results).

## Level 3: RTL and simulation only

Everything else:

- every core other than SERV;
- the `log` and `floonoc` bus fabrics;
- every design with an on-chip SRAM pool or external RAM;
- every design that keeps the DMA, the debug module or the PLIC;
- every design on `pdk: sky130` or `pdk: ihp-sg13g2`.

The IHP sg13g2 technology is declared in the technology store
(`harness/physical/technology.py`) with its site geometry and corner names, a
probe configuration (`configs/mosaic_ihp_probe.yaml`) and an unqualified signoff
template. `./mosaic pdk-port ihp-sg13g2` reports five of eight porting
requirements missing, starting with an area calibration from a hardened run.
For sky130, none of the eight is satisfied.

## What is proven, and how

| Area | Claim | How it is checked |
|---|---|---|
| Generation | One YAML file yields RTL, linker scripts, a boot manifest and a testbench in a bundle named by a hash of its inputs. | `make mosaic-gen`; `test_build_manifest.py` and the generator tests in `test/test_mosaic_gen/` |
| Validation | A configuration is refused before anything is generated if it breaks a rule. | `core_registry.py`; `./mosaic tb-matrix run --tier validate` over 248 configurations |
| Cores | Ten cores run in the complete SoC: cv32e20, cv32e40x, serv, fazyrv, picorv32, snitch, hazard3, cva6, rocket, boom. | the `wake_*` and `titan_smp_*` steps of `scripts/run_sweep.sh` |
| Fabrics | The wake demonstration and the four-hart multiprocessor run on `obi`, `log` and `floonoc`. | sweep steps `wake_obi`, `wake_log`, `wake_floonoc`, `titan_smp_*` |
| TDU | Register access at the SoC address, the eight-deep queue, per-hart wake. | `tb/tdu/soc`, `tb/mosaic/cocotb`, the full-SoC benches |
| DMA | The iDMA wrapper copies in one, two and three dimensions on every stream. | `tb/idma/cocotb/run.sh` |
| Memory profiles | SRAM pool, execute in place with and without a scratchpad, external RAM. | `test_external_memory_profile.py`; sweep step `generic_boot_blocka` |
| Firmware | A deployable flash image boots the default seven-hart design. | sweep step `firmware_7hart` |
| Any generated SoC | Every configured hart must report before the bench passes. | `tb/mosaic_soc/run_generic.sh`; `tb-matrix` `sim` tier, 30 configurations |
| Physical flow | No step is skipped and a missing metric is not read as clean. | `flow/librelane/experimental/run_signoff.sh`; `harness/evidence/` and its tests |
| Reproducibility | The simulation and physical toolchains are pinned to the same nix-eda and nixpkgs revisions. | `flake.nix`; `test_toolchain_pin.py`; `./mosaic doctor` |
| Agent tooling | Tools reach the flows only through typed, gated calls. | `harness/gates.py`; `test_mcp_plugin_mode.py`, `test_agent_runtime.py` |
| Python suite | 1653 passed, 100 skipped. | `make test` |

The sweep steps are defined in the repository; their results depend on running
them with the pinned toolchain ([verification.md](verification.md)).

## Known defects and gaps

Cores and fabrics:

- **QERV** is exercised only inside the CPU subsystem testbench, not in the
  complete SoC.
- **Ibex** appears only in a configuration that is rendered, never simulated.
  **cv32e40p** and **cv32e40px** have no shipped MOSAIC configuration. All three
  are accepted by the generator and unproven.
- **CVA6, Rocket and BOOM** are simulation only and cannot enter a GF180MCU
  layout.
- Only the `obi` fabric has been through the physical flow.
- Six peripherals can be requested. The underlying platform has more that the
  configuration cannot name.

Verification:

- **Gate-level simulation is functional only.** The GF180MCU cell models cannot
  be timing-annotated under Icarus Verilog. A GLS pass is reported but is not a
  signoff hard check.
- **Equivalence checking** is wired as the `lec` flow and does not yet return a
  usable verdict.
- The `tb-*` flows have timeouts shorter than the RTL generation they can
  trigger on a cold build directory, so the first run can time out.

Physical:

- **Power is a proxy.** Energy per cycle is computed from the timing tool's
  default switching activity, not from a simulated workload.
- **The area model and the routability records are GF180MCU only**, and cover
  only SERV designs of 2 to 4 harts.
- **The routing guard** recognises a plateau only after it has happened; it does
  not predict one before routing starts.
- **Block A's evidence needs the full run directory.** The PPA gate and
  `waiver-author` read the design name from the run's `resolved.json`, which is
  not tracked. On a fresh clone `./mosaic physical-intent ppa` therefore reports
  `blocka_d15_rstsync` as rejected, and `./mosaic waiver-author` reports the
  disconnected-pin waiver as not matching, although both pass on the complete
  run.
- **The chip-level flow has no inputs.** `make harden` and `make classic` in
  `flow/librelane/` need a physical bundle with SRAM macro views and a bound
  pad adapter that the repository does not contain.

Tooling:

- `configs/titan_nomem.yaml` does not pass validation.
- `./mosaic doc-gen dashboard` expects a `DASHBOARD.md` file that the
  repository does not contain; pass `--file`.
- `./mosaic pdk-port` reports GF180MCU as incomplete until the PDK has been
  cloned with `make -C flow/librelane clone-pdk`.
- Model-driven use of the plugin has been checked by handshake and by the test
  suite, not by a live model session in every host
  ([agent-harness.md](agent-harness.md#host-plugins)).
