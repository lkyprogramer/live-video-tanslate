# 本地日中实时字幕下一轮优化方案

执行后续探索、机制诊断或新模型验证时，先读 [后续执行方案](exploration-plan.md)。本文保留首轮优化实验的设计、评测口径和晋级条件。

基线为 `a3a2804` 和 D5 原速实测。目标是在这台 Apple M1 Pro、10 核 CPU、32 GiB、macOS 26.7.2 的 Mac 上，从 Chrome 标签页音频生成可读、语义正确的简体中文字幕。第一轮先用相同音频切片比较 **ReazonSpeech 与 Haas-ja ONNX**，同时用人工核对的日文测 **Hy-MT2 的翻译上限和短上下文收益**。Parakeet 日语 Core ML 紧随其后，验证另一条日语识别和资源路径。先完成短测归因，再把胜出的组合接进现有服务。原视频字幕仅作离线参考，运行时继续使用音频。[调研输入](next.md) · [D5 验收](v1-acceptance.md) · [v1 合同](subtitle-backend-contract.md)

## 1. 已确认的问题和顺序

| 本机证据 | 对下一轮的影响 |
| --- | --- |
| 17/17 段原速结束，静音零字幕，无积压中断。服务与回放平均 CPU 8.46%（100% = 一个逻辑核），物理内存峰值 1.85 GiB | 先争取语义覆盖。资源以同条件对照验证，现有结果只代表服务端回放 |
| 5 秒内正确 66/121，15 秒内正确 69/121。句末 P95 2.745 秒基于这 69 个匹配，40 个在参考句末前输出 | 90% 需要至少 109/121。即使把已确认的 3 个晚命中全移进 5 秒，也只有 69/121。其余 52 个要逐项归因 |
| 121 个单元里 CSJ 占 15 个，5 秒命中 6 个。其余 106 个来自同一场 Wikimedia 会议，命中 60 个 | 现有套件用于防回归，选型还要过来源独立的新视频 |
| D5 诊断中 331 个识别段有 203 个超过 4 秒，段长 P95 8.50 秒，最长 11.54 秒。按同机单调时钟关联，音频段末到 `recognized` 的 P95 约 0.68 秒，`recognized` 到 `translated` 约 0.44 秒 | 现有 `max_speech_duration=4` 在实测中没有形成 4 秒硬边界。两个时差分别包含多个步骤，长段内部的等待也不在其中。先补事件点再决定切哪里 |
| 对 D5 原始译文重跑现有过滤，5 条均含假名。3 条来自未纳入 121 单元的片段，另 2 条在 `wmf-01` 和 `wmf-02` | 按时间范围核对具体影响。先查正确中文为何混入假名，过滤策略单独做 A/B |
| 日文把「一句话」识成与毛发相关，把 2024 识成 2004。正确日文的「片假名」却被 Hy-MT2 译成「卡塔卡纳」 | ASR 和 MT 都有可复现问题，分别建立基线 |

表中段长、两段耗时和过滤分类来自本机保留的 `p0/evidence/v1-d5/replay/model.jsonl` 与 `resources/phases.jsonl`，属于历史记录重算。音频段末以供流锚点加 `end_sample / 16000` 估算，不能用作参考句末或真实 Chrome 上屏时间。[完整指标](../p0/evidence/v1-d5/metrics.json) · [资源数据](../p0/evidence/v1-d5/resources/resources.json) · [原始逐句判定](../p0/evidence/v1-d5/verdict.json) · [逐句样例](../p0/evidence/v1-d5/review/notes.md)

## 2. 阶段和推进条件

| 阶段 | 交付 | 进入下一阶段的条件 |
| --- | --- | --- |
| N0 评测与归因 | 冻结样本、运行清单、逐条对照和诊断事件 | 历史结果重算一致，新增 trace 能把音频、日文、中文和浏览器事件对应起来 |
| N1 单变量试验 | 同切片 ASR 对照、人工日文 MT 对照 | 至少一条路线带来可归因的收益，或确定下一轮应该解决切分和端点 |
| N2 组合与服务 | 原速 WS 对照，最多两个候选 profile | 每个入围组合有可复核的正确覆盖增益，且没有新增关键错误、静音幻觉和积压中断 |
| N3 浏览器与长稳 | 新视频盲测、真实 Chrome 上屏、60 分钟验收 | 全部发布门槛达标后才切默认配置 |

N0 完成短测部分后，N1 的 ASR 和 MT 可以独立执行。资源采样轮次串行。本文的短测晋级门槛和资源预算是建议值，执行时在第一轮对照前写入运行清单并冻结。

