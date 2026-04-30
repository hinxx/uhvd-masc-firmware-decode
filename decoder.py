#!/usr/bin/env python3
"""
decoder.py — MC56F8345 / DSP56800E firmware decoder

Converts firmware.elf.e (Applied Motion Products proprietary format) to raw
DSP56800E binary words ready for disassembly.

Encoding formula (confirmed):
    decoded_high = encoded_group[0] ^ 0xCD
    decoded_low  = encoded_group[3] ^ 0x45

Frame structure:
    - 13-byte ASCII header: "1.07R,22\\r\\n" + preamble bytes 80 af a9
    - Frames delimited by 8-byte markers embedded in the payload
    - Regular marker:  99 56 87 68 9c 66 1b a5  (1,146 occurrences)
    - Variant marker:  99 55 83 68 9c 66 1b a5  (first frame only)
    - Each frame block: 4-byte header group (byte[0]=0xfa) + N data groups
                        + 4-byte trailer group (byte[0]=0x95)
"""

import sys
import argparse
from collections import Counter

HEADER_SIZE = 13
MARKERS = {
    bytes.fromhex('995687689c661ba5'),  # regular
    bytes.fromhex('995583689c661ba5'),  # variant (first frame)
}
XOR_HIGH = 0xCD
XOR_LOW  = 0x45

# Known 16-bit word encodings for pretty-printing
OPCODE_NAMES = {
    0x54e1: 'JMP',
    0x54e2: 'JSR',
}


def find_markers(payload: bytes) -> list[int]:
    positions = []
    i = 0
    while i <= len(payload) - 8:
        if payload[i:i+8] in MARKERS:
            positions.append(i)
            i += 8
        else:
            i += 1
    return positions


def extract_blocks(payload: bytes, positions: list[int]) -> list[bytes]:
    """Return the data slice between each pair of consecutive markers."""
    blocks = []
    for idx, pos in enumerate(positions):
        start = pos + 8
        end = positions[idx + 1] if idx + 1 < len(positions) else len(payload)
        blocks.append(payload[start:end])
    return blocks


def decode_blocks(blocks: list[bytes], strip_noise: bool = True) -> bytes:
    """
    Decode each block's 4-byte groups to 2-byte DSP words.

    strip_noise=True removes the frame header (first group, byte[0]==0xfa)
    and frame trailer (last group, byte[0]==0x95) from every block.
    """
    decoded = bytearray()
    for block in blocks:
        groups = len(block) // 4
        for gi in range(groups):
            g = block[gi * 4: gi * 4 + 4]
            if strip_noise and (gi == 0 or gi == groups - 1):
                continue
            decoded.append(g[0] ^ XOR_HIGH)
            decoded.append(g[3] ^ XOR_LOW)
    return bytes(decoded)


def word_at(data: bytes, byte_offset: int) -> int:
    return (data[byte_offset] << 8) | data[byte_offset + 1]


def print_vector_table(decoded: bytes, count: int = 82) -> None:
    """Print the first `count` 2-word vector entries from the decoded binary."""
    print(f"\n{'='*60}")
    print(f"Vector table (first {count} entries, 2 words each)")
    print(f"{'='*60}")
    print(f"{'Vec':>4}  {'Opcode':>6}  {'Mnem':<6}  {'Target':>6}")
    print(f"{'-'*4}  {'-'*6}  {'-'*6}  {'-'*6}")

    offset = 0
    for vec in range(count):
        if offset + 3 >= len(decoded):
            break
        opcode = word_at(decoded, offset)
        target = word_at(decoded, offset + 2)
        mnem = OPCODE_NAMES.get(opcode, '???')
        print(f"{vec:>4}  0x{opcode:04x}  {mnem:<6}  0x{target:04x}")
        offset += 4

    print()


