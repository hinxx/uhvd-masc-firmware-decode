#!/usr/bin/env python3
"""
DSP56800E firmware decoder for the 4b/8b line-coded, 168-byte-framed format.

File format (from analysis):
    Stream of frames separated by 4-byte sync.  Each frame's structure is:
        [sync : 4][payload : N-8][tag : 4]
    where N is the frame size.  Most frames are standard 168-byte ones
    (160-byte payload = 40 codewords).  The first frame is typically a
    40-byte boundary header (32-byte payload).  The last frame is often
    truncated (typically 108 bytes, 100-byte payload).  A small leading
    file header (e.g., 13 bytes of version string) may precede the first
    sync; the decoder skips over it automatically.

Encoding:
    Each 4-byte payload codeword carries one 16-bit DSP word using a
    4b/8b line code with running-disparity polarity.  Per byte
    position, the alphabet has 32 valid values forming 16 XOR-paired
    groups:
        pos 0 mask 0x66, pos 1 mask 0x33,
        pos 2 mask 0x99, pos 3 mask 0xCC.
    Each XOR pair represents one logical nibble (0..F); the encoder
    picks polarity to balance the running 0/1 disparity.  The 4-byte
    tag at the end of each frame uses an extension pair at positions
    2 and 3 (markers 0x26/0xBF and 0x9F/0x53), with positions 0,1
    carrying frame metadata in the regular alphabet.

Decoding pipeline:
    1. find_frames        — locate every sync.
    2. strip_framing      — concatenate payload bytes (skip sync at start
                            and tag at end of each frame).
    3. decode_pair_indices — bytes → pair-id (0..15) tuples.
    4. derive_mappings    — pair-id → nibble (needs anchors).
    5. apply_mappings     — produce final 16-bit words.

What is fully known:
    Frame structure, sync/tag bytes, the 32-byte alphabet at every
    byte position, and the XOR-pair structure.  Stages 1–3 are
    deterministic and need no extra information.

What requires anchors:
    The bijection pair-id → nibble at each of the four positions
    cannot be determined from the histogram alone.  Pass anchors
    (codeword-index, expected-word) to derive_mappings.  Recommended
    anchor: the SECL_VALUE = 0xE70A appearing 8 words from the top of
    program flash (see the MC56F8300 Flash Memory chapter, Table 6-7
    and Section 6.5.4).
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
TAG_LEN = 4
FRAMING = SYNC_LEN + TAG_LEN
STD_FRAME_SIZE = 168
STD_PAYLOAD_SIZE = STD_FRAME_SIZE - FRAMING  # 160 bytes = 40 codewords

# All sync values observed in the analysed file.
VALID_SYNCS = {
    b"\x99\x56\x87\x68",  # standard
    b"\x99\x55\x83\x68",  # boundary variant — observed at file start
    b"\x99\x56\x83\x68",  # boundary variant — observed at file end
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
    """Locate every frame in `data` by sync-byte detection.

    Frame structure: [sync 4 bytes][payload N-8 bytes][tag 4 bytes].
    The tag's bytes 2,3 carry the marker (0x26,0x9F) or (0xBF,0x53),
    but tag detection is *not* used for sync validation since the tag
    sits at the end of the frame, not next to the sync.  Sync is
    detected by exact byte-sequence match alone.

    Args:
        data: full file bytes.
        start_offset: do not look for syncs before this offset.
        end_offset: do not look for syncs at or after this offset.
            Defaults to len(data).

    Returns:
        Frames in file order.  Each frame's `frame_size` is the
        distance to the next sync (or to `end_offset` for the final
        frame).  Each frame includes pre-computed `payload_start`,
        `payload_size`, and `tag` (the 4 bytes preceding the next
        frame's start, or the file end for the last frame).
    """
    if end_offset is None:
        end_offset = len(data)
    last_search = end_offset - SYNC_LEN + 1

    sync_positions: list[int] = []
    pos = start_offset
    while pos < last_search:
        if bytes(data[pos:pos + SYNC_LEN]) in VALID_SYNCS:
            sync_positions.append(pos)
            # Skip past the sync; a sync inside its own bytes is not
            # possible, and skipping avoids matching inside payloads
            # by chance in the rare case where a payload codeword
            # happens to equal one of the sync values.
            pos += SYNC_LEN
            continue
        pos += 1

    if not sync_positions:
        raise ValueError(
            "no valid sync words found in file; is this the right "
            "format? (expected sync ∈ "
            "{99 56 87 68, 99 55 83 68, 99 56 83 68})")

    frames = []
    for i, start in enumerate(sync_positions):
        end = (sync_positions[i + 1] if i + 1 < len(sync_positions)
               else end_offset)
        size = end - start
        sync = bytes(data[start:start + SYNC_LEN])
        # Payload sits between sync and tag.  Tag is the last 4 bytes
        # of the frame (immediately before the next sync, or before EOF
        # for the last frame).  If the frame is short enough that
        # there's no room for both, treat it as sync-only.
        if size >= SYNC_LEN + TAG_LEN:
            payload_start = start + SYNC_LEN
            payload_size = size - SYNC_LEN - TAG_LEN
            tag = bytes(data[end - TAG_LEN:end])
        else:
            payload_start = start + SYNC_LEN
            payload_size = max(0, size - SYNC_LEN)
            tag = b""
        frames.append({
            "index": i,
            "start": start,
            "end": end,
            "frame_size": size,
            "sync": sync,
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
                  only_standard: bool = False
                  ) -> tuple[bytes, list[dict], list[dict]]:
    """Concatenate the payload bytes of every frame, with framing
    (sync + tag) removed.

    Args:
        data: the encoded firmware.
        frames: optional pre-computed frame list; defaults to find_frames(data).
        only_standard: if True, only standard 168-byte frames contribute
            to the returned payload; anomalous frames are skipped (still
            returned in `anomalous_blocks`).

    Returns:
        (payload_bytes, frames, anomalous_blocks)

        payload_bytes is a multiple of 4 (whole codewords).  Anomalous
        frames are included only if their bytes pass the per-position
        alphabet check (i.e. they really are encoded codewords); raw
        bytes of every anomalous frame are also returned for inspection.
    """
    if frames is None:
        frames = find_frames(data)

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

        payload.extend(pbytes)

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

    nibble_order = tuple(int(c) for c in args.nibble_order)
    if sorted(nibble_order) != [0, 1, 2, 3]:
        print("--nibble-order must be a permutation of 0123", file=sys.stderr)
        return 2

    anchors: list[tuple[int, int]] = []
    if args.anchors:
        raw = json.loads(Path(args.anchors).read_text())

        def _to_int(x):
            return int(x, 0) if isinstance(x, str) else int(x)

        anchors = [(_to_int(idx), _to_int(word)) for idx, word in raw]
        print(f"[+] {len(anchors)} anchor(s) loaded", file=sys.stderr)
    else:
        print("[!] no anchors supplied; mapping is heuristic only and "
              "almost certainly wrong",
              file=sys.stderr)

    mappings = derive_mappings(pair_rows, anchors, nibble_order)
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
