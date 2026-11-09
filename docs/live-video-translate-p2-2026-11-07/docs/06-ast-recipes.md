# 06 · AST：Gemma 日语语音直译的有界探索

> 2026-11-09执行进展：Gemma4官方协议已实跑两轮各88音频及完整音频链路G0；实测入口为p0/p2/b3_worker.py，不是生产backend。 [B3报告](../../p2-b3-results-2026-11-09.md)为实际证据。下述配方与原型保留设计语义；候选replay/accept/产品接入仍为PLANNED且受质量门槛约束。

> 核验日期：2026-11-07。结论等级：官方资料与源码已核验，目标 M1 Pro 性能和日译中质量未实测。
> 本章的B3首筛命令现已实现于实验包；未执行的后续变体与产品适配器仍为 **PLANNED**，按上述进展及报告区分。
> 目标是检验 Gemma 4 是否增加一个有效选择。未通过质量门槛，不接入整套生产字幕链路。

## 1. 本轮判断与范围

Gemma 4 E2B 确实支持音频，MLX 也有从音频特征、编码器、投影到文本生成的完整实现，因此可进入有限测试。
官方文档允许源语言与目标语言的语音翻译提示；不能说它只支持翻译成英语。
但官方技术报告的日语 AST 成绩是 JA→EN，没有找到官方 JA→ZH 与 M1 Pro 低占用组合的验证。[S1–S3]

本轮只把 **Gemma 4 E2B / MLX 4-bit / 完整音频处理链 / 官方双语协议**作为主试，ID 为 AST-G4-OFFICIAL，预登记路线 Q。
Gemma 3n MLX 只作为有明确原因的替补；GGUF 信息用于核验部署差异，不扩成全运行时矩阵。
标准入口每次处理一个完整音频片段；文本逐 token 输出不代表持续接收音频的原生流式会话。
用 VAD 取得三秒片段再调用 Gemma，仍然是 chunk 请求，边界等待和短段缺语境问题仍存在。

| Gemini 判断 | 本章采用的结论 |
|---|---|
| 音频直译能必然消除同音词误译 | 不成立。可减少外部日文转写接口的约束，但声学歧义、缺上下文和生成错误仍可能存在。 |
| Gemma 4 每秒 25、3n 每秒 6.25 音频 token | 官方名义速率成立；不能据此推算完整音频推理时间。[S2] |
| 三秒音频 prefill 在 M1 Pro 上不到 20ms | 无可用证据；特征、音频编码器、投影和自回归输出都必须计入。 |
| PLE 令实际占用自动等于有效参数量 | 不成立。参数参与计算的方式与权重是否实际驻留是不同问题。 |
| MLX/GGUF 文件可加载即支持音频 | 不成立。须同时核验音频模块、处理器、投影、模板和实际张量路径。 |
| ja+zh JSON 能免费获得双语字幕 | 不成立。日文、字段名与结构均增加输出成本，可能延后中文并引入解析错误。 |

## 2. 模型与运行时锁

正式执行保存模型 revision、源 checkpoint、运行时 commit、配置/处理器/模板/权重 SHA256。
源 checkpoint 与转换包 revision 不是同一个概念；不能只记一个模型昵称。
主试的 MLX 转换包来自较早的 Google checkpoint，不能把当前 Google HEAD 当作转换一致性参考。

| 字段 | 主试固定值 |
|---|---|
| 模型 | mlx-community/gemma-4-e2b-it-4bit |
| 模型 revision | 238767527555cb75a05732a84dff5d6ba0dd6809 |
| 模型卡声明的 Google source revision | 70af34e20bd4b7a91f0de6b22675850c43922a03 |
| 运行时 | Neywa-Labs/mlx-vlm，原 Blaizzy/mlx-vlm |
| runtime commit | a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4 |
| 主体量化 | 4-bit、group_size=64、affine；按实物 config 复核 |
| 音频处理器 | 随模型固定的 processor_config.json；feature_extractor 必须实际实例化 |
| 输入 | 16kHz、mono；处理波形为 float32、范围 [-1,1]；0 < duration ≤ 30s |
| 音频编码器/投影 dtype | 使用该发布包的原有张量 dtype，preflight 实读登记；不得推定全部为 4-bit |
| 模板/解码 | instruct chat template、num_audios=1、thinking=false |
| 采样 | temperature=1.0、top_p=0.95、top_k=64；两轮 seeds=11/29 |
| 首轮缓存 | 每个片段独立 KV；模型进程持久加载；不启用跨片段音频缓存 |

