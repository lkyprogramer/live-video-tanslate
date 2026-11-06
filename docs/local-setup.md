# 本机使用与验收

## 启动

当前默认链路是 ReazonSpeech、Silero VAD 600 ms 和 Hy-MT2。运行环境为 Apple Silicon Mac、Python 3.12、uv、Node.js 22 和 Chrome。以下命令均从项目根目录运行。

```sh
uv sync --project engine --frozen
```

使用预构建 ZIP 时直接解压。也可以从源码构建扩展。

```sh
npm --prefix extension ci
npm --prefix extension run build
```

首次安装时，用以下命令下载固定版本的模型。已有缓存可以直接复用。

```sh
mkdir -p .model-cache/reazonspeech-assets
curl -fLsS https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx \
  -o .model-cache/reazonspeech-assets/silero_vad.onnx
HF_HOME="$PWD/.model-cache" HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
  uv run --project engine --frozen python - <<'PY'
from huggingface_hub import snapshot_download

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

服务启动时校验 Silero 文件的 SHA-256。权重备齐后，观看阶段在本机推理。

1. 在 Chrome 打开 `chrome://extensions`，开启开发者模式，加载 `extension/dist` 或 ZIP 解压后包含 `manifest.json` 的目录。
2. 复制扩展 ID。首次设置时将 `engine/config/profiles.example.toml` 复制为 `engine/config/profiles.toml`，把 `allowed_origin` 中的占位 ID 换成刚复制的 ID。
3. 启动本地服务。

   ```sh
   uv run --project engine --frozen subtitle-server --config engine/config/profiles.toml
   ```

4. 服务打印令牌文件路径。将文件中的令牌录入扩展 Options，点击「刷新配置」确认连通，再点击「保存」。
5. 播放日语视频，点击工具栏扩展图标。字幕上屏后继续播放即可。再次点击同一标签页的图标停止。暂停或拖动会清掉旧字幕，恢复播放从当前位置重新识别。

普通网页和播放器容器全屏支持字幕浮层。若网页直接让视频元素进入原生全屏，工具栏图标会显示 `!`，退出全屏后恢复字幕。

开发阶段在真实 Chrome 验证用的是独立临时浏览器配置。本地服务在终端前台运行，按 Ctrl-C 停止。
扩展连接固定的 `127.0.0.1:18765`；新增后端若需要另一个进程，在该后端的配置里填写其独立端口。

## 可选的 WhisperLiveKit 配置

默认配置可以单独运行。要在 Options 启用另一种真实后端，先按原 [P0 安装说明](p0.md#复现)准备 WhisperLiveKit 及模型，使用独立 loopback 端口 18766。保留原 P0 的 PCM、日语、Hy-MT2 和 `--retention-seconds 30` 参数。

从项目根目录开两个终端，各自使用同一个私有的上游令牌文件。以下命令要求原 P0 的 `.venv` 及缓存已就绪。

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

在本地 `profiles.toml` 的 `profiles.whisper-small` 将 `enabled` 设为 `true`。从另一个终端带同一个上游令牌启动本地服务。

```sh
export WLK_API_TOKEN="$(cat .model-cache/whisper-service.token)"
uv run --project engine --frozen subtitle-server --config engine/config/profiles.toml
```

刷新 Options 后选择配置并保存。工具栏停止并重新启动字幕时生效。

## 验证命令

```sh
uv run --project engine --frozen pytest -q engine/tests
npm --prefix extension run typecheck
npm --prefix extension test
npm --prefix extension run build
node extension/test/chrome-smoke.mjs p0/evidence/v1-chrome-check
```

[阶段验收与当前限制](v1-acceptance.md)。浏览器测试使用本机 Chrome、FFmpeg 和现有 CSJ PCM，并自行启动 loopback 服务。请先让出端口 18765，并为每次执行选择一个新证据目录。`--lifecycle` 加测接管、换视频、倍速、播放结束和断线恢复；`--youtube`、`--wikimedia` 加测在线样本。加 `--switch` 前先启动 WhisperLiveKit 18766 并向测试命令导出 `WLK_API_TOKEN`。
