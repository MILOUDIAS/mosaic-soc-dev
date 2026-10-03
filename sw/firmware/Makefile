# Makefile — MOSAIC-SoC firmware build system.
#
# Builds the multi-core firmware (TITAN + ATLAS + NANO) and packs a deployable
# SPI-flash image. See DEPLOYMENT.md for the cold-boot contract.
#
# Toolchain: riscv64-unknown-elf-gcc (from system or oss-cad-suite)
# Usage:
#   make              — build all firmware
#   make titan        — build TITAN only
#   make clean        — remove build artifacts
#   make disasm       — generate disassembly listings
#
# The hex file (mosaic_fw.hex) is compatible with the tb/mosaic_soc/
# simulation testbench ($readmemh format).

# ── Toolchain ───────────────────────────────────────────────────────
# Override with: make RISCV_TC=/path/to/toolchain
RISCV_TC    ?= riscv64-unknown-elf
PYTHON      ?= python3
CC          = $(RISCV_TC)-gcc
OBJCOPY     = $(RISCV_TC)-objcopy
OBJDUMP     = $(RISCV_TC)-objdump
SIZE        = $(RISCV_TC)-size

# ── Architecture flags ──────────────────────────────────────────────
# All cores compiled as rv32i for maximum compatibility (serial cores
# can't execute compressed instructions).
ARCH        = -march=rv32i -mabi=ilp32

# An isolated MOSAIC generation bundle supplies the exact ISA/ABI for each
# core group. The production demo has one group per role and builds one ELF per
# boot-image slot, so RV32E TITAN objects never get ABI-merged with RV32I worker
# objects. Legacy standalone builds retain the historical common RV32I ELF.
MOSAIC_GENERATED_ROOT ?=
MOSAIC_GEN_CPPFLAGS =
MOSAIC_XIP_CPPFLAGS =
MOSAIC_FIRMWARE_CPPFLAGS ?=
TITAN_ARCH = $(ARCH)
ATLAS_ARCH = $(ARCH)
NANO_ARCH = $(ARCH)
ifneq ($(strip $(MOSAIC_GENERATED_ROOT)),)
include $(MOSAIC_GENERATED_ROOT)/sw/make/mosaic_isa.mk
PRODUCTION_DEMO_VALIDATION := $(shell $(PYTHON) \
    $(abspath ../../util/xheep_gen/software_gen.py) \
    --validate-production-demo $(MOSAIC_GENERATED_ROOT)/sw/boot_images.json \
    --isa-makefile $(MOSAIC_GENERATED_ROOT)/sw/make/mosaic_isa.mk 2>&1)
ifneq ($(strip $(PRODUCTION_DEMO_VALIDATION)),)
$(error $(PRODUCTION_DEMO_VALIDATION))
endif
MOSAIC_MULTI_IMAGE = 1
MOSAIC_GEN_CPPFLAGS = -DMOSAIC_USE_BUILD_GENERATED_HEADERS \
                      -I$(MOSAIC_GENERATED_ROOT)/sw/include
MOSAIC_XIP_CPPFLAGS = -DMOSAIC_FLASH_XIP
TITAN_GROUP = $(firstword $(MOSAIC_TITAN_GROUPS))
ATLAS_GROUP = $(firstword $(MOSAIC_ATLAS_GROUPS))
NANO_GROUP = $(firstword $(MOSAIC_NANO_GROUPS))
TITAN_ARCH = -march=$(MOSAIC_GROUP_$(TITAN_GROUP)_MARCH) \
             -mabi=$(MOSAIC_GROUP_$(TITAN_GROUP)_MABI)
ATLAS_ARCH = -march=$(MOSAIC_GROUP_$(ATLAS_GROUP)_MARCH) \
             -mabi=$(MOSAIC_GROUP_$(ATLAS_GROUP)_MABI)
NANO_ARCH = -march=$(MOSAIC_GROUP_$(NANO_GROUP)_MARCH) \
            -mabi=$(MOSAIC_GROUP_$(NANO_GROUP)_MABI)
TITAN_IMAGE = $(MOSAIC_GROUP_$(TITAN_GROUP)_IMAGE)
ATLAS_IMAGE = $(MOSAIC_GROUP_$(ATLAS_GROUP)_IMAGE)
NANO_IMAGE = $(MOSAIC_GROUP_$(NANO_GROUP)_IMAGE)
endif

