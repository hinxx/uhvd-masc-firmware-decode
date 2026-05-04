#!/usr/bin/env python3
"""
Dump decoded frame metadata alongside decoded payload words.

This is intentionally a diagnostic view.  Payload words use the decoder's
heuristic fallback unless direct bijection pins are supplied elsewhere.
Metadata can use a direct pair-id -> nibble JSON such as counter_bijection.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from dsp56800e_decoder import (
    PAIR_INDEX,
    TAG_LEN,
    apply_mappings,
    decode_pair_indices,
    derive_mappings,
    find_frames,
    strip_framing,
)


def parse_nibble_order(text: str) -> tuple[int, int, int, int]:
    order = tuple(int(ch) for ch in text)
    if sorted(order) != [0, 1, 2, 3]:
        raise ValueError("nibble order must be a permutation of 0123")
    return order


def load_direct_mapping(path: str | None) -> list[dict[int, int]]:
    mapping = [dict() for _ in range(4)]
    if not path:
        return mapping
    raw = json.loads(Path(path).read_text())
    if "pinned" not in raw:
        raise ValueError(f"{path}: expected top-level 'pinned' key")
    for pos, pid_map in enumerate(raw["pinned"]):
        if pos >= 4:
            break
        for pid, nibble in pid_map.items():
            pid_i = int(pid, 16) if isinstance(pid, str) else int(pid)
            nib_i = int(nibble, 16) if isinstance(nibble, str) else int(nibble)
            mapping[pos][pid_i] = nib_i
    return mapping


def codeword_pair_ids(codeword: bytes) -> tuple[int, int, int, int]:
    return tuple(PAIR_INDEX[pos][codeword[pos]] for pos in range(4))


def decode_codeword_partial(
    codeword: bytes,
    mappings: Sequence[dict[int, int]],
    nibble_order: tuple[int, int, int, int],
) -> tuple[str, str, str]:
    try:
        pids = codeword_pair_ids(codeword)
    except KeyError:
        return "illegal", "?, ?, ?, ?", "?, ?, ?, ?"

    nibbles: list[int | None] = []
    value = 0
    complete = True
    for pos, pid in enumerate(pids):
        nibble = mappings[pos].get(pid)
        nibbles.append(nibble)
        if nibble is None:
            complete = False
            continue
        shift = 12 - 4 * nibble_order[pos]
        value |= nibble << shift

    pid_text = ",".join(f"{pid:X}" for pid in pids)
    nib_text = ",".join("?" if n is None else f"{n:X}" for n in nibbles)
    if complete:
        return f"{value:04X}", pid_text, nib_text

    chars = ["?"] * 4
    for pos, nibble in enumerate(nibbles):
        if nibble is None:
            continue
        chars[nibble_order[pos]] = f"{nibble:X}"
    return "".join(chars), pid_text, nib_text


def payload_words_for_frame(frame: dict) -> int:
    return max((frame["payload_size"] // 4), 0)


def expected_metadata_values(data: bytes, frames: Sequence[dict]) -> list[int | None]:
    """Expected count-up word offsets for valid data frames."""
    value = 0
    out = []
    for frame in frames:
        pbytes = frame_payload_bytes(data, frame)
        is_valid_data = (
            frame["sync"][1] == 0x56
            and len(pbytes) > 0
            and payload_is_valid(pbytes)
        )
        out.append(value & 0xFFFF if is_valid_data else None)
        if is_valid_data:
            value = (value + payload_words_for_frame(frame)) & 0xFFFF
    return out


def frame_payload_bytes(data: bytes, frame: dict) -> bytes:
    used = (frame["payload_size"] // 4) * 4
    return bytes(data[frame["payload_start"]:frame["payload_start"] + used])


def payload_is_valid(pbytes: bytes) -> bool:
    return all(pbytes[i] in PAIR_INDEX[i % 4] for i in range(len(pbytes)))


def print_words(words: Sequence[int], start_cw: int, per_line: int) -> None:
    for base in range(0, len(words), per_line):
        chunk = words[base:base + per_line]
        print(
            f"    cw {start_cw + base:06d}: "
            + " ".join(f"{word:04X}" for word in chunk)
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show decoded metadata beside per-frame decoded payload")
    parser.add_argument("input", help="encoded firmware file")
    parser.add_argument("--skip", type=lambda s: int(s, 0), default=0,
                        help="byte offset to start frame scan")
    parser.add_argument("--end", type=lambda s: int(s, 0), default=None,
                        help="byte offset to stop frame scan")
    parser.add_argument("--nibble-order", default="0231",
                        help="payload nibble order (default: 0231)")
    parser.add_argument("--metadata-bijection", default="counter_bijection.json",
                        help="direct metadata mapping JSON (default: counter_bijection.json)")
    parser.add_argument("--metadata-nibble-order", default="0123",
                        help="metadata nibble order (default: 0123)")
    parser.add_argument("--only-standard", action="store_true",
                        help="match decoder behavior that excludes anomalous payload")
    parser.add_argument("--max-frames", type=int, default=20,
                        help="number of frames to print; 0 means all (default: 20)")
    parser.add_argument("--payload-words", type=int, default=12,
                        help="payload words to print per frame; 0 means all (default: 12)")
    parser.add_argument("--words-per-line", type=int, default=12,
                        help="payload words per output line (default: 12)")
    args = parser.parse_args()

    data = Path(args.input).read_bytes()
    frames = find_frames(data, start_offset=args.skip, end_offset=args.end)
    payload, frames, _anomalous = strip_framing(
        data, frames, only_standard=args.only_standard)

    payload_order = parse_nibble_order(args.nibble_order)
    payload_rows = decode_pair_indices(payload)
    payload_mappings = derive_mappings(payload_rows, payload_order)

    metadata_path = args.metadata_bijection
    metadata_mapping = None
    if metadata_path:
        if Path(metadata_path).exists():
            metadata_mapping = load_direct_mapping(metadata_path)
        else:
            print(
                f"[!] metadata mapping {metadata_path!r} not found; "
                "metadata decoded values will be '?'",
                file=sys.stderr,
            )
            metadata_mapping = [dict() for _ in range(4)]
    else:
        metadata_mapping = [dict() for _ in range(4)]
    metadata_order = parse_nibble_order(args.metadata_nibble_order)
    expected_meta = expected_metadata_values(data, frames)

    print(f"input={args.input} frames={len(frames)} payload_words={len(payload_rows)}")
    print(f"payload_order={args.nibble_order}")
    print(
        f"metadata_bijection={metadata_path or '(none)'} "
        f"metadata_order={args.metadata_nibble_order}"
    )
    print()

    max_frames = len(frames) if args.max_frames == 0 else min(args.max_frames, len(frames))
    global_cw = 0
    for frame in frames[:max_frames]:
        pbytes = frame_payload_bytes(data, frame)
        valid_payload = payload_is_valid(pbytes)
        included = (
            (frame["is_standard"] or (not args.only_standard and valid_payload))
            and len(pbytes) > 0
        )

        meta_value, meta_pids, meta_nibs = ("(none)", "", "")
        if len(frame["metadata"]) == 4:
            meta_value, meta_pids, meta_nibs = decode_codeword_partial(
                frame["metadata"], metadata_mapping, metadata_order)

        size = frame["frame_size"]
        tag = frame["tag"].hex(" ") if len(frame["tag"]) == TAG_LEN else "(none)"
        print(
            f"frame {frame['index']:04d} off=0x{frame['start']:06X} "
            f"size={size:3d} sync={frame['sync'].hex(' ')} "
            f"payload_words={payload_words_for_frame(frame):2d} "
            f"std={int(frame['is_standard'])} included={int(included)}"
        )
        expected = expected_meta[frame["index"]]
        expected_text = "n/a" if expected is None else f"{expected:04X}"
        print(
            f"  metadata raw={frame['metadata'].hex(' ')} "
            f"decoded={meta_value} expected_offset={expected_text} "
            f"pids=({meta_pids}) nibbles=({meta_nibs})"
        )
        print(f"  marker={frame['ffff_marker'].hex(' ')} tag={tag}")

        if valid_payload and pbytes:
            frame_rows = decode_pair_indices(pbytes)
            words = apply_mappings(frame_rows, payload_mappings, payload_order)
            limit = len(words) if args.payload_words == 0 else min(args.payload_words, len(words))
            print_words(words[:limit], global_cw if included else -1, args.words_per_line)
            if limit < len(words):
                print(f"    ... {len(words) - limit} more payload word(s)")
        elif pbytes:
            print(f"    payload not in codeword alphabet: {pbytes[:32].hex(' ')}")
        else:
            print("    no payload")

        if included:
            global_cw += len(pbytes) // 4
        print()

    if max_frames < len(frames):
        print(f"... {len(frames) - max_frames} more frame(s); use --max-frames 0 for all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
