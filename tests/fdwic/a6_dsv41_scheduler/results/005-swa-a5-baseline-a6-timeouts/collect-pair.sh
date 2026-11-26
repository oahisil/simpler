#!/usr/bin/env bash
# Copyright (c) PyPTO Contributors.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# -----------------------------------------------------------------------------------------------------------
# Capture matched A5 SWA data from a pristine main checkout and a migrated checkout.
# Source the matching CANN environment before invoking. All compilation precedes this script.
set -euo pipefail
if [[ $# != 5 ]]; then
    cat >&2 <<'USAGE'
Usage: collect-pair.sh prepare MAIN_REPO MIGRATED_REPO GATED_MAIN_RUNTIME NEW_PREPARED_DIR
       collect-pair.sh collect MAIN_REPO MIGRATED_REPO SWA_SNAPSHOT NEW_RESULTS_DIR
The first command captures the original EP2 forward and materializes the single-device SWA.
Pass NEW_PREPARED_DIR/cases/pypto_lib_latest/swa as SWA_SNAPSHOT for the second command.
A5 uses the shared 1 GHz device counter. Set DSV41_SHARED_DEVICE_CLOCK=0 to omit the clock split.
USAGE
    exit 2
fi
mode=$1
main=$(realpath -e -- "$2")
migrated=$(realpath -e -- "$3")
input=$(realpath -e -- "$4")
out=$(realpath -m -- "$5")
suite=tests/fdwic/dsv41_scheduler
build=$main/$suite/build/portable
# Keep the full-forward resource settings and native launch code identical for both runtime arms.
runner=$migrated/$suite/build/portable/host/native-runner
: "${ASCEND_HOME_PATH:?source the CANN set_env.sh}"
[[ -x $runner ]] || { echo "Missing executable $runner" >&2; exit 2; }
export PYTHONPATH="$migrated:$migrated/python${PYTHONPATH:+:$PYTHONPATH}"
export DSV41_PYTHON="$migrated/.venv/bin/python" DSV41_RUNNER="$runner" DSV41_BUILD_DIR="$build"
if [[ $mode == prepare ]]; then
    [[ ! -e $out ]] || { echo "Preparation output already exists: $out" >&2; exit 2; }
    exec "$DSV41_PYTHON" "$migrated/$suite/production/prepare.py" "$input" "$out" \
         --build-dir "$build" --runner "$runner" --runtime host_build_graph --case swa
fi
if [[ $mode == collect ]]; then
    [[ ! -e $out ]] || { echo "Capture output already exists: $out" >&2; exit 2; }
    "$DSV41_PYTHON" - "$main/$suite" "$input" "$build/host/record-tool" "$migrated/tests/fdwic/a6_dsv41_scheduler" <<'PY'
import json
import subprocess
import sys
from pathlib import Path
suite, bundle, tool, case_tools = map(Path, sys.argv[1:])
sys.path.insert(0, str(case_tools))
from prepare_plan import read_records, validate_source
sys.path.insert(0, str(suite / "production"))
from topology import device_count
source = json.loads((suite / "workloads/pypto_lib_latest/source.json").read_text())["cases"]["swa"]
case = json.loads((bundle / "case.json").read_text())
if (case["flavor"], case["case"], case["source"], device_count(bundle)) != ("pypto_lib_latest", "swa", source, 1):
    sys.exit("Prepared SWA differs from the main checkout or lacks a valid single-device snapshot")
if not (bundle / "callable.bin").is_file() or not any((bundle / "reference").glob("*.bin")):
    sys.exit("Missing replay callable or fused reference data")
first, last = (bundle / "slice.txt").read_text().split()
record = json.loads(subprocess.check_output([tool, bundle / "plan-rank0.bin", first, last]))
if (first, last, record["tasks"], record["executions"]) != (
    source["first_source_task"], source["last_source_task"], source["tasks"], source["executions"]
):
    sys.exit("Captured slice or physical executions differ from source.json")
validate_source(read_records(tool, bundle / "plan-rank0.bin"))
PY
    mkdir -p -- "$(dirname -- "$out")"
    mkdir -- "$out"
    for arm in main migrated; do
        repo=$main
        [[ $arm == main ]] || repo=$migrated
        library=$repo/build/lib/a5/onboard
        git -C "$repo" rev-parse HEAD > "$out/$arm.commit"
        sha256sum "$library"/host_build_graph/{libhost_runtime.so,libaicpu_kernel.so,aicore_kernel.o} \
                  "$library/../dispatcher/libsimpler_aicpu_dispatcher.so" > "$out/$arm.runtime.sha256"
    done
    sha256sum "$runner" > "$out/runner.sha256"
    (cd "$input" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$out/swa.sha256"
    printf -v command '%q ' bash "$(realpath -- "$0")" device "$main" "$migrated" "$input" "$out"
    # TASK_DEVICE is supplied by task-submit. A single reservation keeps all six batches on the same card.
    task-submit --device auto --device-num 1 --max-time "${DSV41_MAX_TIME:-600}" \
        --run 'TASK_DEVICE="$TASK_DEVICE" '"$command" 2>&1 | tee "$out/queue.log"
    for arm in main migrated; do
        base=$out/$arm
        for level in 0 1 2; do
            options=()
            if [[ $arm == main && $level == 2 ]]; then
                options+=(--trace "$base/swa-a5-main-hbg-L2.json.xz")
            fi
            [[ ${DSV41_SHARED_DEVICE_CLOCK:-1} != 1 ]] || options+=(--shared-device-clock)
            "$DSV41_PYTHON" "$migrated/tests/fdwic/a6_dsv41_scheduler/performance.py" "$base/L$level" \
                --platform a5 --level "$level" --json "$base/L$level/metrics.json" "${options[@]}"
        done
    done
    for arm in main migrated; do
        "$DSV41_PYTHON" "$migrated/$suite/production/sync_bound.py" "$input" "$out/$arm/L1/rank0" \
            "$out/$arm/sync-bound" --time-limit "${DSV41_BOUND_SECONDS:-30}"
    done
    exit
fi
if [[ $mode != device || -z ${TASKQUEUE_INSIDE:-} || ! ${TASK_DEVICE:-} =~ ^[0-9]+$ ]]; then
    echo 'The device stage requires a single card allocated by task-submit.' >&2
    exit 2
fi
sha256sum --check "$out/runner.sha256"
bash "$main/.claude/skills/onboard-arch-precheck/check.sh" a5
npu-smi info -t board -i "$TASK_DEVICE" > "$out/board.log"
printf '%s\n' "$TASK_DEVICE" > "$out/device.txt"
unset SIMPLER_HBG_AICPU_DETAIL_ENABLE SIMPLER_HBG_BIND_BREAKDOWN_ENABLE SIMPLER_HBG_HOST_PHASE_RECORDS_ENABLE
for level in 0 1 2; do
    arms=(main migrated)
    [[ $level != 1 ]] || arms=(migrated main)
    for arm in "${arms[@]}"; do
        repo=$main
        [[ $arm == main ]] || repo=$migrated
        base=$out/$arm/L$level
        rank=$base/rank0
        mkdir -p -- "$rank/ascend"
        echo "$arm L$level device=$TASK_DEVICE"
        if ! ASCEND_PROCESS_LOG_PATH="$rank/ascend" "$runner" "$input" "$repo/build/lib/a5/onboard" \
            "$TASK_DEVICE" 0 "$base/rootinfo" "$rank" "$level" 1 10 --runtime=host_build_graph \
            > "$rank/console.log" 2>&1; then
            tail -n 30 "$rank/console.log" >&2
            exit 1
        fi
    done
done
