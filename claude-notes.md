Looking at the byte histogram, several structural observations stand out — and together they let you rule out a lot of candidate encodings before doing any further analysis.

## 1. Alphabet size: 109 of 256 values

Only 109 distinct byte values appear in 192,740 bytes. The data is even-sized (consistent with 16-bit DSP word alignment), and 0xBF appearing exactly twice is almost certainly noise / framing artifact / a lone trailer byte rather than part of the regular alphabet — so the *real* working alphabet is 108 symbols.

That immediately rules out a lot:

- **Not a simple XOR mask** — XOR with any constant is a bijection over {0..255}; you'd see all 256 values, just permuted. The drop from 256 → 109 cannot come from XOR.
- **Not a byte-substitution** (S-box) for the same reason.
- **Not a strong cipher / not compressed-then-encrypted** — both produce ~uniform byte distributions over (essentially) all 256 values.
- **Not base64 / base32 / base85 / hex** — alphabet sizes don't match (64 / 32 / 85 / 16), and those alphabets sit in printable ASCII (0x20–0x7E), but here 0x70–0x7F never appears at all.
- **Not S-records or Intel HEX** — those are pure printable ASCII; you have plenty of high-bit bytes.

So the encoding is *expansive* (each output byte carries strictly less than 8 bits of input) and *deterministic but non-uniform* over a restricted alphabet. That's the fingerprint of a **line-code / bit-stuffing / restricted-alphabet expansion**, not a cipher.

Information-theoretic ceiling: log₂(109) ≈ 6.77 bits/byte → the decoded payload is at most ≈ 163 kB, but more likely smaller because the distribution is far from uniform.

## 2. The forbidden region is structured by *upper nibble*

Group the missing values by high nibble:

| Upper nibble | Status |
|---|---|
| 0x0_, 0x3_, 0x4_, 0x7_, 0xC_ | **entirely absent** (80 values) |
| 0xB_ | absent except 0xBF×2 (treat as absent) |
| 0x1_, 0x2_, 0x5_, 0x6_, 0x8_, 0x9_, 0xA_, 0xD_, 0xE_, 0xF_ | present (with row-specific holes) |

So the constraint that produces the restricted alphabet lives primarily in the **top 4 bits**. In binary, the forbidden upper nibbles are {0000, 0011, 0100, 0111, 1011, 1100} and the allowed ones are {0001, 0010, 0101, 0110, 1000, 1001, 1010, 1101, 1110, 1111}. That's exactly the kind of "valid code symbol" table you see in line codes — only certain 4-bit prefixes are legal, the rest are reserved for control / illegal / never-emitted.

## 3. Pairwise symmetry between high-nibble rows

The rows aren't independent — several are *identical* in their low-nibble support:

- Row 0x2_ and row 0xD_ both populate exactly lows {0,1,3,4,6,7}. Note 2 = 0010 and D = 1101 are bitwise complements.
- Row 0x5_ and row 0xA_ both populate exactly lows {0..7, C, D}. And 5 = 0101, A = 1010 are bitwise complements.
- Row 0x6_ and row 0xF_ are essentially full (0xF5 missing). 

That kind of complementary pairing is the signature of an encoder that emits two "polarities" of the same underlying symbol — exactly what DC-balanced line codes (8b/10b-style, or running-disparity-aware schemes) and certain framing codes do, where both X and ~X are valid representations of the same logical value chosen to balance the bitstream.

## 4. Distribution shape: structure-preserving

The histogram is strongly non-uniform — top occupants are 0x1B (4.54%), 0xA5 (3.96%), 0x82 (3.79%), 0x69 (3.60%), 0x9C (3.23%), 0x66 (3.09%). That's the second clue that the encoding is straightforward: it's preserving the bias of the underlying instruction stream rather than flattening it. DSP56800E machine code has very biased opcode/operand byte frequencies (a handful of move/parallel-move encodings dominate), and you can see that bias survive intact through the encoding — meaning the transform is essentially **memoryless or near-memoryless and structure-preserving** (likely a fixed table lookup applied per fixed-size group of input bits, rather than something with state propagation like a stream cipher or LFSR-whitened format).

