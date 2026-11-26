# 单卡 SWA 性能优化阶段索引

后续优化以迁移完成时 A6 的 **370.307 µs** 为起点。主指标取生产 HBG 的 L0 首个计算核开始到末个计算核结束，单卡预热 1 轮、正式 10 轮取均值。A5 提供跨代对照。[复测方法](a6-environment.zh-CN.md) · [迁移记录](a6-migration.zh-CN.md)

| 阶段 | 改动与对照 | A6 L0 窗口 | A6 Device wall（L0） | 报告与数据 |
| ---- | ---------- | ---------: | -------------------: | ---------- |
| [00 优化前基线](../results/00-baseline/) | A6 已完成 1C1V 迁移，A5 保留 1C2V。A5 对照窗口为 269.286 µs | 370.307 µs | 463.159 µs | [阶段报告](../results/00-baseline/README.zh-CN.md) · [指标 JSON](../results/00-baseline/metrics.json) · [A5 L3 泳道](../results/00-baseline/swa-a5-hbg-L3-detail.json.xz) |
| [001 合入主线 HBG](../results/001-hbg-main-merge-swa/) | 同一张 A5 上，纯主线 219.287 µs，合并版 218.050 µs。A6 使用 GM 完成计数，待单卡回填 | | | [阶段报告](../results/001-hbg-main-merge-swa/README.zh-CN.md) · [指标 JSON](../results/001-hbg-main-merge-swa/metrics.json) · [A5 L3 泳道](../results/001-hbg-main-merge-swa/swa-a5-hbg-L3-detail.json.xz) |
| [002 同址访问](../results/002-hbg-same-address-access/README.zh-CN.md) | A6 的 96 核 GM 64 位加一全部完成均值 1.051 µs；CntNotify ADD 直映射返回 507899。SWA 使用 GM-FAA | 328.139 µs | 414.771 µs | [A6 访问结果](../results/002-hbg-same-address-access/README.zh-CN.md#a6-同址读写) · [A5 逐名次数据](../results/002-hbg-same-address-access/a5-ranks.json) |
| [003 A6 输出量化与投影分片](../results/003-a6-swa-blocks/README.zh-CN.md) | t17、t20 均用 54 组。较 002 核窗口缩短 9.079 µs；t20 的 L3 跨度相对 00 增长 | 319.060 µs | 406.690 µs | [实测与回退分析](../results/003-a6-swa-blocks/README.zh-CN.md) |
| [004 A6 输出尾部分片](../results/004-a6-swa-output-tiles/README.zh-CN.md) | HC post 与 O-B 均用 54 组。同批 L0 核窗口缩短 26.502 µs，Device wall 缩短 26.295 µs | 290.068 µs | 377.707 µs | [分项结果与下界](../results/004-a6-swa-output-tiles/README.zh-CN.md#同卡对照) · [指标 JSON](../results/004-a6-swa-output-tiles/metrics.json) |
| [005 SWA 性能对照](../results/005-swa-a5-baseline-a6-timeouts/README.zh-CN.md) | A5 迁移版已采集同卡 L0、L1 下界与 L2 泳道；A6 五分块旧核稳定停在 t12，四核 C++ 组合待实测 | | | [性能对照](../results/005-swa-a5-baseline-a6-timeouts/README.zh-CN.md) · [问题定位](../results/005-swa-a5-baseline-a6-timeouts/issues.zh-CN.md) · [指标 JSON](../results/005-swa-a5-baseline-a6-timeouts/metrics.json) |

006 保留原始 VF 和 Attention 的 20 个 HC collapse 分块，归档 Engram、SWA、C1A reuse、C1A reindex 四个 A6 板端包。正确性及超时现象待板端验证。[006 板端运行与用例范围](../results/006-main-case-coverage/README.zh-CN.md)

## 追加一个阶段

在 `results/` 下按顺序新建目录，例如 `006-<改动名>/`。该目录的 `README.zh-CN.md` 记录改了什么、对照哪一阶段、实测差值和输出校验。`metrics.json` 保存同口径的汇总值。完成实测后在上表追加一行，链接本阶段报告和指标 JSON。各阶段保留各自的提交号、采集等级与轮次，方便复核收益。

每轮实测结束后，将通过输出校验且有性能收益的分片并入默认配置，删除已完成对照的临时开关。历史方案通过该阶段的提交和指标复核。
