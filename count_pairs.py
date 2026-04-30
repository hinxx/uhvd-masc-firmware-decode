#!/usr/bin/env python3
from collections import Counter

filename = "firmware-no-header.elf.e"

with open(filename, "rb") as f:
    data = f.read()

assert len(data) % 4 == 0

pairs01 = Counter()
pairs23 = Counter()
groups = Counter()

for i in range(0, len(data), 4):
    e0, e1, e2, e3 = data[i:i+4]
    pairs01[(e0, e1)] += 1
    pairs23[(e2, e3)] += 1
    groups[(e0, e1, e2, e3)] += 1

print(f"Groups: {len(data)//4}")
print(f"Unique full 4-byte groups: {len(groups)}")
print(f"Unique pos0,pos1 pairs: {len(pairs01)}")
print(f"Unique pos2,pos3 pairs: {len(pairs23)}")
print()

print("Most common pos0,pos1 pairs:")
for (a, b), count in pairs01.most_common(30):
    print(f"{a:02X} {b:02X}  {count:8d}")

print()
print("Most common pos2,pos3 pairs:")
for (a, b), count in pairs23.most_common(30):
    print(f"{a:02X} {b:02X}  {count:8d}")

print()
print("Most common full groups:")
for g, count in groups.most_common(30):
    print(" ".join(f"{x:02X}" for x in g), f"{count:8d}")
