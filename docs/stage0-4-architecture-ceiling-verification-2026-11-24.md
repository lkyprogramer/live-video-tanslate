# Stage 0–4 架构上限优化与全量实测验收报告（2026-11-24）

> **结论**：在 Apple M1 Pro（10 核 CPU / 16 核 GPU `applegpu_g13s` 200 GB/s / 32 GiB RAM / macOS 26.7.2）上完成了 [M1 Pro 架构上限优化路线图](m1-pro-architecture-ceiling-roadmap-2026-11-23.md) 的 **Stage 0–4 全部改造与硬件实测**。生产主力档 `m1-pro-nemo` 通过 **MTL0 GPU 交错调度 + 64ms 收音帧 + 350ms 能量谷短切分 + 2 轮滚动 KV Cache + 2154-token 假名硬掩码 + 1.2s Draft-and-Commit**，将 D1 高语速日剧的 `final_e2e P50` 压至 **0.604s（P95 1.008s）**，`first_delta_e2e P50` 压至 **0.508s（P95 0.883s）**，说话起音至首字预览中位延迟 **下降 54.4%（4.873s → 2.224s）**，且保持 **100%（121/121）终版字幕精确一致**。
>
> **未在本轮重跑的项**：Chrome 1080p/4K MP4 实际播放端到端联调（因上一轮清理已删除本地测试 MP4 文件，本轮仅完成 1× 实速 WebSocket 回放与前端单测/构建，Chrome 4K 长时间联调顺延至 **Stage 5C**）。

---

## 1. 各阶段核心改动与硬件验收数据

### 1.1 Stage 0：环境基线、依赖锁定与模型校验
- **依赖锁定**：[engine/pyproject.toml](../engine/pyproject.toml) 新增 `override-dependencies = ["mlx==0.32.4"]` 并重建 [engine/uv.lock](../engine/uv.lock)，`uv lock --check --directory engine` 通过（93 packages）。
- **模型防篡改**：[scripts/setup_speech_cpp_nemo.sh](../scripts/setup_speech_cpp_nemo.sh) 增加 `EXPECTED_MODEL_SHA`（`944411cb740938f858c939aa1d42f7eb4bb884fc350d9daae8b7e724853e54a3`）校验并打印 dylib SHA256。
- **验收证据**：[stage0_1_verification.json](experiments/stage0-1-2026-11-23/stage0_1_verification.json)。

### 1.2 Stage 1：正确性底座（Ring Buffer + `pad_ms=350` + 假名硬掩码）
- **分段与去重**：[local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py) `_SmartSegmenter` 改用绝对采样索引环形缓冲（零丢帧、`cut_reason` 标注、边界钳位）；[backend.py](../engine/src/subtitle_service/backend.py) `SpeechCppNeMoRecognizer` 启用 `pad_ms=350`、`timestamps=8` 重叠区 token 采样去重（`_filter_tokens_by_sample`）及严格 C API 返回值校验。
- **零假名泄漏与 KV 不变量**：`Qwen3Translator` 对 2154 个假名 token 实施 `-1e9` logits 硬掩码，并修复可取消 `generate_step` 路径。
- **91 条冻结切片实测**：
  - `SpeechCppNeMoRecognizer`（6T beam, `pad_ms=350`）：`p50 = 256.1ms / p95 = 494.4ms`，**D0 CER `0.1531`（15.31%）**（优于 PyTorch FP32 `15.46%`），**D1 CER `0.2934`（29.34%）**。
  - `Qwen3Translator`：假名泄漏 **`0/91`**，连续两轮确定性一致 **`91/91`**，乱序 **`20/20`**，正常/取消/异常全路径 `offset == prefix_len` **100%** 成立。
  - 1× 实速 WebSocket：`csj-reproduction` `13` 条字幕 / `0` 警告 / `drain 0.373s` / `final_e2e P50 0.903s`；`d1-ep01-45s` `13` 条字幕 / `0` 警告 / `drain 0.373s` / `P50 1.088s`。

### 1.3 Stage 2：64ms 收音 + `caption_delta` 流式译文 + 滚动 KV Cache
- **端到端链路**：[extension/src/offscreen.ts](../extension/src/offscreen.ts) 与 [protocol.ts](../extension/src/protocol.ts) 将收音帧从 `200ms` 降至 `64ms`（`SAMPLES_PER_FRAME=1024`, `2048B`）；[protocol/v1.schema.json](../protocol/v1.schema.json) 与 [content.ts](../extension/src/content.ts) 支持 `caption_delta` 流式预览、原地定稿与 `4500ms` 孤儿超时保护及真双行 DOM 诊断。
- **引擎侧**：[app.py](../engine/src/subtitle_service/app.py) `QUEUE_MAX_BYTES=128000`；`vad_pause_ms=350`；`[n-0.65s, n-0.08s]` 能量谷；`_drain_deltas()` 保序流式输出；`_Qwen3Session` 维护 `context_turns=2` 滚动 KV Cache（额外上下文 `<=180` tokens、静音间隔 `<=5.0s`、关闭/取消/异常严格重置，实测 D1 消解了 `"保育员"` 主语指代并将 `"ほうであります。"` 稳定译为 `"没错，就是这样。"`）。
- **1× 实速 WebSocket（CPU 6T 基线）**：

| 数据集 | `first_delta_e2e` (P50 / P95) | `final_e2e` (P50 / P95) | 特殊检查 |
| :--- | :---: | :---: | :--- |
| `d1-ep01-45s` | `0.694s / 1.042s` | `0.784s / 1.168s` | 2 轮 KV 指代消解通过 |
| `csj-reproduction` | `0.753s / 1.152s` | `0.906s / -` | `seek_50ms`: `14` 条字幕 / `0` 警告 |
| `csj-task` | `0.662s / 0.874s` | `-` | — |
| `wmf-04` | `0.708s / 1.030s` | `-` | — |