模型卡的约 2.3B 是有效参数；HF 原始 Gemma 4 E2B 约 5.1B 总参数，音频编码器约 305M。[S1–S3]
M1 Pro 使用统一内存，CPU、Metal 与所谓卸载参数并非互不影响的三个物理内存池。
PLE/MatFormer 的架构设计不能证明选定 MLX 路径已经实现特定卸载，也不能证明默认运行在 ANE。
记录实际 runtime 参数与驻留内存；不要将进程 footprint 和 GPU allocated 简单相加。

### 2.1 有界替补与不进入首轮的制品

| 制品 | 固定 revision | 处理规则 |
|---|---|---|
| mlx-community/gemma-3n-E2B-it-4bit | 66e7276cfc589073b4f92ebed58c05301f990222 | AST-G3N-FALLBACK；仅主试明确受阻或有可检验的替补理由时启动。 |
| google/gemma-3n-E2B-it | 5e092ebca197cdcd8d8b195040accf22693501bc | 原始模型来源；不能假定该 revision 就是所选转换包的实际来源，须查转换记录。 |
| ggml-org/gemma-3n-E2B-it-GGUF | 989cffaba23976934324f5e3abfabe31b30eb73b | README 明确不含多模态，无 mmproj；状态 blocked_artifact，不作 AST 质量比较。 |
| ggml-org/gemma-4-E2B-it-GGUF | 6938c58e698502037401a5f19f6905f2fb2f7dee | 具有音频部署材料；本轮保留核验信息，不与主试并跑全套。 |
| ggml-org/llama.cpp | f7591a6a01f9273de03331732bdc0b39721168be | 已核验 Gemma 4 E2B/E4B 音频支持；CPU projector/Metal projector 都不等于 ANE。 |

当前 MLX Gemma 3n 配置 audio_seq_length=188，源码将短音频特征补到 audio_soft_tokens_per_image=188。
因此“三秒 × 6.25 ≈ 19，所以 LM 只处理十九个音频位置”不适用于这条已核验实现；必须记录实际 shape、mask 与输入长度。[S7][S9]
如果未来启用 Gemma 4 GGUF，必须显式固定文本主体及 mmproj 两个文件，首个质量探针使用 BF16 mmproj。
历史音频实现曾对 F16/Q8 projector 的数值误差敏感；不能把文本量化测试等同于音频投影量化测试。[S12]

该 GGUF 快照 .src_sha 区分 PRIMARY、QAT_Q4_0、QAT_Q4_0_UNQUANTIZED 与 ASSISTANT。
例如 PRIMARY=3e22461f65e89153144f8adb70e3b8c2cc9845a7，QAT_Q4_0=ebc7e1a211354561464cb82ed6d886792138dcb6。
执行器必须根据所选文件的 conversion log 确认来源；不同 source、QAT、dtype 同时变化时只能比较整套方案，不能归因于 MLX/GGUF 框架。

## 3. G0：证明音频真正进入模型

先实现 AST-G4-OFFICIAL 的 capability.json，完成以下检查后才运行冻结集。
使用三至五条内容不同、人工可听清的日语探针，加等长静音、仅 BGM、换音频和缺音频对照。
参考转写仅用于事后评分，不能出现在输入提示；同一文字提示必须固定。
能力烟测允许附加 zh-only 指令核验音频响应，但只写 capability 诊断，不计正式质量分、不改变正式主配置 ID。

1. 校验采样率、声道、时长与样本范围；超过 30 秒显式拒绝，不能让处理器静默截断。
2. 保存 processor 实际类名，确认非空 feature_extractor、音频 tower、投影和对应非空权重。
3. 保存 input_features / mask 的 shape、有效长度、finite 检查、特征哈希及实际音频占位数。
4. 在音频编码/投影入口记录调用次数与输出 shape；确认不是只把文件路径作为文字传入。
5. 不同音频应产生不同特征，并使输出内容随实际音频改变；缺音频、静音与 BGM 检查编造内容。
6. 输出内容对照不能替代结构断言：两段不同语音偶然得到同一句中文，单独不足以判定音频被忽略。
7. 保存模板、解码参数、首 token 事件、完整输出、finish_reason、异常与超时。
8. 模型只加载一次；冷启动、暖机与正式推理分开记账。

MLX issue #903 曾报告音频被忽略，已由 #931 修复；当前包与源码已有相关音频组件。
这段历史说明入口审计必要，不意味着当前版本仍然失败。[S10][S11]

