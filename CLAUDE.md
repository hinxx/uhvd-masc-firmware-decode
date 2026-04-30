# MC56F8345 Firmware Decode — Project Context

## Goal

Decode `firmware.elf.e` (Applied Motion Products proprietary format) into raw MC56F8345 / DSP56800E opcodes ready for disassembly and reverse engineering.

## Target hardware

- MCU: NXP/Freescale MC56F8345 (DSP56800E core, 16-bit fixed-point DSP)
- Flash: 128 KB, word-addressed (16-bit words)
- Device: Applied Motion Products ST5/ST10 motion controller

Reference documents in this directory:
- `MC56F8345.pdf` — MCU datasheet
- `DSA-222991.pdf` — DSP56800E architecture reference

## File structure (firmware.elf.e — 192,753 bytes)

```
Offset 0      13 bytes   ASCII header: "1.07R,22\r\n" + preamble bytes 80 af a9
Offset 13     40 bytes   Pre-frame preamble (10 groups of 4 encoded bytes)
Offset 53     8 bytes    First frame marker: 99 56 87 68 9c 66 1b a5
Offset 61     160 bytes  Frame 0 data
Offset 221    8 bytes    Frame marker (repeats every 168 bytes, typically)
...
              1,146 frames total, each 168 bytes (8-byte marker + 160-byte payload)
```

The frame marker `99 56 87 68 9c 66 1b a5` appears 1,146 times. Most inter-marker gaps are 168 bytes; a few differ (serial framing anomalies).

## Encoding scheme (CONFIRMED)

### The formula

For every 4-byte group in the clean payload (markers stripped):

```
decoded_high = group[0] ^ 0xCD
decoded_low  = group[3] ^ 0x45
```

Bytes at positions `[1]` and `[2]` within each group are **not yet understood** — they may carry error-correction data, a checksum, or redundant encoding. They are not needed to decode the firmware words.

### Ratio

4 encoded bytes → 2 decoded bytes (one 16-bit DSP word). Total decoded size: **~91,786 bytes** (~90 KB), fitting the 128 KB MC56F8345 flash.

### Validated against known patterns

| Encoded (hex)    | Decoded (hex) | Meaning                  |
|------------------|---------------|--------------------------|
| `99 62 6e a7`    | `54 e2`       | JSR opcode               |
| `99 62 6e a4`    | `54 e1`       | JMP opcode               |
| `8e 54 8b 6d`    | `43 28`       | Default handler address  |
| `8b 51 82 69`    | `46 2c`       | Reset handler address    |

## Decoded data layout

Applying the formula to the full 183,572-byte clean payload produces:

```
Decoded words 0–9    (pre-frame preamble, 20 bytes)
Decoded word  10     noise: frame-start header (bytes[0]=0xfa → decodes to 0x37xx)
Decoded words 11–12  JMP 0x462c  ← V0, reset vector
Decoded words 13–14  JMP 0x462c  ← V1 (COP reset)
Decoded words 15–16  JSR 0x4328  ← V2 (first default-handler vector)
...
Decoded words 17–168 Mostly JSR 0x4328 pairs (default interrupt handler)
                     Interspersed with ~2 noise words at each frame boundary
                     Some specific vectors point to unique addresses (e.g., 0x4228, 0x4525)
Decoded words ~170+  Code section (diverse opcodes, beginning of actual firmware)
```

### Frame boundary noise

At each frame boundary in the decoded output, 2 words are noise and must be stripped:
- **Frame trailer** (last 4 bytes of each 160-byte frame): encoded bytes[0]=0x95, decodes to `0x58da` (the "impostor" patterns `956f26xx`, `956e26xx`, etc.)
- **Frame header** (first 4 bytes of next frame): encoded bytes[0]=0xfa, decodes to `0x37xx`

These 2 noise words appear roughly every 40 decoded words (one per 160-byte frame).

### Corrected addresses (vs. prior analysis)

Prior analysis docs (`DECODING_SUCCESS.md`, `CURRENT_STATUS_AND_ANALYSIS.md`) incorrectly forced `0x54` as the high byte of every decoded word. The actual values from the formula are:

| Prior claim   | Correct value |
|---------------|---------------|
| JSR → 0x5428  | JSR → 0x4328  |
| JMP → 0x542c  | JMP → 0x462c  |

## Current scripts (status)

