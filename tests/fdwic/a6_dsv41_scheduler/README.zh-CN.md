# SWA 独立用例

005 使用主线 `48fd8fef3` 的原生 SWA 回放。当前默认用例将四个含导出 VF 的活跃核切换到两端共用的 C++ 实现。A5 与 A6 共用这一版计算图和来自原始 forward 的输入与参考输出。[005 性能对照](results/005-swa-a5-baseline-a6-timeouts/README.zh-CN.md)

| 项目 | 内容 |
| ---- | ---- |
| 计算图 | 源任务 t8 至 t34 中有 15 个计算任务，使用 19 个业务核。输出端 t34 把 O-B 和 mHC post 合为一个 MIX |
| A5 | 32 个 1C2V 核组，每轮 632 次核执行 |
| A6 | 54 个 1C1V 核组。t0 从 20 分块改为 5 分块后，每轮计划从 660 次核执行减为 645 次。五分块旧导出 t12 连续三轮均为领取 12 个、完成 0 个；共享 C++ 包尚无 A6 整图结果。[005 故障现象与定位结论](results/005-swa-a5-baseline-a6-timeouts/issues.zh-CN.md) |
| 核与输入 | 两端各自编译核。固定输入与参考由原生 forward 提取，运行独立 SWA 只需单卡 |
| 历史版本 | 00–004 使用上一版 SWA。004 的 A6 L0 为 290.068 µs，涉及 16 个任务和 616 个实例。[004 报告](results/004-a6-swa-output-tiles/README.zh-CN.md) |

## 文档索引

| 要看什么 | 文档 | 内容 |
| -------- | ---- | ---- |
| SWA 从 Host 建图到设备退出如何运转 | [HBG 调度全流程交互图解](doc/hbg-scheduler-guide.zh-CN.html) | 逐阶段交付物、普通与 MIX 调度、A5／A6 硬件路径、性能口径与泳道，可离线打开 |
| 00–004 的 A6 迁移进展 | [迁移进展简报](doc/a6-swa-progress.zh-CN.md) | 逐项适配清单、输出校验和 A5／A6 单卡性能总表 |
| 怎么构建、运行和复测 | [使用方法](doc/usage.zh-CN.md) | 命令、参数、输出位置、修改后需要重编什么 |
| 为什么不依赖 PyPTO／PTOAS | [执行原理](doc/execution.zh-CN.md) | 已有材料、真实执行路径、依赖边界、全新算子的生成流程 |
| A6 迁移完成了什么、怎样适配 | [迁移状态与适配说明](doc/a6-migration.zh-CN.md) | 开头勾选索引；逐项说明问题、改动、核实结论、验证范围和剩余缺口 |
| 如何复测 A6 | [同口径复测](doc/a6-environment.zh-CN.md) | 005 A6 新机组包、四核 C++ 回退、5-block 整层验证、L2 泳道与同卡 L0 对照 |
| 性能优化到了哪个阶段 | [性能优化阶段索引](doc/a6-optimization.zh-CN.md) | 各阶段的改动、报告、指标 JSON 与可归档的泳道 |
| 优化开始时 A5 和 A6 的性能如何 | [A5／A6 性能复测报告](results/00-baseline/README.zh-CN.md) | PR 的 L0 数据、Host 耗时、L3 的 AICPU 前后段拆解和详细泳道 |

平台适配结论归入迁移说明，性能实验按阶段归入 `results/`，并在优化索引登记。

## 代码与数据

| 位置 | 用途 |
| ---- | ---- |
| [prepare.py](prepare.py) | 读取新版快照，编译目标核与原生工具，组装运行包 |
| [compile_kernels.py](compile_kernels.py) | 独立检查目标平台的 19 个核：编译、链接、提取机器码；不启动设备 |
| [prepare_plan.py](prepare_plan.py)、[adapt_plan.cpp](adapt_plan.cpp) | 按 005 的 A6 分片生成计划，逐条核对原始记录与允许的改动 |
| [run.py](run.py) | A5 单卡性能复测；A6 单卡 HBG 功能验收入口 |
| [test_fixture.py](test_fixture.py) | 离线检查输入包与参考覆盖 |
| [fixtures/](fixtures/) | 固定输入、plan、参考与来源清单 |
| [performance.py](performance.py) | 统计 PR 指标与 AICPU 生命周期耗时，导出详细泳道 |
| [A5 详细泳道](results/00-baseline/swa-a5-hbg-L3-detail.json.xz) | 00 阶段单卡 HBG 的 L3 历史泳道 |

使用说明和阶段索引在 `doc/`，性能报告及泳道在 `results/<阶段>/`。构建产物写入 `build/`，测试工作目录由 `--output` 指定。
