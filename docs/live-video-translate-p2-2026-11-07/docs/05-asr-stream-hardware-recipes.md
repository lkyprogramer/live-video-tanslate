# 05｜ASR、原生流式与硬件分配实验配方

> 2026-11-09执行进展：Voxtral960连续9PCM与独立Hy-MT2中文首筛已完成；实测入口为p0/p2/b3_worker.py及显式b3_vox_cache.py，不是生产backend。 [B3报告](../../p2-b3-results-2026-11-09.md)为实际证据。下述配方与原型保留设计语义；候选replay/accept/产品接入仍为PLANNED且受质量门槛约束。

本章状态：B2的SenseVoice/whisper-ja760已于2026-11-09实现并完成目标机首筛，均未晋级，见[B2报告](../../p2-b2-results-2026-11-09.md)。其余原生流式/硬件/增强路线仍是待执行规格。研究核验日期：2026-11-07。
目标是找出能改善 M1 Pro 上日语→中文字幕的有限候选；不承诺任何候选的本机毫秒数。
后续B2执行没有修改生产基线；已实现统一CLI的B2离线子集，不能把这一状态扩成Voxtral等路线已实现。

## 1. 先决事实与执行纪律

现有 Reazon 路径已经显式使用 `provider="cpu"`，MT 才使用 MLX。
因此，ASR 与 MT 抢 Metal 只能是部分 GPU 候选的待检验解释，不能概括之前所有失败。
Apple 的 CPU、ANE、GPU 共享统一内存及系统资源；拆成独立进程不等于硬件隔离。[A01][A02]
不要用参数量、MFLOP、模型文件大小、音频 token 数直接换算 M1 Pro 毫秒数或进程内存。
官方 benchmark、M5/M2 测量、社区“实时”体验，都只能形成假设，不能替代目标机数据。
已有 Index、FireRed、Kotoba 负结果默认保持关闭；只有明确的新变量才允许重开。
每个实验记录唯一 ID、父实验、唯一主要变化、模型 revision、文件散列、源码 revision、解码和提交策略。
固定音频、M0 翻译器、上下文预算、参考答案、评分准则后，再修改一个实验因素。

## 2. 两条首筛路径，使用同一语义评分集

### 2.1 离线 ASR：固定切片探针

SenseVoice、Fun-ASR Nano、Qwen CoreML 先使用已冻结的 88 个切片，保持原切片边界不变。
固定切片探针判断换模型是否改善识别及下游中文；它不代表新模型已达到实时服务条件。
先输出原始日文、ASR 元数据、M0 中文及异常，再按同一批 34 个语义项评分。
质量与资源候选均不得靠删掉失败切片、空字幕、改分母或把错误原文“修复”后再送 MT 通过。

### 2.2 原生流式：连续、因果、暂不按 1× 时钟运行

Voxtral 与 VibeVoice 必须直接使用冻结的 9 条完整 PCM 时间线，不能被 88 切片逐条重置状态。
首筛 `causal-stream` 允许尽快计算，但按固定输入步长、原有顺序逐步开放样本；一次只见已经到达的前缀。
每次调用保留 `visible_until_sample`；模型允许的 lookahead 也只能来自已开放的音频。
禁止先送整段音频后补造早期时间戳，禁止把文件模式的整段未来上下文带入在线结果。
完整保留跨句模型状态，并对同一批 34 个语义项评分；首筛的处理耗时不冒充直播延迟。
只有通过质量闸门，才进入原始时间线 1× 回放，记录真实排队、处理与显示时钟。

### 2.3 初始提交器：连续 ASR + 现有 VAD 端点

ASR 连续吃音频、保留模型状态；VAD 只提供 MT 的初始提交时机，不在每个端点重启 ASR。
保持现有 VAD 参数，先隔离“换识别模型”的影响，再研究增量提交。
端点到来时只提交已获得的稳定新增原文；解码尚未追上时，保留 pending endpoint 并记录额外等待。
没有原生词时间戳时，记录音频消费水位与提交区间，不能制造精确词时间戳或参考字幕对齐结果。
适配器必须写清延迟到达文本的归属和去重；不能把晚到的原文悄悄并入已经显示过的中文。
**v1 最终中文字幕只显示一次，不回写、不替换已显示内容。** 内部 ASR 草稿可变化，但不计作已可用中文。
增量策略必须用独立实验 ID；不能把模型、切句、MT 触发和字幕回写同时改变后宣称模型获胜。

