/* link.ld — all-TITAN SMP demo: ONE program at 0x180 for all four harts
   (every native core boots via the boot ROM to the shared BOOT_ADDR and
   branches on mhartid). Sentinels/results live at 0x3000+/0x3100+, well away
   from the code. objcopy -O verilog emits the byte-hex the testbench loader
   places into RAM. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

SECTIONS {
  . = 0x00000180;
  .text : {
    *(.text.init)              /* _start must be first, at 0x180 */
    *(.text*) *(.rodata*) *(.data*) *(.bss*) *(COMMON)
  }
  _end = .;
}
