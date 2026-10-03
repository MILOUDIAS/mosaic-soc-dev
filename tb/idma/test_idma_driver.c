// Host-side register-map/unit test for the MOSAIC iDMA driver.
#include <assert.h>
#include <stdint.h>

#include "sw/device/lib/drivers/idma/idma.h"
#include "sw/device/lib/drivers/idma/idma_regs.h"

int main(void) {
  uint32_t regs[IDMA_STREAM_STRIDE * 2 / sizeof(uint32_t)] = {0};
  idma_t idma;
  assert(idma_init(&idma, (uintptr_t)regs, 2) == kIdmaOk);

  const uint32_t bank = IDMA_STREAM_STRIDE / sizeof(uint32_t);
  const uint32_t token = 0x1234;
  regs[bank + IDMA_OWNER_REG_OFFSET / 4] = token;
  regs[bank + (IDMA_NEXT_ID_BASE_REG_OFFSET + 4) / 4] = 7;

  const idma_descriptor_t descriptor = {
      .src_addr = 0x100,
      .dst_addr = 0x800,
      .length = 4,
      .src_stride_2 = 4,
      .dst_stride_2 = 8,
      .reps_2 = 2,
      .src_stride_3 = 16,
      .dst_stride_3 = 32,
      .reps_3 = 3,
      .dimensions = 3,
  };
  uint32_t id = 0;
  assert(idma_submit(&idma, 1, token, &descriptor, &id) == kIdmaOk);
  assert(id == 7);
  assert(regs[bank + IDMA_SRC_ADDR_REG_OFFSET / 4] == 0x100);
  assert(regs[bank + IDMA_DST_ADDR_REG_OFFSET / 4] == 0x800);
  assert(regs[bank + IDMA_LENGTH_REG_OFFSET / 4] == 4);
  assert(regs[bank + IDMA_REPS_2_REG_OFFSET / 4] == 2);
  assert(regs[bank + IDMA_REPS_3_REG_OFFSET / 4] == 3);
  assert(((regs[bank + IDMA_CONF_REG_OFFSET / 4] >>
           IDMA_CONF_ENABLE_ND_OFFSET) & 3u) == 2u);
  return 0;
}
