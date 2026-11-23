# Step 2 + Step 3 全栈优化落地与真机端到端验收报告

- **验证日期**：2026-11-23
- **硬件与环境**：Apple M1 Pro（10 核 CPU / 16 核 GPU / 32 GiB 统一内存 / macOS 26.7.2），`engine/.venv`（`mlx==0.32.4`、`mlx-audio==0.6.7`、`mlx-lm==0.31.3`、`sherpa-onnx==2.0.1`）
- **验证状态**：**PASS（83 项 Python 单元测试 + 5 项扩展单元测试 + TypeScript/构建 + 1× 实速双档对比 + 50ms 激进抢占恢复 + Chrome 1080p 跳跃/播中跳转 & 4K 2160p 真机双行 DOM 可见联调全部通过）**

---

## 1. 全栈改造内容（Layer 0–4）

1. **Layer 0（可观测与零抖动日志）**：
   - [app.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/app.py#L60-L87)：`Connection.record()` 改为线程锁保护的长连接缓冲句柄，仅在关键阶段 flush、并在会话退出时关闭，消除每 200ms 同步开关文件对事件循环的抖动。
   - [verify_chrome_1080p_4k.mjs](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/verify_chrome_1080p_4k.mjs#L140-L165)：修复原脚本在 Shadow DOM 中误查 `div` 的缺陷，改为读取 `span[data-current]` 与 `span[data-prev]`，并接入 `SUBTITLE_DIAGNOSTICS` 事件归档。
2. **Layer 1（异步解耦 + 15ms 级协作式取消 + 优先调度）**：
   - [local_pipeline.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/local_pipeline.py#L369-L567)：`accept_pcm()` 仅执行 CPU VAD / 分段后即刻返回；`m1-pro-hq` 在单 MLX 线程执行器上运行 **`MT > ASR` 优先调度器**；`m1-pro-nemo` 实现 **CPU 8 线程 ASR 与 GPU MLX MT 真并行异构调度**；ASR 与 MT 均下沉至逐 token `generate_step`（`prefill_step_size=512`）与 `speech_request_set_progress` 回调检查 `self.cancelled`，`aclose()` 立即置位中止废旧推理。
3. **Layer 2（极速纯 MLX 内核与异构 `speech.cpp` 后端）**：
   - **`Qwen3AsrRecognizer` (`A3`)**：预缓存前后缀 embedding、O(1) `mx.concatenate` 合并、去除重复预处理与中间 `mx.clear_cache()`、`mx.set_cache_limit(1GiB)`。
   - **`Qwen3Translator` (`M1`)**：预计算固定 `SYSTEM_PROMPT` KV Cache（`make_prompt_cache`）并在每次翻译后 `trim_prompt_cache` 精准归位，保持单一异常/取消恢复不变量。
   - **`SpeechCppNeMoRecognizer` (`m1-pro-nemo`)**：通过 `ctypes` 绑定驻留 `.model-cache/speech-cpp/libspeech.dylib` + `reazonspeech-nemo-619M-v2-Q8_0.gguf`（CPU 8 线程 beam 解码 + 静音幻觉过滤 + 退出 `speech_model_free` 清理）。
4. **Layer 3（能量谷短切分 + 重叠去重 + 双行自适应字幕 UI）**：
   - **`_SmartSegmenter` + `_dedup_boundary_ja`**：`max_segment_s = 4.5s` 时回看最近 `1.2s` 寻找 `160ms` 局部最低 RMS 能量谷强切，保留 `350ms` 左侧声学重叠并对日语边界做 2–12 字去重。
   - **[content.ts](file:///Users/luo/Documents/github/jp-live-video-tanslate/extension/src/content.ts#L77-L125) & [offscreen.ts](file:///Users/luo/Documents/github/jp-live-video-tanslate/extension/src/offscreen.ts#L104-L148)**：升级为 `<span data-prev>` + `<span data-current>` **双行滚动字幕**，停留时长按字符数自适应 `clamp(2.5s, chars × 170ms, 6.5s)`；`busy` 重试升级为 `[150, 350, 700]ms` 三段退避。
   - **单元测试**：[test_m1_pro_hq.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/tests/test_m1_pro_hq.py) 新增分段器、边界去重、`MT > ASR` 调度、取消标志与 `speech_cpp_nemo` 配置校验测试，全仓 **83 项 Python 单测（`4.66s`）+ 5 项扩展单测 + TypeScript 检查全部通过**。

---

## 2. 1× 实速 WebSocket 端到端对比（改造前 vs 改造后双档）

数据来源：[live_comparison.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/step2-3-e2e-2026-11-23/live_comparison.json)，对比基线 [e06ceea 验证报告](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/m1-pro-hq-verification-2026-11-23.md)。

| 评测项 | `m1-pro-hq` 改造前（`e06ceea`） | **`m1-pro-hq` v2（全 MLX 改造后）** | **`m1-pro-nemo`（CPU NeMo + GPU Qwen3-4B 异构档）** |
| :--- | :--- | :--- | :--- |
| **`csj-reproduction`** | `6` 条 / `0` 警告 / P50 `1.88s` / P95 `3.76s` | `12` 条 / `0` 警告 / P50 `1.21s` / P95 `1.86s` | `11` 条 / `1` 警告 / P50 **`0.90s`** / P95 **`1.64s`** |
| **`csj-task`** | `11` 条 / `0` 警告 / P50 `2.44s` / P95 `4.19s` | `14` 条 / `0` 警告 / P50 `0.98s` / P95 `1.45s` | `14` 条 / `0` 警告 / P50 **`0.79s`** / P95 **`1.32s`** |
| **`wmf-04`** | `45` 条 / `0` 警告 / P50 `1.66s` / P95 `3.13s` | `69` 条 / `0` 警告 / P50 `1.05s` / P95 `1.76s` | `69` 条 / `0` 警告 / P50 **`0.89s`** / P95 **`1.56s`** |
| **`d1-ep01-45s`（《重启人生》）** | `3` 大段 / `0` 警告 / P50 `3.45s` / P95 `4.19s` / `drain 4.18s` | **`13` 条** / `0` 警告 / P50 `1.21s` / P95 `1.58s` / `drain 0.71s` | **`13` 条** / `0` 警告 / P50 **`1.14s`** / P95 **`1.38s`** / `drain 0.43s` |
| **`silence`（10s 静音）** | `0` 条 / `0` 警告 | `0` 条 / `0` 警告 | `0` 条 / `0` 警告 |
| **激进断连重连** | 200ms 间隔重连通过 | **50ms 间隔**重连 `12` 条 / `0` `busy` / `0` 警告 | **50ms 间隔**重连 `11` 条 / `0` `busy` / `1` 警告 |
| **默认档 `reazon-hy`** | `6` 条 / `0` 警告 / `drain 0.26s` | `6` 条 / `0` 警告 / `drain 0.28s`（无回归） | 同左（无回归） |

- **语义质量亮点（D1《重启人生》45s 剧集对白）**：
  - `m1-pro-nemo` 在 4.5s 短切分下仍稳定抓出关键台词：`"我以前也是会说“你好”的。"`、`"被骂了一次真是难受啊。"`、`"这么说起来，保育员啊。"`、`"那我们也要说“欢迎回来”吧。"`、`"“辛苦了”这种说法真的挺奇怪的。"`。

---

## 3. Chrome 真机 1080p（`04:35` + 播中 `12:05` 跳转）& 4K 2160p 端到端联调

实测档位：`m1-pro-nemo`（诊断版扩展），数据归档：[chrome_e2e_summary.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/step2-3-e2e-2026-11-23/chrome_e2e_summary.json)，真机双行字幕截图：[1080p-dual-caption.jpg](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/step2-3-e2e-2026-11-23/1080p-dual-caption.jpg)、[4k-dual-caption.jpg](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/step2-3-e2e-2026-11-23/4k-dual-caption.jpg)。

| 场景 | 视频规格与操作 | 掉帧 | 真实 DOM 捕获字幕数 | 网关拦截 | 并发资源占用 |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **1080p 核心对白 + 播中跳转** | `1920×1080` 跳 `04:35.0` 播 `46s` → 播中跳 `12:05.0` 播 `24s` | `67 / 2178`（含两次 Seek） | 跳前 **`12` 条** + 跳后 **`4` 条**（共 `16` 条） | `0` | `subtitle-server` 峰值 RSS **`3942 MiB`**（平均 CPU `18.2%`） |
| **4K 2160p H.264 并发播放** | `3840×2160` 播放 `35s` 至 `ended` | **`0 / 1049`（0% 掉帧）** | **`11` 条**（双行滚动 `prev_zh` + `text_zh` 均有记录） | `0` | Chrome 进程树峰值 RSS **`6167.8 MiB`**（平均 CPU `47.5%`） |

- **DOM 诊断链实证**：`model.jsonl` 记录 **`27` 条 `ws_sent`、`0` 条 `gateway_filtered`**；Chrome 控制台实时捕获到 **`54` 条 `subtitle-diagnostic` 事件（`dom_visible=27`、`dom_removed=27`）**，已实现从页面视频音频采集到 Shadow DOM 双行字幕可见的全链路闭合验收。
- **收尾工程调优复测**（[nemo_fixes_verification.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/step2-3-e2e-2026-11-23/nemo_fixes_verification.json)）：将 `m1-pro-nemo` 设为本地默认档位并调优至 `threads = 6`、强化 `REPAIR_PROMPT`（带入 `SYSTEM_PROMPT` 与日语原文）后，**峰值 CPU 从 `407.0%` 降至 `214.0%`**（平均 `13.0%`），D1 45s 进一步提速至 **P50 `0.954s` / P95 `1.335s`、`drain 0.38s`、`0` 警告**；`csj-reproduction` 末尾 `0.2s` 碎片 `"ほうであります。"` 仍由网关 `translation_invalid` 正常拦截（`1` 条警告）。
