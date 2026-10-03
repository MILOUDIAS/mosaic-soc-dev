# cocotb + Verilator Makefile for the iDMA per-block test.
# Run via ./run.sh, or: make SIM=verilator

SIM           ?= verilator
TOPLEVEL_LANG ?= verilog
TOPLEVEL      ?= idma_tb_top
MODULE         = test_idma

THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO     := $(abspath $(THIS_DIR)/../../..)

INC  := $(REPO)/hw/core-v-mini-mcu/include
IDMA := $(REPO)/hw/vendor/mosaic/idma
CC   := $(REPO)/hw/vendor/pulp_platform/common_cells
AXI  := $(REPO)/hw/vendor/pulp_platform/axi
RI   := $(REPO)/hw/vendor/pulp_platform/register_interface
OBI  := $(REPO)/hw/vendor/pulp_platform/obi
DMA  := $(REPO)/hw/vendor/xheep/dma/rtl
PRIM := $(REPO)/hw/vendor/lowrisc/opentitan/hw/ip/prim/rtl
TB   := $(REPO)/tb/idma

# Packages first (declaration order), then RTL + tb.
VERILOG_SOURCES = \
  $(CC)/src/cf_math_pkg.sv \
  $(AXI)/src/axi_pkg.sv \
  $(INC)/obi_pkg.sv \
  $(INC)/reg_pkg.sv \
  $(INC)/fifo_pkg.sv \
  $(INC)/addr_map_rule_pkg.sv \
  $(INC)/core_v_mini_mcu_pkg.sv \
  $(DMA)/dma_reg_pkg.sv \
  $(IDMA)/rtl/idma_reg32_3d_reg_pkg.sv \
  $(IDMA)/rtl/idma_pkg.sv \
  $(IDMA)/rtl/idma_reg_top.sv \
  $(IDMA)/idma_xheep_wrapper.sv \
  $(TB)/tb_idma_mem.sv \
  $(TB)/tb_idma_xbar_mem.sv \
  $(TB)/idma_tb_top.sv \
  $(TB)/idma_soc_tb_top.sv

COMPILE_ARGS += --timescale 1ns/1ps
COMPILE_ARGS += -Wno-WIDTH -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
COMPILE_ARGS += -Wno-UNDRIVEN -Wno-UNUSEDPARAM -Wno-DECLFILENAME -Wno-TIMESCALEMOD
COMPILE_ARGS += -Wno-PINMISSING -Wno-CASEINCOMPLETE -Wno-SYMRSVDWORD -Wno-GENUNNAMED
COMPILE_ARGS += -Wno-UNOPTFLAT -Wno-ASCRANGE -Wno-LATCH
COMPILE_ARGS += -Wno-UNSIGNED
COMPILE_ARGS += $(IDMA_TOP_PARAMS)
COMPILE_ARGS += -I$(CC)/include -I$(AXI)/include -I$(RI)/include -I$(INC)
COMPILE_ARGS += -I$(IDMA)/rtl/include -I$(OBI)/include -I$(PRIM)
COMPILE_ARGS += -y $(IDMA)/rtl -y $(IDMA)/rtl/backend -y $(IDMA)/rtl/midend
COMPILE_ARGS += -y $(IDMA)/rtl/frontend -y $(CC)/src -y $(AXI)/src -y $(RI)/src
COMPILE_ARGS += -y $(PRIM) -y $(INC) -y $(DMA) -y $(TB)

include $(shell cocotb-config --makefiles)/Makefile.sim
