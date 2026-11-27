# A5 Calendar SWA 泳道采集

本次在 A5 `Ascend950PR_9589`（32C + 64V）上采集，使用 Simpler `951377c0e`、PTO-ISA `45435c062` 和 CANN 9.3.0。以下命令在 Linux 的仓库根目录执行。

## 构建 A5 用例和运行时

激活项目的 Python 环境并加载 CANN。`build/pto-isa` 指向与 `pto_isa.pin` 一致的 PTO-ISA checkout。每次采集使用新目录。

```bash
source /usr/local/Ascend/cann-9.3.0/set_env.sh
export SWA_OUT
SWA_OUT=$(mktemp -d ./build/a5-swa-calendar.XXXXXX)
SWA_OUT=$(realpath "$SWA_OUT")
swa=tests/fdwic/a6_dsv41_scheduler

python -I simpler_setup/build_runtimes.py --platforms a5 --runtime percounter \
  --lib-dir "$SWA_OUT/lib" --profiling-dfx 1 \
  --profiling-orch 0 --profiling-sched 0 --profiling-tensormap 0
python -I "$swa/prepare.py" build --platform a5 --pto-isa "$PWD/build/pto-isa" \
  --build-dir "$SWA_OUT/case" --jobs 6
```

## 运行 L0 和 L2

L0 预热 1 轮、测量 10 轮，保留无 profiling 的计时基线。L2 预热 1 轮、采集 1 轮，在 `run1` 生成逐核和调度记录。通过生产 `run.sh` 入口运行，它接受 A5 的 L2 参数。

```bash
export DSV41_RUNNER="$SWA_OUT/case/host/native-runner"
export DSV41_PYTHON
DSV41_PYTHON=$(command -v python)
export SWA_RUN=tests/fdwic/dsv41_scheduler/production/run.sh
export SWA_LIB="$SWA_OUT/lib/a5/onboard"

task-submit --device auto --device-num 1 --max-time 600 --run '
  bash .claude/skills/onboard-arch-precheck/check.sh a5 &&
  bash "$SWA_RUN" "$SWA_OUT/case/bundle" "$SWA_LIB" "$SWA_OUT/L0" 0 1 10 --runtime=percounter &&
  bash "$SWA_RUN" "$SWA_OUT/case/bundle" "$SWA_LIB" "$SWA_OUT/L2" 2 1 1 --runtime=percounter
'
```

本次主机缺少 `npu-smi` 和 `task-submit`。实测用仓内 CANN 查询工具确认 0 号设备的 `detected_arch: a5`，用 `fuser` 检查设备空闲，然后直接运行。运行未持有设备队列锁。

```bash
cmake -S tools/cann-examples/query -B "$SWA_OUT/query"
cmake --build "$SWA_OUT/query"
"$SWA_OUT/query/query" device 0
fuser -v /dev/davinci0
```

确认架构和设备状态后，本次使用的直接运行命令如下。

```bash
TASK_DEVICE=0 bash "$SWA_RUN" "$SWA_OUT/case/bundle" "$SWA_LIB" "$SWA_OUT/L0" 0 1 10 --runtime=percounter
TASK_DEVICE=0 bash "$SWA_RUN" "$SWA_OUT/case/bundle" "$SWA_LIB" "$SWA_OUT/L2" 2 1 1 --runtime=percounter
```

## 导出 Perfetto JSON

L0 读取 Host 的 `[STRACE]` 日志生成 Host/Device 计时视图。L2 读取同一轮的 `chip_swimlane_records.json` 和 `deps.json`，并从 SWA kernel manifest 生成函数名称映射。