| 状态 | 使用条件 | 是否进入质量排名 |
|---|---|---|
| blocked_download | 权重/依赖无法取得 | 否 |
| blocked_artifact | 制品本身缺音频模块或投影 | 否 |
| blocked_runtime | 模型类、处理器或张量路径不能有效运行 | 否 |
| failed_task | 音频路径有效，但任务方向/指令遵循/输出不能通过探针 | 记录真实任务失败 |
| ready_for_quality | G0 结构与行为对照通过 | 进入 G1，尚未证明胜出 |

缺少官方 JA→ZH benchmark 不应直接判 blocked；用小样本验证实际任务。
如果输出始终为英文或无关文字，先限量排查模板和音频输入；排除适配问题后再判该配置失败。
最多一次针对明确故障的修复与重跑；原因不明时保存证据并停止，不无限更换依赖和量化。

## 4. G1：同冻结音频、同语义门槛

### 4.1 先测官方格式；zh-only 作为后续有界对照

AST-G4-OFFICIAL 是 B3 的唯一 AST 正式主配置，忠实采用官方格式：先日文转写，换行后以 Chinese: 开始中文。
固定提示为官方模板填入 Japanese / Chinese，并将结果语言约束为简体中文；实际 prompt 及其 hash 在生成前冻结。
执行器从已锁定的官方音频指南提取其AST模板，固定源语言Japanese、目标语言Chinese，并明确简体中文要求。模板快照与渲染结果写入prompt-lock；不得临时改写为JSON或加入参考答案。
保存完整日文与中文输出，只提取中文做字幕语义评分；全部输出 token、生成耗时与失败都保留。
不得删除日文生成时间，或把首个日文 token 当作首条中文字幕；也不能声称它完全跳过了可见转写阶段。
当前官方文档推荐先转写再翻译，但没有证明 zh-only 是被禁止的任务形式；采用官方主配置是本方案的控制选择，不捏造强制约束。

AST-G4-ZH 只作为 B4 后续菜单中的一个候选，占全局最多三个附加正式配置中的一个名额；并非跑完官方格式后必跑。
需要主配置已有资格或存在单一可验证的协议/日文生成成本假设，再预登记后执行；无收益、无名额即归档。
其固定提示为“Translate the Japanese speech in the audio into Simplified Chinese. Output only the translation. Do not invent speech that is absent.”

首轮不加历史，不生成 ja+zh JSON，不同时改切片、VAD、缓存、采样和量化。
两个输出协议的结果不能拼接成逐句择优的虚拟模型；不能只报表现较好的 seed。

### 4.2 冻结集与双轨评估

**真实任务轨道：**消费冻结的 88 段音频，使用既有 34 个语义单元映射和两轮 seed=11/29。
62 条文本 MT 输入不是 AST 的输入；它们只帮助定位基线误差，不能替代音频测试。
保持音频边界、顺序、可用时刻和语义评分规则。不能为了模型方便只留下句子完整、背景安静的片段。
中文按信息保留、遗漏、错译及关键错误判定；有无日文转写、CER 或中文表面相似度不决定最终胜负。
每条保留基线 ASR、基线中文、AST 中文与对应音频 ID，便于解释误差来自何处。

**机制诊断轨道：**单独设置少量同音/歧义挑战，不能用它替换冻结集或抬高主评分。
挑战必须同时包含可利用当前音频/前文消歧的案例，以及可用声学信息本来无法唯一判断的案例。
“機会/機械”“橋/箸”只是例子；不能看到标签就假定发音或重音必定足够区分，须考虑方言、发音和上下文。
人工先标出消歧证据、出现时间与是否对所有方案可见；真实歧义允许多个合理答案或标为不可唯一判定。
再观察 ASR 汉字错而 MT 是否仍可恢复、AST 是否减少错译，以及 AST 是否出现新的臆测。
挑战参考文本与正确汉字不能进入提示；未来音频、未来字幕和人工参考中文都不能进入模型。
可额外用完整语义音频做离线诊断，但其未来信息优势必须标注，不能参与实时胜负。

### 4.3 必须通过的质量门槛

使用相同 34 项、相同评分方式，重放确认基线两轮为 20/18；无法复现时先回到基线复核。
每轮都要求相对各自基线 **至少 +3 净增**，即最低 23/21，并且 **0 个新增关键错误、0 个保护项损失**。
净增等于修复的单元减去退化的单元；不接受“修好了几条”但隐藏退化。
保护项沿用总方案的预冻结清单；即使新增关键错出现在基线原失败单元，也阻塞晋级，不能只检查基线正确项。
否定、主体关系、数量、事实方向等关键错误逐项独立复核；当前由AI做文本证据复核并标暂定，不安排无人承担的人工任务。保守算术不过门则停止；过门但参考待确认时仅允许08章的暂定工程测量，不视为正式质量验收。
若只有某个题材改善，记录为局部线索；不把它宣布为全局优于现有版本。