| File                    | Status   | Notes                                              |
|-------------------------|----------|----------------------------------------------------|
| `firmware_analyzer.py`  | Useful   | Correctly extracts clean payload (de-frames)       |
| `complete_decoder.py`   | Broken   | Forces byte0=0x54, wrong target addresses          |
| `real_final_decoder.py` | Broken   | Same forced-0x54 error, code-style fallback wrong  |
| `vector_decoder.py`     | Unknown  | Not yet reviewed                                   |
| `bit_decoder.py`        | Obsolete | Bit-level experiments, superseded                  |
| `advanced_decoder.py`   | Obsolete | Early experiments, superseded                      |
| `final_decoder.py`      | Obsolete | Nibble-based, superseded                           |
| `targeted_decoder.py`   | Unknown  | Not yet reviewed                                   |

Generated binaries (`firmware_elf_clean*.bin`) contain partially correct data but with wrong addresses in the vector table.

---

## Plan

### Phase 1 — Authoritative clean decoder (priority: HIGH)

Write a single `decoder.py` that replaces all prior scripts:

1. Read `firmware.elf.e`
2. Strip the 13-byte header
3. Remove all 8-byte frame markers, building the clean 183,572-byte payload
4. Apply the confirmed formula to every 4-byte group: `(group[0]^0xCD, group[3]^0x45)`
5. Identify and strip frame boundary noise words (trailer: `group[0]==0x95`, header: `group[0]==0xfa` immediately following a trailer)
6. Output a clean binary `firmware_decoded.bin` — raw 16-bit DSP words, big-endian, ready for disassembly

### Phase 2 — Vector table verification (priority: HIGH)

The MC56F8345 has an 82-entry interrupt vector table (per the datasheet). Each entry is 2 words: opcode + absolute address.

1. From the decoded output (after noise stripping), locate the 82-vector block
2. Print a table: vector index, opcode (JMP/JSR/other), target address
3. Verify that V0 = JMP reset-handler and count of JSR-default-handler entries
4. Cross-reference the few non-default vectors (e.g., 0x4228, 0x4525, 0x452d) against the MC56F8345 interrupt table to identify which peripherals are active

### Phase 3 — Disassembly (priority: MEDIUM)

1. Check if `m68hc11-elf` or `56800e` binutils/objdump are available (`apt-cache search 56800`)
2. Alternatively, try the open-source `dis56800` or `sc100dis` disassemblers
3. Pass `firmware_decoded.bin` through the disassembler, setting load address to `0x0000` (vector table base)
4. Verify the first recognizable instructions at the reset handler entry point (0x462c)
5. Locate the default interrupt handler at 0x4328 and confirm it is a minimal stub (RTI or similar)

### Phase 4 — Understand bytes[1] and bytes[2] (priority: LOW)

Each 4-byte encoded group has positions [1] and [2] currently unused. These could be:
- **CRC/checksum**: compute CRC8/16 of the group's data bytes and compare to [1][2]
- **Duplicate encoding**: same data bits encoded redundantly (forward error correction)
- **Scrambling/whitening**: apply LFSR and see if [1][2] become predictable

Approach: take 10 consecutive known groups (JSR 0x4328 pattern) where the decoded value is guaranteed, and analyze the [1][2] bytes for any formula relating them to the decoded word or to a running state.

### Phase 5 — Re-encoding (optional, priority: LOW)

If the purpose is to modify and reflash firmware, reverse the encoder:
- Understand bytes[1] and bytes[2] generation (Phase 4)
- Rebuild the frame structure with correct markers
- Prepend the ASCII header
- Verify the re-encoded file passes any checksum the downloader tool might check

---

## Key numbers

| Item                          | Value              |
|-------------------------------|--------------------|
| Raw file size                 | 192,753 bytes      |
| Header size                   | 13 bytes           |
| Frame marker                  | `99 56 87 68 9c 66 1b a5` |
| Frame count                   | 1,146              |
| Typical frame period          | 168 bytes          |
| Clean payload (markers out)   | 183,572 bytes      |
| Decoded firmware size         | ~91,786 bytes      |
| Encoding ratio                | 4 → 2 bytes        |
| XOR key (high byte)           | 0xCD (on byte[0])  |
| XOR key (low byte)            | 0x45 (on byte[3])  |
| Reset handler address         | 0x462c             |
| Default interrupt handler     | 0x4328             |
