# 实时日中字幕优化调研

本文记录 2026-11-06 开始本机对照实验前的调研。后续的实验设计见[优化方案](optimization-plan.md)。

我的优先级是：

1. **先测 Haas-ja 的量化 ONNX 版，以及 Parakeet 日语版的 Core ML 实现。**前者有同款 M1 Pro、CPU 推理、多套日语数据集的记录；后者有较完整的 Apple Silicon 日语测试。
2. **把 Moonshine 日语 Small Streaming 作为真正流式识别的独立实验。**它有降低等待时间和资源占用的潜力，但不能预先认定准确率会明显超过你现在的 ReazonSpeech。
3. **翻译侧先保留 Hy-MT2-1.8B，补上短上下文和术语，再对照 Apple Translation。**你的代码和错误样例已经说明，一部分问题发生在文本翻译阶段。
4. **Whisper 值得换正确的 Apple 运行路径重测。**你现在的测试并不是完整 MLX 推理，现有成绩不足以判定 Whisper 模型在 M1 Pro 上没有价值。

同时需要明确：**目前没有找到一个经过可信实测、能保证在 M1 Pro 上同时实现“日中视频字幕大幅更准、更快、占用更低”的现成组合。**能够确认的是，存在有价值的新候选，也存在你项目里可以定位、可以验证的改进点。

我检查了仓库 `a3a2804` 版本的实现、测试脚本、验收记录和上游依赖，并核对了 GitHub、模型卡、Reddit、Zenn 等来源。以下研究截至 **2026-11-06**；没有在你的 Mac 上重新跑模型。你的仓库记录的机器是 **M1 Pro、10 核 CPU、32 GiB 统一内存**。

---

## 一、先看项目：现在的问题不能简单归结为“M1 Pro 算力不够”

### 1. 当前默认链路的资源占用，其实已经相当低

你贴出的几张表之外，仓库里的 [D5 完整验收记录](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/docs/v1-acceptance.md)更能说明现状。

当前默认组合是：

**ReazonSpeech k2-v2 INT8、CPU 2 线程、600 ms VAD → Hy-MT2-1.8B-4bit / MLX。**

| 指标 | 仓库中的完整测试结果 |
|---|---:|
| 运行测试范围 | 17 段，合计约 29 分 45 秒 |
| 有逐句语义参考的范围 | 其中 9 段，121 个参考单元 |
| 5 秒内正确命中 | **66/121，54.5%** |
| 15 秒窗口内正确命中 | **69/121，57.0%** |
| 已成功匹配部分的句末延迟 P95 | **2.75 秒** |
| 服务与回放进程 CPU 均值 | **8.46%**，100% 代表一个逻辑核 |
| 服务与回放进程物理内存峰值 | **1,889.96 MiB，约 1.85 GiB** |
| 运行完成情况 | 17/17 完成，没有积压中断 |
| 10 秒静音 | 没有输出字幕 |

**我的判断是：这组配置在现有 1× 回放中，没有表现出持续吞吐跟不上播放的问题；但及时且语义正确的覆盖率不够。**

这不等于已经证明“识别模型、切分、翻译中谁是最大原因”。目前能确认的是，只盯着模型推理提速，不能解释或解决所有失败。

还有两个统计细节会明显影响判断：

- **2.75 秒 P95 只统计匹配成功的输出。**69 个成功匹配中，有 40 个在参考字幕结束前就出现，负延迟被截成了 0。因此这个 P95 不能单独代表整体观看体验。
- 剩下的 52 个未匹配单元，当前指标没有进一步区分“识别错了、翻译错了、漏了、还是正确输出晚于 15 秒”。不能把它们全算成错译，也不能据此断言纯提速的收益上限。

这些口径可以直接在 [评分实现 `review.py`](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/p0/review.py) 和 [完整指标](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/p0/evidence/v1-d5/metrics.json) 中核对。

