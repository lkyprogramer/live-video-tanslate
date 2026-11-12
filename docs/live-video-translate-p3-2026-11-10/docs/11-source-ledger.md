# 11 来源台账与证据边界

## 本包实际核查了什么

研究日期为2026年11月10日。项目基准固定为 [live-video-tanslate@77e24c3e490a11721041a40ad4bd203f51f84039](https://github.com/lkyprogramer/live-video-tanslate/commit/77e24c3e490a11721041a40ad4bd203f51f84039)，提交时间为UTC 2026年11月9日13:30:30，即北京时间21:30:30。所有“当前仓库已有能力”均指这个pin；执行前如HEAD变化，先做差异说明，不能把浮动main当作同一个实验版本。

本包完成了公开资料及已连接仓库的只读核查，并执行了下载源码的argparse `--help`。**没有新增目标M1 Pro上的模型编译、加载、推理、播放、资源或质量实测。** 文档辅助工具通过，不构成模型可运行、质量合格或发布资格。此前P2实测属于历史证据，必须保留其原run、硬件、冻结配置与参考状态。

2026年11月10日扩展Exa检索共 **45次查询、395条结果（含重复）、311个按精确URL去重的地址**。这些是命中统计，不是全文阅读量、独立论文数、独立实验数或已验证模型数。查询、命中和后续阅读的关系以[搜索台账](../research/search-ledger.json)为准；同一页面的不同URL也可能属于同一来源。搜索摘要仅用于发现，语言方向、工件身份、接口和许可尽量回到论文、作者仓库、模型卡及实际源码核验。

## 证据层级

| evidence_level | 含义 | 本包可支持与不可推出的结论 |
|---|---|---|
| `source_verified` | 已读取相应源码、配置、工件元数据或一手文本，能指出具体锚点 | 可确认接口存在、代码调用、某项公开声明或pin；不证明该路径在M1成功运行。论文表格被读到时，数值仍是作者实验，不升级为本地实测 |
| `author_reported` | 作者论文、模型卡或项目文档所报告的精度、速度、内存与平台结果 | 只能连同作者硬件、数据、语言和协议转述；不能外推为用户M1、日语短流或日中字幕结果 |
| `local_measured` | 特定设备与冻结run真实执行并保存原始收据、输出和采样 | 本包引用P2历史记录时可注明历史local_measured；本包没有新的目标机模型结果。此次help与源码hash检查只证明相应工具/文件检查，不是模型实测 |
| `inferred` | 基于已核查事实作出的工程判断或待验证假设 | 例如“全llama Mac小验证可能可行”“123M更值得先做缓存运行gate”；必须标为判断，不写成已支持、已通过或已省内存 |

同一候选可以同时拥有不同层级的多项证据，不应给整个候选贴一个最高级标签后覆盖所有字段。模型体积、训练参数、算法lookahead、offline RTF、首次token和稳定正确可读中文延迟是不同量，不能互相替代。

## 已核验项目源码与既有能力

通过GitHub只读工具取得固定pin的完整recursive tree，响应未截断；读取并保存66份相关源码、协议和历史报告。逐份计算Git blob SHA-1，与该tree返回的blob SHA核对，**66/66一致**，见[源码blob校验表](../research/source-blob-sha1.txt)。这是“下载内容与指定提交一致”的证据，不是编译、运行、模型数值parity或测试通过证据。详见[仓库对接审计](../research/repo-audit.md)及[CLI帮助证据](../research/cli-help-evidence.md)。

| 项目源文件/报告 | 核查到的能力或事实 | 边界 |
|---|---|---|
| [P2 CLI](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/cli.py)与[P2说明](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/README.md) | 已有B0–B4分派，freeze/probe/score/report及相应实现；不是待创建的空目录 | 没有P3 CLI；B1–B4的candidate replay/accept仍unsupported。B3候选preflight会触发G0，不是纯静态检查 |
| [runner.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/runner.py)、[realtime.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/realtime.py) | B0离线逐字段复现、真实短尾分包与1×WS回放已经存在 | 不能直接把新experiment_id代入就得到候选在线整链 |
| [b3_worker.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/b3_worker.py)、[b3_protocol.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/b3_protocol.py) | Vox已真实一视频一session、逐包feed/step、跨VAD保状态；Gemma已有真实音频tower/projector及输出协议检查 | Vox本轮历史探针未1×节拍、MT另阶段离线执行，不能称生产在线取消/长稳已验；Gemma token流不是音频流式 |
| [b3_vox_cache.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/b3_vox_cache.py)、[resources.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/p0/p2/resources.py) | 显式512MiB free allocator cache诊断，250ms资源采样、CPU/RSS/footprint与系统压力等已有 | cache上限不是KV量化或active内存上限；通用Vox命令不会静默启用诊断；现有守卫不等于所有P3拟新增停止规则已实现 |
| [backend.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/engine/src/subtitle_service/backend.py)、[app.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/engine/src/subtitle_service/app.py)、[local_pipeline.py](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/engine/src/subtitle_service/local_pipeline.py) | 当前Reazon CPU与Hy MLX分离worker、队列、busy、EOF与清理合同 | 同步worker安全返回前保留lease；async任务取消不是Metal硬抢占，新后端必须补合同测试 |
| [v1合同](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/subtitle-backend-contract.md)、[v1 Schema](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/protocol/v1.schema.json)、[engine依赖](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/engine/pyproject.toml) | 16k mono s16le、200ms包、真实样本范围、单session及固定依赖底座 | 实验epoch不是新v1控制字段；P3实施不得自动升级生产锁或改变协议 |

历史结果入口：[B0](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/p2-b0-results-2026-11-08.md)、[B1](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/p2-b1-results-2026-11-08.md)、[B2](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/p2-b2-results-2026-11-09.md)、[B3](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/p2-b3-results-2026-11-09.md)、[B4只中文G0](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/p2-b4-gemma-zh-results-2026-11-09.md)。B0质量20/18为暂定参考；Vox17/18、Gemma官方6/8均未晋级；Gemma只中文正式双轮未运行，不能写0/34。P2正式R的主指标锁为`paired_common_correct_dom_p95`；本包的`R_memory`是**新增预登记探索轨**，不覆盖旧主指标，不追认P2正式R通过，不凭探索结果自动替换默认。

[2026年11月9日清理记录](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/local-runtime-cleanup-2026-11-09.md)说明已删除未采用候选下载与隔离环境，保留当前Reazon/Hy/Silero、engine环境与历史证据。历史命令存在，不代表所有候选现在即用；恢复工件需要锁定身份和新的实施授权，不能把清理后缺文件记成模型能力失败。

## 新路线的pin台账：已锁源码不等于已锁权重

| 路线 | 已核查的一手来源/pin | 当前未知或仍须实施前锁定 | 能支持的决定 |
|---|---|---|---|
| R2T2主线 | [官方源码72b81be7070095410b75ed2e11998e347f655218](https://github.com/netease-youdao/Confucius4-R2T2/commit/72b81be7070095410b75ed2e11998e347f655218)；[llama.cpp ad6c66839af3c5646fba8c6c2e2087a1e4e38948](https://github.com/ggml-org/llama.cpp/tree/ad6c66839af3c5646fba8c6c2e2087a1e4e38948)；[论文v1](https://arxiv.org/html/2611.06043v1) | [GGUF仓库](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF)具体Q4_K_M/Q8 mmproj文件名、HF revision、SHA256尚未冻结；基础Qwen同后端同量化控制也需锁；NetEase模型许可须保存原文并核适用范围 | 全llama源码含音频路径且上游含Metal，值得有界source/import/load/parity验证；不能称Mac构建成功或低于2.5GiB |
| 123M日语cache-aware RNNT主线 | [精确模型仓库](https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open)、`M1-6.nemo`身份；[原论文](https://www.isca-archive.org/interspeech_2026/yang26r_interspeech.pdf)；[作者代码入口](https://github.com/yoinncyan/my-interspeech-2026) | HF resolved revision、文件SHA256、匹配运行时及cache-enabled RNNT导出图未冻结（作者源码已由本次复核pin至cc1cdf12a3133b22349c520e5b81f0334a01e5ad，仍非权重revision）；无已验证的精确权重M1缓存运行包；CC-BY-NC 4.0使用范围须确认 | 是不同于既测0.6B日语TDT的新checkpoint/架构路线；先证缓存与parity，不用论文CER证明本项目胜出 |
| Omnilingual备选 | [Meta官方代码](https://github.com/facebookresearch/omnilingual-asr/)；[OpenVoiceOS ONNX仓库](https://huggingface.co/OpenVoiceOS/omnilingual-asr-ctc-300m-onnx) | v2精确上游身份、转换来源、HF revision、INT8文件SHA256与tokenizer/配置未冻结；不能用名称代替追溯 | 仅在唯一备选席位内做版本/运行gate；不是已验证Mac v2流式产品 |
| Kizuna条件备选 | [作者模型卡](https://huggingface.co/kizuna-intelligence/zipformer-rnnt-japanese-hiragana)及20.6M/假名输出的公开说明 | `model.pt` revision/SHA256、作者代码commit、frontend/greedy配置待锁；未证原生cache API | 先做正确假名→Hy的独立机制诊断，再按预算决定是否加载声学模型；oracle结果不是模型质量 |
| Fun-ASR-MLT备选 | 研究保留的候选线索，见[综合复核](../research/expanded-review.md) | 本包不提供已冻结的新正式配置；必须区分Nano/MLT、模型checkpoint与MLX/GGUF后端，选定后补全身份、许可和锁 | 不能借“廉价探针”额外开正式评分席位；已有冻结结果可引用，新加载/正式评分须计入统一预算 |

用于纠正平台宣传的独立锚点：[speech-swift源码a57702e6f523fa36ac5532d2bdc5f35f701a052c](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/OmnilingualASR/OmnilingualASR.swift)默认CPU+GPU请求与固定窗口，不能被文档中的ANE描述升级为实测派发；[CoreML工件a755e007d0a56bd8c7e9885f55bb2112e52b4d51](https://huggingface.co/aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8/tree/a755e007d0a56bd8c7e9885f55bb2112e52b4d51)并未证明v2等权重；[Audio8 Apple包6a3f52c400cd436e1bd32c0514758345ddf14bfa](https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE/tree/6a3f52c400cd436e1bd32c0514758345ddf14bfa)只支持其特定工件检查，不构成本项目日语质量或M1内存证据。

所有未知revision/hash应保存为`null`并阻塞相关正式加载/计分，不能写猜测SHA、浮动latest或成功占位值。Git源码commit、HF权重revision、下载文件SHA256和转换源版本是不同字段，不能用一个替代另一个。发布物不可访问或权重private时记来源/权限阻塞；抓取失败不自动等于模型不存在，更不能借旧缓存绕过权限。

## 旧失败配置什么时候可以重开

同名模型重新被搜索命中、换一个UI/框架、社区给出其他机器速度、出现CoreML/Metal标签，都不构成重开理由。Haas、PengChengStarling、已测Moonshine、Whisper、PLaMo、SenseVoice、Voxtral及Gemma的历史失败继续有效于各自冻结条件。

重开必须同时具备：明确未被旧实验覆盖的错误机制；可核验的新checkpoint/转换/运行时差异；说明哪些变量保持不变及如何做parity；预登记预算、候选席位、质量/资源主指标与停止条件；需要超出当前范围时获得新的明确授权。只换运行时但权重相同，应标“同checkpoint后端验证”，不能重复包装成新模型。源码/加载阻塞不能写成质量失败；质量未运行不能记0分；新量化或解析修补不能追溯修改旧成绩。

## 包内研究导航

| 材料 | 主要用途 | 阅读边界 |
|---|---|---|
| [expanded-review.md](../research/expanded-review.md) | 本轮综合结论、优先级与排除理由 | 综合判断不是新的目标机成绩；实施规格与其不同处，以本包显式预算/合同为准 |
| [r2t2-mac-feasibility.md](../research/r2t2-mac-feasibility.md) | R2T2固定源码锚点、构建/导入风险、KV清理与两种流API区别 | source-only；没有编译或成功加载记录 |
| [japanese.md](../research/japanese.md) | 123M、日语原生流与假名路线的一手来源、语言/许可/平台缺口 | 作者硬件结果与未核实Mac能力分列 |
| [native.md](../research/native.md) | CoreML/ANE、Audio8、Omnilingual版本与native部署的核查 | 配置要求计算单元不等于实际设备派发 |
| [streaming.md](../research/streaming.md) | 稳定前缀训练、语言方向、权重公开与因果/不可回写边界 | “未找到”仅指本轮范围，不能绝对证明不存在；初步结论如被后续固定源码复核修正，以固定锚点为准 |
| [search-ledger.json](../research/search-ledger.json) | 查询式、命中与统计的机器收据 | 不能当全文阅读清单、权重锁或质量测试 |
| [repo-audit.md](../research/repo-audit.md) | 指定项目pin的既有接口、结果、调度/取消合同与P3拟新增位置 | 只读对接审计，不是实现完成证明 |
| [cli-help-evidence.md](../research/cli-help-evidence.md) | 已下载P2源码的实际argparse帮助输出 | 参数存在不等于每个phase/候选都已实现；只有help被运行 |
| [source-blob-sha1.txt](../research/source-blob-sha1.txt) | 66份源码/报告blob与项目pin的一致性 | 不含模型权重完整性证明，也不等于模型或设备测试 |

材料中的来源入口若为main/master或模型卡URL，只是可追溯入口，不自动成为正式内容锁。正式实施应把实际采用内容解析为不可变pin并保存收据；本轮未保存的目标机结果不得在后续报告中补写为本包已测。
