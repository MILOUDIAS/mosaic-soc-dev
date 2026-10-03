# cocotb + Verilator Makefile for the SoC-level TDU test.
# Run via ./run.sh, or: make SIM=verilator SUB=1
#   SUB=1  tap subtracts TDU_START_ADDRESS (correct: TDU sees offsets)
#   SUB=0  tap passes the full SoC address (reproduces the original bug)

SIM           ?= verilator
TOPLEVEL_LANG ?= verilog
TOPLEVEL       = tdu_soc_tb_top
MODULE         = test_tdu_soc
SUB           ?= 1

THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO     := $(abspath $(THIS_DIR)/../../../..)

INC := $(REPO)/hw/core-v-mini-mcu/include
TDU := $(REPO)/hw/tdu/rtl
CC  := $(REPO)/hw/vendor/pulp_platform/common_cells

VERILOG_SOURCES = \
  $(INC)/obi_pkg.sv \
  $(INC)/reg_pkg.sv \
  $(INC)/fifo_pkg.sv \
  $(INC)/addr_map_rule_pkg.sv \
  $(INC)/power_manager_pkg.sv \
  $(INC)/core_v_mini_mcu_pkg.sv \
  $(TDU)/tdu_pkg.sv \
  $(TDU)/tdu.sv \
  $(REPO)/tb/tdu/soc/tdu_soc_tb_top.sv

COMPILE_ARGS += --timescale 1ns/1ps -GTAP_SUBTRACT_BASE=$(SUB)
COMPILE_ARGS += -Wno-WIDTH -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
COMPILE_ARGS += -Wno-UNDRIVEN -Wno-UNUSEDPARAM -Wno-DECLFILENAME -Wno-TIMESCALEMOD
COMPILE_ARGS += -Wno-PINMISSING -Wno-CASEINCOMPLETE -Wno-SYMRSVDWORD -Wno-GENUNNAMED
COMPILE_ARGS += -I$(CC)/include -I$(INC)
COMPILE_ARGS += -y $(CC)/src -y $(INC) -y $(TDU)

include $(shell cocotb-config --makefiles)/Makefile.sim
