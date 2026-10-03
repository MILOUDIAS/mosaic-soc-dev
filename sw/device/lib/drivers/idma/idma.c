// Copyright MOSAIC-SoC
// SPDX-License-Identifier: Apache-2.0
#include "sw/device/lib/drivers/idma/idma.h"

#include "sw/device/lib/drivers/idma/idma_regs.h"

static bool valid_stream(const idma_t *idma, uint8_t stream) {
  return idma != NULL && stream < idma->num_streams;
}

static ptrdiff_t stream_reg(uint8_t stream, uint32_t offset) {
  return (ptrdiff_t)((uint32_t)stream * IDMA_STREAM_STRIDE + offset);
}

static ptrdiff_t indexed_reg(uint8_t bank, uint32_t base, uint8_t stream) {
  return stream_reg(bank, base + (uint32_t)stream * sizeof(uint32_t));
}

idma_result_t idma_init(idma_t *idma, uintptr_t base_addr,
                        uint8_t num_streams) {
  if (idma == NULL || num_streams == 0 || num_streams > kIdmaMaxStreams) {
    return kIdmaBadArgument;
  }
  idma->base = mmio_region_from_addr(base_addr);
  idma->num_streams = num_streams;
  return kIdmaOk;
}

uint32_t idma_owner(const idma_t *idma, uint8_t stream) {
  if (!valid_stream(idma, stream)) {
    return 0;
  }
  return mmio_region_read32(idma->base, stream_reg(stream, IDMA_OWNER_REG_OFFSET));
}

bool idma_try_claim(const idma_t *idma, uint8_t stream, uint32_t owner_token) {
  if (!valid_stream(idma, stream) || owner_token == 0) {
    return false;
  }
  mmio_region_write32(idma->base,
                      stream_reg(stream, IDMA_OWNER_CLAIM_REG_OFFSET),
                      owner_token);
  return idma_owner(idma, stream) == owner_token;
}

idma_result_t idma_release(const idma_t *idma, uint8_t stream,
                           uint32_t owner_token) {
  if (!valid_stream(idma, stream) || owner_token == 0) {
    return kIdmaBadArgument;
  }
  if (idma_owner(idma, stream) != owner_token) {
    return kIdmaNotOwner;
  }
  if (idma_is_busy(idma, stream)) {
    return kIdmaBusy;
  }
  mmio_region_write32(idma->base,
                      stream_reg(stream, IDMA_OWNER_RELEASE_REG_OFFSET),
                      owner_token);
  return idma_owner(idma, stream) == 0 ? kIdmaOk : kIdmaNotOwner;
}

bool idma_is_busy(const idma_t *idma, uint8_t stream) {
  if (!valid_stream(idma, stream)) {
    return false;
  }
  return (mmio_region_read32(
              idma->base,
              indexed_reg(stream, IDMA_STATUS_BASE_REG_OFFSET, stream)) &
          IDMA_STATUS_BUSY_MASK) != 0;
}

uint32_t idma_done_id(const idma_t *idma, uint8_t stream) {
  if (!valid_stream(idma, stream)) {
    return 0;
  }
  return mmio_region_read32(
      idma->base, indexed_reg(stream, IDMA_DONE_ID_BASE_REG_OFFSET, stream));
}

idma_result_t idma_submit(const idma_t *idma, uint8_t stream,
                          uint32_t owner_token,
                          const idma_descriptor_t *descriptor,
                          uint32_t *transaction_id) {
  if (!valid_stream(idma, stream) || descriptor == NULL || owner_token == 0 ||
      descriptor->length == 0 || descriptor->dimensions < 1 ||
      descriptor->dimensions > 3 ||
      (descriptor->dimensions >= 2 && descriptor->reps_2 == 0) ||
      (descriptor->dimensions == 3 && descriptor->reps_3 == 0)) {
    return kIdmaBadArgument;
  }
  if (idma_owner(idma, stream) != owner_token) {
    return kIdmaNotOwner;
  }
  if (idma_is_busy(idma, stream)) {
    return kIdmaBusy;
  }

  const uint32_t conf =
      ((uint32_t)(descriptor->dimensions - 1) << IDMA_CONF_ENABLE_ND_OFFSET) |
      (IDMA_PROTOCOL_OBI << IDMA_CONF_SRC_PROTOCOL_OFFSET) |
      (IDMA_PROTOCOL_OBI << IDMA_CONF_DST_PROTOCOL_OFFSET);
  mmio_region_write32(idma->base, stream_reg(stream, IDMA_CONF_REG_OFFSET), conf);
  mmio_region_write32(idma->base, stream_reg(stream, IDMA_DST_ADDR_REG_OFFSET),
                      (uint32_t)descriptor->dst_addr);
  mmio_region_write32(idma->base, stream_reg(stream, IDMA_SRC_ADDR_REG_OFFSET),
                      (uint32_t)descriptor->src_addr);
  mmio_region_write32(idma->base, stream_reg(stream, IDMA_LENGTH_REG_OFFSET),
                      descriptor->length);
  mmio_region_write32(idma->base,
                      stream_reg(stream, IDMA_DST_STRIDE_2_REG_OFFSET),
                      descriptor->dst_stride_2);
  mmio_region_write32(idma->base,
                      stream_reg(stream, IDMA_SRC_STRIDE_2_REG_OFFSET),
                      descriptor->src_stride_2);
  mmio_region_write32(idma->base, stream_reg(stream, IDMA_REPS_2_REG_OFFSET),
                      descriptor->dimensions >= 2 ? descriptor->reps_2 : 0);
  mmio_region_write32(idma->base,
                      stream_reg(stream, IDMA_DST_STRIDE_3_REG_OFFSET),
                      descriptor->dst_stride_3);
  mmio_region_write32(idma->base,
                      stream_reg(stream, IDMA_SRC_STRIDE_3_REG_OFFSET),
                      descriptor->src_stride_3);
  mmio_region_write32(idma->base, stream_reg(stream, IDMA_REPS_3_REG_OFFSET),
                      descriptor->dimensions == 3 ? descriptor->reps_3 : 1);

  const uint32_t issued = mmio_region_read32(
      idma->base, indexed_reg(stream, IDMA_NEXT_ID_BASE_REG_OFFSET, stream));
  if (transaction_id != NULL) {
    *transaction_id = issued;
  }
  return kIdmaOk;
}

idma_result_t idma_wait(const idma_t *idma, uint8_t stream,
                        uint32_t transaction_id, uint32_t poll_limit) {
  if (!valid_stream(idma, stream) || poll_limit == 0) {
    return kIdmaBadArgument;
  }
  for (uint32_t i = 0; i < poll_limit; ++i) {
    if (idma_done_id(idma, stream) > transaction_id) {
      return kIdmaOk;
    }
  }
  return kIdmaTimeout;
}
