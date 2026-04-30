#!/usr/bin/env python3
import argparse
import re
import sys


def parse_hex_stream(text: str) -> bytes:
    """
    Accepts input like:
      99 62 6e a7  8e 5c 8b 6d
      99626ea78e5c8b6d
      0x99, 0x62, 0x6e, 0xa7
    """
    text = text.replace("0x", " ").replace("0X", " ")

    # Collect all hex byte-looking tokens.
    tokens = re.findall(r"\b[0-9a-fA-F]{1,2}\b", text)

    if not tokens:
        # Fallback for compact hex string without spaces.
        compact = re.sub(r"[^0-9a-fA-F]", "", text)
        if len(compact) % 2:
            raise ValueError("Odd number of hex digits in compact input")
        tokens = [compact[i:i + 2] for i in range(0, len(compact), 2)]

    return bytes(int(t, 16) for t in tokens)


def decode_chunk(e0: int, e1: int, e2: int, e3: int):
    """
    Tentative decode formula inferred from:

      99 62 6e a4 -> 54 e1 -> JMP
      99 62 6e a7 -> 54 e2 -> JSR

    Decoded file byte order is little-endian per 16-bit MCU word:
      bytes 54 e2 -> MCU word 0xe254
    """
    b0 = e0 ^ e1 ^ 0xAF
    b1 = e2 ^ e3 ^ 0x2B
    word = b0 | (b1 << 8)
    return b0, b1, word


def decode_stream(data: bytes):
    if len(data) % 4:
        raise ValueError(
            f"Encoded byte count must be divisible by 4; got {len(data)} bytes"
        )

    decoded_bytes = bytearray()
    decoded_words = []

    for i in range(0, len(data), 4):
        b0, b1, word = decode_chunk(data[i], data[i + 1], data[i + 2], data[i + 3])
        decoded_bytes.extend([b0, b1])
        decoded_words.append(word)

    return bytes(decoded_bytes), decoded_words


def fmt_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def fmt_words(words) -> str:
    return " ".join(f"{w:04X}" for w in words)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decode 4-byte encoded firmware chunks into DSP56800E MCU words."
    )
    parser.add_argument(
        "hex",
        nargs="*",
        help="Hex bytes to decode, e.g. '99 62 6e a7 8e 5c 8b 6d'. "
             "If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="Show per-chunk decode table.",
    )

    args = parser.parse_args()

    text = " ".join(args.hex) if args.hex else sys.stdin.read()
    data = parse_hex_stream(text)

    decoded_bytes, decoded_words = decode_stream(data)

    print("encoded bytes:")
    print(fmt_bytes(data))
    print()
    print("decoded bytes, little-endian word order:")
    print(fmt_bytes(decoded_bytes))
    print()
    print("decoded MCU words:")
    print(fmt_words(decoded_words))

    if args.table:
        print()
        print("offset  encoded              decoded-bytes  mcu-word")
        print("------  -------------------  -------------  --------")
        for chunk_index, i in enumerate(range(0, len(data), 4)):
            e = data[i:i + 4]
            b0, b1, word = decode_chunk(e[0], e[1], e[2], e[3])
            print(
                f"{i:06X}  {fmt_bytes(e):19s}  "
                f"{b0:02X} {b1:02X}          0x{word:04X}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
