# cocotb + Verilator Makefile for the MOSAIC multi-core harness.
# Run via ../run_cocotb.sh (which generates the RTL first), or directly:
#   make SIM=verilator
# Requires the SoC RTL to already be generated for configs/mosaic_sim.yaml.

SIM           ?= verilator
TOPLEVEL_LANG ?= verilog
TOPLEVEL       = cocotb_top
MODULE         = test_mosaic

THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO     := $(abspath $(THIS_DIR)/../../..)

INC  := $(REPO)/hw/core-v-mini-mcu/include
SERV := $(REPO)/hw/vendor/mosaic/serv
FAZ  := $(REPO)/hw/vendor/mosaic/fazyrv
SCI  := $(REPO)/hw/sci
MCU  := $(REPO)/hw/core-v-mini-mcu
MOSAIC_GENERATED_ROOT ?= $(REPO)/build/mosaic/INVALID-MANIFEST-NOT-SUPPLIED/generated
GEN_MCU := $(MOSAIC_GENERATED_ROOT)/hw/core-v-mini-mcu
TB   := $(REPO)/tb/mosaic
TC   := $(REPO)/hw/vendor/pulp_platform/tech_cells_generic/src/rtl/tc_clk.sv

# Packages first (declaration order matters), then DUT + memories + wrapper.
VERILOG_SOURCES = \
  $(INC)/obi_pkg.sv \
  $(INC)/reg_pkg.sv \
  $(INC)/fifo_pkg.sv \
  $(INC)/addr_map_rule_pkg.sv \
  $(GEN_MCU)/include/core_v_mini_mcu_pkg.sv \
  $(TC) \
  $(GEN_MCU)/cpu_subsystem.sv \
  $(TB)/tb_obi_mem.sv \
  $(THIS_DIR)/cocotb_top.sv

# Verilator: resolve core modules by library dir (-y), includes by -I, and
# silence the vendored-core lint noise.
COMPILE_ARGS += --timescale 1ns/1ps
COMPILE_ARGS += -Wno-WIDTH -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
COMPILE_ARGS += -Wno-UNDRIVEN -Wno-UNUSEDPARAM -Wno-DECLFILENAME -Wno-TIMESCALEMOD
COMPILE_ARGS += -Wno-PINMISSING -Wno-CASEINCOMPLETE -Wno-SYMRSVDWORD -Wno-GENUNNAMED
# FazyRV's bit-serial carry chains (fazyrv_pc/alu) are intentional combinational
# loops Verilator flags as UNOPTFLAT but evaluates correctly by iterating.
COMPILE_ARGS += -Wno-UNOPTFLAT
COMPILE_ARGS += -I$(GEN_MCU)/include -I$(INC)
COMPILE_ARGS += -y $(SERV)/rtl -y $(SERV)/servile -y $(FAZ)/rtl -y $(SCI) -y $(MCU) -y $(INC)

include $(shell cocotb-config --makefiles)/Makefile.sim
