本轮扩展检索确认：ASR 生态仍有实质进展，前一轮候选集确有遗漏，不能据此前的失败实验断言 M1 Pro 上只剩小修小补。最值得补入的是 Confucius4-R2T2 的稳定前缀后训练，以及 123M 日语原生缓存流式 RNNT/CTC。两者改变了低延迟识别的训练或计算方式，不只是把已有离线模型换一个包装。

但“模型在低延迟条件下进步”“Mac 上有实现”“本项目端到端胜出”是三个不同结论。本轮确认了前两者中的若干新路径，尚未产生新的 M1 Pro 产品胜者。本报告更新候选优先级，不改写已经完成的项目实验结果。

# 项目约束与判断边界

2026年11月10日重新请求 GitHub，main 仍为 [77e24c3](https://github.com/lkyprogramer/live-video-tanslate/commit/77e24c3e490a11721041a40ad4bd203f51f84039)，提交时间为北京时间2026年11月9日21时30分30秒。既有 ReazonSpeech 加 Hy-MT2 的默认路径保留。此前的 Haas、PengChengStarling、Moonshine、Whisper 等实验不能因为再次被搜索命中而重算为“新发现”。

目标仍是 M1 Pro 上低占用、当前播放音频的日语识别与简体中文翻译。只使用已经到达的音频；不通过原字幕、OCR、未来音频预取、云服务或另一台 GPU 改变问题。最终上屏的字幕不可回写，模型内部隐藏少量不稳定 token 则不违反这一要求。约2.5 GiB 是当前默认路径的低驻留目标，不是这台32 GiB机器的物理内存上限。

本轮是模型卡、论文、源码和已有实验的只读复核，没有在用户的 M1 Pro 上安装或运行新模型。尚待人工听审的测试材料也没有因此变成经过人工验证的参考答案。

# 优先补查的稳定前缀训练路线

## Confucius4-R2T2 改变的是何时提交文字

[Confucius4-R2T2 模型卡](https://huggingface.co/netease-youdao/Confucius4-R2T2)、[官方代码](https://github.com/netease-youdao/Confucius4-R2T2)与[论文](https://arxiv.org/html/2611.06043v1)在2026年11月发布。它以 Qwen3-ASR-1.7B 为基础，冻结音频编码器与投影层，通过最长稳定前缀 LSP、Wait 决策、监督微调及 GRPO 后训练，让解码器学习在不完整音频下哪些文字可以提交，哪些内容应该继续等待。

这不是原生因果音频编码器，也不是普通 Qwen3-ASR 换成160毫秒切片。其价值是专门改善“小块音频、稳定输出”的质量损失。训练使用中英文，日语成绩属于跨语言迁移，不能称为日语专精训练。

官方 FLEURS 日语 CER 如下，越低越好：

| 配置 | 日语 CER |
| --- | ---: |
| 基础 Qwen3-ASR-1.7B 离线 | 5.42% |
| 基础模型 2秒块 U5 允许可见尾部修订 | 7.23% |
| 基础模型 160毫秒块 U1 | 36.07% |
| R2T2 160毫秒块 U0 | 10.15% |
| R2T2 160毫秒块 U1 | 8.66% |

关键比较是同为160毫秒块时的36.07%与8.66%，说明后训练带来了远大于普通推理微调的改善。但8.66%仍差于基础模型离线5.42%和2秒可修订配置7.23%，更没有与本项目 Reazon 基线直接比较。160毫秒是输入块长，不是推理耗时、首条可用中文延迟或句末延迟。

U1 会把一个临时 token 留在内部，U0与U1对外提交的日文前缀均不可修改。适配时应消费第二返回值中的已定稿文本，而不是直接显示可变的 state.text。但稳定日文不等于稳定中文：MT还必须仅消费已提交原文，并独立保证中文的追加提交不回写。论文中的回溯发射延迟也不包含完整推理、排队和翻译开销，且依赖匹配成功的识别单元，不能替代本项目包含漏译的用户指标。

## Mac 源码复核揭示了部署差距

复核固定到官方 [72b81be](https://github.com/netease-youdao/Confucius4-R2T2/commit/72b81be7070095410b75ed2e11998e347f655218)。项目确实有完整 stream_llama 路径，包含音频编码、投影和文字解码，不只是 GGUF 文字解码器。其固定 llama.cpp 版本包含 Metal，所以源码层面不能判为“只能 CUDA”；但发行的预编译组件面向 Linux x86_64、CUDA 和 CPython3.12，不能直接在 Mac arm64 使用。

| 源码核查项 | 对 M1 Pro 的实际影响 |
| --- | --- |
| pybind11 原生扩展与 Linux 导向的库打包 | 需要 arm64 原生构建、动态库与 RPATH 适配；不是换一个 Metal 参数就结束 |
| 共享流式控制器在指定 max_new_tokens 时导入 vLLM SamplingParams | 全 llama 路径仍需解耦 vLLM 参数与安装依赖 |
| native_ext.cpp 的 generate_once 每次调用 llama_memory_clear，再编码本次传入音频 | 当前 GGUF 实现没有实现论文所讨论的全部高效缓存路径，短块频繁调用可能仍然很贵 |
| 标准 streaming_transcribe 累积当前 state 的音频 | 官方单 WAV 示例从文件起点累计；不能误称每个用户会话必然无限累计 |
| 另有 streaming_transcribe_no_reset | 采用16秒阈值丢最早8秒的窗口，不是无界；文本对齐逻辑硬编码首块320毫秒、后续160毫秒，修改块长需要一起核验 |
| hybrid 编码器依赖 vLLM 原生实现、分布式配置及 CUDA 默认设置 | 不可把 device 改为 mps 就视为完成适配，优先级低于全 llama 小样验证 |

来源：[流式控制器](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2/r2t2_asr.py)、[原生实现](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/native_ext.cpp)、[构建说明](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/README.md)。

官方 [GGUF](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF) 的 Q4_K_M 解码器约1.0 GiB，另加约0.3 GiB的 Q8音频投影包。这是权重文件大小，不能推出加上 Hy、KV和运行时后仍低于2.5 GiB。当前较大的默认上下文与 batch 配置也不适合直接用于低驻留判断。代码是 Apache-2.0，权重采用 NetEase 自定义模型许可。

判断：这是本轮最值得增加的小规模可行性验证，不是已经可以替换默认模型的 Mac 成品。先确认构建、导入、加载、短日语数值与文字一致性，再决定是否值得投入缓存优化。若一开始就需要大改音频编码图或移植 hybrid，停止扩大投入。

# 优先补查的日语原生缓存流式模型

[parakeet-streaming-ja-fully-open](https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open) 发布了 M1-6.nemo 与 M1-4.nemo 权重，属于约123M的日语 FastConformer 混合 RNNT/CTC 模型。[原始 Interspeech 2026 论文](https://www.isca-archive.org/interspeech_2026/yang26r_interspeech.pdf)和[作者代码](https://github.com/yoinncyan/my-interspeech-2026)均可访问。

它不是此前测试的0.6B日语 Parakeet TDT，也不是 Moonshine。M1-6 是论文中的模型编号，不代表 Apple M1 适配认证。

这一路线的差别在音频编码器：限制注意力范围并维护缓存，同一模型支持多种右侧上下文，不需要每个新块都从头计算整个窗口。训练使用约3.5万小时 Reazon 数据，再以507小时多领域数据调整。

论文五领域平均 CER：

| 右侧声学上下文 | RNNT CER | CTC CER |
| --- | ---: | ---: |
| 1040毫秒 | 12.39% | 13.80% |
| 480毫秒 | 12.65% | 14.37% |
| 80毫秒 | 13.43% | 15.58% |
| 0毫秒 | 14.04% | 16.19% |

这里的80毫秒是模型前瞻长度，不是首字或中文字幕端到端延迟。结果体现了可调的质量与前瞻权衡，而非“零延迟不损精度”。

部署仍有门槛：当前公开的是每个约495MB的 NeMo 包，未核实到这个精确权重的现成 Mac 缓存流式运行包。作者的吞吐测试使用 RTX6000 Ada，不是 M1；论文比较的 ReazonSpeech NeMo 620M 也不是项目正在使用的 Reazon k2/Zipformer 基线。权重 CC-BY-NC 4.0，商业用途需要单独解决许可。

判断：它比再测同类离线大模型更贴合低占用目标。第一步只验证保留缓存状态的运行或导出路径及与 NeMo 的一致性。如果只导出了 CTC，不能把 CTC 的失败宣布为 RNNT 路线失败，也不能把丢掉缓存的分段运行称作原生流式完成。

# 其他真实遗漏及其适用范围

| 路线 | 新价值 | 不能直接成为默认方案的原因 |
| --- | --- | --- |
| [Meta Omnilingual CTC 300M v2](https://github.com/facebookresearch/omnilingual-asr/) 与 [ONNX INT8](https://huggingface.co/OpenVoiceOS/omnilingual-asr-ctc-300m-onnx) | 独立于 Whisper 的多语非自回归识别，支持日语；INT8文件约328MB | 非原生低延迟缓存流式；CTC不能按传入语言强制日语；尚无本项目短音频质量证据 |
| [Kizuna 日语假名 Zipformer RNNT](https://huggingface.co/kizuna-intelligence/zipformer-rnnt-japanese-hiragana) | 20.6M、权重82MB，显著缩小声学模型；输出假名 | 作者单核RTF0.020未说明CPU型号；只核实到整段解码。假名正确不等于汉字或中文正确 |
| [WavLM 假名 CTC v2](https://huggingface.co/TylorShine/wavlm-base-plus-hiragana-ctc-v2) 与 [DistilHuBERT](https://huggingface.co/TylorShine/distilhubert-hiragana-ctc) | 95.7M与24.2M的假名或音素路线，不必依赖大自回归解码器 | 双向离线编码；小模型在广播域的假名错误仍多；缺少对应 M1 长流证据 |
| [日语 wav2vec2 假名](https://github.com/nyosegawa/hiragana-asr) | 作者给出 M2 Air MPS 的实际运行路线 | 约315.6M；实时示例是 VAD 加预览重算，不是缓存流式。其假名误差不能与书面日语CER混比 |
| [Edge0 Audio8-ASR-0.1B](https://huggingface.co/Edge0/Audio8-ASR-0.1B) 与 [Apple 部署包](https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE) | 更小语音语言模型，CoreML音频塔加CPU解码器 | 0.1B只指文字模型，总计约324M；列出日语但公开主准确率表没有日语CER；5/10/30秒桶与整段调用不等于输入流式 |
| [Granite Speech 4.1 2B base](https://huggingface.co/ibm-granite/granite-speech-4.1-2b) | 双头CTC编码、关键词偏置；官方明确提供 MLX 与 macOS llama.cpp 用法；支持日语ASR | 不同于已测 Granite5Turbo470M，但仍较大；官方“online”示例是完整音频请求。NAR与Plus变体不能自动继承base的语言覆盖 |
| [OWSM CTC v4 1B](https://huggingface.co/espnet/owsm_ctc_v4_1B) | 非自回归、多语ASR及语音翻译，是独立权重路线 | 固定30秒训练与补零计算，非原生流式；没有据此核实日语到中文的质量。102M和370M的OWSM v4小模型属于另一自回归分支 |
| [Kodama 日语 streaming small](https://huggingface.co/ayousanz/kodama-ja-streaming-small) | 不同于官方日语 Moonshine 的独立微调，实际约140M | 官方诊断中流式较离线退化，噪声与专名表现未形成强优势；Mac社区示例也未证明缓存流式 |
| [Dolphin](https://github.com/DataoceanAI/Dolphin) | 140M与372M多语识别，日语在支持列表中 | 中文streaming权重不能套给日语；已见Mac数字主要是其他语言测试，保留为低优先级质量筛选 |
| [Hojo ASR Multi V1](https://huggingface.co/HojoAI/Hojo-ASR-Multi-V1) | 新的多语权重并声明支持日语 | 公开主性能表偏欧洲语言，缺少日语短音频与M1证据，不宜排在上面两条路线之前 |

假名路线值得探索的具体问题是：把汉字选择延后到翻译阶段，能否避免某些过早的同音汉字决定，同时缩小声学模型？它并没有消除歧义，只是把歧义转移给下游。应先用正确假名测试现有 Hy 的翻译能力，再决定是否接实际声学模型。仅看到“假名CER低”就宣布中文字幕更准，会重复此前的评估错误。

# 有突破但暂时不符合本项目的路线

| 路线 | 确认的进展 | 当前排除或降级原因 |
| --- | --- | --- |
| [CSSEL-P2P](https://arxiv.org/html/2607.13158) | 真正块因果编码器、前缀约束及专门的提交时机训练；隐藏临时尾部 | 论文有日语到英语、中文到日语，但没有日语到中文表；未找到官方释出的完成模型和推理代码 |
| [Hikari medium](https://huggingface.co/sbintuitions/hikari-medium) | 约0.8B因果Whisper式语音翻译、WAIT策略，而且官方有MLX | 发布方向包含日语到英语，不包含日语到中文或日语ASR；官方还提示可能修订。M4英语性能也有持续运行压力 |
| [Index Echo S2ST 9B v4.1](https://huggingface.co/IndexTeam/Index-Echo-S2ST-9B) | 发布包明确支持日语到中文，不只是语言列表推断 | 面向整句配音，chunk=True未支持；9B仅指解码器，另有音频和语音合成组件。公开0.056 CER是合成语音回听与自身译文的比较，不是独立翻译质量 |
| [WhisperRT](https://huggingface.co/MLSpeech/WhisperRT-Streaming) | 真正进行因果编码与部分音频对齐训练，不等同 AlignAtt wrapper | 当前发布的多语权重仅英语、法语、西语、德语、葡语，没有日语 |
| [X-ASR](https://huggingface.co/GilgameshWind/X-ASR-zh-en) | 百万小时训练的原生 Zipformer，多块长、sherpa-onnx和签名Mac客户端 | 当前权重只有中英，不能把中英低延迟成绩当成日语方案 |
| [fujielab 流式 Zipformer 与 Cascade](https://github.com/fujielab/fujielab-asr) | 66M、84.5M的小型日语流式及音素到书面文字双阶段模型 | 当前官方明确所有权重为private，需联系作者；公开代码不等于现成可下载权重 |
| [LC-BiMamba](https://www.isca-archive.org/interspeech_2025/moriya25_interspeech.pdf)、[TC-BiMamba](https://aclanthology.org/2026.findings-emnlp.330.pdf)、[CHAT](https://arxiv.org/html/2602.24245) | 状态空间、块内对齐与解码效率的结构进展 | 已核实论文实验主要是英语、中英或欧洲语种，未找到已释可部署日语权重；日本作者不等于日语模型 |
| [Qwen3-ASR 0.6B causal streaming](https://huggingface.co/qfuxa/qwen3-asr-0.6b-streaming) | 真正块因果编码及缓存，区别于原始离线Qwen | 该派生模型明确仅英语；多语训练尝试出现大幅退化，不能继承基础模型的日语能力 |
| [NemotronCoreAI](https://github.com/mweinbach/NemotronCoreAI) | 新Apple运行栈和持续PCM状态接口 | 当前系统与预编译硬件矩阵不覆盖本机配置；ANE路径还有算子编译限制，不构成M1即用的硬件突破 |

这些排除项不是“生态没有进展”，而是语言方向、权重公开、运行平台、输入流式或输出不可回写中的至少一环尚未满足。

# 框架进展值得借鉴但不等同模型质量

## audio.cpp 是比浏览器迁移更相关的测试底座

[audio.cpp](https://github.com/0xShug0/audio.cpp) 的 v0.9.4 于2026年11月4日发布，提供基于ggml的多类音频模型原生运行路径，支持CPU、Metal等后端，并加入多种ASR家族。它对本项目的价值是降低不同声学模型的适配成本，以及复用原生内存、算子和调度优化，而不是保证一个模型的日语识别会自动变准。

项目公布的 Parakeet Metal 优化是在 M4 Pro 上端到端处理时间减少12.1%至13.9%，不是M1结果，更不是中文字幕准确率提升。支持表中的Stream也只能证明有某种会话接口，仍要逐模型检查是否维护编码器缓存、是否重复编码、是否改写输出。

因此，audio.cpp 比围绕 wllama 做浏览器迁移更贴近当前ASR探索。wllama 的浏览器内运行价值与模型训练进展应分开判断；没有本项目同权重、同输入、同资源口径的结果，就不应把 wllama 3.13.0 放在“识别精度突破”的优先级上。本轮不重新对其版本性能作背书。

## CoreML 和 ANE 不能只看名称

[speech-swift 的 Omnilingual 文档](https://soniqo.audio/guides/omnilingual)与[实际源码](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/OmnilingualASR/OmnilingualASR.swift)存在重要差异：说明中强调ANE，但当前代码默认请求CPU加GPU，并解释部分设备上ANE编译不稳定。其CoreML实现使用固定5秒或10秒窗口，不能当作已验证的原生缓存流式。另一个版本问题是，当前 aufklarer 转换包的来源标签仍指向没有v2后缀的模型，尚未确认它等价于上文 OpenVoiceOS 的v2 ONNX权重；不能把两个发布包拼成一个已经验证的Mac v2路径。

同样，Haas 的 ONNX、CoreML、speech.cpp 三条路线此前已有覆盖；不能以新发现的社区速度数字抹掉用户测试中的质量失败。CoreML转换后的数值一致性、短句起始丢失和真实设备派发，比“格式支持”“要求ANE”更重要。

# 为什么新模型仍可能在用户视频上失败

[日本社区的可复现双数据集测试](https://zenn.dev/yukobayashi500/articles/849115a7c596f7)提供了很直观的例子：Qwen3-ASR-1.7B在650条FLEURS朗读上CER为5.36%，在1000条Reazon广播上则为25.98%；Parakeet日语在两者分别为6.37%和11.21%。测试作者公开逐条输出，并注明Reazon派生模型在广播数据上的域内优势。该结果不是本项目排行榜，也不是M1实时测试，但足以说明通用朗读榜排名不能直接外推到视频域。

另一个反例是 [BuzzASR Japanese](https://huggingface.co/BuzzASR/japanese)：其系列宣传强调专语优化，日语FLEURS却是14.49%，基础Whisper large-v3为9.82%；Common Voice日语则略有改善。全语言平均收益不能替代日语分项，更不能替代日语视频短块结果。

应把瓶颈拆开看：

- 稳定前缀与正确提交时机是训练问题，R2T2正面处理了这一点。
- 重复编码、后端数值偏差、缓存与默认分配是实现问题，新的权重不会自动修复它们。
- 何时积累到足够日语语义、怎样向翻译器提交，是级联策略问题；流式ASR不等于每个token都适合立即翻译。
- M1 Pro的算力与带宽决定可承受的模型规模和重算频率，但不能仅凭“多轮模型没赢”断言硬件是唯一瓶颈，也不能把低排队耗时解释成硬件完全不重要。

# 更新后的探索顺序与止损条件

这次不建议把所有新名字加入一次大矩阵。候选排序依据是：是否针对当前失败机制、是否能保留既有约束、是否值得付出Mac适配成本。

| 优先级 | 路线 | 先回答的问题 | 进入完整测试的条件 |
| --- | --- | --- | --- |
| 1 | R2T2 与基础Qwen1.7配对 | 后训练能否改善同块长下的日语稳定前缀，而且Mac全llama路径可运行 | arm64加载与短日语一致性通过；日文及中文分别保证不回写；推理无持续积压；ASR加MT按约2.5 GiB默认目标核验，超出则单列高资源分支由用户决定 |
| 2 | 123M日语cache-aware RNNT | 能否保留真正的音频缓存，并在CPU或可用Mac后端运行 | cache张量、长度与RNNT状态一致性通过；短块结果与原始运行时对齐 |
| 3 | Omnilingual CTC 300M v2 | 独立小CTC家族是否对当前日语失败样本更有效 | 正确固定版本，短句与静音不出现明显质量倒退，再接MT |
| 4 | 假名小模型 | 正确假名到中文能否先保持语义质量 | Hy或必要的小规模消歧不造成新的错误与明显延迟，才接真实声学输出 |
| 后续 | Audio8、Granite4.1、OWSM、Dolphin | 是否有低成本直接运行路径和特定错误类型收益 | 不为“更多候选”单独开发重型框架；先质量筛选，失败即停 |

既有 Fun-ASR-MLT 的低成本质量筛选可保留，但新的高成本工程优先级应转向R2T2和123M原生流式，而不是继续扩散普通离线大模型或浏览器迁移。

各候选须区分三种结果：运行不通、运行通但质量失败、质量有利但资源或延迟失败。前一种不能证明模型本身无效，后两种也不能用后端名字掩盖。具体测试仍沿用项目的冻结音频、成对顺序、中文可用性、漏错计数、静音、1倍速回放及长期资源口径，不以英语RTFx、模型体积或算法块长代替。

大幅提升需要端到端证据。R2T2的36.07%到8.66%说明专门训练可能显著改善一个困难条件，但这个收益不能机械加到Reazon基线；123M模型说明原生流式不一定需要大模型，但也不保证日语视频语义更准。当前正确结论是“存在值得新增、可能改变权衡的路线”，不是“已经找到最优解”，也不是“确定只剩微优化”。

# 检索覆盖与可追溯性

本轮使用Exa进行了45次查询，返回395条含重复结果，按精确URL去重为311个URL。这是检索覆盖量，不是311篇全文精读，也不是311个独立模型；同一论文的PDF、HTML和代码页可能分别计数。重点候选进一步核对原始论文、HF权重、官方代码和具体运行入口。

近期优先窗口为2025年11月10日至2026年11月10日，同时对仍有价值的旧模型不限年份补查。覆盖英文、日文关键词，GitHub、Hugging Face、arXiv、ISCA、ACL Anthology，以及作者在Zenn、Qiita和社区发布的实际代码。Reddit被纳入检索式，但没有得到足以支持“日语M1已明显胜出”的可靠新证据，不能因此捏造社区共识。

| 分支 | 查询次数 | 返回结果条数 | 分支内精确URL去重 |
| --- | ---: | ---: | ---: |
| 多语模型与基准交叉检查 | 7 | 70 | 65 |
| 日本本土与学术模型 | 8 | 100 | 83 |
| Apple原生与浏览器运行框架 | 13 | 108 | 84 |
| 流式训练与端到端语音翻译 | 17 | 117 | 103 |

以下保存各分支检索式，便于下一轮复查而不是再次从主流模型名单开始。

# 附录 检索式

## 多语模型与基准

1. Official open multilingual speech recognition model releases in 2026 with Japanese FLEURS evaluation and downloadable weights on Hugging Face, beyond Whisper and Qwen ASR （返回 15 条）

2. Meta Omnilingual ASR Japanese 300M 1B 3B 7B model inference streaming ONNX Apple Silicon fairseq2 released 2025 2026 （返回 10 条）

3. ESPnet OWSM v4 Dolphin multilingual speech recognition Japanese open model low latency native implementation 2026 （返回 10 条）

4. Open ASR Japanese leaderboard FLEURS CER 2026 small multilingual speech recognition benchmark reproducible model catalog （返回 15 条）

5. OWSM CTC v4 Japanese to Chinese speech translation audio.cpp GGUF Metal support model language task symbols （返回 10 条）

6. BuzzASR Japanese Whisper large v3 monolingual checkpoint Japanese CER FLEURS EMNLP 2026 （返回 5 条）

7. yukobayashi500 ja-asr-leaderboard-2026 broadcasting FLEURS Reazon Qwen reproducible benchmark dataset methodology （返回 5 条）


## 日本本土与学术模型

1. 2025年11月10日から2026年11月10日 日本語 音声認識 軽量 オープンソース モデル 公開 NII Reazon NTT LINE Sakana （返回 15 条）

2. 日本語 ストリーミング 音声認識 ESPnet icefall sherpa Zipformer Conformer CTC 学習済みモデル （返回 15 条）

3. 日本語 音声認識 ローカル Mac Apple Silicon ONNX 軽量 wav2vec HuBERT MMS Omnilingual モデル （返回 15 条）

4. category:research paper Japanese streaming automatic speech recognition pretrained model 2025 2026 NTT NII Reazon ESPnet （返回 15 条）

5. streaming-ja-asr-anon parakeet-streaming-ja-fully-open M1-6.nemo Japanese 123M cache aware streaming Yinchang Yang （返回 10 条）

6. 日本語 小型 音声認識 モデル NII NTT LINE PFN Sakana 2026 オープンソース 重み 公開 Conformer CTC （返回 10 条）

7. 日本語 distilhubert-hiragana-ctc wav2vec2 hiragana 0x3 315M TylorShine M2 音声認識 （返回 10 条）

8. 日本語 音声認識 ESPnet OWSM CTC v4 1B 180M JTubeSpeech Japanese pretrained checkpoint （返回 10 条）


## Apple原生与浏览器框架

1. 日本語 音声認識 M1 Mac CoreML ANE リアルタイム 2025 2026 GitHub （返回 10 条）

2. GitHub Apple Neural Engine Core ML speech recognition Japanese streaming ASR Swift C++ runtime 2026 AppleNN （返回 10 条）

3. Reddit developer speech recognition Apple Silicon M1 Japanese streaming quantized encoder CoreML Metal problems （返回 10 条）

4. GitHub sherpa onnx icefall streaming Japanese multilingual speech recognition CPU small model 2025 2026 （返回 10 条）

5. GitHub transformers.js ONNX WebGPU Japanese speech recognition streaming new models 2026 （返回 10 条）

6. AppleNN ANE ASR speech recognition small Japanese model Swift CoreML encoder quantization 2026 GitHub （返回 8 条）

7. soniqo speech-swift Qwen3 ASR CoreML MLState M1 encoder decoder benchmark memory parity （返回 8 条）

8. Sanami Haas-ja CoreML M1 speech.cpp 0.10 quantization short chunk Japanese latency GitHub （返回 8 条）

9. Reddit Haas ja speech.cpp Mac M1 Japanese recognition author Sanami sakasegawa （返回 8 条）

10. Audio8 ASR 0.1B Japanese benchmark CER WER Edge0 Audio8 iOS ANE GitHub streaming speech recognition （返回 8 条）

11. speech-swift Omnilingual CTC v2 coreml ANE CPU GPU japanese issue regression 2026 （返回 6 条）

12. AppleNN Swift ANE speech ASR runtime Japanese 2026 GitHub （返回 6 条）

13. Reddit Audio8 ASR Edge0 Japanese Apple ANE M1 transcription developer （返回 6 条）


## 流式训练与语音翻译

1. category:research paper streaming speech recognition 2026 2025 block causal encoder state space Mamba RWKV transducer CIF open source model weights multilingual Japanese （返回 10 条）

2. multilingual speech foundation model open weights Japanese streaming ASR speech translation new release 2026 2025 compact 1B 500M （返回 10 条）

3. category:research paper simultaneous speech translation Japanese Chinese 2025 2026 open source streaming speech to text prefix training （返回 10 条）

4. category:research paper partial speech prefix training incremental speech translation teacher student distillation streaming multilingual 2026 2025 （返回 10 条）

5. A Compact Fully-Open Cache-Aware Streaming Model for Japanese ASR Yang 2026 Interspeech checkpoint code Huggingface （返回 7 条）

6. Chunk-wise Attention Transducers for Fast and Accurate Streaming Speech-to-Text 2602.24245 official weights Japanese languages （返回 7 条）

7. Japanese streaming ASR continuous integrate and fire transducer 2026 2025 open checkpoint Hugging Face WeNet Zipformer Squeezeformer （返回 7 条）

8. category:research paper streaming ASR translation N-best lattice semantic interface Japanese contextual biasing 2026 2025 open source （返回 7 条）

9. CSSEL-P2P Junkun Chen Microsoft Phi-4-MM 2607.13158 model weights github release Japanese Chinese （返回 5 条）

10. TC-BiMamba LC-BiMamba Mamba-ASR Japanese pretrained model checkpoint github streaming （返回 5 条）

11. Hikari Streaming Translation Transcription Speech to Text Causal Alignment SoftBank Intuitions 2603.11578 Huggingface github （返回 5 条）

12. Japanese native streaming speech recognition model open source 2026 causal checkpoint （返回 5 条）

13. streaming speech recognition contextual biasing low memory Japanese hotword FST transducer sherpa onnx 2026 2025 implementation （返回 5 条）

14. simultaneous speech translation Japanese Chinese source streaming open source weights StreamUni StreamSpeech SimulMask DiSeg 2026 （返回 5 条）

15. streaming translation stable prefix training open source weights Japanese Chinese 2026 small model no revision （返回 5 条）

16. X-ASR Guo 2026 streaming Zipformer WhisperRT Krichli 2026 causal Whisper open model Japanese GitHub （返回 7 条）

17. GPT-Live-Transcribe open source model weights streaming Japanese WhisperRT causal encoder 2026 （返回 7 条）