# ── Compiler flags ──────────────────────────────────────────────────
CFLAGS      = -Os -g -Wall -Wextra \
              -ffreestanding -nostdlib -nostartfiles \
              -ffunction-sections -fdata-sections \
              -I$(ROOT)/sw/device/lib/base \
              $(MOSAIC_GEN_CPPFLAGS)

ASFLAGS     = -g -ffreestanding -nostdlib $(MOSAIC_GEN_CPPFLAGS)

LDFLAGS     = -T $(LINKSCRIPT) -nostdlib -nostartfiles \
              -Wl,-Map=$(BUILD)/firmware.map

# ── Directories ─────────────────────────────────────────────────────
ROOT        = $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
BUILD       = $(ROOT)/sw/firmware/build
LINKSCRIPT  = $(ROOT)/sw/firmware/mosaic_link.ld

# ── Source files ────────────────────────────────────────────────────
TITAN_C_SRC = $(ROOT)/sw/firmware/common/tdu.c \
              $(ROOT)/sw/firmware/common/cold_boot.c \
              $(ROOT)/sw/firmware/titan/titan_main.c
DEMO_C_SRC  = $(ROOT)/sw/firmware/common/tdu.c \
              $(ROOT)/sw/firmware/titan/titan_scheduling_demo.c
TITAN_A_SRC = $(ROOT)/sw/firmware/titan/start.S
ATLAS_A_SRC = $(ROOT)/sw/firmware/atlas/atlas_worker.S
NANO_A_SRC  = $(ROOT)/sw/firmware/nano/nano_worker.S

# ── Object files (flat names in build dir) ──────────────────────────
TITAN_OBJ   = $(BUILD)/start.o $(BUILD)/tdu.o $(BUILD)/titan_main.o
DEMO_OBJ    = $(BUILD)/start.o $(BUILD)/tdu.o $(BUILD)/titan_scheduling_demo.o
ATLAS_OBJ   = $(BUILD)/atlas_worker.o
NANO_OBJ    = $(BUILD)/nano_worker.o
ALL_OBJ     = $(TITAN_OBJ) $(ATLAS_OBJ) $(NANO_OBJ)
DEMO_ALL    = $(DEMO_OBJ) $(ATLAS_OBJ) $(NANO_OBJ)

# ── Outputs ─────────────────────────────────────────────────────────
ELF         = $(BUILD)/mosaic_fw.elf
HEX         = $(BUILD)/mosaic_fw.hex
DEMO_ELF    = $(BUILD)/mosaic_demo.elf
DEMO_HEX    = $(BUILD)/mosaic_demo.hex

ifeq ($(MOSAIC_MULTI_IMAGE),1)
TITAN_OBJ   = $(BUILD)/start_flash.o $(BUILD)/tdu.o $(BUILD)/cold_boot.o $(BUILD)/titan_main.o
TITAN_ELF   = $(BUILD)/image_$(TITAN_IMAGE)_titan.elf
ATLAS_ELF   = $(BUILD)/image_$(ATLAS_IMAGE)_atlas.elf
NANO_ELF    = $(BUILD)/image_$(NANO_IMAGE)_nano.elf
TITAN_HEX   = $(BUILD)/image_$(TITAN_IMAGE)_titan.hex
ATLAS_HEX   = $(BUILD)/image_$(ATLAS_IMAGE)_atlas.hex
NANO_HEX    = $(BUILD)/image_$(NANO_IMAGE)_nano.hex
TITAN_BIN   = $(BUILD)/image_$(TITAN_IMAGE)_titan.bin
ATLAS_BIN   = $(BUILD)/image_$(ATLAS_IMAGE)_atlas.bin
NANO_BIN    = $(BUILD)/image_$(NANO_IMAGE)_nano.bin
FLASH_BIN   = $(BUILD)/mosaic_flash.bin
FLASH_HEX   = $(BUILD)/mosaic_flash.hex
FLASH_JSON  = $(BUILD)/mosaic_flash.json
DEMO_TITAN_ELF = $(BUILD)/image_$(TITAN_IMAGE)_demo.elf
DEMO_TITAN_HEX = $(BUILD)/image_$(TITAN_IMAGE)_demo.hex
ELF         = $(TITAN_ELF)
DEMO_ELF    = $(DEMO_TITAN_ELF)
endif

