# 06 次候选：一个正式首筛席位，先语义后工程

## 预算与选择规则

R2T2 与日语 123M RNNT 是两条主线。本章不是全跑清单：**最多一个备选正式质量首筛，即至多三个主候选配置；另一个预登记条件消融，合计最多四个 `experiment_id`，E2E 最多两个候选**。S1 与 RNNT 80 ms 竞争唯一条件槽。基础 Qwen 控制不计候选但计成本；量化／chunk／U／状态策略衍生均算配置。不同实验组及其下载／构建不重叠，同组播放、ASR、Hy 与采样按生产调度并行。默认备选为 **Omnilingual CTC 300M v2 ONNX INT8**。正确假名→Hy 为不加载新声学模型的语义 gate；只有 gate 通过且唯一备选席位未使用或获明确重分配，才可首筛 Kizuna。不得失败后自动逐个补测。

既有 Fun-ASR-MLT 可保留廉价质量筛选，但不能据此另开正式席位或重型工程线。若 P2 已有可用、冻结的首筛结果，直接引用而不重复跑；若需要新模型加载／新增正式评分，必须计入唯一备选席位并先登记替代谁。Audio8、Granite、OWSM 仅候补，不为“覆盖更多模型”安装框架。

冻结原产品 16 kHz s16le 单声道、200 ms 到达包，不改 main，不用未来音频、云、原字幕或 OCR。离线／chunked 识别只可访问当前已到达范围，不把 whole-file 质量探针算实时胜出。低驻留目标仍为 ASR+现有 Hy 约 2.5 GiB，模型体积、英语 RTF 或其他 Apple 芯片结果均不替代测量。目标文件 `p0/p3/adapters/secondary.py`，接口与错误遵守 [09 章](09-runtime-integration.md)，不定义平行协议。

## A. 默认备选：Omnilingual CTC 300M v2 ONNX INT8

来源固定为 Meta `omniASR-CTC-300M-v2` 家族与 `OpenVoiceOS/omnilingual-asr-ctc-300m-onnx` 的 INT8 工件。研究确认约 328 MB 文件和日语支持，未提供可采用的 HF commit／文件 SHA256；未知字段填 `null`，`run.status=blocked_data`，不用 latest。preflight 补齐 encoder、tokenizer、配置、导出来源哈希及许可。转换卡若不能证明来自 **v2**，停止 `blocked_data`，不借文档标签替代来源追溯；实际部署／转换 parity 失败才标 `blocked_runtime`。

### Task checklist

- [ ] 检查 ONNX 输入输出、真实采样率／归一化／有效长度、CTC blank 和 tokenizer；锁 ORT 版本、CPU provider、线程与 INT8 实际执行。拒绝静默回落到另一个 checkpoint 或 float32。
- [ ] 同精确 v2 参考实现做少量日语前端／logit／文本 parity，包含短句、静音、低音量起始。将转换误差与模型质量分开；缺少参考只能记烟测，不填 parity 成功。
- [ ] 只跑冻结切片的正式首筛；记录每条原文、异常、空输出、推理全过程和现有 Hy 译文，用统一语义门判断。CTC 非自回归不代表零幻觉，照样计插词、错词与静音误触发。
- [ ] 原生缓存未获证明，按离线 CTC 对待。若进入因果流，明确过去窗口、重算量、overlap、去重与稳定提交门；输入语言参数不能强制 CTC 输出日语，不把 language hint 写成约束保证。
- [ ] 质量不过即停；质量通过再申请剩余 E2E 席位，不自动开发 CoreML/MLX/Metal 三路。

speech-swift 的 CoreML 路径不能拼装成“已验证 v2 Mac”：核查 pin `a57702e6f523fa36ac5532d2bdc5f35f701a052c` 默认 `.cpuAndGPU`，固定 5/10 s 窗口而非缓存流式，文档 ANE 宣称不等于实际派发；当前 aufklarer 转换包指向不带 v2 的上游身份，未证同权重。M3 Pro 的内存／速度不可外推本机。上述发现只说明不要偷换对象，不要求重做另一运行栈。

## B. 条件路线：正确假名→Hy，再决定是否加载 Kizuna

假名路线将同音汉字歧义推迟到翻译端，没有消除歧义。先选冻结、人工听审过的开发语义项，构建**正确发音假名**；仅用于离线评估门，不向产品提供 oracle，不从原字幕读取。保持原有 Hy 权重、prompt、分段与中文提交规则不变，比较同一语义项的书面日语输入和正确假名输入。保留数字、计数、否定、敬语、人物关系、专名和同音词；错误假名与正确假名不得混成一个分数。

人工听审未完成的材料只能标待复核；自动 kana 转换可能把汉字误读，不能作为 oracle。若 Hy 在正确假名上已经出现新增关键语义错误或明显等待代价，直接停止声学加载，结果是语义接口 gate 失败，不是 Kizuna 识别质量失败。禁止此时自动加新消歧模型、外部词典服务或更大 MT 解决问题；额外方案需审批。

语义 gate 通过且预算允许，才选 `kizuna-intelligence/zipformer-rnnt-japanese-hiragana` 的 `model.pt`，约 20.6M、82 MB、Apache-2.0。固定 HF revision、SHA256、83 字符+blank、作者代码 commit、PyTorch/torchaudio 版本与 greedy_decode 配置。当前只核实整段 greedy 解码，不因 RNNT/Zipformer 名称就宣称有 causal/cache API。

### 条件 Task checklist