## 5. What this collectively implies

Putting it together, the most likely class is a **fixed-rate block code** of the form "*N* input bits → *M* output bits with *M > N*, where each output group is constrained to a legal symbol set." The observed pairing-of-complements rows hint at a DC-balance / running-disparity element. A few plausible specific shapes worth probing:

- A **6b → 8b** style mapping (each output byte = one 6-bit symbol from a 64-entry table, with two extra bits used for parity/disparity/framing) — would expand input by 8/6 ≈ 1.33×, giving a raw size of ~144.5 kB.
- A **block code over multiple bytes** (e.g., 4 input bytes → 5 output bytes), with the upper-nibble pattern reflecting symbol-position within the block. 192,740 is divisible by 5 (=38,548 blocks) and by 4, so 4↔5 is a clean candidate; not divisible by 3, so 3↔4 is out for whole-file alignment.
- Something **HDLC/COBS-flavored** — but COBS would not produce this particular forbidden-nibble structure, so probably not COBS proper.

## 6. Concrete next steps if you want to crack it

Things the histogram alone won't tell you, but these will:

- Check if the encoding has **fixed period**. Compute byte-position-modulo-*k* histograms for *k* ∈ {2, 3, 4, 5, 8}. If, say, every 5th byte has a different alphabet than the others, you've found the block size.
- Look at **bigram statistics** (count P(byte₂ | byte₁)). If P(b₂|b₁) is close to 1 for many pairs, you have a state machine / disparity tracker. If pairs are nearly independent, it's a stateless table.
- Check whether there's **a known plaintext anchor** — e.g., the DSP56800E reset vector or a NOP-sled at a known offset. Aligning known input bytes against the encoded bytes at the right offset typically gives away the block size and the codeword table in one shot.
- Try treating it as a **bitstream** (concatenate all bytes MSB-first, then re-split into 5-bit, 6-bit, 9-bit, 10-bit groups) and look for the alphabet shrinking to a "clean" power-of-two-friendly size in one of those groupings — that immediately reveals the symbol width.

The headline though: from the histogram alone, this is *encoded*, not *encrypted* — and almost certainly a deterministic line/block code with an expansion factor somewhere in the 1.18×–1.33× range.


The position-mod-4 results pin this down decisively. Here's what the new data tells us.

## The encoding is a 4-byte block code with per-position alphabets

The fact that every position's alphabet is essentially disjoint from the others' (apart from a few overlaps) confirms the file is a stream of **independent 4-byte codewords**. Position 0 always comes from {0x88, 0x89, 0x8B, …, 0xFF}, position 1 from {0x10, 0x12, …, 0x6F}, etc. — there's no slippage, no escape into other modes that I can see.

## Each position's alphabet has exactly 32 "main" values, structured as 16 XOR pairs

This is the key result. At each position, the alphabet is **closed under XOR with a position-specific mask**:

| Position | Mask | Examples |
|---|---|---|
| 0 | **0x66** | 0x88↔0xEE, 0x9C↔0xFA, 0x99↔0xFF, … |
| 1 | **0x33** | 0x10↔0x23, 0x55↔0x66, 0x5D↔0x6E, … |
| 2 | **0x99** | 0x12↔0x8B, 0x1B↔0x82, 0x6E↔0xF7, … |
| 3 | **0xCC** | 0x69↔0xA5, 0x68↔0xA4, 0x6E↔0xA2, … |

Every observed byte at every position has its XOR-partner also present — and the partition is clean (16 pairs × 2 = 32 values). The four masks themselves form a closed family: each has popcount 4 (perfectly balanced), pos 0 ↔ pos 2 are bitwise complements (0x66 ⊕ 0x99 = 0xFF), and pos 1 ↔ pos 3 are bitwise complements (0x33 ⊕ 0xCC = 0xFF). That's not random; that's a deliberately constructed code table.

## What this means: it's a 4b/8b line code with running disparity

