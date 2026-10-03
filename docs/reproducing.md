# Reproducing results: the pinned toolchain

Two runtimes are supported. **Nix is the reference**: every signoff and
simulation result the project reports is produced there. The IIC-OSIC-TOOLS
container is a convenience runtime, and its results are labelled as such.

`mosaic doctor` checks a machine against the pins and tells you what to fix.

## Nix (reference)

```bash
nix develop .#sim        # simulation: Verilator 5.050, RISC-V GCC, Icarus, cocotb,
                         # kepler-formal, verible  (also the default shell)
nix develop .#physical   # the LibreLane 3.0.0 shell (same as flow/librelane's)
```

| Tool | Version | Source |
|---|---|---|
| LibreLane | 3.0.0 | `github:librelane/librelane/3.0.0` |
| nix-eda / nixpkgs | 6.11.0 (`8f990fb`) / 25.11 (`b3aad46`) | LibreLane 3.0.0's own lock |
| Verilator (sims) | **5.050** | nix-eda's package, re-pinned |
| RISC-V GCC | 14.3.0, `riscv32-none-elf-` | nixpkgs `pkgsCross.riscv32-embedded` |
| kepler-formal | nix-eda 7.5.0 | the revision `run_lec.sh` last resolved |
| Icarus, cocotb, verible | nix-eda 6.11 / nixpkgs 25.11 | |

Why these pins:

- **LibreLane's own nix-eda and nixpkgs.** The Block A/B/C signoff evidence was
  produced under exactly these binaries (`flow/librelane/flake.lock`). The root
  `flake.lock` pins the same three revisions, and
  `test_toolchain_pin.py` fails if the two ever disagree.
- **Verilator 5.050, not nix-eda's 5.044.** The oss-cad-suite 5.047-devel build
  miscompiles cv32e40x's load-use hazard (bug 21). 5.050 is the release the
  full-SoC regression passed on. 5.044 was never checked.
- **Two shells, not one.** LibreLane puts nix-eda's Verilator (5.044) on its
  PATH, and its lint step calls `verilator` by name. Merging the sim shell into
  it would silently change a tool inside the signoff flow.

The first `nix develop .#sim` builds Verilator 5.050 locally (about 10 minutes).
Everything else is a binary-cache hit.

**Disk.** A flake inside a git repository is evaluated from a store copy of the
tracked tree. A dirty tree makes a new copy each time its content changes.
This was already true of `flow/librelane`'s flake. Reclaim the space with
`nix-collect-garbage` when runs are not in flight.

### The testbench runners refuse, they don't guess

Every runner that calls Verilator sources `tb/tools.sh`:

- **Verilator:** `$VERILATOR_PIN` (an install prefix, `bin/` or `usr/bin/`)
  if set. Otherwise `verilator` on `PATH`, but only if it reports 5.050.
  Otherwise the runner **stops** and names the fix.
- **RISC-V GCC:** `$RISCV_TC` (e.g. `<dir>/bin/riscv32-unknown-elf`) if
  set, otherwise the first `riscv32-*-elf-gcc` on `PATH`, otherwise stop.

The runners used to default to paths that exist on one machine, then fall back
to whatever was on `PATH`. That is how a 5.047-devel Verilator can silently
decide a verdict.

`mosaic flow-runner run <flow>` enters `nix develop <repo>#sim` by itself
when it is not already inside a nix shell. It does not do this for three flows:
- `harden-*`, where LibreLane brings its own environment;
- `pytest`, which runs the harness's own Python;
- `gls`, whose Block A/C evidence came from the host's Icarus 13.0. nix-eda
  ships a different Icarus snapshot, so moving GLS means re-running that
  evidence.

### Python

`.venv/` (from `make venv`) stays the Python layer. `util/python-requirements.txt`
pins every git dependency to an exact commit:
- FuseSoC is pinned to `c36dffc` (the `@ot` branch it used to follow).
- edalize is pinned to 0.6.8 from PyPI, which is what the working venv actually
  runs.

## IIC-OSIC-TOOLS (convenience)

```bash
tools/iic-osic.sh                    # shell in hpretl/iic-osic-tools:2026.09
tools/iic-osic.sh klayout run.gds    # one command
```

This mounts the repo root at `/workspace`, runs as your user, and exports
`MOSAIC_TOOLCHAIN=iic:2026.09`. The tag is pinned; `latest` is never used.

It is not the reference because its tools are other versions:

| | IIC 2026.09 | Nix reference |
|---|---|---|
| Magic / KLayout / Netgen | 8.3.684 / 0.30.12 / 1.5.323 | 8.3.623 / 0.30.7 / 1.5.316 |
| Yosys | 0.69 | 0.62 |
| Verilator | 5.052 | 5.050 |

Different signoff tools can give different DRC/LVS verdicts. So:
- `mosaic doctor` reports the `iic:<tag>` runtime.
- The evidence store folds the runtime into the tool digest
  (`harness/evidence/store.py`), so a run recorded there never shares a key
  with a nix-made run.

Use the container for viewing layouts and for machines without Nix. Make
claims from Nix runs only.

## Checking a machine

```bash
mosaic doctor           # human-readable
mosaic --json doctor    # for scripts; exits 1 when a required tool is off-pin
```

`doctor` reports:
- the runtime;
- each tool, required or advisory, with its version and the fix;
- where each open PDK is installed: repo gf180mcuD, and `~/.ciel` sky130A and
  ihp-sg13g2.

It warns when the shell exports `PDK_ROOT`/`PDK`, because
`flow/librelane/Makefile` uses `?=` and yields to them. (`run_signoff.sh`
passes `--pdk-root` explicitly and is unaffected.)