## 3. N0 冻结样本、评分和事件

### 样本

- **短测开发集**使用 `csj-task`（8 单元）、`csj-reproduction`（4）和 `wmf-04`（22），共 34 个可复核单元，历史 5 秒命中 19/34。再用 `offsiderule` 检查配乐，用现有四条短句和 10 秒静音做专项检查，分别报数。带配乐样本的旧辅助参考不混入这 34 个单元。
- **历史回归集**继续用原来 17 段、121 个语义单元，固定 PCM SHA-256、1×、200 ms 帧、收尾和原评分规则。完整的 121 项逐条对照保留原判定。优先复核 52 个未匹配单元、3 个 5 秒后才命中的单元，再抽查两种来源的已命中样本。
- **新视频盲测集**至少 6 个独立视频来源、60 个语义单元，覆盖双人对话、长讲解和有配乐或环境音的日语视频，每类至少两个来源，单一来源至多占参考单元的 25%。至少标 20 条独立的 1–3 秒短句，可以与 60 个单元重叠。按完整视频或节目来源分组，先冻结参考日文、语义要点、起止和否定、数字、专名、动作关系标签。对比模型、词表、切分、过滤和显示策略都用开发集。选定一个候选并冻结全部配置后才开盲测。开封后按结果改动配置时，该批素材转为回归集，发布验收另用独立来源。结果按来源和内容类型分开报。

本地 `test/` 的视频和日语 SRT 保留为一个独立盲测来源。先抽听开头、中间和结尾，校对字幕与实际语音，合并滚动重复并把有独立含义的重叠发言分别标注。只评分视频内能完整听到的语义单元，用实际语音核对句末。另建本地素材清单，按 16 kHz 单声道 s16le 提取 PCM，记录音轨、时长和 SHA-256。SRT 用作人工日文参考，中文语义单独标注。原始素材和标注保存在本机。

### 归因和指标

对同一参考单元对照实际 PCM、VAD 切片、日文输出、MT 原文、过滤结果、WS 和 DOM。先标一个有证据的首个失败环节，可附第二原因，证据不足时记 `unknown`。标签至少覆盖 `vad_missing_or_cut`、`asr`、`mt`、`filtered`、`late`、`render`。已有 `null` 判定只有在检查原始事件后才能改为具体原因。关键错误单独统计实际发生次数，错译和完全缺字幕分别报告。

用三路对照区分 ASR、切分和 MT。A 取实际 ASR 输出。B 人工转写同一段 VAD 切片中实际听到的日文，保留段尾截断和原顺序。C 用完整参考语义单元的人工日文。三路使用同一个 MT 设置。A→B 检验固定切片上的 ASR 内容错误，B→C 检验当前切分下缺失的语义和上下文。对 VAD 完全漏掉的单元单列，用原音频和人工边界复听定位。C 是翻译上限诊断，单独记账。

- **主指标**为截至参考语音句末后 2／3／5 秒的语义正确覆盖，分母始终是全部参考单元。按同一参考和同一候选输出比较语义，不以字符串相似度自动判对。允许数条连续字幕组成一个单元，WS 以最后一个必要片段首次收到为计时点。DOM 以最后一个必要片段开始实际可见为计时点，且每个必要片段都达到冻结的可读标准。建议连续可见时长至少 `max(0.6 秒, 非标点字符数 / 12 字每秒)`，缺字、被遮挡或过时回写不能计入。先在开发集复核这个口径，再随评分规则一起冻结。15 秒未匹配继续计入漏错。分别报原始首条、首条正确、首条稳定正确，短句按语音起点计时。缺失计失败。
- **延迟**保留 `p0/review.py` 的历史配对口径，报告匹配数、原始有符号时差、提前输出数，以及旧口径 P50／P95。浏览器另记首次可见和可见时长。用标注和采样范围核对提前命中，尤其要确认否定、条件和动作关系已表达完整。若试用修订，首次正确但随后被改错的一条不计为稳定正确。
- **资源**复用 `p0/monitor.py` 的 250 ms 进程树 CPU 和物理内存采样。冷启动、模型预热、热态推理分开。记录宿主 GPU/共享内存和可识别的系统辅助进程。ANE、增量功耗仅在本机有同条件可复现计数时报告，缺项写 `N/A`。整机指标按对照差值解释。

### 最小诊断改动