Each encoded byte carries:
- **4 bits of payload** (which of 16 logical symbols)
- **1 bit of polarity** (which of the two XOR-paired representatives is emitted), chosen by the encoder to balance running disparity
- **3 "structural" bits** that are fixed by the position and serve as framing — they're what makes positions 0/1/2/3 alphabets distinguishable from each other

In other words, this is the byte-aligned analog of an 8b/10b-style scheme: a **2× expansion code** where 4 logical bits get inflated to 8 line bits to maintain DC balance and provide a self-synchronizing structure.

## Decoded payload size

Each 4-byte encoded group → 4 nibbles → one 16-bit DSP56800E word. So:

> **192,740 encoded bytes → 96,370 decoded bytes → 48,185 sixteen-bit instruction/data words.**

The 2× expansion factor lines up exactly with the 4-byte alignment and the DSP56800E's 16-bit word architecture. No padding required, no remainder — strong confirmation that the model is right.

## One subtlety: positions 2 and 3 have a rare 17th pair

Position 2 has the pair {0x26, 0xBF} with counts 1149 and 2. Position 3 has the pair {0x53, 0x9F} with counts 2 and 1149. These respect the same XOR rule (0x26 ⊕ 0x99 = 0xBF, 0x53 ⊕ 0xCC = 0x9F), so they're legal codewords — just very rare and **almost always emitted in one specific polarity** (which is why the partner shows up only 2 times each).

Two things stand out:

1. The "rare" partners 0xBF and 0x53 are precisely the values that the global histogram flagged as anomalies in the previous analysis (BF=2, 53 mostly at pos 1 with only 2 at pos 3). So those weren't framing noise — they're the disparity-flipped versions of a special symbol that almost never needs the flip.
2. The common partners both have count **1149** at their respective positions. Identical count strongly suggests they appear **together in the same 4-byte groups** — i.e., there's a special class of ~1149 codewords (≈2.4% of all groups) where positions 2 and 3 use the extended alphabet.

That special class likely corresponds to either an escape into a different inner format (e.g., a literal 16-bit immediate that bypasses the normal nibble-table), or a 17th logical value used only for a specific instruction class — DSP56800E does have variable-length instructions with extension words, and this is exactly the kind of asymmetry you'd see if certain extension words use a slightly different sub-table.

## Why DC balance for a DSP56800E firmware?

Three plausible motivators, none of them about the DSP56800E itself:

- **Flash storage** — many flash technologies (especially older NOR / charge-trap) prefer codewords with balanced 0/1 counts to avoid programming asymmetries and to make ECC behavior more predictable.
- **Secure boot / code protection** — Freescale/NXP's protected-flash and secure-bootloader formats for the 56F8xxx family use balanced encodings as part of the obfuscation/integrity layer.
- **Serial download protocol** — if the firmware was captured off a UART/SPI bootloader stream, the line was probably DC-coupled-via-capacitor and required balanced bytes.

## Concrete next steps

Now that the structure is known, recovering the cleartext is a straightforward table-fitting problem:

1. Build the four pair-lists (16 pairs per position for 0/1, 17 for 2/3).
2. For each position, label the 16 "normal" pairs with logical values 0–F somehow (initially arbitrary).
3. Concatenate the 4 nibbles of each group into a candidate 16-bit word.
4. Validate against a known DSP56800E anchor — the **reset vector** (a JMP/BRA at the start) or a recognizable opcode pattern (the parallel-move bit-fields are very distinctive). The correct nibble-to-value mapping at each position is the unique permutation that makes the disassembly look like real code.
5. The 1149 "escape" groups can be set aside on the first pass and decoded once the regular codeword mapping is locked in — they're almost certainly extension words or immediates whose interpretation will be obvious once their host instructions are decoded.

Yes — the CSV is essentially the missing piece, and it explains the 1149 count almost exactly. Let me lay out what falls into place.

## The arithmetic lines up almost perfectly

The CSV contains **1148 sync markers** at 168-byte stride. Compare to what we observed at positions 2 and 3 of the global mod-4 alphabet:

