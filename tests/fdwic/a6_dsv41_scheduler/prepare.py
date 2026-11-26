#!/usr/bin/env python3
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Build a DSV4.1 single-device replay from an upstream materialized A5 snapshot."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compile_kernels import CASES_006, HERE, REPO, SOURCES, SUITE, case_source, case_tasks, compile_kernel_set, sha256
from prepare_plan import build_converter, convert_plan, convert_single_vector, read_records, validate_source


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def capture(command):
    return subprocess.check_output([str(arg) for arg in command], text=True)


def copy_snapshot(source, destination, tools, name="swa"):
    case = json.loads((source / "case.json").read_text())
    source_spec = case_source(name)
    if (case["flavor"], case["case"], case["device_count"], case["source"]) != (
        "pypto_lib_latest",
        name,
        1,
        source_spec,
    ):
        raise ValueError("Expected a materialized slice matching the current upstream model and task table")
    lines = (source / "rank0.txt").read_text().splitlines()
    bounds = [source_spec["first_source_task"], source_spec["last_source_task"]]
    if (
        lines[:2] != ["DSV41_NATIVE_V1", "1 0"]
        or (source / "slice.txt").read_text().split() != bounds
        or (source / "snapshot.txt").read_text().split() != ["rank0"]
        or (source / "rank1.txt").exists()
    ):
        raise ValueError("Expected a completed single-device snapshot")
    plan = source / "plan-rank0.bin"
    validate_source(read_records(tools / "record-tool", plan), name)
    description = json.loads(capture([tools / "native-runner", "--describe-callable", source / "callable.bin"]))
    manifest = json.loads((SOURCES / "manifest.json").read_text())
    original = {k["id"]: k["signature"] for k in description["kernels"]}
    if original != {k["func_id"]: k["signature"] for k in manifest["kernels"]}:
        raise ValueError("Source callable signatures differ from the model kernel manifest")
    signature = description["signature"]
    base = manifest["entry_signature"]
    extras = len(case["ranks"][0]["boundary_buffers"])
    if signature != base + [3] * extras:
        raise ValueError("Snapshot entry signature differs from source and captured boundary buffers")
    files = {"case.json", "rank0.txt", "slice.txt", "snapshot.txt", "plan-rank0.bin"}
    tensors = lines[3 : 3 + int(lines[2])]
    if len(tensors) != int(lines[2]) or len(tensors) != len(signature):
        raise ValueError("Snapshot tensor count differs from the entry signature")
    references = set()
    for index, line in enumerate(tensors):
        fields = shlex.split(line)
        filename = Path(fields[-1])
        if len(filename.parts) != 1 or filename.name in (".", "..") or filename.is_absolute():
            raise ValueError(f"Invalid input path for tensor {index}")
        size = int(fields[2])
        actual = (source / filename).stat().st_size
        if (
            fields[0] not in ("DEVICE", "HOST")
            or fields[-2] not in ("REPEAT", "RAW")
            or size < 1
            or actual < 1
            or (size % actual if fields[-2] == "REPEAT" else size != actual)
        ):
            raise ValueError(f"Input initializer does not cover tensor {index}")
        files.add(filename.as_posix())
        if signature[index] in (2, 3) and fields[0] == "DEVICE":
            reference = f"reference/{index}.bin"
            if (source / reference).stat().st_size not in (1, size):
                raise ValueError(f"Reference length differs for tensor {index}")
            references.add(reference)
    if not references or references != {p.relative_to(source).as_posix() for p in (source / "reference").glob("*.bin")}:
        raise ValueError("Snapshot reference set differs from writable device tensors")
    hashes = {}
    for name in sorted(files | references):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        hashes[name] = sha256(target)
    return {"signature": signature, "files": hashes, "reference_count": len(references)}


FIXTURE = HERE / "fixtures"
ARCHIVE = "swa.tar.xz"


