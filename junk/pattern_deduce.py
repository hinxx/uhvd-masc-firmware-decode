#!/usr/bin/env python3
"""
Search a binary file for a hex pattern with nibble wildcards and summarize the
bytes that follow each match.

Pattern syntax:
  99 5? 8? ?? 9C 66 1B A5
  995?8???9C661BA5

Each byte token has two nibbles. A '?' wildcard can replace either nibble or
the whole byte.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatternByte:
    mask: int
    value: int
    text: str

    def matches(self, byte: int) -> bool:
        return (byte & self.mask) == self.value


def parse_int(value: str) -> int:
    return int(value, 0)


def read_input(path: str) -> tuple[str, bytes]:
    if path == "-":
        return "<stdin>", sys.stdin.buffer.read()
    return path, Path(path).read_bytes()


def normalize_pattern(text: str) -> list[str]:
    compact = "".join(text.replace(",", " ").split())
    if "?" in compact or all(ch in "0123456789abcdefABCDEF" for ch in compact):
        if len(compact) % 2:
            raise ValueError("compact pattern must contain an even number of nibbles")
        return [compact[i:i + 2] for i in range(0, len(compact), 2)]
    return text.replace(",", " ").split()


def parse_pattern(text: str) -> list[PatternByte]:
    tokens = normalize_pattern(text)
    pattern = []
    for token in tokens:
        token = token.strip()
        if len(token) == 1 and token == "?":
            token = "??"
        if len(token) != 2:
            raise ValueError(f"invalid byte token {token!r}; use two nibbles, e.g. 5? or ??")

        mask = 0
        value = 0
        for idx, nibble in enumerate(token):
            shift = 4 if idx == 0 else 0
            if nibble == "?":
                continue
            if nibble not in "0123456789abcdefABCDEF":
                raise ValueError(f"invalid hex nibble {nibble!r} in token {token!r}")
            mask |= 0xF << shift
            value |= int(nibble, 16) << shift
        pattern.append(PatternByte(mask=mask, value=value, text=token.upper()))

    if not pattern:
        raise ValueError("pattern is empty")
    return pattern


def find_matches(data: bytes, pattern: list[PatternByte]) -> list[int]:
    width = len(pattern)
    matches = []
    for offset in range(0, len(data) - width + 1):
        if all(p.matches(data[offset + idx]) for idx, p in enumerate(pattern)):
            matches.append(offset)
    return matches


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def entropy(counter: Counter[int]) -> float:
    total = counter.total()
    if total == 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counter.values())


def signed_delta(a: int, b: int) -> int:
    delta = (b - a) & 0xFF
    return delta - 0x100 if delta >= 0x80 else delta


def run_lengths(values: list[int]) -> Counter[int]:
    runs = Counter()
    if not values:
        return runs
    current = values[0]
    length = 1
    for value in values[1:]:
        if value == current:
            length += 1
        else:
            runs[length] += 1
            current = value
            length = 1
    runs[length] += 1
    return runs


def longest_run(values: list[int]) -> tuple[int | None, int]:
    if not values:
        return None, 0
    best_value = values[0]
    best_length = 1
    current = values[0]
    length = 1
    for value in values[1:]:
        if value == current:
            length += 1
        else:
            if length > best_length:
                best_value = current
                best_length = length
            current = value
            length = 1
    if length > best_length:
        best_value = current
        best_length = length
    return best_value, best_length


def describe_sequence(values: list[int]) -> str:
    if len(values) < 2:
        return "n/a"

    deltas = [signed_delta(a, b) for a, b in zip(values, values[1:])]
    delta_counts = Counter(deltas)
    dominant_delta, dominant_count = delta_counts.most_common(1)[0]
    same_count = sum(1 for delta in deltas if delta == 0)
    inc_count = sum(1 for delta in deltas if delta > 0)
    dec_count = sum(1 for delta in deltas if delta < 0)

    parts = [
        f"delta {dominant_delta:+d} x{dominant_count}/{len(deltas)}",
        f"same {same_count}",
        f"inc {inc_count}",
        f"dec {dec_count}",
    ]
    if len(delta_counts) <= 5:
        parts.append("deltas " + ", ".join(f"{delta:+d}:{count}" for delta, count in sorted(delta_counts.items())))
    return "; ".join(parts)


def print_match_summary(data: bytes, matches: list[int], pattern_len: int) -> None:
    print("Match summary")
    print(f"  Count          : {len(matches)}")
    if not matches:
        return
    print(f"  First offset   : 0x{matches[0]:08X}")
    print(f"  Last offset    : 0x{matches[-1]:08X}")

    gaps = [b - a for a, b in zip(matches, matches[1:])]
    if gaps:
        print("  Match gaps     : " + ", ".join(
            f"0x{gap:X}/{gap} x{count}" for gap, count in Counter(gaps).most_common()
        ))
        payload_lengths = [gap - pattern_len for gap in gaps]
        print("  Bytes to next  : " + ", ".join(
            f"0x{size:X}/{size} x{count}" for size, count in Counter(payload_lengths).most_common()
        ))
    tail = len(data) - (matches[-1] + pattern_len)
    print(f"  Tail after last: 0x{tail:X}/{tail}")


def print_variant_summary(data: bytes, matches: list[int], pattern: list[PatternByte]) -> None:
    pattern_len = len(pattern)
    print()
    print("Matched pattern variants")
    variants = Counter(data[offset:offset + pattern_len] for offset in matches)
    for variant, count in variants.most_common():
        print(f"  {hex_bytes(variant):<{pattern_len * 3}} x{count}")

    print()
    print("Wildcard positions inside match")
    for idx, pat in enumerate(pattern):
        if pat.mask == 0xFF:
            continue
        values = Counter(data[offset + idx] for offset in matches)
        top = ", ".join(f"{value:02X}:{count}" for value, count in values.most_common(8))
        print(f"  +{idx:02d} {pat.text:<2} distinct={len(values):>3}  {top}")


def collect_following_windows(
    data: bytes,
    matches: list[int],
    pattern_len: int,
    follow: int,
    stop_at_next: bool,
) -> list[bytes]:
    windows = []
    for idx, offset in enumerate(matches):
        start = offset + pattern_len
        end = min(start + follow, len(data))
        if stop_at_next and idx + 1 < len(matches):
            end = min(end, matches[idx + 1])
        windows.append(data[start:end])
    return windows


def print_offset_deductions(windows: list[bytes], limit_top: int) -> None:
    width = max((len(window) for window in windows), default=0)
    print()
    print("Following-byte deductions")
    print("  rel  distinct  most common                                     entropy  longest run     sequence")
    print("  ---  --------  ----------------------------------------------  -------  --------------  ------------------------------")
    for rel in range(width):
        values = [window[rel] for window in windows if rel < len(window)]
        counts = Counter(values)
        top = " ".join(f"{value:02X}:{count}" for value, count in counts.most_common(limit_top))
        run_value, run_length = longest_run(values)
        run_text = "n/a" if run_value is None else f"{run_value:02X} x{run_length}"
        print(
            f"  +{rel:02d}  {len(counts):>8}  {top:<46}  "
            f"{entropy(counts):>7.3f}  {run_text:<14}  {describe_sequence(values)}"
        )


def print_transition_deductions(windows: list[bytes], max_offsets: int) -> None:
    width = min(max((len(window) for window in windows), default=0), max_offsets)
    print()
    print("Most common transitions by relative offset")
    for rel in range(width):
        values = [window[rel] for window in windows if rel < len(window)]
        transitions = Counter(zip(values, values[1:]))
        if not transitions:
            continue
        top = ", ".join(
            f"{left:02X}->{right:02X}:{count}"
            for (left, right), count in transitions.most_common(8)
        )
        print(f"  +{rel:02d}: {top}")


def print_sample_records(data: bytes, matches: list[int], pattern_len: int, follow: int, count: int) -> None:
    if count <= 0:
        return
    print()
    print("Sample records")
    for idx, offset in enumerate(matches[:count]):
        start = offset + pattern_len
        end = min(start + follow, len(data))
        print(f"  #{idx:04d} @ 0x{offset:08X}: match {hex_bytes(data[offset:start])}  follow {hex_bytes(data[start:end])}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find a wildcard hex pattern and infer structure from following bytes."
    )
    parser.add_argument("input", help="binary file to scan, or '-' to read binary data from stdin")
    parser.add_argument("pattern", help="hex pattern; '?' is a nibble wildcard")
    parser.add_argument("--start", type=parse_int, default=0, help="first file offset to scan")
    parser.add_argument("--end", type=parse_int, default=None, help="exclusive file offset to scan")
    parser.add_argument("--follow", type=parse_int, default=32, help="bytes to analyze after each match")
    parser.add_argument(
        "--stop-at-next",
        action="store_true",
        help="truncate each following window at the next pattern match",
    )
    parser.add_argument("--top", type=int, default=6, help="values to show per offset")
    parser.add_argument("--transitions", type=int, default=16, help="following offsets to include in transition report")
    parser.add_argument("--samples", type=int, default=8, help="sample matches to print")
    args = parser.parse_args()

    input_label, data = read_input(args.input)
    scan_end = len(data) if args.end is None else min(args.end, len(data))
    if args.start < 0 or args.start > scan_end:
        raise SystemExit(f"invalid scan range: start={args.start}, end={scan_end}")

    pattern = parse_pattern(args.pattern)
    scan = data[args.start:scan_end]
    matches = [args.start + offset for offset in find_matches(scan, pattern)]

    print(f"Input          : {input_label} ({len(data):,} bytes)")
    print(f"Scan range     : 0x{args.start:X}..0x{scan_end:X} ({scan_end - args.start:,} bytes)")
    print(f"Pattern        : {' '.join(p.text for p in pattern)} ({len(pattern)} bytes)")
    print(f"Follow window  : {args.follow} bytes")
    print()

    print_match_summary(data, matches, len(pattern))
    if not matches:
        return

    print_variant_summary(data, matches, pattern)
    windows = collect_following_windows(data, matches, len(pattern), args.follow, args.stop_at_next)
    print_offset_deductions(windows, args.top)
    print_transition_deductions(windows, args.transitions)
    print_sample_records(data, matches, len(pattern), args.follow, args.samples)


if __name__ == "__main__":
    main()
