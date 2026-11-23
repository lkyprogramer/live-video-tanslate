# `m1-pro-hq` 全 MLX GPU 档接入、修复与 1× 实速 / Chrome 1080p & 4K 端到端验证报告

- **验证日期**：2026-11-23
- **硬件与环境**：Apple M1 Pro（10 核 CPU / 16 核 GPU / 32 GiB 统一内存 / macOS 26.7.2），`engine/.venv`（`mlx==0.32.4`、`mlx-audio==0.6.7`、`mlx-lm==0.31.3`）
- **验证状态**：**PASS（80 项单元测试 + 1× 实速 WebSocket 全量验收 + `wmf-04` 瑕疵复测 + Chrome 1080p 跳跃/播中跳转 & 4K 2160p 真机联调全部通过）**

---

## 1. 接入架构与瑕疵修复

1. **配置与依赖**：
   - [pyproject.toml](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/pyproject.toml)：升级 `mlx==0.32.4` 并新增 `mlx-audio==0.6.7`。
   - [config.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/config.py) 与 [profiles.example.toml](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/config/profiles.example.toml)：保留 `default_profile_id = "reazon-hy"` 不变，新增可选 Profile `m1-pro-hq`（`Qwen3-ASR-1.7B-8bit` + `Silero VAD v4` + `Qwen3-4B-Instruct-2507-4bit`）。
2. **单 MLX 线程安全与自修复逻辑**（[local_pipeline.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/local_pipeline.py)）：
   - `Silero VAD v4` 留在 CPU `asr_pool`，将返回的 `list[float]` 显式转为 `np.asarray(..., dtype=np.float32)`；**`Qwen3AsrRecognizer` 与 `Qwen3Translator` 的初始化、ASR 解码、MT 翻译全部串行绑定至同一条常驻 `mt_pool` 单线程执行器**，避免 Metal 跨线程采样漂移。
   - **ASR 尾音重复消除**：按音频时长动态收紧 `max_tokens = min(256, max(48, int(len(audio) / RATE * 18)))`，启用 `repetition_penalty=1.15, repetition_context_size=64` 并以 `_collapse_repetition()` 兜底折叠连续重复短语。
   - **MT 残留假名自修复**：强化 `SYSTEM_PROMPT` 与 `repetition_penalty=1.08`，仅在译文命中 `_KANA` 时触发一次局部假名自修复（正常纯中文段 **0ms 额外开销**）。
   - **单元测试**（[test_m1_pro_hq.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/tests/test_m1_pro_hq.py)）：覆盖配置解析、模型路径、单 MLX 线程亲和性、重复折叠与假名自修复，全仓 **80 passed**（`1.27s`）。

---

## 2. 1× 实速 WebSocket 端到端验收（`subtitle-server`）

验证脚本：[verify_m1_pro_hq_live.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/verify_m1_pro_hq_live.py)，结果归档：[live_summary.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/m1-pro-hq-live-2026-11-23/live_summary.json) 与修复复测 [wmf04_summary.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/m1-pro-hq-browser-2026-11-23/wmf04_summary.json)。

| 评测项 | Profile | `eof` | 字幕条数 | 网关警告 | `drain_s` | 端到端（段末→WS）P50 / P95 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **`csj-reproduction`**（D0） | `m1-pro-hq` | `true` | `6` | `0` | `1.59s` | `1.88s / 3.76s` |
| **`csj-task`**（D0） | `m1-pro-hq` | `true` | `11` | **`0`** | `0.94s` | `2.44s / 4.19s` |
| **`wmf-04`（修复前 → 修复后）** | `m1-pro-hq` | `true` | `43 → 45` | **`2 → 0`** | `0.015s` | `1.69s/4.31s` → **`1.66s/3.13s`** |
| **`silence`**（10s 静音） | `m1-pro-hq` | `true` | **`0`** | `0` | `0.010s` | — |
| **`d1-ep01-45s`**（《重启人生》） | `m1-pro-hq` | `true` | `3` | `0` | `4.18s` | `3.45s / 4.19s` |
| **8s 忙时断连 → 立即重连** | `m1-pro-hq` | `true` | `6` | `0` | `1.25s` | — |
| **默认档无回归**（`csj-repro`） | `reazon-hy` | `true` | `6` | `0` | `0.26s` | — |

---

## 3. Chrome 真实标签页 1080p（跳跃 + 播中跳转）& 4K 2160p 真机联调

驱动脚本：[verify_chrome_1080p_4k.mjs](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/verify_chrome_1080p_4k.mjs)，汇总：[chrome_e2e_summary.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/m1-pro-hq-browser-2026-11-23/chrome_e2e_summary.json)，4K 字幕实景截图：[4k-caption.jpg](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/m1-pro-hq-browser-2026-11-23/4k-caption.jpg)。

| 测试场景 | 视频规格与行为 | 掉帧情况 | WS 字幕 / 拦截 | 稳态端到端延迟 |
| :--- | :--- | :---: | :---: | :---: |
| **1080p《重启人生》EP01 核心跳点** | `1920×1080` 跳转 `04:35.0` 播放 `46s` | `36 / 2171`（含首次 Seek） | **`7` 条 / `0` 拦截** | **`1.37s – 2.07s`**（首段 `4.61s`） |
| **1080p 播中二次跳转** | 运行中跳转 `12:05.0` 播放 `24s` | 同上连续会话 | **`1` 条 / `0` 拦截** | **`1.62s`** |
| **4K 2160p H.264 并发播放** | `3840×2160` 播放 `35s` 至 `ended` | **`0 / 1049`（0% 掉帧）** | **`4` 条 / `0` 拦截** | **`1.92s – 2.78s`**（首段 `5.07s`） |

- **并发资源占用**：`subtitle-server` 峰值 RSS **`2439.6 MiB`**（平均 CPU `15.4%`），Chrome 进程树峰值 RSS **`4821.4 MiB`**（平均 CPU `52.6%`），4K 视频硬解 **0 掉帧**且双行中文字幕清晰渲染。
