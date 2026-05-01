# DSP56800E Firmware Decoder — Project Summary

## Target

- **File:** `firmware.elf.e` (192,753 bytes)
- **Source:** Applied Motion Products ST5-Q-RE single-motor drive controller
- **Chip:** Freescale/NXP MC56F8345 (DSP56800E core)
- **Goal:** Reverse-engineer the proprietary file format used to flash the chip and produce a usable firmware image.

## Status

**Partial decode working.** File format fully understood; per-byte-position bijection mapping is 14 of 64 nibbles pinned, remainder filled by frequency heuristic. Vector table and overall structure recoverable; absolute addresses are partly heuristic-guessed.

---

## File Format — Verified

### Top-level layout

```
[file header 13 bytes][frame 0][frame 1]...[frame N]
```

- **File header (13 bytes):** ASCII string `1.07R,22\r\n` plus 3 binary bytes (`80 AF A9`). Likely a version identifier; the binary tail bytes are unidentified. Stripped via `--skip 13`.
- **1148 frames** in the analyzed file (after header).

### Frame structure (verified from raw byte analysis)

```
[sync 4][FFFF-marker 4][metadata 4][payload N-16][tag 4]
```

| Field | Size | Content |
|---|---|---|
| sync | 4 | Frame-type marker (4 variants observed) |
| FFFF-marker | 4 | **Constant** `9c 66 1b a5` (decodes to codeword `0xFFFF`) |
| metadata | 4 | **Not** in codeword alphabet — uses different/extended encoding |
| payload | N−16 | Codeword stream (each codeword = 4 bytes = one 16-bit DSP word) |
| tag | 4 | Variable bytes 0,1; fixed marker `26 9F` (or polarity-flipped `BF 53`) at bytes 2,3 |

### Sync variants observed

| Sync | Count | Meaning (inferred from position) |
|---|---|---|
| `99 56 87 68` | 1145 | Standard frame (168 bytes, 38 codeword payload) |
| `99 55 83 68` | 1 | Boundary frame at file start (40 bytes, 6 codewords) |
| `99 56 83 68` | 1 | Boundary frame at file end (108 bytes, truncated) |
| `99 56 83 1D` | 1+ | Mid-file boundary frame (64 bytes) |

### Frame size distribution

- 1145 standard frames @ 168 bytes
- 1 frame @ 40 bytes (file-start boundary)
- 1 frame @ 232 bytes (mid-file, irregular)
- 1 frame @ 108 bytes (file-end boundary)

### Tag marker distribution

- 1147 frames: `26 9F` (standard marker)
- 1 frame: `BF 53` (disparity-flipped marker — bytes 0xBF and 0x53 are XOR partners of 0x26 and 0x9F under masks 0x99 and 0xCC)

---

## Codeword Encoding — Verified

### 4b/8b line code

Each 4-byte payload codeword carries one 16-bit logical word, encoded with disparity-balanced 4b/8b line coding.

- **Per byte position**, 32 valid byte values form **16 XOR-paired groups**
- Each XOR-pair represents one logical 4-bit nibble (0–F)
- Encoder picks polarity (which member of the pair) based on running disparity to maintain DC balance

### Per-position XOR masks

| Byte position | Mask |
|---|---|
| 0 | 0x66 |
| 1 | 0x33 |
| 2 | 0x99 |
| 3 | 0xCC |

### Per-position alphabets

Each position has exactly 32 valid byte values out of 256. The full alphabets are encoded in the decoder (`POSITION_PAYLOAD_ALPHABETS`).

### Nibble order — verified

Byte position to nibble index mapping (where nibble index 0 = bits 15:12 of the 16-bit word):

| Byte position | Nibble index | Bit range |
|---|---|---|
| 0 | 0 | 15:12 (high) |
| 1 | 2 | 7:4 |
| 2 | 3 | 3:0 (low) |
| 3 | 1 | 11:8 |

CLI flag: `--nibble-order 0231`

### Word byte order — verified

Decoded 16-bit words are **little-endian** in the file output. CLI flag: `--byte-order little`.

This was confirmed against a reference dummy firmware compiled with CodeWarrior for the same chip (`aa.elf.bin`), where JSR opcode bytes appear as `54 e2` (little-endian for `0xE254`).