你表里 Whisper small 的 **2.63 秒**和 **6.94 秒**也不是同一指标：前者是自动识别的首条中文，后者是首个命中参考语义单元的可用中文。后续选型应主要看后者与覆盖率。[项目对比记录](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/docs/p0-asr-selection.md)

### 2. Whisper 的测试路径有一个非常关键的限制

你的 P0 使用：

```text
--backend mlx-whisper
--backend-policy simulstreaming
```

但在锁定的 WhisperLiveKit 版本中，这个组合默认走的是：

**MLX encoder + PyTorch decoder。**

在你的 Mac 环境中，后半部分解码落在 CPU 路径。也就是说，指定 `mlx-whisper` 并不代表 encoder 和 decoder 都通过 MLX 执行。这是上游代码明确写出的混合模式。[锁定版本的 backend.py](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/simul_whisper/backend.py)

这会影响对 CPU 和内存数据的解释。**现有表格测到的是“模型＋这套混合运行时＋SimulStreaming 策略”的表现，不能等同于模型在完整 MLX 或 Core ML 路径下的表现。**

不过，也不能直接把 `--use-full-mlx` 打开就当成修复：同一版本的源码说明，完整 MLX decoder 默认关闭，是因为标点之后的 token 生成存在已知问题。

更合理的对照是：

