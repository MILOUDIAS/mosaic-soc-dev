# Copyright EPFL contributors.
# Copyright 2026 MOSAIC-SoC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

MAKE	= make

# Get the absolute path
mkfile_path := $(shell dirname "$(realpath $(firstword $(MAKEFILE_LIST)))")
$(info $$You are executing from: $(mkfile_path))

# Root of the project that vendors this repository (itself by default)
HEEP_EXTERNAL_ROOT 	?= .

# Include the self-documenting tool
export FILE_FOR_HELP=$(mkfile_path)/Makefile

help:
	${mkfile_path}/util/MakefileHelp

# Setup to autogenerate python virtual environment
VENVDIR?=$(WORKDIR)/.venv
REQUIREMENTS_TXT ?= util/python-requirements.txt
include Makefile.venv

# FUSESOC and Python values (default)
ifndef CONDA_DEFAULT_ENV
$(info USING VENV)
FUSESOC 	= $(PWD)/$(VENV)/fusesoc
PYTHON  	= $(PWD)/$(VENV)/python
else
$(info USING MINICONDA $(CONDA_DEFAULT_ENV))
FUSESOC 	:= $(shell which fusesoc)
PYTHON  	:= $(shell which python)
endif

# FuseSoC cores root
FUSESOC_CORES_ROOT	?= $(HEEP_EXTERNAL_ROOT)

# RegTool and StructGen path
REGTOOL 			?= $(mkfile_path)/hw/vendor/pulp_platform/register_interface/vendor/lowrisc_opentitan/util/regtool.py
PERIPH_STRUCTS_GEN 	?= $(mkfile_path)/util/periph_structs_gen/periph_structs_gen.py
TEMPLATE_FILE 		?= $(mkfile_path)/util/periph_structs_gen/periph_structs.tpl

# Build directories
BUILD_DIR         = build
FUSESOC_BUILD_DIR = $(shell find $(BUILD_DIR) -maxdepth 1 -type d -name 'openhwgroup.org_systems_core-v-mini-mcu_*' 2>/dev/null | sort -V | head -n 1)
VERILATOR_DIR     = $(FUSESOC_BUILD_DIR)/sim-verilator

# Legacy single-core configuration files (make mcu-gen)
X_HEEP_CFG  ?= configs/general.hjson
PADS_CFG ?= configs/pad_cfg.py
PYTHON_X_HEEP_CFG ?=

# MOSAIC-SoC configuration (single declarative YAML driving the multi-core flow)
MOSAIC_CFG ?= mosaic.yaml
BASE_CFG   ?= configs/general.hjson
MOSAIC_OUTPUT_ROOT ?= build/mosaic
# Optional executable/command invoked after template rendering and before the
# FuseSoC overlay is staged. It receives MOSAIC_MANIFEST and
# MOSAIC_GENERATED_ROOT in its environment and may register generated platform
# RTL with `build_manifest.py register` (for example, per-hart PLIC reg RTL).
MOSAIC_PLATFORM_GENERATOR ?=

# Template files to render
# ./.claude/* holds agent worktrees: whole repository copies whose templates
# would otherwise be rendered too (and overflow the 128 KiB argument limit).
MCU_GEN_TEMPLATES = $(shell find . \
  \( -path './build/*' -o \
     -path './.claude/*' -o \
     -path './hw/vendor/*' ! -path './hw/vendor/xheep' ! -path './hw/vendor/xheep/*' -o \
     -path './util/*' -o \
     -path './test/*' -o \
     -path './refs/*' \) -prune -o \
  -name '*.tpl' -print)
MCU_GEN_OUTPUTS = $(patsubst %.tpl,%, $(MCU_GEN_TEMPLATES))

# Optionally, additional external template files can be provided to mcu-gen
EXTERNAL_MCU_GEN_TEMPLATES ?= 
EXTERNAL_MCU_GEN_OUTPUTS = $(patsubst %.tpl,%, $(EXTERNAL_MCU_GEN_TEMPLATES))

# RISC-V compiler prefix. Set RISCV_XHEEP to the toolchain root (the directory
# containing bin/); `nix develop .#sim` does this. The glob is restricted to
# bare-metal (elf) gcc so an unset variable cannot pick the host compiler.
COMPILER_PREFIX ?= $(shell basename $$(ls $(RISCV_XHEEP)/bin/riscv*elf-gcc 2>/dev/null | head -1) | sed 's/elf-gcc$$//')

# Additional simulation arguments, e.g. MAX_SIM_TIME=<clock cycles>
SIM_ARGS += $(if $(MAX_SIM_TIME),+max_sim_time=$(MAX_SIM_TIME))

# Vendored IPs
VENDOR_FILES	:= $(shell find hw/vendor util -maxdepth 2 -type f -name "*.vendor.hjson" -print)
VENDOR_LOCKS	:= $(subst .vendor.hjson,.lock.hjson,$(VENDOR_FILES))

# Export variables to sub-makefiles
export

## @section Generation

## Legacy single-core generation, inherited from X-HEEP. Renders the templates
## in place and runs the FuseSoC register generators.
## @param X_HEEP_CFG=[configs/general.hjson(default),<path-to-config-file>]
## @param PYTHON_X_HEEP_CFG=[<path-to-python-config-file>]
mcu-gen:
	$(PYTHON) util/mosaic_gen/mcu_gen.py --config $(X_HEEP_CFG) --python_config $(PYTHON_X_HEEP_CFG) --pads_cfg $(PADS_CFG) --outtpl "$(MCU_GEN_TEMPLATES)" --externaltpl "$(EXTERNAL_MCU_GEN_TEMPLATES)" --cpu $(CPU) --bus $(BUS) --memorybanks $(MEMORY_BANKS) --memorybanks_il $(MEMORY_BANKS_IL)

	@echo "### MCU-GEN completed! Running FuseSoC register generators..."	
	# NOTE: use the refs-excluding cores-root helper. A bare `--cores-root .`
	# makes FuseSoC recurse into refs/ and crash on the 0-byte test fixture
	# refs/IP_Tools/fusesoc/tests/capi2_cores/misc/empty.core. The helper builds
	# a temporary cores-root with only the project trees (hw tb util configs sw)
	# plus the root .core files, then runs the identical `--setup`.
	bash scripts/fusesoc-setup.sh