---

## Decoder Pipeline (5 stages)

| Stage | Function | Deterministic? |
|---|---|---|
| 1 | `find_frames` | Yes — scans for `99 ?? ?? ??` sync followed by FFFF-marker `9c 66 1b a5` |
| 2 | `strip_framing` | Yes — concatenates payload bytes, dropping framing fields |
| 3 | `decode_pair_indices` | Yes — converts bytes to (pos, pair-id) tuples |
| 4 | `derive_mappings` | **Requires anchors** — maps pair-id → nibble per position |
| 5 | `apply_mappings` | Yes (given mappings) — reconstructs 16-bit words |

Stages 1-3 produce **45,808 codewords as pair-id 4-tuples**. This is everything recoverable without external knowledge.

Stage 4 needs **anchors** (codeword index, expected 16-bit value) pairs to break the bijection.

---

## Anchors — Working Set

```json
[
  [41124, "0xFFFF"],
  [6, "0xE154"],
  [10, "0xE254"],
  [17, "0x7015"],
  [19, "0x7025"]
]
```

All 5 mutually consistent. Pin **14 of 64** nibble assignments:
- Position 0: 3 pinned (out of 16 pair-ids)
- Position 1: 4 pinned
- Position 2: 3 pinned
- Position 3: 4 pinned

### Justification per anchor

| Anchor | Codeword index | Source of confidence |
|---|---|---|
| `0xFFFF` | 41124 | Longest constant-codeword run (39 codewords) — erased flash |
| `0xE154` | 6 | First codeword of vector 0; JMP `<ABS19>` opcode template (Freescale ref manual) |
| `0xE254` | 10 | First JSR codeword in vector table; JSR `<ABS19>` opcode template |
| `0x7015` | 17 | Vector 5 target = misalign handler (Freescale standard layout) |
| `0x7025` | 19 | Vector 6 target = default `intRoutine` handler (74 vectors share this value) |

---

## Vector Table — Decoded

82 entries, exactly matching the documented MC56F834x layout from `MC56F834x_vector.asm`:

| Vector | Decoded target | Role |
|---|---|---|
| 0 | 0xDF5F | Reset → `Finit_MC56F83xx_` (startup) |
| 1 | 0xDF5F | COP reset → same handler |
| 2 | 0x7075 | illegal instruction |
| 3 | 0x7025 | software interrupt 3 (default) |
| 4 | 0x70D5 | hardware stack overflow |
| 5 | 0x7015 | misaligned long word access |
| 6-19, 22-77, 79-81 | 0x7025 | default handler (74 vectors) |
| 20 | 0x98AF | Low Voltage Detector |
| 21 | 0xB025 | PLL |
| 78 | 0x?1FF | **Unknown** — see Outstanding Issues |

### Vector 78 — Forced Constraint

Codeword 163 has pair-ids `(B, B, 5, 8)`. Three of four positions are already pinned, forcing the low 12 bits of the decoded value to be `0x1FF`:

- pos 1 pid B → F (FFFF anchor)
- pos 2 pid 5 → F (FFFF anchor)
- pos 3 pid 8 → 1 (JMP anchor)
- pos 0 pid B → ? (not pinned, heuristic guesses `0`)

So target = `0x?1FF` for some unknown high nibble. Unable to determine `?` without an additional anchor that pins pos 0 pid B.

---

## Codeword Stream Statistics

- **Total payload codewords:** 45,808
- **Decoded output:** 87,144 bytes (43,572 16-bit words after current decoder pipeline)
- **Exact `0xFFFF`:** 3,262 occurrences (erased flash regions)
- **Almost-FFFF (3 of 4 nibbles = F):** 1,667 occurrences
- **Most common codeword overall:** `(C, B, 5, 9)` at 4410 occurrences = `0xFFFF`
- **Most common decoded word after FFFF:** `0xF4F4` (1171 occurrences) — likely alignment padding
- **JSR opcode (`0xE254`):** 906 occurrences in payload
- **Distinct JSR target addresses:** 178

---

## What Worked