### 2.4 进入 1× 性能验证的闸门

质量路线：同轮 MT seed 11/29 两次配对运行，每次均净增加至少 3/34，新增关键错误为 0、保护项无损失。
基线仍为 20/18 时，分别至少达到 23/21；34 项固定为 CSJ 12 + WMF 22，不是两个类别各加 3。
资源路线：同轮两次语义质量数量均不低于 B0，新增关键错误为 0、保护项无损失，才有资格测资源和实时收益。
这里的“0”是**新增关键错误为 0**，不是宣称候选已经消除基线的全部错误。
若总方案给出更严格的保留集闸门，以统一验收定义为准；不能测试后下调阈值。
不满足任一路线即停止该候选的长时性能验证，输出失败样本和原因，避免为差质量模型继续做性能工程。
相同音频范围和日文沿用原 `mt_seed_index`；新增输入按预登记范围+原文哈希派生，映射改变须同轮重跑 B0。
两次 seed 不构成 68 个独立语义样本；类别表现另分层报告，不改评分分母。

## 3. 版本冻结表

下表是调研时已核验的 revision；运行时 registry 还须填入实际下载文件的 SHA256。
模型或源码发生升级需形成新实验，不能让 `main`、`latest` 或自动拉取覆盖同一 run。

| 用途 | 仓库/工件 | 固定 revision |
|---|---|---|
| MLX ASR 实现 | `Neywa-Labs/mlx-audio` | `d52b8cde2ee28d583f173e1bc0eb424b425c11c4` |
| Fun-ASR 官方参考 | `FunAudioLLM/Fun-ASR-Nano-2512` | `272c57b82523ada6fd87095e955f8e29100979ab` |
| Fun-ASR MLX 权重 | `mlx-community/Fun-ASR-Nano-2512` | `a7bc96fceaafce39ed6748e0c0fa9a9508b67f86` |
| Fun-ASR GGUF 备选 | `cstr/funasr-nano-GGUF` | `5d4346e47c268ba91a332a148a1fd39b766408ac` |
| GGUF 实现 | `CrispStrobe/CrispASR` | `b50266000c31c7fe5710bf86587de5417b784899` |
| SenseVoice 官方参考 | `FunAudioLLM/SenseVoiceSmall` | `3847d57b6bdf2dd8875cb1508d2af43d80a16bf7` |
| SenseVoice 执行器 | `k2-fsa/sherpa-onnx` | `v2.0.1`，preflight 补 tag commit 与包散列 |
| Voxtral M1 候选 | `iris-sfg/Voxtral-Mini-4B-Realtime-2602-4bit-fp16` | `29c8d08b2d648d3c0437298a64b2a9adda0c4296` |
| VibeVoice 备选 | `microsoft/VibeVoice-ASR-Streaming-1.5B` | `4262d23d8a539a6530cf64fbd0b1751ef9a30853` |
| VibeVoice 上游实现 | `microsoft/VibeVoice` | `16fb2cb1217c9934a886e1948ffb06120caa2df5` |
| AlignAtt MLX | `QuentinFuxa/WhisperLiveKit` | `13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2` |
| AlignAtt 原实现 | `ufal/SimulStreaming` | `077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6` |
| Qwen CoreML 权重 | `aufklarer/Qwen3-ASR-CoreML` | `8c6bc87b87856930b435550e94ce47de710ce4ed` |
| Qwen CoreML 实现 | `soniqo/speech-swift` | `a57702e6f523fa36ac5532d2bdc5f35f701a052c` |

SenseVoice INT8 工件从 sherpa 官方预训练页面选定一个包，初选 `2024-07-17-int8`，固定 `model.int8.onnx` 与 `tokens.txt` 散列。[A03]
不得把上游 PyTorch 模型 revision 当成 ONNX 导出工件的散列；若选择 `2025-09-09` 包，应单独说明导出差异。
Voxtral 的社区量化与 FP16 非量化张量是运行时适配候选，不是日语量化无损证明。[A04]

