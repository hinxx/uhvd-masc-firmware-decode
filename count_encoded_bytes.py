#!/usr/bin/env python3
import argparse
import re
import sys
from collections import Counter


def parse_hex_text(text: str) -> bytes:
    """
    Accepts:
      99 62 6e a7  8e 5c 8b 6d
      0x99, 0x62, 0x6e, 0xa7
      99626ea78e5c8b6d
    """
    # First try tokenized bytes.
    tokens = re.findall(r"(?:0x)?([0-9a-fA-F]{2})\b", text)

    if tokens:
        return bytes(int(t, 16) for t in tokens)

    # Fallback: compact hex string.
    compact = re.sub(r"[^0-9a-fA-F]", "", text)
    if not compact:
        return b""

    if len(compact) % 2:
        raise ValueError("Odd number of hex digits in compact input")

    return bytes(int(compact[i:i + 2], 16) for i in range(0, len(compact), 2))


def read_input(args) -> bytes:
    if args.hex:
        return parse_hex_text(" ".join(args.hex))

    if args.text_file:
        with open(args.text_file, "r", encoding="utf-8", errors="replace") as f:
            return parse_hex_text(f.read())

    if args.binary_file:
        with open(args.binary_file, "rb") as f:
            return f.read()

    if not sys.stdin.isatty():
        return parse_hex_text(sys.stdin.read())

    raise SystemExit("No input given. Use --bin FILE, --text FILE, or pass hex bytes.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate occurrence list for encoded byte values in a file/hex stream."
    )

    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--bin",
        dest="binary_file",
        help="Read raw binary file bytes.",
    )
    src.add_argument(
        "--text",
        dest="text_file",
        help="Read text file containing hex dump bytes.",
    )

    parser.add_argument(
        "hex",
        nargs="*",
        help="Hex bytes directly on command line, e.g. '99 62 6e a7'.",
    )

    parser.add_argument(
        "--sort",
        choices=["value", "count"],
        default="value",
        help="Sort occurrence table by byte value or by descending count. Default: value.",
    )

    parser.add_argument(
        "--only-seen",
        action="store_true",
        help="Only print byte values that occur at least once.",
    )

    args = parser.parse_args()
    data = read_input(args)

    counts = Counter(data)
    total = len(data)

    print(f"Total encoded bytes: {total}")
    print()

    rows = []
    for value in range(256):
        count = counts.get(value, 0)
        if args.only_seen and count == 0:
            continue

        percent = (count / total * 100.0) if total else 0.0
        rows.append((value, count, percent))

    if args.sort == "count":
        rows.sort(key=lambda r: (-r[1], r[0]))

    print("Encoded byte occurrence table:")
    print("hex  dec  count       percent")
    print("---  ---  ----------  --------")
    for value, count, percent in rows:
        print(f"{value:02X}   {value:3d}  {count:10d}  {percent:7.3f}%")

    never_seen = [v for v in range(256) if counts.get(v, 0) == 0]

    print()
    print(f"Never seen byte values: {len(never_seen)}")
    if never_seen:
        print(" ".join(f"{v:02X}" for v in never_seen))
    else:
        print("(none)")

    print()
    print(f"Seen byte values: {256 - len(never_seen)}")
    seen = [v for v in range(256) if counts.get(v, 0) > 0]
    print(" ".join(f"{v:02X}" for v in seen))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
