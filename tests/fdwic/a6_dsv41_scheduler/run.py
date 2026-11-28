"""Run an A6 SWA or MPMD Diamond bundle on one device."""

import argparse
import json
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--runtime-lib", type=Path, required=True, help="e.g. build/lib/a6/onboard")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--core-pairs", type=int, help="A6 C/V pairs. Defaults to the stream limit.")
    parser.add_argument("--level", type=int, choices=(0, 2, 4), default=0)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.core_pairs is not None and not 1 <= args.core_pairs <= 54:
        parser.error("--core-pairs must be in [1, 54]")
    if min(args.device, args.warmup) < 0 or args.rounds < 1:
        parser.error("Expected a nonnegative device and warmup and a positive round count")
    bundle = args.bundle.resolve(strict=True)
    runner = args.runner.resolve(strict=True)
    runtime = args.runtime_lib.resolve(strict=True)
    case = json.loads((bundle / "case.json").read_text())
    if case.get("platform") != "a6" or case.get("case") not in ("swa", "mpmd_diamond"):
        parser.error("Expected an A6 SWA or MPMD Diamond bundle")
    for name in ("libhost_runtime.so", "libaicpu_kernel.so", "aicore_kernel.o"):
        if not (runtime / "host_build_graph" / name).is_file():
            parser.error(f"Missing A6 HBG runtime library: {name}")
    if not (runtime.parent / "dispatcher/libsimpler_aicpu_dispatcher.so").is_file():
        parser.error("Missing A6 AICPU dispatcher library")
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "run-config.json").write_text(json.dumps({**case, "core_pairs": args.core_pairs}, indent=2) + "\n")
    env = os.environ.copy()
    env.pop("SIMPLER_A6_CORE_PAIRS", None)
    if args.core_pairs is not None:
        env["SIMPLER_A6_CORE_PAIRS"] = str(args.core_pairs)
    subprocess.run(
        [runner, bundle, runtime, str(args.device), "0", "-", args.out.resolve(), str(args.level),
         str(args.warmup), str(args.rounds)],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
