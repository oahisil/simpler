# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Aggregate completion ranks relative to each round's earliest start."""

import argparse
import csv
import io
import json
import lzma
import math
import statistics
from collections import defaultdict
from pathlib import Path


def percentile(values, q):
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    return ordered[lower] + (ordered[math.ceil(position)] - ordered[lower]) * (position - lower)


def summarize(rows, blocks, vectors, hz):
    count = blocks + vectors
    if set(rows) != set(range(193)):
        raise ValueError("Expected warmup 0 and 192 measured rounds")
    ranked = []
    skews = []
    for number, samples in sorted(rows.items()):
        if len(samples) != count or {s[0] for s in samples} != set(range(count)):
            raise ValueError(f"Invalid coverage in round {number}")
        if any(bool(v) != (slot >= blocks) or end <= start for slot, v, start, end in samples):
            raise ValueError(f"Invalid core type or ticks in round {number}")
        if number == 0:
            continue
        origin = min(start for _, _, start, _ in samples)
        ordered_ends = sorted(end for _, _, _, end in samples)
        ranked.append([(end - origin) * 1e6 / hz for end in ordered_ends])
        skews.append((max(s[2] for s in samples) - origin) * 1e6 / hz)
    ranks = [
        {
            "rank": i,
            "mean_us": round(statistics.mean(col), 6),
            "p10_us": round(percentile(col, 0.1), 6),
            "p90_us": round(percentile(col, 0.9), 6),
        }
        for i, col in enumerate(zip(*ranked, strict=True), 1)
    ]
    gap = statistics.mean((r[-1] - r[0]) * 1000 / (count - 1) for r in ranked)
    return {"mean_adjacent_ns": round(gap, 6), "mean_start_skew_us": round(statistics.mean(skews), 6), "ranks": ranks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--blocks", required=True, type=int)
    parser.add_argument("--vectors", required=True, type=int)
    parser.add_argument("--clock-hz", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.clock_hz <= 0 or min(args.blocks, args.vectors) < 1:
        parser.error("Require positive capacity and device clock frequency")
    results = {}
    for path in args.captures:
        modes = defaultdict(lambda: defaultdict(list))
        binary = lzma.LZMAFile(path) if path.suffix == ".xz" else path.open("rb")
        with io.TextIOWrapper(binary, encoding="ascii", newline="") as stream:
            for row in csv.DictReader(stream):
                modes[row["scenario"]][int(row["round"])].append(
                    tuple(int(row[k]) for k in ("slot", "is_vector", "begin_tick", "end_tick"))
                )
        if set(modes) not in (
            {"cnt_notify_read", "cnt_notify_write"},
            {"gm_ldst_read", "gm_ldst_write", "gm_atomic_read", "gm_atomic_write"},
        ):
            raise ValueError(f"Incomplete group in {path}")
        for name, rounds in modes.items():
            if name in results:
                raise ValueError(f"Duplicate capture for {name}")
            results[name] = summarize(rounds, args.blocks, args.vectors, args.clock_hz)
    workers = args.blocks + args.vectors
    print("scenario,first_us,middle_us,last_us,mean_adjacent_ns,mean_start_skew_us")
    for name, data in results.items():
        ranks = data["ranks"]
        print(
            f"{name},{ranks[0]['mean_us']:.3f},{ranks[(workers - 1) // 2]['mean_us']:.3f},"
            f"{ranks[-1]['mean_us']:.3f},{data['mean_adjacent_ns']:.1f},{data['mean_start_skew_us']:.3f}"
        )
    with args.output.open("x") as out:
        json.dump(
            {
                "cube": args.blocks,
                "vector": args.vectors,
                "clock_hz": args.clock_hz,
                "warmup": 1,
                "rounds": 192,
                "scenarios": results,
            },
            out,
            indent=2,
        )
        out.write("\n")


if __name__ == "__main__":
    main()
