#!/usr/bin/env python3
"""
Decode the framed Applied Motion / MC56F8345 firmware stream.

This script is based on claude-notes.md, with one adjustment verified against
the local firmware bytes: the stable on-disk sync marker is the 8-byte sequence

    99 56 87 68 9c 66 1b a5

with a few boundary variants. Normal frame-to-frame distance is 168 bytes.
The bytes between markers are a stream of 4-byte encoded words.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


HEADER_LEN = 13
ASCII_HEADER_PREFIX = b"1.07R,22\r\n"

SYNC_MARKERS = {
    bytes.fromhex("995687689c661ba5"): "regular",
    bytes.fromhex("995583689c661ba5"): "first",
    bytes.fromhex("995683689c661ba5"): "last",
    bytes.fromhex("9956831d9c661ba5"): "short_variant",
}


@dataclass(frozen=True)
class Frame:
    index: int
    offset: int
    marker: bytes
    marker_kind: str
    body: bytes

    @property
    def next_distance(self) -> int | None:
        return None


def strip_file_header(raw: bytes) -> tuple[bytes, bytes]:
    if raw.startswith(ASCII_HEADER_PREFIX) and len(raw) >= HEADER_LEN:
        return raw[:HEADER_LEN], raw[HEADER_LEN:]
    return b"", raw


def find_markers(data: bytes) -> list[tuple[int, bytes, str]]:
    found: list[tuple[int, bytes, str]] = []
    i = 0
    while i <= len(data) - 8:
        marker = data[i : i + 8]
        kind = SYNC_MARKERS.get(marker)
        if kind is not None:
            found.append((i, marker, kind))
            i += 8
        else:
            i += 1
    return found


def build_frames(
    data: bytes,
    markers: list[tuple[int, bytes, str]],
    include_trailer_after_last_marker: bool,
) -> list[Frame]:
    frames: list[Frame] = []
    for idx, (offset, marker, kind) in enumerate(markers):
        body_start = offset + len(marker)
        if idx + 1 < len(markers):
            body_end = markers[idx + 1][0]
        elif include_trailer_after_last_marker:
            body_end = len(data)
        else:
            body_end = body_start

        frames.append(
            Frame(
                index=idx,
                offset=offset,
                marker=marker,
                marker_kind=kind,
                body=data[body_start:body_end],
            )
        )
    return frames


def body_groups(frame: Frame, strip_mode: str) -> list[bytes]:
    groups = [
        frame.body[i : i + 4]
        for i in range(0, len(frame.body) - (len(frame.body) % 4), 4)
    ]

    if strip_mode == "none":
        return groups
    if strip_mode == "frame-overhead":
        if len(groups) <= 2:
            return []
        # In normal frames the first post-marker group is a record header
        # and the final group is the ?? ?? 26 9f-ish tag/trailer.
        return groups[1:-1]
    raise ValueError(f"unknown strip mode: {strip_mode}")


def decode_edge_xor(group: bytes) -> bytes:
    """Known vector-friendly decode: 4 encoded bytes -> one 16-bit word."""
    if len(group) != 4:
        raise ValueError("encoded group must be 4 bytes")
    return bytes((group[0] ^ 0xCD, group[3] ^ 0x45))


def decode_pair_xor(group: bytes) -> bytes:
    """Polarity-insensitive candidate from tentative_rts_before_jsr_address.py."""
    if len(group) != 4:
        raise ValueError("encoded group must be 4 bytes")
    low = group[0] ^ group[1] ^ 0xAF
    high = group[2] ^ group[3] ^ 0x2B
    return bytes((high, low))


DECODERS = {
    "edge-xor": decode_edge_xor,
    "pair-xor": decode_pair_xor,
}


def extract_encoded(frames: list[Frame], strip_mode: str) -> bytes:
    out = bytearray()
    for frame in frames:
        for group in body_groups(frame, strip_mode):
            out.extend(group)
    return bytes(out)


def decode_frames(frames: list[Frame], strip_mode: str, decoder_name: str) -> bytes:
    decode_group = DECODERS[decoder_name]
    out = bytearray()
    for frame in frames:
        for group in body_groups(frame, strip_mode):
            out.extend(decode_group(group))
    return bytes(out)


def word_be(data: bytes, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def print_vector_preview(decoded: bytes, vectors: int) -> None:
    if vectors <= 0:
        return
    print()
    print(f"Vector preview ({vectors} entries)")
    print("idx  opcode  target")
    for idx in range(vectors):
        offset = idx * 4
        if offset + 3 >= len(decoded):
            break
        opcode = word_be(decoded, offset)
        target = word_be(decoded, offset + 2)
        marker = ""
        if opcode == 0x54E1:
            marker = " JMP"
        elif opcode == 0x54E2:
            marker = " JSR"
        print(f"{idx:3d}  {opcode:04x}  {target:04x}{marker}")


def print_report(
    source: Path,
    raw_len: int,
    header: bytes,
    data_len: int,
    markers: list[tuple[int, bytes, str]],
    frames: list[Frame],
    strip_mode: str,
    decoded_len: int,
) -> None:
    print(f"Input bytes      : {raw_len:,} ({source})")
    if header:
        print(f"Header           : {header[:10]!r} + {header[10:].hex(' ')}")
    else:
        print("Header           : none detected")
    print(f"Framed bytes     : {data_len:,}")
    print(f"Markers found    : {len(markers):,}")

    marker_counts = Counter(kind for _, _, kind in markers)
    for kind, count in marker_counts.most_common():
        print(f"  {kind:<13}: {count:,}")

    distances = Counter(
        markers[i + 1][0] - markers[i][0] for i in range(len(markers) - 1)
    )
    if distances:
        print("Marker distances : " + ", ".join(f"{k}={v}" for k, v in sorted(distances.items())))

    body_group_counts = Counter(len(frame.body) // 4 for frame in frames)
    print(
        "Body groups      : "
        + ", ".join(f"{groups} groups={count}" for groups, count in sorted(body_group_counts.items()))
    )
    print(f"Strip mode       : {strip_mode}")
    print(f"Decoded bytes    : {decoded_len:,} ({decoded_len // 2:,} words)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decode the framed 4-byte-word MC56F8345 firmware stream."
    )
    parser.add_argument("input", type=Path, help="Input .elf.e file or headerless payload")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("claude_decoded.bin"),
        help="Decoded output path",
    )
    parser.add_argument(
        "--encoded-output",
        type=Path,
        help="Optional path for the extracted encoded 4-byte groups",
    )
    parser.add_argument(
        "--strip-mode",
        choices=("frame-overhead", "none"),
        default="frame-overhead",
        help="Use frame-overhead for firmware words, none for all post-marker groups",
    )
    parser.add_argument(
        "--decoder",
        choices=tuple(DECODERS),
        default="edge-xor",
        help="4-byte-group decoding formula",
    )
    parser.add_argument(
        "--include-last-trailer",
        action="store_true",
        help="Decode groups after the final marker too",
    )
    parser.add_argument(
        "--vectors",
        type=int,
        default=16,
        help="Number of 4-byte vector entries to preview",
    )
    args = parser.parse_args()

    raw = args.input.read_bytes()
    header, data = strip_file_header(raw)
    markers = find_markers(data)
    if not markers:
        raise SystemExit("no known frame markers found")

    frames = build_frames(data, markers, args.include_last_trailer)
    encoded = extract_encoded(frames, args.strip_mode)
    decoded = decode_frames(frames, args.strip_mode, args.decoder)

    args.output.write_bytes(decoded)
    if args.encoded_output:
        args.encoded_output.write_bytes(encoded)

    print_report(
        source=args.input,
        raw_len=len(raw),
        header=header,
        data_len=len(data),
        markers=markers,
        frames=frames,
        strip_mode=args.strip_mode,
        decoded_len=len(decoded),
    )
    print(f"Decoder          : {args.decoder}")
    print(f"Output           : {args.output}")
    if args.encoded_output:
        print(f"Encoded output   : {args.encoded_output}")
    print_vector_preview(decoded, args.vectors)


if __name__ == "__main__":
    main()
