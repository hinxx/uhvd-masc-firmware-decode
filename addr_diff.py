import csv
import sys

def main(path="bytes1.csv"):
    offsets = []
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr = int(row["offset"], 16)
            offsets.append(addr)
            rows.append(row)

    print(f"{'Row':>5}  {'Offset':>12}  {'Diff':>10}  {'Diff(dec)':>10}")
    print("-" * 46)
    for i, (addr, row) in enumerate(zip(offsets, rows)):
        if i == 0:
            diff_str = "         -"
            diff_dec = "         -"
        else:
            d = addr - offsets[i - 1]
            diff_str = f"0x{d:08X}"
            diff_dec = f"{d:10d}"
        print(f"{i:>5}  0x{addr:010X}  {diff_str}  {diff_dec}")

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "bytes1.csv"
    main(path)
