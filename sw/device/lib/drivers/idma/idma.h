// Copyright MOSAIC-SoC
// SPDX-License-Identifier: Apache-2.0
#ifndef MOSAIC_SW_DEVICE_LIB_DRIVERS_IDMA_IDMA_H_
#define MOSAIC_SW_DEVICE_LIB_DRIVERS_IDMA_IDMA_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "sw/device/lib/base/mmio.h"

#ifdef __cplusplus
extern "C" {
#endif

enum { kIdmaMaxStreams = 16 };

typedef enum idma_result {
  kIdmaOk = 0,
  kIdmaBadArgument,
  kIdmaNotOwner,
  kIdmaBusy,
  kIdmaTimeout,
} idma_result_t;

typedef struct idma {
  mmio_region_t base;
  uint8_t num_streams;
} idma_t;

typedef struct idma_descriptor {
  uintptr_t src_addr;
  uintptr_t dst_addr;
  uint32_t length;
  uint32_t src_stride_2;
  uint32_t dst_stride_2;
  uint32_t reps_2;
  uint32_t src_stride_3;
  uint32_t dst_stride_3;
  uint32_t reps_3;
  uint8_t dimensions;
} idma_descriptor_t;

idma_result_t idma_init(idma_t *idma, uintptr_t base_addr,
                        uint8_t num_streams);

// Tokens must be nonzero and unique among participating harts. Claim is an
// atomic hardware compare-empty operation and does not require ISA atomics.
bool idma_try_claim(const idma_t *idma, uint8_t stream, uint32_t owner_token);
idma_result_t idma_release(const idma_t *idma, uint8_t stream,
                           uint32_t owner_token);
uint32_t idma_owner(const idma_t *idma, uint8_t stream);

// Programs and launches a 1D/2D/3D OBI-to-OBI transfer on an owned stream.
idma_result_t idma_submit(const idma_t *idma, uint8_t stream,
                          uint32_t owner_token,
                          const idma_descriptor_t *descriptor,
                          uint32_t *transaction_id);

bool idma_is_busy(const idma_t *idma, uint8_t stream);
uint32_t idma_done_id(const idma_t *idma, uint8_t stream);
idma_result_t idma_wait(const idma_t *idma, uint8_t stream,
                        uint32_t transaction_id, uint32_t poll_limit);

#ifdef __cplusplus
}
#endif
#endif  // MOSAIC_SW_DEVICE_LIB_DRIVERS_IDMA_IDMA_H_