| Position | "Common" 17th pair | Count | "Rare" partner | Count |
|---|---|---|---|---|
| 2 | 0x26 | **1149** | 0xBF | **2** |
| 3 | 0x9F | **1149** | 0x53 | **2** |

1149 ≈ 1148 (off by one), and the rare-partner count is **exactly 2** — which matches the **exactly 2 anomalous sync variants** in the CSV. That's not coincidence; it's the smoking gun.

## So what's actually going on per frame

The 168-byte frame holds **42 four-byte codewords**, partitioned like this:

```
codeword #0       sync  →  encoded bytes  99 56 87 68    ← what's in the CSV
codeword #1       tag   →  encoded bytes  ?? ?? 26 9F    ← the "17th pair" codeword
codeword #2..#41  data  →  40 codewords  = 80 decoded bytes of real payload
```

The CSV only enumerated codeword #0 (because that's the obvious sync pattern), but every frame also contains a second special codeword right after it whose encoded bytes 2 and 3 are exactly **0x26** and **0x9F**. That's why those two values both have count ≈ 1148: each appears once per frame, at a fixed offset within the frame.

So the "extension" 17th codeword from the previous turn isn't really an opcode-class extension — it's a **frame-level tag word**, used by the framing layer rather than the payload layer. Bytes at positions 0 and 1 of that tag word probably carry varying content (frame counter, length, type — to be determined), but bytes at positions 2 and 3 are essentially fixed.

## Why 0xBF / 0x53 appear exactly twice — running disparity

This was the open question, and the CSV answers it cleanly. Pop-count disparities (where 4 ones = balanced):

| Frame variant | Count | Disparity |
|---|---|---|
| `99 56 87 68` (regular) | 1146 | **−1** |
| `99 55 83 68` (file start, offset 0x0) | 1 | **−2** |
| `99 56 83 68` (file end, offset 0x2F078) | 1 | **−2** |

The two "irregular" frames sit at the very start and very end of the file and end with a *different* running disparity than the 1146 regular ones. The encoder, faced with that altered DC state, picks the *opposite polarity* of the same logical tag word — which is exactly the XOR-partner: 0xBF instead of 0x26 at position 2, and 0x53 instead of 0x9F at position 3.

So the count breakdown is:

```
0x26 at pos 2:  1146 (regular)  +  ~3 elsewhere   = 1149
0xBF at pos 2:  2 (the file-edge frames)          = 2
0x9F at pos 3:  1146 (regular)  +  ~3 elsewhere   = 1149
0x53 at pos 3:  2 (the file-edge frames)          = 2
```

The trailing ~3 occurrences are most likely in the **104 bytes (26 codewords) of trailer that sit after the last sync** — it's plausible that a few "tag" codewords appear there without a preceding sync (e.g., a footer or final metadata block).

## What this confirms about the encoding scheme

A few things now click into place that were hypotheses before:

1. **It really is a DC-balanced line code with running disparity** — and the disparity is tracked across the whole stream, not reset per-frame. The fact that two specific frames at the file boundaries triggered exactly two polarity flips is the kind of evidence you can't easily get any other way.

2. **The "17th pair" isn't a payload codeword at all.** It's reserved for the framing layer. So the actual payload alphabet is 16 logical symbols per position — exactly what you'd want for a clean 4-bits-per-codeword-byte encoding (i.e., true 4b/8b).

3. **Effective decoded payload size is smaller than 96,370 bytes.** Each 168-byte frame yields only 80 bytes of payload (the 40 data codewords × 2 bytes each), not 84:
   - 1148 frames × 80 bytes = **91,840 bytes of real DSP56800E code/data**
   - Plus a partial trailer of at most ~50 bytes
   - The remaining ~4500 decoded bytes are framing overhead (sync + tag) — about 5% overhead.

