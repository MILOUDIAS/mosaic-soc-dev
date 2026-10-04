# Roadmap

What is planned, grouped by theme. Nothing on this page exists yet. What does
exist is in [status.md](status.md).

## Widen the design family

- Prove Ibex, cv32e40p and cv32e40px in the complete SoC, or mark them
  unsupported.
- Bring QERV into the full-SoC regression.
- Expose more peripherals. The platform can instantiate blocks that the
  configuration cannot yet name, and adding them changes the memory map.
- Qualify a second bus fabric physically, or state that OBI is the only one.
- Caches, and accelerators as first-class configuration entries.

## A second and third technology

- Finish IHP sg13g2: an area calibration from a real run, corner names end to
  end, then one complete signoff.
- Port to sky130 to show that the technology store generalises.
- Routability records per technology. The demonstrated densities follow the
  metal stack and do not transfer from GF180MCU.

## Power evidence

- Compute energy per cycle from a simulated workload instead of the timing
  tool's default switching activity. The workload capture exists
  (`harness/evidence/workload.py`); it is not yet the basis of the objective.
- A catalogue of SRAM macros so that memory choices can be searched.

## The improvement loop

- More settings for the line search: the capacitance repair margin, antenna
  repair iterations, the synthesis fan-out limit.
- Re-check the clock after a density change. The two interact and the search
  treats them as independent.
- Search over several settings at once, when enough single-setting experiments
  exist to know which interact.
- Walk the density of Block B, and the clock of Block C.
- Housekeeping: timestamps in the search journal, a disk pruning policy, and a
  way to re-measure run-to-run variation after a tool change.

## Verification depth

- Decide whether gate-level simulation becomes a signoff hard check.
- Make the equivalence check return a verdict.
- Longer gate-level runs, and gate-level simulation of Block B at 20 MHz.
- Raise the flow timeouts that are shorter than a cold RTL generation.

## Agent tooling

- Measure the text-to-configuration path with small local language models. The
  evaluation set and its scoring are in place (`./mosaic config-author eval`).
- Grow the evaluation set with requests written by people who did not write the
  grammar.
- Constrain model output with a JSON schema where the provider supports it.
- Verify a live model session in each supported host.
- Expose the remaining skills (preflight, waiver audit, netlist comparison,
  gate-level triage, technology porting) as gated tools.

## The generator as a compiler

- An explicit intermediate representation between the configuration and the
  templates. The first half, a typed view of the design intent, exists in
  `harness/intent.py`.
- Platform backends beyond the microcontroller shape: a clustered design and an
  application-class design with coherent caches.
- Catalogues that record, as data, which core is qualified on which technology
  and at which corner.