## 5. G2：通过质量后才做连续 1× 实时回放

真实视频按 1× 音频到达速度释放，使用相同捕获、切片释放、队列和上屏规则。
不能预先让模型读到未来音频，也不能将加速离线跑完的 RTF 当作端到端字幕延迟。
标准 AST 请求仍以片段为单位；不能在报告中改名为原生流式 ASR。
同一模型持久运行，每片段 KV 独立；先隔离模型能力，再讨论上下文或缓存优化。

| 计时点/指标 | 必须记录的内容 |
|---|---|
| 捕获与排队 | audio_capture_end、chunk_release、queue_enter/leave |
| 预处理 | 重采样/特征的 start/end；不得藏入未计时的读取步骤 |
| 音频编码 | audio_encoder start/end；投影独立计或明确包含于该阶段 |
| 语言模型 | LM prefill start/end、first_generated_token |
| 中文 | first_readable_chinese、first_semantically_usable_chinese、full_zh_end |
| 上屏 | generation_end、WebSocket send、DOM render |
| 运行状态 | context/output tokens、finish_reason、队列长度、异常、截断、片段 ID |

MLX 惰性执行/异步提交要求微测边界显式求值或同步；不能拿 Python 调用返回时间代替 GPU 完成。
同步会改变调度，因此阶段剖析与无插桩端到端回放分开报告。
官方双语格式把全部日文生成计入中文等待，zh-only 也按实际完整正确中文计分。
资源按统一口径采集进程树 CPU、内存/footprint、系统 GPU、内存压力/压缩/swap及背景负载。
不以有效参数数、模型文件大小或 token/s 代替内存、功耗、端到端 P95 测量。

持续处理跟不上 1× 到达并造成队列增长，则停止实时候选；不能丢弃困难段来改善延迟。
质量通过但占用超过总方案预算，可标记为可选质量模式；长片段仅改善离线结果，可标记为回看候选。
正式30–60分钟稳定性验收要求质量、延迟和占用均通过统一门槛。符合08章provisional工程例外时可在相同预算内先做工程长稳，参考可信度仍待确认；不得以长稳跑完倒推质量或发布通过。

## 6. 后续变量与止损

ZH、HISTORY、JSON 都是 B4 的可选菜单，不是必跑序列，与 MT、ASR、缓存等机制共享全局最多三个附加正式配置。
AST-G4-HISTORY 只在 G1 通过且有预算、有具体语义假设时选择：无历史对照 vs 前两条实际已经生成并发布的中文字幕，最多 256 tokenizer tokens。
必须使用本次 run 自己的历史，记录发布时间与文本哈希；不使用参考译文、另一个更强模型或未来字幕。
对错误历史造成的污染单独检查；seek、跨视频和长间隔按统一规则重置。
跨片段缓存需另证正确性：相同音频占位 token ID 不代表音频 embedding 内容相同，不能据此误命中 KV。

AST-G4-JSON 只在明确接口需求且尚有 B4 名额时选择：在已胜出的配置上比较结构解析失败、输出 token 与中文延迟。
先由应用包装结构即可满足接口时，不要求模型额外生成 ja 字段。
AST-G3N-FALLBACK 只做 G0 和同一小样本探针；仍看不到目标任务价值即停止，不再扩散到更多量化。
只有替补明确通过自己的 G0 并有实际收益信号，才获准跑同样的 88 段/34 项完整门槛。

## 7. 拟新增统一命令与适配入口

以下首筛命令已在B3实验包实现；replay/accept及生产适配仍为 **PLANNED**，且必须满足后续阶段门槛。
PATH、registry 内容与实验输出目录以总方案约定为准；B3 preflight将G0记录到该实验g0/capability.json，质量probe保留独立目录。

~~~bash
python -m p0.p2.cli --run-dir PATH --registry PATH preflight --experiment AST-G4-OFFICIAL
python -m p0.p2.cli --run-dir PATH --registry PATH probe --experiment AST-G4-OFFICIAL --mode fixed-cuts
python -m p0.p2.cli --run-dir PATH --registry PATH score --experiment AST-G4-OFFICIAL
python -m p0.p2.cli --run-dir PATH --registry PATH replay --experiment AST-G4-OFFICIAL --repeat 1
python -m p0.p2.cli --run-dir PATH --registry PATH replay --experiment AST-G4-OFFICIAL --repeat 2
python -m p0.p2.cli --run-dir PATH --registry PATH accept --experiment AST-G4-OFFICIAL --stage engineering
python -m p0.p2.cli --run-dir PATH --registry PATH report
~~~

