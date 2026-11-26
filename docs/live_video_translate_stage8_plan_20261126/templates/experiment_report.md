# 实验报告模板

## 身份与事前假设

- experiment_id / run_ids / manifest hashes：
- 当前repo SHA、patch/native/model/runtime hashes：
- 基线/候选、唯一变化变量、旧失败路线重开理由：
- 数据split与场景数、是否曾用于调参：
- 事前门限文件与hash：
- 授权范围、实际下载/代码执行/系统权限动作：

## 可复现输入

- PCM/参考/语义单元hash、音轨/downmix/采样：
- prompt/template/tokenizer/stop/max_tokens/precision：
- history来源、cache命中/重播策略、同epoch约束：
- cold/warm、ABBA顺序、重复轮次、并发负载：

## 完整结果

| 指标/层 | B0 | 候选 | 配对差值及95%区间 | N/缺失/失败 | 是否同口径 |
| --- | --- | --- | --- | --- | --- |
| pre-MT ASR CER v2 / S D I | | | | | |
| final-display语义遗漏 | | | | | |
| E3/E4关键语义错误 | | | | | |
| first useful DOM P50/P95 | | | | | |
| unit end→final DOM P50/P95 | | | | | |
| preview erasure / semantic flips | | | | | |
| MT prefill/replay/decode/queue | | | | | |
| ASR frontend/predict/copy/decode | | | | | |
| 全机压力/swap/进程/MLX | | | | | |
| Chrome掉帧/时钟误差 | | | | | |

另外附legacy指标和标准chrF完整signature；二者不混列。

## 质量复核

- 盲审方式、human_verified、审阅人数、分歧仲裁：
- 保护集新增退化、改善的关键错例、仍错的类别：
- 标签歧义/不可评样本、排除依据及是否事前规定：
- 是否存在参考文本/未来音频/holdout泄漏：

## 决定与下一步

- 状态：PASS / FAIL / INCONCLUSIVE / BLOCKED。
- 通过哪一级门禁，不等于通过哪些门禁：
- 是否只能opt-in质量档：
- 回滚是否验证：
- 下一步唯一最有价值任务，或停止理由：

## 证据清单

原始事件、DOM、时钟、资源、逐条输出/判定、错误日志、代码diff、测试命令/退出码及文件hash。公共仓库只含允许公开的脱敏部分；私有证据保留可定位索引。
