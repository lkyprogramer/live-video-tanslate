# live-video-tanslate

在 Apple Silicon Mac 上本地运行的 Chrome 实时日语视频中文字幕扩展：捕获当前标签页音频（保留原声回放），通过本地 WebSocket（`127.0.0.1:18765`）完成日语语音识别与简体中文翻译并叠加至视频画面，**不读取网页字幕轨、不调用任何云端服务**。

## 核心配置档位（Profiles）

本地服务通过 `engine/config/profiles.toml` 提供三档可切换后端（可在扩展 Options 中随时切换）：

| 档位 ID | 架构路线 | 适用场景 | Stage 0–4 实测表现 |
| :--- | :--- | :--- | :--- |
| **`m1-pro-nemo`**（推荐主力） | **MTL0 GPU 交错调度 + Draft-and-Commit**：`speech.cpp`（NeMo-v2 619M Q8_0 beam, `device="gpu"`）+ MLX `Qwen3-4B-Instruct-2507-4bit`（2 轮滚动 KV Cache）+ `1.2s` SOV 预览 + `4.5s` 能量谷短切分 | 日剧、动漫、综艺、直播等高语速口语（最快且口语还原度最高） | D1 1× WS：`final_e2e` **P50 `0.604s` / P95 `1.008s`**，`first_delta` **P50 `0.508s` / P95 `0.883s`**；起音至首字 **-54.4%**（`4.873s → 2.224s`），100% 终版一致 |
| **`m1-pro-hq`** | **全 MLX GPU v2**：`Qwen3-ASR-1.7B-8bit`（A3 内核）+ `Qwen3-4B-Instruct-2507-4bit`（滚动 KV Cache），单线程 `MT > ASR` 优先调度 | 英文外来词或学术/技术专名密集的讲座、会议视频（免 C++ 编译） | D1 `13` 条字幕、`0` 警告、端到端 **P50 `1.21s` / P95 `1.58s`** |
| **`reazon-hy`**（示例默认） | `ReazonSpeech k2-v2 INT8`（CPU 2 线程）+ `Hy-MT2 1.8B 4-bit`（MLX） | 轻量低占用后台挂机（仓库 `profiles.example.toml` 保留为默认以兼容开箱即用与单测） | 轻载低内存，密集长对白有漏句 |

## 核心架构特性

- **MTL0 交错调度与零争用**：收音（`64ms` 帧）与 Silero VAD（`vad_pause_ms=350`）完全解耦；`m1-pro-nemo` 通过 `Final MT > Final ASR > Draft` 三级可抢占调度共享单 GPU（100 句并发 MT 膨胀 `-3.57%`），峰值 CPU `-59%`、峰值 RSS `-687 MiB`。
- **1.2s Draft-and-Commit 与毫秒级取消**：SOV 从句门控驱动 `greedy + pad=150ms` 快速预览与只读 KV 翻译，终版 `beam + pad=350ms` 原地覆盖且保持 `121/121` 零漂移；`generate_step` 与 C++ 进度回调支持毫秒级协作式取消。
- **能量谷短切分、边界去重与零假名泄漏**：长语音 `4.5s` 内回溯 `[n-0.65s, n-0.08s]` 局部最低能量谷强切并保留 `350ms` 重叠区（`timestamps=8` token 去重）；`Qwen3-4B` 对 2154 个假名 token 实施 `-1e9` 硬掩码实现 `0/91` 假名泄漏。
- **双行流式自适应字幕**：Shadow DOM 浮层支持 `caption_delta` 流式上屏与原地定稿，展示上一句（`<span data-prev>`）与当前句（`<span data-current>`），停留时间按字数自适应（`2.5s – 6.5s`）。

## 快速安装与启动

环境要求：Apple Silicon Mac（推荐 M1 Pro / 32 GiB 及以上）、Chrome 116+、Python 3.12、`uv`、Node.js 22；启用 `m1-pro-nemo` 另需 `cmake`、`ninja` 与 `clang++`。

