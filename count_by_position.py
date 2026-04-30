#!/usr/bin/env python3
from collections import Counter

filename = "firmware-no-header.elf.e"

with open(filename, "rb") as f:
    data = f.read()

print(f"Total bytes: {len(data)}")
print(f"Total 4-byte groups: {len(data) // 4}")
print(f"Remainder: {len(data) % 4}")
print()

for pos in range(4):
    sub = data[pos::4]
    c = Counter(sub)
    seen = sorted(c)
    missing = [x for x in range(256) if x not in c]

    print("=" * 72)
    print(f"Encoded byte position {pos} within each 4-byte group")
    print(f"Bytes at this position: {len(sub)}")
    print(f"Seen values: {len(seen)}")
    print(f"Missing values: {len(missing)}")
    print()

    print("Most common:")
    for value, count in c.most_common(20):
        pct = count / len(sub) * 100.0
        print(f"  {value:02X}  {count:8d}  {pct:7.3f}%")

    print()
    print("Seen values:")
    print(" ".join(f"{x:02X}" for x in seen))
    print()
    print("Never seen values:")
    print(" ".join(f"{x:02X}" for x in missing))
    print()
