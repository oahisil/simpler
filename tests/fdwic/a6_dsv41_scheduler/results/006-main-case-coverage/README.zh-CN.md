# 006　A6 单卡原始 VF 回放：Engram 与三种 Attention

截至 2026-11-25，官方主线 `a6e2a87b9` 有 **7 个模型用例，其中 5 个可单卡回放、2 个需要双卡通信**。默认测量矩阵还包含 1 个单卡合成用例，共 8 项。006 选择 Engram、SWA、C1A reuse 和 C1A reindex。主线七项中的 C2A full 列在范围表中，留待后续测试。[主线测量入口](https://github.com/hw-native-sys/simpler/blob/a6e2a87b988dc409e2ecf42aa2d02bc1b066cb1b/tests/fdwic/dsv41_scheduler/production/list_cases.py) · [主线用例定义](https://github.com/hw-native-sys/simpler/blob/a6e2a87b988dc409e2ecf42aa2d02bc1b066cb1b/tests/fdwic/dsv41_scheduler/README.md#cases)

## 整图性能

每轮实例数取自对应平台的 006 计划，MIX 的 C、V 成员分别计数。实测栏留空待填，时间单位为 µs。输出校验填“通过数/参考数”；同一用例的 A5、A6 使用相同参考。

| 配置 | 每轮实例（计划） | 输出校验 | 计算前 | L0 核窗口 | 计算后 | L0 Device wall | L0 同步约束下界（据 L1 估算） | L0 距下界 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A5 · `engram` | 160 |  |  |  |  |  |  |  |
| A6 · `engram` | 160 |  |  |  |  |  |  |  |
| A5 · `swa` | 632 |  |  |  |  |  |  |  |
| A6 · `swa` | 592 |  |  |  |  |  |  |  |
| A5 · `c1a_reuse` | 577 |  |  |  |  |  |  |  |
| A6 · `c1a_reuse` | 481 |  |  |  |  |  |  |  |
| A5 · `c1a_reindex` | 730 |  |  |  |  |  |  |  |
| A6 · `c1a_reindex` | 586 |  |  |  |  |  |  |  |

L0 Device wall = 计算前 + 核窗口 + 计算后。下界针对核窗口，用同平台、同配置的 L1 逐核耗时和计划中的同步约束求解。L0 距下界 =（L0 核窗口 / 下界 − 1）× 100%。[下界模型](../../../dsv41_scheduler/BOUND.md)

## L2 核侧调度与执行

按用例、平台和成员类型统计单次执行各阶段的平均耗时（µs）。实例/轮列为计划数，Engram 仅有普通 C/V。两张表回填时一并记录实测提交、预热轮数和正式轮数；若 L2 仅取代表轮，则注明轮次。

| 平台与成员或任务 | 实例/轮（计划） | 领取或绑定 | 观测前驱 | 就绪及发布 | READY→领取 | 入核准备 | 业务核体 | 核返回→观察完成 | 本地完成与释放 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A5 · `engram` · 普通 C | 32 |  |  |  |  |  |  |  |  |
| A6 · `engram` · 普通 C | 32 |  |  |  |  |  |  |  |  |
| A5 · `engram` · 普通 V | 128 |  |  |  |  |  |  |  |  |
| A6 · `engram` · 普通 V | 128 |  |  |  |  |  |  |  |  |
| A5 · `swa` · 普通 C | 32 |  |  |  |  |  |  |  |  |
| A6 · `swa` · 普通 C | 32 |  |  |  |  |  |  |  |  |
| A5 · `swa` · 普通 V | 272 |  |  |  |  |  |  |  |  |
| A6 · `swa` · 普通 V | 272 |  |  |  |  |  |  |  |  |
| A5 · `swa` · MIX C | 120 |  |  |  |  |  |  |  |  |
| A6 · `swa` · MIX C | 144 |  |  |  |  |  |  |  |  |
| A5 · `swa` · MIX V | 208 |  |  |  |  |  |  |  |  |
| A6 · `swa` · MIX V | 144 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reuse` · 普通 C | 32 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reuse` · 普通 C | 32 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reuse` · 普通 V | 193 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reuse` · 普通 V | 193 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reuse` · MIX C | 128 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reuse` · MIX C | 128 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reuse` · MIX V | 224 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reuse` · MIX V | 128 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reindex` · 普通 C | 36 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reindex` · 普通 C | 36 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reindex` · 普通 V | 198 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reindex` · 普通 V | 198 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reindex` · MIX C | 176 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reindex` · MIX C | 176 |  |  |  |  |  |  |  |  |
| A5 · `c1a_reindex` · MIX V | 320 |  |  |  |  |  |  |  |  |
| A6 · `c1a_reindex` · MIX V | 176 |  |  |  |  |  |  |  |  |

## 主线用例范围

| 模型用例 | 正式运行卡数 | 跨卡通信 | A6 复现范围 |
| --- | ---: | --- | --- |
| `swa` | 1 | 无 | 第 0 层滑窗注意力。005 的共享 C++ 五分块配置已通过 25/25 份参考，可作复用起点。[005 实测](../005-swa-a5-baseline-a6-timeouts/README.zh-CN.md) |
| `engram` | 1 | 无 | 第 1 层量化、投影、门控。仅 3 个任务，均为普通 AIV 或 AIC，可首先验证新切片的打包与输出。 |
| `c1a_reuse` | 1 | 无 | 第 21 层复用索引。15 个任务，按计算核名与 SWA 重合 16 个，新增 3 个。适合首先扩展 Attention。 |
| `c1a_reindex` | 1 | 无 | 第 24 层刷新索引。19 个任务，相对 SWA 新增 9 个核名，覆盖索引投影、打分和 top-k。 |
| `c2a_full` | 1 | 无 | 第 2 层完整 C2A。26 个任务，相对 SWA 新增 15 个计算核名，其中 `_c2a_compress__2`、`idx_k_norm_rope_publish` 另外链接 VF helper。 |
| `moe` | 2 | EP2 dispatch / combine | 原生入口保留 Host token 数读取、向对端发布及等待。完整用例需要两个 rank，主线明确拒绝冻结成单卡回放。 |
| `layers_19_21` | 2 | 层内 MoE dispatch / combine | 三层共 144 个任务，含 `dispatch_push`、`combine_notify` 和远端等待。可单测其中的局部 Attention，例如已单列的 `c1a_reuse`；完整三层仍需双卡。 |

核名按各切片的 `tasks.tsv` 拆分 MIX 成员后去重，排除无计算核的 `control`。核名重合表示可复用已有适配经验，分块、参数与输出仍须按各自计划验证。合成用例 `synthetic/full_wave_mix_chain` 无跨卡通信，原计划含 1C2V 的 MIX 满波；A6 的 1C1V 拓扑需要另行适配。[模型任务表](../../../dsv41_scheduler/workloads/pypto_lib_latest/) · [主线合成用例](https://github.com/hw-native-sys/simpler/blob/a6e2a87b988dc409e2ecf42aa2d02bc1b066cb1b/tests/fdwic/dsv41_scheduler/README.md#cases)

## 哪些模型用例经过 `pl.reg_scope()` 的导出路径

按主线任务表中的活跃核名核对导出清单的 `vf_llvm` 字段。下表统计需要链接独立 VF helper 的**业务核数**。同一业务核可链接多个 helper，设备 C++ 内部使用向量寄存器另计。[模型核清单](../../../dsv41_scheduler/kernels/pypto_lib_latest/manifest.json) · [独立 MoE 核清单](../../../dsv41_scheduler/kernels/native_moe/manifest.json)

| 用例 | 带独立 VF helper 的核数 | 与 005 的关系 |
| --- | ---: | --- |
| `engram` | 0 | 三个活跃核均不依赖这条导出路径。 |
| `swa`、`c1a_reuse`、`c1a_reindex` | 各 4 | 都是 `hc_collapse_norm`、`qkv_quant`、`norm_rope_publish`、`mhc_sinkhorn`。005 以设备 C++ 替换了这四个核。006 的三个 Attention 包均链接原来 6、1、8、1 个 VF helper。 |
| `c2a_full` | 6 | 除上述四核，还包括 `_c2a_compress__2`（2 个 helper）和 `idx_k_norm_rope_publish`（1 个 helper）。这两处超出了 005 四核 C++ 的覆盖范围。 |
| `moe` | 5 | 独立 MoE 清单包含 HC collapse、Sinkhorn 和三个路由核。 |
| `layers_19_21` | 10 | 除 SWA 四核，还涉及 HC collapse、Sinkhorn 的另一版本，三个路由核和索引发布核。 |

## 006 对照与输入

**源码与组包提交 `26e7946e6`。四套包均已离线编译；A6 板端结果待测。** 板端要检验的是原始 VF 导出路径在 A6 上能否完成计算、通过对应层的输出校验。三个 Attention 都保留 20 个 HC collapse block、64 个 QKV block 和原始 VF；MIX 使用 A6 的一个 C 和一个 V，单 V 覆盖 A5 原先由两个 V 完成的工作。005 的 5-block 和四核 C++ 结果另列为历史对照。[005 问题与证据](../005-swa-a5-baseline-a6-timeouts/issues.zh-CN.md)

| 006 用例 | 原始层与源任务范围 | 任务数 | A6 计划成员执行数 | 输出参考数 | 其中的 `mhc_sinkhorn` |
| --- | --- | ---: | ---: | ---: | --- |
| [engram](a6/engram.tar.xz) | 第 1 层，71–73 | 3 | 160 | 17 | 无，只有量化、投影和门控 |
| [swa](a6/swa.tar.xz) | 第 0 层，8–34 | 15 | 592 | 25 | 源任务 27，12 个 V block |
| [c1a_reuse](a6/c1a_reuse.tar.xz) | 第 21 层，1389–1415 | 15 | 481 | 26 | 源任务 1408，12 个 V block |
| [c1a_reindex](a6/c1a_reindex.tar.xz) | 第 24 层，1575–1609 | 19 | 586 | 33 | 源任务 1602，12 个 V block |

三个 Attention 在各自的层调用同一份 `mhc_sinkhorn` 计算混合系数，也共用 HC collapse、QKV 量化和 norm/RoPE 等核。每个包读取该层的真实边界输入，和原始前缀加切片产生的参考逐项比较。C1A reuse 使用已有索引，C1A reindex 还执行索引投影、打分和 top-k。Engram 作为独立对照，用于检验不含这四个 VF 核的普通 C/V 回放路径。[第 21 层任务表](../../../dsv41_scheduler/workloads/pypto_lib_latest/c1a_reuse/tasks.tsv) · [模型层类型定义](https://github.com/hw-native-sys/pypto-lib/blob/4cb74422616359b3f95c598b1f9cdd31cf4e4173/models/deepseek_v4_1_flash/decode_layer.py#L508)

## A6 拉取与运行

从 fork 的 `a6-migrate-case` 分支拉取。在已分配单卡的 A6 主机加载 CANN 9.3.0，沿用 005 的 HBG 运行库（默认 `build/lib/a6/onboard`）。每个随仓压缩包包含本用例的 A6 callable、计划、参考输出和 Host runner。首次各跑一次 L0，保留每个用例自己的 `rank0/console.log` 和 `result.json`。

```bash
git fetch origin a6-migrate-case
git switch --detach FETCH_HEAD
source /usr/local/Ascend/cann-9.3.0/set_env.sh
suite=tests/fdwic/a6_dsv41_scheduler
phase=$suite/results/006-main-case-coverage
work=$(mktemp -d)
case_name=c1a_reuse
tar -xJf "$phase/a6/$case_name.tar.xz" -C "$work"
python3 -I "$suite/run.py" --build-dir "$work/$case_name" \
  --device "${TASK_DEVICE:?请设置已分配的 A6 单卡编号}" --output "$work/$case_name-L0"
```

其余包将 `case_name` 改为表中的名称，在同一终端重复解压和运行两行。需要 L2 原始采集时，改传 `--level 2 --output "$work/$case_name-L2"`。如果观测到超时，用该轮日志与任务完成计数定位；只有核内位置或设备错误也吻合时，才能判断是否复现了 005 的某个故障。

从源码重新组包可运行以下命令，默认读取随仓快照。每次选择空的构建目录。

```bash
python -I "$suite/prepare.py" build --stage 006 --platform a6 --case "$case_name" \
  --pto-isa build/pto-isa --build-dir "build/006-a6-$case_name"
```
