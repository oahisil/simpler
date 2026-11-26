# 005　SWA 回放故障与定位结论

[005 性能对照与 L2 调度开销](README.zh-CN.md)

**005 在 A6 上记录了两个与 PyPTO `pl.reg_scope()` 导出核相关的故障：t0 报向量指令配置错误 331；t0 改为五分块后，旧 t12 连续三轮在 helper5 调用后的输出交接阶段超时。** 005 性能报告记录，保持 t0 五分块、将四个向量核切到设备 C++ 后，A6 整图通过 25/25 份输出校验。[整图实测](README.zh-CN.md#整图性能)

下文 t0、t4、t10、t12 均为**回放任务号**。其中 t4 是 QKV 量化（源任务 12），t12 是 `norm_rope_publish`（源任务 20）。`CLAIMED` 表示 worker 已领取分块；判断业务核执行到哪里，还需看同一轮的核内标记。

## 合并后遇到的问题

| 问题 | 现象 | 定位结论与修复 |
| --- | --- | --- |
| 双卡 forward 生成 SWA 输入失败（`-1004`） | 每个 rank 需要 4,218,097,664 字节激活空间，超过 2 GiB 上限 | `14c95f96b` 将完整双卡 HBG forward 的限额调到 4 GiB，成功导出输入。单卡 SWA 对照沿用 2 GiB 和同一 runner。[实现](../../../dsv41_scheduler/production/prepare.py) |
| [CntNotify 完成通知适配遗漏（271）](#a6-completion-271) | Host 的 CntNotify 映射数为 0，设备仍向地址 0 写 1 | 根因已确认。A6 在未映射时改走 GM 记账，复测未再出现 271 |
| [启动超时](#a6-bootstrap) | 108 个 worker 的到达计数为 0 | 配套组包可通过启动，产物混用只是推测 |
| [t0 的 `pl.reg_scope()` 导出核在 A6 报 331](#a6-t0-vf-331) | A5 导出核通过参考校验；A6 的 AIV 报向量指令配置非法，PC 落在 `hc_collapse_norm` | 已确认这版 t0 导出核在 A6 上触发硬件错误，改用设备 C++ 绕过。触发 331 的具体指令和参数仍未确定 |
| [C++ t0 的 20 个分块缺一个](#a6-t0-20-blocks) | 两版 C++ 都是 worker62 领了 block2 后未完成 | 改为 5 分块后能越过 t0。20 分块缺票原因待查 |
| [五分块下的 qkv、t10](#a6-qkv-block8) | qkv block8 曾未返回，t10 block20 曾缺 AIV 贡献；后续原版均能通过 | 间歇现象，重复对照不支持给 qkv 加屏障作为修复 |
| [t12 的 `pl.reg_scope()` 导出核在 A6 超时](#a6-t12-stall) | 连续三轮前驱完成，t12 均领取 12 个分块、返回 0 个 | 旧导出核卡在 helper5 调用后的输出交接。t12 已切到设备 C++，绕过独立 VF helper；四核一起切换后整图通过 |

<a id="a6-t12-stall"></a>

### t12 的 `pl.reg_scope()` 导出核在 A6 超时

**现象。** 旧导出核、t0 五分块的连续 3 个有效轮次里，t4 的 block8 已完成，t10 的 64 份贡献已收齐，t12 的前驱 t0、t11 也都完成。12 个 AIV worker 领取 t12 后，完成字始终是 `total=12, remaining=12, debt=12`。同轮下游 t14、t22、t25、t26 没有执行记录。

**与 `pl.reg_scope()` 的关系。** 在 005 对应的 Python 源码中，`norm_rope_publish` 通过 `_flush_window` 按行数调用 `_window_postprocess`。后者在 `with pl.reg_scope():` 中计算待写回的数据和缩放因子。这份导出物将不同调用分支编成 8 个独立 VF helper，其中 6 行分支对应 helper5。[Python 寄存器作用域](https://github.com/hw-native-sys/pypto-lib/blob/4cb74422616359b3f95c598b1f9cdd31cf4e4173/models/deepseek_v4_1_flash/qkv_proj_rope.py#L388) · [6 行分支](https://github.com/hw-native-sys/pypto-lib/blob/4cb74422616359b3f95c598b1f9cdd31cf4e4173/models/deepseek_v4_1_flash/qkv_proj_rope.py#L544)

**定位结论。** A6 标量控制流越过了 helper5 的调用点。去掉它后面的四级 TMOV，保留真实计算、GM 地址、事件与两次 TSTORE，三个直写版本仍未完成。最后可见标记 65 位于事件发布之后，紧邻 `wait_flag` 之前的 66 都未见。现有证据只把范围缩到 **helper5 调用后的输出交接**；向量工作是否完成、是否已执行到 wait，仍缺同轮 AIV 指令或事件状态。

**规避方式。** t12 改用设备 C++ 的内联 `postprocess`，在 `__VEC_SCOPE__` 中用 `RegTensor` 处理输出，绕过旧版的 8 个独立 VF helper。A6 整图通过时一共切换了四个核，尚无单独替换 t12 的整图对照。[t12 C++ 实现](../../kernels/norm_rope_publish.cpp#L32)

| t12 对照 | 板端最后可见位置 |
| --- | --- |
| helper 调用后打点 | 12 个 block 均到 37（helper5 调用后），未到 48（核尾等待前） |
| TMOV 链内打点 | 部分到 60，个别到 62，都未到 63；不同轮次能走过的 TMOV 数量不同 |
| 去掉 TMOV，直接写回 | 三版均无返回和完成标记；最远见事件发布后的 65，未见 66、67 |

基线包与标记包共用同一计划、输入和参考，19 个活跃业务核中仅 t12 不同。按 LLVM 元素宽度核对，helper5 的输入与输出地址均落在调用方分配的 tile 中。A5、A6 离线产物都包含 VF 启动指令；静态核对能排除漏编 helper，仍无法判断超时时向量工作是否完成。[调用方](../../../dsv41_scheduler/kernels/pypto_lib_latest/vendor/aiv/norm_rope_publish.cpp#L1706) · [helper5](../../../dsv41_scheduler/kernels/pypto_lib_latest/vendor/vf/norm_rope_publish/__pypto_vf_norm_rope_publish_5.ll#L86)

<a id="a6-completion-271"></a>

### CntNotify 完成通知适配遗漏（271）

**现象。** L0、L1、L2 都在首轮超时。Host 上 `mapped_tasks=0`，AIC 反汇编显示 `ST_DEV.b32` 执行时地址寄存器为 0、待写值为 1。

**原因与修复。** A6 共用了 A5 的编译期假设“Host 一定映射了 CntNotify”，导致调度器在业务 block 返回时跳过检查。已将该假设限于 A5；A6 根据 Host 发布的映射状态选择 GM 记账。配套组包复测后，271 未再出现。

A6 的 Host 以零偏移表示走 GM，旧设备代码却解引用计数器描述符，最终在 `scheduler_notify_add` 写零地址。修复覆盖普通、MIX 和异步完成三条路径。用同一零偏移输入编译验证，A6 的 C/V 两端均选择 GM，A5 仍选择原通知路径。[Host 映射](../../../../../src/a5/runtime/host_build_graph/host/runtime_maker.cpp) · [设备端选择](../../../../../src/a5/runtime/host_build_graph/runtime/scheduler/scheduler_memory.h) · [完成记账](../../../../../src/a5/runtime/host_build_graph/runtime/scheduler/scheduler_completion.h)

<a id="a6-bootstrap"></a>

### 启动阶段曾超时，重新组包后进入业务执行

**现象。** 修复 271 后的一次运行读到 `workers=108, dispatch_arrived=0, bootstrap_complete=0`，业务核还未放行。重新 prepare 并使用配套 runner 与运行库后越过了 bootstrap。

**定位结论。** 失败和通过两组缺少完整的产物指纹，产物混用仍属推测。另一轮通过启动后报出的 `site=95` 属于执行进度看门狗；它只说明完成计数没有继续变化，须结合同轮任务和 PC 定位。

到达数只统计已上报的完整核组。读到 0 能确认业务核尚未放行，无法区分组内哪一步停住。本地对照的 runner 和回放包逐文件一致，因此也无法从这份对照确定板上加载了哪套运行库。[组内计数](../../../../../src/a5/runtime/host_build_graph/aicore/resident_scheduler_impl.h)

早期 20 分块快照中，t0 的 20 个分块均已领号、完成 0 个，另一独立支路 t18 则完成 3/4。t0 有 AIV 采样落在业务核内，t18 只有任务总数，缺少对应分块的返回记录。因此可以优先定位 t0，尚无依据将 t18 的余数归因于同一处。

<a id="a6-t0-vf-331"></a>

### t0 的 `pl.reg_scope()` 导出核在 A6 触发向量指令配置错误 331

**现象。** 005 首次上板时，AIV 报 `VEC_INSTR_ILLEGAL_CFG_T0`（331），采样 PC 落在 `hc_collapse_norm`。另一次把 t0 减到单个 block，它仍未完成。

**定位结论。** 004 用的是旧 `_hc_collapse`。合并后的 005 换成 `pl.reg_scope()` 导出的 `hc_collapse_norm`，需要单独链接 6 个 VF helper。错误码把范围定在 t0 向量指令配置；现有 PC 无法确定是哪条指令、哪个参数触发。A6 随后改用手写 C++ 实现，保留四路相加、BF16 舍入和 RMSNorm 的数值顺序。

004 的旧核同样包含全流水线屏障。005 随上游更新改变了分块和数值顺序，随后又把归一化改写为 PyPTO 的寄存器作用域。单 block 对照仍未完成，采样落在 helper 调用前的参数准备附近，仅凭这个位置无法判断哪条指令参数非法。换 C++ 的离线包只改变 t0 的机器码，计划、输入和 25 份参考一致。[004 对照](../004-a6-swa-output-tiles/README.zh-CN.md) · [数值与分块变更](https://github.com/hw-native-sys/pypto-lib/pull/1803) · [RegScope 改写](https://github.com/hw-native-sys/pypto-lib/pull/1862) · [C++ 实现](../../kernels/hc_collapse_norm.cpp)

<a id="a6-t0-20-blocks"></a>

### t0 换成 C++ 后仍缺一个分块：为什么从 20 改为 5

**现象。** 20 分块下，同卡各跑一轮的两版 C++ t0，都停在 block2、worker62，状态为 `CLAIMED`。换成五分块后，后续多轮整图都能越过 t0。

**定位结论。** 这两次缺票发生时已绕过 t0 的导出 helper。把归约中间值从 UB 移到寄存器仍未恢复完成；带标记的另一次运行也只能把范围缩到核尾附近。20 分块为何缺票仍未查明。

20 分块对照中只有 t0 机器码不同。带核尾标记的版本最后可见 stage35，后面有 `BAR.ALL`；标记写入与 premix 数据重叠，因而不能断定屏障永久等待。五分块改变了每个 block 处理的行数，也改变了工作分配。现有证据仅支持这一配置能越过 t0，尚未验证共享四核 C++ 组合能否恢复 20 分块。

<a id="a6-qkv-block8"></a>

### qkv block8 曾缺一份完成，重复对照未稳定复现

**现象。** 五分块整图首次越过 t0 后，t4 只完成 53/54；缺的是 worker62 的 block8，`pre/post/complete=63/0/0`。这里的 63 是 worker 编号加一。随后只统计取得该 block 探针的有效轮次，qkv 原版、只加 NOP、只加 `PIPE_V` 屏障分别 **5/5 通过**。三轮原版整图也都越过 t4。

**定位结论。** 初次读数说明调用前标记可见、返回后的标记未见。第五个参数改传常量 16，或在调用前多写一次变量，两版都曾通过；加写版读回的原变量也是 16（`qmark=0x10`）；结合随后原版也能过，无法把故障归因于行数、缺屏障或单颗物理核故障。另有一轮 t10 block20 只看到 AIC 贡献，随后三轮均收齐 64 份，故该次 t10 观察也不足以解释 t12 的稳定超时。

同轮 qkv 的 block0、block1 能完成，worker62 也完成过补零任务的空分片。按计划重算，block8 两个 tile 都有 16 行，与正常分块相同。空分片只覆盖简单返回路径；相同 qkv 原版多次通过的结果，对判断屏障和参数假设更有辨别力。[量化核分块](../../../dsv41_scheduler/kernels/pypto_lib_latest/vendor/aiv/qkv_quant.cpp#L356)

## 为什么 A5 通过，A6 仍优先怀疑 VF 导出路径

005 锁定的 PyPTO 版本在 A5 上生成 VF 调用时，明确依赖“VF 退出时寄存器指令已经完成”的语义。PTOAS 由此给调用方安排搬运事件，并处理 VF 内部依赖。005 的 A6 适配从已经生成的调用方 C++ 和 helper LLVM IR 开始，替换目标 CPU、处理标量移位指令，再用 A6 后端重编。这个过程没有针对 A6 重新规划调用边界上的同步。[A5 CI 的版本](https://github.com/hw-native-sys/pypto-lib/blob/4cb74422616359b3f95c598b1f9cdd31cf4e4173/.github/workflows/ci.yml#L264) · [生成 VF 调用所用的完成语义](https://github.com/hw-native-sys/pypto/blob/3db18db1286bdbb44266179e3aedae659174b711/src/codegen/pto/vf_codegen.cpp#L295-L298) · [A6 构建适配](../../compile_kernels.py#L49)

这解释了优先更换的范围。t0 有硬件报告的指令配置错误，t12 有 helper5 调用后的稳定超时，两者是否同因仍待验证。新的两端共用包把 t0、qkv、t12 和 Sinkhorn 四个核切到设备 C++，其中仍使用向量寄存器。A6 五分块实测验证了这一组合，旧导出核的具体故障指令仍待确定。