- [ ] 检查实际采样率、frontend、token 到假名映射和 blank；用已审短句确认无罗马字／假名归一化混淆。
- [ ] 固定 CPU 路径运行首筛，不先开发 ONNX/CoreML。作者“单核 RTF 0.020”未给 CPU 型号，不作为目标机 SLA。
- [ ] 同时报告 kana CER／KER、关键内容错误和最终中文语义分数，**不得拿 kana CER 与书面日文 CER 排名**；保留错误假名输入 Hy 后的错误传播链。
- [ ] 全静音、重复音、长音／促音、小假名、数字读法、人名同音、BGM、句尾否定为必测；检查输出长度上限及 EOF。
- [ ] 若申请 E2E，明确它是 past-only 外部切片／VAD 包装，除非实际源码证实 cache；任何预览重算不得回写已显示中文。质量／资源门不过即停，不自动再换 WavLM、DistilHuBERT 或 315M wav2vec2。

## C. 保留与排除：有进展，不等于当前可执行

| 候选 | 本阶段决定与可重开条件 |
| --- | --- |
| Fun-ASR-MLT | 保留既定低成本质量筛选；不得把 Nano/MLT、MLX/GGUF 的不同能力拼为一份成绩。新正式首筛占唯一备选预算 |
| Audio8-ASR-0.1B | 候补；0.1B 是 LM，总约 324M，CoreML tower+CPU ORT decoder 是整段输入，5/10/30 s 桶不是原生流。缺公开日语 CER；约 437 MB 包及 iPhone 内存不等于 M1 RSS，CC-BY-NC 4.0 |
| Granite Speech 4.1 2B base | 候补；不同于已测 Granite5Turbo470M，官方有 macOS MLX/llama 路径，但“online”完整音频请求不等于连续流。不能把 NAR/Plus 继承 base 日语覆盖 |
| OWSM CTC v4 1B | 候补；固定 30 s 训练／补零不是低成本原生流，102M/370M 属另一自回归分支。未证日语→中文直译质量 |
| Haas、PengChengStarling | 已在用户材料失败；本阶段不因新后端／社区速度重新推荐。重开须有明确未覆盖机制与新授权 |
| Hikari medium | 当前发布方向无日语 ASR／日语→中文；MLX 存在也不能补语言缺口，不做英译桥接 |
| WhisperRT、X-ASR | 已发布对应权重无日语；不能继承基础 Whisper 多语或把中英成绩改标日语 |
| fujielab 流式／Cascade | 有相关研究但权重当前 private；公开代码不是可执行许可，不访问旧缓存绕过权限 |
| CSSEL-P2P、TC/LC-BiMamba、CHAT | 缺本项目对应日语已释权重／方向，保留研究线索，不启动训练 |

## 统一证据、失败分类与止损

每次 gate 保留 `candidate-lock.json`、逐切片 ASR/Hy 输出、parity 证据、质量配对表和 stop reason；路径纳入统一 run 目录。先证来源与运行正确，再评质量；质量通过才给 E2E。发生静默换模型、未来音频越界、上屏回写或丢弃音频，属于契约失败，不能通过更换分母补救。

未执行／未获预算为 `run.status=not_run`，缺人审为 `needs_human`；来源／工件锁／参考工件缺失为 `blocked_data`，未知锁填 null；依赖／算子／加载或实际部署／转换 parity 阻塞为 `blocked_runtime`；有效运行且可靠参考下质量门失败才是 `failed_quality`。`unscored` 仅作 `score_status`，不能作全局 run.status；只测 oracle 假名不产生声学分数。资源／延迟失败另列维度。oracle gate 失败不是声学模型失败，CTC 窗口包装可运行也不是原生缓存成立。

不存在自动“下一个候选”。需要大缓存优化、hybrid MPS、新前端框架，或超出三个主配置加唯一获准条件配置的范围时，停止并列出预期收益、证据缺口和新增成本，等明确审批。

## 一手来源与 pin 要求

- [Meta 官方 Omnilingual 代码](https://github.com/facebookresearch/omnilingual-asr/)；[OpenVoiceOS ONNX 工件](https://huggingface.co/OpenVoiceOS/omnilingual-asr-ctc-300m-onnx)。执行前保存不可变 revision、来源模型身份与文件哈希。
- [speech-swift 固定源码](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/OmnilingualASR/OmnilingualASR.swift)；[aufklarer CoreML 卡](https://huggingface.co/aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8)，已审 snapshot `a755e007d0a56bd8c7e9885f55bb2112e52b4d51`，不等于 v2 证明。
- [Kizuna 模型卡](https://huggingface.co/kizuna-intelligence/zipformer-rnnt-japanese-hiragana)；[Audio8 原始卡](https://huggingface.co/Edge0/Audio8-ASR-0.1B)、[Apple 包](https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE)（已审 snapshot `6a3f52c400cd436e1bd32c0514758345ddf14bfa`）。
- [Granite 4.1 base](https://huggingface.co/ibm-granite/granite-speech-4.1-2b)、[OWSM CTC v4 1B](https://huggingface.co/espnet/owsm_ctc_v4_1B)、[Hikari](https://huggingface.co/sbintuitions/hikari-medium)、[WhisperRT](https://huggingface.co/MLSpeech/WhisperRT-Streaming)、[X-ASR](https://huggingface.co/GilgameshWind/X-ASR-zh-en)、[fujielab](https://github.com/fujielab/fujielab-asr)。这里只保留身份／排除证据，不代表获得首筛名额。

证据底稿：[扩展结论](../research/expanded-review.md)、[日语研究](../research/japanese.md)、[原生后端](../research/native.md)、[流式研究](../research/streaming.md)、[R2T2 Mac 复核](../research/r2t2-mac-feasibility.md)。除明确固定的源码／snapshot 外，URL 均为来源入口，不是内容锁；未锁定不得正式计分。