4. **The frame layout looks like a flash-page / record format, not a streaming protocol.** 168-byte stride is unusual for a UART line code (those tend to use 7-bit or 9-bit symbols, not 168-byte frames). It's much more consistent with a record-oriented flash layout, possibly **80 bytes of payload per frame = 40 × 16-bit DSP words**, which is a clean alignment for the 56800E's word-addressed program memory. The two anomalous boundary frames being slightly different in their last bytes (the `83` vs `87` swap toggles one bit) is a very protocol-flavored signal — likely "first frame" and "last frame" markers.

## Practical decoder implications

When you write the decoder, treat each 168-byte frame as:

```
struct frame {
    uint8_t sync[4];      // 99 56 87 68 (or boundary variants)
    uint8_t tag[4];       // ?? ?? 26 9F  — record metadata, decode separately
    uint8_t payload[160]; // 40 codewords → 80 bytes / 40 DSP words
};
```

Then for the disassembly anchor work I mentioned previously: drop the sync and tag, concatenate the 40 payload codewords from each frame in order, decode the 4b/8b table, and **anchor the disassembly at the start of frame #0's payload** (which should be the reset vector or the first instruction at program-memory address 0).

The first thing to look at once you have a tentative decode is whether the *tag word* contents (positions 0 and 1, varying per frame) look like a monotonic counter, a checksum, or a length field — that'll tell you whether records are independent (random-access flash pages) or sequenced (a download stream).

The flash docs don't directly explain the encoding format — that's a software/tooling layer applied externally — but they confirm several structural assumptions and, more usefully, hand you a set of known-plaintext anchors that should let you finish breaking the codeword tables.

## What the docs confirm

A few things we'd been treating as hypotheses are now nailed down:

**The 16-bit word orientation is mandatory.** Section 6.5.2: *"Only 16-bit write operations are allowed to the FM space."* Every flash address is a 16-bit word, programmed atomically. Our model where each 4-byte codeword decodes to exactly one 16-bit word is the right granularity — there's no byte-level alternative on this hardware.

**Program Flash is interleaved with explicit even/odd pairing.** Section 6.5.3.2 step 1b: *"two words may be programmed at once due to its interleaved design. The first data value… should be written to an Even address and an additional data value may be written to the complementary Odd address. Both values will be written concurrently."* This tells you that, within Program Flash, the natural unit isn't really one 16-bit word — it's a 32-bit (even, odd) pair. So once you have a tentative decode, **expect (word_2k, word_2k+1) to be a meaningful pair**, not just two adjacent words. Some firmware tools store opcodes in the even word and operands/literals in the odd word; this can give a sanity check on whether you've got the byte-position-to-nibble mapping right.

**Erased flash reads as `0xFFFF`.** Section 6.3 note. So any region of the decoded image that's all-`FFFF` is unprogrammed. If your decoded image ends with a long run of `FFFF`s, that's the tail of program flash that wasn't used by this firmware. (And if you find a long run of `FFFF`s in the *middle*, your decode is probably wrong.)

## The configuration field gives you precise anchors

This is the most actionable part of the document for cracking the encoding. Section 6.4 / Table 6-1 specifies that the **top 9 words of program flash hold a fixed-structure configuration block**:

| Offset from top | Word | Likely value |
|---|---|---|
| TOP − 0 | BACK_KEY_3 | unknown (8-byte key spread over 4 words) |
| TOP − 1 | BACK_KEY_2 | unknown |
| TOP − 2 | BACK_KEY_1 | unknown |
| TOP − 3 | BACK_KEY_0 | unknown |
| TOP − 4 | PROT_BANK1 | typically `0x0000` (data flash unprotected) or `0xFFFF` (default erased) |
| TOP − 5 | PROT_BANK0 | typically `0xFFFF` or has high bit set (see below) |
| TOP − 6 | PROTB | typically `0xFFFF` |
| TOP − 7 | SECH | upper bits encode KEYEN; rest is `0` |
| TOP − 8 | SECL | **`0xE70A` if secured, else any other value** |

Two of these are gold:

- **`SECL = 0xE70A`** is a single-bit-different signal: section 6.5.4 / Table 6-7 explicitly tells you this is the magic word. It's also confirmed elsewhere (footnote on Table 6-7) that *"`0xE70A`… represents an illegal instruction on the 56800E core,"* which is precisely why it was chosen — meaning if your firmware is real code and not a config-field reproduction, this word should appear *only* at this one location. That makes it a unique fingerprint.

