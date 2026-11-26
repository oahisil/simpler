#!/usr/bin/env python3
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Offline checks for the SWA inputs, references and archive integrity."""

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("swa_prepare", HERE / "prepare.py")
assert SPEC is not None and SPEC.loader is not None
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


class FixtureTest(unittest.TestCase):
    def test_inputs_and_all_writable_references_are_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            metadata = PREPARE.unpack_fixture(destination)
            lines = (destination / "rank0.txt").read_text().splitlines()
            self.assertEqual(lines[:2], ["DSV41_NATIVE_V1", "1 0"])
            tensors = lines[3 : 3 + int(lines[2])]
            expected_references = set()
            for index, line in enumerate(tensors):
                fields = line.split()
                self.assertIn(fields[0], {"DEVICE", "HOST"})
                data = (destination / fields[-1]).read_bytes()
                self.assertTrue(data)
                size = int(fields[2])
                self.assertTrue(len(data) == size or (fields[-2] == "REPEAT" and size % len(data) == 0))
                if metadata["signature"][index] in (2, 3) and fields[0] == "DEVICE":
                    reference = f"reference/{index}.bin"
                    expected_references.add(reference)
                    self.assertIn((destination / reference).stat().st_size, (1, size))
            self.assertEqual(
                expected_references,
                {p.relative_to(destination).as_posix() for p in (destination / "reference").glob("*.bin")},
            )
            self.assertEqual(len(expected_references), 30)
            self.assertFalse((destination / "callable.bin").exists())
            self.assertFalse((destination / "rank1.txt").exists())

    def test_corrupted_fixture_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            fixture.mkdir()
            (fixture / "swa.tar.gz").write_bytes((PREPARE.FIXTURE / "swa.tar.gz").read_bytes())
            metadata = json.loads((PREPARE.FIXTURE / "fixture.json").read_text())
            metadata["files"]["rank0.txt"]["sha256"] = "0" * 64
            (fixture / "fixture.json").write_text(json.dumps(metadata))
            with patch.object(PREPARE, "FIXTURE", fixture):
                with self.assertRaisesRegex(ValueError, "Fixture content changed: rank0.txt"):
                    PREPARE.unpack_fixture(Path(temporary) / "bundle")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Native replay tools require Linux")
    def test_a6_host_preflight_and_callable_wire(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            env = {key: value for key, value in os.environ.items() if key not in {"ASCEND_HOME_PATH", "TASK_DEVICE"}}
            subprocess.run(
                [sys.executable, "-I", HERE / "prepare.py", "check", "--platform", "a6", "--build-dir", build],
                check=True,
                env=env,
            )
            report = json.loads((build / "summary.json").read_text())
            self.assertEqual((report["tasks"], report["executions"], len(report["kernel_ids"])), (16, 570, 19))
            self.assertFalse(report["runtime_executed"])

            bundle, tools, binaries = build / "bundle", build / "host", build / "kernels"
            metadata = PREPARE.unpack_fixture(bundle)
            sources = json.loads((HERE / "kernels/manifest.json").read_text())
            kernels = [kernel for kernel in sources["kernels"] if kernel["func_id"] in report["kernel_ids"]]
            binaries.mkdir()
            for kernel in kernels:
                (binaries / f"kernel-{kernel['func_id']}.bin").write_bytes(b"host-only packing test")
            image, runner = bundle / "callable.bin", tools / "native-runner"
            PREPARE.assemble_replay(tools, image, build / "assembly.txt", metadata, kernels, binaries)
            description = json.loads(PREPARE.capture([runner, "--describe-callable", image]))
            self.assertEqual(len(description["signature"]), 99)
            self.assertEqual([kernel["id"] for kernel in description["kernels"]], report["kernel_ids"])

            stale = bytearray(image.read_bytes())
            struct.pack_into("<I", stale, 17556, 1)  # ChipCallable wire_version_ offset.
            image.write_bytes(stale)
            PREPARE.save(
                build / "build.json",
                {
                    "platform": "a6",
                    "kernel_runtime": "percounter",
                    "runner_sha256": PREPARE.digest(runner.read_bytes()),
                    "callable_sha256": PREPARE.digest(stale),
                    "kernels": description["kernels"],
                },
            )
            result = subprocess.run(
                [
                    sys.executable, "-I", HERE / "run.py", "--platform", "a6", "--build-dir", build,
                    "--output", build / "result", "--device", "0", "--a6-query", build / "absent-query",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("unsupported ChipCallable wire version", result.stderr)
            self.assertFalse((build / "result").exists())


if __name__ == "__main__":
    unittest.main()
