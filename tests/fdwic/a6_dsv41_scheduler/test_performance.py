#!/usr/bin/env python3
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Exercise production identity decoding and named SWA trace export on CPU."""

import json
import lzma
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import performance


def write_capture(folder, level):
    source, replay, block, epoch = 8, 0, 0, 2
    _, slots = performance.swa_tasks()[source]
    path = folder / "chip_swimlane_records.json"
    identity = {
        "run_epoch": epoch,
        "core_id": 1,
        "reg_task_id": 5,
        "task_id": replay,
        "block_idx": block,
        "block_num": 1,
        "subtask_slot": 1,
        "order_key": 0,
        "group_seq": None,
        "group_kind": 0,
        "cluster_index": 0,
        "cluster_lane": 1,
        "pending_slot": 0,
        "executed": True,
    }
    records = {
        "chip_swimlane_level": level,
        "metadata": {
            "clock_freq_hz": 1_000_000_000,
            "core_types": ["aic", "aiv"],
            "num_cores": 2,
            "runtime": "host_build_graph",
        },
        "aicore_tasks": [[1, replay, 5, 110, 200, 5, epoch]],
        "aicore_execution_identities": {
            "schema": "hbg-execution-identity",
            "version": 2,
            "producer": "aicore",
            "records": [identity],
        },
    }
    if level == 2:
        identity["schedule_cycles"] = [70, 80, 90, 230]
        records["aicore_execution_identities"]["version"] = 3
        records["scheduler_tasks"] = {"producer": "aicore", "records": [[1, 5, 100, 210, epoch]]}
    if level == 1:
        records["aicpu_lifecycle_records"] = [
            {"aicpu_thread_id": 0, "handshake_start_cycles": 55, "handshake_complete_cycles": 62},
            {"aicpu_thread_id": 1, "handshake_start_cycles": 60, "handshake_complete_cycles": 65},
        ]
    path.write_text(json.dumps(records))
    (folder / "source-task-map.txt").write_text(f"{source} {replay}\n")
    deps = {"tasks": [{"task_id": str(replay), "block_num": 1, "kernel_ids": slots}], "edges": []}
    (folder / "deps.json").write_text(json.dumps(deps))
    return path, {source: (1, slots)}, records


class PerformanceTest(unittest.TestCase):
    core = {"start_cycle": 110, "end_cycle": 200, "clock_hz": 1_000_000_000}

    def test_observation_joins_the_host_runner_with_its_own_epoch_and_thread(self):
        prefix = "[STRACE] v=1 pid=4 tid=9"
        identity = " ep=8 gen=2 disp=0 slot=1"
        lines = [
            f"{prefix} inv=0 hid=0 depth=1 name=chip.run.proof.observe_begin ts=900 dur=0" + identity,
            f"{prefix} inv=0 hid=0 depth=1 name=chip.run.proof.observe_return ts=1100 dur=0" + identity,
            f"{prefix} inv=3 hid=0 depth=1 name=chip.run.runner_run ts=800 dur=600",
        ]
        measured = performance.runner_observation_spans(performance.parse_span_records(lines))[(4, 3)]
        self.assertEqual(measured, {"runner_before_observe": 0.1, "runner_observe": 0.2, "runner_after_observe": 0.3})
        lines[1] = lines[1].replace("slot=1", "slot=0")
        with self.assertRaisesRegex(ValueError, "Unpaired"):
            performance.runner_observation_spans(performance.parse_span_records(lines))

    def test_l1_validates_source_mapping_and_execution_join(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path, shape, raw = write_capture(folder, 1)
            with patch.object(performance, "swa_tasks", return_value=shape):
                metrics, rows = performance.capture_metrics(folder, "a5", 1, 2, self.core)
                self.assertAlmostEqual(metrics["handshake"], 0.010)
                self.assertEqual(
                    (rows[0]["source_task"], rows[0]["replay_task"], rows[0]["task"]), (8, 0, "hc_collapse_norm")
                )
                (folder / "source-task-map.txt").write_text("8 0\n8 1\n")
                with self.assertRaisesRegex(ValueError, "mapping is incomplete or duplicated"):
                    performance.capture_metrics(folder, "a5", 1, 2, self.core)
                (folder / "source-task-map.txt").write_text("8 0\n")
                raw["aicore_execution_identities"]["records"][0]["reg_task_id"] += 1
                path.write_text(json.dumps(raw))
                with self.assertRaisesRegex(ValueError, "mismatched execution identity join"):
                    performance.capture_metrics(folder, "a5", 1, 2, self.core)

    def test_a6_diagnostic_requires_five_t0_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_capture(folder, 2)
            table = performance.swa_tasks()
            with patch.object(performance, "swa_tasks", return_value={8: table[8]}):
                path = folder / "deps.json"
                deps = json.loads(path.read_bytes())
                for reported, selected in ((5, True), (20, False)):
                    deps["tasks"][0]["block_num"] = reported
                    path.write_text(json.dumps(deps))
                    _, shapes, _ = performance.workload(folder, "a6", selected)
                    self.assertEqual(shapes[0][0], reported)
                    with self.assertRaisesRegex(ValueError, "differs from source"):
                        performance.workload(folder, "a6", not selected)

    def test_l2_uses_checkpoint_schema_and_names_worker_without_optional_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path, shape, _ = write_capture(folder, 2)
            with patch.object(performance, "swa_tasks", return_value=shape):
                metrics, rows = performance.capture_metrics(folder, "a5", 2, 2, self.core)
            self.assertNotIn("handshake", metrics)
            self.assertAlmostEqual(rows[0]["mean_kernel_us"], 0.09)
            trace = folder / "l2.json.xz"
            performance.save_trace(path, trace, (50, 110, 200, 250), 1)
            with lzma.open(trace, "rt") as stream:
                events = json.load(stream)["traceEvents"]
            workers = [e for e in events if e.get("pid") == 4 and "kernel-duration-us" in e.get("args", {})]
            self.assertEqual(len(workers), 1)
            self.assertIn("hc_collapse_norm", workers[0]["name"])
            device = next(e for e in events if e.get("cat") == "device_wall_breakdown" and e["name"] == "Device wall")
            self.assertAlmostEqual(device["ts"], workers[0]["ts"] - 0.06)
            self.assertAlmostEqual(device["dur"], 0.20)
            self.assertTrue(any(e.get("cat") == "HBG L2" for e in events))


if __name__ == "__main__":
    unittest.main()
