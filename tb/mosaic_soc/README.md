# Full-SoC simulation

These runners build and simulate the complete generated SoC in Verilator: the
X-HEEP test harness around `core_v_mini_mcu`, with every hart, the bus fabric,
the memories, the TDU, the DMA and the peripherals. Programs run on the harts
through the real boot ROM, bus and memory.

Each runner generates the RTL for the configuration named by `MOSAIC_CFG`,
builds firmware, builds the Verilator model, runs it, and prints a result line.
A run passes only when its log contains `EXIT SUCCESS`, which the program
produces by writing an exit value of zero to the SoC controller.

The pinned tools are required (`nix develop .#sim`); `tb/tools.sh` refuses any
other Verilator.

## Runners

| Script | Default configuration | What it checks |
|---|---|---|
| `run_generic.sh` | `mosaic.yaml` | Any configuration. Reads the generated `boot_images.json`, builds one liveness image per boot slot, wakes every worker through the TDU, and requires `sentinel[hart] == hart + 1` from every configured hart. Log: `sim-generic.log`. |
| `run.sh` | `configs/mosaic_wake_demo.yaml` | The wake demonstration on a design with one orchestrator and two workers. Log: `sim.log`. |
| `run_titan.sh` | `configs/mosaic_titan_log.yaml` | Four `titan` harts running one program that branches on `mhartid`: hart 0 queues three task descriptors, the others pop and execute them. |
| `run_fw.sh` | `mosaic.yaml` | The deployable firmware image of `sw/firmware`, loaded only through the SPI flash model: boot ROM hand-off, execution in place, worker copy with CRC check, TDU dispatch to six workers. |
| `run_uart.sh` | `configs/mosaic_tapeout_ultra.yaml` | The UART of a design whose only peripheral is the UART. |

```bash
MOSAIC_CFG=configs/mosaic_picorv32.yaml tb/mosaic_soc/run_generic.sh
MOSAIC_CFG=configs/mosaic_floonoc.yaml  tb/mosaic_soc/run.sh
MOSAIC_CFG=configs/mosaic_titan_obi.yaml tb/mosaic_soc/run_titan.sh
```

The same runs are available as flows, which check the marker:

```bash
./mosaic flow-runner run tb-soc-generic --config configs/mosaic_picorv32.yaml
./mosaic flow-runner run tb-soc-wake    --config configs/mosaic_floonoc.yaml
./mosaic flow-runner run tb-soc-titan   --config configs/mosaic_titan_obi.yaml
./mosaic flow-runner run tb-soc-fw
```

## The wake demonstration in detail

With `configs/mosaic_wake_demo.yaml` (cv32e20 orchestrator, FazyRV and SERV
workers):

1. The orchestrator boots from the boot ROM and writes `0xC0FFEE00` to address
   `0x3000`.
2. It writes `0x6` to the TDU register `WAKE_REQ` at `0x200A000C`, which pulses
   the wake lines of harts 1 and 2.
3. Hart 1 starts at its boot address `0x1000` and writes `0xA71A5000` to
   `0x3004`. Hart 2 starts at `0x2000` and writes `0x4E414E00` to `0x3008`.
4. The orchestrator polls both addresses, then writes exit value 0 to the SoC
   controller, and the test harness prints `EXIT SUCCESS`.

The addresses `0x3000` and above are the sentinel window. A configuration's boot
images and linker regions must stay clear of it.

For Rocket and BOOM workers the programs `prog/atlas_tl.S` and `prog/nano_tl.S`
store their sentinels through the address-window translation described in
`hw/vendor/mosaic/berkeley/README.md`.

## Configurations without an orchestrator

A configuration with `profile: testbench` and no `titan` is legal. In
`run_generic.sh` hart 0 is then released at reset only to dispatch work to the
other harts through the TDU. A configuration with `profile: soc` always requires
one leading `titan`.

## Simulation-only substitutions

`gen_filelist.py` builds the Verilator file list from the FuseSoC output and
makes three substitutions that apply to simulation only:

- **Clock gate.** The cv32e20 clock gate is a latch whose combinational feedback
  does not converge in Verilator. `cve2_clock_gate.sv` here replaces it with a
  registered gate that honours the same enable. The latch gate is what synthesis
  uses.
- **Test utilities.** `tb_util.svh` is rendered from `tb/tb_util.svh.tpl` for the
  configuration's RAM bank layout, without the DPI export of the upstream
  version.
- **Live sources.** Build copies in the FuseSoC output are remapped to the
  sources and generated files they came from, so a regenerated file is never
  shadowed by an older copy.

## Why not Icarus Verilog

`run_icarus.sh` records an attempt to run this bench in Icarus Verilog. Icarus
cannot compile the SoC: its SystemVerilog front end rejects package function
calls in parameter defaults, named struct patterns in parameters and unpacked
array parameters, all of which occur in the bus crossbar's dependencies and in
the cv32e20 core. `idma_stub.sv` and `uartdpi_stub.sv` belong to that attempt.
Icarus is used for gate-level simulation (`tb/gls/`), where the input is a
netlist of library cells.

## Files

| File | Purpose |
|---|---|
| `run*.sh` | the runners above |
| `gen_filelist.py` | writes the Verilator file list |
| `pack_xip_hex.py` | packs execute-in-place images into a flash hex file |
| `prog/` | wake demonstration programs and linker script |
| `prog_generic/generic.S` | the liveness program of `run_generic.sh` |
| `prog_titan/` | the multiprocessor program |
| `prog_uart/uart.S` | the UART program |
| `mosaic_tb.sv`, `build_diag.sh` | a diagnostic top level that dumps fetch addresses and reset state |
| `cve2_clock_gate.sv` | the simulation clock gate |
