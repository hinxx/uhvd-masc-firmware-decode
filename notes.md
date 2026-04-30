# notes

## firmware.elf.e

file size 192753 bytes
downloaded firmware update file with ascii header and encoded payload

## firmware-no-header.elf.e

file size 192740 bytes
ascii firmware id removed (13 bytes) from downloaded file
this is the data payload the firmware update tool sends to the MCU.
this is the update file we are working on and refering to from now on.

## content

the update file employs some sort of encoding as the data is not valid MCU opcodes
at a glance it looks like 16 bit MCU words are encoded with 4 bytes; this was determined by looking at patterns in the update file
the size of the final MCU update payload must be less than 128kB (MCU internal flash size)

## instruction set

RTI         Return from Interrupt                       1110 0111 0000 1001     E7 09
FRTID       Delayed Return from Fast Interrupt          1110 0111 0001 1010     E7 1A
RTID        Delayed Return from Interrupt               1110 0111 0000 1101     E7 0D
RTS         Return from Subroutine                      1110 0111 0000 1000     E7 08
RTSD        Delayed Return from Subroutine              1110 0111 0000 1100     E7 0C
DEBUGHLT    Enter Debug Mode                            1110 0111 0000 0001     E7 01
NOP         No Operation                                1110 0111 0000 0000     E7 00
JMP         Unconditional Jump <ABS19>                  1110 0001 0101 0100     E1 54
JMPD        Delayed Unconditional Jump <ABS19>          1110 0011 0101 0100     E3 54
JSR         Jump to Subroutine <ABS19>                  1110 0010 0101 0100     E2 54


## patterns

identifying patterns

### vector table

mcu flash needs to contain 82 interrupt vector entries
first two are `jmp <addr>` (16 bit opcode , 16 bit address)
the rest are `jsr <addr>` (16 bit opcode , 16 bit address)
user manual counts 16 bit addresses
last vector is at 0xA2 (162), counting from 0 .. 81 = 82 vectors
this means that there should be a repetition pattern that is quite visible at the start of the firmware update file
there could be some bytes preceeding the actual vector table in the update file

00000034 99 62 6e a4 8b 51 82 69    V0  jmp <addr>
0000003C 99 62 6e a4 8b 51 82 69    V1  jmp <addr>
00000044 99 62 6e a7 8e 50 8b 6d    V2  jsr <addr>
0000004C 99 62 6e a7 8e 54 8b 6d    V3  jsr <addr>
00000054 99 62 6e a7 8e 5c 8b 6d    V4  jsr <addr>
0000005C 99 62 6e a7 8e 21 8b 6d    V5  jsr <addr>
00000064 99 62 6e a7 8e 54 8b 6d    V6  jsr <addr>


### framing ?

pattern that could be seen as a framing header/footer looks like this:

    99 5? 8? ?? 9C 66 1B A5

the pattern is repeated 1150 times in the update file.

00000000 99 55 83 68 9C 66 1B A5    1x times        40 bytes long (start of file)
00000028 99 56 87 68 9C 66 1B A5    4x times        168 bytes long
000002C8 99 56 83 1D 9C 66 1B A5    1x times        64 bytes long
00000308 99 56 87 68 9C 66 1B A5    1142x times     168 bytes long
0002F078 99 56 83 68 9C 66 1B A5    1x times        40 bytes long
0002F0A0 99 56 83 6E 9C 66 1B A5    1x times        68 bytes long (end of file)


## decoding

### count encoded bytes

python count_encoded_bytes.py --bin firmware-no-header.elf.e

The encoded file length perfectly supports 4 encoded bytes -> 1 decoded 16-bit MCU word.
The encoded data uses a restricted byte alphabet: only 109/256 values appear.
It is not raw DSP56800E code.
It is not likely to be ordinary encryption or compression.
Plain opcodes like JMP/JSR cannot be searched directly, because bytes like E1/E2 never occur.
The encoding is probably deterministic and position/group dependent.
The next useful step is counting byte values separately for positions 0, 1, 2, and 3 of each encoded group.

### count encoded bytes by position

python3 count_by_position.py

The stream is divided into fixed 4-byte records.
Each record encodes exactly one 16-bit MCU word.
Each byte position uses its own restricted alphabet.
Positions 0 and 1 likely encode the low decoded byte.
Positions 2 and 3 likely encode the high decoded byte.
The alphabets have about 32 values each, suggesting a 5-bit-symbol or custom base32-like scheme.
Positions 2 and 3 have two extra rare/special values, probably markers or control/edge cases.
The most important next output is the pair count

### pair-frequency script

Now that we know the position alphabets are small, the next useful test is to count pairs

python3 count_pairs.py

The stream is fixed 4-byte records.
Each record probably encodes one 16-bit MCU word.
Each encoded byte position has a restricted alphabet.
Pair counts around 512 suggest 5-bit symbols with parity/check/control constraints.
The encoding is structured and deterministic, not encryption.
The two-byte-pair model is too simple because each pair has ~512 states, not <=256.
The known JSR group appears 479 times, which is plausible and gives a strong anchor.
The next best analysis is to inspect the encoded groups immediately following JSR/JMP, because those should be address words.