## 4. `ASR-SENSEVOICE-I8`：CPU 首筛，元数据后测

**假设**：小型非自回归识别器能改善现有 CPU ASR 的质量/占用折中。
SenseVoiceSmall 支持中、粤、英、日、韩；官方速度宣传不能推出 M1 Pro 每句 15–25 ms。[A05]
sherpa 的此模型属于 `OfflineRecognizer`，不是原生持续音频状态机。[A06]

执行步骤：

1. 固定 INT8 包、CPU provider、`language="ja"`、线程数 2、greedy 解码；沿用当前 VAD 与切片。
2. smoke 覆盖日语、纯静音、音乐-only；记录错误、空结果及返回的 `lang/emotion/event`。
3. 对 88 切片运行 `fixed-cuts`，按 34 语义项评 ASR→M0 MT；**MT 首筛不接收情绪标签**。
4. 通过质量或资源路线闸门后，再用少量代表片比较 1/2/4 线程；选一个设置进入 1× 回放。
5. 记录 CPU 核当量、整进程树 RSS、整机内存与 P95，避免多线程降延迟但抢占播放/MT CPU。

停止条件：新增关键错误、语义质量门失败、音乐/静音误触发恶化，或初始化/退出不稳定。
不要在质量失败后用情绪提示“补救”ASR；情绪不能恢复未识别的内容。
额外产物：`sensevoice-metadata.jsonl`、原始文本、provider/线程实参及 ONNX Runtime 实际 provider。

### 4.1 `ASR-SENSEVOICE-EMOTION` 条件消融

只在 `ASR-SENSEVOICE-I8` 已通过后开启，使用完全相同的识别结果。
sherpa C++ 会把特殊 token 分离成 `lang/emotion/event`，Python 绑定也暴露这些字段。[A06][A07]
比较“无提示 / 实际预测情绪提示”，其余 MT 参数及上下文不变；只允许白名单、有限长度提示。
原日文正文保持不变，不把情绪标签当作人物意图、人称或省略主语的真值。
盲评语气、忠实度和无依据添加内容；标签不得泄漏到中文字幕。
若没有稳定净收益则关闭元数据到 MT 的路径，保留日志用于诊断即可。

## 5. `ASR-FUNASR-MLX`：条件性 BGM 与热词路线

**假设**：不同训练数据和语言建模能修复本项目的日语识别错误，尤其是部分噪声/专名。
触发条件：`Dnoise` 参考就绪，且既有失败样本支持 BGM/噪声/专名是有价值的独立变量；不能无条件扩池。
Nano 官方约 800M、支持中英日；MLX 实现为声学编码器、适配器和 Qwen3-0.6B 解码器。[A08][A09]
`stream_generate` 先编码已有完整音频再吐文本 token，不能注册为 `native_audio_stream=true`。[A10]
上游宣传背景音乐/歌词能力，不等于已经证明日本综艺/动画/BGM 上胜过项目基线。

执行步骤：

1. 固定 MLX revision 与模型工件；`language="ja"`、无 hotwords、确定性解码，保留现有切片与 M0。
2. 跑 `fixed-cuts`，分别看对话、讲解、长句、专名、数字、遗漏、BGM 与非语音误触发。
3. 通过首筛才准备成对干净/加 BGM 的开发片；时间线与语音相同，混音参数提前冻结。
4. 真实 BGM 片保持独立保留集；纯音乐和歌词分别标注，不能把歌词转写更多解释成更少幻觉。
5. 通过质量或资源门再和 MT 联跑；GPU 竞争由 ASR 单跑、MT 单跑、联跑差异确认。

停止条件：三语支持不能实际复现、关键遗漏恶化、同一切片反复解码失控、或资源超过统一预算。
模型家族“数千万小时训练”的宣传不构成跳过项目质量门的理由。

### 5.1 `ASR-FUNASR-HOTWORDS`