- **止损门实测**（[stage2_verification.json](experiments/stage2-2026-11-23/stage2_verification.json)）：
  - `speech.cpp` 6T `greedy 207.4ms` vs `beam 247.4ms`（差值仅 `+40.0ms`），**拒绝 fork** 定制 greedy 内核。
  - `MLX PR #4787` `[3,16]` 区间命中率在 2 轮滚动 KV 下为 **`0.0%`（0/91）**（`36` 条在 `[17,32]`，`55` 条 `>32`），**跳过 fork**。

### 1.4 Stage 3：MTL0 交错单 GPU 调度与量化/小模型选型
- **核心改动**：[config.py](../engine/src/subtitle_service/config.py) 与 [backend.py](../engine/src/subtitle_service/backend.py) 新增 `AsrConfig.device`（`"cpu" | "gpu"`）与交错单 GPU 调度器（`MT > ASR`，`MTL0` 上配置 `decode_on_cpu=1`），`m1-pro-nemo` 默认启用 `device = "gpu"`。
- **GPU vs CPU 6T 对比**（[stage3_verification.json](experiments/stage3-2026-11-24/stage3_verification.json)）：
  - `d1-ep01-45s`：`first_delta_e2e` **`0.717s → 0.508s`**（P95 **`1.361s → 0.883s`**）；`final_e2e` **`0.825s → 0.604s`**（P95 **`1.433s → 1.008s`**）。
  - `csj-reproduction`：`first_delta_e2e P95` **`1.229s → 0.853s`**；`final_e2e P95` **`1.429s → 1.019s`**。
  - 资源占用：峰值 CPU **`351.6% → 144.1%`（-59%）**；峰值 RSS **`1805.1 MiB → 1117.9 MiB`（-687 MiB）**。
  - 100 句交错并发：MT 延迟膨胀 **`-3.57%`**（`330.99ms → 319.17ms`，零争用膨胀）。
- **量化与小模型止损**：
  - `NeMo-v2` 量化扫描（`pad=350ms`）：保留 **`Q8_0`**（`660.9 MB` / GPU `137.8ms` / D0 `15.23%` / D1 `29.34%`），淘汰 `Q5_K` 与 `Q4_K`。
  - MT 候选模型：`HY-MT1.5-1.8B-4bit` 虽快 2×（`160.8ms`），但 D1 出现语态反转（`キレられた` 误译为 `"我生气了"`）且漏翻 `"欢迎回来"`；`Qwen3.5-2B-4bit` 存在 `ArraysCache` `.offset` 兼容障碍，均不替换 `Qwen3-4B-Instruct-2507-4bit`。

### 1.5 Stage 4：SOV 句法门控 + 1.2s 可抢占 Draft-and-Commit
- **核心改动**：[local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py) 新增 `AsrConfig.draft_interval_s = 1.2`、非破坏性滑窗快照 `_SmartSegmenter.push_and_preview()` 及 SOV 从句/助词门控 `_should_emit_draft_ja()`；`SpeechCppNeMoRecognizer.decode()` 支持 `decoding_override="greedy", pad_ms_override=150`；`Qwen3Translator.translate_draft()` 实现只读不提交的 KV 窥视；三级可抢占调度（`Final MT > Final ASR > Draft`）。
- **Draft ON (`1.2s`) vs OFF (`0.0s`) A/B 实测**（[stage4_verification.json](experiments/stage4-2026-11-24/stage4_verification.json)）：
  - **终版字幕 100% 零漂移**：`121/121` 条终版字幕精确一致（`csj-reproduction 14/14`、`csj-task 15/15`、`wmf-04 79/79`、`d1-ep01-45s 13/13`）。
  - **起音至首字预览延迟（`onset -> first delta P50`）**：

| 数据集 | Draft OFF (`0.0s`) | Draft ON (`1.2s`) | 降幅 |
| :--- | :---: | :---: | :---: |
| `d1-ep01-45s` | `4.873s` | **`2.224s`** | **-54.4%** |
| `csj-reproduction` | `3.510s` | **`2.014s`** | **-42.6%** |
| `csj-task` | `2.160s` | **`1.471s`** | **-31.9%** |
| `wmf-04` | `2.977s` | **`1.766s`** | **-40.7%** |

  - `50ms` seek 压力：`14` 条字幕、`0` 警告、`drain 0.293s`。

---

## 2. 本轮回归验证清单

| 检查项 | 命令 | 结果 |
| :--- | :--- | :--- |
| 依赖锁校验 | `uv lock --check --directory engine` | 通过（93 packages） |
| 后端单元测试 | `PYTHONPATH=.:engine/src engine/.venv/bin/pytest engine/tests p0/p2/tests p0/local_streaming` | **90 passed** |
| 扩展类型检查 / 单测 / 构建 | `npm --prefix extension run typecheck && npm --prefix extension test && npm --prefix extension run build` | `tsc` 0 错误，**5/5 passed**，构建成功 |
| Stage 0–4 硬件探针 | `verify_stage0_1.py` / `verify_stage2.py` / `verify_stage3.py` / `verify_stage4.py` | 全部执行并归档至 [experiments/](experiments) |
| Chrome 1080p/4K MP4 播放 | — | **未重跑**（缺本地测试 MP4，纳入 Stage 5C 30–60 分钟热稳定性长跑） |
