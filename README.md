# live-video-tanslate

在 Apple Silicon Mac 上本地运行的 Chrome 实时日语视频中文字幕扩展：捕获当前标签页音频（保留原声回放），通过本地 WebSocket（`127.0.0.1:18765`）完成日语语音识别与简体中文翻译并叠加至视频画面，**不读取网页字幕轨、不调用任何云端服务**。

## 核心配置档位（Profiles）

本地服务通过 `engine/config/profiles.toml` 提供三档可切换后端（可在扩展 Options 中随时切换）：

| 档位 ID | 架构路线 | 适用场景 | Stage 5 实测表现 |
| :--- | :--- | :--- | :--- |
| **`m1-pro-nemo`**（推荐主力） | **ANE + Metal GPU 物理异构真并行 + Draft-and-Commit**：`libnemo_ane.dylib`（CoreML ANE `w8a16` 静态桶 + AMX `greedy` RNNT，回退 `speech.cpp` MTL0 GPU）‖ MLX `Qwen3-4B-Instruct-2507-4bit`（2 轮滚动 KV Cache）+ `1.2s` SOV 预览 + `4.5s` 能量谷短切分 | 日剧、动漫、综艺、直播等高语速口语（最快且口语还原度最高） | D1 1× WS：`final_e2e` **P50 `0.491s` / P95 `0.916s`**，`first_delta` **P50 `0.440s` / P95 `0.808s`**，ASR **P50 `73.5ms`**；峰值 CPU `48.2%`（`-69%`）；并发 MT 膨胀 `+7.76%` |
| **`m1-pro-hq`** | **全 MLX GPU v2**：`Qwen3-ASR-1.7B-8bit`（A3 内核）+ `Qwen3-4B-Instruct-2507-4bit`（滚动 KV Cache），单线程 `MT > ASR` 优先调度 | 英文外来词或学术/技术专名密集的讲座、会议视频（免 C++ 编译） | D1 `13` 条字幕、`0` 警告、端到端 **P50 `1.21s` / P95 `1.58s`** |
| **`reazon-hy`**（示例默认） | `ReazonSpeech k2-v2 INT8`（CPU 2 线程）+ `Hy-MT2 1.8B 4-bit`（MLX） | 轻量低占用后台挂机（仓库 `profiles.example.toml` 保留为默认以兼容开箱即用与单测） | 轻载低内存，密集长对白有漏句 |

## 核心架构特性

- **ANE ‖ Metal GPU 物理异构并行与优雅回退**：收音（`64ms` 帧）与 Silero VAD（`vad_pause_ms=350`）完全解耦；`m1-pro-nemo` 默认将 FastConformer Encoder（`w8a16`）与 `greedy` RNNT 解码卸载至 ANE + AMX，与 Metal GPU `Qwen3-4B` 真并行（525 次并发解码下 GPU MT 仅膨胀 `+7.76%`，峰值 CPU 降至 `48.2%`）；缺少 ANE 资产或超长切片时自动无感回退至 `speech.cpp` MTL0 GPU。
- **1.2s Draft-and-Commit 与毫秒级取消**：SOV 从句门控驱动 `pad=150ms` 快速预览与只读 KV 翻译，终版 `pad=350ms` 原地覆盖定稿（`m1-pro-nemo` 默认在 ANE `w8a16` + AMX `greedy` 上完成预览与定稿，并保留 `device="gpu"` / `decoding="beam"` 可配置回退；Stage 4 已验证 `121/121` 终版零漂移）；`generate_step` 与 C++/Objective-C++ 进度回调均支持毫秒级协作式取消。
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
   按 [本机配置与模型下载](docs/local-setup.md) 准备模型缓存；若启用 **`m1-pro-nemo`**，先编译 `speech.cpp` 共享库并拉取 `Q8_0` 权重，再编译 `libnemo_ane.dylib` 启用 ANE 异构加速（缺失 `.mlmodelc` 桶时自动无感回退至 `speech.cpp` MTL0 GPU）：
   ```sh
   ./scripts/setup_speech_cpp_nemo.sh
   ./scripts/setup_nemo_ane.sh
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

### 核心文档与实测验收（2026-11-25）

- **配置与契约**：[本机配置与模型下载](docs/local-setup.md) · [本地字幕服务 v1 合同](docs/subtitle-backend-contract.md)
- **架构路线与实测**：[Stage 6 全集日剧真浏览器深度验证方案](docs/stage6-full-episode-verification-plan-2026-11-25.md) · [Stage 5 ANE 物理异构并行实测验收报告](docs/stage5-ane-heterogeneous-verification-2026-11-24.md) · [Stage 0–4 架构上限全量实测验收报告](docs/stage0-4-architecture-ceiling-verification-2026-11-24.md) · [M1 Pro 架构上限优化路线图（Stage 0–5）](docs/m1-pro-architecture-ceiling-roadmap-2026-11-23.md)

> 注：早期 P0 / P2 / P3 调研草稿、过渡期实验记录与阶段执行包已从工作树精简移除，完整记录保留在 Git 历史中。
