# Apple SpeechAnalyzer 本机短测

2026-11-06，Apple M1 Pro、32 GiB、macOS 26.7.2（25G332）、Swift 6.4.2。通过 `AssetInventory` 安装 `ja-JP` 语音资源，使用 `SpeechTranscriber.timeIndexedProgressiveTranscription`。翻译沿用原 P0 的 Hy-MT2 1.8B 4-bit，使用本机 MLX 和同一个缓存 revision `e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912`。本机安装的 WhisperLiveKit 0.2.26 提供 Hy-MT2 调用。[Apple SpeechAnalyzer](https://developer.apple.com/documentation/speech/speechanalyzer) · [原 P0](p0.md)

Apple 能持续跟上这两个原速片段，当前两种提交策略都未达到观影门槛。双人对话的 15 秒漏译或错译率均为 75%，提前定稿缩短了等待，也把一处否定和后文切到了两条字幕里。继续用相同片段验证下一个流式 ASR，再决定插件引擎。

## 默认定稿策略

| 原 P0 音频 | Apple EOF | 首条临时日文 | 首条最终日文 | 首条中文 | 播放结束到 ASR 完成 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 双人对话，40.56 秒 | 完整 | 1.26 秒 | 12.75 秒 | 13.24 秒 | 0.06 秒 |
| 带配乐越位讲解，97.42 秒 | 完整 | 4.03 秒 | 15.65 秒 | 16.69 秒 | 0.09 秒 |
| 纯静音，10 秒 | 完整 | 8.83 秒 | 10.04 秒 | 10.25 秒 | 0.03 秒 |

首条时间从开始播放算，中文以翻译函数返回为终点。临时结果相对 Apple 自报的音频范围末端，两个有声片段 P95 分别为 0.046 和 0.049 秒。该值衡量范围更新，人工句末延迟另计。首条中文的翻译耗时分别为 0.49 和 1.04 秒。两个有声片段的首条临时日文到首条最终日文均超过 11 秒。

越位讲解在 WhisperLiveKit `small` 和 `large-v3-turbo` 中分别于 47.01 和 20.41 秒因积压中断。Apple 跑完全部 97.42 秒。双人对话上次的 `small` 完整跑完，音频结束后还等待约 6.98 秒，`large-v3-turbo` 于 35.21 秒中断。Apple 的 10 秒静音输入有 320,000 个全零字节，仍最终识别出「 あ 」，Hy-MT2 返回「啊」。Whisper 基线（本地 `p0/evidence/comparison.json`） · Apple 默认回放（本地 `p0/evidence/apple/runs.jsonl`） · 静音输出（本地 `p0/evidence/apple/silence.zh.jsonl`）

## 提前定稿和语义复核

第一次尝试从 Apple 临时转写的词级时间范围找句末，在双人对话没有触发定稿请求。随后记录首个以日文句号、问号或叹号结尾的临时结果，并要求后续识别的音频范围至少向后推进 0.6 秒且保留相同前缀。满足后用该临时结果的范围末端调用 `finalize(through:)`。两次切点间距至少 1 秒。这个范围末端是切点的近似值。对话的首次请求传入 2.00 秒，返回的最终段却覆盖到 5.00 秒。另一次在「知らない人がいっぱいいるぞ」中间切开，中文分别变成「我不认识它」和「有很多人啊」。[定稿 API](https://developer.apple.com/documentation/speech/speechanalyzer/finalize(through:)) · 提前定稿事件（本地 `p0/evidence/apple-sentences/csj-task.asr.jsonl`）

| 双人对话 | 首条中文 | 句末到可用中文 P95 | 5 秒内可用 | 15 秒内漏译或不可用 |
| --- | ---: | ---: | ---: | ---: |
| Apple 默认定稿 | 13.24 秒 | 7.80 秒（2 条） | 1/8 | 6/8 |
| Apple 提前定稿 | 5.45 秒 | 0.33 秒（2 条） | 2/8 | 6/8 |
| WhisperLiveKit small | 2.57 秒 | 2.53 秒（3 条） | 3/8 | 5/8 |
| WhisperLiveKit large-v3-turbo | 4.74 秒 | 7.07 秒（4 条） | 1/8 | 4/8 |

两种 Apple 策略的 P95 分别基于两个有效配对，仅「我这边有照片」一个单元共同命中。该句默认耗时 7.80 秒，提前定稿耗时 0.01 秒。提前定稿在「塞尔吉奥越后是谁」上命中，默认在「我这边有名字和一句话」上命中。原 P0 门槛是 P95 ≤ 3 秒、至少 90% 在句末后 5 秒内有可用译文、静音无字幕。逐句判定（本地 `p0/evidence/apple-csj-task-review.json`） · [验收门槛](architecture.md)

提前定稿也完整跑完越位讲解，首条中文从 16.69 秒降到 9.25 秒。默认 ASR 识别出了「オフサイド」，Hy-MT2 同时有「禁区」和「越位」两种译法。提前定稿的静音对照也输出了「啊」。提前定稿回放（本地 `p0/evidence/apple-sentences/runs.jsonl`） · 越位讲解默认译文（本地 `p0/evidence/apple/offsiderule.zh.jsonl`） · 静音复测（本地 `p0/evidence/apple-sentences/silence.zh.jsonl`）

## 测量与复现

- 串行回放原 P0 的两个有声片段和静音对照。运行器核对 PCM SHA-256，Swift 每 200 ms 输入一帧 16 kHz 单声道 s16le PCM，最后不足一帧的部分按实长输入。正式录数在 SpeechAnalyzer 准备好、Hy-MT2 预热后开始，包含片头静音。使用单独的串行翻译线程，识别流可以持续接收日文。
- 日文和中文使用各自进程的单调时钟。翻译端以收到 Swift 开始事件为时间零点，包含译前排队和推理时间。首次 IPC 到达带来的小量时钟误差未单独校准。首条中文沿用 P0 的字符过滤口径，另做核心语义人工复核。
- 双人对话沿用原 P0 的 8 个人工参考单元。句末 P95 只统计 15 秒内核心语义正确的译文，同时列出漏译和 5 秒覆盖。越位讲解用于吞吐、首条延迟和定性检查，缺少独立逐句时间码。短测未覆盖完整 P0、60 分钟持续播放和 Chrome 渲染。

```sh
swiftc -O -parse-as-library p0/apple.swift -o .model-cache/apple-replay
.model-cache/apple-replay --install
/usr/bin/caffeinate -i .venv/bin/python -u p0/run_apple.py .model-cache/apple-replay p0/evidence/apple-repeat csj-task offsiderule silence
/usr/bin/caffeinate -i .venv/bin/python -u p0/run_apple.py .model-cache/apple-replay p0/evidence/apple-sentences-repeat csj-task offsiderule silence --sentence-finals
```

每次使用新的输出目录。目录内 `*.asr.jsonl` 是带时间戳的日文事件，`*.zh.jsonl` 是实际翻译事件，`runs.jsonl` 记录每段供流和完成状态。