def print_stats(decoded: bytes, vector_count: int = 82) -> None:
    """Print statistics about the decoded binary."""
    print(f"\n{'='*60}")
    print(f"Decoded firmware statistics")
    print(f"{'='*60}")
    print(f"  Total decoded bytes : {len(decoded):,}")
    print(f"  Total 16-bit words  : {len(decoded)//2:,}")

    # Vector table summary
    vec_end = vector_count * 4
    vec_data = decoded[:vec_end] if len(decoded) >= vec_end else decoded
    opc_counts: Counter = Counter()
    tgt_counts: Counter = Counter()
    for off in range(0, len(vec_data) - 3, 4):
        opc = word_at(vec_data, off)
        tgt = word_at(vec_data, off + 2)
        opc_counts[opc] += 1
        tgt_counts[tgt] += 1

    print(f"\n  Vector table (first {vector_count} entries):")
    for opc, cnt in opc_counts.most_common():
        mnem = OPCODE_NAMES.get(opc, '???')
        print(f"    0x{opc:04x} ({mnem:<3}): {cnt} entries")
    print(f"\n  Most common targets:")
    for tgt, cnt in tgt_counts.most_common(5):
        print(f"    0x{tgt:04x}: {cnt} vectors")

    # Code section opcode diversity
    code = decoded[vec_end:]
    if code:
        words = [(code[i] << 8) | code[i + 1] for i in range(0, len(code) - 1, 2)]
        word_counts: Counter = Counter(words)
        unique_words = len(word_counts)
        diversity = unique_words / len(words) if words else 0
        print(f"\n  Code section ({len(code):,} bytes, {len(words):,} words):")
        print(f"    Unique words      : {unique_words}")
        print(f"    Diversity ratio   : {diversity:.3f}  (unique/total words)")
        print(f"    Top-5 words       : {[(hex(w), c) for w, c in word_counts.most_common(5)]}")


def hexdump(data: bytes, start: int = 0, length: int = 256,
            word_address: int = 0) -> None:
    """Hex dump with word-addressed labels (DSP56800E addresses words, not bytes)."""
    end = min(start + length, len(data))
    for off in range(start, end, 16):
        row = data[off:off + 16]
        # Word address = byte offset / 2
        waddr = (off + word_address * 2) // 2
        hex_part = ' '.join(f'{b:02x}' for b in row)
        print(f"  {waddr:06x}  {hex_part}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Decode MC56F8345 firmware.elf.e to raw DSP56800E binary'
    )
    parser.add_argument('input', help='Input file (firmware.elf.e)')
    parser.add_argument('-o', '--output', default='firmware_decoded.bin',
                        help='Output binary (default: firmware_decoded.bin)')
    parser.add_argument('--no-strip', action='store_true',
                        help='Disable frame noise stripping (include header/trailer groups)')
    parser.add_argument('--vectors', type=int, default=82,
                        help='Number of vector table entries to show (default: 82)')
    parser.add_argument('--dump', action='store_true',
                        help='Print hex dump of the first 512 decoded bytes')
    parser.add_argument('--stats', action='store_true',
                        help='Print decoding statistics')
    args = parser.parse_args()

    with open(args.input, 'rb') as f:
        raw = f.read()

    header = raw[:HEADER_SIZE]
    payload = raw[HEADER_SIZE:]

    print(f"Input           : {args.input} ({len(raw):,} bytes)")
    print(f"Header          : {header[:10].decode('ascii', errors='replace')!r} "
          f"+ preamble {header[10:].hex()}")

    positions = find_markers(payload)
    print(f"Frame markers   : {len(positions)} found")
    if positions:
        print(f"  First marker  : payload offset {positions[0]} (raw {positions[0]+HEADER_SIZE})")
        print(f"  Variant frame : {'yes' if payload[0:8] in MARKERS else 'no (expected yes)'}")

    blocks = extract_blocks(payload, positions)

    strip = not args.no_strip
    decoded = decode_blocks(blocks, strip_noise=strip)
    print(f"Strip noise     : {'yes' if strip else 'no'}")
    print(f"Decoded size    : {len(decoded):,} bytes ({len(decoded)//2:,} words, "
          f"{len(decoded)/1024:.1f} KB)")

    with open(args.output, 'wb') as f:
        f.write(decoded)
    print(f"Output          : {args.output}")

    print_vector_table(decoded, args.vectors)

    if args.stats:
        print_stats(decoded, args.vectors)

    if args.dump:
        print(f"\nHex dump (first 512 decoded bytes, word-addressed):")
        hexdump(decoded, 0, 512)


if __name__ == '__main__':
    main()