只在基本文本质量可用后开启；MLX 已核验支持 `hotwords`，并将其作为上下文提示。
它不是强制声学词图；`context` 与非空 `hotwords` 不能同时使用。[A09][A10]
比较空热词与视频预先已知术语表，禁止从参考字幕抽词回填；记录词表来源与冻结散列。
检查正确召回和无关词诱发率，尤其同音替换、错误人名与无依据补词。
GGUF 文件约 897 MB 不代表同等接口；当前 CrispASR FunASR 文档明确 hotwords 不适用、prompt hardcoded。[A11]
若另开 GGUF CPU 路线，应作为新父子实验；不能把 MLX 热词成绩与 GGUF 性能拼成一个“结果”。

## 6. `NATIVE-VOX-960`：原生音频流主实验

**假设**：持续声学/解码状态能够减少切片边界损失与重复工作，同时守住中文质量。
960 ms 是首测的模型 target delay，**不是实际 ASR 延迟或中文字幕 SLA**。
作者通用建议为480ms；其[官方FLEURS表](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)中日语错误率在240/480/960ms分别为15.17%/9.59%/6.80%。因此本方案优先验证960ms的质量，再探索更低delay；这些是作者协议的结果，不是所选MLX量化或M1日译中实测。
固定同一量化工件和参数后，先做 9 条 PCM 的 `causal-stream` 质量探针，再做 1× 回放。
初始 MT 提交使用第 2.3 节的现有 VAD 端点，不同时上线新增量提交器。

已核验的运行时契约：[A12]

