# A6 SWA and MPMD Diamond regression

Both workloads use the upstream `host_build_graph` runtime with online PGO and Calendar scheduling. A6 launches one Cube and one Vector per pair. By default it uses the available stream capacity, up to 54 pairs. `--core-pairs 8` caps the launch and the calendar at 8C8V. Use `--core-pairs 54` to require full capacity and fail if it is unavailable.

SWA uses the pinned, single-card 15-task A5 snapshot and compares the writable tensors against the fused-prefix references. It rebuilds kernels and packs a callable with the current wire format. The A6 port uses one vector per MIX block and maps source task 22 from 24 two-vector blocks to 48 single-vector blocks. The 48 blocks have no synchronized start requirement, so Calendar can schedule them across multiple waves on 8 pairs. Both A6 profiles use the same bundle. Diamond verifies the join with analytical outputs for the balanced, B-last, and C-last variants.

## Build on Linux with the A6 CANN toolchain

Load the SDK environment with `ASCEND_HOME_PATH`, `ccec`, and `bisheng` available. Use the repository's pinned PTO-ISA checkout for `PTO_ISA`.

```bash
python -m simpler_setup.build_runtimes --platforms a6 --runtime host_build_graph
suite=tests/fdwic/a6_dsv41_scheduler
prod=tests/fdwic/dsv41_scheduler/production
python "$suite/prepare.py" --pto-isa "$PTO_ISA" --build-dir build/a6-swa
runner=build/a6-swa/host/native-runner
python "$prod/mpmd_diamond.py" build/a6-diamond --runner "$runner" --pto-isa "$PTO_ISA"
```

## Run the regression and collect swimlanes

Run from the repository root. Set `--device` to the A6 device index if it differs from zero. Each command uses a fresh output directory.

```bash
for bundle in build/a6-swa/bundle build/a6-diamond/{balanced,b_last,c_last}; do
  case_id=$(basename "$bundle")
  for pairs in 54 8; do
    python "$suite/run.py" "$bundle" --runner "$runner" \
      --runtime-lib build/lib/a6/onboard --core-pairs "$pairs" \
      --level 0 --out "build/a6-results/${case_id}-${pairs}-L0"
  done
done

python "$suite/run.py" build/a6-swa/bundle --runner "$runner" \
  --runtime-lib build/lib/a6/onboard --core-pairs 8 \
  --level 4 --out build/a6-results/swa-8-L4
```

Check `result.json` for numerical validation and the host logs for the resolved `block_dim`. L2 and L4 write `chip_swimlane_records.json` under the run output. The capture's `metadata.num_cores` should be 16 at 8C8V or 108 at 54C54V. Convert the capture with `python -m simpler_setup.tools.swimlane_converter` and pass the bundle's `names.json` to `--func-names`.

## Offline checks

`python tests/fdwic/a6_dsv41_scheduler/test_offline.py` verifies the pinned snapshot and 1C1V conversion, packs a callable with synthetic bytes, and exercises the A5 and A6 Calendar planner. This command needs Linux, CMake, and a C++17 compiler.
