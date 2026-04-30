#!/usr/bin/env python3
from collections import Counter

filename = "firmware-no-header.elf.e"
jsr = bytes.fromhex("99 62 6E A7")

def tentative_decode_group(g):
    e0, e1, e2, e3 = g
    b0 = e0 ^ e1 ^ 0xAF
    b1 = e2 ^ e3 ^ 0x2B
    return b0 | (b1 << 8)

with open(filename, "rb") as f:
    data = f.read()

next_groups = Counter()
next_words = Counter()

for i in range(0, len(data) - 7, 4):
    if data[i:i+4] == jsr:
        ng = tuple(data[i+4:i+8])
        next_groups[ng] += 1
        next_words[tentative_decode_group(ng)] += 1

print("Most common encoded groups after JSR:")
for g, count in next_groups.most_common(50):
    word = tentative_decode_group(g)
    print(
        " ".join(f"{x:02X}" for x in g),
        f"{count:6d}",
        f"tentative word 0x{word:04X}"
    )

print()
print("Most common tentative words after JSR:")
for word, count in next_words.most_common(50):
    print(f"0x{word:04X} {count:6d}")