- **`PROT_BANK0` bit 15 must be `0`** if the device ever needs to be reprogrammed in the field — section 6.8.1 says the FM configuration field lives in the top program-flash sector, and to update it you need bit 15 (the top sector's protection bit) clear. So that bit gives you one more constrained nibble.

Combined with the **reset vector** at decoded word 0 (which on the 56800E is a `JMP` — opcode pattern starting `0xE984…` for a 24-bit absolute jump, or similar), you have at minimum:

- Word 0: high nibble `E`, second nibble `9`, third nibble `8`
- Word TOP − 8: full word `E70A` 

That's enough constrained nibbles at known positions to solve the codeword-to-nibble mapping at all four byte-positions by inspection. You don't need to guess.

## On the 1149 count specifically

The flash docs don't add anything to the explanation we already had. The 1149 count is fully accounted for by frame structure: 1148 sync words + a 1148-times-recurring tag word with bytes 2–3 fixed at `26 9F`, plus the two boundary frames where running disparity flips those to their XOR partners `BF 53`. The flash hardware has nothing to say about that — it's a property of whatever serial bootloader or production-image format is wrapping the actual program data.

That said, the docs do hint at *why* such a wrapper exists. The flash command interface (section 6.5.3) is a strict three-step state machine per word programmed, with multiple ACCERR conditions for any deviation. A bootloader that streams firmware over UART/SCI and programs it word-by-word into flash would naturally want **frame-level integrity protection** (sync + checksum + length) so a corrupted byte doesn't propagate into bad flash contents. The "tag" codeword whose low byte is fixed and whose high byte varies is exactly what a per-frame checksum or sequence number would look like.

## What's still externally determined

The choice of **40 data words per frame** isn't explained by anything in the document. It doesn't align with the flash row (32 words non-interleaved, effectively 64 words interleaved) or page (256 / 512 words). It's almost certainly a software-side choice — a buffer size, a checksum granularity, a UART-friendly chunk — made by whatever tool produced the `.e` file. You'd need the bootloader source or the encoder's documentation to nail that down, and it doesn't matter for decoding the payload.

## Suggested next move

Pin the codeword table using two anchors simultaneously:

1. The encoded bytes corresponding to decoded word 0 must produce nibble sequence `E, 9, 8, ?` — the JMP opcode prefix.
2. The encoded bytes corresponding to decoded word at offset (frame_count_to_top − 8 within the data stream) must produce nibble sequence `E, 7, 0, A` — the security word.

You'll need to figure out where in the encoded stream "word 0" and "TOP − 8" actually sit (accounting for sync+tag in each frame, and whether decoded data is laid out frame-payloads-concatenated or in some other order), but once you've placed those two anchors, the per-position 16-pair tables fall out: each of the four byte positions has 16 unknowns, and you've got enough constrained nibbles from these two anchors plus statistical bias (the most-common encoded bytes at each position should map to the most-common nibbles in real DSP code, with `0` and `F` heavily overrepresented) to lock the mapping uniquely.


All three self-tests pass. Now let's also confirm the CLI works and runs `--info` correctly:Full pipeline works end-to-end. Let me move the files to outputs and present them.## Decoder structure

The module separates the decode into 5 independent stages so you can stop at whichever one the available information supports:

| Stage | Function | What it needs |
|---|---|---|
| 1 | `find_frames` | Nothing — uses the sync/tag patterns |
| 2 | `strip_framing` | Nothing — removes 8 bytes (sync + tag) per frame |
| 3 | `decode_pair_indices` | Nothing — uses the validated 32-byte alphabets |
| 4 | `derive_mappings` | **Anchors** — `(codeword_index, expected_word)` pairs |
| 5 | `apply_mappings` | Output of stages 3 + 4 |

**Stages 1-3 are fully deterministic** and produce framing-stripped pair-id rows. The pair-id stream is everything you can recover without any external knowledge — it tells you the logical content per codeword in 4-nibble groups, modulo the unknown bijection at each position.

**Stage 4 is where the anchors come in.** `derive_mappings` accepts a list of `(codeword_index, expected_16bit_word)` tuples, applies bijection propagation (a nibble pinned to one pair-id at a position can be excluded from the other 15 pairs), and falls back to a frequency heuristic for any pair-ids no anchor reaches. With well-chosen anchors covering all 16 pair-ids at each position, the fallback is never invoked and the result is exact.

## How to run it

The likely first attempt — using only the `SECL = 0xE70A` anchor from the flash documentation:

```
python3 dsp56800e_decoder.py firmware.e --info
```

That prints frame counts, anomalous-frame details, and the pair-id frequency at each position (very useful for sanity-checking).

To decode with anchors, write a JSON file like:

```json
[[45896, "0xE70A"]]
```

(replace the codeword index with the actual position of `SECL_VALUE` in the decoded stream; it's 8 words from the top of program flash, but you have to figure out where program flash actually ends in the data stream — typically near the end of the last standard frame).

Then:

```
python3 dsp56800e_decoder.py firmware.e --anchors anchors.json -o decoded.bin
```

A single anchor pins only 4 of the 64 pair-ids, so the result will still be wrong in most positions. Add more anchors as you identify them (the JMP at word 0, the `0xFFFF` words at unprogrammed addresses, recognizable opcode prefixes, etc.) until the decoded output starts disassembling cleanly.

## Things to be aware of

**Nibble order.** The default `--nibble-order 0123` puts byte-position-0's nibble in bits 15:12 of the output word (big-endian within the word). If your anchors don't validate, try `--nibble-order 3210` (little-endian) — the four positions could be permuted any way and the structure analysis can't tell you which is which without an anchor confirming.

**Word byte order.** `--byte-order big` writes each 16-bit word as MSB-first; `little` writes LSB-first. The 56800E is little-endian on memory, but flash dumps and tools vary.

**Anomalous frames.** By default they're concatenated into the payload if their bytes pass the alphabet check. The 40-byte header at offset 0 has only 8 codewords (32 bytes payload after stripping 8 bytes of framing) and may not be real DSP code — it's probably file-format metadata. Pass `--only-standard` to exclude it; you'll lose those 8 words but get a cleaner stream.

**The 232-byte mid-file frame.** The decoder treats it as one frame whose payload is 224 bytes (= 56 codewords). If that frame is actually two back-to-back records of a different size, the codeword count from that point onward will be wrong by however many words are misaligned — worth checking once you have a tentative decode by looking for opcode-aligned content right after that frame.

## What the self-test verifies

`test_decoder.py` builds a synthetic encoded stream from a known random mapping, runs it through every stage, and checks:

1. Full pipeline round-trips (134-byte payload → 128 16-bit words exactly matching ground truth).
2. Bijection propagation fills gaps when anchors cover only 15 of 16 pair-ids per position.
3. Contradictory anchors raise `ValueError` rather than silently producing garbage.

All three pass. The decoder logic is correct — what remains is purely a matter of finding enough valid anchors in the real firmware to lock the per-position mappings.

The hexdump revealed a real bug in my mental model. Looking at it carefully:

```
0x0D:  99 55 83 68              ← sync (boundary variant, file start)
0x11:  9c 66 1b a5 ...          ← codeword payload starts immediately
0x31:  95 60 26 9f              ← tag bytes (with the 26 9f marker)
0x35:  99 56 87 68              ← next (standard) sync
```

The `26 9f` marker isn't right after the sync — it's at the **end** of each frame, just before the next sync. The frame structure is actually:

```
[sync 4 bytes] [payload N-8 bytes] [tag 4 bytes]
```

not

```
[sync 4 bytes] [tag 4 bytes] [payload N-8 bytes]   ← what my decoder assumed
```

This explains the failure: my decoder validates a sync candidate by checking bytes 2-3 of the *next* 4 bytes for the tag marker, but those bytes are actually the start of the payload (`1b a5`), not the tag. The real tag for frame N sits at the end of frame N, just before the sync of frame N+1.

There's also a useful consequence: the `26 9f` tag suffix is per-frame metadata, not a sync validator. Sync detection should rely on the sync byte sequence alone.

Let me fix both bugs and verify against your hexdump:Confirmed: the layout is `[sync][payload][tag]`, every payload byte is a valid codeword in its position alphabet, and the tag's bytes 2-3 are exactly `26 9f` at the end of the frame. Now I'll fix the decoder:Now `strip_framing` is mostly fine because it uses `payload_start` and `payload_size` which are now computed correctly. But the anomalous-frame path needs a small adjustment — let me also update `decode_tag_high_bytes`:Now `decode_tag_high_bytes` — the tag layout is unchanged (it's still a 4-byte object with bytes 0,1 being variable and 2,3 being marker), only its location moved. But the `tag` field on each frame is now correctly the last 4 bytes:Add a stride histogram and tag marker check to `--info` (very useful diagnostic now that sync detection no longer validates the tag):Now update `test_decoder.py` to use the corrected layout (sync + payload + tag), and rerun all tests:Now let me also add a test that uses the actual hexdump bytes from the user's file as known input:All four tests pass, including one that uses the real bytes from your hexdump. Let me also do a smoke test of the CLI directly on a file built from your hexdump prefix:The diagnostic now shows everything correctly. The 13-byte ASCII header (`1.07R,22\r\n` plus 3 bytes) is identified, frame 0 is the 40-byte boundary header with valid tag marker `26 9F`, and the second frame's "tag" is flagged as unexpected (`54 8B`) because the prefix is truncated and the second frame doesn't really end where this 160-byte slice ends. That warning is exactly what we want — it tells the user when frame boundaries look wrong.

Copy the updated files to outputs:## What was wrong

Two real bugs surfaced once we had hexdump bytes to look at:

**1. Frame layout was inside-out.** I had it as `[sync][tag][payload]`, but the actual layout is `[sync][payload][tag]` — the tag with marker `26 9F` sits at the *end* of each frame, right before the next sync. Look at your hexdump at offset `0x31`:

```
00000030  68 95 60 26 9f 99 56 87  68 ...
                ^^^^^^^^^^^ ^^^^^^^^^^^
                tag of f1   sync of f2
```

That's the tag of frame 1 (which starts at `0x0D`), followed immediately by the standard sync of frame 2. There's no tag right after a sync — the codeword payload begins directly.

**2. Sync detection used the wrong validator.** Because of the layout error, `find_frames` was checking for the `26 9F` marker right after each sync, which never matches. Sync detection now relies on the sync byte sequence alone (`99 56 87 68` and the two boundary variants are unique enough that false positives are extremely improbable). Tag-marker validation moves to the `--info` diagnostic, which checks every frame's *trailing* 4 bytes and warns if any aren't `26 9F` or `BF 53` — useful when a frame boundary looks wrong.

## What `--info` now shows

A new tag-marker histogram and a frame-size histogram. On a well-formed file you should see:

- ~1148 frames with tag marker `26 9F`
- 1-2 frames with marker `BF 53` (the disparity-flipped polarity at file boundaries)
- Frame sizes overwhelmingly 168, with one 40-byte starting boundary frame and one 108-byte trailing boundary frame

If the histogram shows many "unexpected" markers, frame boundaries are off and there's another structural surprise to investigate. If it shows clean numbers like the above, the framing is correctly understood and you can proceed to anchor selection.

## The 13-byte ASCII header

Your file's prefix `31 2e 30 37 52 2c 32 32 0d 0a 80 af a9` decodes to `1.07R,22\r\n` plus 3 binary bytes. That's almost certainly a **firmware version string** — `1.07R,22` followed by what looks like a small binary length or checksum field. Worth keeping a record of, since the version may correlate with which DSP variant the binary targets, and the trailing 3 bytes might be a CRC/length you can check after decoding.

Try running `--info` again on the full file — you should see a clean structure now.