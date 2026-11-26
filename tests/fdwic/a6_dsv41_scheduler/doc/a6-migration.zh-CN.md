# A6 SWA 迁移状态与适配说明

本文记录 00–004 使用的 SWA 图、夹具和适配结果。005 的图、核实现及超时定位见[问题与定位结论](../results/005-swa-a5-baseline-a6-timeouts/issues.zh-CN.md)。

[返回用例索引](../README.zh-CN.md) · [性能优化阶段索引](a6-optimization.zh-CN.md) · [性能复测方法](a6-environment.zh-CN.md) · [构建与复现命令](usage.zh-CN.md)

## 完成状态索引

**单卡 SWA 已在 A6 的生产 HBG 上通过固定输出校验。** 迁移复用已有 C++ 核和输入，全流程不调用 PyPTO／PTOAS。同口径 L0、L1 下界和 L3 生命周期拆解已归档。

- [x] [1. 独立 SWA 与 A5 基线](#1-独立-swa-与-a5-基线)　固定输入和参考，A5 的 HBG 与 percounter 均通过输出检查。
- [x] [2. A6 计算核编译](#2-a6-计算核编译)　19 个核完成编译、链接与提取，A5 的机器码指纹逐一保持一致。
- [x] [3. 输出投影 t28 的单 V 分工](#3-输出投影-t28-的单-v-分工)　32 个 block 保持不变，每组由一个 V 分两次处理 256 列。
- [x] [4. Attention t22 的单 V 分工](#4-attention-t22-的单-v-分工)　24 个 block 拆为 48 个，每组负责 32 个 head。
- [x] [5. plan 转换与回放包](#5-plan-转换与回放包)　核对 2735 条记录，仅转换两处 MIX 描述，得到 16 个计算任务、570 个执行实例。
- [x] [6. MIX 启动与子核调用](#6-mix-启动与子核调用)　验证镜像内间接调用和返回，独立 SWA 又覆盖了生产装载路径。
- [x] [7. SSBUF 共享与调度区布局](#7-ssbuf-共享与调度区布局)　组内共享状态通过双向握手校验。
- [x] [8. 核身份与 CP1 控制窗口](#8-核身份与-cp1-控制窗口)　按设备上报身份登记 worker，全组完成报到。
- [x] [9. IPC 寄存器与退出协议](#9-ipc-寄存器与退出协议)　逐核确认退出。
- [x] [10. 设备计时](#10-设备计时)　AICore 的 SYS_CNT 实测约 1 GHz。
- [x] [11. A6 HBG 运行时构建](#11-a6-hbg-运行时构建)　Host、AICPU、AICore 和 dispatcher 四项构建产物已生成。
- [x] [12. 启动与退出修复](#12-启动与退出修复)　修正 1C1V 启动掩码与退出回执的寄存器，保留启动与退出回归。
- [x] [13. A6 单卡 SWA 验收](#13-a6-单卡-swa-验收)　固定参考的 30 个可写缓冲区校验通过。
- [x] [14. L4 协议采集修复](#14-l4-协议采集修复)　转换器适配 1C1V，采集端越界已修复并通过 CPU 回归，板上新 L4 尚未采集。
- [x] [15. A5 与 A6 性能对照](#15-a5-与-a6-性能对照)　两端的 L0、L1 下界与 L3 拆解已归档。
- [x] [16. 合入主线双槽调度](#16-合入主线双槽调度)　A6 使用 GM 完成计数，两端已完成 HBG 和 SWA 构建，001 的 A6 对照保留为空，002 已给出 GM-FAA 数据。

[平台与工具链](#平台与工具链) · [验收要求](#验收要求) · [同口径复测](a6-environment.zh-CN.md)

适配基线和后续优化阶段分别在表中注明版本。

## 适配点与核实结论

### 1. 独立 SWA 与 A5 基线

**问题：原流程要跑双卡完整 forward 捕获任务，容易被误认为 SWA 本身也需要两卡和 PyPTO。**

**已做：** 固化 [输入、plan 和参考](../fixtures/fixture.json)，只编译 SWA 的 19 个核，复用生产 C++ runner、HBG 和原 PR 统计器。C++ 源码在 [vendor](../../dsv41_scheduler/kernels/pypto_lib_latest/vendor/)，入口为 [prepare.py](../prepare.py) 和 [run.py](../run.py)。已有用例无需重新捕获：编译不占卡，独立 SWA 执行只用一张卡。

解除的是 PyPTO／PTOAS 的代码生成依赖；C++ 仍需 CANN、PTO-ISA 编译成 AICore 机器码，执行仍需适配 A6 的 Simpler。新 PyPTO 算子的生成流程见 [执行原理](execution.zh-CN.md)。

**验证：** A5 HBG／percounter 各 11 轮通过，每轮检查 30 个可写 Device buffer；L0 AICore 均值 **0.267843／0.185173 ms** 为 11-17 历史记录；新复测见 [性能总表](../results/00-baseline/README.zh-CN.md)。本轮 A6 沿用已有零权重夹具，不新增局部非零用例。SWA 覆盖 MIX／sync_start，不覆盖跨卡通信或 defer_wait。

### 2. A6 计算核编译

**问题：A5 目标、SDK 自带 PTO 与仓库 pin 混用，不能得到可核查的 A6 核。核能编译也不等于运行时已支持 A6。**

**已做：** [compile_kernels.py](../compile_kernels.py) 使用 `dav-920r1-cube/vec`，逐核执行 ccec → `ld.lld -e kernel_entry` → `.text` 合法性检查。优先使用仓库 pin 的 PTO-ISA `83b1b2b52ded9ec5d91e02532a57cd95e497ba86`；A5／A6 输出和缓存分开，失败不继续组包。

| 适配项 | 处理与依据 |
| ------ | ---------- |
| 原始核 17／19 通过 | 两个 AIC 核的 `DualModeSplitM/N` 被 A6 拒绝；对应 AIV 的分工也必须一起改，见 [输出投影](#3-输出投影-t28-的单-v-分工)和 [Attention](#4-attention-t22-的单-v-分工) |
| PTO 头文件优先级 | 修正 include 顺序，避免误用 SDK 自带 PTO；此前错误顺序的编译不计入原核基线 |
| `-fno-jump-tables` | 保留。[elf_parser.py](../../../../simpler_setup/elf_parser.py) 只装载 `.text`，拒绝外置已分配数据；开关检查观察到 `.rodata` 差异 |
| VF AA 选项 | 保留 `-mllvm -cce-vf-aa-between-iters=true`；已检查的 4 个核开关前后 `.text` 相同，不推广为所有核都无影响 |
| 三种编译目标 | Host 用 x86_64 工具，AICPU 用 aarch64 交叉工具，AICore 用 ccec；三者不能混用 |
| 项目构建环境 | 沿用当前 Makefile 的 `DEPFILE` 构建路径需 CMake ≥3.20；项目 `.venv` 提供工具，不要求升级系统。命令见 [使用方法](usage.zh-CN.md) |

**验证结果**　A6 的 19／19 个核均通过编译、链接与机器码提取。A5 的 19 个核逐一与适配前的 `.text` 一致。

### 3. 输出投影 t28 的单 V 分工

**问题：A5 一次算 256 列，分别交给 V0、V1；A6 只有一个 V，不能只把两次搬运都改成 `SingleModeVec0`。** 同 pin PTO-ISA 的 `include/pto/npu/a6/SyncAll.hpp` 提供 1C1V 依据，独立 MIX 探针也与之相符。

**已改：** [o_proj_a.cpp](../../dsv41_scheduler/kernels/pypto_lib_latest/vendor/external/o_proj_a.cpp) 在 A6 每次计算 128 列，增加内部列循环；单 V 量化、写回后继续另一半。READY／RELEASE／STORE_DONE 与生产者等待同步改为一个消费者。

```text
A5 一个 block：C 算 256 列 → V0 前 128 列 + V1 后 128 列
A6 一个 block：C 算 128 列 → V 处理 → 列循环继续另一半
```

32 个 block 保持不变，执行实例 **96 → 64**；量化与写回算法保留，没有新增 GM 中转或工作区。A6 上这段参与了完整 SWA 的固定输出校验。

### 4. Attention t22 的单 V 分工

**问题：原一个 block 的 C 计算 64 个 head，由两个 V 分担；单 V 不能直接承接原分工和双目标搬运。**

**已改：** [sparse_gather_vf.cpp](../../dsv41_scheduler/kernels/pypto_lib_latest/vendor/external/sparse_gather_vf.cpp) 用两个 1C1V block 分担原来的 head。核内 `block/2`、`blocks/2` 保留 token、区间和 partial 槽位，`block%2` 选择 head 的前后半。

```text
A5：b    → C(64 heads) + V0(32 heads) + V1(32 heads)
A6：2b   → C(前 32 heads) + V(完整 KV、前 32 heads)
    2b+1 → C(后 32 heads) + V(完整 KV、后 32 heads)
```

| 变化 | 具体处理 |
| ---- | -------- |
| block／实例 | 24 → 48 个 block，72 → 96 个执行实例；输入、输出和工作区大小不变 |
| 数据搬运 | 单 V gather 从 32 扩至 64 条，C 算 32 行并向单 V 搬运；保留 softmax、PV 累积和分片合并算法 |
| UB | 扩大 raw／NZ 缓冲以容纳单 V 的数据分片，整图输出校验通过 |
| 事件编号 | 修正初版超出 0–7 的编译错误：NZ 的 MTE3↔V 用 5／6／2／3，P staging 用 7；MTE2↔V 的 raw 事件属于另一管线对 |

本切片已通过计算、同步和输出校验。两个 block 会重复读取 KV，因此后续跨代性能比较包含分片变化，不能都归为调度收益。其余独立 AIV 任务的 80／64／64 个逻辑 block 保留，需要运行时支持多波次推进。

### 5. plan 转换与回放包

**问题：** [record.h](../../dsv41_scheduler/production/record.h) 直接保存 `Header`、`Operand` 和 `TensorData`，不是跨 Host 架构的自描述格式；A5 的 1C2V plan 也不能直接驱动 A6 核。

**已改：** [prepare_plan.py](../prepare_plan.py) 在目标 Host 编译原生解码和转换工具，先核对全部 **2735 条记录**的规范化语义指纹，再由 [adapt_plan.cpp](../adapt_plan.cpp) 转换 t22／t28。任何非预期差异立即报错；原始夹具不覆盖。

| 检查对象 | 转换要求／结果 |
| -------- | -------------- |
| t22 | 24 个 1C2V block → 48 个 1C1V block |
| t28 | 32 个 1C2V block → 32 个 1C1V block |
| 总量 | 16 个任务不变；执行实例 **578 → 570** |
| 其余字段 | Tensor 字段、shape／stride／offset、别名、buffer 标识、标量、依赖和其他任务逐项保持一致 |
| 重复转换 | 非零退出且不创建输出，防止 Attention block 再次翻倍 |

aarch64 和 x86_64 Host 均可解码该夹具。2735 条 plan 记录按相同语义核对；单卡入口在执行前校验计划与 callable 的 SHA256。[构建与运行](usage.zh-CN.md#单卡-a6-swa)

### 6. MIX 启动与子核调用

ACL 顶层入口和 HBG 子核使用不同的编译目标。

| 被测路径 | 结果 |
| -------- | ---- |
| 将 `dav-920r1-vec` 的 ELF 直接交给 ACL 启动 | `e_flags=0xa50000`；失败 507015，设备错误 259：`binary version does not match` |
| 通用 `dav-920r1` 的 MIX ELF | `e_flags=0xa60000`；`aclrtBinaryLoadFromFile` → `aclrtBinaryGetFunction` → `aclrtLaunchKernelWithHostArgs` 执行成功 |
| 单 block MIX | GM 原子计数为 2；AIV 的 `get_subblockdim()` 为 1，支持 1C1V 结论 |
| 成功样例的链接参数 | `ld.lld -Ttext=0 -static -n -e probe`，该样例不需要 `-m aicorelinux` |

**外层和子核的调用已经在同一镜像内分两步验证。** 通用 MIX 目标生成 C/V 两个入口。两个入口按编译期宏分别走 `__DAV_CUBE__` 或 `__DAV_VEC__` 分支。

| 调用方式 | 构建与寻址 | 单 block 结果 |
| -------- | ---------- | ------------- |
| 镜像内函数指针 | `sub_c2`／`sub_v2` 分别以 `-Ttext=0 -e <入口>` 链接，用 `llvm-objcopy --only-section=.text` 提取各 24 B，以 256 B 对齐放入同一可执行镜像 | 输入 `x=5`，结果 `out_c=6, out_v=7, C_done=1, V_done=1`。两个子核均读到参数并返回外层 |

镜像内入口由 `(PC - pc_offset) + blob_offset` 确定。具体偏移依赖链接布局，验证覆盖机器码提取、间接调用、参数传递与返回。

**SWA 直接沿用生产装载。** 保留 cube／vec 子核的 `.text`，由 HBG 上传整个 `ChipCallable` 后解析入口并调用。已测镜像内入口按 256 B 对齐；生产对象的子项偏移按 `CALLABLE_ALIGN=64` 排列，子核入口的 ABI 对齐保证是 `CALLABLE_CHILD_ALIGN=16`。目前没有 A6 要求 256 B 的依据。SWA 已覆盖该生产路径，见 [单卡验收](#13-a6-单卡-swa-验收)。[装载布局](../../../../src/common/task_interface/arg_direction.h) · [Host 上传](../../../../src/common/platform/onboard/host/device_runner_base.cpp) · [调用入口](../../../../src/a5/runtime/host_build_graph/aicore/resident_scheduler_impl.h)

### 7. SSBUF 共享与调度区布局

A6 按本组实际参与的 C/V 数量布置共享调度状态，避免与已有工作区重叠。双向握手及迁移基线的整图校验均通过。[共享状态定义](../../../../src/a5/runtime/host_build_graph/runtime/scheduler/scheduler_cluster_signal.h)

### 8. 核身份与 CP1 控制窗口

启动时按设备上报的核身份关联 worker 和控制通道；算子使用逻辑 block 编号分片。全部 108 个 worker 完成报到。

### 9. IPC 寄存器与退出协议

AICPU 放行执行并在退出时逐核核对本轮回执。收齐后允许核返回。两端通过各自的平台接口读写同一通道。[AICPU 启停](../../../../src/a5/platform/shared/aicpu/platform_regs.cpp) · [核侧回执](../../../../src/a5/platform/onboard/aicore/inner_kernel.h)

### 10. 设备计时

核体耗时按设备计数器差值换算为微秒。各轮比较使用相同频率和起止端点。

### 11. A6 HBG 运行时构建

显式选择 `a6/host_build_graph`，复用平台源码，在独立的 A6 缓存和输出目录构建。Host、AICPU、AICore、回放工具与业务核使用一致的平台配置，C/V 目标分别为 `dav-920r1-cube`、`dav-920r1-vec`。Host 已识别 A6 的设备函数地址，回放沿用同一份固定输入与参考。

Host、AICPU、AICore 及 dispatcher 分开构建；HBG 的 MIX ELF 目标标志为 `0xa60000`。[单卡构建与运行](usage.zh-CN.md#单卡-a6-swa)

可在 aarch64 Host 交叉构建，也可在 x86_64 Host 构建。plan 转换工具使用 Host 原生编译器。

### 12. 启动与退出修复

| 问题 | 原因与修正 |
| ---- | ---------- |
| Host 准备失败 | `prepare_scheduler_calls` 需要识别 A6 并选择设备函数地址空间。 |
| 全卡 bootstrap 无法完成 | 1C1V 组的到达掩码为 `INIT \| AIV0 = 3`，1C2V 为 7。`PLATFORM_CORES_PER_BLOCKDIM` 是 C++ 常量，用 `#if` 判断会把它当作 0，两种配置都选到 7。改用 C++ 常量表达式后各组选中对应掩码。[组内准备与全卡计数](../../../../src/a5/runtime/host_build_graph/aicore/resident_scheduler_impl.h) |
| 核在启动时一直读到 0 | CP1 在全卡 bootstrap 完成后才写启动值。在这之前读到 0，不能据此判断 IPC 写入的可见性。[CP1 的放行顺序](../../../../src/a5/runtime/host_build_graph/aicpu/aicpu_executor.cpp) |
| CP1 收不到退出确认 | AICore 经平台写接口回送本轮确认值，CP1 从对应平台接口读取。[退出回执](../../../../src/a5/platform/onboard/aicore/inner_kernel.h) |

- **启动。** 迁移基线由 C 汇总本组到达数。合入主线后改由 AIV0 汇总，A6 掩码为 3，A5 为 7。CPU 回归调用生产 `bootstrap_ready_graph()`，覆盖 54 组、108 个 worker，验证最后一个 AIV0 发布 `bootstrap_complete=1`。该用例使用 Host 预置 INIT。[启动回归](../../../ut/cpp/a5/runtime/host_build_graph/test_hbg_paired_bootstrap.cpp)
- **退出。** 正常退出和异常收尾共用平台回执接口。A6 单卡 SWA 已验证整轮返回，A5 启停回归通过。
- **离线验证结果。** 构建与工具链 Python 测试 133 项通过，SWA 固定夹具 2 项通过。A6 配对启动和 A5 调度、启动、退出共 4 个 CTest 目标通过。

### 13. A6 单卡 SWA 验收

迁移基线包含 28 个图任务、570 个核执行实例。生产 HBG 完成启动、调度及退出，固定参考的 **30／30 个可写缓冲区**逐项校验通过。跨代性能见 [L0 与 L3 总表](../results/00-baseline/README.zh-CN.md)。

### 14. L4 协议采集修复

1C1V 拓扑有 54 组、108 个 worker。每组第三个成员槽填 `UINT64_MAX`。L4 的 `error_flags=4` 表示身份或关联校验失败，即使全图 `scheduler_error=0`，转换器仍应检查该位。[错误位定义](../../../../src/a5/runtime/host_build_graph/runtime/scheduler/scheduler_protocol_types.h)

- **转换端。** 支持按每组两个活跃成员校验 A6 的拓扑。保留对设备错误位的检查。原文件已带错误位，重新转换仍会拒绝协议审计。L3 可以从同轮依赖数据及执行子槽还原核名，`--device-only` 按设备时间显示。
- **采集端。** A6 plan 的 t22 为 48 个同步启动的 1C1V block。基线中 C 与 V 两条 L4 路径仅分配两个观测槽，记录函数读取第三项可能置错误位 4。双槽版本同样按协议保留三个槽位，第 7 代组确认在 A6 记为 `[7,7,0]`。[当前观测值采集](../../../../src/a5/runtime/host_build_graph/runtime/scheduler/scheduler_cluster_mix.h) · [协议字段](../../../../src/a5/runtime/host_build_graph/runtime/scheduler/scheduler_protocol_types.h)
- **验证范围。** A6 配置的 CPU 回归在旧版 `3505ceeea` 复现错误位 4，AddressSanitizer 定位到确认记录函数的第三项读取。修复后相同回归通过，A5 和 A6 HBG 均已编译。此修复的验证范围为 CPU 回归和双平台构建。性能比较按 L0 比性能，按 L3 加 AICPU 细分采泳道，两项均可独立完成。[回归用例](../../../ut/cpp/a5/runtime/host_build_graph/test_hbg_paired_bootstrap.cpp)

### 15. A5 与 A6 性能对照

迁移基线的 A5／A6 L0 核体窗口为 269.286／370.307 µs，L1 下界为 133.030／212.200 µs。两种数据均预热 1 次、正式 10 轮。Attention 分片与输出投影单 V 分工的影响已用逐核记录核对，A6 L3 run5 的 570 个核体区间已重建。[性能总表](../results/00-baseline/README.zh-CN.md) · [逐核分析与泳道](../results/00-baseline/README.zh-CN.md#下界为何更高)

### 16. 合入主线双槽调度

主线 `ea50c264e` 为每个 worker 提供两个可领取 block 的槽位，每组 C/V 在 SSBUF 中交替使用两份 MIX 描述符。A6 按 1C1V 建组，由 AIV0 发布本组 MIX 工作。SWA 沿用原分片和 570 个执行实例，19 个核随回放包携带主线清单的同步完成声明。[MIX 调度](../../../../src/a5/runtime/host_build_graph/runtime/scheduler/scheduler_cluster_mix.h) · [用例组包](../prepare.py)

A6 使用主线的 GM 完成路径。每个逻辑任务由唯一负责记账的核对同一个 64 位整轮计数加一，AICPU 读取它判断进度和退出。后继任务读各自前驱的 GM 完成状态，有异步条件时 CHECK 使用 GM 位图。每核的启停握手独立于任务完成计数。A5 继续按可用资源映射 CntNotify。[Host 映射选择](../../../../src/a5/platform/onboard/host/device_runner.cpp) · [完成与退休](../../../../src/a5/runtime/host_build_graph/docs/RUNTIME_LOGIC.md#73-canonical-completion-and-retirement)

合入版本的 A5 同卡对照见[阶段 001](../results/001-hbg-main-merge-swa/README.zh-CN.md)。A6 后续版本 `0d76a7d` 使用 GM-FAA，L0 核窗口 328.139 µs、Device wall 414.771 µs，30／30 个缓冲区通过校验。[阶段 002](../results/002-hbg-same-address-access/README.zh-CN.md#a6-单卡-swa-l0)

## 平台与工具链

由 CANN 和仓库锁定版本的 PTO-ISA 编译。Host、AICPU 和业务核分别构建，使用同一目标配置组包。[构建与运行](usage.zh-CN.md)

## 验收要求

1. **生产路径可用：** A6 Host 建图、AICPU 启动／握手、AICore HBG 调度与完成真实执行。直接通过 SWA 执行两处 MIX 任务，覆盖上传、调度、完成与退出。
2. **SWA 完整执行：** 迁移基线使用 `merge-baseline` 的 16 任务／570 实例计划，覆盖多波次、MIX 依赖、退出及 30 个可写 Device buffer 的数值校验。后续分片按[优化阶段 003](../results/003-a6-swa-blocks/README.zh-CN.md)分别验收。
3. **结果可复核。** 对齐 CANN、PTO-ISA、运行时版本和分片配置；核对 19 个核、计划语义和固定参考输出。
4. **结论范围明确：** 零权重夹具通过不代表真实权重精度；首轮不要求 percounter 或性能对比。后续计时分列 AICore 窗口、Device wall，并说明硬件、Host、SDK 与分片差异；L3 不混入 L0。

本切片不验证 MoE。后续若扩展，需区分固定回放与 native Host 编排；原 `pypto_lib_latest/moe` 标记 `numerical_validation: false`，不能沿用 SWA 的数值校验结论，见 [原用例说明](../../dsv41_scheduler/README.md)。
