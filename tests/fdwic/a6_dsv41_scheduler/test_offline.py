"""Check the A6 migration without SDK, PTO-ISA, or NPU hardware."""

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from prepare import REPO, assemble_replay, build_host, prepare_snapshot, read_records


def run_test(build_dir):
    tools = build_dir / "host"
    build_host(tools, "c++", 4)
    with TemporaryDirectory(dir=build_dir) as temporary:
        root = Path(temporary)
        bundle = root / "bundle"
        signature, kernels = prepare_snapshot(bundle, tools, "a6", "c++")
        case = json.loads((bundle / "case.json").read_text())
        assert case["executions"] == 592, case["executions"]
        records = read_records(tools / "record-tool", bundle / "plan-rank0.bin")
        tasks = [r for r in records if r["op"] == 2 and 8 <= r["id"] <= 34]
        assert len(tasks) == 15
        gather = next(task for task in tasks if task["id"] == 22)
        assert gather["blocks"] == 48 and gather["sync"] == 0
        assert all(task["kernels"][2] < 0 for task in tasks)
        assert len(list((bundle / "reference").glob("*.bin"))) == 25
        assert not (bundle / "callable.bin").exists()

        # Synthetic bytes exercise the current wire packer, never kernel execution.
        compiled = []
        for kernel in kernels:
            ident = kernel["func_id"]
            name = f"{ident}.bin"
            (root / name).write_bytes(bytes([ident]) * 32)
            compiled.append({"id": ident, "name": kernel["name"], "file": name, "registration_file": name})
        assemble_replay(tools, bundle, root, root, compiled, kernels, signature)

        common = REPO / "src/common"
        runtime = REPO / "src/a5/runtime/host_build_graph"
        for platform in ("a5", "a6"):
            exe = root / f"calendar-{platform}"
            includes = [REPO / f"src/{platform}/platform/include", runtime / "host", runtime / "runtime",
                        REPO / "src/a5/runtime", common / "task_interface", common / "platform/include", common]
            subprocess.run(
                ["c++", "-std=c++17", "-O2", *(f"-I{path}" for path in includes),
                 Path(__file__).with_name("test_calendar.cpp"), "-o", exe],
                check=True,
            )
            subprocess.run([exe], check=True)
    print("PASS: SWA plan conversion, callable wire, and A5/A6 Calendar topology")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=REPO / "build/a6-calendar-offline")
    run_test(parser.parse_args().build_dir.resolve())