# ── Targets ─────────────────────────────────────────────────────────
.PHONY: all demo clean disasm size

all: $(HEX)
	@echo "=== Firmware build complete ==="
	@echo "  ELF: $(ELF)"
	@echo "  HEX: $(HEX)"
	@$(SIZE) $(ELF)

demo: $(DEMO_HEX)
	@echo "=== Scheduling demo build complete ==="
	@echo "  ELF: $(DEMO_ELF)"
	@echo "  HEX: $(DEMO_HEX)"
	@$(SIZE) $(DEMO_ELF)

clean:
	rm -rf $(BUILD)

disasm: $(ELF)
	$(OBJDUMP) -d -S $< > $(BUILD)/firmware.dis

size: $(ELF)
	$(SIZE) -A $<

# ── Build directory ─────────────────────────────────────────────────
$(BUILD):
	mkdir -p $@

# ── Compile C sources (TITAN) ──────────────────────────────────────
$(BUILD)/tdu.o: $(ROOT)/sw/firmware/common/tdu.c | $(BUILD)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) $(TITAN_ARCH) -c $< -o $@

$(BUILD)/cold_boot.o: $(ROOT)/sw/firmware/common/cold_boot.c | $(BUILD)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) $(MOSAIC_XIP_CPPFLAGS) $(MOSAIC_FIRMWARE_CPPFLAGS) $(TITAN_ARCH) -I$(ROOT)/sw/firmware/common -c $< -o $@

$(BUILD)/titan_main.o: $(ROOT)/sw/firmware/titan/titan_main.c | $(BUILD)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) $(MOSAIC_XIP_CPPFLAGS) $(MOSAIC_FIRMWARE_CPPFLAGS) $(TITAN_ARCH) -I$(ROOT)/sw/firmware/common -c $< -o $@

$(BUILD)/titan_scheduling_demo.o: $(ROOT)/sw/firmware/titan/titan_scheduling_demo.c | $(BUILD)
	@echo "  CC    $<"
	$(CC) $(CFLAGS) $(TITAN_ARCH) -I$(ROOT)/sw/firmware/common -c $< -o $@

# ── Compile assembly sources ───────────────────────────────────────
$(BUILD)/start.o: $(ROOT)/sw/firmware/titan/start.S | $(BUILD)
	@echo "  AS    $<"
	$(CC) $(ASFLAGS) $(TITAN_ARCH) -c $< -o $@

$(BUILD)/start_flash.o: $(ROOT)/sw/firmware/titan/start.S | $(BUILD)
	@echo "  AS    $< (SPI flash XIP)"
	$(CC) $(ASFLAGS) $(TITAN_ARCH) -DMOSAIC_FLASH_XIP -c $< -o $@

$(BUILD)/atlas_worker.o: $(ROOT)/sw/firmware/atlas/atlas_worker.S | $(BUILD)
	@echo "  AS    $<"
	$(CC) $(ASFLAGS) $(ATLAS_ARCH) -c $< -o $@

$(BUILD)/nano_worker.o: $(ROOT)/sw/firmware/nano/nano_worker.S | $(BUILD)
	@echo "  AS    $<"
	$(CC) $(ASFLAGS) $(NANO_ARCH) -c $< -o $@

# ── Link multi-core ELF ────────────────────────────────────────────
ifeq ($(MOSAIC_MULTI_IMAGE),1)
$(TITAN_ELF): $(TITAN_OBJ) $(MOSAIC_GENERATED_ROOT)/sw/linker/titan_flash.ld | $(BUILD)
	@echo "  LD    $@"
	$(CC) $(TITAN_ARCH) -nostdlib -nostartfiles \
		-T $(MOSAIC_GENERATED_ROOT)/sw/linker/titan_flash.ld \
		-Wl,-Map=$(BUILD)/image_$(TITAN_IMAGE)_titan.map $(TITAN_OBJ) -o $@

