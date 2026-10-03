// Copyright 2026 MOSAIC-SoC Contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

#include "cold_boot.h"

#include <mosaic_memory_map.h>
#include <mosaic_deployment.h>

#define MOSAIC_DEPLOY_MAX_ENTRIES 15u

typedef struct {
    uint32_t magic, version, header_size, entry_count;
    uint32_t flags, topology_crc32, entries_crc32, titan_offset;
} deploy_header_t;

typedef struct {
    uint32_t destination, size, flash_offset, crc32;
} deploy_entry_t;

static uint32_t crc32_bytes(const volatile uint8_t *data, uint32_t size) {
    uint32_t crc = 0xFFFFFFFFu;
    for (uint32_t index = 0; index < size; ++index) {
        crc ^= data[index];
        for (uint32_t bit = 0; bit < 8u; ++bit) {
            const uint32_t mask = 0u - (crc & 1u);
            crc = (crc >> 1) ^ (0xEDB88320u & mask);
        }
    }
    return ~crc;
}

uint32_t mosaic_cold_boot_load_workers(void) {
    const volatile deploy_header_t *header =
        (const volatile deploy_header_t *)(uintptr_t)MOSAIC_FLASH_BASE;
    if (header->magic != MOSAIC_DEPLOYMENT_MAGIC) return 0xCB000001u;
    if (header->version != MOSAIC_DEPLOYMENT_VERSION || header->flags != 1u) {
        return 0xCB000002u;
    }
    if (header->entry_count > MOSAIC_DEPLOY_MAX_ENTRIES) return 0xCB000003u;
    const uint32_t expected_size = (uint32_t)sizeof(*header) +
        header->entry_count * (uint32_t)sizeof(deploy_entry_t);
    if (header->header_size != expected_size || expected_size > header->titan_offset) {
        return 0xCB000004u;
    }
    if (header->titan_offset != MOSAIC_DEPLOYMENT_TITAN_FLASH_OFFSET ||
        header->topology_crc32 != MOSAIC_DEPLOYMENT_TOPOLOGY_CRC32) {
        return 0xCB000006u;
    }
    const volatile deploy_entry_t *entries =
        (const volatile deploy_entry_t *)(uintptr_t)(MOSAIC_FLASH_BASE + sizeof(*header));
    if (crc32_bytes((const volatile uint8_t *)entries,
                    header->entry_count * (uint32_t)sizeof(*entries)) !=
        header->entries_crc32) return 0xCB000005u;

    for (uint32_t index = 0; index < header->entry_count; ++index) {
        /* Read fields separately: assigning a volatile struct may make GCC
         * emit a libc memcpy, which is unavailable in this freestanding ROM. */
        const uint32_t destination_address = entries[index].destination;
        const uint32_t size = entries[index].size;
        const uint32_t flash_offset = entries[index].flash_offset;
        const uint32_t expected_crc32 = entries[index].crc32;
        if (size == 0u || destination_address > MOSAIC_SRAM_END ||
            size > MOSAIC_SRAM_END - destination_address ||
            flash_offset >= MOSAIC_FLASH_SIZE ||
            size > MOSAIC_FLASH_SIZE - flash_offset) {
            return 0xCB001000u | index;
        }
        volatile uint8_t *destination =
            (volatile uint8_t *)(uintptr_t)destination_address;
        const volatile uint8_t *source = (const volatile uint8_t *)(uintptr_t)
            (MOSAIC_FLASH_BASE + flash_offset);
        uint32_t byte = 0u;
        if (((destination_address | flash_offset) & 3u) == 0u) {
            volatile uint32_t *destination_word =
                (volatile uint32_t *)(uintptr_t)destination_address;
            const volatile uint32_t *source_word = (const volatile uint32_t *)(uintptr_t)
                (MOSAIC_FLASH_BASE + flash_offset);
            for (; byte + 4u <= size; byte += 4u) {
                destination_word[byte >> 2] = source_word[byte >> 2];
            }
        }
        for (; byte < size; ++byte) destination[byte] = source[byte];
        if (crc32_bytes(destination, size) != expected_crc32) {
            return 0xCB002000u | index;
        }
    }
    return 0u;
}
