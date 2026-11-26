# 005 SWA · 四个 C++ 核的 A6 五 block 回放

默认组包使用 `--kernel-set cpp`。两端共用 005 的 15 个计算任务、19 个活跃核的源码及输入、输出参考。A6 继续使用 54 个 qkv block 和 5 个 t0 block，每轮 645 次执行；A5 原计划为 632 次。

## 回退范围

| 业务核 | 两端共用实现及适配 |
| --- | --- |
| t0 `hc_collapse_norm` | 原 A6 的 C++ 实现扩展到 A5，沿用四路顺序求和、BF16 舍入及 RMSNorm。源自 PyPTO-lib `ad299c7f` |
| t4 `qkv_quant` | 采用 #1870 前的 C++ 量化实现（`873c2452`），保留 005 入口的 atomic mode 复位，按实际 block 数跨步，写 M16 排布的 scales |
| t12 `norm_rope_publish` | 采用 #1810 前的 C++（`31ed39ac`），保留页归属、RMSNorm、RoPE、FP8 打包和两个真实写回。005 的 cache 是二维字节视图，按 `strides[0]` 取每行 544 字节 |
| 源任务 27 `mhc_sinkhorn` | 采用 #1750 前的 C++ 系数计算（`5359e772`），接 005 的 7 个张量和 4 个标量，每组 6 行、20 次迭代、epsilon 为 1e-6 |

这四项覆盖本图所有原来带独立 VF LLVM 文件的核，其余 15 项使用原源码。两个平台均直接用 CCEC 编译 C++，按原 func ID 注册实际生成的 19 个核。每项源码、机器码的 SHA-256 和编译目标见各包的 `build.json` 和 `kernels/summary.json`。历史导出核对照可通过 `--kernel-set 005-export` 组包。[构建选择](../compile_kernels.py) · [导出核超时定位结论](../results/005-swa-a5-baseline-a6-timeouts/issues.zh-CN.md#a6-t12-stall)

## 从源码构建 A6 回放包

在仓库根目录使用项目虚拟环境，加载 CANN 9.3.0，并按仓库的 `pto_isa.pin` 构建。

使用空的组包目录。如果本地 Python 扩展与源码版本不一致，可在项目虚拟环境中运行 `pip install --no-build-isolation -e .`。

```bash
set -eo pipefail
source /usr/local/Ascend/cann-9.3.0/set_env.sh
source .venv/bin/activate
export PYTHONNOUSERSITE=1
case_dir=tests/fdwic/a6_dsv41_scheduler
python simpler_setup/build_runtimes.py --platforms a6 --runtime host_build_graph \
  --profiling-dfx 1 --profiling-orch 0 --profiling-sched 0 --profiling-tensormap 0
test "$(git -C build/pto-isa rev-parse HEAD)" = "$(cat pto_isa.pin)"
python -I "$case_dir/prepare.py" build --platform a6 --kernel-set cpp --five-block-t0 \
  --pto-isa build/pto-isa --build-dir build/005-core/a6
```

## A6 单卡 L2 与 L0

从仓库根目录运行，设置 `TASK_DEVICE` 为已分配的逻辑设备号。L2 采集泳道，L0 测量性能；各等级独立预热 1 轮、测量 10 轮。

```bash
set -eo pipefail
source /usr/local/Ascend/cann-9.3.0/set_env.sh
source .venv/bin/activate
export PYTHONPATH="$PWD/python:$PWD" PYTHONNOUSERSITE=1
case_dir=tests/fdwic/a6_dsv41_scheduler
replay="$PWD/build/005-core/a6"
runtime="$PWD/build/lib/a6/onboard"
device=${TASK_DEVICE:?请设置已分配的 A6 逻辑设备号}
out=$(mktemp -d "${TMPDIR:-/tmp}/swa-cpp-a6-XXXXXXXX")
echo "$out"
cp "$replay/build.json" "$out/"
unset SIMPLER_HBG_AICPU_DETAIL_ENABLE SIMPLER_HBG_BIND_BREAKDOWN_ENABLE SIMPLER_HBG_HOST_PHASE_RECORDS_ENABLE
for level in 2 0; do
  step="$out/L$level"
  rank="$step/rank0"
  mkdir -p "$rank/ascend"
  if ! ASCEND_PROCESS_LOG_PATH="$rank/ascend" "$replay/host/native-runner" \
       "$replay/bundle" "$runtime" "$device" 0 "$step/rootinfo" "$rank" "$level" 1 10 \
       --runtime=host_build_graph > "$rank/console.log" 2>&1; then
    tail -n 40 "$rank/console.log"
    exit 1
  fi
  opts=()
  if [[ $level == 2 ]]; then opts+=(--trace "$out/swa-a6-cpp-L2.json.xz"); fi
  python "$case_dir/performance.py" "$step" --platform a6 --five-block-t0 --level "$level" \
    --json "$step/metrics.json" "${opts[@]}" | tee "$step/summary.log"
done
```

每个采集等级分别生成 `result.json` 和 `metrics.json`。L2 导出器核对正式轮的 645 次执行，以 Device wall 最接近中位数的一轮生成 `swa-a6-cpp-L2.json.xz`。[导出与校验](../performance.py)