$(ATLAS_ELF): $(ATLAS_OBJ) $(MOSAIC_GENERATED_ROOT)/sw/linker/image_$(ATLAS_IMAGE).ld | $(BUILD)
	@echo "  LD    $@"
	$(CC) $(ATLAS_ARCH) -nostdlib -nostartfiles \
		-T $(MOSAIC_GENERATED_ROOT)/sw/linker/image_$(ATLAS_IMAGE).ld $(ATLAS_OBJ) -o $@

$(NANO_ELF): $(NANO_OBJ) $(MOSAIC_GENERATED_ROOT)/sw/linker/image_$(NANO_IMAGE).ld | $(BUILD)
	@echo "  LD    $@"
	$(CC) $(NANO_ARCH) -nostdlib -nostartfiles \
		-T $(MOSAIC_GENERATED_ROOT)/sw/linker/image_$(NANO_IMAGE).ld $(NANO_OBJ) -o $@

$(DEMO_TITAN_ELF): $(DEMO_OBJ) $(MOSAIC_GENERATED_ROOT)/sw/linker/image_$(TITAN_IMAGE).ld | $(BUILD)
	@echo "  LD    $@"
	$(CC) $(TITAN_ARCH) -nostdlib -nostartfiles \
		-T $(MOSAIC_GENERATED_ROOT)/sw/linker/image_$(TITAN_IMAGE).ld \
		-Wl,-Map=$(BUILD)/image_$(TITAN_IMAGE)_demo.map $(DEMO_OBJ) -o $@

$(TITAN_HEX): $(TITAN_ELF)
	$(OBJCOPY) -O verilog $< $@

$(ATLAS_HEX): $(ATLAS_ELF)
	$(OBJCOPY) -O verilog $< $@

$(NANO_HEX): $(NANO_ELF)
	$(OBJCOPY) -O verilog $< $@

$(TITAN_BIN): $(TITAN_ELF)
	$(OBJCOPY) -O binary $< $@

$(ATLAS_BIN): $(ATLAS_ELF)
	$(OBJCOPY) -O binary $< $@

$(NANO_BIN): $(NANO_ELF)
	$(OBJCOPY) -O binary $< $@

$(FLASH_BIN): $(TITAN_BIN) $(ATLAS_BIN) $(NANO_BIN) $(MOSAIC_GENERATED_ROOT)/sw/boot_images.json
	@echo "  PACK  $@"
	$(PYTHON) $(ROOT)/util/xheep_gen/pack_flash.py \
		--manifest $(MOSAIC_GENERATED_ROOT)/sw/boot_images.json \
		--image $(TITAN_IMAGE)=$(TITAN_BIN) \
		--image $(ATLAS_IMAGE)=$(ATLAS_BIN) \
		--image $(NANO_IMAGE)=$(NANO_BIN) --output $@

$(FLASH_HEX) $(FLASH_JSON): $(FLASH_BIN)
	@test -f $@

$(DEMO_TITAN_HEX): $(DEMO_TITAN_ELF)
	$(OBJCOPY) -O verilog $< $@

$(HEX): $(FLASH_BIN) $(FLASH_HEX) $(FLASH_JSON)
	@echo "  HEX   $@"
	@cp $(FLASH_HEX) $@

$(DEMO_HEX): $(DEMO_TITAN_HEX) $(ATLAS_HEX) $(NANO_HEX)
	@echo "  HEX   $@"
	@{ cat $(DEMO_TITAN_HEX); cat $(ATLAS_HEX); cat $(NANO_HEX); } > $@

else
$(ELF): $(ALL_OBJ) $(LINKSCRIPT) | $(BUILD)
	@echo "  LD    $@"
	$(CC) $(ARCH) $(LDFLAGS) $(ALL_OBJ) -o $@

# ── Link scheduling demo ELF ────────────────────────────────────────
$(DEMO_ELF): $(DEMO_ALL) $(LINKSCRIPT) | $(BUILD)
	@echo "  LD    $@"
	$(CC) $(ARCH) $(LDFLAGS) $(DEMO_ALL) -o $@

# ── Convert to Verilog hex ──────────────────────────────────────────
$(HEX): $(ELF)
	@echo "  HEX   $@"
	$(OBJCOPY) -O verilog $< $@

$(DEMO_HEX): $(DEMO_ELF)
	@echo "  HEX   $@"
	$(OBJCOPY) -O verilog $< $@
endif
