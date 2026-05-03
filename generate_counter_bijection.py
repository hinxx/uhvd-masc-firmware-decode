#!/usr/bin/env python3
"""Generate metadata/header pair-id -> nibble pins from valid data frames."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analyze_frame_headers import check_constraints, word_constraints
from dsp56800e_decoder import find_frames
from dump_frames_with_metadata import frame_payload_bytes, payload_is_valid


def payload_words(frame: dict) -> int:
    return max(frame["payload_size"] // 4, 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate count-up metadata bijection JSON")
    parser.add_argument("input", help="encoded firmware, preferably no-header")
    parser.add_argument("--skip", type=lambda s: int(s, 0), default=0,
                        help="byte offset to start scanning from")
    parser.add_argument("--nibble-order", default="0123",
                        help="metadata nibble order (default: 0123)")
    args = parser.parse_args()

    nibble_order = tuple(int(c) for c in args.nibble_order)
    if sorted(nibble_order) != [0, 1, 2, 3]:
        print("--nibble-order must be a permutation of 0123", file=sys.stderr)
        return 2

    data = Path(args.input).read_bytes()
    frames = find_frames(data, start_offset=args.skip)
    valid_data_frames = [
        frame for frame in frames
        if frame["sync"][1] == 0x56
        and payload_is_valid(frame_payload_bytes(data, frame))
        and payload_words(frame) > 0
    ]

    offset = 0
    constraints = []
    for frame in valid_data_frames:
        constraints.extend(word_constraints(
            f"frame {frame['index']} metadata={frame['metadata'].hex()} "
            f"-> 0x{offset:04X}",
            frame["metadata"],
            offset,
            nibble_order,
        ))
        offset = (offset + payload_words(frame)) & 0xFFFF

    result = check_constraints(constraints)
    payload = {
        "ok": result["ok"],
        "model": {
            "field": "metadata bytes 9..12",
            "meaning": "count-up word offset before current data frame",
            "nibble_order": "".join(str(x) for x in nibble_order),
            "start": "0x0000",
            "step": "payload_words",
            "frames": (
                "valid data frames only; excludes the initial ID frame and "
                "the final non-payload terminator frame"
            ),
            "valid_data_frames": len(valid_data_frames),
            "final_offset": f"0x{offset:04X}",
        },
        "pinned": [
            {
                f"{pid:X}": f"{nibble_source[0]:X}"
                for pid, nibble_source in sorted(mapping.items())
            }
            for mapping in result["assigned"]
        ],
        "contradictions": result["contradictions"],
        "duplicates": result["duplicates"],
    }
    print(json.dumps(payload, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
