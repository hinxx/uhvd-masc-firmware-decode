next prompt


> The FFFF runs strongly cluster toward the end of the file (94-99% through). The longest run (84 words!) is at 94.4% through. This is exactly what erased flash looks like — at the end of program flash space, after the last bit of code, you find big runs of erased 0xFFFF.



these are not consecutive ffff, hence not a complete area of empty flash. note that this mcu is harvard arch. data is separate from code, likely after the code, at the end of this file. i think there is a big chance 0x00A4 is address of one of the first vectors defined; aka dummy default vector if you look at the 0x148 location of the dummy project this is where most of the jsr 0x00a4 are pointing to

