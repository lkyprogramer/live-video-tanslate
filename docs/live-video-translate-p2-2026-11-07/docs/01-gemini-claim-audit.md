# Gemini 反馈的逐项核验与采用决定

## 1.1 对总体归因的修正

Gemini 提出的“切片上下文不足”“GPU 竞争”“级联误差”都是可研究的机制，但没有证据把它们合并成所有失败的唯一原因。

当前代码已经是 Reazon CPU + Hy-MT2 Metal。原端点由 Silero 决定，并非统一固定 1.5–3 s 硬切。现有记录也没有持续积压。因此本轮需要分别验证：输入边界、ASR 语义、MT 语义、提交等待、排队与显示。只有可对应到实际请求的设备轨迹，才能把某段等待归因于 GPU command queue。

日语中同音词的字符选择会传播到 MT，但“ASR 选错字，下游必定无法恢复”过于绝对：语言上下文有时能纠正它，更多时候也可能误纠正。AST 减少一个显式文字中间层，并不消除声学歧义、语言推断错误或幻觉。采用 AST 的依据必须是实际中文错误减少。

## 1.2 主张审计表

| Gemini 主张 | 核验判定 | 纳入计划的方式 |
|---|---|---|
| PLaMo 3 Translate 刚发布，含 0.8/2/8/31B | 已确认，2026-11-05 | 只开放 0.8/2B 首筛 |
| PLaMo 正式支持日译中 | 已确认，ja → zh-hans | 使用官方 PFML3 模板 |
| 官方 MLX4bit/GGUF | 已确认 | 精确固定格式、revision、tokenizer |
| 0.8B MLX 仅约 500 MB | 错误；MLX 约 0.98 GB，GGUF Q4_K_M 约 0.57 GB | 不混写文件格式或运行内存 |
| M1 Pro 整句翻译不到 40 ms | 无匹配实测证据 | 删除性能承诺，测 TTFT 与最终可用中文 |
| AutoRound 零损量化 | 官方平均指标接近，但有非零变化 | 实际脏 ASR 文本回归仍必需 |
| 日语训练必然改善零代词/敬语 | 缺少本任务专项证据 | 单独标注人物关系、疑问、主语增补 |
| PFML3 支持历史与 glossary | 已确认 | H0、HS、HP、G0 分组；缓存后测 |
| 日文礼貌等级开关直接改善日译中 | 方向不符，form 针对目标日文 | 中文目标不设置 form |
| Voxtral 原生流式 | 已确认，有 encoder/decoder 状态 | 连续因果输入测试，不强迫先切 88 段 |
| transcription_delay_ms=240 锁定 240ms 中文延迟 | 错误 | 960ms 为主，240ms 只作质量/延迟消融 |
| feed/step 可保证非阻塞、固定时间片 | feed 可排队；step 的 ingest 仍可能处理积压 | 单个推理执行者、队列水位、实际 step wall time |
| Fun-ASR Nano 的 token stream 是原生音频流式 | 不成立；当前 MLX 先编码完整输入 | 固定切片 ASR 候选，不标 native streaming |
| FunASR BGM 训练代表日语综艺必胜 | 无对应项目证据 | 只有可靠 BGM 标注齐备时开放专题组 |
| FunASR MLX/GGUF 同样支持 hotwords | 不可通用；当前 CrispASR GGUF prompt 固定 | MLX 热词单独验证，不迁移宣称 |
| SenseVoice 一次非自回归推理 | 核心结构方向成立 | CPU INT8 候选 |
| SenseVoice M1 Pro 每句 15–25ms | 输入长度、硬件和条件不足 | 不写入验收承诺 |
| 透传情绪标签会大幅提高中文 | 未证明 | 先纯文本；情绪/事件仅作独立消融 |
| Gemma 4 E2B / 3n E2B 音频能力 | 模型真实；不同运行格式支持不同 | 先做音频路径功能验证 |
| AST 在 M1 直接日译中已证实优势 | 未找到完整证据；官方成绩含 JA→EN | JA→ZH 为待测，不误称只支持英语 |
| 2B effective 等于 2B 实际驻留 | 错误 | 记录总权重、音频模块、KV、峰值 |
| PLE 自动把参数卸载并隔离 GPU | 未验证所选 runtime 有此优化 | 不据宣传推导功耗或占用 |
| 3秒音频约19token，所以 prefill<20ms | 推导错误；当前 MLX 3n 还会补到188音频位置 | 分别计特征、encoder、projector、LLM |
| 直接输出 ja+zh JSON 是最低成本 AST | 未证明，会增加解码与解析开销 | 官方输出、zh-only、JSON分组 |
| AlignAtt 取最后一层峰值即可100%锁词 | 错误；实现依赖对齐头与启发式阈值 | 查实际代码、测误提交与改写 |
| AlignAtt 固定省60%重算、300–500ms | 无同机同任务依据 | 计编码音频总量与真实中文字幕延迟 |
| 4–6帧恒等于120ms | 不成立；依模型特征步长/下采样 | 明确 frame 单位后换算 |
| cpuAndNeuralEngine 意味着全链路只走ANE | 错误；允许CPU，外围仍可能用MLX/Metal | 记录每组件 computeUnits 与实际路径 |
| Qwen CoreML 只有180MBencoder就够 | 不完整；需要decoder/embedding/执行栈 | 核对整个组合，不能仅报encoder体积 |
| Kotoba v2.2 是新ASR权重突破 | 官方说明是v2.0加diarization/punctuation管线 | 不因版本号重跑已负的v2.0 |
| CPU/ANE/GPU 三者物理隔离，GPU100%独占带宽 | 错误；M1统一内存与共享系统资源 | 作为调度分配试验，不能承诺物理隔离 |
| Index2B Q8 应立即替换翻译 | 与本仓库13/13负结果冲突 | 本轮不重开 |

一手证据分别列在 MT、ASR/硬件、AST 专章及 [来源索引](12-sources.md)。这张表的“无证据”是指未找到匹配**指定模型、运行时、M1 Pro、日→中、计时口径**的证据，不代表该技术永远无效。

## 1.3 采纳后的路线分层

- **直接进入第一批**：Reazon 官方 padding、日文标点恢复、PLaMo 0.8B H0、PLaMo 2B H0。
- **第二批固定切片候选**：SenseVoiceSmall INT8、whisper-ja-760M。FunASR 需专题素材与部署能力就绪。
- **连续输入路线**：Voxtral 960ms；240ms 为后续消融。VibeVoice 只作一个有界替补。
- **端到端研究路线**：Gemma 4 E2B，一个完整音频运行栈；3n 只在定义的替补条件下开放。
- **证据触发的工程改造**：AlignAtt、Qwen CoreML 调度分配、PLaMo 历史与缓存。
- **可选输入路线**：日文字幕轨、硬字幕 OCR、点播预读，分开计分。

保留先前的轻量发现，不把新模型宣传覆盖掉已确认的代码差异；也不把所有新架构强行放进旧短切片首筛，以免测试方法先排除了它们的设计优势。
