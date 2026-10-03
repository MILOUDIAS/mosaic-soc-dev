// Copyright 2026 MOSAIC-SoC Contributors
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

// Legacy fallback for invoking `make -C sw/firmware` without an isolated
// MOSAIC generation bundle. New flows must define
// MOSAIC_USE_BUILD_GENERATED_HEADERS and consume generated/sw/include.

#ifndef MOSAIC_LEGACY_CONFIG_H_
#define MOSAIC_LEGACY_CONFIG_H_

#include "mosaic_hw.h"

#define MOSAIC_NUM_HARTS 7u
#define MOSAIC_NUM_TITAN_HARTS 1u
#define MOSAIC_NUM_ATLAS_HARTS 2u
#define MOSAIC_NUM_NANO_HARTS 4u
#define MOSAIC_NUM_WORKER_HARTS 6u

#define MOSAIC_TITAN_HART_MASK 0x00000001u
#define MOSAIC_ATLAS_HART_MASK 0x00000006u
#define MOSAIC_NANO_HART_MASK 0x00000078u
#define MOSAIC_WORKER_HART_MASK 0x0000007Eu
#define MOSAIC_FIRST_TITAN_HART 0u
#define MOSAIC_FIRST_ATLAS_HART 1u
#define MOSAIC_FIRST_NANO_HART 3u

#define MOSAIC_TDU_ENABLED 1u
#define MOSAIC_TDU_TASK_QUEUE_DEPTH 8u
#define MOSAIC_SCHED_MODE TDU_SCHED_DYNAMIC
#define MOSAIC_SENTINEL_BASE SENTINEL_BASE
#define MOSAIC_RESULT_BASE 0x00003100u

#endif  // MOSAIC_LEGACY_CONFIG_H_
