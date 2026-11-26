# SWA 005 的构建与运行

005 对应主线 `48fd8fef3` 的 PyPTO-lib 原生 SWA。A5 迁移版用固定快照、共享 C++ 核与本版本的 HBG 运行库采集 L0、L1、L2。[005 结果和复测入口](../results/005-swa-a5-baseline-a6-timeouts/README.zh-CN.md)

## 单卡运行

在仓库根目录加载匹配的 CANN，激活本工作树的项目虚拟环境。PTO-ISA 使用主线固定的 `45435c06`。构建工具需要 CMake、Ninja、C++ 和 CANN 针对目标芯片的编译器。

```bash
source /usr/local/Ascend/cann/set_env.sh
source .venv/bin/activate
export PYTHONNOUSERSITE=1
case_dir=tests/fdwic/a6_dsv41_scheduler
platform=a6                  # 在 A5 上改为 a5
python simpler_setup/build_runtimes.py --platforms "$platform" --runtime host_build_graph \
  --profiling-dfx 1 --profiling-orch 0 --profiling-sched 0 --profiling-tensormap 0
python -I "$case_dir/prepare.py" build --platform "$platform" --pto-isa build/pto-isa \
  --build-dir "build/005-swa-$platform"
```

A6 在已分配的逻辑卡上运行，示例取设备 0。默认执行一次 HBG 用于数值验收。A5 可由 `task-submit` 自动分配单卡。

```bash
python -I "$case_dir/run.py" --build-dir build/005-swa-a6 --device 0 \
  --runtime-lib build/lib/a6/onboard --output outputs/005-swa-a6
# A5 环境
python -I "$case_dir/run.py" --build-dir build/005-swa-a5 --runtime host_build_graph \
  --runtime-lib build/lib/a5/onboard --output outputs/005-swa-a5
```

输出目录包含原生 runner 日志及逐轮数值校验。A5 迁移版的采集等级、调度下界、分块及代表轮选取见[005 性能报告](../results/005-swa-a5-baseline-a6-timeouts/README.zh-CN.md)。每次复测使用新的输出目录。

## 从原始 forward 刷新单卡快照

在 A5 上构建生产工具和匹配的运行库。原始 forward 需要两张卡同步起跑，使用测试套件的 device-start gate。SWA 前缀输入和最终参考随后在单卡上生成。请保留 gate 对应的原始运行库，供单卡准备使用。[生产套件构建说明](../../dsv41_scheduler/README.md#prepare-and-run)

```bash
suite=tests/fdwic/dsv41_scheduler
bash "$suite/build.sh" --pto-isa "$PWD/build/pto-isa"
python "$suite/production/device_start_gate.py" --runtime-dir build/lib/a5/onboard \
  --output-dir build/005-gate
python "$suite/production/prepare.py" build/005-gate/runtime/a5/onboard build/005-prepared \
  --case swa --build-dir "$suite/build/portable"
python -I "$case_dir/prepare.py" build --platform a6 \
  --bundle build/005-prepared/cases/pypto_lib_latest/swa --build-dir build/005-swa-a6-fresh
```

默认的 cpp 组在 A5、A6 上均重编并注册 19 个活跃核。仅历史 A5 对照组（--kernel-set 005-export）保留原 callable 的全部核位置。A6 按 1C1V 改写分片，005 五分块配置使用 `--five-block-t0`，详见[A6 单卡采集方法](a6-environment.zh-CN.md)。输入和参考取自同一快照。只改调度时可沿用快照和算子包，重编运行库再测；图、算子或 ABI 有变化时应重新校对计划、输入和参考。

## 离线检查

```bash
python -I "$case_dir/test_fixture.py"
python -I "$case_dir/compile_kernels.py" --platform a6 --output build/005-a6-kernels-check
python -I "$case_dir/prepare_plan.py" --plan build/005-prepared/cases/pypto_lib_latest/swa/plan-rank0.bin \
  --record-tool "$suite/build/portable/host/record-tool" --output build/005-a6-plan-check
```

004 的 `post54-ob54` 是旧图分片。005 的 t34 已经融合 O-B 和 mHC post，当前 A6 组合固定为每轮 660 次业务核执行。旧图对照见 [004 归档](../results/004-a6-swa-output-tiles/README.zh-CN.md)。