```bash
export PYTHONPATH="$PWD/python:$PWD"
python -m simpler_setup.tools.strace_timing "$SWA_OUT/L0/rank0/host/"host.*.log \
  --rounds-table --trace-out "$SWA_OUT/a5-swa-L0.perfetto.json"

run="$SWA_OUT/L2/rank0/run1"
python - "$swa/kernels/manifest.json" "$run/name_map.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
names = {entry['func_id']: entry['name'] for entry in manifest['kernels']}
mapping = {'callable_id_to_name': names, 'orchestrator_name': 'DSV4.1 SWA'}
Path(sys.argv[2]).write_text(json.dumps(mapping, indent=2) + '\n')
PY

python -m simpler_setup.tools.swimlane_converter "$run/chip_swimlane_records.json" \
  --deps-json "$run/deps.json" --func-names "$run/name_map.json" \
  -o "$SWA_OUT/a5-swa-L2.perfetto.json"
```

在两个 `rank0/result.json` 中确认 `passed` 和 `validated_against_fused_prefix` 均为 `true`，在 `console.log` 中确认正式轮的 `scheduler_mode=5` 及对应的 `profiling` 级别。L0 的 `--rounds-table` 包含预热行，基线均值只使用正式轮。L2 检查 `percounter_task_events.dropped` 和 `unmatched_deferred_updates` 均为 0。

## Scheduler View 标签与耗时

统计取自 A5 L2 正式轮 `rank0/run1`。`t25 [3]` 表示任务 `t25` 的第 3 号 block。`C` 是 Cube 核，`V0` 和 `V1` 是同一 cluster 的两个 Vector 核。 [L2 Perfetto 数据](swimlanes/a5-calendar-951377c0e/a5-swa-L2.perfetto.json.gz)

总耗时按 96 条核泳道累加 `Scheduler View` 中相同标签的区间，并行区间分别计入。平均耗时以导出的区间数为分母。瞬时标记只有时间戳，时长列记为 `N/A`。

| 泳道标签 | 含义 | 次数 | 总耗时（µs） | 平均耗时（µs） |
| --- | --- | ---: | ---: | ---: |
| `Range entry` | 从开始检查 Calendar 段的入口条件到进入该段 | 96 | 13.881 | 0.145 |
| `Wait for range` | 开始检查 Calendar 段的入口条件 | 96 | N/A | N/A |
| `Range entered` | 满足入口条件并进入该段 | 96 | N/A | N/A |
| `Calendar row begins` | 本核开始读取当前 Calendar 行 | 578 | N/A | N/A |
| `Read row and wait for inputs` | 从开始读行到确认输入依赖满足。部分 MIX 主核在此期间等待同组成员到齐 | 434 | 7,238.998 | 16.680 |
| `Inputs satisfied` | 本核确认输入依赖满足 | 434 | N/A | N/A |
| `Read row and wait for local MIX release` | MIX 从核从开始读行到观察到同 cluster 主核放行 | 144 | 2,483.147 | 17.244 |
| `Observe local MIX release` | MIX 从核观察到主核放行 | 144 | N/A | N/A |
| `Finalize row readiness` | 从确认输入满足到 `Ready`。MIX 主核可在此期间发布放行信号 | 434 | 33.063 | 0.076 |
| `Ready` | 当前行满足启动条件，进入 kernel 准备 | 578 | N/A | N/A |
| `Prepare kernel entry` | 从 `Ready` 到进入 kernel，包含参数、block 上下文和执行前屏障的准备 | 578 | 82.485 | 0.143 |
| `Kernel return` | kernel 返回 | 578 | N/A | N/A |
| `Output fence` | 从 kernel 返回到输出写屏障完成。另有 103 次起止时间戳相同，未导出区间 | 475 | 0.558 | 0.001 |
| `Issue DONE and retire` | 从输出屏障完成到本核退出当前行，期间发出 DONE 完成计数 | 578 | 65.348 | 0.113 |
| `DONE issued` | 本核发出 DONE 计数写入 | 578 | N/A | N/A |
| `Row retired` | 本核退出当前 Calendar 行 | 578 | N/A | N/A |
| **带时长区间合计** | | **2,739** | **9,917.480** | |

两类读行等待累计 **9,722.145 µs**，占区间累计时长的 **98.03%**，其中包含等待前驱计算和核间同步的时间。kernel 执行区间可在 `Worker View` 查看。
