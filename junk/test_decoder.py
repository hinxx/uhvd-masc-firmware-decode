#!/usr/bin/env python3
"""Self-test for dsp56800e_decoder using synthetic data.

Builds a tiny encoded stream from a chosen pair-id->nibble mapping,
runs it through the decoder, and checks every stage round-trips.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dsp56800e_decoder as d


def build_test_stream(seed: int = 0) -> tuple[bytes, list[int], dict]:
    """Synthesize an encoded stream with a known mapping & content.

    Frame layout: [sync 4][payload N-8][tag 4].

    Returns (encoded_bytes, expected_words, ground_truth_mapping).
    """
    rng = random.Random(seed)

    # Build a known pair-id -> nibble mapping for each position.
    mappings = []
    for _ in range(4):
        nibs = list(range(16))
        rng.shuffle(nibs)
        mappings.append({pid: nibs[pid] for pid in range(16)})

    # Inverse: nibble -> (byte, partner_byte) at each position.
    nib_to_bytes = []
    for p in range(4):
        alpha = sorted(d.POSITION_PAYLOAD_ALPHABETS[p])
        mask = d.POSITION_MASKS[p]
        nib_map = {}
        seen = set()
        for b in alpha:
            if b in seen:
                continue
            partner = b ^ mask
            seen |= {b, partner}
            pid = d.PAIR_INDEX[p][b]
            nib_map[mappings[p][pid]] = (b, partner)
        nib_to_bytes.append(nib_map)

    def encode_word(w: int) -> bytes:
        out = bytearray(4)
        for p in range(4):
            nb = (w >> (12 - 4 * p)) & 0xF
            b0, b1 = nib_to_bytes[p][nb]
            out[p] = rng.choice([b0, b1])
        return bytes(out)

    expected_words = []
    encoded = bytearray()

    # Frame 1: 40-byte boundary header.
    #   sync (4) + 8 codeword payload (32) + tag (4) = 40
    encoded += b"\x99\x55\x83\x68"          # boundary sync
    for _ in range(8):
        w = rng.randint(0, 0xFFFF)
        expected_words.append(w)
        encoded += encode_word(w)
    # Tag bytes 0,1 = arbitrary in pos-0/pos-1 alphabet; bytes 2,3 = marker.
    encoded += bytes([0x88, 0x10, 0x26, 0x9F])
    assert len(encoded) == 40

    # Frames 2-4: standard 168-byte frames.
    #   sync (4) + 40 codeword payload (160) + tag (4) = 168
    for fi in range(3):
        encoded += b"\x99\x56\x87\x68"      # standard sync
        for _ in range(40):
            w = rng.randint(0, 0xFFFF)
            expected_words.append(w)
            encoded += encode_word(w)
        # Variable tag bytes for frame counter; fixed marker.
        b0 = sorted(d.POSITION_PAYLOAD_ALPHABETS[0])[fi % 32]
        b1 = sorted(d.POSITION_PAYLOAD_ALPHABETS[1])[(fi * 3) % 32]
        encoded += bytes([b0, b1, 0x26, 0x9F])

    assert len(encoded) == 40 + 3 * 168
    return bytes(encoded), expected_words, mappings


def test_full_pipeline():
    encoded, expected_words, gt_map = build_test_stream(seed=42)
    print(f"synthesized {len(encoded)} bytes, "
          f"{len(expected_words)} expected words")

    # Stage 1: find frames.
    frames = d.find_frames(encoded)
    print(f"  frames: {len(frames)} "
          f"(sizes: {[f['frame_size'] for f in frames]})")
    assert len(frames) == 4
    assert frames[0]["frame_size"] == 40
    assert frames[0]["is_standard"] is False
    for f in frames[1:]:
        assert f["is_standard"] is True

    # Stage 2: strip framing.
    payload, frames, anomalous = d.strip_framing(encoded, frames)
    expected_payload = 32 + 3 * 160
    assert len(payload) == expected_payload, \
        f"got {len(payload)}, expected {expected_payload}"
    assert len(anomalous) == 1
    print(f"  payload after strip: {len(payload)} bytes "
          f"({len(payload)//4} codewords)")

    # Stage 3: pair indices.
    rows = d.decode_pair_indices(payload)
    assert len(rows) == len(expected_words)
    print(f"  decoded {len(rows)} pair-id rows")

    # Stage 4: derive mappings using anchors.
    # Use 4 well-chosen anchors that pin all 16 nibbles at every position.
    # Pick codewords whose 4 nibbles together cover many distinct pair-ids.
    anchors = []
    seen_pids = [set(), set(), set(), set()]
    for cw_idx, w in enumerate(expected_words):
        useful = False
        for p in range(4):
            if rows[cw_idx][p] not in seen_pids[p]:
                useful = True
                break
        if useful:
            anchors.append((cw_idx, w))
            for p in range(4):
                seen_pids[p].add(rows[cw_idx][p])
        if all(len(s) == 16 for s in seen_pids):
            break
    print(f"  using {len(anchors)} anchors to pin all nibbles")

    derived = d.derive_mappings(rows, anchors)

    # Verify derived mapping == ground truth.
    for p in range(4):
        for pid in range(16):
            assert derived[p][pid] == gt_map[p][pid], \
                f"pos {p} pid {pid}: got {derived[p][pid]:X}, " \
                f"expected {gt_map[p][pid]:X}"
    print("  mappings match ground truth at all 4 positions")

    # Stage 5: apply mappings.
    decoded = d.apply_mappings(rows, derived)
    assert decoded == expected_words, \
        f"first mismatch at index " \
        f"{next(i for i,(a,b) in enumerate(zip(decoded,expected_words)) if a!=b)}"
    print(f"  all {len(decoded)} decoded words match ground truth")

    print("\nFULL PIPELINE: OK")


def test_partial_anchors_propagation():
    """Test that bijection propagation fills in the gaps when anchors
    cover only some pair-ids."""
    encoded, expected_words, gt_map = build_test_stream(seed=7)
    payload, _, _ = d.strip_framing(encoded)
    rows = d.decode_pair_indices(payload)

    # Provide anchors that pin 15 of 16 pair-ids; the 16th must be
    # filled by uniqueness propagation.
    anchors = []
    seen_pids = [set(), set(), set(), set()]
    for cw_idx, w in enumerate(expected_words):
        useful = any(rows[cw_idx][p] not in seen_pids[p] for p in range(4))
        if useful:
            anchors.append((cw_idx, w))
            for p in range(4):
                seen_pids[p].add(rows[cw_idx][p])
        if all(len(s) >= 15 for s in seen_pids):
            break
    print(f"  partial anchors: pid coverage = "
          f"{[len(s) for s in seen_pids]}")

    derived = d.derive_mappings(rows, anchors)
    for p in range(4):
        for pid in range(16):
            assert derived[p][pid] == gt_map[p][pid], \
                f"propagation failed at pos {p} pid {pid}"
    print("  bijection propagation completed mapping correctly")
    print("\nPARTIAL-ANCHOR PROPAGATION: OK")


def test_contradictory_anchors():
    """Anchors that disagree should raise."""
    encoded, expected_words, _ = build_test_stream(seed=1)
    payload, _, _ = d.strip_framing(encoded)
    rows = d.decode_pair_indices(payload)

    # Anchor 0 to a wrong value contradicting anchor 0 to its real value.
    bad = [(0, expected_words[0]), (0, expected_words[0] ^ 0x000F)]
    try:
        d.derive_mappings(rows, bad)
    except ValueError as e:
        print(f"  raised as expected: {e}")
    else:
        raise AssertionError("expected ValueError on contradictory anchors")
    print("\nCONTRADICTORY-ANCHOR DETECTION: OK")


def test_with_real_file_prefix():
    """Verify the decoder handles the real firmware.elf.e file prefix
    (first 160 bytes from the hexdump the user provided)."""
    hex_text = """
    31 2e 30 37 52 2c 32 32  0d 0a 80 af a9 99 55 83
    68 9c 66 1b a5 fa 55 82  69 99 66 1e a7 fe 23 86
    6e 99 64 1f a4 fe 21 80  6f 98 62 1f a4 ff 51 86
    68 95 60 26 9f 99 56 87  68 9c 66 1b a5 fa 55 82
    69 99 62 6e a4 8b 51 82  69 99 62 6e a4 8b 51 82
    69 99 62 6e a7 8e 50 8b  6d 99 62 6e a7 8e 54 8b
    6d 99 62 6e a7 8e 5c 8b  6d 99 62 6e a7 8e 21 8b
    6d 99 62 6e a7 8e 54 8b  6d 99 62 6e a7 8e 54 8b
    6d 99 62 6e a7 8e 54 8b  6d 99 62 6e a7 8e 54 8b
    6d 99 62 6e a7 8e 54 8b  6d 99 62 6e a7 8e 54 8b
    """
    real_data = bytes(int(x, 16) for x in hex_text.split())
    print(f"  real-file prefix: {len(real_data)} bytes")

    frames = d.find_frames(real_data)
    print(f"  frames found: {len(frames)} "
          f"(starts: {[hex(f['start']) for f in frames]})")
    assert len(frames) >= 2, "expected at least 2 syncs in the prefix"

    # First frame is the boundary header at offset 13.
    f1 = frames[0]
    assert f1["start"] == 0x0D, f"got start 0x{f1['start']:X}, expected 0xD"
    assert f1["sync"] == b"\x99\x55\x83\x68"
    assert f1["frame_size"] == 40
    assert f1["tag"] == bytes([0x95, 0x60, 0x26, 0x9F]), \
        f"unexpected tag: {f1['tag'].hex()}"
    assert f1["payload_size"] == 32
    print(f"  frame 0: 40 B boundary frame, tag={f1['tag'].hex()} "
          f"(marker 26 9f) - OK")

    # Second frame is a standard 168-byte frame, but our prefix is only
    # 160 bytes long so the frame appears truncated; still, its sync is
    # valid and its start is correct.
    f2 = frames[1]
    assert f2["sync"] == b"\x99\x56\x87\x68"
    assert f2["start"] == 0x35
    print(f"  frame 1: standard sync at 0x35 - OK")

    # Validate every payload byte of frame 1 is in its position alphabet.
    p_start = f1["payload_start"]
    p_end = p_start + f1["payload_size"]
    payload = real_data[p_start:p_end]
    for i, b in enumerate(payload):
        assert b in d.PAIR_INDEX[i % 4], \
            f"frame 1 payload byte {i} (0x{b:02X}) not in pos {i%4} alphabet"
    print(f"  frame 1 payload ({len(payload)} bytes): all in valid alphabets")

    # And we can decode pair-ids without error.
    rows = d.decode_pair_indices(payload)
    print(f"  decoded {len(rows)} pair-id rows")
    print(f"  first row: {rows[0]}  (each value is 0..15)")

    print("\nREAL-FILE-PREFIX HANDLING: OK")


if __name__ == "__main__":
    test_full_pipeline()
    print()
    test_partial_anchors_propagation()
    print()
    test_contradictory_anchors()
    print()
    test_with_real_file_prefix()
