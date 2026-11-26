# Stage 8：日语实时字幕质量与延迟联合探索包

版本：2026-11-26 · 审查基线：`7ea13865c8c3673a1463495058a200723b153111`（北京时间 09:50:39）

项目：[lkyprogramer/live-video-tanslate](https://github.com/lkyprogramer/live-video-tanslate)。目标机器已于 2026-11-26 通过本机 `sysctl`/`system_profiler`/`df` 核实：`Apple M1 Pro`、10 CPU、16 GPU（Metal 4）、32 GiB RAM（`34,359,738,368` B）、磁盘可用余量 `82 GiB`；每次运行前仍随 manifest 重采，不能从另一台 Mac 的跑分替代。

**结论：保留 Stage 7 的 ANE 主链；不能据现有证据宣布硬件或模型天花板。下一轮应先校正评测、核查真实 KV 状态、处理切分与语义提交，再按门限引入更高精度模型。不要直接按“30B 的智能、4B 的延迟”重构主链。**

这是完整设计与执行文档，不是已实现的 Stage 8。本文档内所有 Stage 8 文件、CLI、配置项、测试和阈值均是拟议项，除非明确标为“仓库已有”。本轮未修改项目、未下载模型、未在用户 Mac 跑测试、未提交或推送。

## 阅读顺序

| 文件 | 用途 |
| --- | --- |
| [stage8_review_20261126.md](stage8_review_20261126.md) | 决策者先读：审查结论、Gemini 哪些应保留/改正、下一步顺序 |
| [01_review_and_decisions.md](01_review_and_decisions.md) | 固定提交审计、指标口径、已经修复的事项、源码风险 |
| [02_design_spec.md](02_design_spec.md) | 设计合同、范围、接口、资源与数据边界 |
| [03_measurement_contract.md](03_measurement_contract.md) | 如何测“第一次读懂”“撤销”“听错”“翻反”，避免假提升 |
| [04_mt_experiments.md](04_mt_experiments.md) | 翻译模型/精度/缓存/上下文实验和历史失败路线 |
| [05_asr_experiments.md](05_asr_experiments.md) | 声学窗口、分词边界、同模型归因、选择性高精识别 |
| [06_runtime_and_low_level.md](06_runtime_and_low_level.md) | 队列、内存、CPU/ANE/Metal 热点与底层优化触发条件 |
| [07_implementation_plan.md](07_implementation_plan.md) | AI 可逐任务实施的文件、接口、测试、依赖与产物 |
| [08_acceptance_and_rollback.md](08_acceptance_and_rollback.md) | 选型/发布门禁、停止条件、回滚和人工复核 |
| [09_sources_and_history.md](09_sources_and_history.md) | 固定提交和来源台账、旧路线重开条件、证据可信度 |
| [10_commands_and_handoff.md](10_commands_and_handoff.md) | 已有命令与待实现 CLI 分开；运行/交接顺序 |
| [11_machine_contracts.md](11_machine_contracts.md) | 三基线、缺失计分、提交/截止时间、版本化数据与聚合合同（有冲突时以此补严） |
| [AI_START.md](AI_START.md) | 可复制给后续开发 AI 的启动指令 |
| [templates/run_manifest.example.json](templates/run_manifest.example.json) | 运行清单示例；占位内容不允许作为有效实验输入 |
| [templates/thresholds.proposed.json](templates/thresholds.proposed.json) | 统一拟议门限；运行前锁定，不可事后调分 |
| [templates/experiment_report.md](templates/experiment_report.md) | 单实验报告与证据要求 |
| [templates/decision_record.md](templates/decision_record.md) | 接受/拒绝/证据不足/阻塞的决定记录 |
| [templates/semantic_cases.json](templates/semantic_cases.json) | 自建语义单元测试，不是独立测试集 |

## 执行顺序与边界

1. **G0：冻结事实和口径。** 本机硬件/实际 profile/native 二进制/模型/媒体哈希；重采同层 ASR 与 DOM；修复指标定义。
2. **G1：推理正确性（已复现 P0）。** 2026-11-26 已在本机 `Qwen3-4B-Instruct-2507-4bit` + `mlx-lm 0.31.3` 实测复现 `generate_step` look-ahead 导致每轮向 KV Cache 多写 1 个 `EOS (151645)`（Turns 1–4 `offset` 偏差 `+1/+2/+3/+4`，第 5 轮滑窗截断失真）；列为确认 P0 修复项，修通 EOS/取消/历史裁剪/清洗/重播一致性后再做模型选型。
3. **G2：廉价消融（零下载）。** 仅使用本机 `.model-cache` 实际已有权重（`Qwen3-4B-4bit` 与 `NeMo-v2` ANE/GPU），做 prompt × context × 约束器单因素及同段边界 × 左上下文对照，不下载新模型。
4. **G3：有限挑战者。** MT 优先零新 runtime 的原生 MLX 路线（`Qwen3-4B-8bit` 量化归因 -> 条件上探 `Qwen3-30B-A3B-4bit` 12–24 难例首筛，仍不预设延迟或胜出）；`Index-Translate-2B-GGUF` 降为可选独立 sidecar 备选（仅在 MLX 候选不达标且愿引入锁定 `llama.cpp` sidecar 时开启）。ASR 说明 `Qwen3-ASR-1.7B-8bit` 仅有代码适配、本地权重已清理，需在 G3 获下载授权后拉取，与 `whisper-large-v3-turbo` 同口径评测。
5. **G4：受控合流。** 胜出且有互补收益才做选择性 ASR、稳定前缀和共享 GPU 调度；不直接并联所有模型。
6. **G5：真实体验。** 个人单人项目默认采用 1 位人类审阅者盲评+二次复核、至少 1 部未见留出集（`>=10` 场景 / `>=15` 分钟）+ 胜出唯一最终候选 45m 全长与关字幕对照（同时保留双人仲裁 / 双剧 20 场景作为 `strict` 级可选标准）；依据门禁决定保留轻量档、增加质量档，或停止。

“文档允许做什么”不等于用户已授权做什么。此包可直接供 AI 规划实施，但新模型下载、安装/执行外部代码、功耗特权工具、生产默认档切换、提交/推送，都须在后续实际执行任务中取得相应授权。不得修改用户已有脏工作区。

## 数据与发布纪律

- EP01 已多轮用于调参，属于开发/回归集，不能改名为盲测集。
- 片源、完整 SRT、实际个人目录、令牌和完整转写默认不进入公共仓库；发布哈希、脚本、聚合结果和经许可的最小错例。
- 缺媒体、原始日志、人类审阅或目标 Mac 时填 `BLOCKED` / `INCONCLUSIVE`，不猜测，不把 CPU 模拟或 mock 测试写成硬件性能。
- 文件 `PACKAGE_CHECK.json` 是文档包自身结构检查，不是 Stage 8 算法或性能验收。
- `schemas/` 是拟议Stage8数据格式的JSON Schema；可以用来实现验证器，不意味着仓库已经支持这些接口。示例manifest因占位/许可未满足必须BLOCKED。
