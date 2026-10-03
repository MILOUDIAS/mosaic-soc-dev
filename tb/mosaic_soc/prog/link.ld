/* link.ld — place the three wake-and-run programs at their core boot addresses,
   all within RAM0 (0x0..0x8000). TITAN @0x180 (boot ROM jumps here), ATLAS
   (fazyrv) @0x1000 (BOOTADR), NANO (serv) @0x2000 (RESET_PC). objcopy -O verilog
   then emits one byte-hex with @-address blocks at each VMA; the testbench loader
   ($readmemh -> tb_writetoSram) places them into RAM0. Sentinels live at 0x3000+. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

SECTIONS {
  . = 0x00000180;
  .titan : {
    *(.text.init)              /* TITAN _start must be first, at 0x180 */
    *(.text*) *(.rodata*) *(.data*) *(.bss*) *(COMMON)
  }

  . = 0x00001000;
  .atlas : { *(.atlas) }        /* ATLAS (fazyrv) program at its BOOTADR */

  . = 0x00002000;
  .nano  : { *(.nano) }         /* NANO (serv) program at its RESET_PC */

  _end = .;
}
