# 本机日语实时字幕 ASR 选型

**综合首选是 ReazonSpeech k2-v2 + sherpa-onnx + Silero VAD（600 ms）+ Hy-MT2 1.8B 4-bit。** 用这组配置进入下一阶段本地原型与全量验收。它在双人对话的 5 秒覆盖最高，在讲解的 5 秒覆盖并列最高，两段都完整播放，静音无字幕，进程峰值约 1.92 GiB。现有测试仍有 4/8 和 8/12 个单元漏译或错译，离 5 秒覆盖 ≥ 90% 的目标有明显差距。

## ReazonSpeech 复测配置

MacBook Pro，M1 Pro（10 核 CPU、32 GiB 统一内存），macOS 26.7.2（25G332）。使用官方 ReazonSpeech k2-v2 权重 revision `291488c8151be24d7da4bf7af26e533fad96e407`，INT8 编码器和 joiner、FP32 解码器。sherpa-onnx 2.0.1 在 CPU 上用 2 线程做 greedy 解码。Silero VAD 阈值 0.5、最短语音 150 ms、长段触发阈值 4 秒。停顿起初设为 350 ms，观察到断词后只把该参数调到 600 ms 复测。长段参数会提高 VAD 判停灵敏度，实际语音段可能超过 4 秒。每个段落一次离线解码，完成后交给原版 Hy-MT2 1.8B 4-bit。持续播放时边进音频边切段，ASR 事件只有定稿。[ReazonSpeech 权重](https://huggingface.co/reazon-research/reazonspeech-k2-v2/tree/291488c8151be24d7da4bf7af26e533fad96e407) · [sherpa-onnx 官方 INT8 与 VAD 用法](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/zipformer-transducer-models.html#sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01-japanese)

沿用 CSJ 双人对话、带配乐越位讲解和 10 秒全零 PCM，按 16 kHz、单声道、200 ms 一帧、1× 回放。固定同一个 Hy-MT2 revision `e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912`，预热后开始计时。其他五组使用上一轮同机回放数据。两轮都接电，资源记录覆盖实际窗口，GPU 为整机采样。参考和计分规则沿用前一轮。对话使用官方时码和 8 个参考单元，讲解使用离线 Whisper 对齐的 12 个辅助参考，分开计分。P95 仅使用 15 秒内可用译文，漏译与错译同计，早于参考句末的延迟按零计。[前一轮回放与计量口径](p0-nemotron.md)

## 字幕与稳定性

| 配置 | 首条中文 对话 / 讲解 | 首条可用 对话 / 讲解 | 句末 P95 对话 / 讲解 | 15 秒漏错 对话 / 讲解 | 5 秒可用 对话 / 讲解 | 有声片段跑完 | 10 秒静音 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ReazonSpeech 600 ms | 2.65 / 12.98 秒 | 6.54 / 7.79 秒 | 1.94 / 2.97 秒 | 4/8 · 8/12 | 4/8 · 4/12 | 2/2 | 无字幕 |
| ReazonSpeech 350 ms | 2.46 / 4.86 秒 | 6.28 / 32.71 秒 | 1.05 / 2.98 秒 | 6/8 · 10/12 | 2/8 · 2/12 | 2/2 | 无字幕 |
| Apple 默认定稿 | 13.19 / 16.22 秒 | 13.19 / 16.22 秒 | 8.14 / 8.75 秒 | 5/8 · 4/12 | 1/8 · 4/12 | 2/2 | 「啊」 |
| Apple 提前定稿 | 5.35 / 9.21 秒 | 5.35 / 9.21 秒 | 0.44 / 8.63 秒 | 6/8 · 7/12 | 2/8 · 4/12 | 2/2 | 「啊」 |
| Nemotron BF16 | 12.30 / 9.16 秒 | 12.30 / 37.58 秒 | 8.20 / 6.14 秒 | 6/8 · 10/12 | 0/8 · 1/12 | 2/2 | 无字幕 |
| Whisper small | 2.63 / 8.70 秒 | 6.94 / 35.89 秒 | 2.01 / 4.45 秒 | 5/8 · 10/12 | 3/8 · 2/12 | 2/2 | 无字幕 |
| Whisper large-v3-turbo | 4.41 / 7.17 秒 | 10.60 / 11.82 秒 | 5.86 / 4.96 秒 | 4/8 · 11/12 | 1/8 · 1/12 | 0/2 | 无字幕 |

「首条中文」沿用含汉字且无假名的自动口径，可能抓到语气词或错译。「首条可用」以第一个命中参考单元的译文为准。600 ms 组在 7.79 秒译出了「将解释越位规则」，品牌片段仍是「ショー」，因此自动计时跳到了 12.98 秒。其两段 P95 各基于 4 条配对。讲解两列语义数据基于辅助参考。资源比较中的 turbo 只采集到讲解约第 28 秒中断。逐句结果及全部资源统计（本地 `p0/evidence/reazonspeech-comparison/comparison.json`）

600 ms 组的 ASR 解码 P95 为 126 ms，末尾到链路完成为对话约 0.32 秒、讲解约 0.004 秒，最大送帧迟到约 5.55 ms。350 ms 组提前返回了「生姜」，600 ms 组保住了「オフサイド」；在双人对话里多译对了「有很多人我不认识」和「从最上面开始读名字」。仍有两类丢失。越位说明中的「射进也无效」在转写后缺失，日文正确识别的「レミはカタカナです」又被 Hy-MT2 译成「雷米是卡塔卡纳」。600 ms 对话输出（本地 `p0/evidence/reazonspeech-comparison/reazonspeech-600/csj-task.zh.jsonl`） · 600 ms 讲解输出（本地 `p0/evidence/reazonspeech-comparison/reazonspeech-600/offsiderule.zh.jsonl`）

Apple 默认定稿在讲解的 15 秒窗口保留了 8/12 个单元，为该片最高，但讲解 P95 8.75 秒，且静音复现假字幕。提前定稿在少数对话命中上很快，切点可能拆开否定与后文。Nemotron 没有积压且静音通过，当前配置的日文错字、长段切句和 GPU 占用均不占优。Whisper small 在旧的 17 段 P0 中有 2/17 次积压中断，5 秒覆盖 49/121；turbo 为 11/17 和 18/121。旧全量测试只涵盖这两组 Whisper。[旧全量 P0](p0.md)

## 资源

下表取带配乐讲解从开始供流到完成或中断的实际区间。括号内为各轮启动前约 4 秒的整机 GPU 均值。

| 配置 | 进程 CPU 均值 / P95 | 进程统一内存峰值 | 整机 GPU 均值（启动前） | GPU 内存中位增量 / 整机峰值 |
| --- | ---: | ---: | ---: | ---: |
| ReazonSpeech 600 ms | 7.8% / 66.4% | 1.92 GiB | 26.7%（21.0%） | −0.10 / 3.11 GiB |
| ReazonSpeech 350 ms | 7.9% / 74.6% | 1.88 GiB | 22.1%（23.1%） | −0.03 / 3.06 GiB |
| Apple 默认定稿 | 3.2% / 17.0% | 1.43 GiB | 15.3%（4.6%） | +0.08 / 2.65 GiB |
| Apple 提前定稿 | 3.5% / 34.4% | 1.46 GiB | 15.6%（7.9%） | +0.05 / 2.61 GiB |
| Nemotron BF16 | 6.2% / 41.3% | 3.89 GiB | 48.6%（3.6%） | +2.39 / 5.27 GiB |
| Whisper small | 181.1% / 338.0% | 7.00 GiB | 64.4%（9.7%） | +2.91 / 6.34 GiB |
| Whisper large-v3-turbo | 58.4% / 200.2% | 6.29 GiB | 80.2%（18.9%） | +4.40 / 6.39 GiB |

进程 CPU 的 100% 代表一个逻辑核。ReazonSpeech 由 CPU 做 ASR，资源表包含 MLX 翻译进程。两次 ReazonSpeech 回放时的整机后台负载高于部分历史对照，GPU 内存中位增量出现负值，无法据此估计模型专用内存。Apple 默认定稿的系统语音进程未单列，提前定稿的 XPC 单独采到约 1.84% CPU、30.42 MiB 内存峰值。两轮都采样 250 ms，全部片段与讲解共同前 15 秒的资源记录在汇总 JSON。[资源口径](p0-nemotron.md#计量口径) · ReazonSpeech 600 ms 原始资源汇总（本地 `p0/evidence/reazonspeech-comparison/reazonspeech-600-resources/resources.json`）

## 插件方案

进入实施、接入其他后端或执行验收时使用[开发方案](development-plan.md)和[字幕协议与后端合同](subtitle-backend-contract.md)。

独立开发 Chrome 扩展，复用 JP Live Subtitle CN 的标签页采音和字幕浮层思路。Mac 本地服务通过 WebSocket 接收 16 kHz PCM，按 Silero 切段，经 sherpa-onnx 输出日文，再用 Hy-MT2 发中文字幕。用分段的稳定 ID、音频范围和有序输出承接播放控制。WhisperLiveKit 作为复现基线，暂时复用现成的 Hy-MT2 调用实现。

优先用同一条链路补齐 VAD 截断和翻译上下文的验证，再用全量 P0 看 5 秒覆盖和 P95。600 ms 在这两段上选出，跨视频效果、Chrome 上屏和长时间播放仍需验证。若 5 秒覆盖继续偏低，用已有日文时码定位是 VAD、ASR 或翻译造成漏译，再决定调哪一层。

## 复现

使用项目已有的 `.venv` 和 Hy-MT2 缓存。ReazonSpeech 使用独立虚拟环境，缓存不需要额外服务。Silero 文件 SHA-256 为 `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`。

```sh
uv venv --python .venv/bin/python .model-cache/reazonspeech-venv
uv pip install --python .model-cache/reazonspeech-venv/bin/python sherpa-onnx==2.0.1 numpy
mkdir -p .model-cache/reazonspeech-assets
curl -fLsS https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx \
  -o .model-cache/reazonspeech-assets/silero_vad.onnx
HF_HOME="$PWD/.model-cache" HF_HUB_DISABLE_IMPLICIT_TOKEN=1 .venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    "reazon-research/reazonspeech-k2-v2",
    revision="291488c8151be24d7da4bf7af26e533fad96e407",
    allow_patterns=["encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx",
                    "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt", "README.md"],
)
PY
evidence=p0/evidence/repeat-reazonspeech
mkdir "$evidence"
/usr/bin/caffeinate -is .venv/bin/python p0/monitor.py "$evidence/reazonspeech-resources" \
  .venv/bin/python -u p0/run_apple.py p0/reazonspeech.py "$evidence/reazonspeech" \
  csj-task offsiderule silence --asr-python .model-cache/reazonspeech-venv/bin/python
P0_REAZON_SILENCE_S=0.60 /usr/bin/caffeinate -is .venv/bin/python p0/monitor.py \
  "$evidence/reazonspeech-600-resources" .venv/bin/python -u p0/run_apple.py \
  p0/reazonspeech.py "$evidence/reazonspeech-600" csj-task offsiderule silence \
  --asr-python .model-cache/reazonspeech-venv/bin/python
.venv/bin/python p0/review_short.py p0/evidence/reazonspeech-comparison \
  --review p0/evidence/reazonspeech-comparison/review.json
```