## MOSAIC-SoC: generate RTL from a single mosaic.yaml config file.
## Drives the entire multi-core SoC generation flow: core selection/counts,
## memory, bus fabric, scheduler (TDU) and peripherals.
## @param MOSAIC_CFG=[mosaic.yaml(default),<path-to-mosaic-config>]
## @param BASE_CFG=[configs/general.hjson(default),<base-xheep-hjson-for-peripherals>]
## @param PADS_CFG=[configs/pad_cfg.py(default),<pad-config>]
mosaic-gen:
	$(PYTHON) util/mosaic_gen/mcu_gen.py --mosaic_config $(MOSAIC_CFG) --base_config $(BASE_CFG) --pads_cfg $(PADS_CFG) --output-root $(MOSAIC_OUTPUT_ROOT) --outtpl "$(MCU_GEN_TEMPLATES)" --externaltpl "$(EXTERNAL_MCU_GEN_TEMPLATES)"
	@set -e; \
		manifest="$$($(PYTHON) util/mosaic_gen/build_manifest.py locate \
			--config "$(MOSAIC_CFG)" --base-config "$(BASE_CFG)" \
			--pads-cfg "$(PADS_CFG)" --repo-root "$(mkfile_path)" \
			--output-root "$(MOSAIC_OUTPUT_ROOT)")"; \
		echo "### MOSAIC-GEN completed! Running FuseSoC register generators..."; \
		if [ -n "$(strip $(MOSAIC_PLATFORM_GENERATOR))" ]; then \
			MOSAIC_MANIFEST="$$manifest" \
			MOSAIC_GENERATED_ROOT="$$($(PYTHON) -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_root"])' "$$manifest")" \
			$(MOSAIC_PLATFORM_GENERATOR); \
		fi; \
		bash scripts/fusesoc-setup.sh --manifest "$$manifest"; \
		echo "### MOSAIC manifest: $$manifest"

## Display mcu_gen.py help
mcu-gen-help:
	$(PYTHON) util/mosaic_gen/mcu_gen.py -h

## Runs verible formatting
verible: | .check-verible
	util/format-verible;

## Runs black formatting for python files
format-python:
	$(PYTHON) -m black util/mosaic_gen
	$(PYTHON) -m black util/periph_structs_gen
	$(PYTHON) -m black util/waiver-gen.py
	$(PYTHON) -m black test/test_mosaic_gen
	$(PYTHON) -m black configs

## @section Simulation

## Builds the Verilator model of the legacy single-core system
verilator-build: | .check-verilator
	$(FUSESOC) --cores-root $(FUSESOC_CORES_ROOT) run --no-export --target=sim --tool=verilator $(FUSESOC_FLAGS) --build openhwgroup.org:systems:core-v-mini-mcu $(FUSESOC_PARAM) 2>&1 | tee buildsim.log

## Runs the Verilator model built by `verilator-build`.
## @param FIRMWARE=<path-to-hex-file>
verilator-run:
	$(FUSESOC) --cores-root $(FUSESOC_CORES_ROOT) run --no-export --target=sim --tool=verilator $(FUSESOC_FLAGS) --run openhwgroup.org:systems:core-v-mini-mcu $(FUSESOC_PARAM) \
		--run_options="+firmware=$(FIRMWARE) $(SIM_ARGS)"

## Opens gtkwave to view the waveform generated by the last verilator simulation
verilator-waves: .check-gtkwave
	gtkwave $(VERILATOR_DIR)/waveform.fst

## @section Testing

## Runs the Python test suite
.PHONY: test
test:
	$(PYTHON) -m pytest test/test_mosaic_gen -q

## @section Vendored IPs
## Update the vendored IPs based on the .vendor.hjson description files
.PHONY: vendor-update
vendor-update: $(VENDOR_LOCKS)
	python3 util/check-vendor.py

$(VENDOR_LOCKS): %.lock.hjson: %.vendor.hjson util/vendor.py
	@echo "### Updating vendored IP '$(notdir $*)'..."
	python3 util/vendor.py -vU $<

.PHONY: vendor-clean
vendor-clean:
	$(RM) $(VENDOR_LOCKS)

## @section Cleaning commands

## Remove the build folders and the rendered templates
.PHONY: clean
clean:
	$(RM) -r $(BUILD_DIR)
	$(RM) $(MCU_GEN_OUTPUTS) $(EXTERNAL_MCU_GEN_OUTPUTS)
	find . -type f -name "*_reg_gen.cache" -delete

## @section Utilities
# Check if a program is available in PATH
define CHECK_PROGRAM
.PHONY: .check-$(1)
.check-$(1):
	@command -v $(2) >/dev/null 2>&1 || { \
		printf "### ERROR: '%s' is not in PATH.\\n" "$(2)" >&2; \
		exit 1; \
	}
endef
$(eval $(call CHECK_PROGRAM,gtkwave,gtkwave))
$(eval $(call CHECK_PROGRAM,verible,verible-verilog-format))
$(eval $(call CHECK_PROGRAM,verilator,verilator))
