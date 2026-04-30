#!/usr/bin/env python3
from collections import Counter, defaultdict

INFILE = "firmware-no-header.elf.e"

JSR_WORD = 0xE254
JMP_WORD = 0xE154
RTS_WORD = 0xE708

def dec_group(g):
    e0, e1, e2, e3 = g
    b0 = e0 ^ e1 ^ 0xAF
    b1 = e2 ^ e3 ^ 0x2B
    return b0 | (b1 << 8)

def fmt_group(g):
    return " ".join(f"{x:02X}" for x in g)

with open(INFILE, "rb") as f:
    enc = f.read()

if len(enc) % 4:
    raise SystemExit("encoded length not divisible by 4")

groups = [tuple(enc[i:i+4]) for i in range(0, len(enc), 4)]
words = [dec_group(g) for g in groups]

print(f"Encoded groups / decoded words: {len(words)}")
print()

# 1. Find tentative RTS encoded groups.
rts_groups = Counter()
rts_indices = []

for idx, (g, w) in enumerate(zip(groups, words)):
    if w == RTS_WORD:
        rts_groups[g] += 1
        rts_indices.append(idx)

print(f"Tentative RTS word 0x{RTS_WORD:04X} occurrences: {len(rts_indices)}")
print("RTS encoded groups:")
for g, n in rts_groups.most_common(20):
    print(f"  {fmt_group(g)}  {n}")
print()

# 2. Find JSR occurrences and their following address word.
jsr_calls = []
for idx in range(len(words) - 1):
    if words[idx] == JSR_WORD:
        target = words[idx + 1]
        jsr_calls.append((idx, target))

print(f"Tentative JSR occurrences: {len(jsr_calls)}")
print()

# 3. Check whether target-1, target-2, ..., target-window contains RTS.
WINDOW = 16
hits_by_distance = Counter()
targets_checked = 0
targets_out_of_range = 0

for call_idx, target in jsr_calls:
    # If target is a word address into this decoded word array:
    if 0 <= target < len(words):
        targets_checked += 1

        for dist in range(1, WINDOW + 1):
            probe = target - dist
            if 0 <= probe < len(words) and words[probe] == RTS_WORD:
                hits_by_distance[dist] += 1
                break
    else:
        targets_out_of_range += 1

print(f"JSR targets in decoded-word range: {targets_checked}")
print(f"JSR targets out of range:         {targets_out_of_range}")
print()

print(f"RTS found within {WINDOW} words before JSR target:")
if hits_by_distance:
    for dist, count in sorted(hits_by_distance.items()):
        print(f"  target - {dist:2d}: {count}")
else:
    print("  none")
print()

# 4. Print a few examples around target functions.
shown = 0
for call_idx, target in jsr_calls:
    if not (0 <= target < len(words)):
        continue

    found_dist = None
    for dist in range(1, WINDOW + 1):
        probe = target - dist
        if 0 <= probe < len(words) and words[probe] == RTS_WORD:
            found_dist = dist
            break

    if found_dist is None:
        continue

    print("=" * 72)
    print(f"CALL at word index 0x{call_idx:04X} -> target 0x{target:04X}")
    print(f"RTS found at target - {found_dist}")
    print()

    start = max(0, target - 8)
    end = min(len(words), target + 12)

    for i in range(start, end):
        marker = ""
        if i == target:
            marker = "<-- target"
        elif words[i] == RTS_WORD:
            marker = "<-- RTS"

        print(f"{i:05X}: {fmt_group(groups[i])}  ->  0x{words[i]:04X} {marker}")

    shown += 1
    if shown >= 10:
        break