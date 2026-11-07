> 外部研究反馈原文，保留供追溯。下文基线与候选数据属于当时的历史版本。当前执行以 [exploration-plan.md](exploration-plan.md) 为准，结论见 [本机探索结果](exploration-results-2026-11-07.md)。

**新的探索顺序应该调整为：保留 `reazon-hy` 基线，先排查 VAD 分包、旧版权重和 WhisperKit 空结果这几个具体机制；新声学模型优先筛 Granite Speech 5.0 多语种 470M，再按错误类型选择 Cohere 日语微调或 Fun-ASR-Nano。**

你的质疑成立。上一轮我对跨硬件、朗读测试集和框架宣传的结果外推过多，对模型版本、流式实现和失败路径查得不够深。你这批实测已经足以撤回 Haas、Parakeet-ja、完整 MLX 作为优先替换路线的建议。

我重新核对了远端，`main` 仍是 **`16567cb00f86ac6e131745f7b75c20a1c271cb33`**，读了两个提交中的方案、实验结果和相关实现。下面引用的本机数字来自你的报告；新找到的模型尚未在你的 M1 Pro 上运行。公开仓库目前没有收录完整实验适配器和原始记录，因此涉及适配器配置的判断，我会明确保留条件。[最新实验结果](https://github.com/lkyprogramer/live-video-tanslate/blob/16567cb00f86ac6e131745f7b75c20a1c271cb33/docs/optimization-results.md)

## 一、最新实验真正说明了什么

### 1. 当前主要矛盾已经很明确

| 最新结果 | 对下一轮探索的含义 |
|---|---|
| 固定切片、相同 MT：Reazon **20/34**；Haas **19/34**；Parakeet-ja **17/34、18/34** | 后两者未形成整体质量优势，不能继续按公开榜单优先推荐 |
| Reazon 离线 ASR P95 约 **148 ms** | 把识别计算再压低几十毫秒，难以解决首条正确中文等待数秒的问题 |
| Qwen3-ASR 0.6B **11/34**；Kotoba v2 **12/34** | 换到 GPU、更大的语音语言模型，并未改善这批实际短片 |
| Moonshine 的 5 秒覆盖 **18/34**，Reazon **19/34**；partial→final P95 **9.067 秒** | 原生流式和更早出现文字，尚未转化为更早的正确中文 |
| 完整 MLX 降低 CPU，但相同 12 单元仍只有 **4/12** 在 5 秒内正确 | 这是资源路径改善，不能当作准确性改善 |
| WhisperKit 在相同 43 个 VAD 片段中有 **19 个空结果** | 必须查清空结果发生在哪层；其较低占用同时包含少做了下游翻译的影响 |
| 仅命中时启用 MT 词表：**20/34 → 22/34**，两轮一致，未新增原先记录的回归 | 这是现有实验里值得继续验证的小幅正收益 |

以上均来自你的[固定版本实验报告](https://github.com/lkyprogramer/live-video-tanslate/blob/16567cb00f86ac6e131745f7b75c20a1c271cb33/docs/optimization-results.md)，其中离线覆盖和原速 5 秒覆盖是不同指标。

你的链路诊断还显示：没有持续积压，MT 排队很短，受控 Chrome 测试的 WS→DOM P95 只有 **13.9 ms**。目前证据不支持把主要精力继续放在队列、WebSocket 或渲染优化上。

下一轮应该重点回答：

- **语音是否完整进入 ASR？**
- **模型是否执行了解码，还是被框架提前跳过？**
- **进入 ASR 的语音，具体丢了哪些词、关系和否定？**
- **正确的日文信息，是否又被 MT 丢掉？**

### 2. “日语榜单更好”确实不能推出“视频字幕更好”

这次找到一份比较贴近问题的一手评测：作者同时测试了 **650 条 FLEURS 日语朗读**和 **1,000 条广播语音**。Qwen3-ASR 0.6B 的 CER 从朗读集 **8.63%** 变为广播集 **30.41%**；ReazonSpeech NeMo v2 则为 **7.46% / 11.00%**。这份评测本身也存在训练域优势、解码配置不完全一致等限制，但清楚展示了不同场景下排序可能明显变化。[zenn.dev](https://zenn.dev/yukobayashi500/articles/849115a7c596f7?utm_source=chatgpt.com)

因此，下一轮模型排序要优先看**你的短促对白、连续讲解、BGM 和实际切片**。公开 CER 的作用是筛候选。

---

## 二、这次找到的四个具体突破口

这些都与上一轮已经失败的“全局合并、固定 padding、普通 beam、完整 MLX”有实质区别。

### 1. 项目当前锁定的 Silero，实际上是 v4

我核对了三处：

- 项目 `local_pipeline.py` 中的 `VAD_SHA`；
- sherpa 官方 release asset 的 SHA-256；
- 官方说明该模型来源的 PR。

当前 SHA 以 `9e2449e1087496d8…` 开头，对应的是 **Silero v4 的重新导出版**。更新 sherpa 运行时，不会自动更新这个被 SHA 固定的模型文件。[项目代码](https://github.com/lkyprogramer/live-video-tanslate/blob/16567cb00f86ac6e131745f7b75c20a1c271cb33/engine/src/subtitle_service/local_pipeline.py) · [官方模型资产](https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/assets/271935959) · [确认 v4 来源的 PR #2372](https://github.com/k2-fsa/sherpa-onnx/pull/2372)

**这使 Silero v6.2 成为一个真正没有被前面实验覆盖的变量。**

v6.2 发布说明明确提到低声、特殊声线、儿童和卡通声线等边界情况的改进，与你的视频场景有相关性。不过它没有证明在你的音频上一定胜出。[GitHub](https://github.com/snakers4/silero-vad/releases/tag/v6.2?utm_source=chatgpt.com)

建议对照顺序：

1. 旧 v4，保持基线配置。
2. v6.2，保持业务端点参数。
3. FireRed **Stream-VAD**，单列其阈值与平滑配置。

先看**真实语音漏掉多少、音乐误收多少、关键短句有没有恢复**，再看端到端中文。模型升级即使找回了「開けます」，如果 MT 仍译错，也不能记作用户可用性的提升。

### 2. 200 ms 整包调用 VAD，可能影响切分边界

当前扩展每包发送 **3,200 samples，即 200 ms**；服务端将整包直接传给 `vad.accept_waveform()`。

我查了锁定版本 sherpa 的实现：它会先处理一次调用里的多个模型窗口，将各窗口的语音状态做 **逻辑 OR**，然后根据整个包处理完之后的 `buffer.Tail()` 更新段起止。源码也明确提醒不要一次传入很大的 `n`。[传输包定义](https://github.com/lkyprogramer/live-video-tanslate/blob/16567cb00f86ac6e131745f7b75c20a1c271cb33/extension/src/protocol.ts) · [sherpa 固定版本实现](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/voice-activity-detector.cc)

这意味着：**持续计算了每个模型窗口，并不保证最终段边界与分包方式无关。**

最小实验很便宜：

- 网络继续使用 200 ms 包。
- 服务端增加持续 FIFO，按 **512 samples／32 ms** 推进 VAD。
- 保存余数，正确处理末尾 flush。
- 旧 v4、阈值、600 ms 静音等待、ASR 和 MT 全部保持原样。
- 比较连续 PCM 产生的段起止、实际语音覆盖和下游结果。

它改变的是**端点状态机推进粒度**；之前的 ±320 ms padding 改变的是**切完以后送入 ASR 的音频**，实验对象不同。

目前只能确认这段实现对调用边界敏感，**还不能认定它就是 `csj-task/1` 漏句的根因**。如果只是时间戳移动，没有找回真实语音或改善切分，就停止这条路线。

### 3. WhisperKit 的 19 个空结果，存在可核验的框架退出路径

这是本轮另一条重要发现。

你使用的 WhisperKit 1.1.1，默认配置包含：

```text
windowClipTime = 1.0
noSpeechThreshold = 0.6
logProbThreshold = -1.0
```

它的主解码循环条件是：

```swift
while seek < seekClipEnd - windowPadding
```

因此，**如果实验适配器没有覆盖默认 `windowClipTime`，不超过 1 秒的输入片段可能根本不会进入主解码循环。** 另外，`SegmentSeeker` 在无语音概率较高、平均 log-prob 未超过阈值时，会直接跳过整段并返回空段结果。[默认配置](https://github.com/argmaxinc/argmax-oss-swift/blob/1bbc57cb7fe6410248a685b1c5a3c6d56adc2ed4/Sources/WhisperKit/Core/Configurations.swift) · [主解码循环](https://github.com/argmaxinc/argmax-oss-swift/blob/1bbc57cb7fe6410248a685b1c5a3c6d56adc2ed4/Sources/WhisperKit/Core/TranscribeTask.swift) · [静音跳过逻辑](https://github.com/argmaxinc/argmax-oss-swift/blob/1bbc57cb7fe6410248a685b1c5a3c6d56adc2ed4/Sources/WhisperKit/Core/Text/SegmentSeeker.swift)

下一步不需要重跑整个 Whisper 生态，只需要把这 **19 段**分成四类：

| 空结果类别 | 应记录的证据 |
|---|---|
| 根本没进入解码 | 输入长度、`windowClipTime`、decoder 调用次数 |
| 被 no-speech 规则跳过 | `noSpeechProb`、`avgLogProb`、跳过位置 |
| decoder 只产生特殊 token | 原始 token、EOS、时间戳 token |
| 后处理清空 | 原始文本、清洗文本、过滤原因 |

只有确认某一分支后，才对对应语音片段改变一个配置，并加入静音、BGM 和原先正确片段作为对照。

**这不能解释所有长片段空结果，也不是全局关闭静音防护的理由。** 它的价值是判定：当前淘汰的是 Whisper `small` 的识别能力，还是某个短片适配组合。

### 4. Reazon 的“解码期热词”仍未测试

你测过的普通 beam4 和 MT 词表，没有覆盖 **ASR 解码时的 contextual biasing**。

当前纯日语 `reazonspeech-k2-v2` 使用日语字符级词表。sherpa 2.0.1 的源码中，下面这条路径是完整的：

- `modified_beam_search`
- `modeling_unit="cjkchar"`
- `create_stream(hotwords=...)`
- context graph 进入实际 decoder 的打分流程。

我核对到了 context graph 构造与 decoder 消费它的位置。[context graph 构造](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/offline-recognizer-transducer-impl.h) · [实际解码打分](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/offline-transducer-modified-beam-search-decoder.cc)

最小实验保留三个组：

1. 当前 greedy 基线。
2. 已测过的 beam4，无热词。
3. beam4，加少量视频级、事先给定的领域词。

词表可以来自视频标题、简介或用户指定术语。先试少量 boost 档位即可，重点检查**恢复术语的同时是否向无关句子插词**。

这里还有两个限制：

- 必须相对当前 greedy 有净收益，不能只战胜已经退步的 beam4。
- 当前实现先筛 top-k，再加 context score，无法保证救回所有已被剪掉的声学候选。

这是一条低成本、针对术语错误的待验证机制，无法修复没有进入 ASR 的声音。

---

## 三、你给的五个项目，分别适合做什么

它们处在不同层次，带来的收益也不同。

| 项目 | 核实后的定位 | 对你的任务的安排 |
|---|---|---|
| **[openasr](https://github.com/QuintinShaw/openasr)** | Rust／ggml 多模型运行时，提供 CPU、Metal 和模型打包；不同模型的实时实现不同 | 作为新模型的原生执行后端候选，暂不整体替换现有服务 |
| **[MOSS-Transcribe-Diarize](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize)** | 0.9B，联合转写、说话人、时间标记和声学事件，侧重长音频、多说话人 | 保留作重叠对白、复杂长段的质量探针 |
| **[FireRedVAD](https://huggingface.co/FireRedTeam/FireRedVAD)** | 包含非流式 VAD、流式 Stream-VAD、非流式 AED | 纳入前端对照，但必须使用正确模型和连续特征处理 |
| **[TypeWhisper](https://github.com/TypeWhisper/typewhisper-mac)** | 多引擎 Mac 应用，包含录音、预览、最终提交和错误恢复 | 借鉴状态管理、错误检查，以及它实际接入的新模型 |
| **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** | Whisper 的 CTranslate2 实现 | 可作受控解码对照；没有足够理由优先迁移当前 M1 服务 |

### openasr：值得研究，但“支持实时”要追到具体 executor

它的限制文档明确区分了不同模型的实时行为。Fun-ASR-Nano、MOSS 等路线包含完整话语快照或整窗重解码；其它路线也可能通过反复解码、前缀确认和裁剪音频实现实时输出。**统一的 streaming API，并不会使这些模型获得持续缓存的声学推理能力。**

另外，当前日语强制对齐还有未移植的分词依赖，不能顺带承诺精确日语词时间戳。[GitHub](https://github.com/QuintinShaw/openasr?utm_source=chatgpt.com)

对你更合适的用法是：**让 openasr 承载一个明确的新 checkpoint，测它的输出和资源，再决定是否值得集成。**

### MOSS：参数量不大，但优化目标与你当前的主要缺口不完全一致

MOSS 开源版是 **0.9B**，使用 Whisper medium 音频编码器与 Qwen3 0.6B 风格解码器，输出带说话人和时间标记的结构化文本。官方展示的主要优势集中在长音频、多说话人和复杂场景。[GitHub](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize?utm_source=chatgpt.com)

目前没有找到足以证明它在 **M1 Pro 日语短片、持续输入、首次正确中文**上优于你基线的证据。生成过程中逐 token 回调，也不意味着音频编码器正在增量接收新声音。

如果人工核对发现主要失败来自**多人重叠和跨长段关系**，它值得做小样本质量测试；如果主要问题是短句被 VAD 丢掉，优先级就低。

### FireRedVAD：模型选择和接入方式都容易误判

官方宣传的 **97.57% F1** 对应的是**非流式 VAD**，数据集是 FLEURS-VAD-102。它不能直接作为 Stream-VAD 在日语影视中的成绩。其 binary VAD 的 voice 还包含 singing；能够检测音频事件，也不意味着能够分离背景歌声。[Hugging Face](https://huggingface.co/FireRedTeam/FireRedVAD?utm_source=chatgpt.com)

我还核到一个实际接入陷阱：官方 Python 特征提取每次创建新的 `OnlineFbank`，流式示例依靠重叠输入维持连续特征窗口。

在 16 kHz 下：

- 特征窗 400 samples，步长 160 samples。
- N 帧输入长度为 `400 + (N - 1) × 160`。
- 相邻批次需要保留 240 samples 的跨块上下文。

若直接把现有非重叠 3,200 samples 包独立送进去，每包只提取 18 帧，会丢失跨包特征窗口，并可能扭曲按帧累计的时间。[特征实现](https://github.com/FireRedTeam/FireRedVAD/blob/c30ec49e8cc69642b0ee65362eba11b9d11c6e54/fireredvad/core/audio_feat.py) · [官方分块示例](https://github.com/FireRedTeam/FireRedVAD/blob/c30ec49e8cc69642b0ee65362eba11b9d11c6e54/fireredvad/bin/stream_vad.py)

所以这里应先验证**分块连续性、幅度、状态、时间轴**，然后比较模型质量。

### TypeWhisper：有值得借鉴的机制，也有尚未完成的字幕功能

它目前专门的系统音频 Live Captions 仍是 [open 的 #1677 提案](https://github.com/TypeWhisper/typewhisper-mac/issues/1677)。已有 workaround 是系统录音加 Live Transcript 窗口，不能据此认为长时间日语视频字幕已经经过验证。

它的 WhisperKit 插件确实做了有价值的事情：检查每个分片的 `Result`，失败片串行重试一次，避免拼接阶段吞掉错误、返回貌似成功但缺段的文本。不过，这处理的是 **failure**；你的 19 个 **success-empty** 不一定会触发它。[固定版本插件实现](https://github.com/TypeWhisper/typewhisper-mac/blob/c8fe66dee3eb29af7899b54151d9ba17656bcc24/TypeWhisperPluginSDK/Plugins/WhisperKitPlugin/WhisperKitPlugin.swift)

### faster-whisper：官方 M1 路径的优势主要仍需在 CPU 上证明

当前 CTranslate2 官方硬件支持列出了 ARM64 CPU、CUDA 和 ROCm，**没有正式的 Apple Metal／MPS／ANE 后端**。因此，README 的 GPU 加速数字不能直接套到 M1 Pro。[GitHub](https://github.com/SYSTRAN/faster-whisper?utm_source=chatgpt.com)

社区存在实验性 Metal/MPS 分支，但本轮没有找到足以改变你当前选型的 M1 日语持续字幕证据。把同一 Whisper 权重换成这个 runtime，首先改变的是执行和解码实现；它不会自动补充日语视频训练数据。

---

## 四、真正值得新增的模型候选

### 推荐顺序

| 候选 | 与已测路线的实质区别 | 当前安排 |
|---|---|---|
| **Granite Speech 5.0 470M TurboCTC，多语种版** | 新的非自回归 CTC 声学模型；明确日语测试；有 Apple 路径 | **首个全新模型候选** |
| **ReazonSpeech k2-v2 ja-en** | 与当前纯日语版不同的权重、词表和训练配置；现有 sherpa 可承载 | **低成本同族对照** |
| **Cohere Transcribe 2B／日语微调 `efwkjn/cohere-asr-ja`** | 独立的专用 ASR 模型；日语微调面向 general/anime，具有针对删除错误的证据 | **质量对照，资源风险较高** |
| **Fun-ASR-Nano 800M** | 新模型家族；标准版明确支持中英日；不同于已测 Qwen3-ASR checkpoint | **第二梯队，先做日语离线筛选** |
| **Voxtral Realtime 4B** | 真正按持续音频训练的流式架构 | **保留为流式机制对照，低占用优先级较低** |

这里的排序是探索价值排序，尚不是性能排名。

### 1. Granite：本轮最值得优先验证的新模型

要锁定的是 **2026-10-21 发布的多语种更新**，不是旧英语版。当前多语种 revision 为：

`1dd23f46f6d4e6eae2531982ba2e33220086dd97`

官方日语图中，470M 模型的 CER 为：

| 数据集 | CER |
|---|---:|
| CSJ eval1 | **17.80%** |
| CommonVoice-17 JA | **8.48%** |
| FLEURS JA | **5.26%** |

这些结果证明它有明确的日语评测，**尚不能证明它胜过你当前的 Reazon**；尤其 CSJ 与 FLEURS 的差距值得关注。官方提供 `mlx-audio >= 0.5.1` 使用方式。[Hugging Face](https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc?utm_source=chatgpt.com)

另一个路径是 **`transcribe.cpp` 的 GGUF／Metal／CPU 实现**。这是这轮值得补进生态视野的运行时，不应与 whisper.cpp 或 openasr 混为一谈。[GitHub](https://github.com/handy-computer/transcribe.cpp?utm_source=chatgpt.com)

测试建议：

- 首先固定同一 checkpoint，选一个运行时完成质量筛选。
- 质量有净收益后，再比较 MLX 与原生 C++ 的持续资源。
- 第一轮不要同时做多种量化和多个 runtime 的大矩阵。
- 暂按离线 CTC 加端点处理使用；block attention 本身不足以证明原生缓存流式。

### 2. Reazon ja-en：一个容易被名称掩盖的不同模型

这轮追到了两个直接做字幕的项目：

- [hayamimi](https://github.com/oboroge0/hayamimi)
- [simul-subtitle](https://github.com/KorenKrita/simul-subtitle)

它们使用的 `reazonspeech-k2-v2-ja-en`，与当前纯日语 `reazonspeech-k2-v2` 不同。

`simul-subtitle` 作者在 M4 Pro 上报告：

- ja-en 模型文件约 **77 MB**，纯日语约 **169 MB**；
- 650 条 FLEURS CER **8.57% / 8.56%**，基本持平；
- 真实直播 141 个片段中有 92 个输出不同，抽查互有优劣；
- ja-en 偶尔把日语识成发音相近的英语。[原始测试记录](https://github.com/KorenKrita/simul-subtitle/blob/main/docs/asr.md)

这支持一个很有限但有用的假设：**它可能更轻，并在部分短促口语上形成互补。** 它没有证明平均准确率提升。

因为你已经有 sherpa 路径，这个候选的验证成本较低。但纯日语版的 `cjkchar` 热词配置不能直接套过去，它使用不同词表。

### 3. Cohere 日语微调：有新质量线索，但资源不能忽略

这里有两类证据，要分开看。

**基础模型的 M1 Pro 实测：**

TypeWhisper 维护者在 M1 Pro 16 GiB 上，用一套固定 Core ML 转换测试了日语 FLEURS 前 50 条，报告 **CER 3.71%、热态吞吐 8.18×**。同一批 APAC/MENA 多语言运行的峰值 RSS 达到 **8.16 GB**。[原始测试 #1031](https://github.com/TypeWhisper/typewhisper-mac/issues/1031)

这证明该具体路径在同款芯片上跑通过，但：

- 是文件转写吞吐，不是首条中文延迟。
- 是基础模型 Core ML，不是日语微调 GGUF。
- 其对照 Parakeet 是非日语专用的 v3，不能引用相对提升比例证明优于你的 Parakeet-ja 或 Reazon。

**日语微调的场景证据：**

`efwkjn/cohere-asr-ja` 面向 general/anime，作者报告 TEDx CER **22.0 → 12.9**、JSUT-book **29.8 → 17.5**；FLEURS 并没有同步明显改善。作者还明确撤回了受到训练污染影响的 Common Voice／Reazon 测试结果。[Hugging Face](https://huggingface.co/efwkjn/cohere-asr-ja?utm_source=chatgpt.com)

你这个场景里很值得研究的社区应用是 **[crisp-caption](https://github.com/CKwasd/crisp-caption)**：它组合了日语 Cohere、FireRedVAD 和 HyMT2。但它的公开目标是 Windows GPU；其“约 5 秒”属于目标配置描述。[GitHub](https://github.com/CKwasd/crisp-caption?utm_source=chatgpt.com)

当前使用的日语 Q6 GGUF 约 **1.98 GB**，公开量化测试来自 RTX 3080。[Hugging Face](https://huggingface.co/CKHO/cohere-asr-ja-GGUF?utm_source=chatgpt.com)

我会先让它回答一个问题：**是否能恢复你那些“输入声音完整，但 ASR 删除关键内容”的片段？** 只有这个问题得到肯定答案，才值得投入 M1 上的资源优化。

### 4. Fun-ASR-Nano：应列入候选，但两个版本必须分开

官方现在明确区分：

- **Fun-ASR-Nano-2512**：中、英、日。
- **Fun-ASR-MLT-Nano-2512**：独立的 31 语 checkpoint。

两者都约 800M，不能用其中一个的成绩为另一个背书。[Hugging Face](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512-hf?utm_source=chatgpt.com)

它值得进入第二梯队，因为是不同的模型路线，而且已有原生部署与 Mac 移植工作。但是当前“实时”包装仍需检查是否重算整窗；本轮未找到与你条件一致的 M1 日语视频端到端结果。

优先验证标准 Nano 的精确部署组合。MLT 可以留作后续同家族对照，不需要两个版本同时全面集成。

### 5. Voxtral Realtime：真流式有价值，但低延迟下的日语质量有明确代价

官方论文给出了日语分项，而不是只给多语平均数：

| 模型延迟配置 | FLEURS 日语 CER | Common Voice 日语 CER |
|---|---:|---:|
| 240 ms | 15.17% | 27.25% |
| 480 ms | 9.59% | 20.87% |
| 960 ms | 6.80% | 16.77% |
| 2,400 ms | 5.50% | 15.45% |

这是同一模型随着允许等待的音频增加，准确率变化的结果；**480 ms 不是首次正确中文字幕的端到端耗时**。[arxiv.org](https://arxiv.org/html/2602.11298v2?utm_source=chatgpt.com)

它证明了一个很实际的边界：即便采用真正的流式模型，日语质量与等待时间仍需要权衡。再考虑 4B 模型与 HyMT2 同机工作，我不会把它排在低占用探索的第一位。

---

## 五、其它生态路线为什么没有排到前面

这轮也筛了以下方向。列出它们，是为了明确哪些仍值得关注、哪些目前缺少与你需求相关的证据。

| 路线 | 本轮判断 |
|---|---|
| **SenseVoiceSmall** | 明确支持日语，非自回归，已有 ONNX 和 Core ML 路径。适合资源对照；公开朗读与广播结果存在明显差异，尚无优于你基线的目标域证据 |
| **GLM-ASR-Nano 1.5B** | 官方语言范围包含日语，不能误当成中英模型；但当前没有足够的 M1 日语视频改善证据 |
| **Dolphin** | 官方支持日语，也有本机运行时移植；保留候选，但目前缺少足以提高优先级的目标场景评测 |
| **Omnilingual CTC** | 多语覆盖很广；覆盖语言数量无法说明日语视频准确率，精确版本和部署质量仍需单独验证 |
| **OWSM-CTC v4 1B** | 有价值的开放 CTC 路线，但短片效率、持续输入与 M1 质量证据不足 |
| **Kyutai STT、X-ASR-zh-en、Canary-Qwen 2.5B** | 当前所查公开 checkpoint 的语言范围不满足日语需求 |
| **FireRedASR2** | 其 ASR 主要面向中文方言、英语；100+ 语言能力属于 VAD／LID，不能混用 |

对应的一手资料：[SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)、[GLM-ASR](https://github.com/zai-org/GLM-ASR)、[Dolphin 语言表](https://github.com/DataoceanAI/Dolphin/blob/main/languages.md)、[Omnilingual ASR](https://github.com/facebookresearch/omnilingual-asr)、[OWSM-CTC](https://huggingface.co/espnet/owsm_ctc_v4_1B)。语言排除依据来自各自官方说明。[Hugging Face](https://huggingface.co/kyutai/stt-1b-en_fr?utm_source=chatgpt.com)

还有一个部署层细节值得记住：FluidAudio 的 SenseVoice Core ML 文档明确要求其 FP16 encoder 使用 `.cpuAndNeuralEngine`；CPU/GPU FP16 路径可能产生 NaN。它公开的完整验证主要在 M5 Pro、中文和英文上，不能直接称作“M1 日语量化无损”。[GitHub](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/ASR/SenseVoice.md?utm_source=chatgpt.com)

Reddit 本轮没有补充足以改变排序的同条件一手证据。相比泛 Apple Silicon 的速度展示，GitHub 中带具体模型 revision、失败样本和实现路径的记录更有用。

---

## 六、重新制定的探索方案

### 阶段 0：补齐最小规模的归因证据

你已经把失败项映射到了 **28 个唯一 VAD 片段，约 131 秒音频**。下一步优先完成这些片段的人工日语转写，而不是继续增加大量模型运行。

这批标注需要回答四种错误：

| 类型 | 判定方式 | 后续方向 |
|---|---|---|
| 前端丢音 | 人工确认有语音，但没有进入任何 ASR 片段 | VAD 版本、推进粒度、边界 |
| ASR 删除／误听 | 送入的声音完整，日文结果缺关键内容 | 新声学模型、解码热词 |
| 运行时抑制 | 没进入解码，或被 gate／后处理跳过 | 修适配和门控 |
| MT 丢失／误译 | 日文已正确表达，中文仍错 | MT 词表、针对性语义诊断 |

同时人工核对两个已经影响判断的问题：

- 年份究竟说的是 **2004 还是 2024**。
- `wmf-04/153-155` 的真实语义结束位置。

你已经展示了仅调整参考结束时间，就能使某次 P95 从 **5.359 秒变为 1.882 秒**。这说明时间标注必须版本化保存，否则容易把评分变化误当成模型提速。[实验复核记录](https://github.com/lkyprogramer/live-video-tanslate/blob/16567cb00f86ac6e131745f7b75c20a1c271cb33/docs/optimization-results.md)

### 阶段 1：先完成四个小实验

| 实验 | 输入与控制 | 通过条件 | 停止条件 |
|---|---|---|---|
| **VAD 推进粒度** | 9 段连续 PCM；旧 v4；只改变服务端 512-sample 分窗 | 找回真实语音或改善实际切分，并转化为有效内容 | 只有时间戳移动、段数变化 |
| **v4／v6.2／FireRedStream** | 固定连续 PCM、业务端点等待；记录阈值与平滑差异 | 漏语音减少，同时控制音乐误收和中文回归 | 主要增加噪声进入 ASR，或没有恢复关键内容 |
| **WhisperKit 空结果归因** | 原有 43 片，先解释 19 个空结果 | 明确机制，单变量修正后恢复可读日文／中文 | 只是输出更多幻觉或错误文字 |
| **Reazon 解码热词** | 原有 88 片；greedy、beam4、beam4＋热词 | 相对 greedy 恢复关键语义，负例无新增插词 | 只改善术语字面、整体语义不增或退步 |

其中 **VAD 实验必须输入连续原始 PCM**；不能用预切好的语音片段来证明端点行为改善。ASR 对照则继续复用固定 88 片。

### 阶段 2：新模型只让少数候选进入原速测试

第一批做：

- **Granite 多语种 470M**：完整的离线质量筛选。
- **Reazon ja-en**：复用现有 sherpa 的低成本对照。
- **Cohere 日语微调或 Fun-ASR-Nano**：根据人工归因选择一个，先看失败片与相应成功对照。

同切片测试衡量的是“适应现有切片接口的能力”。如果某模型在短片失败、在人工完整语义窗口明显恢复，可以记录其边界敏感性；这仍需通过真实在线端点验证，不能把离线完整句成绩直接用于实时排名。

**进入完整原速回放的候选最多保留两个。** 质量筛选未通过的，不继续展开四种量化、三个 runtime 和多套端点组合。

### 阶段 3：利用已有结果评估“选择性补救”

你已有的结果存在互补：某些候选救回了 Reazon 失败项，同时又丢掉原先正确项。

这里可以先做一个**不需要新增推理**的分析：计算已有结果的 oracle union，看看如果能正确选择，最多有多少恢复空间。

随后才验证能否用运行时可见信号触发补救：

- 明确的 success-empty；
- 长语音对应异常稀疏的文本；
- 有可靠证据表明尾部语音没有被消费；
- 预先给定的领域术语存在识别歧义。

补救应有明确次数和时间预算，先保留首发路径，独立统计错误修订。**oracle 上限、文本更长、两次输出相同，都不能作为实际选择器已经有效的证据。**

Hayamimi 的 partial／final／静音后 refine 提供了实现参考，但它的修订收益和首发延迟需要分别统计，不能合并宣传。[GitHub](https://github.com/oboroge0/hayamimi?utm_source=chatgpt.com)

### 阶段 4：保留 MT 的小幅正收益，继续做独立验证

“命中术语时才启用词表”的 **22/34**，值得单列验证。

现有门槛下，它确实还没有达到离线净增 3 个单元的要求，不能直接宣布通过。我建议让这种小范围、可控影响的改动进入独立新素材验证，继续看：

- 新术语是否也有收益；
- 未命中句子是否保持原结果；
- 是否增加否定、动作关系和数字错误；
- 实际 5 秒覆盖是否增加。

同时，完整参考日文输入也只有 **28/34**，说明当前翻译设置仍存在损失。这个数字是特定输入、边界和提示词下的诊断结果，不能当作所有翻译方式的数学上限，但足以说明“ASR 更准”不保证中文自动达到目标。

### 阶段 5：用端到端收益决定保留谁

最终继续采用你的原速与盲测体系，重点放在：

1. **5 秒内正确中文覆盖率，以及漏错率。**
2. 首条正确中文、共同命中单元的延迟。
3. 错误中文暴露时间、修订次数和错误覆盖。
4. 同一完整任务下的 CPU、整机 GPU、内存与持续增长。
5. 冷启动编译、加载、热态推理分别记录。

P95 必须和覆盖率一起看。只剩少数简单句被识别出来的候选，可能具有很好看的条件 P95，却更难用。

**第一批实际投入，我建议就限定在：512-sample VAD 对照、v6.2／FireRedStream 前端对照、WhisperKit 19 个空结果归因、Reazon 解码热词，以及 Granite 多语种 470M 的离线筛选。** 这些项目分别有代码机制、精确版本变化或不同模型架构作为依据；每项都有明确的停止条件，能够让下一轮实验真正减少不确定性。
