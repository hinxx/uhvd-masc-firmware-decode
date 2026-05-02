#!/usr/bin/env python3
"""
DSP56800E firmware decoder for the 4b/8b line-coded, 168-byte-framed format.

File format (verified from real frame data):
    Stream of frames, each structured as:
        [sync 4][FFFF-marker 4][metadata 4][payload 152][tag 4] = 168 bytes
    where:
        sync         = one of {99 56 87 68, 99 55 83 68, 99 56 83 68}
        FFFF-marker  = constant 9c 66 1b a5 (one codeword, decodes to 0xFFFF)
        metadata     = 4 bytes that are NOT in the codeword alphabet;
                       likely a destination flash address or frame counter
        payload      = 38 codewords = 38 16-bit DSP words = 76 bytes of
                       decoded program data
        tag          = ?? ?? 26 9F  (or BF 53 with disparity flip);
                       last 4 bytes of frame; bytes 0,1 are variable
                       (possibly checksum or sub-frame counter)

    Most frames are standard 168 bytes.  A few are anomalous: the first
    frame is typically 40 bytes (starting boundary header), the last is
    often truncated, and occasional mid-file 232-byte frames may occur.
    A small leading file header (e.g., a version string) may precede
    the first sync; the decoder skips over it automatically.

Encoding:
    Each 4-byte payload codeword carries one 16-bit DSP word using a
    4b/8b line code with running-disparity polarity.  Per byte
    position, the alphabet has 32 valid values forming 16 XOR-paired
    groups:
        pos 0 mask 0x66, pos 1 mask 0x33,
        pos 2 mask 0x99, pos 3 mask 0xCC.
    Each XOR pair represents one logical nibble (0..F); the encoder
    picks polarity to balance the running 0/1 disparity.

Decoding pipeline:
    1. find_frames        — locate every sync, parse frame fields.
    2. strip_framing      — concatenate payload bytes (drop sync,
                            FFFF-marker, metadata, and tag from each).
    3. decode_pair_indices — bytes → pair-id (0..15) tuples.
    4. derive_mappings    — pair-id → nibble (needs anchors).
    5. apply_mappings     — produce final 16-bit words.

What is fully known:
    Frame structure, sync bytes, the 32-byte codeword alphabet at
    every byte position, and the XOR-pair structure.  Stages 1–3 are
    deterministic and need no extra information.

What requires anchors:
    The bijection pair-id → nibble at each of the four positions
    cannot be determined from the histogram alone.  Pass anchors
    (codeword-index, expected-word) to derive_mappings.  Reliable
    anchors include 0xFFFF (erased flash, found via --find-runs)
    and the JMP/JSR opcodes 0xE154/0xE254 at the start of the
    vector table (see DSP56800E reference manual).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence


# ============================================================================
# Frame structure constants
# ============================================================================

SYNC_LEN = 4
FFFF_MARKER_LEN = 4    # The constant 4-byte FFFF codeword (9c 66 1b a5)
METADATA_LEN = 4       # 4 bytes of frame metadata (not a codeword)
TAG_LEN = 4
FRAMING = SYNC_LEN + FFFF_MARKER_LEN + METADATA_LEN + TAG_LEN  # 16 bytes
STD_FRAME_SIZE = 168
STD_PAYLOAD_SIZE = STD_FRAME_SIZE - FRAMING  # 152 bytes = 38 codewords

# The FFFF marker bytes (constant for all frames)
FFFF_MARKER = b"\x9c\x66\x1b\xa5"

# Sync values observed across the analysed firmware files.  This set is
# kept for informational purposes only — frame detection now uses the
# constant FFFF_MARKER at +4 from the sync, which matches every variant.
VALID_SYNCS = {
    b"\x99\x56\x87\x68",  # standard / interior
    b"\x99\x55\x83\x68",  # boundary variant — observed at file start
    b"\x99\x56\x83\x68",  # boundary variant — observed at file end
    b"\x99\x56\x83\x1D",  # mid-file variant — observed in custom firmware
}

# Bytes 2 and 3 of the tag word are fixed markers (with disparity flip).
VALID_TAG_SUFFIXES = {(0x26, 0x9F), (0xBF, 0x53)}


# ============================================================================
# 4b/8b alphabet (32 values per position, 16 XOR-pairs)
# ============================================================================

POSITION_MASKS = (0x66, 0x33, 0x99, 0xCC)

POSITION_PAYLOAD_ALPHABETS = (
    # pos 0  — XOR mask 0x66
    {0x88, 0x89, 0x8B, 0x8C, 0x8E, 0x8F, 0x94, 0x95,
     0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E, 0x9F,
     0xE8, 0xE9, 0xEA, 0xED, 0xEE, 0xEF, 0xF2, 0xF3,
     0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF},
    # pos 1  — XOR mask 0x33
    {0x10, 0x12, 0x13, 0x14, 0x15, 0x17, 0x20, 0x21,
     0x23, 0x24, 0x26, 0x27, 0x50, 0x51, 0x52, 0x53,
     0x54, 0x55, 0x56, 0x57, 0x5C, 0x5D, 0x60, 0x61,
     0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x6E, 0x6F},
    # pos 2  — XOR mask 0x99   (tag pair {0x26, 0xBF} excluded)
    {0x12, 0x13, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D,
     0x1E, 0x1F, 0x68, 0x69, 0x6A, 0x6D, 0x6E, 0x6F,
     0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
     0x8A, 0x8B, 0xF0, 0xF1, 0xF3, 0xF4, 0xF6, 0xF7},
    # pos 3  — XOR mask 0xCC   (tag pair {0x9F, 0x53} excluded)
    {0x18, 0x1A, 0x1B, 0x1C, 0x1D, 0x1F, 0x60, 0x61,
     0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F,
     0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7,
     0xAC, 0xAD, 0xD0, 0xD1, 0xD3, 0xD4, 0xD6, 0xD7},
)


def _build_pair_index(alphabet: set, mask: int) -> dict:
    """Map every byte in `alphabet` to a pair-id 0..15.
    Both members of an XOR pair share the same id."""
    pair = {}
    next_id = 0
    for b in sorted(alphabet):
        if b in pair:
            continue
        partner = b ^ mask
        if partner not in alphabet:
            raise ValueError(
                f"alphabet not closed under XOR 0x{mask:02X}: 0x{b:02X}")
        pair[b] = next_id
        pair[partner] = next_id
        next_id += 1
    if next_id != 16:
        raise ValueError(f"expected 16 pairs, got {next_id}")
    return pair


PAIR_INDEX = tuple(
    _build_pair_index(a, m)
    for a, m in zip(POSITION_PAYLOAD_ALPHABETS, POSITION_MASKS)
)


# ============================================================================
# Stage 1 — Frame discovery
# ============================================================================

def find_frames(data: bytes,
                start_offset: int = 0,
                end_offset: int | None = None,
                ) -> list[dict]:
    """Locate every frame in `data` by scanning for the FFFF-marker.

    Frame structure: [sync 4][FFFF-marker 4][metadata 4][payload N-16][tag 4].

    The frame's sync (bytes 0..3) varies — observed values include
    99 55 83 68, 99 56 87 68, 99 56 83 68, and 99 56 83 1D — and likely
    encode some frame-type flag.  The FFFF-marker, however, is the
    constant 4-byte sequence 9c 66 1b a5 at offset +4 from every sync.
    Detecting frames via the marker is far more robust than enumerating
    every legal sync variant.

    Args:
        data: full file bytes.
        start_offset: do not look for syncs before this offset.
        end_offset: do not look for syncs at or after this offset.
            Defaults to len(data).

    Returns:
        Frames in file order.  Each frame's `frame_size` is the
        distance to the next sync (or to `end_offset` for the final
        frame).  Each frame includes pre-computed `payload_start`,
        `payload_size`, `ffff_marker`, `metadata`, and `tag`.
    """
    if end_offset is None:
        end_offset = len(data)

    # A frame begins at byte B where:
    #   data[B] == 0x99
    #   data[B+4 : B+8] == FFFF_MARKER (= 9c 66 1b a5)
    # The 0x99 prefix on the sync gives us a quick reject; the FFFF marker
    # confirms.  This catches every sync variant without enumerating them.
    sync_positions: list[int] = []
    pos = start_offset
    last_search = end_offset - SYNC_LEN - FFFF_MARKER_LEN + 1
    while pos < last_search:
        if (data[pos] == 0x99 and
                bytes(data[pos + SYNC_LEN
                            :pos + SYNC_LEN + FFFF_MARKER_LEN]) == FFFF_MARKER):
            sync_positions.append(pos)
            pos += SYNC_LEN + FFFF_MARKER_LEN
            continue
        pos += 1

    if not sync_positions:
        raise ValueError(
            "no frames found; expected the constant FFFF marker "
            "9c 66 1b a5 at offset +4 from each sync, with sync byte 0 = 0x99")

    frames = []
    for i, start in enumerate(sync_positions):
        end = (sync_positions[i + 1] if i + 1 < len(sync_positions)
               else end_offset)
        size = end - start
        sync = bytes(data[start:start + SYNC_LEN])
        # New layout (verified from real frame data):
        #   [sync 4][FFFF-marker 4][metadata 4][payload N-16][tag 4]
        # The FFFF-marker is the constant 4-byte sequence 9c 66 1b a5 (= one
        # codeword whose pair-ids decode to 0xFFFF).  The metadata is 4 bytes
        # NOT in the codeword alphabet — likely the destination flash address
        # or a frame counter.  Tag occupies the last 4 bytes of the frame.
        if size >= SYNC_LEN + FFFF_MARKER_LEN + METADATA_LEN + TAG_LEN:
            ffff_marker = bytes(data[start + SYNC_LEN
                                     :start + SYNC_LEN + FFFF_MARKER_LEN])
            metadata = bytes(data[start + SYNC_LEN + FFFF_MARKER_LEN
                                  :start + SYNC_LEN + FFFF_MARKER_LEN + METADATA_LEN])
            payload_start = start + SYNC_LEN + FFFF_MARKER_LEN + METADATA_LEN
            payload_size = size - (SYNC_LEN + FFFF_MARKER_LEN + METADATA_LEN
                                   + TAG_LEN)
            tag = bytes(data[end - TAG_LEN:end])
        else:
            # Frame too short for the full structure; treat conservatively.
            ffff_marker = b""
            metadata = b""
            payload_start = start + SYNC_LEN
            payload_size = max(0, size - SYNC_LEN)
            tag = b""
        frames.append({
            "index": i,
            "start": start,
            "end": end,
            "frame_size": size,
            "sync": sync,
            "ffff_marker": ffff_marker,
            "metadata": metadata,
            "tag": tag,
            "payload_start": payload_start,
            "payload_size": payload_size,
            "is_standard": (size == STD_FRAME_SIZE
                            and sync == b"\x99\x56\x87\x68"),
        })
    return frames


# ============================================================================
# Stage 2 — Framing strip
# ============================================================================

def strip_framing(data: bytes,
                  frames: list[dict] | None = None,
                  only_standard: bool = False,
                  drop_leading_per_frame: int = 0,
                  ) -> tuple[bytes, list[dict], list[dict]]:
    """Concatenate the payload bytes of every frame, with framing
    (sync + tag) removed.

    Args:
        data: the encoded firmware.
        frames: optional pre-computed frame list; defaults to find_frames(data).
        only_standard: if True, only standard 168-byte frames contribute
            to the returned payload; anomalous frames are skipped (still
            returned in `anomalous_blocks`).
        drop_leading_per_frame: number of bytes (must be a multiple of 4)
            to drop from the start of each STANDARD frame's payload.
            This is useful when each frame begins with a fixed number
            of padding/header codewords that are not part of the
            decoded program memory image.  For this format, set to 8
            to drop the 2 leading 0xFFFF padding words per frame.
            Anomalous frames are passed through verbatim regardless.

    Returns:
        (payload_bytes, frames, anomalous_blocks)

        payload_bytes is a multiple of 4 (whole codewords).  Anomalous
        frames are included only if their bytes pass the per-position
        alphabet check (i.e. they really are encoded codewords); raw
        bytes of every anomalous frame are also returned for inspection.
    """
    if frames is None:
        frames = find_frames(data)

    if drop_leading_per_frame % 4 != 0:
        raise ValueError("drop_leading_per_frame must be a multiple of 4")

    payload = bytearray()
    anomalous_blocks = []

    for f in frames:
        p_start = f["payload_start"]
        p_size = f["payload_size"]
        if p_size <= 0:
            continue

        # Truncate to a whole-codeword multiple.
        used = (p_size // 4) * 4
        pbytes = bytes(data[p_start:p_start + used])

        if not f["is_standard"]:
            anomalous_blocks.append({
                "frame_index": f["index"],
                "frame_size": f["frame_size"],
                "sync": f["sync"].hex(),
                "tag": f["tag"].hex() if f["tag"] else "(none)",
                "payload_raw": bytes(data[p_start:p_start + p_size]),
            })
            if only_standard:
                continue
            # Validate alphabet — anomalous frames may carry non-codeword data.
            ok = all(pbytes[k] in PAIR_INDEX[k % 4] for k in range(used))
            if not ok:
                # Skip; data isn't 4b/8b encoded.
                continue
            # Anomalous frames pass through verbatim — no leading drop.
            payload.extend(pbytes)
        else:
            # Standard frame: optionally drop leading codewords.
            drop = min(drop_leading_per_frame, used)
            payload.extend(pbytes[drop:])

    return bytes(payload), frames, anomalous_blocks


# ============================================================================
# Stage 3 — bytes → pair indices
# ============================================================================

def decode_pair_indices(payload: bytes) -> list[tuple[int, int, int, int]]:
    """Convert payload bytes (multiple of 4) to a list of
    (pid0, pid1, pid2, pid3) tuples — each pid in 0..15."""
    if len(payload) % 4 != 0:
        raise ValueError(
            f"payload length {len(payload)} not multiple of 4")
    rows = []
    for i in range(0, len(payload), 4):
        try:
            rows.append((
                PAIR_INDEX[0][payload[i]],
                PAIR_INDEX[1][payload[i + 1]],
                PAIR_INDEX[2][payload[i + 2]],
                PAIR_INDEX[3][payload[i + 3]],
            ))
        except KeyError:
            raise ValueError(
                f"illegal codeword at byte offset {i}: "
                f"{payload[i:i+4].hex()}")
    return rows


# ============================================================================
# Stage 4 — derive pair-id → nibble mapping from anchors
# ============================================================================

# Heuristic frequency rank for unconstrained nibbles in DSP firmware.
# 0xF dominates (sign-extension, erased flash, immediate -1); 0x0 is
# common (small constants, MSBs of low values).  The remainder is a
# rough guess used only as a tie-break when no anchor pins the value.
DEFAULT_NIBBLE_RANK = (0xF, 0x0, 0x1, 0x2, 0x4, 0x8, 0x6, 0xE,
                       0x3, 0x5, 0x7, 0xA, 0x9, 0xC, 0xB, 0xD)


def derive_mappings(pair_rows: Sequence[tuple[int, int, int, int]],
                    anchors: Iterable[tuple[int, int]] = (),
                    nibble_order: tuple[int, int, int, int] = (0, 1, 2, 3),
                    rank: Sequence[int] = DEFAULT_NIBBLE_RANK,
                    ) -> tuple[dict, dict, dict, dict]:
    """Derive per-position pair-id → nibble mappings from anchors.

    Args:
        pair_rows: output of decode_pair_indices().
        anchors: iterable of (codeword_index, expected_16bit_word_value).
            Each anchor pins the four nibbles of the expected word at
            their respective byte positions.
        nibble_order: which nibble of the 16-bit output word each byte
            position carries.  (0,1,2,3) means pos 0 → bits 15:12,
            pos 1 → 11:8, pos 2 → 7:4, pos 3 → 3:0  (big-endian within
            the word).  Use (3,2,1,0) for little-endian.
        rank: tie-break order for nibbles not pinned by any anchor.

    Returns:
        Tuple of 4 dicts (one per byte position), each mapping
        pair-id (0..15) → nibble (0..15).

    Raises:
        ValueError on contradictory or infeasible anchors.
    """
    n = len(pair_rows)
    cands = [{pid: set(range(16)) for pid in range(16)} for _ in range(4)]

    # Apply anchors.
    for cw, expected in anchors:
        if not (0 <= cw < n):
            raise IndexError(f"anchor codeword index {cw} out of range")
        nibs = [(expected >> (12 - 4 * nibble_order[p])) & 0xF
                for p in range(4)]
        for p in range(4):
            pid = pair_rows[cw][p]
            cands[p][pid] &= {nibs[p]}
            if not cands[p][pid]:
                raise ValueError(
                    f"contradictory anchors at codeword {cw} position {p}")

    # Bijection propagation: each nibble must appear in exactly one pair
    # at each position.
    changed = True
    while changed:
        changed = False
        for p in range(4):
            for pid, vs in cands[p].items():
                if len(vs) == 1:
                    locked = next(iter(vs))
                    for opid, ovs in cands[p].items():
                        if opid != pid and locked in ovs:
                            ovs.discard(locked)
                            if not ovs:
                                raise ValueError(
                                    f"no valid nibble for pair {opid} at "
                                    f"position {p} after propagation")
                            changed = True

    # Frequency-based fallback for unresolved pairs.
    pos_freq = [Counter() for _ in range(4)]
    for r in pair_rows:
        for p in range(4):
            pos_freq[p][r[p]] += 1

    out = []
    for p in range(4):
        m = {pid: next(iter(vs)) for pid, vs in cands[p].items()
             if len(vs) == 1}
        used = set(m.values())
        unresolved = sorted(
            (pid for pid in range(16) if pid not in m),
            key=lambda x: -pos_freq[p][x])
        free_nibs = [n for n in rank if n not in used]
        for pid, nb in zip(unresolved, free_nibs):
            m[pid] = nb
        out.append(m)
    return tuple(out)


# ============================================================================
# Stage 5 — pair-rows + mappings → 16-bit words
# ============================================================================

def apply_mappings(pair_rows: Sequence[tuple[int, int, int, int]],
                   mappings: Sequence[dict],
                   nibble_order: tuple[int, int, int, int] = (0, 1, 2, 3),
                   ) -> list[int]:
    """Produce 16-bit word values from pair indices and mappings."""
    shifts = [12 - 4 * nibble_order[p] for p in range(4)]
    return [
        sum(mappings[p][r[p]] << shifts[p] for p in range(4))
        for r in pair_rows
    ]


def find_constant_runs(pair_rows: Sequence[tuple[int, int, int, int]],
                       min_length: int = 4,
                       top_n: int = 20,
                       ) -> list[dict]:
    """Find the longest runs of identical codewords in the pair-id
    stream.  Long runs strongly suggest erased-flash padding (0xFFFF)
    or zero-initialized data (0x0000); both make excellent anchors.

    Returns up to `top_n` runs sorted by length, each as a dict:
        {"start": codeword index, "length": run length,
         "pair_ids": (pid0, pid1, pid2, pid3)}
    """
    runs = []
    n = len(pair_rows)
    i = 0
    while i < n:
        j = i
        cw = pair_rows[i]
        while j < n and pair_rows[j] == cw:
            j += 1
        run_len = j - i
        if run_len >= min_length:
            runs.append({
                "start": i,
                "length": run_len,
                "pair_ids": cw,
            })
        i = j
    runs.sort(key=lambda r: (-r["length"], r["start"]))
    return runs[:top_n]


def codeword_frequency(pair_rows: Sequence[tuple[int, int, int, int]],
                       top_n: int = 20,
                       ) -> list[tuple[tuple[int, int, int, int], int]]:
    """Return the most common codewords in the pair-id stream."""
    return Counter(pair_rows).most_common(top_n)


def inspect_codewords(pair_rows: Sequence[tuple[int, int, int, int]],
                      indices: Iterable[int],
                      context: int = 4,
                      ) -> list[dict]:
    """Show pair-ids around given codeword indices, with context.

    Useful for examining the decoded structure at known anchor
    locations (e.g., near the end of program flash where the
    configuration field lives).
    """
    out = []
    for idx in indices:
        start = max(0, idx - context)
        end = min(len(pair_rows), idx + context + 1)
        out.append({
            "center": idx,
            "rows": [(i, pair_rows[i]) for i in range(start, end)],
        })
    return out


def find_candidate_words(pair_rows: Sequence[tuple[int, int, int, int]],
                         target: int,
                         existing_anchors: Iterable[tuple[int, int]] = (),
                         search_range: tuple[int, int] | None = None,
                         nibble_order: tuple[int, int, int, int] = (0, 1, 2, 3),
                         ) -> list[int]:
    """Find every codeword index where assuming the codeword == `target`
    is consistent with the existing anchor mappings (no contradictions
    on already-pinned pair-ids).

    Args:
        pair_rows: pair-id stream.
        target: 16-bit value to search for.
        existing_anchors: already-known anchors, used to constrain.
        search_range: (start, end) codeword indices to search; defaults
            to whole stream.
        nibble_order: per-byte-position nibble layout.

    Returns:
        List of candidate codeword indices (could be empty, one, or many).
        Use these to narrow down where a specific known value is located.

    Example:
        # Find all positions where assuming the codeword = 0xE70A is
        # consistent with the FFFF anchor we already have.
        candidates = find_candidate_words(rows, 0xE70A,
                                          existing_anchors=[(43250, 0xFFFF)])
    """
    # Build per-position pair-id -> nibble constraints from existing anchors.
    pinned = [{} for _ in range(4)]
    used_nibs = [set() for _ in range(4)]
    for cw_idx, w in existing_anchors:
        for p in range(4):
            shift = 12 - 4 * nibble_order[p]
            nb = (w >> shift) & 0xF
            pid = pair_rows[cw_idx][p]
            if pid in pinned[p] and pinned[p][pid] != nb:
                # Contradictory anchors — skip; we'd have caught this elsewhere.
                pass
            pinned[p][pid] = nb
            used_nibs[p].add(nb)

    # Compute target nibbles
    target_nibs = [(target >> (12 - 4 * nibble_order[p])) & 0xF
                   for p in range(4)]

    lo, hi = search_range or (0, len(pair_rows))
    candidates = []
    for i in range(lo, hi):
        cw = pair_rows[i]
        ok = True
        for p in range(4):
            pid = cw[p]
            tnib = target_nibs[p]
            if pid in pinned[p]:
                # Pinned: must equal target nibble.
                if pinned[p][pid] != tnib:
                    ok = False
                    break
            else:
                # Unpinned: target nibble must not already be used by
                # another pinned pair-id at this position.
                if tnib in used_nibs[p]:
                    ok = False
                    break
        if ok:
            candidates.append(i)
    return candidates


def add_anchor_consistency_check(pair_rows: Sequence[tuple[int, int, int, int]],
                                 anchors: Iterable[tuple[int, int]],
                                 nibble_order: tuple[int, int, int, int] = (0, 1, 2, 3),
                                 ) -> dict:
    """Validate a set of candidate anchors for mutual consistency.

    Returns:
        {
            "consistent": bool,
            "contradictions": list of (anchor_a, anchor_b, position, nibble_a, nibble_b),
            "pinned_per_position": list of int (how many pair-ids each pos pins),
            "duplicate_nibbles": list of (position, nibble, [pair_ids])
                — same nibble assigned to multiple pair-ids,
        }
    """
    pinned = [{} for _ in range(4)]
    contradictions = []
    anchors = list(anchors)
    for ai, (cw_idx, w) in enumerate(anchors):
        for p in range(4):
            shift = 12 - 4 * nibble_order[p]
            nb = (w >> shift) & 0xF
            pid = pair_rows[cw_idx][p]
            if pid in pinned[p]:
                prev_nb, prev_ai = pinned[p][pid]
                if prev_nb != nb:
                    contradictions.append({
                        "anchor_a": (anchors[prev_ai], prev_ai),
                        "anchor_b": (anchors[ai], ai),
                        "position": p,
                        "nibble_a": prev_nb,
                        "nibble_b": nb,
                        "pair_id": pid,
                    })
            else:
                pinned[p][pid] = (nb, ai)

    # Check for nibble duplicates (different pair-ids assigned same nibble)
    duplicates = []
    for p in range(4):
        nib_to_pids = {}
        for pid, (nb, _) in pinned[p].items():
            nib_to_pids.setdefault(nb, []).append(pid)
        for nb, pids in nib_to_pids.items():
            if len(pids) > 1:
                duplicates.append({"position": p, "nibble": nb, "pair_ids": pids})

    return {
        "consistent": not contradictions and not duplicates,
        "contradictions": contradictions,
        "pinned_per_position": [len(pinned[p]) for p in range(4)],
        "duplicate_nibbles": duplicates,
    }


def decode_tag_high_bytes(frames: Sequence[dict],
                          mappings: Sequence[dict],
                          nibble_order: tuple[int, int, int, int] = (0, 1, 2, 3),
                          ) -> list[dict]:
    """Decode the variable bytes (positions 0,1) of each frame's tag.
    The fixed bytes 2,3 are framing markers; only 0,1 carry data.

    The tag of frame N is the last 4 bytes of frame N (bytes
    immediately before the next sync, or before EOF for the last
    frame).  If a frame has no tag (too short), returns None for
    that frame's value.
    """
    out = []
    for f in frames:
        tag = f["tag"]
        value = None
        marker = None
        if len(tag) == TAG_LEN:
            try:
                n0 = mappings[0][PAIR_INDEX[0][tag[0]]]
                n1 = mappings[1][PAIR_INDEX[1][tag[1]]]
                shifts = [12 - 4 * nibble_order[p] for p in range(2)]
                value = (n0 << shifts[0]) | (n1 << shifts[1])
                marker = (tag[2], tag[3])
            except KeyError:
                # Tag bytes 0/1 outside expected alphabet; skip.
                pass
        out.append({
            "frame_index": f["index"],
            "frame_start": f["start"],
            "tag_value_partial": value,   # nibbles from bytes 0,1 only
            "tag_marker": marker,
        })
    return out


# ============================================================================
# Top-level convenience
# ============================================================================

def decode_file(path: str | Path,
                anchors: Iterable[tuple[int, int]] = (),
                nibble_order: tuple[int, int, int, int] = (0, 1, 2, 3),
                only_standard: bool = False,
                ) -> dict:
    """One-shot: read encoded file, return everything."""
    data = Path(path).read_bytes()
    payload, frames, anomalous = strip_framing(
        data, only_standard=only_standard)
    pair_rows = decode_pair_indices(payload)
    mappings = derive_mappings(pair_rows, anchors, nibble_order)
    words = apply_mappings(pair_rows, mappings, nibble_order)
    return {
        "input_size": len(data),
        "frames": frames,
        "anomalous_blocks": anomalous,
        "payload_size": len(payload),
        "pair_rows": pair_rows,
        "mappings": mappings,
        "words": words,
        "tag_partials": decode_tag_high_bytes(frames, mappings, nibble_order),
    }


# ============================================================================
# CLI
# ============================================================================

def _info(data: bytes,
          frames: list[dict],
          payload: bytes,
          pair_rows: list,
          anomalous: list,
          ) -> None:
    print(f"input bytes        : {len(data)}", file=sys.stderr)
    leading = frames[0]["start"]
    trailing = len(data) - frames[-1]["end"]
    print(f"leading header     : {leading} bytes "
          f"(before first sync at offset 0x{frames[0]['start']:X})",
          file=sys.stderr)
    if leading:
        head_hex = bytes(data[:min(leading, 32)]).hex(" ")
        print(f"  bytes: {head_hex}", file=sys.stderr)
    print(f"trailing bytes     : {trailing} "
          f"(after last frame ending at 0x{frames[-1]['end']:X})",
          file=sys.stderr)
    print(f"frames found       : {len(frames)}", file=sys.stderr)
    n_std = sum(1 for f in frames if f["is_standard"])
    print(f"  standard (168 B) : {n_std}", file=sys.stderr)
    print(f"  anomalous        : {len(frames) - n_std}", file=sys.stderr)

    # Stride / size histogram
    sizes = Counter(f["frame_size"] for f in frames)
    print("frame size histogram:", file=sys.stderr)
    for sz, n in sizes.most_common():
        print(f"  {sz:>5d} bytes  ×  {n}", file=sys.stderr)

    # Tag marker validation
    marker_hist = Counter()
    bad_tag = 0
    for f in frames:
        if len(f["tag"]) == TAG_LEN:
            m = (f["tag"][2], f["tag"][3])
            marker_hist[m] += 1
            if m not in VALID_TAG_SUFFIXES:
                bad_tag += 1
        else:
            marker_hist[None] += 1
    print(f"tag-marker histogram (bytes 2,3 of trailing 4):", file=sys.stderr)
    for m, n in marker_hist.most_common():
        if m is None:
            label = "(no tag — frame too short)"
        else:
            label = f"{m[0]:02X} {m[1]:02X}" + (
                "  [VALID]" if m in VALID_TAG_SUFFIXES else "  [unexpected]")
        print(f"  {label:<30s} ×  {n}", file=sys.stderr)
    if bad_tag:
        print(f"  WARNING: {bad_tag} frame(s) with unexpected tag marker — "
              f"frame boundaries may be wrong", file=sys.stderr)

    # FFFF-marker validation
    ffff_ok = sum(1 for f in frames if f.get("ffff_marker") == FFFF_MARKER)
    ffff_other = len(frames) - ffff_ok
    print(f"FFFF-marker (bytes 4-7 of frame): {ffff_ok}/{len(frames)} match "
          f"the constant 9c 66 1b a5", file=sys.stderr)
    if ffff_other:
        print(f"  {ffff_other} frame(s) had a different value — investigate",
              file=sys.stderr)

    # Sample of metadata fields (first 5 standard frames)
    std_frames = [f for f in frames if f["is_standard"]]
    if std_frames:
        print(f"sample metadata fields (first 5 standard frames):",
              file=sys.stderr)
        for f in std_frames[:5]:
            print(f"  frame {f['index']:>4}: metadata = {f['metadata'].hex(' ')}",
                  file=sys.stderr)

    print(f"payload after strip: {len(payload)} bytes "
          f"({len(pair_rows)} codewords)", file=sys.stderr)
    print(f"max decoded size   : {2 * len(pair_rows)} bytes "
          f"({len(pair_rows)} 16-bit words)", file=sys.stderr)
    print(file=sys.stderr)

    if anomalous:
        print(f"anomalous frames ({len(anomalous)}):", file=sys.stderr)
        # Show only first/last few if many
        show = anomalous if len(anomalous) <= 6 else (
            anomalous[:3] + anomalous[-3:])
        for a in show:
            print(f"  frame {a['frame_index']:>4} : "
                  f"{a['frame_size']:>4} bytes  "
                  f"sync={a['sync']}  tag={a['tag']}",
                  file=sys.stderr)
            head = a["payload_raw"][:32].hex(" ")
            print(f"     head[32]: {head}", file=sys.stderr)
        if len(anomalous) > 6:
            print(f"  ... ({len(anomalous) - 6} more) ...",
                  file=sys.stderr)
        print(file=sys.stderr)

    print("per-position pair-id frequencies:", file=sys.stderr)
    for p in range(4):
        c = Counter(r[p] for r in pair_rows)
        line = "  pos {:d}: ".format(p) + "  ".join(
            f"{pid:X}={c.get(pid,0):>5d}" for pid in range(16))
        print(line, file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=("Decode DSP56800E firmware in the 4b/8b "
                     "line-coded, 168-byte-framed format."))
    ap.add_argument("input", help="encoded firmware file")
    ap.add_argument("-o", "--output",
                    help="write decoded 16-bit words here")
    ap.add_argument("--raw-payload",
                    help="write framing-stripped payload here (no nibble decode)")
    ap.add_argument("--anchors",
                    help='JSON: [[cw_idx, word], ...] e.g. [[45896, "0xE70A"]]')
    ap.add_argument("--nibble-order", default="0123",
                    help="byte-pos to nibble-pos mapping; '0123' (default) "
                         "= big-endian within word, '3210' = little-endian")
    ap.add_argument("--byte-order", choices=["big", "little"], default="big",
                    help="byte order for serialized 16-bit words (default big)")
    ap.add_argument("--skip", type=lambda s: int(s, 0), default=0,
                    help="byte offset to start scanning from (default 0); "
                         "useful to skip a leading header explicitly")
    ap.add_argument("--end", type=lambda s: int(s, 0), default=None,
                    help="byte offset to stop scanning at (default EOF)")
    ap.add_argument("--info", action="store_true",
                    help="print structure summary and exit")
    ap.add_argument("--find-runs", type=int, metavar="N", nargs="?",
                    const=15, default=None,
                    help="find the N longest constant-codeword runs and "
                         "exit (default N=15); use this to identify "
                         "anchor candidates for 0xFFFF / 0x0000")
    ap.add_argument("--find-value", metavar="HEX",
                    help="find every codeword index where assuming "
                         "codeword == HEX is consistent with --anchors; "
                         "use to locate known constants like 0xE70A "
                         "(SECL_VALUE) given the FFFF anchor")
    ap.add_argument("--find-near", metavar="IDX:RADIUS",
                    help="restrict --find-value search to IDX±RADIUS "
                         "(codeword indices, e.g. '45799:20')")
    ap.add_argument("--inspect", metavar="SPEC",
                    help="show pair-ids around given codeword indices. "
                         "SPEC is comma-separated; each token is either "
                         "an integer (use 'last:N' for the last N "
                         "codewords, or a Python-style negative index "
                         "like '-1') or a range 'A..B' (inclusive). "
                         "Examples: '0,5,last:1' or '0..7' or 'last:25' "
                         "or '45799,-1'")
    ap.add_argument("--check-anchors", action="store_true",
                    help="validate the anchors in --anchors for mutual "
                         "consistency and report nibble coverage")
    ap.add_argument("--search-anchor", metavar="CW_IDX",
                    type=lambda s: int(s, 0), default=None,
                    help="search for a value at codeword CW_IDX such that "
                         "the resulting decode of the vector table is "
                         "structurally valid (handler targets >= 0x00A4 "
                         "or in upper memory). Combine with --anchors "
                         "for the base anchor set. Tries values 0x0000-"
                         "0xFFFF and reports candidates by structural score.")
    ap.add_argument("--search-range", metavar="LO:HI", default="0:0x10000",
                    help="value range for --search-anchor (default 0:0x10000)")
    ap.add_argument("--vector-table-end", metavar="ADDR",
                    type=lambda s: int(s, 0), default=0xA4,
                    help="chip address where the vector table ends (default "
                         "0xA4 = 82 vectors × 2 words). Used by --search-anchor "
                         "to validate handler targets.")
    ap.add_argument("--only-standard", action="store_true",
                    help="exclude anomalous frames from decode")
    args = ap.parse_args(argv)

    data = Path(args.input).read_bytes()
    print(f"[+] loaded {len(data)} bytes from {args.input}", file=sys.stderr)

    frames = find_frames(data, start_offset=args.skip, end_offset=args.end)
    payload, frames, anomalous = strip_framing(
        data, frames, only_standard=args.only_standard)
    print(f"[+] {len(frames)} frames; payload after strip: "
          f"{len(payload)} bytes", file=sys.stderr)

    pair_rows = decode_pair_indices(payload)

    if args.raw_payload:
        Path(args.raw_payload).write_bytes(payload)
        print(f"[+] raw payload written to {args.raw_payload}", file=sys.stderr)

    if args.info:
        _info(data, frames, payload, pair_rows, anomalous)
        return 0

    if args.find_runs is not None:
        runs = find_constant_runs(pair_rows, top_n=args.find_runs)
        freqs = codeword_frequency(pair_rows, top_n=args.find_runs)

        print(f"\nTop {len(runs)} longest constant-codeword runs "
              f"(out of {len(pair_rows)} codewords):", file=sys.stderr)
        print(f"  {'#':>3}  {'cw_idx':>7}  {'length':>6}  "
              f"{'pair_ids':>15}  {'first byte':>10}",
              file=sys.stderr)
        for i, r in enumerate(runs):
            cw = pair_rows[r['start']]
            pid_str = "(%X,%X,%X,%X)" % cw
            print(f"  {i+1:>3}  {r['start']:>7}  {r['length']:>6}  "
                  f"{pid_str:>15}",
                  file=sys.stderr)

        if runs:
            top = runs[0]
            print(f"\nLongest run: {top['length']} consecutive codewords "
                  f"with pair-ids {top['pair_ids']}",
                  file=sys.stderr)
            print(f"In real DSP firmware this is almost always 0xFFFF "
                  f"(erased flash).  Suggested anchor:",
                  file=sys.stderr)
            print(f'  echo \'[[{top["start"]}, "0xFFFF"]]\' > anchors.json',
                  file=sys.stderr)
            print(f"Then re-run with --anchors anchors.json to decode.",
                  file=sys.stderr)

        print(f"\nTop {len(freqs)} most common codewords overall:",
              file=sys.stderr)
        for i, (cw, n) in enumerate(freqs):
            pid_str = "(%X,%X,%X,%X)" % cw
            pct = 100.0 * n / len(pair_rows)
            print(f"  {i+1:>3}  {pid_str:>15}  count={n:>6d}  ({pct:5.2f}%)",
                  file=sys.stderr)
        return 0

    nibble_order = tuple(int(c) for c in args.nibble_order)
    if sorted(nibble_order) != [0, 1, 2, 3]:
        print("--nibble-order must be a permutation of 0123", file=sys.stderr)
        return 2

    # Load anchors (used by every remaining mode).
    anchors: list[tuple[int, int]] = []
    if args.anchors:
        raw = json.loads(Path(args.anchors).read_text())

        def _to_int(x):
            return int(x, 0) if isinstance(x, str) else int(x)

        anchors = [(_to_int(idx), _to_int(word)) for idx, word in raw]
        print(f"[+] {len(anchors)} anchor(s) loaded", file=sys.stderr)

    if args.check_anchors:
        result = add_anchor_consistency_check(pair_rows, anchors, nibble_order)
        print(f"\nAnchor consistency check:", file=sys.stderr)
        print(f"  Total anchors    : {len(anchors)}", file=sys.stderr)
        print(f"  Consistent       : {result['consistent']}",
              file=sys.stderr)
        print(f"  Pinned per position (out of 16):",
              file=sys.stderr)
        for p in range(4):
            print(f"    pos {p}: {result['pinned_per_position'][p]}",
                  file=sys.stderr)
        if result['contradictions']:
            print(f"\nContradictions ({len(result['contradictions'])}):",
                  file=sys.stderr)
            for c in result['contradictions']:
                print(f"  pos {c['position']}: pair-id {c['pair_id']:X} "
                      f"assigned both {c['nibble_a']:X} and {c['nibble_b']:X}",
                      file=sys.stderr)
                print(f"    by anchor #{c['anchor_a'][1]}: "
                      f"{c['anchor_a'][0]} and "
                      f"#{c['anchor_b'][1]}: {c['anchor_b'][0]}",
                      file=sys.stderr)
        if result['duplicate_nibbles']:
            print(f"\nDuplicate nibbles "
                  f"({len(result['duplicate_nibbles'])}):",
                  file=sys.stderr)
            for d in result['duplicate_nibbles']:
                print(f"  pos {d['position']}: nibble {d['nibble']:X} "
                      f"assigned to pair-ids {d['pair_ids']}",
                      file=sys.stderr)
        return 0 if result['consistent'] else 1

    if args.find_value:
        target = int(args.find_value, 0)
        search_range = None
        if args.find_near:
            idx_str, rad_str = args.find_near.split(":")
            idx = int(idx_str, 0)
            rad = int(rad_str, 0)
            search_range = (max(0, idx - rad),
                            min(len(pair_rows), idx + rad + 1))
        cands = find_candidate_words(
            pair_rows, target, anchors, search_range, nibble_order)
        print(f"\nSearching for codeword(s) consistent with "
              f"value 0x{target:04X}", file=sys.stderr)
        if search_range:
            print(f"  range: codewords [{search_range[0]}, "
                  f"{search_range[1]})", file=sys.stderr)
        print(f"  using {len(anchors)} existing anchor(s)",
              file=sys.stderr)
        print(f"  found {len(cands)} candidate(s)",
              file=sys.stderr)
        if len(cands) <= 30:
            for c in cands:
                pid_str = "(%X,%X,%X,%X)" % pair_rows[c]
                print(f"    cw {c:>6d}  pair-ids {pid_str}",
                      file=sys.stderr)
        else:
            print(f"  (too many to list; showing first 10 and last 10)",
                  file=sys.stderr)
            for c in cands[:10] + cands[-10:]:
                pid_str = "(%X,%X,%X,%X)" % pair_rows[c]
                print(f"    cw {c:>6d}  pair-ids {pid_str}",
                      file=sys.stderr)
        return 0

    if args.search_anchor is not None:
        cw_idx = args.search_anchor
        lo_str, hi_str = args.search_range.split(":")
        val_lo = int(lo_str, 0)
        val_hi = int(hi_str, 0)
        vt_end = args.vector_table_end

        if cw_idx >= len(pair_rows):
            print(f"--search-anchor: codeword index {cw_idx} out of range "
                  f"(have {len(pair_rows)} codewords)", file=sys.stderr)
            return 2

        # Vector table layout: starts at codeword 6 (after 6-codeword boundary).
        # Each vector = 2 codewords (opcode + target). 82 vectors total.
        # Vector N target codeword = 6 + N*2 + 1.
        vec_target_cws = [6 + n * 2 + 1 for n in range(82)]

        # JSR <ABS19> opcode template = 0xE254. Targets that come after JSR (vec 2-81)
        # are 16-bit addresses for in-program-flash targets. JMP targets (vec 0,1)
        # similarly. All vector targets should be valid addresses on the chip.

        def is_valid_target(value: int) -> bool:
            """A vector target is structurally valid if it's:
            - >= vector_table_end (post-vector-table program flash), OR
            - >= 0x8000 (upper memory: data flash, boot flash, etc.)
            """
            return value >= vt_end or value >= 0x8000

        print(f"\nSearching for cw {cw_idx} value such that decoded vector "
              f"targets are structurally valid:", file=sys.stderr)
        print(f"  Considering values 0x{val_lo:04X} .. 0x{val_hi:04X}",
              file=sys.stderr)
        print(f"  Vector table end address: 0x{vt_end:04X}", file=sys.stderr)
        print(f"  Valid target: addr >= 0x{vt_end:04X} OR addr >= 0x8000",
              file=sys.stderr)
        print(f"  Base anchors: {len(anchors)}", file=sys.stderr)
        print(file=sys.stderr)

        # Quick consistency check function
        def try_value(value: int) -> dict | None:
            """Try anchoring cw_idx to value; return scoring info or None on conflict."""
            test_anchors = list(anchors) + [(cw_idx, value)]
            try:
                mappings = derive_mappings(pair_rows, test_anchors, nibble_order)
            except ValueError:
                return None  # contradiction

            # Decode all 82 vector targets
            targets = []
            for vt_cw in vec_target_cws:
                if vt_cw >= len(pair_rows):
                    return None
                pids = pair_rows[vt_cw]
                # Decode using mappings
                v = 0
                for p in range(4):
                    nib = mappings[p][pids[p]]
                    v |= nib << (12 - 4 * nibble_order[p])
                targets.append(v)

            # Score: number of valid targets
            n_valid = sum(1 for t in targets if is_valid_target(t))
            return {
                "value": value,
                "targets": targets,
                "n_valid": n_valid,
                "n_total": len(targets),
            }

        # Search
        results = []
        n_tested = 0
        n_consistent = 0
        for v in range(val_lo, val_hi):
            r = try_value(v)
            n_tested += 1
            if r is None:
                continue
            n_consistent += 1
            results.append(r)

        print(f"  Tested {n_tested} values, {n_consistent} mathematically consistent",
              file=sys.stderr)

        # Filter: only show results where ALL 82 targets are valid
        perfect = [r for r in results if r["n_valid"] == r["n_total"]]
        # Sort by valid count descending
        results.sort(key=lambda r: -r["n_valid"])

        if perfect:
            print(f"\n  *** {len(perfect)} value(s) give ALL 82 vectors valid! ***",
                  file=sys.stderr)
            from collections import Counter
            for r in perfect[:30]:
                tc = Counter(r["targets"])
                default, n_default = tc.most_common(1)[0]
                specifics = sorted(t for t, n in tc.items() if n < n_default)
                spec_str = ", ".join(f"0x{t:04X}" for t in specifics)
                print(f"    cw {cw_idx} = 0x{r['value']:04X}: "
                      f"default=0x{default:04X} ({n_default}×), "
                      f"specifics=[{spec_str}]", file=sys.stderr)
        else:
            print(f"\n  No value gives all 82 vectors valid. "
                  f"Top 10 by score:", file=sys.stderr)
            from collections import Counter
            for r in results[:10]:
                tc = Counter(r["targets"])
                default, n_default = tc.most_common(1)[0]
                # Find which targets are INVALID (inside vector table)
                invalid = sorted({t for t in r["targets"]
                                  if not (t >= vt_end or t >= 0x8000)})
                inv_str = ", ".join(f"0x{t:04X}" for t in invalid)
                print(f"    cw {cw_idx} = 0x{r['value']:04X}: "
                      f"{r['n_valid']}/{r['n_total']} valid, "
                      f"default=0x{default:04X}, invalid=[{inv_str}]",
                      file=sys.stderr)
            print(file=sys.stderr)
            print(f"  Try a different cw_idx, expand --search-range, or "
                  f"check if base anchors are correct.", file=sys.stderr)

        return 0

    if args.inspect:
        # Parse a comma-separated SPEC.  Each token:
        #   "last:N"  -> last N codewords (range [n-N, n-1])
        #   "A..B"    -> inclusive range [A, B]; A,B may be 'last:K' or int
        #   "-N"      -> Python-style negative index (n - N)
        #   integer   -> single index (decimal, hex with 0x, etc.)
        n = len(pair_rows)

        def _resolve(tok: str) -> int:
            tok = tok.strip()
            if tok.startswith("last:"):
                k = int(tok[5:], 0)
                return n - k
            return int(tok, 0) if not tok.lstrip("-").isdigit() \
                else (n + int(tok) if tok.startswith("-") else int(tok))

        indices = []
        for tok in args.inspect.split(","):
            tok = tok.strip()
            if ".." in tok:
                a_str, b_str = tok.split("..", 1)
                a = _resolve(a_str)
                b = _resolve(b_str)
                indices.extend(range(a, b + 1))
            elif tok.startswith("last:"):
                # Treat as a range from (n - k) to (n - 1)
                k = int(tok[5:], 0)
                indices.extend(range(max(0, n - k), n))
            else:
                indices.append(_resolve(tok))

        # De-duplicate while preserving order
        seen = set()
        unique = []
        for i in indices:
            if i not in seen:
                seen.add(i)
                unique.append(i)
        indices = unique

        print(f"\nInspecting codewords (total {n}):", file=sys.stderr)
        for ins in inspect_codewords(pair_rows, indices, context=4):
            print(f"\n  centred on codeword {ins['center']}:",
                  file=sys.stderr)
            for cw_idx, pids in ins['rows']:
                marker = "  <--" if cw_idx == ins['center'] else ""
                pid_str = "(%X,%X,%X,%X)" % pids
                print(f"    cw {cw_idx:>6d}  pair-ids {pid_str}{marker}",
                      file=sys.stderr)
        return 0

    if not anchors:
        print("[!] no anchors supplied; mapping is heuristic only and "
              "almost certainly wrong",
              file=sys.stderr)

    mappings = derive_mappings(pair_rows, anchors, nibble_order)

    # Show the derived mappings so the user can sanity-check.
    pinned = [set() for _ in range(4)]
    for cw, w in anchors:
        for p in range(4):
            pinned[p].add(pair_rows[cw][p])
    print("[+] derived pair-id -> nibble mappings "
          "(P = pinned by anchor, h = heuristic):", file=sys.stderr)
    for p in range(4):
        line = f"  pos {p}: "
        for pid in range(16):
            mark = "P" if pid in pinned[p] else "h"
            line += f"{pid:X}->{mappings[p][pid]:X}{mark}  "
        print(line, file=sys.stderr)

    words = apply_mappings(pair_rows, mappings, nibble_order)

    print(f"[+] first 8 words: "
          f"{' '.join(f'{w:04X}' for w in words[:8])}",
          file=sys.stderr)
    print(f"[+] last  8 words: "
          f"{' '.join(f'{w:04X}' for w in words[-8:])}",
          file=sys.stderr)

    if args.output:
        packer = ">H" if args.byte_order == "big" else "<H"
        out = b"".join(struct.pack(packer, w) for w in words)
        Path(args.output).write_bytes(out)
        print(f"[+] wrote {len(out)} bytes to {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
