# Contributing

Thank you for considering a contribution. This page covers the environment, the
checks to run before sending a change, and what a pull request should contain.

## Environment

The Python layer and the EDA tools are set up separately.

```bash
git clone https://github.com/mosaiclab-org/mosaic-soc.git
cd mosaic-soc
make venv              # creates .venv/ from util/python-requirements.txt
nix develop .#sim      # Verilator 5.050, RISC-V GCC, Icarus Verilog, cocotb, verible
./mosaic doctor        # confirms each tool matches the pinned version
```

`nix develop .#sim` needs Nix with flakes enabled. Its first use builds
Verilator, which takes about ten minutes. The simulation runners refuse any
other Verilator version, so a system Verilator is not a substitute.
[docs/reproducing.md](docs/reproducing.md) explains the pins and an optional
container runtime.

The physical flow has its own shell, `nix develop .#physical`, and is needed
only for hardening ([docs/physical-flow.md](docs/physical-flow.md)).

## Running the checks

The Python suite needs only the virtual environment:

```bash
make test
python3 -m pytest test/test_mosaic_gen/test_bus_types.py -q   # one file
```

On a clone without hardening runs or a PDK, some tests are skipped.

Generate and simulate a design inside the simulation shell:

```bash
make mosaic-gen MOSAIC_CFG=configs/mosaic_wake_demo.yaml
MOSAIC_CFG=configs/mosaic_wake_demo.yaml tb/mosaic_soc/run_generic.sh
```

The simulation passes only if its output ends with `EXIT SUCCESS`. The same two
steps through the tool, which checks that marker for you:

```bash
./mosaic flow-runner run mosaic-gen-config --config configs/mosaic_wake_demo.yaml
./mosaic flow-runner run tb-soc-generic    --config configs/mosaic_wake_demo.yaml
```

For a change that touches the generator, the registry or a template, also run:

```bash
./mosaic tb-matrix run --tier validate
scripts/run_sweep.sh --only <regex>      # the affected steps; --list shows them
```

[docs/verification.md](docs/verification.md) describes every testbench.

## Generated RTL is never committed

Files rendered from `*.sv.tpl` templates are build products. Commit the template
and the generator change, never the rendered `.sv`. `make mosaic-gen` writes
each design to its own directory under `build/mosaic/`, which git ignores.
`make mcu-gen`, the single-core path inherited from X-HEEP, renders in place;
those outputs are listed in `.gitignore`.

After changing a template, regenerate before you simulate. A stale bundle is the
most common cause of a confusing failure.

Layout files are not committed either. A run directory under
`flow/librelane/*/runs/` stays local, except a `final/metrics.json` that a
waiver or the area model cites.

## Commits and pull requests

- One logical change per commit. Write the subject as a sentence that says what
  is true after the commit, and use the body for the reason.
- Keep a pull request focused. Describe what changed, why, and how you checked
  it: the commands you ran and their results.
- Add or update a test for behaviour you change. A change to
  `util/mosaic_gen/core_registry.py` needs a test in `test/test_mosaic_gen/`.
- State any result you could not check, such as a simulation you did not have
  the tools to run.
- Do not weaken a check to make a change pass. In particular, do not add a skip
  to the signoff flow, and do not add or widen a signoff waiver without a
  hardened run that supports it.
- New files carry an SPDX header. MOSAIC's own files are licensed
  `Apache-2.0 WITH SHL-2.1`. Code vendored from another project keeps its
  licence, and its origin and commit are recorded next to it.
- Format Python with `make format-python` and SystemVerilog with `make verible`.

## Adding a core

A core needs a registry entry, a bus wrapper and a template branch. The
procedure, and the `./mosaic wrapper-smith` command that scaffolds it, are in
[docs/cores.md](docs/cores.md#adding-a-core). A new core is accepted when:

1. `./mosaic tb-smith run <core>` prints `TB PASS`;
2. `./mosaic tb-smith wake-demo <core>` reaches `EXIT SUCCESS`;
3. `./mosaic tb-matrix run --tier validate` still passes;
4. the vendored source records its upstream repository, commit and licence.

## Working with AI coding agents

[AGENTS.md](AGENTS.md) is the contract for agents. The skill cards in
`.claude/skills/` and the plugin in `plugins/mosaic/` are part of the product:
if you change a command, update its card.