- **WhisperLiveKit 的 LocalAgreement + 完整 mlx-whisper**：改动相对小，但要测重复解码带来的成本和确认等待。
- **WhisperKit / Core ML**：检验 Apple 原生运行路径。
- **whisper.cpp / Metal，必要时使用 Core ML encoder**：注意 Core ML encoder 并不等于整个模型都在 ANE 上。
- Lightning-SimulWhisper 可以研究，但成熟度和许可证需要单独考虑，后面展开。[GitHub](https://github.com/argmaxinc/WhisperKit?utm_source=chatgpt.com)

### 3. 现在的“实时性”有不少时间花在提交策略上

我在源码里确认了这些具体情况：

| 位置 | 实际行为 | 对实时字幕的影响 |
|---|---|---|
| ReazonSpeech 管线 | 使用 `OfflineRecognizer`，VAD 结束一个语音段后才解码 | 600 ms 是判停参数，不是首条字幕延迟；没有增量识别结果 |
| VAD 长段参数 | `max_speech_duration=4` 是提高判停灵敏度的触发条件 | 不是严格每 4 秒切一次，实际语音段可能更长 |
| Nemotron 包装层 | 已有 partial，但只有 final 才交给翻译 | 原生流式 ASR 的早期结果没有直接变成早期中文 |
| Nemotron 的 1.2 秒规则 | 判断“多久没有新文字”，不是“多久没有声音” | 可能在连续讲话时提前切开，也可能因持续出字而迟迟不定稿 |
| Apple 测试 | 已经使用 `SpeechAnalyzer + SpeechTranscriber` | “换用苹果新 API”不是一个尚未尝试的突破口 |

对应源码：[本地管线](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/engine/src/subtitle_service/local_pipeline.py)、[Nemotron](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/p0/nemotron.py)、[final 到翻译的门控](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/p0/run_apple.py)、[Apple 实现](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/p0/apple.swift)。

因此，**换一个能快速吐 partial 的模型，如果仍然沿用当前 final-only 翻译链路，收益可能很有限。**

也不建议继续单纯缩短静音阈值。你自己的 350 ms 与 600 ms 对照已经出现断词与语义损失，说明“更早切开”会改变识别内容，而不只是改变速度。

---

## 二、最值得加入的新模型和框架

下面是**实验优先级**，不是已经在你机器上测出的性能排名。

| 优先级 | 具体组合 | 最值得验证的方向 | 主要限制 |
|---|---|---|---|
| **第一梯队** | **Haas-ja-v1.0 量化 ONNX + sherpa-onnx** | 复用现有 Python/CPU 管线，提高日语识别质量 | 非原生流式；社区兼容层需固定版本 |
| **第一梯队** | **Parakeet 日语专用模型 + FluidAudio/Core ML** | 检验日语质量，以及把部分负载移到 ANE 的收益 | 非原生流式；M2 数据不能当成 M1 承诺 |
| **第一梯队的后端探索** | **Haas-ja Core ML / Swift** | 同一个日语模型的 ANE 运行路径 | 公开验证规模较小；没有完整实时字幕基准 |
| **第二梯队** | **Moonshine Small Streaming Japanese** | 真正增量识别、降低等待时间 | 视频口语准确率和连续流稳定性仍需验证 |
| **第二梯队** | **Qwen3-ASR-0.6B 完整 MLX，优先 8bit** | 另一类识别模型的质量对照 | 与 MLX 翻译竞争 GPU；量化和内容域影响明显 |
| **第二梯队** | **Whisper turbo / Kotoba v2.0 + 合适 Apple 后端** | 排除当前混合运行时对结果的影响 | 滚动重识别、确认等待及漏句仍可能存在 |

### 1. Haas-ja：这次检索中，最值得你新增的一项

原始模型是 [third-intelligence/Haas-ja-v1.0](https://huggingface.co/third-intelligence/Haas-ja-v1.0)，约 **615.8M 参数**，日语专用，采用 FastConformer、TDT，以及 Kimi Delta Attention 预测网络。官方实现依赖 Linux/CUDA/Triton，Mac 上需要使用社区转换。它是全上下文离线识别模型，官方 live demo 也是通过重复识别当前短语实现实时显示。[Hugging Face](https://huggingface.co/third-intelligence/Haas-ja-v1.0?utm_source=chatgpt.com)

#### 最贴近你项目的是这个量化版本

[oboroge0/Haas-ja-v1.0-onnx-quantized](https://huggingface.co/oboroge0/Haas-ja-v1.0-onnx-quantized)

转换作者公布的条件是：

**M1 Pro、CPU 4 线程、sherpa-onnx 1.13.6、逐条完整音频。**

| 日语数据集 | 样本数 | CER，越低越好 | RTF，越低越快 |
|---|---:|---:|---:|
| FLEURS test | 650 | **4.34%** | **0.039** |
| ReazonSpeech test | 1,645 | **5.54%** | **0.041** |
| SpeechBSD test | 2,093 | **5.96%** | **0.040** |

作者记录的峰值进程 RSS：最长片段不超过 12 秒时约 **0.80 GB**，19–29 秒时约 **0.97–1.12 GB**。

这组证据的价值在于：**同款芯片、真实日语、多个数据集、CPU 路径，并且你已经使用 sherpa-onnx。**但它是转换作者自测，RTF 不包含等切句和中文翻译，也没有持续 CPU 占用或功耗数据，不能据此断言比当前 ReazonSpeech 更省资源。

集成时有一个实质风险：它通过 sherpa 的 Parakeet TDT 路径适配，打包 KDA 状态，并依赖 metadata 约定。初测应固定作者验证过的版本，配置 `model_type="nemo_transducer"` 和 `feature_dim=128`，**不能只替换现有权重文件路径**。你现在的 sherpa-onnx 2.0.1 也需要重新验证兼容性。

另外，这些 CER 不能与下面 Parakeet 的 JSUT 数字直接排名：数据集、归一化和聚合口径都不同。

#### Haas 还有两条真实可用的 Apple 路径

**[Sanami/haas-ja-coreml](https://huggingface.co/Sanami/haas-ja-coreml)** 提供模型和 Swift 推理代码，encoder 使用 INT8 权重并运行于 ANE，decoder 使用 FP32 CPU。作者在 M1 Air 上报告约 85× 的离线吞吐，但验证主要是 33 条短音频和 4 条两分钟音频，测试音频未公开。

它的 encoder 是固定 15.04 秒输入窗口，**短音频可以补零，不需要等满 15 秒**；现有接口仍是整段／分块离线处理。模型文件约 591 MB 也不是运行内存。公开比较中的较小进程内存数字不包含 ANE 内存，不能拿来证明整机只占几百 MB。

**[speech.cpp](https://github.com/nyosegawa/speech.cpp) + [Haas GGUF](https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF)** 则提供 Metal 路线。一位独立开发者在 **M2、16 GB** 上，对 8 次 3–8 秒发话测得常驻服务处理时间约 **0.29–0.41 秒**，但首次加载或闲置后的首次请求仍可能需要数秒。这是完整发话收齐后的处理时间，不是第一条流式字幕延迟。[zenn.dev](https://zenn.dev/airtanker/articles/haas-ja-mac-dictation?utm_source=chatgpt.com)

**我的选择：先用 Haas ONNX 做同切分、同音频的识别质量对照。确认它在你的内容上更准之后，再比较 CPU、Core ML 和 Metal 三条运行路径。**

### 2. Parakeet 日语版 + FluidAudio：最值得对照的 Core ML 方案

这里必须选：

**[`nvidia/parakeet-tdt_ctc-0.6b-ja`](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja)**

它是日语专用模型。不要把大量社区讨论中的 Parakeet v3 欧洲语言模型、英文 EOU 模型与它混为一谈。官方日语模型使用了约 35,000 小时 ReazonSpeech v2 训练数据。[Hugging Face](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja?utm_source=chatgpt.com)

[FluidAudio](https://github.com/FluidInference/FluidAudio) 已经有对应 Core ML 路径，并公布了相对完整的日语测试：

- **Apple M2，2022，macOS 26**。
- JSUT-basic5000，完整 **5,000 条**，单个日语说话人。
- 平均逐条 CER **6.88%**。
- 平均推理耗时 **208.8 ms**。
- 平均 RTFx **28.9×**。[GitHub](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Benchmarks.md?utm_source=chatgpt.com)

这比英文吞吐宣传有参考价值，但 JSUT 主要是干净朗读，不能代表双人对话、配乐视频、动漫或长讲解。

还有两个工程注意点：

- 当前 SDK 的日语路径使用 TDT 解码；旧文档和模型卡名称中仍有 CTC 字样。应固定 SDK 与模型版本，不要混用旧 CTC 路线。
- 项目曾修复日语 blank token ID 问题，旧转换或旧实现的准确率不能直接代表当前版本。[日语修复与基准记录](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/ASR/benchmarks100.md)

**它的价值是检验日语专用识别质量，以及 Core ML 对整机资源分配的影响。**它仍然需要切分策略，ANE 也不等于整个管线没有 CPU/GPU 成本。

### 3. Moonshine 日语 Small Streaming：低延迟方向值得试，准确率需要谨慎

具体候选是：

**[`moonshine-ai/moonshine-streaming-small-ja`](https://huggingface.co/moonshine-ai/moonshine-streaming-small-ja)**，约 1.1 亿参数。

它使用增量编码，具备原生流式能力；这一点与 ReazonSpeech、Haas、Parakeet 的整段识别模式不同。[Hugging Face](https://huggingface.co/moonshine-ai/moonshine-streaming-small-ja?utm_source=chatgpt.com)

但官方日语评估也说明了限制：

- Small Streaming 的日语面板 CER 约 **17.2%**。
- 评估混合了 FLEURS 和 ReazonSpeech。
- 这些准确率数字采用整条音频、关闭 VAD 的评估方式，不能直接视为实时切分后的准确率。
- 官方低延迟数字的口径是 VAD 判定结束到最终回调，也不是“开始讲话到正确中文”。[Moonshine Voice](https://moonshine-voice.readthedocs.io/en/stable/models/accuracy/?utm_source=chatgpt.com)

我还核对了一个 [M3 Pro 上的日语独立测试](https://amanity.co.jp/news/moonshine-vs-whisper-japanese)：在小样本中接近或略好于 Whisper small，句末确认较快；但 20 条 FLEURS 在同一进程连续处理时，有 3 条返回空结果，单独处理又正常，作者没有定位根因。

所以我的定位是：

**用它测试“真正流式能否改善首条可用字幕和句末等待”，不要先把它定义为准确率升级。**

若引入它，必须同时检查日语碎行、连续处理状态、漏句，以及这些碎片交给翻译后是否反而更容易误译。

### 4. Qwen3-ASR：可作为质量对照，优先完整 MLX、避免直接从 4bit 起步

可以关注：

- [Qwen3-ASR 官方仓库](https://github.com/QwenLM/Qwen3-ASR)
- [moona3k/mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr)
- [ontypehq/mlx-swift-asr](https://github.com/ontypehq/mlx-swift-asr)

OnType 的实现确实给出了 **M1 Pro** 测试：0.6B、6bit，在 4 条约 0.98–2.56 秒音频上，推理耗时约 **0.26–0.41 秒**。但没有给出这些短样本的日语准确率，计时也排除了模型加载和 Metal 预热。[GitHub](https://github.com/ontypehq/mlx-swift-asr?utm_source=chatgpt.com)

我建议首先把 **0.6B 8bit** 作为质量对照，而不是默认追求 4bit。一份实现作者的历史 FLEURS 测试中，日语 CER 从 8bit 的 **8.64%**变成 4bit 的 **16.11%**。这不能推广为所有量化都如此，但足以说明日语识别不能默认“4bit 基本无损”。[Soniqo](https://soniqo.audio/benchmarks?utm_source=chatgpt.com)

需要注意：

- MLX 路径主要使用 Metal GPU，会与 Hy-MT2 翻译竞争资源。
- 某些“流式”实现是维护窗口、重新识别并回退前缀，并不等同于原生因果流式模型。
- 1.7B 可以作为质量上限对照，但不要预先把它列为低占用默认方案。

更值得警惕的是内容域。一份公开的日语测试中，Qwen 在朗读数据上很好，在广播音频上的退化明显大于 Parakeet 日语版和 Kotoba。该测试使用 T4、解码配置并不完全统一，作者对 Parakeet 训练数据的标签还有错误，因此不能当绝对榜单；它仍然提醒我们：**朗读成绩不能代替你的视频内容。**[zenn.dev](https://zenn.dev/yukobayashi500/articles/849115a7c596f7?utm_source=chatgpt.com)

### 5. Whisper / Kotoba / Lightning：保留，但要选择正确的实验目的

**Kotoba-whisper-v2.0** 是值得测的日语专用蒸馏模型。官方结果显示，它在部分日语广播数据上优于原始 large-v3，但在另外一些日语数据集上并没有全面超过 large-v3。不能把它理解成所有日语场景都更准。[Hugging Face](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0?utm_source=chatgpt.com)

低延迟实验优先使用裸的 v2.0。**v2.2 主要增加说话人分离和标点等处理，并非新的声学模型升级。**[Hugging Face](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2?utm_source=chatgpt.com)

[Lightning-SimulWhisper](https://github.com/altalt-org/Lightning-SimulWhisper) 的价值是：它确实实现了 **Core ML encoder + MLX decoder**，能避开你当前的 PyTorch CPU decoder 路径。作者在 Reddit 上报告 M2 MacBook Pro 相比原 PyTorch 实现有明显加速，同时明确称其为 beta。[reddit.com](https://www.reddit.com/r/Applesilicon/comments/1o99rbo/lightningsimulwhisper_a_realtime_speech/?utm_source=chatgpt.com)

不过它不能直接作为成熟替代品：

- 有一个仍开放的 [M1 静音后漏词 issue](https://github.com/altalt-org/Lightning-SimulWhisper/issues/4)。
- 没有足够日语准确率和持续功耗数据。
- 当前仓库许可证仍是 [PolyForm Noncommercial](https://github.com/altalt-org/Lightning-SimulWhisper/blob/63f08982e43e8680b24d563dcb54897b1cd87c9b/LICENCE.txt)。
- README 的“无 PyTorch 依赖”与依赖文件并不完全一致；主解码使用 MLX，也不等于整个项目彻底没有 PyTorch。

**我会先用完整 MLX / WhisperKit 做清晰的运行时对照，Lightning 放在后面研究。**

---

## 三、翻译侧：你已经用了合适的小型专用模型，但没有充分利用它

### 1. 现在并不是旧 HY-MT，也不是通用小 Qwen

当前使用的是：

**`mlx-community/Hy-MT2-1.8B-4bit`。**

在你的调用方式中，每个识别段独立传给 `_translate_text(text_ja)`。我核对了锁定的上游实现，构造的是当前文本的单次请求，没有带入前几句源文或术语表。[项目调用处](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/engine/src/subtitle_service/local_pipeline.py) · [上游翻译实现](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/translation/translation_mlx_llm_mt.py)

你的记录里已经出现两类不同错误：

- **日语转写已经错了**，后面的翻译很难可靠恢复原意。
- **日语转写正确，但中文翻译仍错**，例如 `レミはカタカナです` 被翻成“雷米是卡塔卡纳”。

第二类说明，继续换 ASR 并不能解决全部问题。[错误记录](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/docs/p0-asr-selection.md)

Hy-MT2 官方本身支持术语、背景信息和格式指令。你可以在保持模型不变的情况下，先验证输入组织是否足够。[Tencent-Hunyuan/Hy-MT2 · GitHub](https://github.com/Tencent-Hunyuan/Hy-MT2?utm_source=chatgpt.com)

我建议从这些有限改动开始：

- 带入最近 **1–2 个完整日语小句**，明确它们只作为背景。
- 只翻译当前稳定小句，避免模型重复翻译上下文。
- 提供与当前视频相关的小型术语表，例如专业名词、人物名、产品名。
- 优先保存源语言上下文，避免上一条错误中文不断影响下一条。
- 不要把字幕测试集的正确答案直接塞进词表，否则只能得到虚假的评测提升。

### 2. 先做同模型 A/B，再判断是否需要更大或更高精度的翻译模型

建议把变量拆开：

| 实验 | 保持不变 | 改变内容 | 回答的问题 |
|---|---|---|---|
| A | 当前所有设置 | 无 | 现有基线 |
| B | 同一 4bit 模型、同采样参数 | 短上下文＋术语 | 输入组织能改善多少 |
| C | B 的输入 | 贪心与官方采样分别测试 | 随机性、重复和语义质量的取舍 |
| D | 胜出的输入与参数 | 换同模型 8bit | 4bit 是否造成可见质量损失 |
| E | 同一批正确日语 | Apple Translation | 另一种资源布局和翻译能力 |

官方推荐采样参数与你当前配置不同，但不能据此认定官方参数一定更适合实时字幕；同样，贪心更稳定也不等于一定更准确。官方给出的长输出上限，不应直接照搬为字幕默认值。[Tencent-Hunyuan/Hy-MT2 · GitHub](https://github.com/Tencent-Hunyuan/Hy-MT2?utm_source=chatgpt.com)

**最关键的实验是“人工确认正确的日语 → 中文”。**先测清文本翻译的上限，再去判断 ASR 换模型值不值得。

### 3. Apple Translation 值得测，与你已经试过的 Apple ASR 是两回事

Apple 的 `TranslationSession` 是设备端文本翻译，模型资源由系统管理并可跨应用共享。它值得作为减少自带 MLX 翻译模型负担的候选。[Apple Developer](https://developer.apple.com/videos/play/wwdc2024/10117/?utm_source=chatgpt.com)

你的系统已经是 macOS 26，具备使用 [`TranslationSession(installedSource:target:)`](https://developer.apple.com/documentation/translation/translationsession/init(installedsource:target:)) 的系统条件。这个直接初始化接口要求语言资源已安装；未安装时不能靠它自行弹出下载流程。

适合你项目的接法是沿用现有 Swift 辅助进程模式：

- 启动时查询 `ja → zh-Hans` 的可用性与安装状态。
- 提前完成模型下载和预热。
- 常驻会话，通过 JSONL 或本地 socket 接收文本、返回译文。
- 在 ASR 并跑时测量资源和延迟。

**不能预先承诺它比 Hy-MT2 更准确，也没有官方证据保证它固定走 ANE。**但这是很值得做的一次纯 MT 对照。

### 4. 不建议优先投入的翻译路线

- **NLLB-200-distilled-600M** 可以做 CPU 翻译资源对照，但没有证据证明它的日中视频口语翻译优于你当前的 Hy-MT2；模型卡也明确偏研究和单句翻译用途，许可为 CC-BY-NC。[Hugging Face](https://huggingface.co/facebook/nllb-200-distilled-600M?utm_source=chatgpt.com)
- **TranslateGemma 4B** 可以作为质量参照，但它的规模并不符合你当前优先降低占用的方向，未找到可信的 M1 Pro 日中实时优势。[Hugging Face](https://huggingface.co/google/translategemma-4b-it?utm_source=chatgpt.com)
- **SeamlessStreaming** 虽然是真正的多语言流式翻译模型，但约 2.5B，官方明确不建议 CPU 推理；没有找到成熟可信的 M1 Pro 日中低占用方案。[Hugging Face](https://huggingface.co/facebook/seamless-streaming?utm_source=chatgpt.com)
- **Hibiki** 有 Apple 运行实现，但公开现成模型的法语到英语方向不匹配你的需求。[GitHub](https://github.com/kyutai-labs/hibiki?utm_source=chatgpt.com)

另外，上游 WhisperLiveKit 的同时翻译校准不能直接套用。当前公开文档里的校准是 **Hy-MT2-1.8B-8bit、英→中**，要求模型、revision、量化、语言方向和 prompt 都一致；你的 **4bit、日→中**不能直接复用。[校准文档](https://github.com/QuentinFuxa/WhisperLiveKit/blob/main/docs/simul_mt_calibration.md)

---

## 四、要真正降低字幕延迟，还需要调整“什么时间翻译、什么时间显示”

以下是基于源码和模型机制的工程建议，收益需要通过下一轮测试确认。

### 1. 有可用日语字幕轨时，优先绕过 ASR

如果视频已经提供可访问的日语字幕 cue，直接翻译它们，可以省去声学识别和切句等待。对预录视频，还可以提前翻译后续 cue，再按播放时间展示。

HTML 有标准 `textTracks`、`cues`、`activeCues` 接口，但具体站点可能使用自定义播放器，不能假定所有视频都能从标准接口拿到字幕。[html.spec.whatwg.org](https://html.spec.whatwg.org/dev/media.html?utm_source=chatgpt.com)

这条分支对有原字幕的视频，是最直接的低占用方案。

### 2. 真正利用流式 ASR，需要允许有限修订

当前 v1 协议只有最终 `caption`，没有段落 ID、修订号和暂定状态。[协议](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/docs/subtitle-backend-contract.md)

接入原生流式模型后，第二阶段可以增加：

```text
segment_id
revision
status: provisional | final
audio_start / audio_end
text
```

同时控制翻译频率：

- 同一个未结束小句，只保留最新待翻译草稿。
- 正在执行的旧请求返回后，用 revision 判断是否仍然有效。
- 已提交的最终字幕按顺序保留。
- ASR 每出几个 token，不必立即启动一次完整 MT。

这里有一个日语特有的现实约束：**识别前缀稳定，不代表翻译语义已经稳定。**

例如「この設定は有効にしないでください」，否定信息靠后。如果在听到「有効に」时就提交“请启用这个设置”，首字很快，但含义相反。因此要分别测量“首次出字”和“首次语义正确、后续不再改变”。

### 3. 输出过滤和显示逻辑也要纳入覆盖率检查

你的 [`chinese()`](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/engine/src/subtitle_service/app.py) 会拒绝没有汉字的文本，以及包含任何假名的文本。这可能误杀：

- 只有数字的有效字幕；
- 英文产品名；
- 中文句子中保留的日文专名。

全量记录到 5 次输出过滤，**现有证据不足以把它认定为 52 个未命中单元的主因**，而且两者计数单位不同。应把这些事件按音频范围关联检查，再调整规则。

显示侧的 [`content.ts`](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/extension/src/content.ts) 是新字幕到达就覆盖，并在固定约 4 秒后隐藏。如果后端集中返回几条字幕，前几条可能只短暂闪过。

所以最终验收要看 **DOM 实际上屏时间、可见时长和覆盖情况**，不能只看 WebSocket 收到了几条正确文本。

---

## 五、下一轮应该怎样测，才能最快找出有效组合

我建议按下面的顺序推进，避免同时换模型、prompt、VAD 和协议后无法解释收益。

### 第一步：给现有失败做归因，先定位值得改的部分

利用现有参考集，分别回答：

1. **人工正确日语交给当前 MT，会错多少？**
2. **当前 ASR 输出中，数字、否定、专名和动作关系错多少？**
3. **完整语句能识别正确，按当前 VAD 切开后是否变错？**
4. **已经正确生成的中文，有多少被过滤或在 UI 中快速覆盖？**

现在的 trace 还不能完整拆开 VAD、ASR、翻译排队和翻译执行时间。建议补齐这些时间点：

```text
语音结束 → VAD 放行 → ASR 开始/结束
→ MT 入队/开始/结束 → WebSocket 发送 → DOM 上屏
```

这样才能知道下一步应该减少哪一段等待。

### 第二步：第一批只比较三组 ASR

**ReazonSpeech 600 ms 基线、Haas-ja ONNX、Parakeet-ja Core ML。**

先固定：

- 相同音频；
- 相同分段；
- 相同 MT；
- 相同评分规则；
- 相同预热条件。

先看日语识别和最终中文覆盖率。Haas 如果不能在你的片段上带来明显质量收益，就不值得立刻投入多个后端的移植。

然后再让 Moonshine 参加“原生流式”实验，让 Qwen 和正确 Apple 后端的 Whisper 参加补充对照。这样每组实验都有明确问题。

### 第三步：只让胜出的两个组合进入完整观看测试

继续使用已有 17 段回归集，同时增加未参与调参的真实视频，最后做持续播放测试。

需要特别补齐当前评测范围的不足：121 个语义参考单元只覆盖 9 段，而且内容分布不均衡；不能把这组结果直接推广到所有视频。

建议保留这些指标：

| 维度 | 必须报告的结果 |
|---|---|
| 正确性与实时性 | **2/3/5 秒内语义正确覆盖率** |
| 延迟 | 首次中文、首次正确中文、最终稳定中文分别统计 |
| 关键错误 | 否定、数字、人物、动作关系错误单独计数 |
| 流式稳定性 | 漏句、重复、修订次数、静音假字幕、音频积压秒数 |
| 可读性 | 字幕实际可见时长、突发覆盖、过时结果回写 |
| 资源 | 进程 CPU、物理内存、系统服务/XPC、GPU/ANE、增量功耗、持续运行变化 |

资源测试也应重新控制条件。你当前 D5 的浏览器最终测试发生在资源监控之后，因此那组资源数字不包含同一窗口内的真实 Chrome 播放；整机 GPU 运行均值甚至低于空闲均值，也说明不能直接把它当成模型专属占用。[验收与资源口径](https://github.com/lkyprogramer/live-video-tanslate/blob/a3a2804e8ff44338c43c71d8a4e9e162a8783678/docs/v1-acceptance.md)

应该做相同视频、相同播放位置下的成对比较：只播放、播放加 ASR、播放加完整翻译链路。

**第一项具体工作，我会选 Haas-ja 量化 ONNX 的同片段质量 A/B，并同时用正确日语建立 Hy-MT2 的纯翻译基线。**这两项能够最快判断：新的日语模型能救回多少内容，以及现有翻译模型还有多少可挖掘空间。

随后用 **Haas Core ML 与 Parakeet Core ML**比较原生运行成本，用 **Moonshine**验证增量流式收益。你的原目标“5 秒内可用覆盖率达到 90%”应继续作为主要门槛；只有在覆盖率合格的前提下，较低的 P95 和资源数字才有实际意义。
