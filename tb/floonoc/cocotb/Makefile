# cocotb + Verilator Makefile for the MOSAIC OBI<->AXI bridge / FlooNoC tests.
# Run via ./run.sh, or: make SIM=verilator [TOPLEVEL=tb_bridge_top]

SIM           ?= verilator
TOPLEVEL_LANG ?= verilog
TOPLEVEL      ?= tb_bridge_top
MODULE        ?= test_bridges

THIS_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO     := $(abspath $(THIS_DIR)/../../..)

INC  := $(REPO)/hw/core-v-mini-mcu/include
CC   := $(REPO)/hw/vendor/pulp_platform/common_cells
AXI  := $(REPO)/hw/vendor/pulp_platform/axi
AOB  := $(REPO)/hw/vendor/mosaic/axi_obi
TB   := $(REPO)/tb/floonoc

# Packages first (declaration order), then RTL + tb.
VERILOG_SOURCES = \
  $(CC)/src/cf_math_pkg.sv \
  $(AXI)/src/axi_pkg.sv \
  $(INC)/obi_pkg.sv \
  $(AOB)/xheep_obi_to_axi.sv \
  $(AOB)/xheep_axi_to_obi.sv \
  $(TB)/tb_bridge_top.sv

# Stage 2 additionally needs the FlooNoC library + the GENERATED fabric
# (mcu_gen with configs/mosaic_floonoc.yaml — run.sh stage2 handles it).
ifeq ($(TOPLEVEL),tb_noc_top)
FLOO := $(REPO)/hw/vendor/mosaic/floonoc
MOSAIC_GENERATED_ROOT ?= $(REPO)/build/mosaic/INVALID-MANIFEST-NOT-SUPPLIED/generated
FAB  := $(MOSAIC_GENERATED_ROOT)/hw/ip/floonoc_fabric
VERILOG_SOURCES += \
  $(FLOO)/hw/floo_pkg.sv \
  $(FAB)/floo_mosaic_noc_pkg.sv \
  $(AXI)/src/axi_demux_simple.sv \
  $(FAB)/floo_mosaic_noc.sv \
  $(TB)/tb_noc_top.sv
COMPILE_ARGS += -I$(FLOO)/hw/include
COMPILE_ARGS += -y $(FLOO)/hw
endif

COMPILE_ARGS += --timescale 1ns/1ps
COMPILE_ARGS += -Wno-WIDTH -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
COMPILE_ARGS += -Wno-UNDRIVEN -Wno-UNUSEDPARAM -Wno-DECLFILENAME -Wno-TIMESCALEMOD
COMPILE_ARGS += -Wno-PINMISSING -Wno-CASEINCOMPLETE -Wno-GENUNNAMED -Wno-UNOPTFLAT
COMPILE_ARGS += -Wno-ASCRANGE -Wno-UNSIGNED -Wno-PINCONNECTEMPTY
COMPILE_ARGS += -I$(CC)/include -I$(AXI)/include -I$(INC)
COMPILE_ARGS += -y $(CC)/src -y $(AXI)/src

include $(shell cocotb-config --makefiles)/Makefile.sim
