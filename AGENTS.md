# DSP56800E Decoder Summary

`dsp56800e_decoder.py` decodes a framed firmware format that stores DSP56800E 16-bit words as 4-byte line-code codewords. The core idea is that each encoded byte position has its own restricted alphabet, and every alphabet is arranged as 16 XOR-pairs. Each pair represents one logical nibble; the choice between the two bytes in a pair is treated as line-code polarity/disparity, not payload data.

The file is parsed as frames. A normal frame is 168 bytes:

```text
[sync 4][FFFF marker 4][metadata 4][payload 152][tag 4]
```

Frame detection uses the constant FFFF marker `9c 66 1b a5` at offset `+4` from a sync byte starting with `0x99`, which is more robust than matching every known sync variant. The decoder strips sync, marker, metadata, and tag, then concatenates only payload codewords. Anomalous frames are recorded and included only when their payload bytes still pass the codeword alphabet check, unless `--only-standard` is used.

Decoding happens in stages:

1. `find_frames()` locates frame boundaries and records field offsets.
2. `strip_framing()` extracts payload bytes and skips non-payload fields.
3. `decode_pair_indices()` maps each 4-byte codeword to four pair IDs.
4. `derive_mappings()` turns pair IDs into nibbles using known-word anchors.
5. `apply_mappings()` assembles the four nibbles into 16-bit output words.

The deterministic part is byte-to-pair-ID conversion. The uncertain part is pair-ID-to-nibble assignment: histograms reveal the 16 pair groups, but not which pair means which nibble. Anchors such as known `0xFFFF` erased-flash runs or known vector-table opcodes pin that bijection. Without anchors, the script can still produce output using frequency heuristics, but it warns that the decoded words are probably wrong.

The CLI supports investigation as well as decoding. `--info` summarizes frame structure and byte distributions, `--find-runs` finds likely anchor candidates, `--check-anchors` validates anchor consistency, and `--trace-decode` prints one line per 4 input bytes showing field type, raw bytes, byte-to-nibble order, pair IDs, nibbles, decoded word, output bytes, and notes such as frame header, data, metadata, or tag.

## running the script

```bash
# Find the new FFFF anchor index (it shifted with the frame structure fix)
python3 dsp56800e_decoder.py firmware.elf.e --skip 13 --find-runs 5
```

```bash
# Build new anchors. Use the longest FFFF run's start index, and add 0x7015.
# The default-handler target codeword is at index 17 (= 6 + 5*2 + 1, for vector 5).
echo '[[<FFFF_INDEX>, "0xFFFF"], [6, "0xE154"], [10, "0xE254"], [17, "0x7015"]]' > anchors.json
```

```bash
# Check consistency
python3 dsp56800e_decoder.py firmware.elf.e --skip 13 \
    --anchors anchors.json --nibble-order 0231 --check-anchors
```

```bash
# Decode
python3 dsp56800e_decoder.py firmware.elf.e --skip 13 \
    --anchors anchors.json --nibble-order 0231 --byte-order little \
    -o decoded.bin
hexdump -Cv decoded.bin | head -100
```
