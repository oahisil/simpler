#!/usr/bin/env python3
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Check the captured Host ABI and the A6 SWA tile assignments."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compile_kernels import HERE, REPO, SUITE, case_tasks


def a6_partitions(five_block_t0=False):
    partitions = {12: 54, 22: 48, 30: 54, 33: 54, 34: 54}
    if five_block_t0:
        partitions[8] = 5
    return partitions


def read_records(tool, path):
    return json.loads(subprocess.check_output([str(tool), "--records-json", str(path)], text=True))


def validate_source(records, case="swa"):
    tasks = case_tasks(case)
    first, last = min(tasks), max(tasks)
    selected = [(r["id"], (r["blocks"], r["kernels"])) for r in records if r["op"] == 2 and first <= r["id"] <= last]
    if selected != list(tasks.items()):
        raise ValueError(f"Source plan differs from the pinned {case.upper()} task table")


def check_conversion(before, after, five_block_t0=False):
    validate_source(before)
    partitions = a6_partitions(five_block_t0)
    expected = []
    for record in before:
        if record["op"] == 2 and record["id"] in partitions:
            slots = [*record["kernels"][:2], -1]
            record = {**record, "blocks": partitions[record["id"]], "kernels": slots}
        expected.append(record)
    if after != expected:
        raise ValueError("A6 conversion changed fields outside the selected SWA partitions")
    return sum(r["blocks"] * sum(k >= 0 for k in r["kernels"]) for r in after if r["op"] == 2 and 8 <= r["id"] <= 34)


def build_converter(cxx, output):
    subprocess.run(
        [
            cxx,
            "-std=c++17",
            "-O2",
            f"-I{SUITE / 'production'}",
            f"-I{REPO / 'src/common'}",
            f"-I{REPO / 'src/common/task_interface'}",
            str(HERE / "adapt_plan.cpp"),
            "-o",
            output,
        ],
        check=True,
    )


def convert_plan(record_tool, converter, original, converted, five_block_t0=False):
    before = read_records(record_tool, original)
    validate_source(before)
    options = ["--five-block-t0"] if five_block_t0 else []
    subprocess.run([converter, original, converted, *options], check=True)
    return check_conversion(before, read_records(record_tool, converted), five_block_t0)


def convert_single_vector(record_tool, converter, original, converted, case="swa"):
    before = read_records(record_tool, original)
    validate_source(before, case)
    if case == "c1a_reindex":
        pool = next(r for r in before if r["op"] == 2 and r["id"] == 1594)
        # This slice registers only the pool branch of the original MIX callable.
        if len(pool["tensors"]) != 15 or len(pool["scalars"]) <= 4 or pool["scalars"][4] < 1 << 63:
            raise ValueError("C1A reindex expects args[19] to select the negative pool-mode branch")
    tasks = case_tasks(case)
    # Only SWA's original gather splits each 64-head block into two groups.
    # Reuse gathers process both head halves on the same physical group.
    shapes = {}
    for task, (blocks, kernels) in tasks.items():
        if kernels[2] >= 0:
            if kernels[0] < 0 or kernels[1] != kernels[2]:
                raise ValueError(f"Unexpected MIX shape in {case} task {task}")
            shapes[task] = (blocks * 2 if case == "swa" and task == 22 else blocks, [*kernels[:2], -1])
    changes = "".join(f"{task} {blocks}\n" for task, (blocks, _) in shapes.items())
    subprocess.run(
        [converter, original, converted, "--single-vector", str(min(tasks)), str(max(tasks))],
        input=changes, text=True, check=True,
    )
    expected = [
        {**r, "blocks": shapes[r["id"]][0], "kernels": shapes[r["id"]][1]}
        if r["op"] == 2 and r["id"] in shapes else r for r in before
    ]
    if read_records(record_tool, converted) != expected:
        raise ValueError(f"1C1V conversion changed fields outside the selected MIX tasks in {case}")
    return sum(
        r["blocks"] * sum(k >= 0 for k in r["kernels"])
        for r in expected if r["op"] == 2 and r["id"] in tasks
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="Original latest A5 plan-rank0.bin")
    parser.add_argument("--record-tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--cxx", default="c++")
    parser.add_argument("--five-block-t0", action="store_true", help="Use five A6 vector blocks for source task 8")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    converter = args.output / "adapt-plan"
    build_converter(args.cxx, converter)
    executions = convert_plan(
        args.record_tool, converter, args.plan, args.output / "plan-a6-rank0.bin", args.five_block_t0
    )
    print(f"Verified A6 SWA partition and unchanged payloads, flags and dependencies: {executions} kernel instances.")


if __name__ == "__main__":
    main()
