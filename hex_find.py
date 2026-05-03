#!/usr/bin/env python3
import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path


def parse_pattern(text: str) -> tuple[list[int], list[int]]:
    """
    Parse a hex byte pattern with optional wildcards.

    Supported forms:
      9c 66 ? a5
      9c 66 ?? a5
      9c66??a5
      0x9c,0x66,0x?b,0xa?
    """
    raw_tokens = re.findall(r"(?:0x)?[0-9a-fA-F?]+", text)
    if not raw_tokens:
        raise ValueError("empty pattern")

    byte_tokens: list[str] = []
    for token in raw_tokens:
        if token.lower().startswith("0x"):
            token = token[2:]

        if token == "?":
            byte_tokens.append("??")
            continue

        if len(token) == 2:
            byte_tokens.append(token)
            continue

        if len(token) > 2:
            if len(token) % 2:
                raise ValueError(f"compact token has an odd length: {token!r}")
            byte_tokens.extend(token[i:i + 2] for i in range(0, len(token), 2))
            continue

        raise ValueError(f"hex byte must have two digits, or use '?' as wildcard: {token!r}")

    values: list[int] = []
    masks: list[int] = []
    for token in byte_tokens:
        value = 0
        mask = 0
        for idx, char in enumerate(token):
            shift = 4 if idx == 0 else 0
            if char == "?":
                continue
            if char not in "0123456789abcdefABCDEF":
                raise ValueError(f"invalid pattern byte: {token!r}")
            value |= int(char, 16) << shift
            mask |= 0xF << shift
        values.append(value)
        masks.append(mask)

    return values, masks


def find_matches(data: bytes, values: list[int], masks: list[int]) -> list[int]:
    size = len(values)
    if size > len(data):
        return []

    matches: list[int] = []
    for offset in range(0, len(data) - size + 1):
        for idx, expected in enumerate(values):
            if (data[offset + idx] & masks[idx]) != expected:
                break
        else:
            matches.append(offset)
    return matches


def printable(byte: int) -> str:
    return chr(byte) if 32 <= byte <= 126 else "."


def format_hexdump(
    data: bytes,
    base_offset: int,
    width: int,
    show_offset: bool,
    show_diff: bool,
    show_ascii: bool,
    offset_diff: int | None,
) -> list[str]:
    lines = []
    for rel in range(0, len(data), width):
        chunk = data[rel:rel + width]
        left = chunk[:8]
        right = chunk[8:]
        hex_left = " ".join(f"{byte:02x}" for byte in left)
        hex_right = " ".join(f"{byte:02x}" for byte in right)
        hex_bytes = f"{hex_left:<23}  {hex_right:<23}" if right else f"{hex_left:<23}  {'':<23}"
        ascii_bytes = "".join(printable(byte) for byte in chunk)
        if show_offset:
            prefix = f"{base_offset + rel:08x}"
            if show_diff:
                if rel == 0:
                    diff_text = "-" if offset_diff is None else f"0x{offset_diff:x}"
                else:
                    diff_text = ""
                prefix = f"{prefix}  {diff_text:>10}"
            lines.append(f"{prefix}  {hex_bytes}  |{ascii_bytes}|")
        else:
            lines.append(f"{hex_bytes}  |{ascii_bytes}|")
        if not show_ascii:
            lines[-1] = lines[-1].rsplit("  |", 1)[0].rstrip()
    return lines


def format_stats(
    data: bytes,
    matches: list[int],
    printed_matches: list[int],
    match_size: int,
    show_diff: bool,
) -> list[str]:
    unique_matches = Counter(data[offset:offset + match_size].hex(" ") for offset in matches)
    deltas = [right - left for left, right in zip(matches, matches[1:])]
    unique_deltas = Counter(deltas)
    lines = [
        "statistics:",
        f"  occurrences: {len(matches)}",
        f"  printed: {len(printed_matches)}",
        f"  unique matches: {len(unique_matches)}",
        f"  pattern length: {match_size} byte{'s' if match_size != 1 else ''}",
    ]

    if matches:
        lines.extend([
            f"  first offset: 0x{matches[0]:x}",
            f"  last offset: 0x{matches[-1]:x}",
        ])

    if show_diff and deltas:
        lines.extend([
            f"  offset span: 0x{matches[-1] - matches[0]:x}",
            f"  min offset delta: 0x{min(deltas):x}",
            f"  max offset delta: 0x{max(deltas):x}",
            f"  unique offset deltas: {len(unique_deltas)}",
        ])

    if show_diff and 0 < len(unique_deltas) <= 16:
        lines.append("  offset deltas:")
        for delta, count in unique_deltas.most_common():
            lines.append(f"    {count:>6}  0x{delta:x}")

    if 0 < len(unique_matches) <= 16:
        lines.append("  match values:")
        for value, count in unique_matches.most_common():
            lines.append(f"    {count:>6}  {value}")

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search a binary file for a hex pattern. Use ? as a byte or nibble wildcard."
    )
    parser.add_argument("file", type=Path, help="Binary file to search.")
    parser.add_argument("pattern", nargs="+", help="Hex pattern, e.g. '9c 66 ? a5' or '9c66??a5'.")
    parser.add_argument(
        "-C",
        "--context",
        type=int,
        help="Bytes of context to print before and after each match.",
    )
    parser.add_argument(
        "-B",
        "--before",
        type=int,
        default=0,
        help="Bytes of context to print before each match. Default: 0.",
    )
    parser.add_argument(
        "-A",
        "--after",
        type=int,
        default=0,
        help="Bytes of context to print after each match. Default: 0.",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=int,
        default=16,
        help="Bytes per output line. Default: 16.",
    )
    parser.add_argument(
        "-m",
        "--max-count",
        type=int,
        help="Stop after this many matches.",
    )
    parser.add_argument(
        "--no-offset",
        action="store_true",
        help="Suppress the leading hex file offset in output lines.",
    )
    parser.add_argument(
        "--no-ascii",
        action="store_true",
        help="Suppress the trailing ASCII column in output lines.",
    )
    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="Suppress offset-difference reporting.",
    )

    args = parser.parse_args()

    if args.width <= 0:
        parser.error("--width must be greater than zero")
    if args.context is not None and args.context < 0:
        parser.error("--context must not be negative")
    if args.before < 0:
        parser.error("--before must not be negative")
    if args.after < 0:
        parser.error("--after must not be negative")
    if args.max_count is not None and args.max_count <= 0:
        parser.error("--max-count must be greater than zero")

    before = args.context if args.context is not None else args.before
    after = args.context if args.context is not None else args.after

    try:
        values, masks = parse_pattern(" ".join(args.pattern))
    except ValueError as exc:
        parser.error(str(exc))

    try:
        data = args.file.read_bytes()
    except OSError as exc:
        print(f"hex_find.py: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2

    matches = find_matches(data, values, masks)
    printed_matches = matches
    if args.max_count is not None:
        printed_matches = matches[:args.max_count]

    match_size = len(values)

    previous_offset = None
    for offset in printed_matches:
        start = max(0, offset - before)
        end = min(len(data), offset + match_size + after)
        offset_diff = None if previous_offset is None else offset - previous_offset
        for line in format_hexdump(
            data[start:end],
            start,
            args.width,
            not args.no_offset,
            not args.no_diff,
            not args.no_ascii,
            offset_diff,
        ):
            print(line)
        previous_offset = offset

    if printed_matches:
        print()
    for line in format_stats(data, matches, printed_matches, match_size, not args.no_diff):
        print(line)

    return 0 if matches else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w")
        raise SystemExit(1)