### Format discovery
- Identified codeword alphabet size (32 values per byte position)
- Identified XOR-pair structure (16 pairs per position)
- Determined per-position masks (0x66, 0x33, 0x99, 0xCC)
- Discovered frame structure including the FFFF-marker as a frame-level field (not part of payload)
- Found tag marker `26 9F` at frame ends (not at frame starts as initially assumed)

### Decoder design
- 5-stage pipeline with deterministic stages 1-3 working without any external info
- Anchor-based bijection breaking (stage 4)
- Bijection propagation: when one pair-id is pinned, that nibble is excluded from other pair-ids at the same position
- Heuristic fallback for unpinned pair-ids based on overall nibble frequency

### Anchor identification
- FFFF anchor via longest constant-codeword run
- JMP/JSR `<ABS19>` opcode templates from DSP56800E reference manual (`0xE154`, `0xE254`)
- Default handler propagation (74 vectors sharing one target value)
- Specific handler at vector 5 (misalign) per Freescale standard layout

### Validation
- Boundary header bytes verified against user's hexdump (`0x320.txt`)
- Frame structure verified against per-frame CSV dump (`bytes3.csv`)
- Vector count of 82 verified by counting JMP/JSR opcode byte sequences in raw firmware (matches Freescale spec exactly)
- Byte order verified against reference CodeWarrior dummy firmware (`aa.elf.bin`)

### Self-test suite
Synthetic round-trip tests in `test_decoder.py`:
- Full pipeline (`build_test_stream` → all 5 stages → ground truth check)
- Partial-anchor bijection propagation
- Contradictory-anchor detection
- Real-file-prefix handling

---

## What Failed / Was Wrong

### Initial misunderstandings (corrected)

| Initial assumption | Reality |
|---|---|
| Frame = `[sync 4][tag 4][payload 160]` | Actually `[sync 4][FFFF-marker 4][metadata 4][payload 152][tag 4]` |
| Tag right after sync | Tag is at frame **end**, before next sync |
| Sync detection by enumerating `{99 56 87 68, 99 55 83 68, 99 56 83 68}` | Sync varies (4+ variants); detect by FFFF-marker at offset +4 instead |
| 40 codewords per standard frame | 38 codewords (4 bytes go to FFFF-marker, 4 to metadata) |
| Vector table is 56 entries | 82 entries (per Freescale spec); decoder was missing frame 5 because of unknown sync `99 56 83 1D` |
| `--drop-leading 8` to skip per-frame FFFF padding | Hack — the "padding" was actually the FFFF-marker frame field; correct framing handles it |
| SECL_VALUE = 0xE70A at file end | Chip is unsecured / configuration field not in this firmware image; SECL anchor produced contradictions |
| First-call JMP/JSR opcodes are `0xE984`/`0xE9A0` | These are 24-bit forms; vector table uses 19-bit forms `0xE154`/`0xE254` |

### Anchor attempts that failed

| Anchor attempt | Result |
|---|---|
| SECL_VALUE = `0xE70A` at codeword 45799 | Contradicted JMP+JSR anchors; chip is likely not secured |
| Vector 78 target = `0x7025` (default handler) | Contradiction at 3 positions; vector 78 has different pair-ids forcing low 12 bits to `0x1FF` |
| Default nibble order `0123` | Inconsistent with JMP+JSR anchors (pos 1 pid 7 would have to be both `1` and `2`) |
| 5 of 6 alternate nibble orders | Mathematically consistent with FFFF+JMP+JSR alone, but only `0231` produces real DSP code structure when decoded |

### Things that didn't pan out

- **Searching for ASCII command strings (SCL commands like `ST`, `AC`, `DE`):** No clean uppercase letter sequences found. SCL command tables, if present, are likely in data flash and addressed through the (still-encoded) metadata field.
- **`0xF4F4` = miscoded `0xFFFF` hypothesis:** Disproved by bijection rule. Pos 3 pid 9 → F is anchored, so pos 3 pid E cannot also map to F. `0xF4F4` is genuinely a different value (likely alignment padding).
- **"Almost FFFF" runs as anchors:** 1,667 patterns with 3-of-4 F nibbles, but they don't share a single common pid pattern, so they can't be uniformly anchored.
- **Linear chip-address mapping (decoded byte = chip address × 2):** Doesn't work because frames target non-contiguous chip regions; the decoded.bin is a temporal stream of frames, not a flat memory image.

