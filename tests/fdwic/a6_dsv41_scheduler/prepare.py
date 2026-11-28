"""Build the pinned single-card SWA snapshot for HBG Calendar."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from compile_kernels import HERE, REPO, SOURCES, SUITE, case_source, compile_kernel_set, sha256, swa_tasks
from prepare_plan import build_converter, convert_single_vector, read_records, validate_source


def capture(command):
    return subprocess.check_output(command, text=True)


def unpack_fixture(destination):
    fixture = HERE / "fixtures"
    manifest = json.loads((fixture / "fixture.json").read_text())
    archive = fixture / "swa.tar.xz"
    if sha256(archive) != manifest["archive_sha256"]:
        raise ValueError("SWA fixture archive checksum differs")
    hashes = manifest["files"]
    with tarfile.open(archive, "r:xz") as tar:
        members = tar.getmembers()
        if sorted(member.name for member in members) != sorted(hashes):
            raise ValueError("SWA fixture file list differs")
        for member in members:
            path = Path(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Invalid fixture member: {member.name}")
            with tar.extractfile(member) as stream:
                data = stream.read()
            if hashlib.sha256(data).hexdigest() != hashes[member.name]:
                raise ValueError(f"SWA fixture checksum differs: {member.name}")
            # The fixture's callable predates the current wire protocol.
            if member.name != "callable.bin":
                target = destination / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)


def assemble_replay(tools, bundle, build_dir, binary_dir, compiled, kernels, signature):
    def encode(values):
        return " ".join(map(str, (len(values), *values)))

    rows = [encode(signature), str(len(kernels))]
    for kernel, record in zip(kernels, compiled, strict=True):
        if kernel["func_id"] != record["id"]:
            raise ValueError("Compiled kernel ID differs from the manifest")
        path = json.dumps(str(binary_dir / record["registration_file"]), ensure_ascii=False)
        rows.append(f"{record['id']} {encode(kernel['signature'])} {path}")
    assembly = build_dir / "assembly.txt"
    assembly.write_text("\n".join(rows) + "\n")
    runner, image = tools / "native-runner", bundle / "callable.bin"
    subprocess.run([runner, "--assemble-orchestration", assembly, tools / "libreplay.so", image], check=True)
    layout = json.loads(capture([runner, "--describe-layout", image]))
    data = image.read_bytes()
    for child, record in zip(layout["kernels"], compiled, strict=True):
        start, size = child.pop("binary_offset"), child.pop("binary_size")
        if data[start : start + size] != (binary_dir / record["file"]).read_bytes():
            raise ValueError(f"Assembled machine code differs for {record['name']}")
    expected = {"signature": signature, "kernels": [{"id": k["func_id"], "signature": k["signature"]} for k in kernels]}
    if layout != expected:
        raise ValueError("Assembled callable signatures differ")


def build_host(tools, cxx, jobs):
    subprocess.run(
        ["cmake", "-S", str(SUITE / "production"), "-B", str(tools), "-DCMAKE_BUILD_TYPE=Release",
         f"-DCMAKE_CXX_COMPILER={cxx}", f"-DSIMPLER_ROOT={REPO}"],
        check=True,
    )
    subprocess.run(["cmake", "--build", str(tools), "--parallel", str(jobs)], check=True)


def prepare_snapshot(bundle, tools, platform, cxx):
    unpack_fixture(bundle)
    case_path = bundle / "case.json"
    case = json.loads(case_path.read_text())
    if case["source"] != case_source("swa") or case["device_count"] != 1:
        raise ValueError("Expected the pinned single-card SWA snapshot")
    plan = bundle / "plan-rank0.bin"
    record_tool = tools / "record-tool"
    tasks = swa_tasks()
    validate_source(read_records(record_tool, plan))
    executions = case["source"]["executions"]
    if platform == "a6":
        converter = tools / "adapt-plan"
        build_converter(cxx, converter)
        converted = plan.with_suffix(".converted")
        executions = convert_single_vector(record_tool, converter, plan, converted)
        converted.replace(plan)
    case.update(platform=platform, executions=executions)
    case_path.write_text(json.dumps(case, indent=2) + "\n")
    manifest = json.loads((SOURCES / "manifest.json").read_text())
    signature = manifest["entry_signature"] + [3] * len(case["ranks"][0]["boundary_buffers"])
    if int((bundle / "rank0.txt").read_text().splitlines()[2]) != len(signature):
        raise ValueError("Snapshot tensor count differs from callable signature")
    ids = {k for _, slots in tasks.values() for k in slots if k >= 0}
    kernels = [k for k in manifest["kernels"] if k["func_id"] in ids]
    return signature, kernels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("a5", "a6"), default="a6")
    parser.add_argument("--pto-isa", type=Path, default=REPO / "build/pto-isa")
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1 or not (args.pto_isa / "include/pto").is_dir():
        parser.error("Need positive --jobs and --pto-isa containing include/pto")
    build_dir = args.build_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    if (build_dir / "bundle").exists():
        parser.error("Bundle already exists; use a new --build-dir")
    tools, binaries = build_dir / "host", build_dir / "kernels"
    binaries.mkdir(exist_ok=True)
    build_host(tools, args.cxx, args.jobs)
    with tempfile.TemporaryDirectory(prefix="swa-", dir=build_dir) as temporary:
        bundle = Path(temporary) / "bundle"
        signature, kernels = prepare_snapshot(bundle, tools, args.platform, args.cxx)
        compiled = compile_kernel_set(args.platform, args.pto_isa, binaries, args.jobs)
        assemble_replay(tools, bundle, build_dir, binaries, compiled, kernels, signature)
        names = {str(k["func_id"]): k["name"] for k in kernels}
        (bundle / "names.json").write_text(json.dumps({"orchestrator_name": "SWA", "callable_id_to_name": names}) + "\n")
        shutil.move(bundle, build_dir / "bundle")


if __name__ == "__main__":
    main()
