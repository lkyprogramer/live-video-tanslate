# 本机配置、模型准备与验收

以下所有命令默认在项目根目录执行，适用环境为 Apple Silicon Mac、macOS、Chrome 116+、Python 3.12、`uv` 与 Node.js 22。

---

## 1. 准备 Python 与扩展环境

### 1.1 Python 引擎环境（`engine/.venv`）

[engine/pyproject.toml](../engine/pyproject.toml) 已通过 `override-dependencies = ["mlx==0.32.4"]` 锁定 `mlx==0.32.4` 与 `mlx-audio==0.6.7`，可直接使用 `uv` 同步环境：

```sh
uv lock --check --directory engine
uv sync --project engine --frozen
```

### 1.2 构建 Chrome 扩展

使用预构建 ZIP 时直接解压即可；从源码构建或打包：

```sh
npm --prefix extension ci
npm --prefix extension run build      # 输出至 extension/dist
# npm --prefix extension run package  # 可选：打包至 artifacts/
```

---

## 2. 准备模型与原生推理库

所有模型统一缓存在项目根目录 `.model-cache/`（已加入 `.gitignore`）。

### 2.1 公共 VAD 模型（所有档位必需）

服务启动时会校验 `silero_vad.onnx` 的 SHA-256：

```sh
mkdir -p .model-cache/reazonspeech-assets
curl -fLsS https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx \
  -o .model-cache/reazonspeech-assets/silero_vad.onnx
```

### 2.2 `m1-pro-nemo`（推荐 M1 Pro 主力 · ANE ‖ GPU 物理异构并行 + Draft-and-Commit）

1. **编译 `speech.cpp` 并下载/校验 `reazonspeech-nemo-619M-v2-Q8_0.gguf`**（需 `cmake`、`ninja`、`clang++`）：
   ```sh
   ./scripts/setup_speech_cpp_nemo.sh
   ```
   脚本会自动校验 `Q8_0` GGUF 的 SHA256（`944411cb...e54a3`）并打印 `libspeech.dylib` 校验和，产物安装在 `.model-cache/speech-cpp/`。
2. **编译 `libnemo_ane.dylib` 并准备 CoreML ANE 静态桶**（启用默认 `device = "ane"`、`decoding = "greedy"`、`draft_interval_s = 1.2`、`vad_pause_ms = 350`、`max_segment_s = 4.5`、`overlap_ms = 350`、`pad_ms = 350`，需 Xcode Command Line Tools 提供 `CoreML` / `Accelerate`）：
   ```sh
   ./scripts/setup_nemo_ane.sh
   ```
   产物安装在 `.model-cache/nemo-ane/`。若需生成 3 个 `w8a16` 静态桶（`fc_S256_w8a16.mlmodelc`、`fc_M560_w8a16.mlmodelc`、`fc_L1024_w8a16.mlmodelc`），可在包含 `coremltools==9.0` 与 `torch==2.5.1` 的隔离虚拟环境中运行：
   ```sh
   python p0/perf_probe/verify_stage5a_ane.py --export-prod-only --out-dir .model-cache/nemo-ane
   ```
   若未放置 `.mlmodelc` 桶或遇到超长切片，引擎会在启动或推理时自动无感回退至 `speech.cpp` MTL0 GPU。
3. **下载 `Qwen3-4B-Instruct-2507-4bit`**：见下方第 2.3 节共用命令。

### 2.3 `m1-pro-hq`（全 MLX GPU 档）与 `reazon-hy`（轻量基线档）

按需下载对应固定 revision 快照（已存在则自动复用）：

```sh
HF_HOME="$PWD/.model-cache" HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
  engine/.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download

# m1-pro-nemo 与 m1-pro-hq 共用 MT：Qwen3-4B-Instruct-2507-4bit
snapshot_download(
    "mlx-community/Qwen3-4B-Instruct-2507-4bit",
    revision="50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
    allow_patterns=["*.json", "*.jinja", "*.safetensors"],
)

# 仅 m1-pro-hq 需要：Qwen3-ASR-1.7B-8bit
snapshot_download(
    "mlx-community/Qwen3-ASR-1.7B-8bit",
    revision="a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
    allow_patterns=["*.json", "*.safetensors", "vocab.json", "merges.txt"],
)

# 仅 reazon-hy 需要：ReazonSpeech k2-v2 INT8 + Hy-MT2 1.8B 4-bit
snapshot_download(
    "reazon-research/reazonspeech-k2-v2",
    revision="291488c8151be24d7da4bf7af26e533fad96e407",
    allow_patterns=["encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx",
                    "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt", "README.md"],
)
snapshot_download(
    "mlx-community/Hy-MT2-1.8B-4bit",
    revision="e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912",
    allow_patterns=["*.json", "*.jinja", "*.safetensors", "LICENSE.txt"],
)
PY
```