---

## Outstanding Issues

### 1. Metadata field encoding is unknown

The 4-byte metadata field per frame contains values **outside the codeword alphabet** (e.g. `0x78` at byte 2; `0x38`, `0x46` at byte 3). Byte distributions:

- Byte 0: 12 unique values, mostly `{F2, F3, F8, F9, FA, FB, FC, FD, FE, FF, 8B, 8C}`
- Byte 1: 16 unique values
- Byte 2: 17 unique values
- Byte 3: 11 unique values

Likely encodes the **destination flash address** for the frame's payload, but encoding scheme is not yet determined. Without this, decoded.bin is a stream of frames in temporal order, **not a flat memory image** of the chip.

### 2. Bijection only 14/64 pinned

50 of 64 pair-id → nibble mappings are still heuristic guesses (frequency-based). The heuristic gets common values right (FFFF, JMP/JSR opcodes, the most frequent default-handler target) but is wrong for many specific values.

### 3. Vector 78 target unresolved

Forced to `0x?1FF` form. Unknown high nibble. Would require an anchor that pins pos 0 pid B.

### 4. Boundary-frame purpose unknown

Frame 0's 6 codewords decode to: `0xE2F3 0x2488 0xE168 0x261B 0x2158 0xE158`. None of these match documented JMP/JSR opcode patterns (e.g. `0xE158` violates fixed bit 2 of JMP `<ABS19>`). May be file-format metadata, a special boot block, or program memory at an unusual address — current evidence inconclusive.

### 5. Frame address mapping

Without metadata decoding, cannot map decoded codewords to chip memory addresses. This means:
- Cannot locate `_startup` (at chip address `0xDF5F`) in decoded output
- Cannot compare against the reference CodeWarrior dummy firmware
- Cannot find the SCL command table at its real address

---

## Files Produced

| File | Description |
|---|---|
| `dsp56800e_decoder.py` | Main decoder. CLI: `--info`, `--find-runs`, `--find-value`, `--inspect`, `--check-anchors`, plus full decode mode |
| `test_decoder.py` | Self-test suite (4 tests, all passing) |
| `anchors.json` | Working anchor set (5 anchors, consistent) |
| `decoded.bin` | Partial firmware decode (87,144 bytes) |

## Reference Materials Used

| File | Role |
|---|---|
| `firmware.elf.e` | Target file (192,753 bytes) |
| `bytes3.csv` | Per-frame sync + first 12 bytes for all 1150 frames; revealed metadata field structure |
| `0x320.txt` | Hexdump of first 816 bytes; revealed frame structure and confirmed 82-vector table size |
| `flash.pdf` | MC56F8300 Flash Memory chapter (provided SECL_VALUE hypothesis, later disproved for this firmware) |
| `jmp-jsr.pdf` | DSP56800E JMP/JSR opcode encoding (provided correct `0xE154`/`0xE254` templates) |
| `MC56F834x_vector.asm` | Freescale reference vector table (confirmed 82 entries, vector roles) |
| `MC56F83xx_init.asm` | Freescale startup code (reference for `_startup` routine — unused so far) |
| `aa.elf.bin` | CodeWarrior dummy firmware (confirmed little-endian byte order; provides comparison target if metadata decoded) |

---

## Key Discoveries Timeline

1. **Alphabet structure** — 109 of 256 byte values used in payloads, organized into 4 byte-position alphabets of 32 values each
2. **XOR-pair bijection** per position with masks `0x66`, `0x33`, `0x99`, `0xCC`
3. **168-byte frame structure** — initial layout was wrong; corrected after CSV analysis revealed FFFF-marker is at frame start, metadata is non-codeword bytes, payload is 38 codewords
4. **Nibble order `0231`** — derived from JMP vs JSR opcode byte positions
5. **Sync detection via FFFF-marker** — caught the missing `99 56 83 1D` variant frame, recovering the missing 6 JSR vectors
6. **74 default-handler vectors** all decode to same address — strong propagation anchor
7. **Vector 78 is genuinely specific** (target `0x?1FF`), not a heuristic error