def freeze(args):
    source, output = args.bundle.resolve(), args.output.resolve()
    if output.exists():
        raise ValueError("Fixture output already exists; use a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        files = Path(temp) / "files"
        packed = Path(temp) / "packed"
        packed.mkdir()
        metadata = copy_snapshot(source, files, args.tools.resolve(), args.case)
        shutil.copyfile(source / "callable.bin", files / "callable.bin")
        hashes = {**metadata["files"], "callable.bin": sha256(files / "callable.bin")}
        archive = packed / f"{args.case}.tar.xz"
        with tarfile.open(archive, "w:xz", format=tarfile.USTAR_FORMAT) as tar:
            for name in sorted(hashes):
                path = files / name
                member = tarfile.TarInfo(name)
                member.size, member.mode = path.stat().st_size, 0o644
                with path.open("rb") as data:
                    tar.addfile(member, data)
        save(
            packed / "fixture.json",
            {
                "source_revision": args.source_revision,
                "model": json.loads((SOURCES / "manifest.json").read_text())["origin"],
                "case": f"pypto_lib_latest/{args.case}",
                "reference_count": metadata["reference_count"],
                "source": json.loads((source / "case.json").read_text())["source"],
                "archive": archive.name,
                "archive_sha256": sha256(archive),
                "files": hashes,
            },
        )
        packed.rename(output)


def unpack_fixture(destination, fixture=FIXTURE, case="swa"):
    manifest = json.loads((fixture / "fixture.json").read_text())
    archive = fixture / f"{case}.tar.xz"
    if manifest["archive"] != archive.name or sha256(archive) != manifest["archive_sha256"]:
        raise ValueError("Bundled replay archive checksum differs from the fixture manifest")
    hashes = manifest["files"]
    with tarfile.open(archive, "r:xz") as tar:
        members = tar.getmembers()
        if sorted(member.name for member in members) != sorted(hashes):
            raise ValueError("Bundled replay member list differs from the fixture manifest")
        for member in members:
            if not member.isfile() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                raise ValueError(f"Invalid replay fixture member: {member.name}")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(member)
            if data is None:
                raise ValueError(f"Missing replay fixture payload: {member.name}")
            with data, target.open("wb") as output:
                shutil.copyfileobj(data, output)
            if sha256(target) != hashes[member.name]:
                raise ValueError(f"Bundled replay file checksum differs: {member.name}")
    return manifest


def build(args):
    if args.jobs < 1 or not (args.pto_isa / "include/pto").is_dir():
        raise ValueError("Need positive --jobs and --pto-isa containing include/pto")
    build_dir = (args.build_dir or HERE / "build" / args.platform).resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    if (build_dir / "bundle").exists():
        raise ValueError("Bundle already exists; use a new --build-dir")
    with tempfile.TemporaryDirectory(prefix="bundle.preparing-", dir=build_dir) as temporary:
        bundle = Path(temporary) / "bundle"
        bundle.mkdir()
        if args.bundle is None:
            source = Path(temporary) / "source"
            fixture = FIXTURE if args.case == "swa" else FIXTURE / "006" / args.case
            unpack_fixture(source, fixture, args.case)
        else:
            source = args.bundle.resolve()
        build_bundle(args, build_dir, bundle, source)
        bundle.rename(build_dir / "bundle")
    print(f"Built {args.platform} replay bundle: {build_dir / 'bundle'} (offline)")


def build_bundle(args, build_dir, bundle, source):
    tools = build_dir / "host"
    subprocess.run(
        [
            "cmake",
            "-S",
            str(SUITE / "production"),
            "-B",
            str(tools),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={args.cxx}",
            f"-DSIMPLER_ROOT={REPO}",
            f"-DSIMPLER_PLATFORM_A6={int(args.platform == 'a6')}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "cmake",
            "--build",
            str(tools),
            "--target",
            "native-runner",
            "replay",
            "record-tool",
            "--parallel",
            str(args.jobs),
        ],
        check=True,
    )
    metadata = copy_snapshot(source, bundle, tools, args.case)
    tasks = case_tasks(args.case)
    ids = {k for _, slots in tasks.values() for k in slots if k >= 0}
    manifest = json.loads((SOURCES / "manifest.json").read_text())
    kernels = [k for k in manifest["kernels"] if k["func_id"] in ids]
    save(
        bundle / "name_map.json",
        {
            "level": 2,
            "orchestrator_name": args.case.upper(),
            "callable_id_to_name": {str(k["func_id"]): k["name"] for k in kernels},
        },
    )
    binary_dir = build_dir / "kernels"
    binary_dir.mkdir(exist_ok=True)
    compiled = compile_kernel_set(
        args.platform, args.pto_isa, binary_dir, args.jobs,
        kernel_set=args.kernel_set, case=args.case,
    )
    executions = sum(blocks * sum(k >= 0 for k in slots) for blocks, slots in tasks.values())
    if args.platform == "a6":
        converter = tools / "adapt-plan"
        build_converter(args.cxx, converter)
        plan = bundle / "plan-rank0.bin"
        converted = build_dir / "plan-a6.bin"
        if args.stage == "006":
            executions = convert_single_vector(tools / "record-tool", converter, plan, converted, args.case)
        else:
            executions = convert_plan(tools / "record-tool", converter, plan, converted, args.five_block_t0)
        converted.replace(plan)
    runner = tools / "native-runner"
    original_callable = source / "callable.bin"
    if args.platform == "a5" and args.kernel_set == "005-export":
        # Original full-model registration fixes the starting cache set of every kernel.
        # Validate the rebuilt active images and keep every child at its source offset.
        image = original_callable.read_bytes()
        layout = json.loads(capture([runner, "--describe-layout", original_callable]))
        children = {k["id"]: k for k in layout["kernels"]}
        for record in compiled:
            child = children[record["id"]]
            offset, size = child["binary_offset"], child["binary_size"]
            if image[offset : offset + size] != (binary_dir / record["file"]).read_bytes():
                raise ValueError(f"A5 kernel differs from the source snapshot: {record['name']}")
        subprocess.run(
            [runner, "--repack-orchestration", original_callable, tools / "libreplay.so", bundle / "callable.bin"],
            check=True,
        )
    else:
        assemble_replay(tools, bundle, build_dir, binary_dir, compiled, kernels, metadata)
    case_path = bundle / "case.json"
    case = json.loads(case_path.read_text())
    case["source"]["executions"] = executions
    save(case_path, case)
    save(
        build_dir / "build.json",
        {
            "platform": args.platform,
            "source_commit": capture(["git", "-C", REPO, "rev-parse", "HEAD"]).strip(),
            "model": manifest["origin"],
            "tasks": len(tasks),
            "case": args.case,
            "stage": args.stage,
            **({"t0_blocks": 5 if args.five_block_t0 else tasks[8][0]} if args.case == "swa" else {}),
            "kernel_set": args.kernel_set,
            "executions": executions,
            "source_snapshot_files": metadata["files"],
            "reference_count": metadata["reference_count"],
            "source_callable_sha256": sha256(original_callable),
            "plan_sha256": sha256(bundle / "plan-rank0.bin"),
            "callable_sha256": sha256(bundle / "callable.bin"),
            "runner_sha256": sha256(tools / "native-runner"),
            "replay_sha256": sha256(tools / "libreplay.so"),
            "pto_isa_commit": capture(["git", "-C", args.pto_isa, "rev-parse", "HEAD"]).strip(),
            "kernels": compiled,
        },
    )


def assemble_replay(tools, bundle, build_dir, binary_dir, compiled, kernels, metadata):
    sys.path[:0] = [str(REPO), str(REPO / "python"), str(SUITE / "production")]
    from build_sources import capability_words  # noqa: PLC0415

    def signature(values):
        return f"{len(values)} " + " ".join(map(str, values))

    rows = [signature(metadata["signature"]), str(len(kernels))]
    for kernel, record in zip(kernels, compiled, strict=True):
        rows.append(
            f"{kernel['func_id']} {signature(kernel['signature'])} "
            + json.dumps(str(binary_dir / record.get("registration_file", record["file"])), ensure_ascii=False)
            + " "
            + " ".join(map(str, capability_words(kernel)))
        )
    assembly = build_dir / "assembly.txt"
    assembly.write_text("\n".join(rows) + "\n")
    subprocess.run(
        [
            str(tools / "native-runner"),
            "--assemble-orchestration",
            str(assembly),
            str(tools / "libreplay.so"),
            str(bundle / "callable.bin"),
        ],
        check=True,
    )
    expected = {
        "signature": metadata["signature"],
        "kernels": [{"id": k["func_id"], "signature": k["signature"]} for k in kernels],
    }
    image = bundle / "callable.bin"
    layout = json.loads(capture([tools / "native-runner", "--describe-layout", image]))
    data = image.read_bytes()
    for child, record in zip(layout["kernels"], compiled, strict=True):
        start = child.pop("binary_offset")
        size = child.pop("binary_size")
        if data[start : start + size] != (binary_dir / record["file"]).read_bytes():
            raise ValueError(f"Assembled machine code differs for {record['name']}")
    if layout != expected:
        raise ValueError("Assembled replay callable differs from the source signatures")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freezer = commands.add_parser("freeze", help="Export a verified materialized slice into a portable archive")
    freezer.add_argument("--case", choices=CASES_006, default="swa")
    freezer.add_argument("--bundle", type=Path, required=True)
    freezer.add_argument("--tools", type=Path, required=True, help="Matching native-runner and record-tool directory")
    freezer.add_argument("--source-revision", required=True, help="Commit used for the upstream A5 capture")
    freezer.add_argument("--output", type=Path, required=True)
    compile_case = commands.add_parser("build", help="Compile case kernels and assemble a native replay")
    compile_case.add_argument("--stage", choices=("005", "006"), default="005")
    compile_case.add_argument("--case", choices=CASES_006, default="swa")
    compile_case.add_argument("--platform", choices=["a5", "a6"], default="a5")
    compile_case.add_argument(
        "--bundle", type=Path, help="Use a materialized source slice instead of its checked-in fixture"
    )
    compile_case.add_argument("--pto-isa", type=Path, default=REPO / "build/pto-isa")
    compile_case.add_argument(
        "--five-block-t0", action="store_true", help="Use five A6 vector blocks for source task 8"
    )
    compile_case.add_argument(
        "--kernel-set", choices=("cpp", "005-export"), help="Use shared C++ kernels (default) or the earlier 005 build"
    )
    compile_case.add_argument("--build-dir", type=Path)
    compile_case.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    compile_case.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args)
    else:
        if args.stage == "006":
            if args.platform != "a6" or args.five_block_t0 or args.kernel_set is not None:
                parser.error("006 requires A6 with original VF kernels and original t0 block count")
            args.kernel_set = "original-vf"
        else:
            if args.case != "swa":
                parser.error("005 supports only the SWA fixture")
            args.kernel_set = args.kernel_set or "cpp"
        if args.five_block_t0 and args.platform != "a6":
            parser.error("--five-block-t0 requires --platform a6")
        build(args)


if __name__ == "__main__":
    main()
