# DSP56800E Decoder Summary

`dsp56800e_decoder.py` decodes a framed firmware format that stores DSP56800E 16-bit words as 4-byte line-code codewords. The core idea is that each encoded byte position has its own restricted alphabet, and every alphabet is arranged as 16 XOR-pairs. Each pair represents one logical nibble; the choice between the two bytes in a pair is treated as line-code polarity/disparity, not payload data.

The file is parsed as frames. A normal frame is 168 bytes:

```text
[sync 4][FFFF marker 4][metadata 4][payload 152][tag 4]
```

Frame detection uses the constant FFFF marker `9c 66 1b a5` at offset `+4` from a sync byte starting with `0x99`, which is more robust than matching every known sync variant. The decoder strips sync, marker, metadata/counter, and tag, then concatenates only payload codewords. Anomalous frames are recorded and included only when their payload bytes still pass the codeword alphabet check, unless `--only-standard` is used.

Decoding happens in stages:

1. `find_frames()` locates frame boundaries and records field offsets.
2. `strip_framing()` extracts payload bytes and skips non-payload fields.
3. `decode_pair_indices()` maps each 4-byte codeword to four pair IDs.
4. `derive_mappings()` turns pair IDs into nibbles using known-word anchors.
5. `apply_mappings()` assembles the four nibbles into 16-bit output words.

The deterministic part is byte-to-pair-ID conversion. The uncertain part is pair-ID-to-nibble assignment: histograms reveal the 16 pair groups, but not which pair means which nibble. Anchors such as known `0xFFFF` erased-flash runs or known vector-table opcodes pin that bijection. Without anchors, the script can still produce output using frequency heuristics, but it warns that the decoded words are probably wrong.

## line-code alphabets and masks

`POSITION_PAYLOAD_ALPHABETS` is the payload byte whitelist for the four byte positions inside each 4-byte codeword. Position 0, 1, 2, and 3 each have a different set of 32 legal byte values; a payload byte is interpreted using the alphabet for its position modulo 4. This is why `strip_framing()` can reject anomalous frame bytes that are not real encoded payload, and why `decode_pair_indices()` raises an illegal-codeword error when any byte is outside the expected position alphabet.

`POSITION_MASKS = (0x66, 0x33, 0x99, 0xCC)` gives the XOR partner rule for those same positions. For any legal payload byte `b`, `b ^ POSITION_MASKS[pos]` must also be legal in that position's alphabet. Those two bytes are the same logical symbol with opposite line-code polarity/disparity. `_build_pair_index()` walks each alphabet, verifies that it is closed under the position mask, and collapses the 32 bytes into 16 pair IDs.

The pair ID is deterministic but not yet the final nibble value. For example, if two bytes differ only by the position mask, they get the same pair ID, but deciding whether that pair ID means nibble `0x0`, `0xF`, or any other value is the later anchor-derived mapping step. The numeric pair IDs are implementation labels assigned by sorted alphabet order; they are useful for consistency checks and anchors, not intrinsic DSP nibble values.

The alphabets were first named for payload decoding, but the sync/header, FFFF marker, and metadata/counter fields also use bytes from the same per-position alphabets. Framing fields are still handled separately from firmware payload: the constant FFFF marker and metadata/counter are stripped before payload decoding, and tag suffix bytes are recognized through `VALID_TAG_SUFFIXES` rather than treated as normal payload.

## frame-header codewords and counter finding

The 4-byte sync/header word is also in the same per-position codeword alphabet. In `frames.txt`, the observed sync variants decode to pair IDs that correlate with frame payload length when bytes 3 and 4 are treated as the high/low nibbles of a payload word count:

```text
99 56 87 68 -> 0x26 = 38 payload words = normal 168-byte frame
99 55 83 68 -> 0x06 = 6 payload words = initial ID/boundary frame
99 56 83 1d -> 0x0c = 12 payload words = short 64-byte frame
99 56 83 68 -> 0x06 = 6 payload words = short 40-byte frame
```

Other length interpretations, such as encoded frame bytes, decoded total bytes, payload encoded bytes, or payload decoded bytes, produce pair-ID mapping contradictions. This makes "payload word count" the current best interpretation for sync bytes 3..4. Byte 2 still appears to distinguish the first non-data/ID frame (`0x55`) from data frames (`0x56`).

Metadata bytes 9..12 are also valid codeword bytes and form a strong counter/address pattern. With a separate metadata/header bijection and `0123` nibble order, the metadata field is consistent as a 16-bit count-up word offset. The initial ID frame is excluded from this offset stream. The first valid data frame has metadata `fa 55 82 69` and decodes as offset `0x0000`; the following valid data frames decode as `0x0026`, `0x004C`, `0x0072`, `0x0098`, then `0x00A4` after the short 12-word frame, and so on. Normal frames advance by 38 payload words (`0x26`), short valid data frames advance by their decoded payload word count. The final non-payload terminator frame is excluded; the last valid data frame starts at `0xAA28`, contains 6 words, and ends at final offset `0xAA2E`.

Use `analyze_frame_headers.py` to reproduce this:

```bash
python3 analyze_frame_headers.py frames.txt
python3 generate_counter_bijection.py firmware-no-header.elf.e > counter_bijection.json
```

The count-up metadata bijection is also the strongest whole-file payload decode candidate found so far. Using `counter_bijection_extrapolated.json` as direct pair-ID pins for the payload decoder, with `--nibble-order 0123` and no `anchors.json`, produces plausible decoded output. The first decoded bytes are ASCII `PROGRAM&DATAT`, and later decoded data contains two-letter ASCII host command mnemonics matching the command table in `docs/Host-Command-Reference_920-0002V.pdf`, such as `AC`, `AD`, `AF`, `AG`, `AI`, `AM`, `AO`, `AP`, `AS`, `AT`, `AV`, `BD`, `BE`, `BO`, `BR`, `CC`, `CD`, `CF`, `CG`, `CI`, `CM`, `CS`, `DA`, `DB`, `DE`, `DF`, `DL`, `DM`, `DR`, `ED`, `EF`, `EI`, `ES`, `FI`, `FX`, `GC`, `GD`, `GI`, `GL`, `GP`, `GS`, and `GV`.

Important caveat: `counter_bijection.json` contains only observed metadata pins, while `counter_bijection_extrapolated.json` fills the missing pair IDs by pattern extrapolation. The extrapolated file is the one to use for whole-payload decode tests. `anchors.json` reflects an older payload-anchor route and conflicts with the count-up bijection, so do not combine it with `counter_bijection_extrapolated.json` unless intentionally investigating the contradiction.

The CLI supports investigation as well as decoding. `--info` summarizes frame structure and byte distributions, `--find-runs` finds likely anchor candidates, `--check-anchors` validates anchor consistency, and `--trace-decode` prints one line per 4 input bytes showing field type, raw bytes, byte-to-nibble order, pair IDs, nibbles, decoded word, output bytes, and notes such as frame header, data, metadata, or tag.

## running the script

```bash
# Decode using the count-up bijection candidate.
# dsp56800e_decoder_2.py supports --bijection-json direct pins.
python3 dsp56800e_decoder_2.py firmware.elf.e --skip 13 \
    --bijection-json counter_bijection_extrapolated.json \
    --nibble-order 0123 \
    -o decoded_counter.bin
```

```bash
hexdump -Cv decoded_counter.bin | head -100
hexdump -Cv decoded_counter.bin | rg '41 43|41 44|41 46|47 56'
```
