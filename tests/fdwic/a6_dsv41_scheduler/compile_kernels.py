#!/usr/bin/env python3
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Compile/link the selected DSV4.1 kernels without building or launching a runtime."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SUITE = HERE.parent / "dsv41_scheduler"
SOURCES = SUITE / "kernels/pypto_lib_latest"
WORKLOAD = SUITE / "workloads/pypto_lib_latest"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_source(case):
    if case != "swa":
        raise ValueError(f"Unsupported case: {case}")
    return json.loads((HERE / "fixtures/fixture.json").read_text())["source"]


def swa_tasks():
    return case_tasks("swa")


def case_tasks(case):
    names = {k["name"]: k["func_id"] for k in json.loads((SOURCES / "manifest.json").read_text())["kernels"]}
    tasks = {}
    table = WORKLOAD / case / "tasks.tsv"
    text = table.read_text()
    if hashlib.sha256(text.encode()).hexdigest() != case_source(case)["task_table_sha256"]:
        raise ValueError(f"Task table checksum differs for {case}")
    for row in text.splitlines():
        if row.startswith("#"):
            continue
        task, kernels, mask, blocks, _ = row.split()
        active = iter(kernels.split("+"))
        slots = [names[next(active)] if int(mask) & (1 << slot) else -1 for slot in range(3)]
        if next(active, None) is not None:
            raise ValueError(f"Task {task} has excess kernel names")
        tasks[int(task)] = (int(blocks), slots)
    return tasks


def retarget_vector_ir(source, cpu):
    """Translate the exported A5 scalar-shift ABI to the A6 CCE vector intrinsics."""
    if '"target-cpu"="dav-c310-vec"' not in source or cpu != "dav-920r1-vec":
        raise ValueError("Unexpected source or target VF architecture")
    source = source.replace('"target-cpu"="dav-c310-vec"', f'"target-cpu"="{cpu}"')
    source = re.sub(r' "target-features"="[^"]*"', "", source)

    # A6's vshls/vshrs overloads take a uint32_t scalar. Preserve the input,
    # direction, signedness and predicate; zeroing is valid for inactive .x lanes.
    def shift(match):
        operation, signedness = match.groups()
        intrinsic = f"llvm.hivm.{operation}.v64{signedness}32u32.z"
        return f"""declare <64 x i32> @{intrinsic}(<64 x i32>, i32, <256 x i1>)
define internal <64 x i32> @swa.{operation}.{signedness}(<64 x i32> %input, i16 %shift, <256 x i1> %mask)
alwaysinline {{
    %bits = zext i16 %shift to i32
    %result = call <64 x i32> @{intrinsic}(<64 x i32> %input, i32 %bits, <256 x i1> %mask)
    ret <64 x i32> %result
}}"""

    name = r"@llvm\.hivm\.(vsh[lr]s)\.([su])\.x\.v64i32"
    source = re.sub(r"declare <64 x i32> " + name + r"\(<64 x i32>, i16, <256 x i1>\)", shift, source)
    return re.sub(name + r"\(", r"@swa.\1.\2(", source)


def compile_kernel_set(platform, pto, output, jobs):
    sys.path[:0] = [str(REPO), str(REPO / "python"), str(SUITE / "production")]
    from build_sources import VF_FLAGS, compile_vector_functions  # noqa: PLC0415

    from simpler_setup.a5_input_cache import annotate_a5_hbg_text  # noqa: PLC0415
    from simpler_setup.compile_pool import compile_slot, compile_worker_budget  # noqa: PLC0415
    from simpler_setup.elf_parser import extract_text_section  # noqa: PLC0415
    from simpler_setup.kernel_compiler import KernelCompiler  # noqa: PLC0415

    compiler = KernelCompiler(platform)
    toolchain = compiler.ccec
    if toolchain is None:
        raise ValueError(f"Missing CCEC toolchain for {platform}")
    arch = next(f for f in toolchain.get_compile_flags(core_type="aiv") if f.startswith("--cce-aicore-arch="))
    vf_flags = (arch, *VF_FLAGS[1:])
    pto = pto.resolve()
    ids = {k for _, slots in swa_tasks().values() for k in slots if k >= 0}
    kernels = [k for k in json.loads((SOURCES / "manifest.json").read_text())["kernels"] if k["func_id"] in ids]
    if len(kernels) != len(ids):
        raise ValueError("Incomplete SWA source manifest")

    def compile_one(kernel):
        source = SOURCES / kernel["source"]
        vf_paths = kernel.get("vf_llvm", [])
        extra = compiler.get_orchestration_include_dirs("host_build_graph") + [
            str(SOURCES / item) for item in kernel["include_dirs"]
        ]
        helpers = [SOURCES / path for path in vf_paths]
        with compile_slot(), tempfile.TemporaryDirectory() as tmp:
            if helpers:
                includes = [
                    pto / "include",
                    pto / "include/pto",
                    *compiler.get_incore_include_dirs(),
                    *compiler.get_ascend_incore_include_dirs(),
                    *extra,
                ]
                if platform == "a6":
                    # Per-function LLVM target attributes override the driver's selected chip.
                    # Keep the exported helper operations and let the A6 backend select its own features.
                    retargeted = []
                    for path in helpers:
                        target = Path(tmp) / path.name
                        target.write_text(retarget_vector_ir(path.read_text(), arch.split("=")[1]))
                        retargeted.append(target)
                    helpers = retargeted
                binary = compile_vector_functions(compiler, source, includes, helpers, vf_flags)
            else:
                binary = extract_text_section(
                    compiler.compile_incore(
                        str(source), core_type=kernel["core_type"], pto_isa_root=str(pto), extra_include_dirs=extra
                    )
                )
        target = output / f"kernel-{kernel['func_id']}.bin"
        target.write_bytes(binary)
        registration = target
        if platform == "a5":
            annotated = annotate_a5_hbg_text(binary, kernel["core_type"])
            if annotated != binary:
                registration = target.with_suffix(".hbg")
                registration.write_bytes(annotated)
        print(f"Compiled {platform} SWA {kernel['func_id']}: {kernel['name']}", flush=True)
        record = {
            "id": kernel["func_id"],
            "name": kernel["name"],
            "core": kernel["core_type"],
            "file": target.name,
            "registration_file": registration.name,
            "bytes": len(binary),
            "source": os.path.relpath(source, SOURCES),
            "source_sha256": sha256(source),
            "vf_llvm": {path: sha256(SOURCES / path) for path in vf_paths},
            "binary_sha256": sha256(target),
        }
        return record

    with compile_worker_budget(jobs), ThreadPoolExecutor(max_workers=jobs) as pool:
        compiled = list(pool.map(compile_one, kernels))
    summary = {
        "platform": platform,
        "case": "swa",
        "runtime_executed": False,
        "kernels": compiled,
        "compiler": subprocess.check_output([toolchain.cxx_path, "--version"], text=True).strip(),
        "flags": {core: toolchain.get_compile_flags(core_type=core) for core in ("aic", "aiv")},
        "vf_flags": vf_flags,
        "manifest_sha256": sha256(SOURCES / "manifest.json"),
        "pto_isa_commit": subprocess.check_output(["git", "-C", pto, "rev-parse", "HEAD"], text=True).strip(),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return compiled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("a5", "a6"), default="a6")
    parser.add_argument("--pto-isa", type=Path, default=REPO / "build/pto-isa")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1 or not (args.pto_isa / "include/pto").is_dir():
        parser.error("Need positive --jobs and --pto-isa containing include/pto")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    compile_kernel_set(args.platform, args.pto_isa, output, args.jobs)


if __name__ == "__main__":
    main()
