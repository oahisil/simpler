#!/usr/bin/env python3
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
"""Materialize the three new 006 rank-zero snapshots from one real two-rank A5 capture."""

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent / "dsv41_scheduler"
sys.path.insert(0, str(SUITE / "production"))

from export_cases import export
from materialize import materialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True, help="Production A5 build containing forward/ and host/")
    parser.add_argument("--capture", type=Path, required=True, help="Full forward capture with rank0/ and rank1/")
    parser.add_argument("--runtime", type=Path, required=True, help="Matching two-rank gate runtime/a5/onboard")
    parser.add_argument("--output", type=Path, required=True, help="New directory for snapshots and preparation results")
    args = parser.parse_args()
    build, capture = args.build_dir.resolve(), args.capture.resolve()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    for rank in (0, 1):
        result = json.loads((capture / f"rank{rank}/result.json").read_text())
        if not (
            result["passed"]
            and result["ranks"] == 2
            and result["rank"] == rank
            and result["runtime"] == "host_build_graph"
            and not result["isolated_case"]
        ):
            parser.error(f"Expected a successful two-rank HBG forward capture for rank {rank}")
    flavor = "pypto_lib_latest"
    output.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "DSV41_RUNNER": str(build / "host/native-runner")}
    for name in ("engram", "c1a_reuse", "c1a_reindex"):
        (bundle,) = export(
            build / "forward" / flavor, capture, flavor, output / "cases", build / "host", case_names=(name,)
        )
        materialize(bundle, runtime, output / "preparation" / name, env)


if __name__ == "__main__":
    main()