沿用 `subtitle-replay --diagnostics`，让本地 trace 可按 `run_id / clip / session_id / segment_id / start_sample / end_sample` 关联。回放器从 `ready` 记录 `run_id + clip → session_id`，关联字段只进本地诊断。记录音频接纳、VAD 实际放行、ASR 开始与结束、MT 入队/开始/结束、网关过滤或 WS 发出。空 ASR 和过滤事件也带时间范围与原因。生产 trace 默认关闭，原音频、转写和完整运行日志保存在 Git 忽略的本地目录。重点文件是 `local_pipeline.py`、`app.py`、`replay.py`、`p0/review.py` 和 `extension/test/chrome-smoke.mjs`。[默认管线](../engine/src/subtitle_service/local_pipeline.py) · [网关](../engine/src/subtitle_service/app.py) · [回放入口](../engine/src/subtitle_service/replay.py) · [评分脚本](../p0/review.py) · [Chrome 烟测](../extension/test/chrome-smoke.mjs)

Chrome 在同一 `session_id + seq` 下记录 WS 接收、字幕实际可见和移除，Offscreen 与内容页使用共同的浏览器时间基准。用受控媒体的播放帧记录参考语音起点和句末被越过的浏览器时间，字幕可见时间减去这个锚点就是 DOM 延迟。记录 `video.currentTime`、`playbackRate`、`epoch` 和首个实际送流样本对应的播放位置，每个新 `epoch` 重建映射。视频结束后 `currentTime` 会停在末尾，继续用浏览器时钟计入尾帧排空和字幕显示的等待。另报点击启动到首条正确字幕和 `ready` 前跳过的音频，盲测开头的语音照常纳入分母。

执行入口沿用下列命令。回放器会自己启动服务，执行前让出该配置的端口。复现现有评分需要本机保存的原始素材和日志。

```sh
uv run --project engine --frozen python p0/review.py \
  p0/evidence/v1-d5/replay --verdict p0/evidence/v1-d5/verdict.json

uv run --project engine --frozen subtitle-replay \
  --launch-config engine/config/profiles.toml --profile reazon-hy \
  --suite p0/fixtures/suite.json --output p0/evidence/n0-reazon-baseline \
  --clips csj-task csj-reproduction wmf-04 offsiderule \
  first-photo first-strangers first-europe first-slides silence --diagnostics
```

每组运行保留模型与代码 revision、依赖锁、PCM hash、切片清单、配置、采样参数和随机种子、原始输出、逐句判定、聚合指标。N0 结束时历史评分仍为 `121 / 66 / 69 / 2.745`，分别对应总数、5 秒命中、15 秒命中和旧口径 P95。

## 4. N1 单变量试验

### N1-A 日语识别

用现有 Silero 600 ms 一次性导出切片清单，含空段、最后一段和前后静音，以 `clip + start_sample + end_sample` 对齐。所有识别器读同一份 16 kHz 单声道切片，固定 MT 4-bit、prompt、采样和重试策略。每组先预热再跑同一输入顺序。先比日文原样 CER 与固定归一化后的 CER、空结果、数字/否定/专名/动作错误，再把各自日文交给同一 MT 观察中文变化。离线运行记录解码 P50/P95、RTF、CPU、物理内存，实际 5 秒覆盖留给 N2 原速实测。