---

## 3. 配置与启动

1. 在 Chrome 打开 `chrome://extensions`，开启开发者模式，加载 `extension/dist`（或解压后的 ZIP 目录），复制扩展 ID。
2. 复制 `engine/config/profiles.example.toml` 为 `engine/config/profiles.toml`（gitignored）：
   - 将 `allowed_origin` 中的 `<extension-id>` 替换为刚复制的扩展 ID；
   - 按需将 `[profiles.m1-pro-nemo]` / `[profiles.m1-pro-hq]` 的 `enabled` 设为 `true`；
   - 推荐将 `default_profile_id` 改为 `"m1-pro-nemo"`（仓库示例保留 `"reazon-hy"` 以确保无额外下载即可启动）。
3. 启动本地服务：
   ```sh
   engine/.venv/bin/python -m subtitle_service.cli --config engine/config/profiles.toml
   ```
4. 首次启动会打印令牌文件路径。将令牌填入扩展 Options 页面，点击「刷新配置」确认可用档位后保存。
5. 播放日语视频并点击工具栏图标即可开启字幕：
   - 画面底部渲染**双行滚动字幕**（上行历史句 + 下行当前句），按字符数自适应停留 `2.5s – 6.5s`；
   - 拖动进度条（seek）或暂停时立即中止旧推理并清屏，恢复播放自动重建新会话；
   - 支持普通网页与播放器容器全屏；若页面直接触发 `<video>` 原生全屏，图标会提示 `!`，退出全屏后恢复。

---

## 4. 可选的 WhisperLiveKit 配置

仓库保留 `whisperlivekit_ws` 对接能力，使用独立 loopback 端口 `18766`：

```sh
umask 077
test -f .model-cache/whisper-service.token || \
  .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))' > .model-cache/whisper-service.token
export WLK_API_TOKEN="$(cat .model-cache/whisper-service.token)"
export HF_HOME="$PWD/.model-cache" HF_HUB_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1
.venv/bin/whisperlivekit-server --host 127.0.0.1 --port 18766 --pcm-input \
  --warmup-file ../whisper.cpp/samples/jfk.wav --model_cache_dir "$PWD/.model-cache/whisper-pt" \
  --backend mlx-whisper --backend-policy simulstreaming --model small \
  --language ja --target-language zh-cn --translation-backend mlx-llm-mt \
  --mlx-llm-mt-model hy-mt2-1.8b-4bit --retention-seconds 30 \
  --max-concurrent-sessions 1 --max-buffered-audio 4 --backpressure-timeout 1 --log-level WARNING
```

在本地 `profiles.toml` 中将 `[profiles.whisper-small]` 的 `enabled` 设为 `true`，带同一 `WLK_API_TOKEN` 启动 `subtitle-server` 即可在 Options 中切换。

---

## 5. 验证命令

```sh
# 1. 依赖锁、全量单元测试与前端类型检查/构建
uv lock --check --directory engine
PYTHONPATH=.:engine/src engine/.venv/bin/pytest engine/tests p0/p2/tests p0/local_streaming
npm --prefix extension run typecheck && npm --prefix extension test && npm --prefix extension run build

# 2. Stage 0–5 硬件探针与 1× 实速 WebSocket 对比（需停止占用 18765 的服务）
engine/.venv/bin/python p0/perf_probe/verify_stage0_1.py
engine/.venv/bin/python p0/perf_probe/verify_stage2.py
engine/.venv/bin/python p0/perf_probe/verify_stage3.py
engine/.venv/bin/python p0/perf_probe/verify_stage4.py
engine/.venv/bin/python p0/perf_probe/verify_stage5a2_live.py
# 可选离线探针（Stage 5A-1 CoreML 导出需含 coremltools==9.0 与 torch==2.5.1 的隔离 venv；Stage 5B 需 kapitsa）
# python p0/perf_probe/verify_stage5a_ane.py --export-prod-only --out-dir .model-cache/nemo-ane
# engine/.venv/bin/python p0/perf_probe/verify_stage5b_kapitsa_mt.py

# 3. Chrome 1080p / 4K 端到端联调 & Stage 5D 30–60 分钟热稳定性压测（需准备 MEDIA_DIR 视频并空出 18765）
# sudo powermetrics --samplers gpu_power,ane_power,cpu_power,thermal -i 5000 -n 360 -o /tmp/stage5d_powermetrics.txt
# TEST_PROFILE=m1-pro-nemo MEDIA_DIR=... node p0/tier2_mt/verify_chrome_1080p_4k.mjs

# 4. 基础协议与浏览器冒烟
node extension/test/chrome-smoke.mjs p0/evidence/v1-chrome-check
```