- `create_streaming_session()`、`feed(samples)`、`step(max_decode_tokens=...)` 确实存在。
- `feed` 通过锁排队、不执行 MLX，允许音频 producer 与执行器分离。
- 每个模型只设一个 step consumer；不能同时对同一 session 执行多个 step。
- `step(max_decode_tokens=4)` 先摄入积压音频，再限制 decoder 步数；它不保证短 wall-clock 时间片。
- producer 传入的 PCM 若后续复用底层缓冲，适配器须定义复制/所有权，避免输入被修改。
- 默认 `max_tokens=4096` 统计**全部decoder步，包括pad token**，不是4096个可读文字token。固定实现每步1280个16k样本（80ms），约327.7秒步预算，不能覆盖10分钟控制。主配置预登记`max_tokens=8192`；约655.4秒步预算仅足以安排10分钟探针，仍须预留初始delay/EOF预算，不能把它当精确终止墙钟。达到上限必须产生结构化终止原因，不能把剩余语音删出分母。[配置常量](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/config.py#L8-L12)、[实际计数](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/streaming.py#L693-L709)。
- raw mel 与 adapter frames 存在累积路径；KV 有界不代表全部状态有界。

必须实现的观测字段：

- `visible_until_sample`、`consumed_until_sample`、`queued_audio_ms`、`oldest_pending_audio_ms`。
- `step_ingest_ms`、`step_decode_ms`、`asr_commit_ms`、`mt_queue_ms`、最终 `display_ms`。
- token budget、raw/adapter/KV 状态长度、进程树 RSS、MLX 内存及内存随时间斜率。
- EOS、token cap、异常、停止原因、未确认文本与待处理音频长度。

短 smoke 及质量门通过后，先测10分钟连续流。60分钟前必须按冻结的实际音频长度、delay和EOF余量推导总步上限，并验证raw/adapter/KV状态增长能守住内存止损；或先实现并独立评估预登记的静音轮换策略。`8192`不能直接沿用到60分钟。没有完整有界状态方案时标`blocked_long_session`，保留短测成绩，不谎称增加token cap就解决长期内存增长。
队列失控时显式失败，禁止丢弃音频、缩分母或把晚字幕回填为准时字幕。
若需要 session rotation，单独实现稳定边界、重放 overlap、未确认文本移交、去重和状态清理。
轮换前后需检查丢词/重复与瞬时内存峰值；仅提高 max_tokens 不算解决长时问题。

### 6.1 `NATIVE-VOX-240`：延迟参数消融

只在 960 ms 通过质量门且有实际延迟改善空间后运行，保持权重、输入、VAD/提交器、MT 不变。
比较 960 与 240 的同语义项质量、迟到、遗漏和新增关键错误；480 ms 仅可占用 B4 至多 3 个附加正式配置中的一个预先冻结名额，不增加独立预算。
不能因 240 配置数字更小就认为更快、更准；实际推理、排队和翻译仍可能主导总延迟。
若只让第一段草稿更快，而最终正确中文变差，标失败并保留 960 结果。

### 6.2 `NATIVE-VOX-INCREMENTAL-MT`：提交策略独立实验

只在模型/延迟配置已经选定后开启；复用同一因果 ASR 事件轨迹，先离线模拟提交策略。
定义原文稳定前缀、语义边界、最小新增内容、最大等待与 MT 队列上限，全部写进 registry。
不得使用参考语义项边界触发线上提交，也不能将暂时文字当作不可错的确认结果。
日语谓语后置使“更早有原文”不必然“更早有忠实中文”；同义保留和否定翻转须列入错误分析。
v1 每条最终中文字幕只提交一次；若策略需要回写已显示中文，当前版本直接判不兼容。
先通过相同质量门，才做 1× 联跑；MT 请求数和排队可能增加，必须与模型本身收益分开。

## 7. `NATIVE-VIBE-FALLBACK`：只作一个有条件的原生流式备选

触发条件：Voxtral 已定位接口不兼容、状态边界阻塞或资源问题，且总方案后备预算允许；不能逢质量失败自动扩池。
选择 `VibeVoice-ASR-Streaming-1.5B`；不要误用 `VibeVoice-Realtime-0.5B` TTS 或长音频离线 ASR。[A13]
官方支持日语；1.5B 是名称，HF 工件统计约 2.814B 张量参数，不能据名称计算总内存。[A14]
固定工件配置：24 kHz、每 speech frame 3200 样本、22 新帧、4 lookahead 帧。[A15]
将冻结 PCM 按其真实采样率重采样至 24 kHz；保留样本时钟映射并计入重采样成本，不得把 16 kHz 数据直接标为 24 kHz。
由此得到步长约 2.933 秒、首个完整非末段窗口约 3.467 秒；这是输入几何，不含推理/MT。
它有原生分块协议，但不应被作为亚秒字幕首选；不得随意缩为 1 秒窗口后仍宣称原配置性能。

MLX 已有 `init_streaming_state`、`encode_speech`、`streaming_generate_step` 实时窗口接口。[A16]
LM KV 在窗口间持续，encoder 按训练窗口处理；保留 lookahead 并按 chunk 步长前进，末段按协议补零。
必须使用工件 tokenizer 与 `<|text_chunk_end|>` 协议；不能退回普通 Qwen tokenizer。
原始 BF16/本地 FP16 转换需记录实际 dtype、转换命令、源/目标散列与日语一致性检查。
`max_kv_size` 默认可为空，必须检验长会话缓存增长、最大 token 与旋转行为。[A17]
走 9 PCM `causal-stream` 和相同 34 语义项；通过质量门才走 1×，初始仍使用现有 VAD 触发 MT。
仅测试一个预注册配置；如果输入几何已经不能满足可用中文目标，就停止进一步运行时优化。

## 8. `STREAM-ALIGNATT`：存在明确策略差异才重开

触发条件：旧 Whisper 试验没有覆盖同权重的 MLX AlignAtt，且该权重识别质量尚可。
若旧实验已经用同权重、同实现、同阈值失败，不以新框架名字重新测试。
AlignAtt 读取模型指定的 alignment heads，可跨多个层/头，不能概括成最后一层注意力。[A18]
已核验 Whisper 实现每帧 20 ms、默认安全区 25 帧；4–6 帧为 80–120 ms，不是正确性保证。
4 帧还用于 EOF 分支，不能把末段 flush 参数直接当在线默认；实现有注意力失准回退。
当前实现仍会重做 encoder；没有通用“省 60% 重算”或“100% 锁定”的保证。

先选一个已知 checkpoint，只比较现有策略与 AlignAtt 默认阈值，不同时更换精度、模型、MT。
WLK 当前 MLX decoder 能返回 cross-attention；现有 WhisperKit/CoreML 黑盒接口未必能提供。[A19]
首测记录 encoder 调用数、累计编码音频秒数/输入秒数、解码 token、早确认错误和最终中文。
通过质量门才尝试一个更激进阈值；最终中文不得回写，provisional 不计胜出。
CoreML encoder + MLX decoder 的 Lightning-SimulWhisper 仅在纯 MLX 路径已成立且 encoder 为瓶颈时再考虑。[A20]
作者 M2 Pro 示例或相对 PyTorch 加速不能算本用户机器收益，额外后端编译成本也要单列。

## 9. `HW-QWEN-COREML`：先证明完整路径，再比较联跑

触发条件：相应 Qwen 参考 ASR 的日语质量已具潜力，且目标机 macOS/工件依赖满足要求。
模型工件实际包含 encoder、embedding、分片 decoder；“180 MB 编码器”不是全部 ASR。[A21]
`speech-swift` 默认 encoder compute units 为 `.all`，decoder 为 `.cpuAndNeuralEngine`。[A22]
标准 `transcribe` 使用 MLXArray；要测试无 ASR Metal，需走 `transcribeWithoutMLX` 并显式设置两者 compute units。
`cpuAndNeuralEngine` 排除 CoreML GPU，但可回落 CPU，不能写“全部算子强制 ANE”。[A02]
当前路径为 batch ASR，固定 encoder 形状及 decoder 状态影响实际工作量；不是原生音频流。

步骤 1：检查 macOS 15+ 与 MLState/模型版本要求；不满足标 `BLOCKED_OS`，不为实验升级用户系统。
步骤 2：完整固定工件、Swift 代码与编译选项；同一日语输入对比参考实现，识别转换误差。
步骤 3：先跑固定 88 切片及同一语义质量门；首编译、warm-up、常驻和推理峰值分开记录。
步骤 4：用 CoreML Instrument/性能报告核验实际 CPU/ANE 分派及是否存在意外 Metal 工作。
步骤 5：和 M0 MT 联跑，比较现有 Reazon CPU+MT GPU 基线；独立进程本身不算收益。
步骤 6：错误必须变成结构化失败；不要将某些包装接口返回的错误描述字符串送进 MT。[A22]
上游 M5 Pro 英语测量只证明转换/后端存在速度与精度取舍，不能外推本项目日语 M1 Pro。[A23]
如果没有端到端 Pareto 改善，关闭该路线，即使 ASR 单跑 GPU 占用更低。

## 10. 调度归因与交付证据

对通过质量门的 GPU ASR，做 ASR 单跑、M0 单跑、并发联跑与单一有界执行器联跑。
比较 queue wait 与 compute time，确认拥塞在哪一阶段；不要先认定全部延迟都是 Metal 命令队列阻塞。
独立进程、独立线程、`mx.eval`、decode token cap 都不自动保证操作系统时间片或 GPU 抢占。
所有 replay 同视频播放负载、同热态规则、同采集链路，顺序随机或交错，保持基线邻近复测。
CPU 百分比明确每核 100% 口径；统一内存、RSS、GPU 利用率不得相互替代或相加成虚构总量。
内存/队列增长、异常、空输出和未命中语义项都保留；没有完整证据就标 `INCONCLUSIVE`。

统一CLI的B2入口已实现，须先`freeze --phase B2`及当前对照回归；其他路线仍按各任务实现，下面的Voxtral命令目前为unsupported，不能当成已跑结果：

```bash
python -m p0.p2.cli --run-dir PATH --registry PATH preflight --experiment ID
python -m p0.p2.cli --run-dir PATH --registry PATH probe --experiment ASR-SENSEVOICE-I8 --mode fixed-cuts
python -m p0.p2.cli --run-dir PATH --registry PATH probe --experiment NATIVE-VOX-960 --mode causal-stream
python -m p0.p2.cli --run-dir PATH --registry PATH score --experiment ID
```

其余 `freeze/replay/accept/report` 子命令参数由统一实现定义，不在本章臆造可执行参数。
每个实验交付 manifest、逐事件日志、34 项评分、关键错误配对表、资源曲线、状态边界结果与停止原因。
只有 `accept` 按冻结门槛通过，候选才进入默认配置讨论；单次体验、单段首字或截图不能替代验收。

## 11. 已核验的一手来源

- [A01] [Apple：M1 Pro 统一内存与硬件](https://www.apple.com/newsroom/2021/10/introducing-m1-pro-and-m1-max-the-most-powerful-chips-apple-has-ever-built/)。
- [A02] [Apple WWDC22：Core ML 性能与 compute units](https://developer.apple.com/videos/play/wwdc2022/10027/)。
- [A03] [sherpa SenseVoice 预训练工件](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)。
- [A04] [Voxtral FP16/4-bit 工件](https://huggingface.co/iris-sfg/Voxtral-Mini-4B-Realtime-2602-4bit-fp16/tree/29c8d08b2d648d3c0437298a64b2a9adda0c4296)。
- [A05] [SenseVoiceSmall 官方模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)。
- [A06] [sherpa SenseVoice 识别实现](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/offline-recognizer-sense-voice-impl.h)。
- [A07] [sherpa Python 结果字段绑定](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/python/csrc/offline-stream.cc)。
- [A08] [Fun-ASR Nano 官方模型卡](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512)。
- [A09] [MLX FunASR 接口说明](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/fun_asr_nano/README.md)。
- [A10] [MLX FunASR 实现](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/fun_asr_nano/fun_asr_nano.py)。
- [A11] [CrispASR CLI：各后端热词限制](https://github.com/CrispStrobe/CrispASR/blob/b50266000c31c7fe5710bf86587de5417b784899/docs/cli.md)。
- [A12] [Voxtral MLX session、队列与状态代码](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/streaming.py)。
- [A13] [Microsoft VibeVoice Streaming ASR 文档](https://github.com/microsoft/VibeVoice/blob/16fb2cb1217c9934a886e1948ffb06120caa2df5/docs/vibevoice-asr-streaming.md)。
- [A14] [Microsoft VibeVoice-ASR-Streaming-1.5B](https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B)；[参数与 revision API](https://huggingface.co/api/models/microsoft/VibeVoice-ASR-Streaming-1.5B)。
- [A15] [VibeVoice 固定工件 preprocessor 配置](https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B/blob/4262d23d8a539a6530cf64fbd0b1751ef9a30853/preprocessor_config.json)。
- [A16] [VibeVoice MLX live audio 接口](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/vibevoice_asr/README.md)。
- [A17] [VibeVoice MLX 状态和解码实现](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/vibevoice_asr/vibevoice_asr.py)。
- [A18] [SimulStreaming AlignAtt 实现](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming/whisper/simul_whisper/simul_whisper.py)；[参数默认值](https://github.com/ufal/SimulStreaming/blob/077ea37d5ab4ff98bc567e4507f140dc4e5d5ad6/simulstreaming_whisper.py)。
- [A19] [WhisperLiveKit MLX AlignAtt](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/simul_whisper/mlx/simul_whisper.py)。
- [A20] [Lightning-SimulWhisper 混合后端](https://github.com/altalt-org/Lightning-SimulWhisper/blob/63f08982e43e8680b24d563dcb54897b1cd87c9b/README.md)。
- [A21] [Qwen3-ASR CoreML 固定工件](https://huggingface.co/aufklarer/Qwen3-ASR-CoreML/tree/8c6bc87b87856930b435550e94ce47de710ce4ed)。
- [A22] [Qwen CoreML 执行路径与 compute units](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/CoreMLASRModel.swift)。
- [A23] [Qwen CoreML 转换与推理说明](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/inference/qwen3-asr-inference.md)。
- [A24] [Kotoba v2.2 官方说明：v2.0 加 diarization/punctuation pipeline](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2)。

## 12. 补充首筛配方：`ASR-WHISPER-JA760`

该席位是新的日语专用checkpoint，不是再跑通用Whisper small/turbo。源模型为 [efwkjn/whisper-ja-760M](https://huggingface.co/efwkjn/whisper-ja-760M/tree/c20dcc99c561d20f2fa20986bb82f3fbc1dde16c)，作者的CT2版本为 [efwkjn/faster-whisper-ja-760M](https://huggingface.co/efwkjn/faster-whisper-ja-760M/tree/341201ba3e542985d2ad3f78dafb9c1665535013)。

作者裁剪了词表并训练decoder；不是把大encoder缩成了很小的实时模型。作者说明需要CTranslate2≥4.7.1才能在faster-whisper正确处理新词表。其短音频/动漫结果只构成入选依据，不是M1结果；长音频也不保证优于turbo。

preflight固定一个满足版本要求的CTranslate2及faster-whisper wheel、源commit与文件hash。首组采用CPU、2线程、num_workers=1、int8；先调用runtime支持精度查询，不能默默回退为float32。CPU ARM支持与CUDA速度表分开：[CT2硬件说明](https://opennmt.net/CTranslate2/hardware_support.html)、[faster-whisper官方README](https://github.com/SYSTRAN/faster-whisper)。

拟新增adapter固定 `language="ja", task="transcribe", beam_size=5, temperature=0, condition_on_previous_text=False, vad_filter=False`；关闭框架内第二个VAD，原88切片逐条输入。预先冻结输出长度上限，任何截断保留为失败。返回generator必须完整迭代才算完成，不能用创建generator的时间当推理时间。

先验证tokenizer文件、特殊token与词表长度正确、输出日文与音频一致；最多5条已固定探针检查显然错误的转换。依赖不支持自定义词表标blocked_runtime，不替换回普通turbo伪称已测试本模型。随后同88段→M0→两轮34语义评分。质量不合格即停止；质量通过但CPU过高时最多开放一个明确runtime改造，计B4，不无条件扩MLX/WhisperKit/whisper.cpp全矩阵。

额外产物：tokens/vocab hash、CT2支持精度列表、实际compute_type、beam设置、原文及M0输出、完整迭代时间。

## 13. 条件音频增强配方：`AUDIO-GTCRN`

仅Dnoise就绪且噪声确为错因时运行。来源为 [GTCRN作者实现](https://github.com/Xiaobin-Rong/gtcrn)及[sherpa在线示例](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/python-api-examples/online-speech-enhancement-gtcrn.py)；从sherpa release的speech-enhancement-models选择gtcrn_simple.onnx并保存文件SHA256，动态release URL本身不是内容锁。

16k连续音频有状态增强，先以整条PCM顺序运行，再按原88段边界取增强后的对应片段；不同时更换VAD或边界。测量算法延迟、startup/flush、真实有效样本长度并保持时间映射，padding不改变真实范围。第一组始终开启增强，与不增强B0对照；自动按BGM开关属于另一个消融。

CPU1线程起步，配同Reazon和M0，评同34项并加Dnoise。干净语音、低音量词尾、辅音、重叠说话和纯音乐均检查；听感更清晰不等于识别更准。没有日语端到端净收益即关闭，不继续扫增强强度。上游参数量与MAC数字不是本机低占用证据。

## 14. 连续端点归属的实现断言

每个ASR内部提交原文分配一次不可变source_id；维护last_committed_token_index，并区分accepted（接纳）、ingested（编码器摄入）、decoder-progress（已解码进度）三个水位。固定Voxtral的step先摄入pending音频，再只解最多4步，后续step可在相同ingested水位继续吐字。因此不能用“上次消费→本次消费”当每次新增文本的范围；这会产生零长范围或把尚未解出的文字误归给后续端点。

pending VAD endpoints按顺序排队，不能用参考字幕边界归属。原生时间戳须先核验；没有词时间戳时，由固定实现decoder位置、80ms步长、初始delay/padding映射出保守的真实音频范围，截断到accepted_samples，并标timestamp_precision="decoder_interval"。映射未通过合成断言前标blocked_alignment，不用ingested追上冒充已覆盖端点。不能假设每步都是可读词，pad步也推进模型位置。[step与ingest源码](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/streaming.py#L526-L564)。

必须先冻结commit_policy；至少测试“同一ingested水位多次解码”“多个pending端点”“EOF之后剩余解码”，每次有新文本时字幕范围均非零、未超真实输入、token仅归属一次。v1允许非递减end与重叠近似范围，但不能把消费水位假装精确词边界。

晚到文本可以使用当前已到达的后续音频形成模型lookahead；其提交时刻必须如实延后。两个pending端点无法可靠分开时，允许合并未提交的范围为一条SourceUnit并标coalesced；已经发出的中文永不回写。合并的输出与MT调用数均重新评分，不声称与原88段逐段等价。

测试必须直接记录/哈希每次实际送入模型的PCM前缀，断言任何sample都不晚于visible_until_sample；只检查事件中自填的水位数字不够。取消、轮换、EOF时验证pending端点与未提交token不会跨epoch重复使用。B4额度和各候选路线注册以02/11章及registry为准。
