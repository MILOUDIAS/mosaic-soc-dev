MAKEFILE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
REPO_ROOT    := $(realpath $(MAKEFILE_DIR)/../..)

TOP = mosaic_chip_top

PDK_ROOT ?= $(MAKEFILE_DIR)/gf180mcu
PDK      ?= gf180mcuD
PDK_TAG  ?= 1.8.0

AVAILABLE_SLOTS = mosaic
DEFAULT_SLOT    = mosaic
SLOT ?= $(DEFAULT_SLOT)
ifeq ($(SLOT),default)
    SLOT = $(DEFAULT_SLOT)
endif
ifeq ($(filter $(SLOT),$(AVAILABLE_SLOTS)),)
    $(error $(SLOT) not in AVAILABLE_SLOTS: $(AVAILABLE_SLOTS))
endif

# MOSAIC RTL generation remains a standalone development action.  Physical
# runs consume an explicit, hashed source bundle; they never trust a stale
# build/mosaic/design.v or the checked-in unbound adapter placeholder.
MOSAIC_CFG    ?= mosaic.yaml
PHYSICAL_BUNDLE ?=
PREFLIGHT       = python3 $(MAKEFILE_DIR)/scripts/preflight.py

LIBRELANE = librelane slots/slot_$(SLOT).yaml config.yaml \
            --pdk $(PDK) --pdk-root $(PDK_ROOT) --manual-pdk

# Classic flow = harden the SoC core (core_v_mini_mcu) as a macro, no pad ring.
LIBRELANE_CLASSIC = librelane config_classic.yaml \
            --pdk $(PDK) --pdk-root $(PDK_ROOT) --manual-pdk

.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  %-22s %s\n",$$1,$$2}'
.PHONY: help

clone-pdk: ## Clone the wafer-space GF180MCU PDK (matched to LibreLane 3.0.0)
	rm -rf $(MAKEFILE_DIR)/gf180mcu
	git clone https://github.com/wafer-space/gf180mcu.git $(MAKEFILE_DIR)/gf180mcu --depth 1 --branch $(PDK_TAG)
.PHONY: clone-pdk

mosaic-gen: ## Generate the MOSAIC SoC RTL from the config (repo-root target)
	$(MAKE) -C $(REPO_ROOT) mosaic-gen MOSAIC_CFG=$(MOSAIC_CFG)
.PHONY: mosaic-gen

preflight-chip: ## Validate a bound, hashed GF180 chip source bundle
	@test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }
	$(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode chip
.PHONY: preflight-chip

preflight-classic: ## Validate a hashed GF180 core source bundle
	@test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }
	$(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode classic
.PHONY: preflight-classic

flatten: preflight-classic ## Validate the supplied flattened RTL (does not create it)
	@echo "Flattened RTL is accepted only through PHYSICAL_BUNDLE."
.PHONY: flatten

harden: ## Full RTL→GDS signoff on GF180MCU (requires PHYSICAL_BUNDLE)
	@set -eu; test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }; eval "$$($(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode chip --emit-shell)"; $(LIBRELANE) --save-views-to $(MAKEFILE_DIR)/final
.PHONY: harden

harden-nodrc: ## harden but skip DRC/antenna (requires PHYSICAL_BUNDLE)
	@set -eu; test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }; eval "$$($(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode chip --emit-shell)"; $(LIBRELANE) --save-views-to $(MAKEFILE_DIR)/final --skip KLayout.Antenna --skip KLayout.DRC --skip Magic.DRC
.PHONY: harden-nodrc

classic: ## Harden the SoC core only (requires PHYSICAL_BUNDLE)
	@set -eu; test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }; eval "$$($(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode classic --emit-shell)"; $(LIBRELANE_CLASSIC) --save-views-to $(MAKEFILE_DIR)/final_classic
.PHONY: classic

classic-nodrc: ## Classic harden, skip DRC/antenna (requires PHYSICAL_BUNDLE)
	@set -eu; test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }; eval "$$($(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode classic --emit-shell)"; $(LIBRELANE_CLASSIC) --save-views-to $(MAKEFILE_DIR)/final_classic --skip KLayout.Antenna --skip KLayout.DRC --skip Magic.DRC
.PHONY: classic-nodrc

padring: ## Padring flow (requires the same bound PHYSICAL_BUNDLE)
	@set -eu; test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }; eval "$$($(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode chip --emit-shell)"; PDK_ROOT=$(PDK_ROOT) PDK=$(PDK) python3 scripts/padring.py slots/slot_$(SLOT).yaml config.yaml
.PHONY: padring

openroad: ## Open the last chip run in OpenROAD GUI (requires its bundle)
	@set -eu; test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }; eval "$$($(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode chip --emit-shell)"; $(LIBRELANE) --last-run --flow OpenInOpenROAD
.PHONY: openroad

klayout: ## Open the last chip run in KLayout GUI (requires its bundle)
	@set -eu; test -n "$(PHYSICAL_BUNDLE)" || { echo "ERROR: set PHYSICAL_BUNDLE=/path/to/bundle"; exit 2; }; eval "$$($(PREFLIGHT) --bundle "$(PHYSICAL_BUNDLE)" --mode chip --emit-shell)"; $(LIBRELANE) --last-run --flow OpenInKLayout
.PHONY: klayout

render-image: ## Render a PNG from the final GDS
	mkdir -p img/
	PDK_ROOT=$(PDK_ROOT) PDK=$(PDK) python3 scripts/lay2img.py final/gds/$(TOP).gds img/$(TOP).png --width 2048 --oversampling 4
.PHONY: render-image