1. **构建或加载 Chrome 扩展**：
   ```sh
   npm --prefix extension ci
   npm --prefix extension run build    # 构建至 extension/dist；打包 ZIP 可运行 run package
   ```
   在 Chrome 打开 `chrome://extensions`，开启开发者模式，加载 `extension/dist`（或解压后的 ZIP 目录），复制扩展 ID。
2. **准备 Python 环境与模型**：
   按 [本机配置与模型下载](docs/local-setup.md) 准备模型缓存；若启用 **`m1-pro-nemo`**，运行一键脚本编译 `speech.cpp` 共享库并拉取 `Q8_0` 权重：
   ```sh
   ./scripts/setup_speech_cpp_nemo.sh
   ```
3. **生成本机配置并启动服务**：
   复制 `engine/config/profiles.example.toml` 为 `engine/config/profiles.toml`，将 `allowed_origin` 替换为扩展 ID（如需设为默认档，将 `default_profile_id` 改为 `"m1-pro-nemo"` 并启用对应 profile）：
   ```sh
   engine/.venv/bin/python -m subtitle_service.cli --config engine/config/profiles.toml
   ```
4. **连接与使用**：
   将服务启动时打印的令牌写入扩展 Options 页面，点击「刷新配置」并保存；播放日语视频时点击工具栏图标即可开启/停止字幕。

## 测试与验证

```sh
PYTHONPATH=.:engine/src engine/.venv/bin/pytest engine/tests p0/p2/tests p0/local_streaming
npm --prefix extension run typecheck && npm --prefix extension test
```

## 文档索引

### 最新演进与优化验证（2026-11-24）

- **架构与配置**：[本机配置与模型下载](docs/local-setup.md) · [M1 Pro 架构上限优化路线图（Stage 0–5）](docs/m1-pro-architecture-ceiling-roadmap-2026-11-23.md) · [M1 Pro 极致优化五层方案](docs/m1-pro-extreme-optimization-plan-2026-11-23.md) · [M1 Pro 性能上限与底层评估](docs/M1%20Pro%20实时日语字幕的性能上限与底层优化评估.md)
- **实测与验收**：[Stage 0–4 架构上限全量实测验收报告](docs/stage0-4-architecture-ceiling-verification-2026-11-24.md) · [GPT Pro 性能评审与实验建议](docs/M1%20Pro%20实时日语字幕本轮优化后的性能评审与后续实验.md) · [Step 2–3 联调报告](docs/step2-3-optimization-verification-2026-11-23.md)
- **模型选型盲测**：[M1 Pro 升级调研](docs/m1-pro-upgrade-research-2026-11-23.md) · [Tier 2A ASR 与 Smart Turn 盲测](docs/tier2-asr-smart-turn-results-2026-11-23.md) · [Tier 2B 4B–8B MT 盲测](docs/tier2b-mt-results-2026-11-23.md) · [全 MLX GPU 初版接入验证](docs/m1-pro-hq-verification-2026-11-23.md)

### 协议契约与历史基线

- **契约与基线**：[v1 接口合同](docs/subtitle-backend-contract.md) · [开发方案](docs/development-plan.md) · [P0/v0 架构基线](docs/architecture.md) · [v1 验收报告](docs/v1-acceptance.md)
- **历史阶段记录**：[流式验证记录（11-19 / 11-23）](docs/local-streaming-validation-2026-11-23.md) · [P3 执行包](docs/live-video-translate-p3-2026-11-10/README.md) · [P2 执行包](docs/live-video-translate-p2-2026-11-07/README.md) · [B0](docs/p2-b0-results-2026-11-08.md) / [B1](docs/p2-b1-results-2026-11-08.md) / [B2](docs/p2-b2-results-2026-11-09.md) / [B3](docs/p2-b3-results-2026-11-09.md) / [B4](docs/p2-b4-gemma-zh-results-2026-11-09.md)
