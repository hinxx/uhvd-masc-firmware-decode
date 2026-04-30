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

    Returns (encoded_bytes, expected_words, ground_truth_mapping).
    """
    rng = random.Random(seed)

    # Build a known pair-id -> nibble mapping for each position.
    # (Random bijection in 0..15.)
    mappings = []
    for _ in range(4):
        nibs = list(range(16))
        rng.shuffle(nibs)
        mappings.append({pid: nibs[pid] for pid in range(16)})

    # Inverse: nibble -> sorted list of bytes (two polarity choices).
    nib_to_bytes = []
    for p in range(4):
        alpha = sorted(d.POSITION_PAYLOAD_ALPHABETS[p])
        mask = d.POSITION_MASKS[p]
        # Group bytes by pair-id, then by nibble.
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

    # Generate 3 standard frames + a small "header" anomalous frame at start.
    expected_words = []
    encoded = bytearray()

    # Header: 40-byte anomalous block with 'odd' sync 0x99 0x55 0x83 0x68
    # and a tag.  Payload: 32 bytes of arbitrary codewords (8 words).
    encoded += b"\x99\x55\x83\x68"      # sync
    encoded += b"\x88\x10\xBF\x53"      # tag with disparity-flipped marker
    for _ in range(8):
        w = rng.randint(0, 0xFFFF)
        expected_words.append(w)
        for p in range(4):
            nb = (w >> (12 - 4 * p)) & 0xF
            b0, b1 = nib_to_bytes[p][nb]
            encoded.append(rng.choice([b0, b1]))
    assert len(encoded) == 40

    # Three standard frames of 168 bytes each.
    for frame_idx in range(3):
        encoded += b"\x99\x56\x87\x68"      # sync
        # tag: bytes 0,1 are variable (frame counter); 2,3 = 0x26 0x9F
        tb0_pid = frame_idx % 16
        tb1_pid = (frame_idx * 3) % 16
        tb0 = sorted(d.POSITION_PAYLOAD_ALPHABETS[0])[tb0_pid * 2]
        tb1 = sorted(d.POSITION_PAYLOAD_ALPHABETS[1])[tb1_pid * 2]
        encoded += bytes([tb0, tb1, 0x26, 0x9F])
        for _ in range(40):
            w = rng.randint(0, 0xFFFF)
            expected_words.append(w)
            for p in range(4):
                nb = (w >> (12 - 4 * p)) & 0xF
                b0, b1 = nib_to_bytes[p][nb]
                encoded.append(rng.choice([b0, b1]))

    # Sanity: total expected length
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


if __name__ == "__main__":
    test_full_pipeline()
    print()
    test_partial_anchors_propagation()
    print()
    test_contradictory_anchors()
