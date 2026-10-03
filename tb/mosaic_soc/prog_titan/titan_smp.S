# titan_smp.S — all-TITAN 4-core SMP demo (2x cv32e20 + 2x cv32e40x).
#
# ONE program for all four harts, linked at 0x180 (the shared BOOT_ADDR every
# native core boots from via the boot ROM). Each hart branches on mhartid:
#
#   hart 0 (orchestrator):
#     1. writes the TITAN sentinel (0xC0FFEE00) to 0x3000 and clears slots 1-3,
#     2. queues 3 task descriptors in the TDU FIFO (task_id=0x0002,
#        core_hint=1..3) — push-all before any completion dependency,
#     3. pulses WAKE_REQ for harts 1-3 (a no-op here since every role is
#        titan and all cores already run; keeps the program correct if the
#        config ever marks them dormant),
#     4. polls sentinel slots 1-3 for the exact per-slot value
#        (0xC0FFEE00+slot — proves each hart consumed a UNIQUE descriptor),
#     5. signals soc_ctrl EXIT_VALUE=0 / EXIT_VALID=1 -> "EXIT SUCCESS".
#
#   harts 1-3 (SMP workers):
#     1. poll-pop the TDU TASK_POP register — reads return 0 while the FIFO
#        is empty (no side effect), and dequeue ATOMICALLY otherwise, so all
#        three harts can hammer the same register and each still receives a
#        unique descriptor,
#     2. slot = descriptor core_hint (bits [15:11]) — reporting is correct no
#        matter which hart pops which descriptor,
#     3. run a small arithmetic workload, store the result at 0x3100+slot*4,
#     4. write the sentinel 0xC0FFEE00+slot to 0x3000+slot*4 LAST, then park.
#
# Bare-metal, no stack, no libc. RV32E-safe register set only (x5-x9,
# x10-x15): the cv32e20 groups may be built as RV32E. rv32i encodings only.

    .equ TITAN_SENT,    0xC0FFEE00     # hart-0 sentinel; workers write +slot
    .equ SENT_BASE,     0x00003000     # sentinel block: slot*4
    .equ RESULT_BASE,   0x00003100     # worker results:  slot*4
    .equ TDU_WAKE_REQ,  0x200A000C     # TDU WAKE_REQ (W1S pulse)
    .equ TDU_TASK_PUSH, 0x200A0010     # TDU task FIFO push (WO)
    .equ TDU_TASK_POP,  0x200A0014     # TDU task FIFO pop (RO, atomic dequeue)
    .equ SOC_CTRL,      0x20000000     # EXIT_VALID@0x00, EXIT_VALUE@0x04
    .equ NUM_WORKERS,   3              # harts 1..3

    .section .text.init,"ax"
    .global _start
    .option norvc
_start:
    csrr t0, mhartid
    bnez t0, worker

    # ── hart 0: orchestrator ─────────────────────────────────────────
    # 1. own sentinel + clear worker slots (before any push: same-master
    #    stores complete in order, so slots are clean before a worker can
    #    possibly pop a descriptor and report)
    li   t1, SENT_BASE
    li   t2, TITAN_SENT
    sw   t2, 0(t1)                     # mem[0x3000] = 0xC0FFEE00
    sw   zero, 4(t1)
    sw   zero, 8(t1)
    sw   zero, 12(t1)

    # 2. queue one descriptor per worker: task_id=0x0002, core_hint=h
    li   t0, TDU_TASK_PUSH
    li   a0, 1                         # h = 1
    li   a1, NUM_WORKERS
push_loop:
    slli a2, a0, 11                    # core_hint field [15:11]
    li   a3, 0x00020000                # task_id = 0x0002 in [31:16]
    or   a2, a2, a3
    sw   a2, 0(t0)
    addi a0, a0, 1
    ble  a0, a1, push_loop

    # 3. wake pulse for harts 1-3 (no-op while every role is titan)
    li   t0, TDU_WAKE_REQ
    li   a0, 0xE                       # (1<<1)|(1<<2)|(1<<3)
    sw   a0, 0(t0)

    # 4. wait until every slot holds its exact per-slot sentinel
    li   a1, TITAN_SENT
poll_loop:
    lw   a0, 4(t1)                     # slot 1
    addi a2, a1, 1
    bne  a0, a2, poll_loop
    lw   a0, 8(t1)                     # slot 2
    addi a2, a1, 2
    bne  a0, a2, poll_loop
    lw   a0, 12(t1)                    # slot 3
    addi a2, a1, 3
    bne  a0, a2, poll_loop

    # 5. signal success
    li   t0, SOC_CTRL
    sw   zero, 0x04(t0)                # EXIT_VALUE = 0
    li   a0, 1
    sw   a0, 0x00(t0)                  # EXIT_VALID = 1 -> "EXIT SUCCESS"
9:  j    9b

    # ── harts 1-3: SMP workers ───────────────────────────────────────
worker:
    li   t0, TDU_TASK_POP
pop_loop:
    lw   s0, 0(t0)                     # atomic dequeue; 0 while empty
    beqz s0, pop_loop

    srli s1, s0, 11
    andi s1, s1, 0x1F                  # s1 = core_hint = sentinel slot
    slli s1, s1, 2                     # s1 = slot * 4

    # workload: sum = 0+1+...+63 = 2016 (exercises both core types' pipes)
    li   a0, 0                         # sum
    li   a1, 0                         # i
    li   a2, 64
1:  add  a0, a0, a1
    addi a1, a1, 1
    blt  a1, a2, 1b

    # result first (outside the sentinel window), then the sentinel LAST
    li   t1, RESULT_BASE
    add  t1, t1, s1
    sw   a0, 0(t1)

    li   a3, TITAN_SENT
    srli a4, s1, 2                     # slot index
    add  a3, a3, a4                    # 0xC0FFEE00 + slot
    li   t1, SENT_BASE
    add  t1, t1, s1
    sw   a3, 0(t1)

9:  j    9b                            # park (done)
