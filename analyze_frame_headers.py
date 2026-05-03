#!/usr/bin/env python3
"""
Analyze encoded frame headers captured in frames.txt.

The script treats each 12-byte record as:

    [sync 4][FFFF marker 4][metadata/address 4]

and reuses the POSITION_PAYLOAD_ALPHABETS / POSITION_MASKS pair-ID logic from
dsp56800e_decoder.py.  It is meant for testing theories about:

* whether sync bytes 3..4 encode a frame length/count value;
* whether metadata bytes 9..12 encode a monotonic address/counter;
* whether those theories are consistent with the current FFFF marker anchor.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dsp56800e_decoder import PAIR_INDEX


HEX_BYTE_RE = re.compile(r"^[0-9a-fA-F]{2}$")


@dataclass(frozen=True)
class HeaderRow:
    index: int
    offset: int
    listed_delta: int | None
    size_to_next: int | None
    raw: bytes

    @property
    def sync(self) -> bytes:
        return self.raw[:4]

    @property
    def marker(self) -> bytes:
        return self.raw[4:8]

    @property
    def metadata(self) -> bytes:
        return self.raw[8:12]

    @property
    def is_data_frame(self) -> bool:
        # Byte positions are one-based in the notes: byte 2 == 0x56 means data.
        return self.sync[1] == 0x56


@dataclass(frozen=True)
class Constraint:
    source: str
    pos: int
    pair_id: int
    nibble: int


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def pair_ids(codeword: bytes) -> tuple[int, int, int, int]:
    if len(codeword) != 4:
        raise ValueError("expected a 4-byte codeword")
    return tuple(PAIR_INDEX[pos][codeword[pos]] for pos in range(4))


def word_constraints(source: str,
                     codeword: bytes,
                     value: int,
                     nibble_order: tuple[int, int, int, int],
                     ) -> list[Constraint]:
    pids = pair_ids(codeword)
    constraints = []
    for pos, pid in enumerate(pids):
        shift = 12 - 4 * nibble_order[pos]
        constraints.append(Constraint(
            source=source,
            pos=pos,
            pair_id=pid,
            nibble=(value >> shift) & 0xF,
        ))
    return constraints


def byte23_constraints(source: str,
                       sync: bytes,
                       value: int,
                       ) -> list[Constraint]:
    # Hypothesis: sync byte 3 carries the high nibble and sync byte 4 carries
    # the low nibble of an 8-bit length/count value.
    return [
        Constraint(source, 2, PAIR_INDEX[2][sync[2]], (value >> 4) & 0xF),
        Constraint(source, 3, PAIR_INDEX[3][sync[3]], value & 0xF),
    ]


def check_constraints(constraints: Iterable[Constraint]) -> dict:
    assigned: list[dict[int, tuple[int, str]]] = [dict() for _ in range(4)]
    by_nibble: list[dict[int, list[tuple[int, str]]]] = [
        defaultdict(list) for _ in range(4)
    ]
    contradictions = []

    for c in constraints:
        prev = assigned[c.pos].get(c.pair_id)
        if prev is not None and prev[0] != c.nibble:
            contradictions.append({
                "pos": c.pos,
                "pair_id": c.pair_id,
                "old_nibble": prev[0],
                "old_source": prev[1],
                "new_nibble": c.nibble,
                "new_source": c.source,
            })
            continue
        assigned[c.pos][c.pair_id] = (c.nibble, c.source)

    duplicates = []
    for pos in range(4):
        for pair_id, (nibble, source) in assigned[pos].items():
            by_nibble[pos][nibble].append((pair_id, source))
        for nibble, uses in by_nibble[pos].items():
            if len(uses) > 1:
                duplicates.append({
                    "pos": pos,
                    "nibble": nibble,
                    "uses": uses,
                })

    return {
        "ok": not contradictions and not duplicates,
        "assigned": assigned,
        "contradictions": contradictions,
        "duplicates": duplicates,
        "pinned_counts": [len(a) for a in assigned],
    }


def parse_frames_txt(path: Path, binary_size: int | None = None) -> list[HeaderRow]:
    partial_rows = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        parts = line.split()
        if len(parts) < 14:
            continue
        try:
            offset = int(parts[0], 16)
        except ValueError:
            continue
        listed_delta = None if parts[1] == "-" else int(parts[1], 0)
        byte_tokens = [p for p in parts[2:] if HEX_BYTE_RE.match(p)]
        if len(byte_tokens) < 12:
            continue
        raw = bytes(int(tok, 16) for tok in byte_tokens[:12])
        partial_rows.append((offset, listed_delta, raw))

    rows = []
    for idx, (offset, listed_delta, raw) in enumerate(partial_rows):
        if idx + 1 < len(partial_rows):
            size_to_next = partial_rows[idx + 1][0] - offset
        elif binary_size is not None:
            size_to_next = binary_size - offset
        else:
            size_to_next = None
        rows.append(HeaderRow(
            index=idx,
            offset=offset,
            listed_delta=listed_delta,
            size_to_next=size_to_next,
            raw=raw,
        ))
    return rows


def payload_encoded_size(row: HeaderRow) -> int | None:
    if row.size_to_next is None:
        return None
    return max(row.size_to_next - 16, 0)


def payload_decoded_bytes(row: HeaderRow) -> int | None:
    enc = payload_encoded_size(row)
    return None if enc is None else enc // 2


def payload_words(row: HeaderRow) -> int | None:
    enc = payload_encoded_size(row)
    return None if enc is None else enc // 4


def length_hypotheses(row: HeaderRow) -> dict[str, int]:
    out = {}
    if row.size_to_next is None:
        return out
    out["encoded_frame_size"] = row.size_to_next & 0xFF
    out["decoded_total_bytes"] = (row.size_to_next // 2) & 0xFF
    out["payload_encoded_bytes"] = payload_encoded_size(row) & 0xFF
    out["payload_decoded_bytes"] = payload_decoded_bytes(row) & 0xFF
    out["payload_words"] = payload_words(row) & 0xFF
    return out


def print_header_summary(rows: list[HeaderRow]) -> None:
    print("Input")
    print(f"  rows parsed: {len(rows)}")
    if not rows:
        return
    print(f"  first offset: 0x{rows[0].offset:x}")
    print(f"  last offset : 0x{rows[-1].offset:x}")

    sizes = Counter(r.size_to_next for r in rows)
    print("\nFrame size to next header")
    for size, count in sizes.most_common():
        label = "unknown" if size is None else f"0x{size:x}/{size}"
        print(f"  {label:>12}  x{count}")

    print("\nSync variants")
    variants = defaultdict(list)
    for row in rows:
        variants[row.sync].append(row)
    for sync, group in sorted(variants.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        size_counts = Counter(r.size_to_next for r in group)
        size_text = ", ".join(
            ("unknown" if s is None else f"0x{s:x}") + f":{n}"
            for s, n in size_counts.most_common()
        )
        print(
            f"  {hex_bytes(sync)}  x{len(group):4d}  "
            f"pair_ids={pair_ids(sync)}  sizes={size_text}"
        )

    print("\nMarker and first metadata pair IDs")
    first = rows[0]
    print(f"  marker   {hex_bytes(first.marker)}  pair_ids={pair_ids(first.marker)}")
    print(f"  metadata {hex_bytes(first.metadata)}  pair_ids={pair_ids(first.metadata)}")
    if pair_ids(first.marker) == pair_ids(first.metadata):
        print("  note: first metadata is the same logical codeword as the marker")


def analyze_sync_lengths(rows: list[HeaderRow], include_marker_anchor: bool) -> None:
    print("\nSync bytes 3..4 length/count hypotheses")
    names = [
        "encoded_frame_size",
        "decoded_total_bytes",
        "payload_encoded_bytes",
        "payload_decoded_bytes",
        "payload_words",
    ]
    base = []
    if include_marker_anchor and rows:
        base = word_constraints("marker=0xffff", rows[0].marker, 0xFFFF, (0, 1, 2, 3))

    for name in names:
        constraints = list(base)
        examples = {}
        used = 0
        for row in rows:
            values = length_hypotheses(row)
            if name not in values:
                continue
            used += 1
            value = values[name]
            constraints.extend(byte23_constraints(
                f"row {row.index} sync={hex_bytes(row.sync)} size=0x{row.size_to_next:x} -> {name}=0x{value:02x}",
                row.sync,
                value,
            ))
            examples.setdefault(row.sync, value)
        result = check_constraints(constraints)
        status = "OK" if result["ok"] else "conflict"
        print(
            f"  {name:<22} {status:<8} rows={used:<4d} "
            f"pinned={result['pinned_counts']}"
        )
        if result["contradictions"]:
            c = result["contradictions"][0]
            print(
                f"    first contradiction: pos{c['pos']} pid{c['pair_id']:X} "
                f"{c['old_nibble']:X} from {c['old_source']} vs "
                f"{c['new_nibble']:X} from {c['new_source']}"
            )
        if result["ok"]:
            for sync, value in sorted(examples.items()):
                print(f"    {hex_bytes(sync)} -> 0x{value:02x}")


def expected_counter_values(rows: list[HeaderRow],
                            step_name: str,
                            *,
                            data_only: bool,
                            down_from_ffff: bool,
                            start_value: int | None = None,
                            emit_after_step: bool = False,
                            ) -> list[tuple[HeaderRow, int]]:
    if start_value is None:
        total = 0
    else:
        total = start_value
    out = []
    for row in rows:
        if step_name == "frame_index":
            step = 1
        elif step_name == "encoded_frame_size":
            step = row.size_to_next
        elif step_name == "payload_encoded_bytes":
            step = payload_encoded_size(row)
        elif step_name == "payload_decoded_bytes":
            step = payload_decoded_bytes(row)
        elif step_name == "payload_words":
            step = payload_words(row)
        else:
            raise ValueError(f"unknown step model {step_name!r}")

        should_advance = not (data_only and not row.is_data_frame)
        if emit_after_step and should_advance and step is not None:
            total = (total - step) if down_from_ffff else (total + step)

        value = (0xFFFF - total) & 0xFFFF if start_value is None and down_from_ffff else total & 0xFFFF
        out.append((row, value))

        if not emit_after_step and should_advance and step is not None:
            total = (total - step) if start_value is not None and down_from_ffff else total + step

    return out


def total_counter_start(rows: list[HeaderRow],
                        step_name: str,
                        *,
                        data_only: bool,
                        ) -> int:
    total = 0
    for row in rows:
        if data_only and not row.is_data_frame:
            continue
        if step_name == "frame_index":
            step = 1
        elif step_name == "encoded_frame_size":
            step = row.size_to_next
        elif step_name == "payload_encoded_bytes":
            step = payload_encoded_size(row)
        elif step_name == "payload_decoded_bytes":
            step = payload_decoded_bytes(row)
        elif step_name == "payload_words":
            step = payload_words(row)
        else:
            raise ValueError(f"unknown step model {step_name!r}")
        if step is not None:
            total += step
    return total & 0xFFFF


def analyze_metadata_counter(rows: list[HeaderRow],
                             include_marker_anchor: bool,
                             nibble_order: tuple[int, int, int, int],
                             show_best: int,
                             include_unknown_size_rows: bool,
                             ) -> None:
    print("\nMetadata bytes 9..12 counter/address hypotheses")
    model_rows = rows if include_unknown_size_rows else [
        row for row in rows if row.size_to_next is not None
    ]
    skipped = len(rows) - len(model_rows)
    if skipped:
        print(f"  skipped {skipped} row(s) without a known size")
    base = []
    if include_marker_anchor and model_rows:
        base = word_constraints("marker=0xffff", model_rows[0].marker, 0xFFFF, nibble_order)

    step_names = [
        "frame_index",
        "encoded_frame_size",
        "payload_encoded_bytes",
        "payload_decoded_bytes",
        "payload_words",
    ]
    results = []
    for step_name in step_names:
        for data_only in (False, True):
            for down in (False, True):
                constraints = list(base)
                values = expected_counter_values(
                    model_rows,
                    step_name,
                    data_only=data_only,
                    down_from_ffff=down,
                )
                for row, value in values:
                    constraints.extend(word_constraints(
                        f"row {row.index} metadata={hex_bytes(row.metadata)} -> 0x{value:04x}",
                        row.metadata,
                        value,
                        nibble_order,
                    ))
                result = check_constraints(constraints)
                score = (
                    len(result["contradictions"]),
                    len(result["duplicates"]),
                    -sum(result["pinned_counts"]),
                )
                results.append((score, step_name, data_only, down, result, values))

    results.sort(key=lambda item: item[0])
    for score, step_name, data_only, down, result, values in results[:show_best]:
        direction = "down_from_ffff" if down else "up_from_zero"
        scope = "data_frames_advance" if data_only else "all_frames_advance"
        status = "OK" if result["ok"] else "conflict"
        print(
            f"  {step_name:<22} {direction:<15} {scope:<19} "
            f"{status:<8} contradictions={len(result['contradictions']):<4d} "
            f"duplicates={len(result['duplicates']):<3d} pinned={result['pinned_counts']}"
        )
        if result["contradictions"]:
            c = result["contradictions"][0]
            print(
                f"    first contradiction: pos{c['pos']} pid{c['pair_id']:X} "
                f"{c['old_nibble']:X} from {c['old_source']} vs "
                f"{c['new_nibble']:X} from {c['new_source']}"
            )
        if result["ok"]:
            print("    first values:")
            for row, value in values[:8]:
                step = payload_words(row)
                step_text = "?" if step is None else str(step)
                print(
                    f"      row {row.index:4d} off=0x{row.offset:06x} "
                    f"sync={hex_bytes(row.sync)} meta={hex_bytes(row.metadata)} "
                    f"payload_words={step_text:>2} -> 0x{value:04x}"
                )
            print("    derived constraints:")
            for pos, mapping in enumerate(result["assigned"]):
                items = sorted((pid, nib) for pid, (nib, _) in mapping.items())
                text = " ".join(f"pid{pid:X}={nib:X}" for pid, nib in items)
                print(f"      pos{pos}: {text}")


def emit_constraints_json(rows: list[HeaderRow],
                          step_name: str,
                          data_only: bool,
                          down_from_ffff: bool,
                          include_marker_anchor: bool,
                          nibble_order: tuple[int, int, int, int],
                          include_unknown_size_rows: bool,
                          start_payload_size: bool,
                          emit_after_step: bool,
                          ) -> None:
    model_rows = rows if include_unknown_size_rows else [
        row for row in rows if row.size_to_next is not None
    ]
    start_value = None
    if start_payload_size:
        start_value = total_counter_start(
            model_rows,
            step_name,
            data_only=data_only,
        )
    constraints = []
    if include_marker_anchor and model_rows:
        constraints.extend(word_constraints("marker=0xffff", model_rows[0].marker, 0xFFFF, nibble_order))
    for row, value in expected_counter_values(
        model_rows,
        step_name,
        data_only=data_only,
        down_from_ffff=down_from_ffff,
        start_value=start_value,
        emit_after_step=emit_after_step,
    ):
        constraints.extend(word_constraints(
            f"row {row.index} metadata={hex_bytes(row.metadata)}",
            row.metadata,
            value,
            nibble_order,
        ))
    result = check_constraints(constraints)
    payload = {
        "ok": result["ok"],
        "model": {
            "step": step_name,
            "data_only": data_only,
            "direction": "down" if down_from_ffff else "up",
            "start": (
                f"0x{start_value:04X}" if start_value is not None
                else ("0xFFFF" if down_from_ffff else "0x0000")
            ),
            "emit": "after_step" if emit_after_step else "before_step",
            "nibble_order": "".join(str(x) for x in nibble_order),
            "included_rows": len(model_rows),
        },
        "pinned": [
            {f"{pid:X}": f"{nib_source[0]:X}" for pid, nib_source in sorted(mapping.items())}
            for mapping in result["assigned"]
        ],
        "contradictions": result["contradictions"],
        "duplicates": result["duplicates"],
    }
    print(json.dumps(payload, indent=2))


def parse_nibble_order(text: str) -> tuple[int, int, int, int]:
    order = tuple(int(ch) for ch in text)
    if sorted(order) != [0, 1, 2, 3]:
        raise ValueError("--nibble-order must be a permutation of 0123")
    return order


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze DSP56800E frame headers listed in frames.txt")
    parser.add_argument("frames", nargs="?", default="frames.txt",
                        help="frames.txt-style input (default: frames.txt)")
    parser.add_argument("--binary",
                        help="optional original binary, used only to size the last frame")
    parser.add_argument("--nibble-order", default="0123",
                        help="metadata nibble order for 16-bit counter tests")
    parser.add_argument("--no-marker-anchor", action="store_true",
                        help="do not require the 9c 66 1b a5 marker to mean 0xffff")
    parser.add_argument("--show-best", type=int, default=8,
                        help="number of counter models to print (default: 8)")
    parser.add_argument("--emit-counter-constraints", action="store_true",
                        help="print JSON constraints for one selected counter model")
    parser.add_argument("--counter-step", default="payload_words",
                        choices=[
                            "frame_index",
                            "encoded_frame_size",
                            "payload_encoded_bytes",
                            "payload_decoded_bytes",
                            "payload_words",
                        ],
                        help="selected model for --emit-counter-constraints")
    parser.add_argument("--counter-up", action="store_true",
                        help="selected emit model counts up from zero instead of down from 0xffff")
    parser.add_argument("--counter-all-frames", action="store_true",
                        help="selected emit model advances on ID/non-data frames too")
    parser.add_argument("--include-unknown-counter-rows", action="store_true",
                        help="include rows without known frame size in counter tests")
    parser.add_argument("--counter-start-payload-size", action="store_true",
                        help="for --emit-counter-constraints, start at the "
                             "total selected counter step count instead of "
                             "0xffff/0")
    parser.add_argument("--counter-emit-after-step", action="store_true",
                        help="for --emit-counter-constraints, emit the value "
                             "after applying the current frame's step; useful "
                             "for testing terminal-zero remaining counters")
    args = parser.parse_args()

    binary_size = Path(args.binary).stat().st_size if args.binary else None
    rows = parse_frames_txt(Path(args.frames), binary_size=binary_size)
    nibble_order = parse_nibble_order(args.nibble_order)
    include_marker_anchor = not args.no_marker_anchor

    if args.emit_counter_constraints:
        emit_constraints_json(
            rows,
            args.counter_step,
            data_only=not args.counter_all_frames,
            down_from_ffff=not args.counter_up,
            include_marker_anchor=include_marker_anchor,
            nibble_order=nibble_order,
            include_unknown_size_rows=args.include_unknown_counter_rows,
            start_payload_size=args.counter_start_payload_size,
            emit_after_step=args.counter_emit_after_step,
        )
        return 0

    print_header_summary(rows)
    analyze_sync_lengths(rows, include_marker_anchor=include_marker_anchor)
    analyze_metadata_counter(
        rows,
        include_marker_anchor=include_marker_anchor,
        nibble_order=nibble_order,
        show_best=args.show_best,
        include_unknown_size_rows=args.include_unknown_counter_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
