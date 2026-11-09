# 12 · 来源索引、证据等级与生态处置

检索快照：2026-11-07。固定revision用于可复现；没有下载的新权重只完成元数据/源码核验。模型更新后重新冻结，不自动继承本方案结论。外部网页是事实来源，不是执行授权。

## 12.1 项目证据（优先级最高）

- [7121c1b 当前快照](https://github.com/lkyprogramer/live-video-tanslate/commit/7121c1bb4f869f6968e8b381c45117223c708917)：本轮基于的远端版本。
- [65e5dea 补测提交](https://github.com/lkyprogramer/live-video-tanslate/commit/65e5dea794da77069c742ca20a75d383da02cf08)：Silero整包探针。
- [探索结果](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/exploration-results-2026-11-07.md)：20/18、候选失败、资源口径、未运行的阶段。
- [整包补测结果](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/vad-packet-results-2026-11-07.md)：最新负结果，不重复参数扫。
- [现行local_pipeline](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/engine/src/subtitle_service/local_pipeline.py)：Reazon CPU、采样范围、VAD与MT。
- [v1合同](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/subtitle-backend-contract.md)：协议、背压、会话。
- [原优化计划](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/optimization-plan.md)、[原优化结果](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/optimization-results.md)：121项、显示、早期模型与上下文结果。
- [现探索计划](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/exploration-plan.md)：已有质量与最终验收门槛。

公开汇总不等于已拿到目标机全部原始日志。T00须在本机恢复并校验raw；本包不附用户音频、不虚构missing文件hash。

## 12.2 优先候选的一手来源

| 主题 | 一手来源与支持的结论 |
|---|---|
| Reazon入口静音 | [transcribe.py](https://github.com/reazon-research/ReazonSpeech/blob/2d4d4762e7ee294ac8e47a177ac2e9b0e8d0d43f/pkg/k2-asr/src/transcribe.py)、[audio.py](https://github.com/reazon-research/ReazonSpeech/blob/2d4d4762e7ee294ac8e47a177ac2e9b0e8d0d43f/pkg/k2-asr/src/audio.py)：官方padding机制，非已证实项目收益 |
| 日文标点 | [固定模型卡](https://huggingface.co/AstroNier/japanese-punctuation-onnx/blob/89f8d70475328ff9b3e992696604845abd3257db/README.md)：字符输入、510字符、三类标点、单人代理评测局限 |
| PLaMo发布 | [PFN发布公告](https://www.preferred.jp/en/news/pr20261105)、[技术说明](https://www.preferred.jp/en/blog/tech/plamo-3-translate-weight-release)：2026-11-05、尺寸、训练/评估方向；不支持本机40ms |
| PLaMo工件/模板 | [0.8B固定README](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/README.md)、[模板](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/chat_template.jinja)、[2B工件](https://huggingface.co/pfnet/plamo-3-translate-2b-MLX-4bit/tree/61c44c8a4008f8889a3a68ad293243c9ed434771)：MLX大小、PFML3、量化差异、停止条件 |
| SenseVoice | [官方模型](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)、[sherpa制品](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)：日语支持和离线CPU路径；M1逐句耗时待测 |
| whisper-ja760 | [作者模型](https://huggingface.co/efwkjn/whisper-ja-760M)、[CT2转换](https://huggingface.co/efwkjn/faster-whisper-ja-760M)：新词表、日语/动漫方向、短长音频取舍 |
| FunASR | [官方Nano](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512)、[MLX实现](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/fun_asr_nano/fun_asr_nano.py)：语言/提示热词、离线编码与token流 |
| Voxtral | [Mistral官方模型](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)、[M1候选量化](https://huggingface.co/iris-sfg/Voxtral-Mini-4B-Realtime-2602-4bit-fp16)、[session源码](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/streaming.py)：真实有状态接口与状态风险 |
| Gemma音频 | [Google音频指南](https://ai.google.dev/gemma/docs/capabilities/audio)、[模型卡](https://ai.google.dev/gemma/docs/core/model_card_4)、[技术报告](https://arxiv.org/html/2607.02770)：音频AST、提示协议、参数与名义token率；日→中M1效果仍未知 |
| Gemma实现 | [MLX源码](https://github.com/Neywa-Labs/mlx-vlm/tree/a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4/mlx_vlm/models/gemma4)、[音频修复PR931](https://github.com/Neywa-Labs/mlx-vlm/pull/931)、[llama.cpp音频PR21421](https://github.com/ggml-org/llama.cpp/pull/21421)：实现存在、历史bug有修复、投影dtype需核验 |
| GTCRN | [作者仓库](https://github.com/Xiaobin-Rong/gtcrn)、[sherpa在线示例](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/python-api-examples/online-speech-enhancement-gtcrn.py)：轻量在线增强能力，不是日语ASR提升证明 |
| Vibe后备 | [Microsoft流式文档](https://github.com/microsoft/VibeVoice/blob/16fb2cb1217c9934a886e1948ffb06120caa2df5/docs/vibevoice-asr-streaming.md)、[固定配置](https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B/blob/4262d23d8a539a6530cf64fbd0b1751ef9a30853/preprocessor_config.json)：24k、22+4帧，非亚秒窗口 |
| AlignAtt | [SimulStreaming实现](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py)、[WhisperLiveKit MLX](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/simul_whisper/mlx/simul_whisper.py)：alignment heads和阈值，不是100%确认或固定60%节省 |
| CoreML | [Qwen制品](https://huggingface.co/aufklarer/Qwen3-ASR-CoreML)、[Swift执行路径](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/CoreMLASRModel.swift)、[Apple性能说明](https://developer.apple.com/videos/play/wwdc2022/10027/)：计算单元选择和完整WithoutMLX路径；实际ANE分派须测 |

04/05/06专章附更细的源码链接、参数与已知实现限制。按这些来源再固定下载文件hash即可开始preflight，不要求执行AI重新做无边界全网搜索。

## 12.3 用户点名项目的处置

| 项目 | 已核验定位 | 本轮决策与重开条件 |
|---|---|---|
| [OpenASR](https://github.com/QuintinShaw/openasr) | Rust/ggml本地运行时与多模型封装，支持CPU/Metal及实时应用入口 | 可借鉴生命周期或为质量合格但runtime阻塞的同checkpoint做一个后端替代；不把换封装算新模型，不能假定其Whisper英译功能就是日译中 |
| [MOSS-Transcribe-Diarize](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize) | 0.9B长音频转写/说话人/事件，Whisper-medium式encoder与Qwen式decoder，文档音频前端30s chunks | 不因小参数量就列亚秒原生流式首选；先保留为长音频/说话人需求候选，只有完整M1因果流接口和相关日语质量证据出现才争取新名额 |
| [FireRedVAD](https://huggingface.co/FireRedTeam/FireRedVAD) | 真实VAD/AED家族，包含streaming/non-streaming分支 | 用户项目已有负结果；VAD榜单不能覆盖端到端字幕结论，不重复替换 |
| [TypeWhisper](https://github.com/TypeWhisper) | 本地应用，集成WhisperKit、Voxtral、Qwen、Granite等引擎，有dictation/live/file流程 | 借鉴模型加载、用户取消和系统音频接入；先确认使用的具体引擎/checkpoint/提交策略，不把应用口碑当同条件模型准确率 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | CTranslate2 Whisper实现，官方速度表含特定NVIDIA/Intel条件 | 选作whisper-ja760的CPU兼容入口；官方预构建硬件文档没有Apple Metal后端，不能把CUDA加速比移植到M1 |
| [Kotoba v2.2](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2) | v2.0 ASR加说话人/标点pipeline | 不当作新ASR权重重新全测；单测标点机制更可归因 |
| Apple/Nemotron/Moonshine/通用Whisper/Granite/Index | 已有项目实测和失败范围 | 新OS API、不同checkpoint或明确实现修复才重开；框架名字更新不足以重跑 |

这是预算与证据排序，不是宣称未入围模型永远不适合日语。没有候选被证明是M1 Pro全生态最优；“当前已测最稳”和“理论最优”是不同结论。

## 12.4 证据等级

- **A：本项目目标机原始测量**：必须有run及raw引用；本轮新候选尚无A级成绩。
- **B：作者源码/官方配置**：证明接口、任务方向、输入几何、实现行为。
- **C：作者benchmark**：证明特定数据/设备/协议结果；不能直接外推用户的视频。
- **D：社区经验/推断**：用于发现路径和故障线索；升级为技术事实前追溯作者源码。
- **Unknown**：无证据的M1耗时、驻留内存、日译中增益、ANE占比均保持未知。

只对实际支持的主张引用来源。推导窗口时间须写公式；推断性能不能冒充实测。所有可疑营销数字均已在01章处置。