| 顺序 | 候选和接法 | 验证与停止条件 |
| --- | --- | --- |
| A0 | 现有 ReazonSpeech CPU 2 线程，复用 `ReazonRecognizer.decode()` | 同切片控制组，保存原始日文和资源 |
| A1 | Haas-ja 量化 ONNX，固定 HF `7f5ab0a4b1bf39db78afd6691eeecbc4f9d42e39`。把 `encoder.quantized.onnx` 与 `encoder.data` 放同目录，加 `decoder.onnx`、`joiner.onnx`、`tokens.txt`。使用 `model_type="nemo_transducer"`、`feature_dim=128`。先在本机 sherpa-onnx 2.0.1 上做两段语音和静音解码，失败再用隔离环境验证上游测试的 1.13.6。质量比较先固定 2 线程，4 线程另记资源对照 | 本机 2.0.1 已核对 API 接受两个参数，实际兼容性待测。两条运行路径都不能稳定加载和输出日文就停止该候选，不改主工程依赖锁。作者同款芯片数据用于排序，不能当作本项目实测。[固定转换说明](https://huggingface.co/oboroge0/Haas-ja-v1.0-onnx-quantized/blob/7f5ab0a4b1bf39db78afd6691eeecbc4f9d42e39/README.md) · [原始模型](https://huggingface.co/third-intelligence/Haas-ja-v1.0) |
| A2 | Parakeet 日语 0.6B，FluidAudio `AsrModels` 的 `.tdtJa` 路径。先固定包含日语 blank ID 修复的 Swift SDK 与实际加载的权重仓库 revision，用 release 编译的小型 Swift 程序消费同一切片 | 先用已知日语检查分词、数字、短句和空结果。旧 Core ML 模型卡仍描述 CTC，SDK 的 TDT 路径实际复用前处理/encoder 并加载 TDT decoder/joint，须从加载日志核对完整资产。M2 的 JSUT 朗读成绩只供筛选。[SDK 模型映射](https://github.com/FluidInference/FluidAudio/blob/ee78635d23d52379117554ba72358b65a0612447/Sources/FluidAudio/ModelNames.swift) · [日语基准](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Benchmarks.md#tdt-japanese-asr) |

如果 A1 只能在 sherpa-onnx 1.13.6 运行，也在该环境复跑 A0。A0 无法使用同一运行时，则标记为模型与运行时的组合差异，资源差值不能单独归给模型。如果 A1 有语义收益但 CPU 预算紧张，再用**同一 Haas 模型**对照 Core ML。Metal 和更大的模型留给已经定位的问题。首轮候选的日文经同一 MT 后，离线语义命中需至少 **净恢复 3 个短测单元**，或在总命中不下降时减少关键错误，才占用 N2 原速对照名额。只改善 CER 或只提高离线吞吐的结果保留为记录。

### N1-B 日文到简中

先用 N0 的 A/B/C 三路文本测当前 Hy-MT2。在本机锁定的 profile 中，当前日中路径是文本模板，`temp=0.2`、`top_p=0.9`、`top_k=20`、`repetition_penalty=1.1`、`max_tokens=512`。锁住这些设置，先完成 4-bit 输入组织对照。用同一批人工日文评价翻译上限，再用冻结 ASR 的日文确认实用收益。运行顺序打散，固定种子，随机路径记录至少两轮并报告波动。[固定版本翻译实现](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/translation_mlx_llm_mt.py) · [Hy-MT2 官方指令示例](https://github.com/Tencent-Hunyuan/Hy-MT2#hy-mt2-translation-task-instruction-examples-chinese-english-comparison)

| 组别 | 唯一变化 |
| --- | --- |
| M0 | 现有 Hy-MT2 1.8B 4-bit，逐段翻译 |
| M1 | 用官方背景结构加入最近 1–2 个已提交日文小句，背景最多 120 个字符，只译当前小句。上下文按会话隔离，暂停重建、拖动、换源时清空 |
| M2 | 从 M0 单独试已知领域术语，先以视频简介或通用术语来源冻结词表，只给当前源句出现的词。记录是否命中术语和是否错误强制替换 |
| M3 | M1、M2 各自有净收益才组合。固定胜出的输入后，再单独比较贪心与一种官方采样配置，随后按需对照同模型 8-bit。保持同一字幕输出上限，采样变化与量化变化分别记录 |
| M4 | Apple Translation 作为独立 MT 对照。先用 `LanguageAvailability` 检查 `ja → zh-Hans`，确认已安装再用 `TranslationSession(installedSource:target:)` 做 Swift 常驻进程的短测。现有系统支持该入口，实际语对和资源待本机验证。未安装时按系统流程准备语言资源后再测。对照纯 MT 和 ASR 同时运行的延迟、正确率与占用。[Apple 初始化接口](https://developer.apple.com/documentation/translation/translationsession/init(installedsource:target:)) · [语言可用性](https://developer.apple.com/documentation/translation/languageavailability) |

实验提示词将背景、词表与当前待翻译文本明确分开。当前 `_translate_text(text_ja)` 把参数放进原模板的待翻译位置，直接把背景拼进该参数会改变目标文本。改用新模板时，先与 M0 比较同模板的空背景或空词表控制，再比较加入信息的效果。候选先在实验调用层构造 prompt，确认收益后再接入 `HyTranslator`，保留模型复用、取消和占位符停止行为。有上下文的实验按每段音频内的语句顺序运行，只打散组别顺序。冻结 ASR 日文这一组的上下文也来自同一 ASR 输出，人工日文及其上下文只用于上限诊断。词表在查看盲测标注之前定版，错误样本的正确译文仅作评审参考。检查误译、重复输出背景、数字或否定翻转和译文变长。冻结 ASR 日文上的短测至少净恢复 3 个单元，且无新增关键错误，才推进到 N2。

## 5. N2 原速组合、切分和服务集成

1. **先建立原速实验入口。** `subtitle-replay --launch-config` 启动的是现有 CLI。当前类型配置只允许 `reazon_sherpa` 与 `hy_mt2_mlx`，工厂也只识别现有后端。N1 通过后，为入围候选加最小的实验类型或 profile，接入同一 v1 服务再跑 A/B。第二个离线 ASR 需要复用时，从 `local_pipeline.py` 分开共用的切片和各自的解码调用。MT 单独胜出时只扩展 MT 配置和加载逻辑，量化变体要显式指向对应模型目录。需要其他 sherpa 版本或 Swift 的实现由服务端管理独立进程，按顺序返回带样本范围的结果，处理取消与异常退出。每轮启动全新服务进程，基准配置只启用当轮目标并把它设为临时默认。服务启动会预热默认后端，从 `reazon-hy` 切到候选会让两套模型共同驻留，不能拿这类进程做单模型内存或冷启动比较。[回放入口](../engine/src/subtitle_service/replay.py) · [预热与会话](../engine/src/subtitle_service/app.py) · [类型配置](../engine/src/subtitle_service/config.py) · [工厂](../engine/src/subtitle_service/factory.py)
2. **再合并胜出的变量。** 在短测开发集跑 `A0×M0`、胜出 ASR×`M0`、`A0`×胜出 MT、两者组合。某一侧没有胜出者时保留基线，少跑对应组合。用同样的 `subtitle-replay` 入口在 1× 下比较，避免把离线译文命中当实时结果。建议入围条件为短测 5 秒正确覆盖相对同轮基线 **至少净增 4/34**，同时报告救回和丢失的 ID、无新增关键语义错误、静音零输出、无积压中断、可用句末 P95 ≤ 3 秒。接近门槛的组合复跑一次。最多保留两个候选。若无候选达标，依据 N0 的主要失败环节进入对应试验，保持现有默认配置。
3. **切分单独验证。** 若证据指向长段内等待或断句，固定入围 ASR/MT 与 600 ms 对照，选择有复现的连续讲话样本。首个试验以约 6 秒为软目标，在音频流到达时检测低能量短停顿，找到安全边界才提前提交。没有合适边界时保留原端点处理。先离线核对词尾、否定和跨段重复，再原速测实际首条正确与 5 秒覆盖。350 ms 的旧结果保留为反例对照。若仍主要是确认等待，才比较 Moonshine Japanese Small Streaming 的原生增量，先验证持续会话、末尾 flush、空结果率和稳定小句，再用同一 MT 走 v1 最终字幕。官方日语 17.2% CER 来自关闭 VAD 的整段评估，本机优先验证早期可用字幕和语义。若硬件路径是瓶颈，再做 WhisperLiveKit `LocalAgreement + mlx-whisper` 或 WhisperKit 对照。现有 SimulStreaming 测的是 MLX encoder + PyTorch decoder，开启 full MLX 的同版路径还须复核标点后漏词。Qwen3-ASR 0.6B MLX 8-bit 留作 ASR 质量对照，跑时计入与 Hy-MT2 同时使用 GPU 的影响。[Moonshine 评估口径](https://moonshine-voice.readthedocs.io/en/stable/models/accuracy/) · [锁定版 WhisperLiveKit 解码](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/simul_whisper/backend.py) · [LocalAgreement MLX](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/local_agreement/backends.py)
4. **保留通过的 profile。** 使用 `SubtitleBackend/SubtitleSession`，维持 v1 的认证、单会话、队列上限、PCM 时钟、EOF 和简中字段。实验 profile 由设置手动切换，当前默认 `reazon-hy` 保留为回退，切回后重建会话即可。Chrome 只使用 profile ID。[后端接口](../engine/src/subtitle_service/backend.py) · [v1 合同](subtitle-backend-contract.md)

服务定向验证覆盖新增 profile 的 `ready → caption → finished`、尾帧、静音、暂停或断开取消、旧结果不回写、后端进程异常和启动后可切回默认。现有网关测试和扩展类型检查必须通过。若最终字幕链路的盲点确实来自等待定稿，且原生流式短测已证明更早给出语义稳定的中文，才设计可协商的新协议。届时按 `session/epoch + segment_id + revision` 淘汰旧稿，同段只保留最新待译稿，最终字幕顺序和过期规则明确，并单测日文句尾否定、拖动、切换与旧客户端兼容。v1 验收以最终字幕计分。

## 6. N3 Chrome 实际观看与发布门槛

先给同一份视频做 **仅播放、播放加 ASR、播放加完整字幕** 三组资源对照。仅 ASR 使用同一采音、端点和识别路径，在诊断入口跳过 MT 初始化、预热和调用。按 N2 的单目标配置分别启动干净服务与同版 Chrome，固定视频起点、播放倍率、窗口大小和预热，交错组别顺序，至少复测两轮。分别报告 Chrome、服务及子进程、可识别系统进程和整机数据。服务与子进程热态峰值物理内存建议不超过 **2.5 GiB**，或超出时先确认实际整机压力和覆盖收益。CPU 用同轮绝对值和增量，GPU 用整机差值，独立保留冷启动时长。若要测 Chrome 设置切换后的多模型驻留，单列实际使用场景。60 分钟前中后分别比较已预热进程 footprint 和延迟，不把一次回收波动判成泄漏。

先用开发集测现有 `content.ts` 的到达即覆盖和 4 秒隐藏。记录 DOM 实际可见时长、WS 正确但没看清的比例、尾字幕、是否落后视频。若出现可复现的集中到达覆盖，第一项 UI 实验做有限合并或最多 2 条的待显示队列，首条立即显示，过期和旧 `epoch` 直接丢弃。按 N0 冻结的可读标准比较 WS 和 DOM 覆盖，任何增加等待的展示策略必须证明净收益。过滤规则只有在确认某类被挡的内容仍是合格简中时才动，先用数字、纯假名、混合专名和占位符验证并记录误放率。当前 D5 未发现纯数字或纯英文被挡的样本，保留为专项用例。进入盲测前冻结显示和过滤策略。[显示实现](../extension/src/content.ts) · [过滤实现](../engine/src/subtitle_service/app.py)

60 分钟长稳另备一个超过 60 分钟的连续可播放测试文件，素材不足时提前拼成单一文件并记录时间偏移。前、中、后三个 10 分钟窗口都有语音、静音和固定参考单元。模型预热后以 1× 在同一个播放器和同一个 WS 会话连续测满 60 分钟，主动拖动、循环、换源和断开另测。全程记录已接纳与已消费 PCM 的差额和最旧待译段的等待时间，将处理积压与正常 VAD 等待分开。积压阈值随运行清单冻结，建议任一实际队列的积压连续 10 秒超过 2 秒即判失败，并保留峰值及时间范围。

| 发布检查 | 完成标准 |
| --- | --- |
| 原速语义覆盖 | 截至参考语音句末后 **5 秒**，历史回归集 **≥109/121**，新视频盲测集 **≥90%**。WS 与 DOM 独立报数，发布以实际可读 DOM 结果为准。每种内容类型列出分子和分母 |
| 延迟 | 1× 可用句末 P50 ≤1.5 秒、P95 ≤3 秒，并列覆盖率。1–3 秒短句从语音起点到首条可用中文 P90 ≤4.5 秒，盲测集中未命中项按无穷大计入分位数。真实 Chrome 同时报告从点击启动到首条字幕 |
| 静音和关键错误 | 静音零字幕。数字、否定、关键专名和动作关系用例全数复核，原本正确的保护样本保持正确 |
| 生命周期 | 真实 Chrome 跑本地受控视频和真实站点。确认暂停、连续拖动、1.5×、容器全屏、接管、SPA 换片、Worker 重启、断开恢复、EOF 和离页释放。旧字幕不回写，原声不中断 |
| 60 分钟长稳 | 对胜出 profile 实跑；同一会话无持续积压、过载、串线和原声中断。比较热态前/中/后 10 分钟的 WS 与 DOM 延迟、队列和资源。建议最终窗口相对首个热态窗口的 footprint 中位增幅不超过 `max(10%, 128 MiB)`，超出先定位缓存或泄漏并复测 |

放行后才把胜出配置设为默认并更新模型下载、实际依赖与复现记录，保留 `reazon-hy` 可切回。未达到全部门槛的候选标明失败项、实际受益场景和下一项可判定的实验，继续作为实验 profile。[原 D5 发布门槛](development-plan.md#d5-全量验收与本机交付) · [Chrome 复现入口](local-setup.md#验证命令)

第一批工作是 N0 的 34 单元短测和诊断关联、Haas 的本机加载烟测、同一批人工日文的 Hy-MT2 基线。交付 A/B 表和逐句归因，用结果确定下一轮优先改识别、翻译还是切分。
