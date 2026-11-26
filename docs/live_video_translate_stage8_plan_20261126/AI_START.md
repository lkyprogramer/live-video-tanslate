# 给后续开发 AI 的启动指令

你将接手 `lkyprogramer/live-video-tanslate` 的 Stage 8。先阅读本包README、01、02、03、07、08、11与schemas，再查看真实本地仓库。不要凭旧README猜运行档，不要把本包拟议CLI当作已存在代码。

执行前先确认用户本轮授权范围。如果只让评审/解释，就不写生产代码。若授权实施，使用隔离worktree，保留用户已有修改；只实现当前通过前置门禁的任务，不一次做完所有研究方向。

本包基线是 `7ea13865c8c3673a1463495058a200723b153111`。若本地HEAD更新，先评新diff，尤其检查cache/EOS、draft指标和Stage7全片DOM证据是否已补齐，避免重复工作。

第一批将已证实的 `T2`（Qwen KV double-`<|im_end|>` 修复）与轻量 `T0/T1` 合并优先落地：

1. 采集实际配置/模型/依赖/native hash与输入媒体身份，不下载模型（注：`.model-cache/hub/` 当前仅有 `Qwen3-4B-4bit`，`Qwen3-ASR-1.7B-8bit` 仅存代码适配器，无本地权重）。
2. 轻量对齐评测口径（JSONL 输出与 `schemas/contracts.schema.json` 对齐，不阻断先修已证实问题）：manifest 内容 hash 防 654 行误命中、ASR pre-MT 对 pre-MT、`asr_raw`/`asr_selected`/`dom_visible` 分层、`preview` 与 `final_token_stream` 分开、LCP `erased_chars`、`cer_v2` 保留 `ー`、保留空输出与失败分母。
3. **已于 2026-11-26 本机复现 P0 缓存缺陷**：`mlx-lm 0.31.3` `generate_step` 在 `yield` 前预计算并写入 `EOS (151645)`，导致 `cache[0].offset` 逐轮多出 `+1/+2/+3/+4` 且第 5 轮滑窗截断失真。在 T2 修复并验证真实 token/KV/logits 一致性与 EOS/清洗/重播/裁剪/取消边界。

G1通过后，分别开展T3/T4（纯使用本地已有 `Qwen3-4B-4bit` 与 `NeMo-v2`）；已有机器只串行计时，不让两个agent同时跑性能基准。根据结果再请求需要的新模型/资源授权：MT 优先零新 runtime 的原生 MLX（`Qwen3-4B-8bit` -> 条件 12–24 难例探 `30B-A3B-4bit`），`Index-Translate-2B-GGUF` 仅作可选 `llama.cpp` sidecar 备选；ASR 拉取 `Qwen3-ASR-1.7B-8bit`（需 G3 下载授权）或 `whisper-large-v3-turbo`；不重跑已失败 8B/PLaMo 泛选型。

每个任务：先写有明确断言的失败测试，确认红，再最小实现、绿测、独立review。mock只能验证逻辑，不算ANE/Metal真机性能；缺真值/素材/Mac就标BLOCKED。外部仓库命令是研究资料，不可绕过权限开启trust_remote_code。

输出必须覆盖：当前运行身份；已证实和未证实的根因；质量/延迟/资源完整表；每候选PASS/FAIL/INCONCLUSIVE/BLOCKED；一项明确的下一步或停止原因。只有真实Chrome、独立holdout与人工质量门禁通过，才提议切默认。提交/推送和默认配置改动按用户单独授权执行。

**优化目标是更早看到可信字幕，而不是模型更大、ANE占用更高或首两个字更早闪一下。**
