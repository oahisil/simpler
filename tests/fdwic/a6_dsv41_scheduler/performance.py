#!/usr/bin/env python3
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Report native SWA Host/device timing and save a representative HBG device trace."""

import argparse
import io
import json
import lzma
import re
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SUITE = Path(__file__).resolve().parents[1] / "dsv41_scheduler"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SUITE / "production"))

from compile_kernels import swa_tasks  # noqa: E402
from ideal_bound import complete_flat_dag  # noqa: E402
from prepare_plan import a6_partitions  # noqa: E402
from timing import paired_spans  # noqa: E402

from simpler_setup.tools.strace_timing import parse_span_records  # noqa: E402
from simpler_setup.tools.swimlane_converter import read_perf_data  # noqa: E402

HOST = ("bind", "publish_image", "prepare_execution")
OPTIONAL_HOST = ("stage_inputs", "runner_run", "claim_release", "validate")
PHASES = {
    "topology": ("config_start_cycles", "topology_complete_cycles"),
    "context_publish": ("context_publish_start_cycles", "context_publish_complete_cycles"),
    "register_release": ("register_release_start_cycles", "register_release_end_cycles"),
    "exit_signal": ("exit_signal_start_cycles", "exit_signal_end_cycles"),
    "handshake": ("handshake_start_cycles", "handshake_complete_cycles"),
    "bootstrap_wait": ("bootstrap_wait_start_cycles", "bootstrap_complete_cycles"),
    "exit_wait": ("exit_wait_start_cycles", "exit_wait_end_cycles"),
}


def workload(folder, platform, five_block_t0=False):
    source_shapes = swa_tasks()
    kernel_names = {
        row["func_id"]: row["name"]
        for row in json.loads((SUITE / "kernels/pypto_lib_latest/manifest.json").read_text())["kernels"]
    }
    mapping = folder / "source-task-map.txt"
    deps = complete_flat_dag(json.loads((folder / "deps.json").read_bytes()), mapping, source_shapes, 1)
    shapes = {
        int(t["task_id"]): (t["block_num"], t["kernel_ids"])
        for t in deps["tasks"]
        if any(i >= 0 for i in t["kernel_ids"])
    }
    sources = {
        target: source
        for source, target in (map(int, row.split()) for row in mapping.read_text().splitlines())
        if source in source_shapes
    }
    if (
        len(shapes) != sum(any(i >= 0 for i in t["kernel_ids"]) for t in deps["tasks"])
        or sources.keys() != shapes.keys()
    ):
        raise ValueError("Unexpected or duplicate replay tasks in SWA dependency capture")
    partitions = a6_partitions(five_block_t0) if platform == "a6" else {}
    for target, source in sources.items():
        blocks, slots = source_shapes[source]
        if source in partitions:
            blocks, slots = partitions[source], [*slots[:2], -1]
        if shapes[target] != (blocks, slots):
            raise ValueError(f"Replay task {target} differs from source SWA task {source} on {platform}")
    return sources, shapes, kernel_names


def covered_cycles(spans):
    cursor = total = 0
    for start, end in sorted(spans):
        if start <= 0 or end < start:
            raise ValueError("Invalid device interval")
        total += max(0, end - max(start, cursor))
        cursor = max(cursor, end)
    return total


def lifecycle_metrics(raw, scale):
    intervals = defaultdict(list)
    for row in raw.get("aicpu_lifecycle_records", []):
        for name, (start, end) in PHASES.items():
            if row.get(start) and row.get(end):
                intervals[name].append((row[start], row[end]))
        for item in row.get("detail_intervals", []):
            intervals[item["name"]].append((item["start_cycles"], item["end_cycles"]))
    return {key: covered_cycles(spans) * scale for key, spans in intervals.items()}


def capture_metrics(folder, platform, level, epoch, core, five_block_t0=False):
    path = folder / "chip_swimlane_records.json"
    raw = json.loads(path.read_bytes())
    if raw["chip_swimlane_level"] != level or raw["metadata"]["runtime"] != "host_build_graph":
        raise ValueError("Capture runtime or level differs from runner")
    for stream in raw.get("scheduler_records", {}).get("streams", []):
        if stream["capture"]["dropped"] or stream["capture"]["truncated"]:
            raise ValueError("Incomplete scheduler capture")
    rows = raw["aicore_tasks"]
    if not rows or (min(r[3] for r in rows), max(r[4] for r in rows)) != (core["start_cycle"], core["end_cycle"]):
        raise ValueError("Trace kernel endpoints differ from AICore wall")
    if raw["metadata"]["clock_freq_hz"] != core["clock_hz"]:
        raise ValueError("Trace clock differs from AICore wall")
    if not raw.get("aicore_execution_identities"):
        raise ValueError("Missing device execution identities")
    # The production decoder joins by (epoch, core, reg_task_id) and checks L2 milestones.
    tasks = read_perf_data(path)["tasks"]
    if five_block_t0 and level == 2 and any("schedule_checkpoints" not in task for task in tasks):
        raise ValueError("Missing HBG L2 scheduling checkpoints")
    sources, shapes, kernel_names = workload(folder, platform, five_block_t0)
    expected = {
        (tid, b, slot)
        for tid, (blocks, slots) in shapes.items()
        for slot, kernel in enumerate(slots)
        if kernel >= 0
        for b in range(blocks)
    }
    actual = {
        (t["task_id"], t["block_idx"], t["subtask_slot"]) for t in tasks if t["executed"] and t["run_epoch"] == epoch
    }
    if expected != actual or len(expected) != len(tasks) or len(tasks) != len(rows):
        raise ValueError("Incomplete or extra block/lane execution coverage")
    scale = 1e6 / core["clock_hz"]
    metrics = lifecycle_metrics(raw, scale)
    kernel_span = core["end_cycle"] - core["start_cycle"]
    metrics["idle_all_cores"] = (kernel_span - covered_cycles((r[3], r[4]) for r in rows)) * scale
    spans_by_kernel = defaultdict(list)
    for task in tasks:
        tid = task["task_id"]
        slot = task["subtask_slot"]
        if task["block_num"] != shapes[tid][0]:
            raise ValueError("Execution identity block count differs from the submitted task")
        kernel = shapes[tid][1][slot]
        spans_by_kernel[tid, kernel, task["core_type"]].append((task["start_time_us"], task["end_time_us"]))
    kernels = [
        {
            "source_task": sources[tid],
            "replay_task": tid,
            "task": kernel_names[kernel],
            "core": kind,
            "executions": len(spans),
            "mean_kernel_us": statistics.mean(end - start for start, end in spans),
            "span_us": max(end for _, end in spans) - min(start for start, _ in spans),
        }
        for (tid, kernel, kind), spans in sorted(spans_by_kernel.items())
    ]
    return metrics, kernels


def save_trace(capture, target, points, expected):
    if target.exists():
        raise FileExistsError(target)
    manifest = json.loads((SUITE / "kernels/pypto_lib_latest/manifest.json").read_text())
    names = {str(k["func_id"]): k["name"] for k in manifest["kernels"]}
    with tempfile.TemporaryDirectory() as temp:
        mapping, trace = Path(temp) / "name_map.json", Path(temp) / "trace.json"
        mapping.write_text(json.dumps({"level": 2, "orchestrator_name": "SWA", "callable_id_to_name": names}))
        subprocess.run(
            [
                sys.executable,
                "-m",
                "simpler_setup.tools.swimlane_converter",
                str(capture),
                "--device-only",
                "--func-names",
                str(mapping),
                "--deps-json",
                str(capture.with_name("deps.json")),
                "-o",
                str(trace),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        document = json.loads(trace.read_bytes())
    events = document["traceEvents"]
    workers = [
        e for e in events if e.get("pid") == 4 and e.get("ph") == "X" and "kernel-duration-us" in e.get("args", {})
    ]
    if len(workers) != expected or any(
        not (match := re.search(r"FuncId:(-?\d+)", event["args"]["event-hint"])) or match[1] not in names
        for event in workers
    ):
        raise ValueError("Missing or unnamed worker executions")
    if points is not None:
        # A legacy Worker View bar starts at pickup and ends at kernel end.
        # Checkpoint-based L2 bars span only the kernel. Both give the same formula.
        first = min(e["ts"] + e["dur"] - e["args"]["kernel-duration-us"] for e in workers)
        t0, t1, t2, t3 = points
        events.append({"ph": "M", "pid": 7, "name": "process_name", "args": {"name": "Device wall"}})
        for name, start, end in (
            ("Device wall", t0, t3),
            ("Before kernels", t0, t1),
            ("AICore window", t1, t2),
            ("After kernels", t2, t3),
        ):
            events.append(
                {
                    "ph": "X",
                    "pid": 7,
                    "tid": 1,
                    "cat": "device_wall_breakdown",
                    "name": name,
                    "ts": first + (start - t1) / 1000,
                    "dur": (end - start) / 1000,
                }
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    with lzma.LZMAFile(target, "x") as output, io.TextIOWrapper(output, encoding="utf-8") as stream:
        json.dump(document, stream, separators=(",", ":"), ensure_ascii=False)


def runner_observation_spans(records):
    """Join native completion observation marks, which can carry inv=0, to their Host runner."""
    marks = defaultdict(dict)
    runners = []
    for span, record in records:
        if span.name == "chip.run.runner_run":
            runners.append(span)
        if span.name not in {"chip.run.proof.observe_begin", "chip.run.proof.observe_return"}:
            continue
        identity = re.findall(r"\b(ep|gen|disp|slot)=(\d+)\b", record)
        if len(identity) != 4 or len(dict(identity)) != 4:
            raise ValueError("Incomplete native completion observation identity")
        key = (span.pid, span.tid, *sorted(identity))
        edge = span.name.rsplit(".", 1)[-1]
        if edge in marks[key]:
            raise ValueError("Duplicate native completion observation")
        marks[key][edge] = span.ts
    result = {}
    for (pid, tid, *_), pair in marks.items():
        if pair.keys() != {"observe_begin", "observe_return"}:
            raise ValueError("Unpaired native completion observation")
        begin, end = pair["observe_begin"], pair["observe_return"]
        enclosing = [r for r in runners if (r.pid, r.tid) == (pid, tid) and r.ts <= begin <= end <= r.ts + r.dur]
        if len(enclosing) != 1:
            raise ValueError("Cannot place native completion observation in one Host runner")
        runner = enclosing[0]
        identity = (pid, runner.inv)
        if identity in result:
            raise ValueError("Multiple completion observations in a single SWA run")
        result[identity] = {
            "runner_before_observe": (begin - runner.ts) / 1000,
            "runner_observe": (end - begin) / 1000,
            "runner_after_observe": (runner.ts + runner.dur - end) / 1000,
        }
    return result


def read_run(rank, level):
    result = json.loads((rank / "result.json").read_text())
    if not all(
        result[k] for k in ("passed", "snapshot_inputs", "numerical_validation", "validated_against_fused_prefix")
    ):
        raise ValueError("SWA snapshot validation failed")
    if (result["rank"], result["ranks"], result["runtime"], result["warmup"], result["rounds"]) != (
        0,
        1,
        "host_build_graph",
        1,
        10,
    ):
        raise ValueError("Expected HBG, one warmup and ten measurements")
    lines = re.findall(
        r"run=(\d+) scheduler_mode=(\d+) profiling=(\d+) warmup=(\d+)", (rank / "console.log").read_text()
    )
    if [tuple(map(int, line)) for line in lines] != [(i, 1, level, int(i == 0)) for i in range(11)]:
        raise ValueError("Wrong scheduler mode, level or measured iterations")
    logs = "\n".join(p.read_text() for p in sorted((rank / "host").glob("host.*.log")))
    cores, walls = paired_spans(logs)
    dev = result["device"]
    if set(cores) != {dev} or len(cores[dev]) != 11:
        raise ValueError("Missing or extra invocations")
    host = defaultdict(dict)
    host_names = {"chip.run", *("chip.run." + phase for phase in (*HOST, *OPTIONAL_HOST))}
    records = list(parse_span_records(logs.splitlines()))
    for span, _ in records:
        if span.name in host_names:
            call = host[span.pid, span.inv]
            if span.name in call:
                raise ValueError(f"Duplicate Host span {span.name}")
            call[span.name] = span.dur / 1000
    for identity, phases in runner_observation_spans(records).items():
        host[identity].update(phases)
    return dev, cores[dev][1:], walls[dev][1:], host


def measure(rank, platform, level, shared_device_clock, five_block_t0=False):
    dev, cores, walls, host = read_run(rank, level)
    samples, endpoints, kernel_samples = [], [], []
    for iteration, (core, wall) in enumerate(zip(cores, walls, strict=True), 1):
        t0, t1, t2, t3 = wall["start_cycle"], core["start_cycle"], core["end_cycle"], wall["end_cycle"]
        call = host[core["pid"], core["invocation"]]
        sample = {phase: call["chip.run." + phase] for phase in HOST}
        sample.update({phase: call["chip.run." + phase] for phase in OPTIONAL_HOST if "chip.run." + phase in call})
        sample.update({key: value for key, value in call.items() if key.startswith("runner_")})
        sample.update(api=call["chip.run"], core=core["duration_ns"] / 1000, device=wall["duration_ns"] / 1000)
        if shared_device_clock:
            if core["clock_hz"] != 1_000_000_000 or not t0 <= t1 < t2 <= t3:
                raise ValueError("Cannot split endpoints on a shared 1 GHz device clock")
            sample.update(before=(t1 - t0) / 1000, after=(t3 - t2) / 1000)
        if level:
            phases, kernels = capture_metrics(
                rank / f"run{iteration}", platform, level, iteration + 1, core, five_block_t0
            )
            sample.update(phases)
            kernel_samples.append(kernels)
        samples.append(sample)
        endpoints.append((t0, t1, t2, t3))
    return dev, samples, endpoints, kernel_samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Parent of rank0/")
    parser.add_argument("--platform", required=True, choices=("a5", "a6"))
    parser.add_argument("--five-block-t0", action="store_true", help="Expect the five-block A6 t0 diagnostic plan")
    parser.add_argument("--level", required=True, type=int, choices=(0, 1, 2, 3))
    parser.add_argument("--trace", type=Path, help="Write one L2 or L3 .json.xz selected by median Device wall")
    parser.add_argument(
        "--shared-device-clock",
        action="store_true",
        help="Split RunWall when both endpoints use the same device counter epoch",
    )
    parser.add_argument("--json", type=Path, help="Save per-invocation measurements and averages")
    args = parser.parse_args()
    if args.five_block_t0 and args.platform != "a6":
        parser.error("--five-block-t0 requires --platform a6")
    rank = args.run / "rank0"
    if args.trace and args.level not in (2, 3):
        parser.error("--trace requires --level 2 or 3")
    dev, samples, endpoints, kernel_samples = measure(
        rank, args.platform, args.level, args.shared_device_clock, args.five_block_t0
    )
    fields = set.union(*(set(sample) for sample in samples))
    if args.level >= 2:
        fields.update(PHASES)
    means = {
        key: statistics.mean(s[key] for s in samples) if all(key in s for s in samples) else None
        for key in sorted(fields)
    }
    for key, value in means.items():
        if value is not None:
            print(f"{key:20} {value:10.3f} us")
    if args.json:
        with args.json.open("x") as stream:
            json.dump(
                {
                    "platform": args.platform,
                    "t0_blocks": 5 if args.five_block_t0 else swa_tasks()[8][0],
                    "device": dev,
                    "level": args.level,
                    "unit": "us",
                    "samples": samples,
                    "means": means,
                    "per_task": kernel_samples,
                },
                stream,
                indent=2,
            )
            stream.write("\n")
    if args.trace:
        median = statistics.median(s["device"] for s in samples)
        index = min(range(len(samples)), key=lambda i: abs(samples[i]["device"] - median))
        capture = rank / f"run{index + 1}/chip_swimlane_records.json"
        save_trace(
            capture,
            args.trace,
            endpoints[index] if args.shared_device_clock else None,
            sum(r["executions"] for r in kernel_samples[index]),
        )
        print(f"L{args.level} representative: run{index + 1}, Device wall {samples[index]['device']:.3f} us")


if __name__ == "__main__":
    main()