每条命令仅在上一阶段产物允许时运行；engineering 不等于发布，holdout 另按总方案执行，不连跑后跳过人工与独立材料。

MLX 底层入口已按固定源码核验，下面是**拟新增适配器原型**，不是已实现的项目文件或完成测试的代码。
安装固定运行时后，先核对签名；模型从固定 HF revision 下载到 local_model_path，一次 load，多片段调用。

~~~python
from mlx_vlm import load
from mlx_vlm.generate import stream_generate
from mlx_vlm.prompt_utils import apply_chat_template
model, processor = load(local_model_path)
prompt = apply_chat_template(processor, model.config, instruction,
    num_audios=1, enable_thinking=False)
for event in stream_generate(model, processor, prompt=prompt, audio=[wav_path],
    max_tokens=256, temperature=1.0, top_p=0.95, top_k=64):
    record_monotonic_event(event)  # PLANNED：时间、增量、finish_reason和token统计
~~~

适配器另实现随机种子、输入验证、音频路径审计、阶段计时、超时与结果持久化。
当前 event.text 是增量片段，最后还有 flush；不能直接用 event 数当作 token 数。
达到 token 上限的 length finish 必须作为截断记录，不得清洗成完整正确字幕。
每个实验至少产出 capability.json、锁定配置、原始输出、事件 JSONL、34项复核、资源统计和决策理由。
最终状态必须区分 blocked / failed / shortlisted；不能把未成功接通音频写成模型输给基线。

## 8. 一手证据

[S1] [Google Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)：参数、任务与推荐提示。
[S2] [Google audio guide](https://ai.google.dev/gemma/docs/capabilities/audio)：16kHz、30秒、token速率与翻译示例。
[S3] [Gemma 4 technical report](https://arxiv.org/html/2607.02770)：日语 CER、JA→EN AST与音频编码器；不作为本机成绩。
[S4] [主试固定模型快照](https://huggingface.co/mlx-community/gemma-4-e2b-it-4bit/tree/238767527555cb75a05732a84dff5d6ba0dd6809)，[processor](https://huggingface.co/mlx-community/gemma-4-e2b-it-4bit/raw/238767527555cb75a05732a84dff5d6ba0dd6809/processor_config.json)。
[S5] [MLX Gemma 4 固定源码与示例](https://github.com/Neywa-Labs/mlx-vlm/blob/a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4/mlx_vlm/models/gemma4/README.md)。
[S6] [音频路径实现](https://github.com/Neywa-Labs/mlx-vlm/blob/a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4/mlx_vlm/models/gemma4/gemma4.py)，[音频处理器](https://github.com/Neywa-Labs/mlx-vlm/blob/a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4/mlx_vlm/models/gemma4/processing_gemma4.py)。
[S7] [Gemma 3n 固定配置](https://huggingface.co/mlx-community/gemma-3n-E2B-it-4bit/raw/66e7276cfc589073b4f92ebed58c05301f990222/config.json)。
[S8] [无多模态的 Gemma 3n GGUF](https://huggingface.co/ggml-org/gemma-3n-E2B-it-GGUF/raw/989cffaba23976934324f5e3abfabe31b30eb73b/README.md)。
[S9] [MLX Gemma 3n 的188位置补齐](https://github.com/Neywa-Labs/mlx-vlm/blob/a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4/mlx_vlm/models/gemma3n/gemma3n.py)。
[S10] [历史音频问题 #903](https://github.com/Neywa-Labs/mlx-vlm/issues/903)，[已合并修复 #931](https://github.com/Neywa-Labs/mlx-vlm/pull/931)。
[S11] [固定 stream_generate 实现](https://github.com/Neywa-Labs/mlx-vlm/blob/a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4/mlx_vlm/generate/dispatch.py)。
[S12] [Gemma 4 GGUF音频支持PR](https://github.com/ggml-org/llama.cpp/pull/21421)，[固定多模态文档](https://github.com/ggml-org/llama.cpp/blob/f7591a6a01f9273de03331732bdc0b39721168be/docs/multimodal.md)。
[S13] [Gemma 4 GGUF固定源记录](https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/raw/6938c58e698502037401a5f19f6905f2fb2f7dee/.src_sha)。
