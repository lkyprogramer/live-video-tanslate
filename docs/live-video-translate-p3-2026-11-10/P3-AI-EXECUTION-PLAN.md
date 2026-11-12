# M1 Pro 日语实时中文字幕：P3 完整 AI 执行方案

版本：2026-11-10（北京时间）；项目基点77e24c3e490a11721041a40ad4bd203f51f84039。

自动生成阅读副本：分章、config和prompts是维护真源。修改后运行scripts/validate_package.py --refresh；不要直接修改本文件。本包没有新M1模型实测或已实现的P3 runner，入口见[README](README.md)。

## 文档目录

- [00-contract-and-baseline.md](docs/00-contract-and-baseline.md)
- [01-findings-and-decision.md](docs/01-findings-and-decision.md)
- [02-experiment-design.md](docs/02-experiment-design.md)
- [03-phase-tasks.md](docs/03-phase-tasks.md)
- [04-r2t2-mac-spike.md](docs/04-r2t2-mac-spike.md)
- [05-japanese-cache-aware.md](docs/05-japanese-cache-aware.md)
- [06-secondary-candidates.md](docs/06-secondary-candidates.md)
- [07-observability-and-scoring.md](docs/07-observability-and-scoring.md)
- [08-gates-and-stop-rules.md](docs/08-gates-and-stop-rules.md)
- [09-runtime-integration.md](docs/09-runtime-integration.md)
- [10-ai-execution-and-recovery.md](docs/10-ai-execution-and-recovery.md)
- [11-source-ledger.md](docs/11-source-ledger.md)
- [12-delivery-validation.md](docs/12-delivery-validation.md)

---

<!-- BEGIN docs/00-contract-and-baseline.md -->

# 00 目标 合同与已验证基线

## 交付性质与优先级

本包是2026年11月10日的P3执行规格，针对 live-video-tanslate 的M1 Pro本地日语音频到简体中文字幕。它替代旧P2包中“下一轮候选和待开发任务”的建议，不覆盖P2已测事实。远端核验基点为77e24c3e490a11721041a40ad4bd203f51f84039，提交时间北京时间2026年11月9日21:30:30。模型新结果均未运行；本包的辅助工具测试不代表M1验收。

优先顺序是：固定事实和证据底座 → R2T2低延迟稳定前缀 → 123M日语cache-aware RNNT → 最多一个次候选 → 最多两个完整链路验证。允许结论为“新路线运行不通”“无净收益”或“资源代价不合适”，不以找到赢家作为完成条件。

本轮只交付文档包；执行AI只有在用户另行授权实施后才能改仓库、下载模型或运行实验。未来实施默认可在独立worktree进行小范围实验代码、隔离环境和所选公开模型准备；不自动提交到main、push、发布、升级系统、修改生产默认或申请私有权重。

## 不可改变的产品合同

以下来自[项目v1合同](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/subtitle-backend-contract.md)。冲突时先报告，不静默放宽。

- 输入16 kHz单声道s16le；3200 samples即200 ms完整包，仅最后一包可短。网络包不等于ASR块或VAD窗口。
- 仅使用当前和过去已到达音频。原字幕、OCR、未来预读、云端ASR/MT、4090卸载均不进入本轮。
- 对外仅最终简体中文字幕；内部临时日文、情绪标记、翻译草稿不得上屏。日文和中文的不可回写分别验收。
- 每session seq递增且只发一次；结束样本非递减；0 <= start_sample < end_sample <=真实已接纳样本。生成的补零不增加水位。
- 全局至多一个活动或清理中的session；PCM待处理上限64000 samples，入队等待最多1秒；待译段最多4，另有一个在途MT。
- 浏览器bufferedAmount超过64000字节取消；正常drain最多8秒。暂停、seek、切源、取消后旧epoch不得显示。
- 保持127.0.0.1监听、Host/Origin/token校验和现有中文过滤器。禁止为测试关闭鉴权或上传私密音频。
- 模型、音频、带内容的原始日志只保留本地忽略目录；公开仓库只收去敏摘要、配置hash、判定与已授权代码。

目标机器为用户M1 Pro、32GiB。CPU/GPU核数、OS、供电、Chrome版本、显示器、视频分辨率、后台负载以preflight实际采集为准，不把其他机器宣传数字当作本机事实。

## B0 固定配置与分母

| 项目 | 固定起点 |
|---|---|
| ASR | ReazonSpeech k2 v2；INT8 encoder/joiner、FP32 decoder；CPU provider 2线程 |
| ASR revision | 291488c8151be24d7da4bf7af26e533fad96e407 |
| 运行时 | sherpa-onnx 2.0.1；现有engine/.venv与锁，不原地升级 |
| VAD | 现有Silero v4、600ms静音等待、4s最长语音、现有整包调用 |
| VAD SHA256 | 9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6 |
| MT | Hy-MT2 1.8B MLX 4bit，M0模板与现有采样策略 |
| MT revision | e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912 |
| D0资产 | 9段连续PCM，88个ASR切片，62条主MT输入，34个必要语义项 |
| 重复运行 | MT seed 11和29，不是68个独立样本 |
| 已有离线正确项 | 20/34与18/34 |
| 已有1倍速5秒覆盖 | 19/34与17/34，仅作为同口径复现参照 |
| 真值状态 | 15项待复听；现有AI暂判不能改成human_verified |
| 默认资源目标 | 热态完整进程树physical footprint峰值2.5GiB，ASR与MT共同测量 |

来源：[P2 B0](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/p2-b0-results-2026-11-08.md)。原速/离线/Chrome/旧评分版本分开列；如同轮基线漂移，先归因和重新冻结，不能选择历史较弱分数当对照。

B0记录ASR排队P95约0.247ms、MT排队约0.502ms，ASR计算约150ms、MT计算约449ms，VAD放行滞后约628ms。分项P95不能相加。现基线ASR在CPU、MT在GPU；没有证据把当前主要问题直接归因于GPU命令队列阻塞。正确来源日文直接送Hy仍有错误，ASR优化不保证中文同比改善。

## 已失败路线登记

| 已测内容 | 现行处置 |
|---|---|
| PLaMo0.8B/2B H0 | 均17/17，不能因日语专精宣传再开同配置 |
| SenseVoice INT8 | 8/9，接口清理后仍退化，不作为新候选 |
| whisper-ja760 CT2 | 16/15且CPU开销高，不重复 |
| Voxtral Realtime 960ms | 17/18，原生流已真实接入，不再称“尚未试真流式” |
| Gemma4官方双语AST | 6/8，音频塔实际运行，不归因于“没接音频” |
| Gemma4只中文 | G0语音0/3合规，正式34项未运行，不记为0/34 |
| Haas、Parakeet JA TDT、Moonshine日语Small、Nemotron、Apple SpeechAnalyzer、PengChengStarling | 既有结果保留；不同运行框架不自动获得新模型名额 |
| 真实扩边、补零、标点、上下文/术语、VAD重扫 | 无新错误机制证据不重开；不得用未来原文或字幕作提示 |

精确结果见11章来源。不同年代评分不拼成统一排行榜。

## 现有工具与清理状态

P2的p0/p2/cli.py、freeze.py、runner.py、realtime.py、events.py、scoring.py及B1至B4专用实现已存在。CLI虽然显示replay/accept，B1至B4候选分支仍抛unsupported；只有B0基线实时回放已实现。P3必须复用可用基础，不虚构所有候选已能实时接入。

最近清理保留了当前Reazon/Hy/engine环境和证据，删除了未采用候选模型与环境。[清理记录](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/local-runtime-cleanup-2026-11-09.md)不是恢复全部候选的授权；只恢复当前选定的一项。旧证据可以带hash引用，不能标成新推理。

版本发生变化时，记录repo-diff.md，区分无关文档变更、输入/评分变化和执行变化。不得为匹配旧结果改写历史冻结hash。

<!-- END docs/00-contract-and-baseline.md -->

---

<!-- BEGIN docs/01-findings-and-decision.md -->

# 01 新发现与本轮决策

本轮依据附录research中的2026年11月10日扩展调研，而不是重新沿用“所有新模型都差不多”的假设。45次Exa查询返回395条含重复结果、311个精确URL；不代表全文读了311篇。详细检索式与所有命中URL随包保留。

## 必须区分的三层

1. 训练或架构改善了某个识别条件。
2. 有能运行该精确权重的Mac后端，且数值、前端、缓存状态可信。
3. 在本项目过去音频、日中翻译、不可回写和资源约束下获得端到端收益。

上层成功不推出下层成功。每个run明确evidence_level：source_verified、author_reported、local_measured或inferred，不能混列。

## 选择的路线

| 路线 | 新假设 | 首次回答的问题 |
|---|---|---|
| R2T2 | 用最长稳定前缀加Wait后训练，降低短前缀不可逆输出的错误 | 全llama能否在M1构建和正确运行；同块长能否比基础Qwen好；中文是否也改善 |
| 123M日语cache-aware RNNT | 轻量音频缓存与可调右文减少重算和边界损伤 | 精确NeMo权重能否保留缓存导出/运行；RNNT相对B0是否有收益 |
| Omni CTC300M v2 | 独立的非自回归多语家族 | 现成INT8路径在本项目短句是否可靠 |
| 假名小模型 | 延后汉字选择、缩小声学模型 | 先验证正确假名到Hy的语义保持，再考虑实际ASR |
| Fun-ASR-MLT-Nano | 已发现但未完成的另一日语质量探针 | 若准备成本低，在唯一备选席位内与其他备选择一 |

研发策略比较：全面模型矩阵会迅速耗尽测试与评分预算；单纯迁移浏览器框架主要改变部署，不能直接检验新训练路线。本包采用两条主线的有界可行性验证，只有通过者进入质量与实时测试。不是认定主线一定获胜。

## 关键数值的正确解释

R2T2官方FLEURS日语CER：基础离线5.42%，基础2s可修订7.23%，基础160ms/U1为36.07%，R2T2同160ms/U1为8.66%、U0为10.15%。显著改善的是困难短前缀条件；没有超过完整音频，也不是与Reazon对照。160ms不是计算或中文字幕延迟；中英文后训练的日语迁移不等于日语专精。

123M RNNT五领域CER在1040/480/80/0ms右文为12.39/12.65/13.43/14.04%。右文不是总体算法延迟，RTX6000 Ada吞吐不是M1单流速度。M1-6是模型编号。论文Reazon NeMo620M对照不是当前k2基线。

Q4文件约1.0GiB加0.3GiB mmproj不是驻留；Audio8的0.1B仅是文字模型、全模约324M；ANE配置不是实际派发；假名KER不是书面日语CER。Hikari日到英、WhisperRT无日语、X-ASR中英、fujielab私有权重都不能冒充可直接替换方案。

## 框架处置

audio.cpp可以作为某个已选候选的备选后端，但不能同时变模型、前端、量化并把收益归因给框架。其Metal优化来自M4，不外推本机。wllama 3.13.0暂不占正式席位；若目标将来变成无本地服务安装，再独立立项。

当前R2T2 GGUF后端每步清KV并重编码，论文的缓存效率不能照搬。深度encoder cache重写、hybrid MPS和模型微调不在小验证授权内。123M导出若仅成功CTC，记录为“RNNT部署未完成”，不是“模型质量失败”。

## 不放宽需求来制造胜者

所有新模型仍只能用已到达音频，不能播放预取或喂正确字幕。高资源探针只报告收益—成本，不以机器有32GiB为由自动放宽2.5GiB默认目标。人工审阅缺口阻塞对应最终质量认定，不阻塞合法的结构、源码和工具验证。

<!-- END docs/01-findings-and-decision.md -->

---

<!-- BEGIN docs/02-experiment-design.md -->

# 02 数据 输入方式与有限预算

## 数据合同

D0继续使用9PCM/88切片/62MT/34语义，按CSJ和wmf-04分层。保留15个pending-listen及全部争议时标，保守与宽松结果并列；只有保守口径可用于暂定晋级。两个seed是重复，不是样本量翻倍。

D1为新增至少20个开发语义项，用于观察是否只修旧34项；先冻结音频hash、来源、参考和允许提示信息，再看候选输出。Dholdout至少6独立来源、60语义项并含至少20条1–3秒短句，候选配置冻结前不打开。没有素材授权或可信参考时标blocked_data或needs_human；不得抓取受限音频或用AI生成的参考自称人工真值。

Dsilence保留原10秒静音；另备获准使用的纯音乐、笑声、自然BGM。Ddiagnostic含短音节、轻声起音、同音歧义、否定/数字/人际关系、持续讲话及16秒滚窗边界。诊断不替代D0分数，也不能把看到参考后编的测试当独立保留集。

## 输入方式与控制

- fixed-cuts：同88片段，仅作后端/量化或离线家族诊断。原生流式不用先通过此模式。
- causal-stream：200ms包按样本顺序到达；每步只可访问已到达PCM。可不按墙钟等待做质量筛选，但不可报告真实延迟或实时吞吐。
- fixed-text：保持同一真实ASR结果测试MT；不能换成正确参考来晋级。
- realtime-1x：单调时钟按真实音频时长发送，每次发出后记录实际服务器接纳、消费。不能先送全文件再按时间展示。
- oracle-diagnostic：完整正确日文、正确假名、完整音频等只用于机制诊断，存独立目录，不算Q/R。

原生主配置S0以真实因果VAD release驱动MT，不读取未来端点或参考句末。具有可靠区间的模型可用09章对齐桥接；无词时标的R2T2固定S0_UNTIMED_VAD_SNAPSHOT：native实际处理到release水位后的首个已完成update，只消费当时稳定累计日文的新后缀，后稳定尾字留给下一release/EOF。其音频span是因果输入包络，不是词时标；完整规则唯一见09章。该组合改变MT分组，与B0比较的是模型及提交策略整体，不纯归因ASR。基础Qwen机制控制使用相同子策略。S0不承诺更早中文。

S1是唯一可选提交消融：仅对已提交日文中的稳定句末“。！？!?”提前切一次完整句，剩余部分沿用S0；不按每个token翻译、不新加标点模型、不以定时器强切半句。没有稳定标点就回退S0。S1只在父配置两轮质量不低于B0且无新关键错后开放，计入全局唯一追加正式配置；与80ms RNNT子项二选一。当前registry把S1具体限定为R2T2父配置；其他父配置的S1须另行批准。两者都不能偷偷并入主配置。

基础Qwen1.7同量化、同后端、同160ms/U1策略仅作R2T2机制对照；与B0的产品胜负另算。如果基础权重无法用同一后端，允许继续测R2T2产品收益，但不得宣称隔离证明“仅后训练导致收益”。

## 配置冻结与种子

首次输出前冻结hypothesis、selected-experiments、model/runtime锁、代码hash、输入/参考hash、参数、Q/R路线与主指标。量化、块长、U、encoder backend、提交策略、prompt每一项变动都产生新experiment_id。对reference的任何修订同样重算所有对照，不只改候选。

复用p0.p2.b3_protocol.source_seed：范围及日文相同沿用原mt_seed_index；新范围/文本使用确定性hash派生。记录种子算法代码hash。不同实验组串行，下载、构建和其他实验不与正式测量重叠；同一E2E实验组内播放、ASR、Hy和资源采样按实际产品调度并行，不强制把ASR与MT串成一个同步步骤。文档和代码review可并行。

## 席位与预算

| 任务 | 上限 | 到期处置 |
|---|---|---|
| G0材料、控制与工具核验 | 1工程日 | 缺证据则停依赖项；不重造历史 |
| T02a最小接口/静态锁/提交器stub | 计入G0同一8小时，不另开预算 | 未完成则先报告，不无限搭通用框架 |
| R2T2源码/导入/加载/短日语一致性 | 2工程日，每日按8小时计 | 深改encoder、custom kernel、hybrid移植则blocked_runtime |
| 123M RNNT缓存运行或ONNX导出 | 2工程日 | 保留错误/graph；不把CTC临时冒充RNNT |
| 每个主配置质量筛选 | 2小时纯运行预算，评审另记 | 失败行计入计划分母，不挑最佳轮 |
| 次候选 | 最多1个正式席位；工程4小时、质量2小时 | 在Omni、FunASRMLT、假名声学路线中预选一项 |
| 追加消融 | 全局最多1配置、工程4小时 | 仅R2T2-S1与RNNT80ms争用同一席位 |
| 端到端组合 | 最多2个 | 仅Q/R质量前提通过者 |
| T02b在线整链集成 | 整批共1工程日，仅质量前提成立后 | 到期blocked_runtime；不扩大在线框架 |
| 原速与Chrome | 每候选两轮配对、最多1次有证据的环境失效重跑 | 旧失败保留；重跑理由在评分前冻结 |
| 长稳 | 每合格候选1次60分钟连续session | 不用12次5分钟拼接 |

两主线可以分别失败，不因一条失败封死另一条。正式主候选上限3，外加至多1个登记的条件消融；该槽仅限registry中的R2T2-S1或RNNT80ms，其他状态/量化/U/backend变更不能自行占槽，须另行批准。基础Qwen机制控制和正确假名oracle不消耗候选席位，但准备/能力诊断必须计入父路线工程上限，短推理计入父路线2小时运行上限；不能只记账却另开无上限预算。工程预算不含人工等待和下载排队，仍记录总墙钟及排除原因；不得自动连开数轮“各两天”。

同一候选最多两次有明确根因的依赖修复，后端数值诊断最多一组Q8或非量化短样本，不得用诊断精度成绩替换正式量化成绩。模型级正式Q/R只能按被冻结配置计分。

## 资源与运行止损

- 默认ASR加MT热态进程树physical footprint峰值目标2.5GiB；footprint缺失则资源资格inconclusive，不用RSS偷换。超出只生成资源取舍报告，用户未批准不扩展为默认或长期高资源分支。
- 目标进程树physical footprint达到12GiB，或系统critical压力持续30s，或swap-in/out持续增长30s，停止推理并保存证据。
- PCM最老等待>2s持续10s、反复无decoder进展、无限重复文字、单native计算调用超过30s，均停止本run。30s不是session总时长限制。
- 取消不意味着底层Metal调用立即结束；worker返回前保持busy，禁止并发启动第二模型；超时后晚到输出丢弃并留日志。
- 未经用户授权不更改系统权限、绕过受限权重、清理旧模型/旧run、升级生产环境或发布。

<!-- END docs/02-experiment-design.md -->

---

<!-- BEGIN docs/03-phase-tasks.md -->

# 03 AI 分阶段实施计划

**Goal：** 在不放宽M1 Pro、本地当前音频、最终中文不回写和低占用目标的情况下，用有限预算验证R2T2与123M日语原生缓存路线是否比当前基线有净收益；允许得出否定或运行阻塞结论。

**Architecture：** 既有P2证据与生产默认保持不变；独立 `p0/p3/` 适配器与实验backend，沿用200ms PCM／网关合同，原生ASR稳定提交经S0或单独登记S1进入现有Hy，统一因果和DOM证据后决定继续或停止。

**Tech stack：** 项目现有Python3.12、sherpa-onnx／MLX Hy、WebSocket/Chrome扩展；R2T2为固定llama.cpp+pybind的隔离arm64工程；日语RNNT为精确NeMo／cache ONNX ORT路径。此包辅助脚本是Python3.10+标准库，不是模型runtime要求。

**Spec：** [00合同](docs/00-contract-and-baseline.md)、[02预算](docs/02-experiment-design.md)、[09接口](docs/09-runtime-integration.md)、[08门槛](docs/08-gates-and-stop-rules.md)和config JSON。冲突时停止；不能自行挑更宽规则。

**Global constraints：** 先授权再实施；独立worktree；不改历史hash／main默认；串行模型；两主线+一个备选+全局一个条件配置；最多两个E2E；不下载私有模型、不自动push、不把待听审变人工已审。

**Review focus：** 原生缓存是否真实、模型/后端是否同一身份、R2T2第二返回值与滚窗账本、因果采样水位、中文不可回写、实际worker取消、全分母与匹配P95、P2正式R与P3内存探索的边界。

## T00 接收任务与建立隔离（不推理）

- [ ] 阅读README、00、08和EXECUTOR；确认用户当前授权是“实施本包”还是仅继续讨论。只有前者才做以下仓库变更。
- [ ] 在实际仓库运行只读 `git status --short`、`git rev-parse HEAD`、`git log -5 --oneline`；记录未提交改动，绝不reset/checkout覆盖用户文件。
- [ ] 对比77e24c3基点；若相关代码/评分变动，先输出repo-diff，不强拉旧版本到用户工作目录。新worktree固定置于/Users/luo/.codex/worktrees/<task>/，路径和分支由执行器确认不存在后创建；不在本文硬编码删除或覆盖命令。
- [ ] 新建实验run和独立环境计划，不动engine现有锁。收集硬件/OS/电源/Chrome/可用磁盘；检查许可与素材权限。
- [ ] 本包尚未在目标worktree时复制到docs/live-video-translate-p3-2026-11-10/，已在原位则核验后复用；记录保留生产资产的基点checkout及其engine/.venv绝对路径，并将BASE_ENGINE_PYTHON设为该已存在Python可执行文件。新worktree不会包含ignored的模型、profile或虚拟环境，不应先重新安装全部依赖。原始音频、模型或私有profiles继续忽略。

输出：`run-manifest.json`、`repo-diff.md`、`authorization.md`、`environment.json`。退出：权限不足为blocked_permission；dirty overlap不能规避则询问用户。此阶段不给模型质量结论。

## T01 复用证据与重建本轮对照（G0）

创建：`p0/p3/freeze.py`、`p0/p3/tests/test_freeze.py`。只读复用：`p0/p2/freeze.py`、`types.py`、`events.py`、`runner.py`、`realtime.py`、`scoring.py`。不修改历史收据。

- [ ] 先写测试：缺文件/hash、null必需revision、不同experiment参数、输入hash漂移、旧run覆盖、路径越界、假human_verified均失败；相同输入纯读恢复才成功。
- [ ] 实现P3自己的run和锁转换；旧P2锁仍单独留在 `history/`，新的选中材料锁为 `locks/materials.json`。
- [ ] G0只先做静态check。`p0.p2 ... preflight`可能推理，不能拿它作静态check。
- [ ] 核对D0的9/88/62/34和15 pending-listen、历史17/121是否有本地合法资产；缺少时blocked_data，不重新拼凑另一集却沿用分数。
- [ ] 在新候选输出前冻结D1至少20个不与D0重复的开发unit；仅使用已授权材料，保留暂定参考和不确定项，不打开test/。缺D1只阻断D1及其泛化结论，D0可继续有限研究，并在T06/T09明确该缺口。
- [ ] 已获得实施授权并完成锁后，在同轮环境复现B0固定切片/MT；ASR/MT原文逐条一致才继承对应判定。需要重评分时双seed均保留。
- [ ] 原生causal模式用本轮实际VAD生成的端点与B0；相同34语义分母。若基线模式或边界改变，使用新同轮B0，不能机械套20/18。

现有B0命令形态如下，**须在仓库根、旧B0所需资产与profile已就绪、独立新run已选定后**执行；变量必须由执行AI明确赋为安全路径，不能复制未设置的变量调用：

```bash
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" freeze --phase B0
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" probe --experiment B0 --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" probe --experiment B0 --mode fixed-text
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" score --experiment B0
```

这些旧B0命令在仍保有engine/.venv、私密profile和PCM的基点checkout运行，P3_B0_RUN使用其p0/evidence/下全新目录；P3 worktree只读引用该新控制run的绝对路径和hash。候选环境另建且不写生产环境；不因worktree缺ignored资产把清理后的缺文件写成模型失败。这里ID是B0；B1+阶段的控制才叫B0-CURRENT，不能混用。若当前checkout无法原样调用，保存准确错误并在P3实现显式包装；不得改旧receipt使其“通过”。G0总预算1工程日；模型资产下载与人工等待另记，不自动重开计时。

## T02a 最小离线接口与安全探针底座

创建：`p0/p3/{types.py,cli.py,native_runner.py,commit_bridge.py,resources.py}`、`p0/p3/adapters/base.py`及对应最小测试。此时native_runner仅做离线因果包驱动和受控stub，不做完整在线backend/server。T01+T02a共同计入G0的8工程小时；到期报告阻塞，不能先无限搭框架再发现模型无法加载。

- [ ] RED：运行新增无模型测试，确认失败来自未实现行为，不是缺真实权重。
- [ ] 实现09类型、三个音频水位、源前缀账本和S0的最小stub测试；S1代码晚于父候选质量前提，不先扩功能。
- [ ] CLI只先交付check/freeze和能力探针分派；未实现的在线子命令返回not_implemented，不先把help当功能。
- [ ] CLI严格分静态check与capability；phase/ID白名单、预算与required锁先检查，不允许导入模型产生隐式副作用。
- [ ] 复用现有资源采集，为短capability补12GiB/持续压力/30秒native调用守卫；保持单owner、真实返回前不并发启动第二实例。
- [ ] GREEN：最小接口和P2合同回归、独立review通过后，才准备T03/T04环境。完整WS/在线MT/DOM集成留给T02b，未胜出不投入。

未来实现后验证命令（目前仓库没有P3，不能立即运行）：

```bash
"$BASE_ENGINE_PYTHON" -m pytest -q p0/p3/tests
"$BASE_ENGINE_PYTHON" -m pytest -q p0/p2/tests
"$BASE_ENGINE_PYTHON" -m pytest -q engine/tests/test_gateway.py
"$BASE_ENGINE_PYTHON" -m p0.p3.cli --help
```

上述gateway测试在T02b产品合同对接后再跑；T02a运行其实际已实现的测试，不以“尚未实现”测试被跳过来宣称全接口完成。若pytest并非当前test环境已有依赖，使用该仓库既定test命令并记录，禁止为一条示例无条件升级engine。`--help`验证只是接口，不是能力成功。

## T03 R2T2 有界可行性

创建：`p0/p3/adapters/r2t2.py`、`p0/p3/tests/test_r2t2_adapter.py`；详细配方04章。

- [ ] 锁source、llama子依赖、decoder/mmproj以及基础Qwen机制控制；先看许可、已知依赖障碍和内存默认参数。
- [ ] 小型Darwin构建/导入解耦；不做hybrid迁移。2工程日内必须交代source/import/load/parity四门。
- [ ] 测试已累计last_fixed_text到一次delta的差分、Japanese语言、首320/后160与2..4步预算、整块/短尾EOF、内部reset和正式W16O8的16/8边界；S0_UNTIMED_VAD_SNAPSHOT按09章，不虚构词时标。
- [ ] 当缺参考输出时不强称数值等价；记录可验证部分和缺口。无法建立运行正确性则不转正式质量。
- [ ] 通过后固定R2T2 160/U1/S0，跑causal-stream质量，再将其真实原文送Hy两seed；基础Qwen同条件只做机制诊断。

输出：build/parity/来源证据、逐步重算与KV日志、两seed语义包。Q失败停止，或仅在符合02章条件时申请唯一S1槽；资源超限按02章停止。不能为了证明R2T2优势选择一个比B0弱的基础Qwen分数作产品对照。

## T04 123M 日语cache-aware RNNT

创建：`p0/p3/adapters/japanese_cache.py`、`p0/p3/tests/test_japanese_cache.py`；配方05章。

- [ ] M1-6、480ms、RNNT先锁唯一主配置；确认这是论文编号不是Apple型号。
- [ ] 验证NeMo参考及原始PCM因果frontend（跨包预加重、STFT支持域、feature-ready水位与同前缀不同未来反例），再保缓存运行/导出；填exact tensor contract。CPU/MPS/ONNX只能选择实际可行路径，不自动并行三栈。
- [ ] 连续至少两块证明cache读写；对照清零反事实；RNNT predictor/joint/blank/停止行为均检查。仅CTC成功不能晋级。
- [ ] 2工程日后给出blocked或可继续，停止深度框架开发。
- [ ] 通过后causal-stream + 同Hy两seed；记录低驻留探索R_memory和Q分别怎样判定，不把小文件当低内存证据。
- [ ] 只有主配置通过相应前提、并且唯一条件槽空闲时，预登记80ms；与S1二选一。

输出：tensor契约、转换命令与hash、前端/logit/token parity、缓存轨迹、真实质量与资源。CC-BY-NC的商业许可问题不能因测试好而忽略。

## T05 次候选只选一个

创建：`p0/p3/adapters/secondary.py`、`p0/p3/tests/test_secondary.py`；配方06章。

- [ ] 在任何该候选输出前将registry中secondary选择固定；默认Omni300M v2 INT8，另两项未选为not_run。
- [ ] 若正确假名→Hy语义门不通过，不加载Kizuna；其失败不是声学模型失败。若选Fun-ASR-MLT先读取既有证据、确认是否已足够，不重跑已失败配置。
- [ ] 主体只做4小时工程+2小时质量预算。来源不等价、缺日语、权重private或精度回落均停止。
- [ ] 通过同质量门才允许争取最多两个E2E之一；不会因为小模型就跳过关键语义错误。

## T06 盲评与正式实时入口

创建：`p0/p3/scoring.py`、`p0/p3/tests/test_scoring.py`及`p0/p3/decisions.py`的离线判定分支；T02b再补在线判定。复用旧单位/判定格式时显式记录版本。

- [ ] 隐藏模型名和输出顺序；冻结分母。逐项对照ASR、raw/filtered中文，判定restored/lost/new-critical/protected-loss。
- [ ] 15 pending-listen维持provisional；缺人工时间或语义不能用AI签字补齐。只允许标“暂定研究晋级”，不出默认替换结论。
- [ ] 按08章读Q、正式R_latency、探索R_memory；任何路线不得事后改主指标。所有失败项和未完成项保留。
- [ ] 达研究入口的候选最多选两个；先单列D1与同轮B0结果，缺D1则保留数据阻塞并限制为D0研究，不宣称泛化。按预登记目标与风险选择，不按最漂亮单项。R_memory此时只可标quality_entry_eligible，整链内存尚未测就不能标路线pass。若0个通过，直接T09结案。

## T02b 仅为合格候选补在线整链

这是T06之后才执行的条件任务，不是T03之前的固定成本。整批共8工程小时，包括最多两个E2E配置的薄对接；到期仍需深改通用运行栈则blocked_runtime并请求新范围。

创建 `p0/p3/backend.py`、`server.py`，完成 `native_runner.py`、`resources.py`、`decisions.py` 在线分支及09章生命周期测试；复用既有gateway而非重做服务。

- [ ] RED：在线队列、单owner、取消封sink、在途结果、真实返回后释放lease、满队列断线、重复finish和epoch隔离。
- [ ] 实现真实WS接纳/消费、在线Hy、两条独立不可回写账本、8秒drain；client送出不能伪装accepted。
- [ ] 完成资源/队龄观测和持续压力守卫；先stub反例再真实native短取消，不在未证回收安全时长跑。
- [ ] 实现09中的replay/accept分支，验证help与已实现行为相符；旧P2/gateway回归以及P3完整测试通过。
- [ ] 独立review确认可进入T07；本阶段通过不等于目标机质量或资源过线。

实际执行顺序是T00 → T01/T02a → T03/T04/所选T05 → T06 → 合格者T02b → T07/T08 → T09。分支之间可以单独停止，不能因任务编号把昂贵在线工作提前。

## T07 1×、Chrome、工程生命周期

- [ ] 同一天相近环境做两轮配对，seed11先B0→候选，seed29候选→B0；同一轮中输入、视频渲染设置、负载、缓存预热均一致并记录。实验组串行，同组ASR+Hy按真实调度联跑。
- [ ] 先WS 1×验证实际accepted/consumed与MT，再Chrome真实tabCapture/DOM；二者分开报告，不能拿WS分数代替DOM。
- [ ] 做前后时钟校准和可读终点审核；missing/late保留全分母。重复真值/参考时标敏感性分析不得只报更好版本。
- [ ] 测暂停、seek、切源、关闭WS、busy、EOF短尾、过载、后台与全屏；权限拒绝就blocked_permission，不绕过。
- [ ] 资源ASR+MT完整树同驻留；记录各阶段、队龄、native重算/缓存、长句增长，不加各组件单跑峰值。
- [ ] 如某个延迟“改善”仅来自漏译更多或filter更多，failed_quality而非性能胜者。

运行命令使用T02b真正已实现且单测确认的P3 CLI；不能执行P2 candidate replay代替。每个run含完整原始日志和业务状态，不以shell exit0判断通过。

## T08 长稳与保留集（有门才做）

- [ ] 冻结最终唯一配置及代码hash，确认完整工程门、资源目标和许可；未过则不打开holdout。
- [ ] 独立holdout至少6来源/60项，含20短句；执行后不调参再报同一holdout。
- [ ] 17段121项历史回归与独立集分别验证，不能合并凑90%。现有34项不是完整产品验收集。
- [ ] 合格候选一次连续60分钟，不用多段重启拼成长稳；同session、相同模型生命周期，记录最后/最初热态10分钟增长。
- [ ] 所有硬门及人工检查过后，独立review仅能给release_candidate建议。用户明确选择前默认不动。

## T09 结案与恢复点

- [ ] 生成每候选决策、全局台账、收益—成本、待补证据和再开条件，未赢也算完成。
- [ ] 运行本包辅助工具只验证材料/算数，再由未来decisions和独立review检查业务门；两种报告分开。
- [ ] 存工件hash/补丁/许可/版本/命令/退出/时长；去敏后才考虑提交，提交和push仍需用户相应授权。
- [ ] 默认保留原路径，回收临时服务与已结束worker。不要擅删模型缓存/旧实验；可列出清理清单等待授权。

最终必须回答：①模型部署通了吗；②日文稳定源文本改善了吗；③同Hy的中文净收益多少；④实际1×/DOM何时出现；⑤完整资源与长稳代价；⑥哪里被硬件、实现、模型、等待语义或评分证据限制；⑦是否值得下一轮，以及触发新一轮的具体新证据。不能用“更多优化可期”替代这七项。

<!-- END docs/03-phase-tasks.md -->

---

<!-- BEGIN docs/04-r2t2-mac-spike.md -->

# 04 R2T2：有上限的 Mac source/import/load/parity spike

## 决策与边界

本章是规划，不是 Mac 编译／实测成功记录。正式主配置预先固定 **Confucius4-R2T2，full `stream_llama`，Q4_K_M decoder + Q8 mmproj，160 ms，U=1，S0，`streaming_transcribe_no_reset` 的 W16O8（16 s 阈值／丢最早 8 s）**，不由 VAD 强制 reset。基础 Qwen3-ASR-1.7B 仅作同后端、同量化、同块长、同 U、同状态策略的机制控制，不是第三条主线。论文日语 FLEURS 的 36.07%→8.66% CER 只能支持后训练值得验证，不能算本项目基线收益。

冻结产品输入为 16 kHz、单声道 s16le、200 ms 到达包；adapter 只能从已到达样本重分块，不改变采集链路。160 ms 是模型喂入块长，不是计算耗时或中文端到端延迟。本轮驱动显式锁首轮320 ms、之后160 ms；它来自example的调度，不是U1自动设置，U1是独立的隐藏尾token策略；200 ms 到达包还会造成实际可见时刻量化，必须按真实样本时钟记录，禁止预取补足。

本章不修改 main，不替换默认 Reazon+Hy；不使用云、未来音频、OCR 或原字幕。不同实验组及其下载／构建不重叠；同组播放、ASR、Hy 与采样按生产调度并行，不把串行要求错误施加于真实联跑。预算为至多三个主候选配置，加另一个预登记条件消融，合计最多四个 `experiment_id`；S1 与 RNNT 80 ms 竞争唯一条件槽，E2E 最多两个候选。基础 Qwen 控制不计候选但计成本，不自动获得 E2E。量化／chunk／U／状态策略衍生均算配置。ASR+MT 约 2.5 GiB 是低驻留目标，权重文件大小不能代替进程树驻留测量。

## 驱动身份与提交语义

registry同时锁language=Japanese、空context、lookahead160ms、unfixed_chunk_num=0、首320/后160ms、U1及rollback_punctuation=false。官方llama入口默认Chinese，必须显式覆盖。每步max_new_tokens首轮4；有新增定稿则下步2，无新增则加1且封顶4；finish_no_reset显式传max_new_tokens=4（沿用example的first_max_new_tokens），不得用None触发wrapper的4096默认。step_decode_budget_exhausted表示保留state等待下一音频块，不是session失败；会话总量上限、30s超时和持续无进展另外记录。上游driver调用standard streaming，本轮将该调度与no_reset/W16O8组合，须本机gate验证，不能叫官方已测整套recipe。

no_reset第二返回值已是全session累计last_fixed_text，外层只作前缀差分，滚窗裁的是内部chunk_text；再次把第二值按窗口拼接会重复文本。无词时标路径固定09章S0_UNTIMED_VAD_SNAPSHOT，不直接调用旧StreamCommitter。

EOF保持固定pin行为：finish_no_reset的buffer为空时只返回已定稿前缀，U1隐藏尾不自动解锁；有短尾时按pin再次计算。整块/短尾、空尾、重复finish均先stub。未定稿尾标withheld_tail，语义按missing/partial保留全分母，不将provisional晋升final，也不在看分后补一次推理解锁。将来改EOF另行预登记。

## 源码与工件锁

| 对象 | 固定值／动作 |
| --- | --- |
| R2T2 源码 | `72b81be7070095410b75ed2e11998e347f655218`，默认分支为 master，不用浮动 master 计分 |
| llama.cpp | `ad6c66839af3c5646fba8c6c2e2087a1e4e38948`；包装器引用非公开 mtmd helper，禁止自行升级 |
| R2T2 权重 | `netease-youdao/Confucius4-R2T2-GGUF` 的 Q4_K_M 与 Q8 mmproj；执行者先记录实际文件名、HF revision、SHA256、字节数 |
| 基础控制 | `Qwen3-ASR-1.7B` 的同后端同量化工件；记录其转换来源、命令及哈希，不把未量化 PyTorch 作为公平控制 |
| 许可 | 代码 Apache-2.0；权重 NetEase 自定义模型许可，先保存许可原文与适用结论 |

研究尚未固定 HF 权重 revision；未知 revision／哈希填 `null` 并标 `blocked_data`，不得填猜测值或 latest。正式运行前补齐统一 manifest，每次加载前核对；未锁定不得计分。

目标实现位置为 `p0/p3/adapters/r2t2.py`，统一接口、命令和输出 schema 服从 [09 章](docs/09-runtime-integration.md)，不另造平行 signature。实验目录额外保留 `source-audit.md`、`build.log`、`otool.txt`、`parity.json`、`r2t2-state.jsonl`、工件锁及本地最小补丁；这些是实施阶段产物，本包不伪装已经存在。

## Task checklist：最多两个工程日，再做明确决策

两个工程日限定纯原生 source/import/load/parity 工程投入，不含排队／下载；同时记录总墙钟，不能靠排除等待掩盖总成本。

- [ ] **source gate**：逐项定位 `native_ext.cpp::generate_once`、`llama_native_backend.py`、`r2t2_asr.py` 和 CMake。确认完整音频编码→投影→解码链，不把 decoder-only demo 算加载成功。记录 Python、arm64、macOS、Xcode/clang、CMake 与 pybind11 版本。
- [ ] **Darwin build/import gate**：在隔离实验环境重建 arm64 pybind 扩展、llama/ggml/mtmd。当前 pin 有 Metal 源码；可评估 `GGML_CUDA=OFF`、`GGML_METAL=ON`，但这不是已验证的一键命令。检查 Mach-O 架构、实际链接库和 `otool -L`；构建目录绝对 BUILD_RPATH 可先用于本地 smoke，分发再处理 `@loader_path/@rpath`、dylib/metallib。Python 扩展本身的 `.so` 后缀并不等于 Linux ELF。
- [ ] **vLLM leak gate**：`pyproject.toml` 强依赖 `qwen-asr[vllm]`，且 streaming/finish 在 `max_new_tokens != None` 时导入 `vllm.SamplingParams`。只允许小型 DTO／backend 参数解耦并留补丁与测试；正式step与finish均显式传上述有界整数，DTO解耦后验证停止条件，不使用None分支规避导入。不得在 Mac 通过安装整套 CUDA/vLLM 偷渡依赖。
- [ ] **load/parity gate**：G0前冻结reference backend、同量化权重/frontend、输入、语言、驱动/状态策略、比较层级与容差，再做静音/短日语/低声起始/数字/否定/专名探针。同工件跨平台回归、adapter对直接native调用的接线一致性、非量化诊断、最终中文质量四类分开。只在同口径数值/前缀约定实际违背时判blocked_runtime；Q4+Q8对vLLM/hybrid/FP16不逐token相等不能单独归为Mac部署错误。缺可比reference为blocked_data/parity未证，可保留build/load诊断但不进正式质量。所有诊断留在原16工程小时与父路线运行预算内，不展开新量化矩阵。
- [ ] **U1 contract gate**：逐次记录 upstream 第一与第二返回值、隐藏尾 token 数、实际输入 SHA256／样本区间、state/epoch。核对第二返回值仍符合该pin的全session累计前缀，再差分为一次delta；禁止直接上屏 `state.text`。EOF、空返回、异常、取消、重置不得重复发旧 token。
- [ ] 在两个工程日到期时填写继续／停止决定。只有 source/import/load/parity 与 W16O8 session 累计 prefix 映射均成立才申请后续短流；若 full llama no_reset 不支持或两天内无法保有映射，标 `blocked_runtime`。不自动切每 VAD reset、无限累积或扩大矩阵。

## 两种流状态不能混写

当前 GGUF `generate_once` 先 `llama_memory_clear`，再重编码传入音频；embedding 路径也清 decoder KV。这是**当前实现**，不是 LSP 算法必然要求。论文缓存设想不能当已实现性能。

标准 `streaming_transcribe` 累积当前 state 的音频，官方 example 对单个 WAV 初始化一次 state；它不是“必然累计整个人生会话”。该标准 API 仅用于 **G0 短样一致性诊断**，不是正式流式配置的后备策略。正式使用 `streaming_transcribe_no_reset`：16 s 阈值移除最早 8 s，并调整 chunk_text；其首次文本裁切硬编码依赖首5120样本（320ms）、后续2560（160ms）的输入调度，不能用全程160ms替代。首轮就须检查 16 s 前后和连续多次裁切的丢词、重复及时间归属。

按 [09 章](docs/09-runtime-integration.md)，外部 session 的累计 committed prefix／source_id 水位不得随内部滚窗清零；维护窗口偏移与已提交 token 映射，VAD 仅用于 S0 提交策略，不强制重置声学状态。EOF／取消／seek 是显式生命周期事件，不能伪装成每段 VAD 重置。若需变更状态策略，属于未获预算的新配置，必须另行审批；唯一条件槽仅限registry的S1或RNNT80ms，不可自行挪用。不得悄悄退回 G0、无限累计或按 VAD reset。

`stream_llama_hybrid` 不是“full llama 换 device=mps”：其 encoder 依赖 qwen_asr 的 vLLM-native 类、distributed/config、Gloo 初始化和 CUDA/BF16 默认值，且 decode 侧仍加载 mmproj。首轮不做 hybrid。若 full llama 的安静起始遗漏可复现，保留证据，不自动移植 encoder。

## 短流、失败用例与验收

通过上面 gate 后，只做预注册短流与基础控制成对运行；先 ASR 质量，再现有 Hy，最后决定 E2E 席位。记录 `visible/consumed` 样本水位、输入块哈希、首轮额外 lookahead、compute、队列最老年龄、日文 commit、MT 等待和中文 display 时刻。所有累计编码量、n_ctx/n_batch/n_ubatch、线程、FlashAttention、KV/图/scratch 分配进入日志；默认 32768 context、8192 batch、32 线程不能拿来推断低内存。

必须包含：200→160 ms 重分块余数、静音不乱发、低音量句首、长否定后置、U1 token 反转、EOF 不足一块、取消重建、超 token 上限，以及必测 16/8 s 滚窗和跨窗累计 prefix。日文 append-only 只是源侧验收；Hy 仅消费提交原文，中文提交门独立保证已显示条目不回写。对外 revision 次数必须为 0；延迟评分含漏词／漏译，不只算匹配成功 token。

质量、中文可用性和长流阈值使用统一评分章，不在本章另降门槛。实时回放必须无持续积压；资源按 ASR+MT 实测。超低驻留目标单列 `failed_resource`，不能以机器有 32 GiB 判通过。若必须深改 encoder 图、移植私有 kernel、实现新缓存算法或 hybrid MPS，立即停在 `blocked_runtime` 并另行审批；不吃掉其余候选预算。

结果严格区分：未跑为 `run.status=not_run`；缺人审为 `needs_human`；工件／来源锁或参考工件缺失为 `blocked_data`；编译、导入、算子、加载、实际部署／转换 parity 或 W16O8 映射阻塞为 `blocked_runtime`；有效运行且可靠参考下质量门失败才是 `failed_quality`。`unscored` 仅可作 `score_status`，不作全局 `run.status`。资源／延迟失败另附维度，不能改写成模型语言能力失败。

## 原始来源与核查锚点

- [R2T2 固定提交](https://github.com/netease-youdao/Confucius4-R2T2/commit/72b81be7070095410b75ed2e11998e347f655218)；同提交下 `r2t2/r2t2_asr.py` 的 `streaming_transcribe`、`finish_streaming_transcribe`、`streaming_transcribe_no_reset`。
- [native_ext.cpp](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/native_ext.cpp)：`generate_once`、`prefill_only_with_embedding`、`PYBIND11_MODULE`；[构建说明](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/README.md)。
- [固定 llama Metal](https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/ggml/src/ggml-metal/CMakeLists.txt)、[mtmd helper](https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/tools/mtmd/mtmd-helper-common.h)。
- [模型卡](https://huggingface.co/netease-youdao/Confucius4-R2T2)、[GGUF 工件](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF)、[论文 v1](https://arxiv.org/html/2611.06043v1)。[原始只读复核](research/r2t2-mac-feasibility.md)与[扩展结论](research/expanded-review.md)未含本机编译／实测。

<!-- END docs/04-r2t2-mac-spike.md -->

---

<!-- BEGIN docs/05-japanese-cache-aware.md -->

# 05 日语 123M cache-aware RNNT：先证明缓存与导出一致性

## 固定主配置，不把名称当平台支持

第二主线固定 `streaming-ja-asr-anon/parakeet-streaming-ja-fully-open` 的 **`M1-6.nemo`、RNNT、480 ms 右侧声学上下文**。约 123M 的 FastConformer 混合 RNNT/CTC 与此前测试的 0.6B 日语 Parakeet TDT 不是同一模型。M1 是论文模型编号，不是 Apple M1 优化、认证或可运行承诺。

480 ms 是算法所需右侧上下文，不是首字延迟、更不是中文字幕端到端延迟；在仅使用已到达音频的规则下，这些样本必须等真实播放到达。论文五域平均 RNNT CER 为 480 ms 12.65%、80 ms 13.43%；相应 CTC 为 14.37%、15.58%。这是作者数据与协议，不可与本项目 Reazon k2/Zipformer 直接排行，也不能拿 RTX6000 Ada 吞吐当单流 M1 Pro 速度。

80 ms 只允许在 480 ms 的缓存、质量与运行时门均通过且存在实际延迟改善空间时，作为消融与 S1 竞争全局唯一条件槽；须在看到 80 ms 输出前预登记，保持 checkpoint、RNNT decoder、MT 和提交门不变。预算至多三个主候选配置加一个条件消融，合计最多四个 `experiment_id`，E2E 最多两个候选。不得展开 1040/480/80/0 ms、RNNT/CTC、多后端矩阵；M1-4 不在首轮。不同实验组及其下载／构建不重叠；同组播放、ASR、Hy 与采样按生产调度并行。

输入仍为原产品 16 kHz s16le 单声道、200 ms 到达包；内部 framing、首块、右侧上下文和 EOF padding 都要保持样本映射。禁止未来预取、云、原字幕、改 main。目标是 ASR+现有 Hy 低驻留约 2.5 GiB；495 MB `.nemo` 文件不是运行内存。

## 先锁来源、许可和参考运行时

- 模型：固定 HF repo、实际 resolved revision、`M1-6.nemo` SHA256、字节数，以及包内 config/tokenizer／词表哈希。研究只确认文件身份，未给精确 revision；未知锁填 `null`，`run.status=blocked_data`，执行前补齐，不能把 main/latest 当锁。
- 作者代码：`yoinncyan/my-interspeech-2026`，记录实际 commit；重点读 `scripts/infer_streaming.py`，锁定与该 checkpoint 匹配的 NeMo/PyTorch/frontend 配置。论文工作流为 NeMo 2.5.3，不因此承诺此版本在本机可用。
- 权重 **CC-BY-NC 4.0**。保存 attribution 和许可原文；商业部署许可未解决时仅限许可允许的研究评估，不能升默认生产模型。遇到许可不确定先停止相关使用，不绕过。

目标文件为 `p0/p3/adapters/japanese_cache.py`；外部 adapter、异常与时间事件服从 [09 章统一契约](docs/09-runtime-integration.md)，本章只规定模型内部检查。预期实施证据为 `japanese-cache-contract.json`、`frontend-parity.json`、`export.log`、`cache-trace.jsonl`、`rnnt-parity.json` 和锁文件。未生成的产物不得填成功占位值。

## 原始PCM前端的单独因果门

作者入口先append_audio_file整段，再迭代缓存特征；NeMo v2.5.3 buffer也是先preprocess再切feature，不能直接作200ms原始PCM在线runner。已核对的作者代码pin为cc1cdf12a3133b22349c520e5b81f0334a01e5ad；公开M1-6 hparams为normalize=NA、25ms window/10ms hop/FFT512、8倍causal下采样，尚未核验下载后.nemo内配置，不泛称存在全段归一化泄漏。

实际前端必须保留跨包PCM和上一预加重样本。center=True STFT的帧要等其完整支持域已真实到达才放行；缓存必要半窗并记录feature_ready_sample，不把每个200ms包单独center/pad，也不先对整文件提特征。dither等随机项按实际config锁定；EOF补零单列且不增加真实accepted/consumed。

G0增加反事实：相同已到达PCM前缀、不同未来尾部时，已释放feature及已提交token前缀不变；用同一已到达前缀的一次性参考验证数值/长度，仅比较完整支持域已到达的已释放帧；centered STFT在前缀末尾补零形成的未成熟帧不参与此parity，真实EOF才纳入尾帧。分别检查包边界、起始、短尾及16k到内部帧映射。只能生成特征缓存模拟时标blocked_runtime，不进入raw-PCM因果质量或1×计时。分别保留raw已接纳、feature-ready和encoder实际消费的位置；已预处理或已入buffer不等于encoder已计算/已有稳定token，不以其推进提交屏障。这些工作计入原2工程日，不新造通用前端框架。

## Task checklist：首个保缓存导出／运行 gate 最多两个工程日

两个工程日为工程投入上限，不含排队／下载；另记总墙钟，不能省略等待成本。

- [ ] **检查原包**：读取 checkpoint config，确认 RNNT+CTC 混合结构、tokenizer、采样率、特征参数、允许 attention context（研究记录为 `[70,13]`、`[70,6]`、`[70,1]`、`[70,0]`）。由实际下采样与步长推导 480 ms 对应关系，不能只见“6”就复制别的模型张量。
- [ ] **参考运行**：优先最小 NeMo CPU／已支持本机路径 smoke，不下载另一个大模型来代替它。保存 480 ms RNNT 每块输入、encoder 输出、cache 与最终 token。若 NeMo 无法运行，标 `blocked_runtime`；参考 fixture 缺失为 `blocked_data`；评分均可为 `score_status=unscored`，不编造 GPU 真值。
- [ ] **前端 parity**：精确固定 dither、preemphasis、STFT window/hop、center/pad、log-mel、normalize、帧长、下采样、末尾补零与有效长度。比较同 PCM 的逐帧数值、首尾帧和长度；预先冻结 dtype 相关误差阈值。不得用“都是 80 mel”替代检查，也不得静默多一次 resample。
- [ ] **cache contract**：从实际 NeMo export schema 和运行输出导出每个 input/output 的 exact name、dtype、shape、动态轴、长度单位、初始值、更新规则及所有权。记录 attention cache、conv cache、cache length（如该版本暴露 `cache_last_channel`、`cache_last_time`、`cache_last_channel_len`，以真实图为准），以及 RNNT predictor hidden/cell、最后 token、blank ID、decoder 长度与 joint 输入。禁止把示例名字当已核实的 tensor contract。
- [ ] **导出路径**：若 NeMo 本机路径不可持续，尝试该精确权重的 cache-enabled ONNX，显式核实 `cache_support=True` 是否真正进入导出图。分别检查 encoder 与 RNNT decoder/joint（实际拆图由 exporter 决定）；ONNX checker、输入输出枚举及一次 ORT CPU 执行都保留日志。只导出 CTC 不能宣告 RNNT 完成；没有 cache 输入输出的 ONNX 不能标 cache-aware streaming。
- [ ] **逐块 parity**：固定同一已到达 PCM、相同有效长度、初始零态和右侧上下文，与原 NeMo 对比 feature→encoder→joint→token。连续两块至少证明 cache 被读写；故意清 cache 的反事实应能检测到状态差异。用 tensor 哈希、数值范围及长度证明实际复用，不靠接口叫 streaming。
- [ ] 两个工程日内写 gate 结论。若需要重写 FastConformer cache、深改 encoder 图、私有 kernel 或先实现全新转换框架，停止 `blocked_runtime`；不自动把“做不通导出”扩大成后端研究项目。

## 状态边界与不可回写验收

适配器维持独立 stream/epoch；每次 200 ms 包到达后只允许消费当前 visible 水位内的样本。按模型原始 chunk/lookahead 几何组帧，不重复送入重叠样本作新音频；重叠上下文与新增区间分开计数。记录有效帧数、padding、每层 cache 长度、实际 consumed 水位、RNNT predictor 状态和输出 token。任何 state reset 必须对应显式 epoch／冻结端点规则，不能悄悄用每段清缓存规避增长。

RNNT greedy 的实际停止条件、单帧最大 symbols 和 token cap 必须有限并可观测。空白、重复 token、超时、异常不得转成“识别文本”送 Hy；cache 不得跨视频、取消或 seek 复用。只暴露已提交源文字，内部暂态不计可用输出；现有 Hy 的中文不可回写门独立验证，不能由 RNNT 前缀稳定性推导中文稳定性。

必测失败用例：全静音、低音量句首、200 ms 分包跨 frontend 帧边界、首次等待右上下文、短于完整首块、最后不足一块、日语后置否定、同音专名、BGM、RNNT 连续 blank、token cap、seek/取消/重建以及两段之间静音。EOF 只可按官方协议补零，不能把补零长度算成真实音频；与真实语音重叠的末尾词不能被重复发射。

## 进入质量与 E2E 的门

首先要求 exact 张量契约齐全、前端数值与长度一致、连续状态结果与 NeMo 参考对齐、无状态污染。通过后跑冻结短音频和中文语义质量门；失败保留逐项原文／译文，不以论文平均 CER 兜底。若只完成 CTC smoke，登记为独立未获预算的诊断证据，不能替代主 RNNT 分数，更不能将 CTC 失败宣布为 RNNT 失败。

质量通过才决定 E2E 席位并做 1× 因果回放；记录 lookahead 等待、ASR compute、排队、源提交、MT、最终 display 的真实时刻。单跑／联跑分开，warm-up、常驻、峰值与长期斜率分开；无持续积压、无音频丢弃、对外 revision=0，并符合统一评分章才算通过。80 ms 子项若只快草稿却增加关键漏译，保留 480 ms，不继续扫上下文。

状态解释：未执行为 `run.status=not_run`，缺人审为 `needs_human`；工件锁／参考工件缺失为 `blocked_data`，未知锁填 null；依赖／算子／导出／cache 或实际部署／转换 parity 不成立为 `blocked_runtime`；运行正确且参考可靠但质量门失败才是 `failed_quality`。`unscored` 仅可用于 `score_status`，不得作全局 run.status。资源或延迟失败另标维度；运行阻塞不证明模型质量差，权重小也不证明低占用。

## 一手来源与证据限度

- [精确 checkpoint repo](https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open) 与 [工件树](https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open/tree/main)：执行前必须转成不可变 revision+文件 SHA256。
- [原始 Interspeech 2026 论文](https://www.isca-archive.org/interspeech_2026/yang26r_interspeech.pdf)：五域 CER／右侧上下文及 RTX6000 Ada 吞吐，非本机验收。
- [作者代码pin](https://github.com/yoinncyan/my-interspeech-2026/tree/cc1cdf12a3133b22349c520e5b81f0334a01e5ad)、[streaming入口](https://github.com/yoinncyan/my-interspeech-2026/blob/cc1cdf12a3133b22349c520e5b81f0334a01e5ad/scripts/infer_streaming.py#L386)；[NeMo2.5.3 buffer](https://github.com/NVIDIA-NeMo/NeMo/blob/v2.5.3/nemo/collections/asr/parts/utils/streaming_utils.py#L1671)、[STFT](https://github.com/NVIDIA-NeMo/NeMo/blob/v2.5.3/nemo/collections/asr/parts/preprocessing/features.py#L386)。源码pin不是权重revision，后者仍须解析。
- [NeMo ASR 官方文档](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/models.html)：研究仅获得 cache ONNX 导出线索，未核实该权重完整 graph，实施时须核对与锁定版本对应源码。[只读日语汇总](research/japanese.md)与[扩展结论](research/expanded-review.md)没有现成 M1 cache-stream runtime 的已验证结论。

<!-- END docs/05-japanese-cache-aware.md -->

---

<!-- BEGIN docs/06-secondary-candidates.md -->

# 06 次候选：一个正式首筛席位，先语义后工程

## 预算与选择规则

R2T2 与日语 123M RNNT 是两条主线。本章不是全跑清单：**最多一个备选正式质量首筛，即至多三个主候选配置；另一个预登记条件消融，合计最多四个 `experiment_id`，E2E 最多两个候选**。S1 与 RNNT 80 ms 竞争唯一条件槽。基础 Qwen 控制不计候选但计成本；量化／chunk／U／状态策略衍生均算配置。不同实验组及其下载／构建不重叠，同组播放、ASR、Hy 与采样按生产调度并行。默认备选为 **Omnilingual CTC 300M v2 ONNX INT8**。正确假名→Hy 为不加载新声学模型的语义 gate；只有 gate 通过且唯一备选席位未使用或获明确重分配，才可首筛 Kizuna。不得失败后自动逐个补测。

既有 Fun-ASR-MLT 可保留廉价质量筛选，但不能据此另开正式席位或重型工程线。若 P2 已有可用、冻结的首筛结果，直接引用而不重复跑；若需要新模型加载／新增正式评分，必须计入唯一备选席位并先登记替代谁。Audio8、Granite、OWSM 仅候补，不为“覆盖更多模型”安装框架。

冻结原产品 16 kHz s16le 单声道、200 ms 到达包，不改 main，不用未来音频、云、原字幕或 OCR。离线／chunked 识别只可访问当前已到达范围，不把 whole-file 质量探针算实时胜出。低驻留目标仍为 ASR+现有 Hy 约 2.5 GiB，模型体积、英语 RTF 或其他 Apple 芯片结果均不替代测量。目标文件 `p0/p3/adapters/secondary.py`，接口与错误遵守 [09 章](docs/09-runtime-integration.md)，不定义平行协议。

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

证据底稿：[扩展结论](research/expanded-review.md)、[日语研究](research/japanese.md)、[原生后端](research/native.md)、[流式研究](research/streaming.md)、[R2T2 Mac 复核](research/r2t2-mac-feasibility.md)。除明确固定的源码／snapshot 外，URL 均为来源入口，不是内容锁；未锁定不得正式计分。

<!-- END docs/06-secondary-candidates.md -->

---

<!-- BEGIN docs/07-observability-and-scoring.md -->

# 07 计时 资源与语义评分

## 复用并补足事件

复用p0/p2/events.py的EventLog及validate_causality，但它只检部分水位和顺序。现有source_commit范围校验只到accepted，P3还要检查consumed、不可变前缀、重复翻译、epoch晚到事件和scope切换。客户端记录的“pcm_accepted”实际可能只是sent，必须与服务端trace分开，不能当服务器已经接纳。

必要事件：pcm_sent、pcm_accepted、pcm_consumed、vad_release、asr_start/end、ja_commit、mt_enqueue/start/first_token/end、caption_filtered/emit、ws_receive、dom_visible/hidden、cancel/worker_return/finished、resource_sample。每项带run_id、session_id、epoch、clip_id、source_id、host monotonic_ns、真实样本水位和具体来源。JA临时输出另留provisional但不得进入MT。

R2T2另记每步累积音频秒数、encoder实际重算秒数、prompt token数、KV清空次数、单步计算和最老等待；RNNT另记cache各tensor形状/字节、有效长度、chunk/shift/right context、predictor状态大小和解码步数。只记“streaming=true”不够。

包内prefix-audit读取规范化的累计已提交文本快照，按(scope_id,channel)检验只追加、sequence严格递增和source_end_sample不回退。scope固定为session/epoch，utterance/window只作为内部诊断ID，不能为了掩盖回写而换scope。channel=zh检的是已发布字幕串的累计账本，不是正在显示的单条字幕；正常换行显示不等于回写，旧条目内容改动才是违约。工具不证明scope归属、真实时钟或语义，完整生命周期另由集成测试验证。

## 时间与可读终点

所有媒体样本以16kHz计；真实时间采用后端单调时钟，Chrome performance.now需每轮前后至少10次往返校准，保存最低RTT估计和误差区间。seek/新epoch重建映射。不得用Date.now相减、按钮时间替代语音起点或直接把sample/16000当已消费墙钟。

每个语义项的第一次稳定正确可读中文时刻，以实际DOM可见且内容足以表达语义为准。跨字幕拼合取最后必要部分出现时间。每个表达必要语义的字幕片段必须拥有实际连续可见区间，时长至少max(600ms, sum(ch.isalnum() for ch in text_zh)/12*1000ms)，与现有p0/p2/scoring.py口径一致。瞬闪、遮挡、缺hidden/removed结束证据或未知可见性不能计可读成功。跨字幕逐必要片段审核，均达门后取最后必要部分合格区间的首次可见时刻；回溯该时刻，不额外加停留等待时长。WS和可读DOM覆盖分列。节点存在、首个汉字、MT首token、WS收到都不能代替。MLX分阶段测试显式同步，正式链路不插入改变调度的大量同步。

定义L=stable_correct_ms-source_end_ms，允许负值。分别报告：

- 语音起点和按钮启动到首条任意/正确/稳定正确中文。
- 全分母2/3/5/15秒句末覆盖，以及回放结束覆盖。
- 15秒窗内匹配项P50/P95及N；共同正确项配对P95及N。
- 全体P95，missing或超过15秒视为无穷；JSON写null和unbounded=true。
- 1–3秒短句从起点到可用中文P90，未命中按无穷。
- 漏错/过滤/迟到/渲染丢失的unit_id和原因。

固定nearest-rank，不事后切换插值法。正确匹配或共同正确不足20项，尾分位只能描述，不能用于正式R延迟胜负；D0 seed29基线18项使共同正确20项本来就不可达。欲测R延迟，必须在候选首次输出前冻结更大计时开发集和资格，不能跑完后换分母或主指标。

包内timing命令仅算数，不证明stable_correct标注、时间校准或DOM来源。必须同时保存未裁剪raw结果和15秒视图。

## 资源口径

每250ms采样，冷加载、暖机、正式输入和drain分开。每配置包含ASR、MT和它们的子进程；组件峰值不可相加伪造总峰值。记录播放only对照、Chrome/OS/功耗模式和后台干扰。

CPU使用每核100%口径，并报告CPU秒/真实音频秒；RSS与physical footprint同时保留，树RSS可重复计算共享页，比较固定同一种口径。默认2.5GiB资格固定为完整树热态`tree_physical_footprint_peak_bytes`，footprint不可得时为inconclusive，不换RSS凑通过。P3尚未执行的R_memory在本次修订预登记为tree_physical_footprint_peak_bytes，RSS仅辅助；不修改P2正式R的paired_common_correct_dom_p95。逐个采样时刻汇总完整目标进程集合再取峰值，不相加各组件独立峰值，也不称系统唯一物理页精确去重。

热态统一为warmup完成后、第一包正式输入前开始，至正常drain和在途native/MT完成；冷加载/暖机另报且仍受12GiB硬停。WS集合为本run server/native/Hy及可归属子进程；Chrome集合另包含本run隔离浏览器进程，播放only单列。基线/候选只在相同surface、阶段和采样定义配对，不能跨WS与Chrome或拿峰值相减称模型独占。缺footprint、必需进程样本或归属不明时inconclusive，不能用RSS补过。

GPU为整机活跃率，包含视频解码/渲染；ANE采不到则null而非0%；模型文件大小不等于内存。能量需现有权限及有效测量，不能请求不到后改成CPU%作为同一指标。队列同时记PCM真实秒数/最老年龄和MT条数/最老年龄。GPU竞争只能在ASR单跑、MT单跑、联跑的有界配对证据后讨论，不能预设命令队列阻塞。

## 语义与评审

每个冻结unit用correct/partial/incorrect/missing/uncertain，仅correct计覆盖；保留原文、raw中文、过滤后中文和实际DOM。否定、数字、专名、人物关系等关键错误不被流畅中文抵消。

新增关键错按错误内容判定，即使B0原来missing，候选新增反义也算新增关键错。保护项在输出前按同轮B0和已冻结语义规范确定，不事后删。每轮给出restored/lost、新关键错与保护项列表。

独立评审隐藏模型与顺序，核查全部收益、损失、关键错及抽样正确项；AI可辅助对齐但不可代称人工复听。15项争议保持保守/宽松视图，保守过线也只叫provisional。不能由CER单独晋级，也不能由ASR汉字错直接推定中文必错。

D0/D1只是开发证据。独立保留集一旦用于调参便转为回归集；需要另取未看过材料才能再次称holdout。

<!-- END docs/07-observability-and-scoring.md -->

---

<!-- BEGIN docs/08-gates-and-stop-rules.md -->

# 08 晋级 状态与停止规则

## 各状态的含义

planned → locked → capability_pass → quality_scored → realtime_scored → engineering_pass → holdout_pass → release_candidate。

异常终态包括blocked_data、blocked_runtime、blocked_permission、failed_task、failed_quality、failed_resource、inconclusive、needs_human、not_run。能力测试失败不是0/34，未完成的正式质量run保留计划34项但不能作为完整可比成绩。source/import/load通过只表示运行能力，不等于质量合格。release_candidate不是自动发布。

所有报告分别给出已完成步骤、未到达步骤和阻塞原因。退出码0只是命令完成，必须读取业务status；本包三个辅助命令始终release_eligible=false。

## 默认预登记路线

R2T2主配置默认Q。123M RNNT480默认Q和P3新增探索路线R_memory同时登记，后者主指标为同surface完整链路热态tree_physical_footprint_peak_bytes（定义见07章）。第三席位默认Q；若选Omni可在首个输出前同时登记同一R_memory。不得见质量失败后追加路线。S1消融默认Q；如计划R_latency，必须先拥有合格冻结的较大计时开发集。

**政策边界：** P2正式R的主指标仍是`paired_common_correct_dom_p95`，本包不追溯更改。R_memory只支持本轮低占用假设筛选和研究性E2E晋级，不代表“通过原正式R”，也不独立获得默认替换资格。其结果用`research_R_memory_pass`单列；进入release_candidate仍须通过原正式Q或R_latency及完整验收，或者由用户明确批准新的替换政策。不能把收到文档包当作批准放宽旧合同。

| 路线 | 质量前提 | 1倍速及显示晋级 |
|---|---|---|
| Q | 同轮两次D0各净增至少3/34；新增关键错0，保护项无损失 | 两次5秒覆盖各净增至少4/34；15秒内正确匹配句末P95<=3s；静音0，无持续积压 |
| R_memory | 同轮两次正确数均不低于B0；新关键错0，保护项无损失 | 两次5秒覆盖不低于B0；登记内存主指标各改善至少20%；其他指标满足下列容差 |
| R_latency 条件 | 同R，且输出前取得共同正确至少20项的可行计时集 | 同一批共同正确单元、两轮校准后DOM P95各改善至少20%，各自漏错仍按全分母公布 |

当离线B0仍20/18时，Q对应23/21；不得把旧20/20或1x19/17混入。连续causal-stream重新生成边界时同轮B0必须保留，20/18只是历史参照。相同seed结果不独立，23/23是可重复，不是68样本显著性。

R其他容差：CPU秒/音频秒不恶化超过10%；整机GPU均值不增加超过3个百分点；句末P95不恶化超过max(100ms,10%)。R_memory的P95仅作探索guard，可在N不足20时连同N和不确定性描述，用全分母5秒覆盖非劣共同约束；这不能变成正式R_latency胜负。若时钟/人工句末不足以计算guard，则inconclusive，不把缺值当0或pass。正式R_latency仍要求冻结的共同正确N>=20。20%是工程筛选阈值，不是统计显著性结论。

T06离线阶段仅判断Q质量门或R_memory质量前提，后者写quality_entry_eligible而非research_R_memory_pass；完整配对资源和显示须到T07才可判。

S0只有质量不低于B0但未达Q/R时，可以用唯一追加席位试S1；必须先无新关键错/保护损失且资源可承受。父配置失败不会自动开放所有子项。所有候选都不通过时，结束本轮并保留默认，不临时降线。

## 默认替换验收继承

| 验收 | 要求 |
|---|---|
| 历史17段121语义项 | 5秒内稳定正确DOM至少109/121 |
| 独立保留集 | >=6独立来源、>=60语义项，5秒正确覆盖>=90% |
| 句末延迟 | 1x正确匹配P50<=1.5s、P95<=3s，同时公开全部分母与missing |
| 1–3s短句 | 至少20条，起点到正确中文P90<=4.5s，missing为无穷 |
| 语义与静音 | 新关键错0、保护项无损失、原10s静音0字幕 |
| 可读字幕 | 继承07章连续停留公式、结束/遮挡证据与多片段规则；缺测不计正确DOM，WS另列 |
| 生命周期 | 暂停、seek、切源、取消、EOF、busy、断线、后台与全屏真实验证 |
| 长稳 | 同一实际session60分钟，无串线/积压/原声中断 |
| 内存稳定 | 最后热态10分钟中位footprint相对首个热态10分钟增加不超过max(10%,128MiB) |
| 默认低占用 | 完整树热态physical footprint峰值<=2.5GiB目标；缺footprint为inconclusive；超预算只能单列取舍，不能默认替换 |

历史Chrome全屏TypeError: not granted属于未通过或未到达，不能继承为pass；没有用户授权不得绕过权限。纯音乐/笑声/BGM必须单列；没有对应可信标注则不宣称该域更强。

若精度获益但超2.5GiB，报告“高资源候选、默认不合格”，不继续长稳耗费，除非用户另外同意研究该档。12GiB、压力/交换与队列硬停止仍按02章。

## 自动决策的边界

未来p0/p3/decisions.py须校验全部证据、锁、配置和人工状态，不能只用总分。多阶段fail-closed：有未评分、缺hash、未知语义、缺DOM或许可风险时不输出release_candidate。

工具算数通过、Schema通过、脚本测试通过、作者论文优秀都不是发布门。最终独立review签字和用户明确选择才允许生产切换；单次成功不自动合并、推送或删旧模型。

<!-- END docs/08-gates-and-stop-rules.md -->

---

<!-- BEGIN docs/09-runtime-integration.md -->

# 09 待开发的 P3 接口与现有代码对接

本章所有 `p0/p3/*` 都是**未来实施目标**，不是本包已实现文件。包内仅 `scripts/p3_evidence.py` 的三个离线辅助命令可运行。先建无模型测试，再加候选，不把安装成功当作实现完成。

## 现有能力与新增边界

| 现存位置 | 可复用能力 | 不能直接假定 |
| --- | --- | --- |
| `p0/p2/types.py` | AudioSpan、SourceUnit、TranslationResult | 并无统一原生流适配器 |
| `p0/p2/events.py` | EventLog、部分因果水位检查 | 不证明前缀不可回写、DOM可读或真实worker取消 |
| `p0/p2/runner.py` | pcm_packets、B0分段与MT复现 | 现有run_probe只支持B0；feed_1x事件名须核对真实语义 |
| `p0/p2/b3_protocol.py` | StreamCommitter、确定性source_seed | piece区间是估计，不是音素／词级真实时间戳 |
| `p0/p2/b3_worker.py` | Vox持续session、UTF8跨槽、EOF等 | 未实现候选在线MT、1×、生产取消和长稳 |
| `p0/p2/realtime.py` | B0实际WebSocket 1×回放 | 不能换experiment ID就跑新候选；不是Chrome DOM验收 |
| `p0/p2/resources.py`、`p0/monitor.py` | 树内存、CPU、全机GPU、采样及部分守卫 | 需补持续压力和native owner观测；不重造另一套混口径采集 |
| `engine/src/subtitle_service/backend.py` | SubtitleBackend、SubtitleSession异步合同 | 新适配器未被生产factory注册 |

不得修改历史P2冻结代码或旧receipt来适配新run。若新P3只需调用旧稳定纯函数，锁住导入文件hash；如果必须改变行为，在P3显式包装并做回归。

## 原生接口规格

目标：`p0/p3/types.py` 与 `p0/p3/adapters/base.py`。下列为需要实现的Python类型规格；并非拷入本包后已经能加载模型。

```python
@dataclass(frozen=True)
class StreamContext:
    run_id: str
    session_id: str
    epoch: int
    clip_id: str
    sample_rate: int  # 固定16000

@dataclass(frozen=True)
class AudioPacket:
    sequence_id: int
    start_sample: int
    end_sample: int
    pcm_s16le: bytes
    accepted_at_ns: int

@dataclass(frozen=True)
class StablePiece:
    start_sample: int
    end_sample: int
    text: str  # 本次新定稿delta；不可重复包含旧文本
    alignment_kind: str  # 可证decoder区间或causal_input_envelope，不能混称词时标

@dataclass(frozen=True)
class NativeUpdate:
    consumed_samples: int  # 已完成native计算实际使用的真实PCM末端，不是内部buffer长度
    decoder_frontier_sample: int | None  # 无词时标时为None，不伪填consumed
    committed_text: str  # 整个session/epoch的累计已定稿日文
    pieces: tuple[StablePiece, ...]
    pending_text_start: int | None
    provisional_text: str | None  # 仅诊断，不允许进入MT
    finished: bool
    termination_reason: str | None
    diagnostics: dict

class NativeSession(Protocol):
    def push(self, packet: AudioPacket) -> NativeUpdate: ...
    def finish(self) -> NativeUpdate: ...
    def close(self) -> None: ...

class NativeAdapter(Protocol):
    def warmup(self) -> dict: ...
    def open(self, context: StreamContext) -> NativeSession: ...
    def close(self) -> None: ...
```

这些是同步native接口，由**单个所有者worker**串行调用。外层 `p0/p3/native_runner.py` 异步处理WS、PCM队列、取消和Hy任务；不得在事件循环直接执行重型同步推理，也不得多线程同时操作一个模型state。全链路资源把子进程／线程执行器及Hy计入。

构造、warmup、open、push、finish、close使用固定owner；是否必须单进程隔离由所选backend决定，并写入runtime-lock。实验如使用进程，超时后关闭/回收该worker有明确有界过程；若无法安全终止，保持清理中并停止本轮，不能谎报lease已释放。

不变量：

- packet无重叠无缺口，`len(pcm_s16le) == 2*(end-start)`；正常3200样本，短尾后只能finish。
- `0 <= consumed_samples <= accepted_samples`，单调不回退；可证的decoder_frontier存在时再要求0 <= frontier <= consumed且单调，无时标路径为None。送入native的音频哈希可追踪到真实已接纳PCM。
- `committed_text.startswith(previous_committed_text)`，本次pieces拼接恰好等于累计文本的新后缀；不通过Unicode归一化掩盖回写。
- 每piece按alignment_kind提供可证decoder估计区间或实际输入的因果包络，end不越过consumed；均不能代替独立语义参考时间。只有选择对齐桥接时才对未知位置保守等待；R2T2按下节已登记的无时标屏障快照，不等待不存在的word timing。
- R2T2内部重置／16秒滚窗不改变外部session/epoch的累计账本。只能内部记录新的utterance/window ID，不能换外部scope绕过prefix检查。
- pending尾部的位置、right context、padding和输入余数分别记录；padding不增加accepted/consumed真实样本。
- finish最多一次，close可重复且幂等。R2T2 step_decode_budget_exhausted是正常有界步进，保留state继续，不设finished；session_token_cap、timeout、overloaded或持续无进展是异常，不能冒充自然EOS。upstream未暴露真实finish_reason时记unknown，不从text推测EOS。所有终止状态保留。

## S0／S1 提交器

目标：`p0/p3/commit_bridge.py`。

1. 从现有VAD生成同音频的实际因果端点，带 `released_at_sample` 和 `flush`。离线可冻结端点列表，但runner在水位到达前不得让策略读取未来条目，更不能用参考语义句末替代VAD。
2. 仅具有可证frontier及piece区间的对齐S0调用被锁定的 `StreamCommitter.advance(accepted=..., frontier=..., pieces=..., eof=..., pending_text_start=...)`，或P3的等价包装。pieces转换为其真实 `(start,end,text)` 格式；先补严格的consumed／单调／重复验证，不能只依赖旧函数的有限检查。R2T2不执行本步，按下节无时标子策略；其余共同的真实VAD、只翻译一次和真实ASR约束仍适用。
3. 每个释放的SourceUnit只翻译一次。空原文记录missing/empty，不调用Hy制造中文。端点等待和coalescing必须进入日志。
4. S1只在已定稿日文中检测固定句末“。！？!?”，切出完整新句并维护已消费字符游标；同一文字随后遇到VAD端点不再次翻译。未带标点的尾部留给S0。原文span必须与已接纳音频对应，句末检测不能给它虚构声学时间。
5. 所有片段使用真实ASR文字进入现有Hy；禁止把正确日文、正确假名oracle或人工标点塞入产品评分路径。

对齐桥接的保守映射可能推迟提交；无时标R2T2按下面快照屏障，不假造对齐。S0测量实际模型/提交组合的中文质量与资源，机制归因须同子策略对照；S1另检提前稳定整句是否改善中文时效，两者不能混成一个分数。

### R2T2固定的无时标S0子策略

S0_UNTIMED_VAD_SNAPSHOT是该主配置的一部分，不直接灌入旧StreamCommitter。这里consumed仅在该次native解码实际完成并使用对应真实PCM后推进；push只追加缓冲、尚未凑够首320/后160ms时，不能推进水位或满足VAD屏障。每次实际VAD release记录release_watermark；只在native实际consumed覆盖该水位的首个已完成update上取累计稳定prefix相对已送MT字符水位的新后缀。覆盖多个release可合并但记录IDs，只提交一次；空后缀留empty，不等待无定义的最早pending位置。

迟稳定的U1尾字留到下一release/EOF，真实计入迟到或漏译。每delta保存生成时实际输入窗口包络和commit_at；聚合span为包络并集，alignment_kind=causal_input_envelope，不能证窄范围时保守[0,consumed]。这些区间是输入证据，不是词/语义时标；R2T2 decoder_frontier_sample为None，pending_text_start=None也不表示没有隐藏尾。语义延迟只用冻结独立参考与DOM。

源prefix/已送MT字符游标在内部W16O8裁切时保留。无模型反例覆盖跨VAD/后置否定、空release、同update多release、迟稳定尾、滚窗、整块/短尾EOF。不能证明因果包络则blocked_runtime并给reason。该策略改变MT分组；B0是完整产品组合对照，基础Qwen机制控制用同策略，不作仅ASR因素归因。

## MT、中文和安全生命周期

保持Hy权重、M0模板、seed生成规则与中文过滤器不变。现有 `HyTranslator.new_session()/translate()` 的初始化和调用线程行为必须实测；不要复用已close的session。新日文范围或文字通过 `source_seed` 派生；同一输入使用旧index以保持可比。

`p0/p3/backend.py` 实现现有 `SubtitleBackend/SubtitleSession` 的**实验实现**，由 `p0/p3/server.py` 选择，禁止未经产品批准注册到main默认factory。无需为P3修改公开v1消息：开始、PCM、finish不变；取消沿用关闭WS，epoch仅为内部诊断。

取消处理顺序固定为：封住外部输出sink → 记录epoch关闭 → 停止接纳和发起新的native/MT任务 → 等待在途任务安全返回／进程回收 → 丢弃晚到结果 → close模型state → 释放全局lease。不能因asyncio task取消就立即允许新session。

队列上限仍是PCM64000样本、enqueue1秒、MT pending4+inflight1、drain8秒。必须观察队首年龄，严禁丢音频追赶。若ASR/MT同用Metal，应先记录单跑与联跑，不盲目另建线程以为能“物理隔离”带宽。

## 必须新增的无模型测试

这些测试在目标机下载模型之前完成，可用受控native stub，但stub成功不代表真实模型成功。

| 目标测试文件（待实现） | 必测场景 |
| --- | --- |
| `p0/p3/tests/test_types.py` | 200ms、短尾、奇数字节、重复包、漏包、浮点/布尔水位、越界 |
| `p0/p3/tests/test_commit_bridge.py` | prefix回写、日/中文独立账本、跨UTF8／标点、重复翻译、VAD未放行、pending跨边界、S1回退 |
| `p0/p3/tests/test_lifecycle.py` | 在途blocking stub取消、晚到结果、busy持续、异常close、满队列时断线、finish两次、epoch隔离 |
| `p0/p3/tests/test_adapter_contract.py` | 两适配器共同接口、内部window reset不清外部prefix、padding不扩大水位、步预算与session cap区分、R2T2累计返回只差分一次、整块EOF隐藏尾 |
| `p0/p3/tests/test_cli.py` | 静态check绝不加载模型；未知ID拒绝；未锁拒绝；模型动作须显式capability/probe；预算耗尽拒绝 |
| `p0/p3/tests/test_evidence.py` | 时间校准、missing分母、20项资格、provisional人审状态、source lock／配置变更失效 |

旧网关取消／背压、P2 B3提交器、短尾与未来音频测试仍须通过。集成完成后再补真实native取消、重复open/close和60分钟；两者不能互相替代。

## 拟实现 CLI 合同

目标 `python -m p0.p3.cli`，顶层 `--run-dir` 和 `--registry`。P2没有 `--phase P3`。下面只是验收规格，**完成T02并通过test_cli之后才允许执行**。

| 子命令 | 行为与边界 |
| --- | --- |
| `check --experiment ID` | 静态文件/锁/权限声明/预算检查，禁止模型导入和推理 |
| `freeze --experiment ID` | 创建新run不可变锁；拒绝已有不一致目录；不下载、不推理 |
| `capability --experiment ID` | 显式加载模型和G0短探针；必须在已授权、已锁定后调用 |
| `probe --experiment ID --mode causal-stream|fixed-cuts|fixed-text --repeat 1|2` | 按registry允许模式；quality-only，不能冒充1× |
| `score --experiment ID` | 缺独立文本判定时导出待审包、score_status=unscored；冻结AI参考+独立逐项对账后可provisional研究筛选，15项待听审单列；正式人审/发布资格仍未通过 |
| `replay --experiment ID --repeat 1|2 --surface ws|chrome` | 仅合格候选；必须实际1×、完整队列和对应显示证据 |
| `accept --experiment ID --stage engineering|holdout` | 分门校验；holdout只有显式授权/冻结后可打开；不发布 |
| `report` | 读取本run输出，列出not_run/失败/缺证，不引发重跑 |

`--help`必须与已实现分支一致；未实现的命令明确退出3及`not_implemented`，不得先写help再当成功功能。未来CLI统一机器输出 `status`、`evidence_paths`、`blockers`、`next_allowed_action`；权限阻塞不自动重试。P3 run锁格式采用本包schema，旧P2 `models` 格式显式转换为独立历史receipt，不能交叉传入两个validate函数。

<!-- END docs/09-runtime-integration.md -->

---

<!-- BEGIN docs/10-ai-execution-and-recovery.md -->

# 10 执行授权 记录与中断恢复

## 先分清四类命令

| 类别 | 现在是否可运行 | 授权与限制 |
| --- | --- | --- |
| 本包三个helper及文档辅助单测（实际数量见BUILD-VALIDATION） | 是，Python3.10+，无模型 | 只读/合成材料验证，不下载不推理，不证明产品性能 |
| 当前仓库P2已实现命令 | 仅在原资产/环境和命令分支满足时 | 本次文档交付未授权重跑；P2 preflight可能真推理 |
| 09章P3 CLI/adapter | 否，待T02等实现 | 先开发和无模型测试，不能假装现成一键脚本 |
| M1模型运行、真实Chrome/holdout、生产切换 | 本轮未做 | 用户授权实施后仍受权限、许可、数据与阶段门约束；生产切换另行明确批准 |

不需要用户逐行批准本包未来获准范围内的普通实验实现，但有新增权限/私有权重/高资源预算/深度框架重写/默认政策变化时必须暂停相关分支。可并行做无关文档和审查，不能绕过阻塞继续该分支。

## 推荐run目录合同（由未来P3实现创建）

统一使用新run根 `p0/evidence/p3/<run_id>/`，目录在gitignore范围内。`run_id`含日期、候选和attempt，不使用旧run名称。相对目录如下：

| 路径 | 内容 |
| --- | --- |
| `run-manifest.json` | 阶段、模式、授权、候选、种子、预算、参考状态、路径 |
| `locks/materials.json` | 本包model-lock格式；当前阶段实际所需模型required=true |
| `locks/runtime-artifacts.json` | 代码commit、patch、依赖锁、VAD、tokenizer/frontend、许可、真实精度 |
| `locks/inputs.json` | PCM/切片/语义/保护项/提示/时标hash及来源授权 |
| `locks/hypothesis.json` | 首输出前选择的Q/正式R/探索R_memory、主指标与参数 |
| `history/` | 旧P2 receipt的只读副本或hash/绝对原路径清单；不修改它 |
| `raw/asr/`、`raw/mt/` | 未裁剪的真实结果、错误、termination，不只留过滤后字幕 |
| `events/` | 后端、WS、Chrome、native state、资源和clock校准原始日志 |
| `derived/` | helper结果、timing输入、规范化prefix、统计；都能从raw重建 |
| `review/` | 隐名评分、争议、待听审、人工身份/时间及独立review |
| `receipts/` | 每步骤输入fingerprint、实际argv、cwd、起止/exit、输出hash与状态 |
| `decision/` | 单候选判定与批次报告，失败/未做也需列出 |

在报告公开版删除token、私密profile、音频路径中的身份、用户视频内容；保留可核对的相对ID/hash。机读材料不能只存截图。源码快照、模型和原始音频的再分发还须许可，不能塞入公开文档zip。

## 锁定流程

`config/model-lock.json`是**材料登记模板**，不是已安装清单。Reazon、Hy revision已核验；所有文件列表/大小仍须目标机补齐。材料根可以是已明确登记的共享.model-cache而非新run目录；文件清单用相对该根的路径，并检查实际resolve目标仍在根内，保留当前Hy两级链接。无需为每个run复制一份1GB权重。候选权重revision未知为null。helper故意会返回exit2，不能为了绿灯删掉required条目。

每个阶段生成一个投影锁：共同必需的Hy/所需B0材料，加本阶段所选候选和机制控制；未选模型保持optional或不收入该run。必须由registry解析期望集合，并验证`expected_required_ids == actual_required_ids`。helper允许空或全optional集合，但这种ready是空集真，不代表任何模型存在，P3 check必须拒绝缺必需ID。

VAD文件不强塞进只有Git/HF revision语义的模型锁：其已知SHA256放在runtime-artifacts，实际大小/位置/来源receipt在目标机补齐。tokenizer、mmproj、frontend、转换图也必须完整枚举；一个GGUF文件hash不足以冻结整个声学链。若没有可信上游来源，则blocked_data，不伪造revision。

本包helper `check-lock`只验证字段完整性；加`--root`才检查本地字节。它不证明HF revision确实产生这些字节，不核查许可证，也不验证VAD和运行时，后几项由P3 check及人工审查承担。无`--root`的ready不得用于模型启动门。

每次执行前重复校验本地hash、选中配置和运行时，避免同路径被换内容。使用HF锁定snapshot，不默认trust_remote_code；确有需要则先审具体revision的代码并隔离，不执行从搜索摘要复制的shell脚本。

## 恢复与幂等

- 仅当step状态passed，输入fingerprint、代码/模型/参数、输出文件hash全同，才能复用已完成步骤。复用标`reused_receipt`而不是`new_measurement`。
- 失败、中断或timeout的attempt保留，下一次使用新attempt目录；原raw和评分不可覆盖。说明修复根因和预算剩余。
- 恢复前检查本轮服务/worker是否确已退出；未知进程状态不得贸然再启动第二实例。只有能验证属于本run的进程才可终止，不能按宽泛名字杀用户进程。
- 模型量化、块长、U、state reset、提交策略、backend/frontend改变均新experiment_id，并消耗相应配置预算。仅修bug也须保留补丁、影响分析及全部受影响回归，不能拿修复前分数混用。
- 参考时标或语义标签变化：新reference_version，B0和所有比较项重算；不能只改对候选有利的单位。
- 发生权限失败、需收费资源、私有模型、系统设置变化或预算耗尽，停止受影响路径，写下一项需要的明确授权。

## AI可独立做与不能代做

AI可完成代码、单测、静态锁检查、合法公开工件准备、结构对齐、暂定语义评分和证据汇总。人工听审/许可判断需真实责任人；AI不能把自己的判断标成`human_verified`。只有冻结AI参考时，独立逐项文本复核可形成score_status=provisional及保守研究筛选，15项仍pending-listen；无人复听不自动阻断其余有依据的研究步骤。连暂定语义或时标也无法确认的项单列needs_human/unknown，不计correct；正式质量、正式尾延迟和默认替换仍需真实人审，不能由AI签字补齐。

独立reviewer不应是同一份提示直接自我签字；至少独立上下文核对冻结前后的配置、所有收益/损失及raw样本。review发现问题先修证据或实现，再重新执行受影响测试；不能只改结论措辞。

## 停止后保留什么

所有路线不胜出时，交付默认保留、候选失败类型、已花预算、尚未确定问题和重新开启所需的新证据。值得再开应是具体变化，例如该日语权重发布官方cache ONNX、R2T2 full llama实现已验证的编码缓存、许可放开、或已冻结新场景显著改变错误分布；不是README多了“realtime”一词。

不能以“找到更强模型”作为无期限任务。当前包完成标准是形成可信可复核决定，不是保证找到新赢家。

<!-- END docs/10-ai-execution-and-recovery.md -->

---

<!-- BEGIN docs/11-source-ledger.md -->

# 11 来源台账与证据边界

## 本包实际核查了什么

研究日期为2026年11月10日。项目基准固定为 [live-video-tanslate@77e24c3e490a11721041a40ad4bd203f51f84039](https://github.com/lkyprogramer/live-video-tanslate/commit/77e24c3e490a11721041a40ad4bd203f51f84039)，提交时间为UTC 2026年11月9日13:30:30，即北京时间21:30:30。所有“当前仓库已有能力”均指这个pin；执行前如HEAD变化，先做差异说明，不能把浮动main当作同一个实验版本。

本包完成了公开资料及已连接仓库的只读核查，并执行了下载源码的argparse `--help`。**没有新增目标M1 Pro上的模型编译、加载、推理、播放、资源或质量实测。** 文档辅助工具通过，不构成模型可运行、质量合格或发布资格。此前P2实测属于历史证据，必须保留其原run、硬件、冻结配置与参考状态。

2026年11月10日扩展Exa检索共 **45次查询、395条结果（含重复）、311个按精确URL去重的地址**。这些是命中统计，不是全文阅读量、独立论文数、独立实验数或已验证模型数。查询、命中和后续阅读的关系以[搜索台账](research/search-ledger.json)为准；同一页面的不同URL也可能属于同一来源。搜索摘要仅用于发现，语言方向、工件身份、接口和许可尽量回到论文、作者仓库、模型卡及实际源码核验。

## 证据层级

| evidence_level | 含义 | 本包可支持与不可推出的结论 |
|---|---|---|
| `source_verified` | 已读取相应源码、配置、工件元数据或一手文本，能指出具体锚点 | 可确认接口存在、代码调用、某项公开声明或pin；不证明该路径在M1成功运行。论文表格被读到时，数值仍是作者实验，不升级为本地实测 |
| `author_reported` | 作者论文、模型卡或项目文档所报告的精度、速度、内存与平台结果 | 只能连同作者硬件、数据、语言和协议转述；不能外推为用户M1、日语短流或日中字幕结果 |
| `local_measured` | 特定设备与冻结run真实执行并保存原始收据、输出和采样 | 本包引用P2历史记录时可注明历史local_measured；本包没有新的目标机模型结果。此次help与源码hash检查只证明相应工具/文件检查，不是模型实测 |
| `inferred` | 基于已核查事实作出的工程判断或待验证假设 | 例如“全llama Mac小验证可能可行”“123M更值得先做缓存运行gate”；必须标为判断，不写成已支持、已通过或已省内存 |

同一候选可以同时拥有不同层级的多项证据，不应给整个候选贴一个最高级标签后覆盖所有字段。模型体积、训练参数、算法lookahead、offline RTF、首次token和稳定正确可读中文延迟是不同量，不能互相替代。

## 已核验项目源码与既有能力

通过GitHub只读工具取得固定pin的完整recursive tree，响应未截断；读取并保存66份相关源码、协议和历史报告。逐份计算Git blob SHA-1，与该tree返回的blob SHA核对，**66/66一致**，见[源码blob校验表](research/source-blob-sha1.txt)。这是“下载内容与指定提交一致”的证据，不是编译、运行、模型数值parity或测试通过证据。详见[仓库对接审计](research/repo-audit.md)及[CLI帮助证据](research/cli-help-evidence.md)。

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
| Fun-ASR-MLT备选 | 研究保留的候选线索，见[综合复核](research/expanded-review.md) | 本包不提供已冻结的新正式配置；必须区分Nano/MLT、模型checkpoint与MLX/GGUF后端，选定后补全身份、许可和锁 | 不能借“廉价探针”额外开正式评分席位；已有冻结结果可引用，新加载/正式评分须计入统一预算 |

用于纠正平台宣传的独立锚点：[speech-swift源码a57702e6f523fa36ac5532d2bdc5f35f701a052c](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/OmnilingualASR/OmnilingualASR.swift)默认CPU+GPU请求与固定窗口，不能被文档中的ANE描述升级为实测派发；[CoreML工件a755e007d0a56bd8c7e9885f55bb2112e52b4d51](https://huggingface.co/aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8/tree/a755e007d0a56bd8c7e9885f55bb2112e52b4d51)并未证明v2等权重；[Audio8 Apple包6a3f52c400cd436e1bd32c0514758345ddf14bfa](https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE/tree/6a3f52c400cd436e1bd32c0514758345ddf14bfa)只支持其特定工件检查，不构成本项目日语质量或M1内存证据。

所有未知revision/hash应保存为`null`并阻塞相关正式加载/计分，不能写猜测SHA、浮动latest或成功占位值。Git源码commit、HF权重revision、下载文件SHA256和转换源版本是不同字段，不能用一个替代另一个。发布物不可访问或权重private时记来源/权限阻塞；抓取失败不自动等于模型不存在，更不能借旧缓存绕过权限。

## 旧失败配置什么时候可以重开

同名模型重新被搜索命中、换一个UI/框架、社区给出其他机器速度、出现CoreML/Metal标签，都不构成重开理由。Haas、PengChengStarling、已测Moonshine、Whisper、PLaMo、SenseVoice、Voxtral及Gemma的历史失败继续有效于各自冻结条件。

重开必须同时具备：明确未被旧实验覆盖的错误机制；可核验的新checkpoint/转换/运行时差异；说明哪些变量保持不变及如何做parity；预登记预算、候选席位、质量/资源主指标与停止条件；需要超出当前范围时获得新的明确授权。只换运行时但权重相同，应标“同checkpoint后端验证”，不能重复包装成新模型。源码/加载阻塞不能写成质量失败；质量未运行不能记0分；新量化或解析修补不能追溯修改旧成绩。

## 包内研究导航

| 材料 | 主要用途 | 阅读边界 |
|---|---|---|
| [expanded-review.md](research/expanded-review.md) | 本轮综合结论、优先级与排除理由 | 综合判断不是新的目标机成绩；实施规格与其不同处，以本包显式预算/合同为准 |
| [r2t2-mac-feasibility.md](research/r2t2-mac-feasibility.md) | R2T2固定源码锚点、构建/导入风险、KV清理与两种流API区别 | source-only；没有编译或成功加载记录 |
| [japanese.md](research/japanese.md) | 123M、日语原生流与假名路线的一手来源、语言/许可/平台缺口 | 作者硬件结果与未核实Mac能力分列 |
| [native.md](research/native.md) | CoreML/ANE、Audio8、Omnilingual版本与native部署的核查 | 配置要求计算单元不等于实际设备派发 |
| [streaming.md](research/streaming.md) | 稳定前缀训练、语言方向、权重公开与因果/不可回写边界 | “未找到”仅指本轮范围，不能绝对证明不存在；初步结论如被后续固定源码复核修正，以固定锚点为准 |
| [search-ledger.json](research/search-ledger.json) | 查询式、命中与统计的机器收据 | 不能当全文阅读清单、权重锁或质量测试 |
| [repo-audit.md](research/repo-audit.md) | 指定项目pin的既有接口、结果、调度/取消合同与P3拟新增位置 | 只读对接审计，不是实现完成证明 |
| [cli-help-evidence.md](research/cli-help-evidence.md) | 已下载P2源码的实际argparse帮助输出 | 参数存在不等于每个phase/候选都已实现；只有help被运行 |
| [source-blob-sha1.txt](research/source-blob-sha1.txt) | 66份源码/报告blob与项目pin的一致性 | 不含模型权重完整性证明，也不等于模型或设备测试 |

材料中的来源入口若为main/master或模型卡URL，只是可追溯入口，不自动成为正式内容锁。正式实施应把实际采用内容解析为不可变pin并保存收据；本轮未保存的目标机结果不得在后续报告中补写为本包已测。

<!-- END docs/11-source-ledger.md -->

---

<!-- BEGIN docs/12-delivery-validation.md -->

# 12 本地复核与交付验证边界

## 本次任务与来源

用户要求评审并直接完善P3方案，作为后续探索基础。本次只修改本目录规格、配置、文档辅助工具及测试；产品engine/extension、P2冻结代码与历史实验记录保持原状。没有安装依赖、下载权重、开启新模型或浏览器实验。

GPT Pro原始52文件保存在本机ignored的p0/evidence/p3-plan-review-20261110-144948/original/。原validation/INDEPENDENT-REVIEW.md和OFFLINE-TESTS.txt是随包历史回执，已标来源，不能替代本次复核。源码台账66条Git blob已与本地77e24c3逐项核对；这证明pin一致，不是本次逐文件重新精读或模型测试。

## 独立审查与修订

独立Astra使用gpt-6-astra/xhigh、独立上下文审查原稿、现有代码/历史结果和一手源码，初审发现3项P1、5项P2，见[独立初审](validation/ASTRA-INITIAL-REVIEW.md)。主代理另发现pytest命令、prefix端点回退、缺可重建文档工具及worktree资产复用问题。问题、修法和保留边界见[修订台账](REVIEW-CHANGES.md)。

本次补回可读DOM停留门、锁定R2T2实际driver/整块EOF/无时标S0、补raw-PCM前端因果门、明确同工件parity与暂定AI评分、统一内存footprint口径，并修正辅助器和可复验命令。R2T2与123M两主线和3+1配置/2E2E上限保留，P2正式R延迟门未改。

修订后的最终独立复核单独保存在validation/ASTRA-FINAL-REVIEW.md；没有该记录或仍有阻断项时，不能引用历史回执声称已完成最终审查。

## 实际可执行的本地检查

源文件、Schema、模板、五个合成例的预期退出码、阅读副本、相对链接与SHA清单由scripts/validate_package.py检查。它使用当前engine开发环境已有的jsonschema；三个evidence helper与离线单测本身仅需Python标准库。

~~~sh
engine/.venv/bin/python -B docs/live-video-translate-p3-2026-11-10/scripts/validate_package.py --refresh
engine/.venv/bin/python -B docs/live-video-translate-p3-2026-11-10/scripts/validate_package.py --check
~~~

实际执行数量/结果见[本地构建回执](validation/BUILD-VALIDATION.json)和[原始测试输出](validation/LOCAL-OFFLINE-TESTS.txt)。反例前缀预期exit1、未知必需锁预期exit2；release_eligible始终false。新prefix回归先在原helper上实际失败，再修复；同channel端点不回退，跨channel仍独立。

--refresh只能证明当前文档工具通过，--check不重新推理。维护分章/config/prompts后重新生成阅读副本，禁止手改两份权威文本。新增独立review附件只改变文档清单，也须更新清单并check；不把review通过转成模型成功。

## 明确未执行

没有在M1构建R2T2、运行123M RNNT或导出ONNX；没有任何P3中文准确率、1×/DOM、CPU/GPU/共享内存、能耗或60分钟新结果。权重revision、实际工件SHA、目标runtime/parity仍须按T00起逐阶段补齐。没有人工复听15项、解封test/、改默认、提交或push。模型许可与真实人审不能由本包Schema或AI代签。

当前完成的是可执行研究规格及工具校验。用户后续明确授权实施后才从T00推进；遇数据/运行时阻塞保留事实并停止该依赖分支，不据此给模型打0分。

<!-- END docs/12-delivery-validation.md -->

---

## config/experiment-registry.json

```json
{
  "schema_version": 1,
  "document_status": "execution_spec_not_implemented",
  "as_of": "2026-11-10",
  "timezone": "Asia/Shanghai",
  "project_revision": "77e24c3e490a11721041a40ad4bd203f51f84039",
  "reference_docs": [
    "docs/00-contract-and-baseline.md",
    "docs/02-experiment-design.md",
    "docs/08-gates-and-stop-rules.md",
    "docs/09-runtime-integration.md"
  ],
  "baseline": {
    "id": "B0-P3-PAIRED",
    "asr": "reazon-k2-v2",
    "mt": "hy-mt2-1p8b",
    "vad_artifact": "silero-v4-b0",
    "seeds": [
      11,
      29
    ],
    "historical_d0_units": 34,
    "historical_offline_correct": [
      20,
      18
    ],
    "historical_1x_5s_correct": [
      19,
      17
    ],
    "historical_scores_are_not_new_measurements": true
  },
  "budgets": {
    "primary_candidate_limit": 3,
    "conditional_configuration_limit": 1,
    "total_formal_candidate_configuration_limit": 4,
    "e2e_configuration_limit": 2,
    "secondary_selection_limit": 1,
    "dependency_repairs_per_candidate": 2,
    "higher_precision_diagnostic_configurations_per_candidate": 1,
    "engineering_hours_per_day": 8,
    "g0_engineering_hours": 8,
    "minimal_T02a_included_in_g0": true,
    "T02b_online_integration_total_engineering_hours": 8,
    "T02b_requires_quality_entry_gate": true,
    "r2t2_engineering_hours": 16,
    "ja123_engineering_hours": 16,
    "primary_quality_runtime_hours_each": 2,
    "secondary_engineering_hours": 4,
    "secondary_quality_runtime_hours": 2,
    "conditional_engineering_hours": 4,
    "long_run_minutes": 60,
    "exclude_download_and_human_wait_from_engineering_hours": true,
    "record_total_wall_clock": true,
    "model_experiment_groups_serial": true,
    "within_group_production_pipeline_concurrency": true,
    "controls_charged_to_parent_budget": true
  },
  "primary": [
    {
      "id": "P3-R2T2-160-U1-S0-W16O8",
      "role": "primary",
      "selected": true,
      "model_ids": [
        "r2t2-gguf",
        "hy-mt2-1p8b"
      ],
      "routes": [
        "Q"
      ],
      "input_mode": "causal-stream",
      "asr_backend": "full_stream_llama",
      "weight_quantization": "Q4_K_M",
      "mmproj_quantization": "Q8",
      "chunk_ms": 160,
      "unstable_internal_tokens": 1,
      "commit_policy": "S0",
      "state_policy": "streaming_transcribe_no_reset_16s_drop_oldest_8s",
      "reset_on_vad": false,
      "formal_output_is_session_cumulative_committed_prefix": true,
      "source_recipe": "docs/04-r2t2-mac-spike.md",
      "implementation_status": "not_implemented",
      "measurement_status": "not_run",
      "language": "Japanese",
      "context": "",
      "lookahead_ms": 160,
      "first_chunk_ms": 320,
      "subsequent_chunk_ms": 160,
      "unfixed_chunk_num": 0,
      "rollback_punctuation": false,
      "commit_subpolicy": "S0_UNTIMED_VAD_SNAPSHOT",
      "driver_origin": "local_combination_of_pinned_example_schedule_and_no_reset_not_official_complete_recipe",
      "step_token_budget": {
        "first": 4,
        "after_new_committed_text": 2,
        "without_new_committed_text": "min(previous+1,4)"
      },
      "eof_policy": "pinned_finish_no_reset; empty_buffer_keeps_withheld_tail; no_provisional_promotion",
      "eof_max_new_tokens": 4
    },
    {
      "id": "P3-JA123-RNNT-480-S0",
      "role": "primary",
      "selected": true,
      "model_ids": [
        "ja123-m1-6",
        "hy-mt2-1p8b"
      ],
      "routes": [
        "Q",
        "research_R_memory"
      ],
      "input_mode": "causal-stream",
      "checkpoint": "M1-6.nemo",
      "decoder": "RNNT",
      "right_context_ms": 480,
      "commit_policy": "S0",
      "backend": "resolve_exact_NeMo_or_cache_ONNX_before_first_output",
      "ctc_substitution_allowed": false,
      "source_recipe": "docs/05-japanese-cache-aware.md",
      "implementation_status": "not_implemented",
      "measurement_status": "not_run"
    }
  ],
  "secondary": {
    "default_choice": "P3-OMNI300-V2-I8-S0",
    "selected_choice": null,
    "selection_must_be_frozen_before_any_secondary_output": true,
    "automatic_rotation_after_failure": false,
    "choices": [
      {
        "id": "P3-OMNI300-V2-I8-S0",
        "model_ids": [
          "omni300-v2-onnx",
          "hy-mt2-1p8b"
        ],
        "routes": [
          "Q",
          "research_R_memory"
        ],
        "input_mode": "fixed-cuts",
        "backend": "ONNX_Runtime_CPU",
        "weight_quantization": "INT8",
        "commit_policy": "S0",
        "upstream_v2_provenance_required": true
      },
      {
        "id": "P3-FUNMLT-Q8-S0",
        "model_ids": [
          "fun-asr-mlt",
          "hy-mt2-1p8b"
        ],
        "routes": [
          "Q"
        ],
        "input_mode": "fixed-cuts",
        "backend": "transcribe.cpp_Metal_if_verified",
        "weight_quantization": "Q8_0",
        "language": "ja",
        "hotwords": false,
        "itn": "original_default_locked",
        "commit_policy": "S0"
      },
      {
        "id": "P3-KANA20-S0",
        "model_ids": [
          "kana20m",
          "hy-mt2-1p8b"
        ],
        "routes": [
          "Q"
        ],
        "input_mode": "fixed-cuts",
        "backend": "author_PyTorch_CPU",
        "precondition": "oracle_correct_kana_to_Hy_pass_with_reviewed_reference",
        "native_streaming_claim": false,
        "commit_policy": "S0"
      }
    ]
  },
  "conditional": {
    "selected_choice": null,
    "global_limit": 1,
    "choices": [
      {
        "id": "P3-R2T2-160-U1-S1-W16O8",
        "parent_id": "P3-R2T2-160-U1-S0-W16O8",
        "change_only": {
          "commit_policy": "S1"
        },
        "routes": [
          "Q"
        ],
        "precondition": "two_quality_repeats_noninferior_no_new_critical_no_protected_loss_resource_acceptable"
      },
      {
        "id": "P3-JA123-RNNT-80-S0",
        "parent_id": "P3-JA123-RNNT-480-S0",
        "change_only": {
          "right_context_ms": 80
        },
        "routes": [
          "Q",
          "research_R_memory"
        ],
        "precondition": "480ms_cache_runtime_and_quality_gates_pass_and_measured_latency_headroom"
      }
    ]
  },
  "controls": [
    {
      "id": "CTRL-QWEN17-160-U1-W16O8",
      "model_ids": [
        "qwen17-gguf-control"
      ],
      "purpose": "matched_R2T2_mechanism_only",
      "counts_as_candidate": false,
      "cost_record_required": true,
      "match": [
        "backend",
        "quantization",
        "chunk_ms",
        "unstable_internal_tokens",
        "state_policy",
        "input_audio",
        "language",
        "context",
        "lookahead_ms",
        "first_chunk_ms",
        "step_token_budget",
        "rollback_punctuation",
        "commit_subpolicy",
        "eof_max_new_tokens"
      ],
      "no_match_means_no_training_only_attribution": true
    },
    {
      "id": "ORACLE-KANA-HY",
      "model_ids": [
        "hy-mt2-1p8b"
      ],
      "purpose": "semantic_interface_gate_only",
      "counts_as_candidate": false,
      "counts_as_product_score": false,
      "reviewed_pronunciation_required": true
    }
  ],
  "policies": {
    "research_R_memory_is_not_legacy_R": true,
    "default_replacement_requires_legacy_Q_or_R_latency_and_full_acceptance_or_explicit_policy_approval": true,
    "no_auto_default_change": true,
    "no_auto_push": true,
    "no_auto_cleanup": true,
    "no_future_audio": true,
    "no_cloud_or_4090": true,
    "no_subtitles_or_ocr": true,
    "no_unselected_model_download": true
  }
}
```

---

## config/model-lock.json

```json
{
  "schema_version": 1,
  "project_revision": "77e24c3e490a11721041a40ad4bd203f51f84039",
  "entries": [
    {
      "id": "reazon-k2-v2",
      "required": true,
      "source_url": "https://huggingface.co/reazon-research/reazonspeech-k2-v2",
      "revision": "291488c8151be24d7da4bf7af26e533fad96e407",
      "files": []
    },
    {
      "id": "hy-mt2-1p8b",
      "required": true,
      "source_url": "https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit",
      "revision": "e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912",
      "files": []
    },
    {
      "id": "r2t2-gguf",
      "required": false,
      "source_url": "https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF",
      "revision": null,
      "files": []
    },
    {
      "id": "qwen17-gguf-control",
      "required": false,
      "source_url": "https://huggingface.co/Qwen/Qwen3-ASR-1.7B",
      "revision": null,
      "files": []
    },
    {
      "id": "ja123-m1-6",
      "required": false,
      "source_url": "https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open",
      "revision": null,
      "files": []
    },
    {
      "id": "omni300-v2-onnx",
      "required": false,
      "source_url": "https://huggingface.co/OpenVoiceOS/omnilingual-asr-ctc-300m-onnx",
      "revision": null,
      "files": []
    },
    {
      "id": "kana20m",
      "required": false,
      "source_url": "https://huggingface.co/kizuna-intelligence/zipformer-rnnt-japanese-hiragana",
      "revision": null,
      "files": []
    },
    {
      "id": "fun-asr-mlt",
      "required": false,
      "source_url": "https://huggingface.co/FunAudioLLM/Fun-ASR-MLT-Nano-2512",
      "revision": null,
      "files": []
    }
  ]
}
```

---

## config/runtime-artifact-lock.json

```json
{
  "schema_version": 1,
  "document_status": "source_snapshot_with_unresolved_target_fields",
  "as_of": "2026-11-10",
  "project_revision": "77e24c3e490a11721041a40ad4bd203f51f84039",
  "source_locks": [
    {
      "id": "project",
      "source_url": "https://github.com/lkyprogramer/live-video-tanslate",
      "revision": "77e24c3e490a11721041a40ad4bd203f51f84039",
      "patch_sha256": null
    },
    {
      "id": "r2t2",
      "source_url": "https://github.com/netease-youdao/Confucius4-R2T2",
      "revision": "72b81be7070095410b75ed2e11998e347f655218",
      "patch_sha256": null
    },
    {
      "id": "r2t2-llama",
      "source_url": "https://github.com/ggml-org/llama.cpp",
      "revision": "ad6c66839af3c5646fba8c6c2e2087a1e4e38948",
      "patch_sha256": null
    },
    {
      "id": "ja123-author",
      "source_url": "https://github.com/yoinncyan/my-interspeech-2026",
      "revision": "cc1cdf12a3133b22349c520e5b81f0334a01e5ad",
      "patch_sha256": null
    },
    {
      "id": "whisperlivekit-baseline",
      "source_url": "https://github.com/QuentinFuxa/WhisperLiveKit",
      "revision": "13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2",
      "patch_sha256": null
    }
  ],
  "baseline_runtime_from_pyproject": {
    "python": ">=3.12,<3.13",
    "sherpa_onnx": "2.0.1",
    "mlx": "0.31.2",
    "mlx_lm": "0.31.3",
    "transformers": "5.22.1",
    "actual_environment_fingerprint_sha256": null,
    "engine_uv_lock_sha256": null
  },
  "baseline_artifacts": [
    {
      "id": "silero-v4-b0",
      "kind": "existing_frozen_ONNX_not_a_new_download",
      "relative_path": null,
      "sha256": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
      "size_bytes": null,
      "provenance_receipt_sha256": null
    },
    {
      "id": "hy-M0-template-and-generation",
      "kind": "existing_frozen_prompt_and_sampling",
      "relative_path": null,
      "sha256": null,
      "size_bytes": null,
      "provenance_receipt_sha256": null
    }
  ],
  "required_candidate_fields": [
    "exact_runtime_lock",
    "frontend_config_sha256",
    "tokenizer_sha256",
    "actual_weight_dtype",
    "encoder_dtype",
    "projector_dtype",
    "activation_dtype",
    "kv_or_rnnt_state_dtype",
    "context_and_batch_limits",
    "thread_limits",
    "license_text_sha256",
    "license_use_decision",
    "reference_runtime_or_fixture_sha256",
    "build_and_conversion_argv",
    "actual_artifact_source_mapping",
    "reference_backend_artifact_frontend_input_schedule_and_parity_tolerances",
    "raw_PCM_frontend_causal_support_and_feature_ready_watermark",
    "surface_process_membership_and_hot_phase_definition"
  ],
  "unresolved_fields_block_selected_phase": true,
  "note": "Source commits are not model weight revisions. Fill actual file inventories and target runtime per selected phase. Null patch hash means unresolved; use a separately recorded clean-tree assertion when there is genuinely no patch, never a dummy hash. Historical receipts stay immutable."
}
```

---

## config/evaluation-contract.json

```json
{
  "schema_version": 1,
  "as_of": "2026-11-10",
  "status": "pre_registered_spec_not_results",
  "input": {
    "sample_rate_hz": 16000,
    "channels": 1,
    "encoding": "s16le",
    "packet_samples": 3200,
    "last_packet_can_be_short": true,
    "clock": "monotonic_with_browser_calibration"
  },
  "queues": {
    "pcm_samples_max": 64000,
    "enqueue_timeout_ms": 1000,
    "mt_pending_max": 4,
    "mt_inflight_max": 1,
    "browser_buffered_bytes_max": 64000,
    "drain_timeout_ms": 8000,
    "native_call_timeout_ms": 30000
  },
  "dataset": {
    "D0": {
      "pcm": 9,
      "fixed_cuts": 88,
      "fixed_mt_texts": 62,
      "semantic_units": 34,
      "pending_listen": 15,
      "seeds": [
        11,
        29
      ]
    },
    "D1_min_semantic_units": 20,
    "holdout_min_sources": 6,
    "holdout_min_semantic_units": 60,
    "short_utterance_min_count": 20,
    "short_duration_seconds": [
      1,
      3
    ]
  },
  "timing": {
    "coverage_thresholds_ms": [
      2000,
      3000,
      5000,
      15000
    ],
    "matching_window_ms": 15000,
    "quantile": "nearest_rank",
    "allow_negative_sentence_end_latency": true,
    "unmatched_overall_latency": "infinity_encoded_null_with_unbounded_true",
    "formal_latency_common_correct_min": 20,
    "browser_calibration_roundtrips_before": 10,
    "browser_calibration_roundtrips_after": 10
  },
  "Q": {
    "offline_each_repeat_net_correct_min": 3,
    "realtime_each_repeat_net_5s_correct_min": 4,
    "new_critical_errors_max": 0,
    "protected_losses_max": 0,
    "matched_p95_ms_max": 3000
  },
  "formal_R_latency": {
    "main_metric": "paired_common_correct_dom_p95",
    "each_repeat_relative_improvement_min": 0.2,
    "quality_noninferior_each_repeat": true,
    "coverage_5s_noninferior_each_repeat": true,
    "new_critical_errors_max": 0,
    "protected_losses_max": 0,
    "common_correct_min": 20,
    "posthoc_switch_forbidden": true
  },
  "research_R_memory": {
    "main_metric": "tree_physical_footprint_peak_bytes",
    "each_repeat_relative_improvement_min": 0.2,
    "quality_noninferior_each_repeat": true,
    "coverage_5s_noninferior_each_repeat": true,
    "new_critical_errors_max": 0,
    "protected_losses_max": 0,
    "is_legacy_R": false,
    "grants_default_replacement_eligibility": false,
    "p95_guard_can_be_descriptive_below_N20": true,
    "missing_clock_or_timing_reference": "inconclusive",
    "phase": "hot_input_and_drain",
    "same_surface_required": true,
    "offline_entry_is_quality_prerequisite_only": true
  },
  "tolerances": {
    "cpu_seconds_per_audio_second_relative_increase_max": 0.1,
    "system_gpu_mean_percentage_point_increase_max": 3,
    "p95_guard_increase_max_of": {
      "absolute_ms": 100,
      "relative": 0.1
    }
  },
  "resources": {
    "sample_interval_ms": 250,
    "default_hot_tree_footprint_peak_bytes_max": 2684354560,
    "default_footprint_unavailable": "inconclusive",
    "hard_tree_footprint_bytes_max": 12884901888,
    "critical_pressure_duration_s": 30,
    "swap_growth_duration_s": 30,
    "oldest_pcm_age_s": 2,
    "oldest_pcm_age_violation_duration_s": 10,
    "long_run_minutes": 60,
    "long_run_footprint_median_growth_max_of": {
      "relative": 0.1,
      "bytes": 134217728
    },
    "hot_interval": "warmup completed before first formal PCM through completed normal drain/native/MT",
    "surface_process_sets": {
      "ws": "run-owned server/native/Hy descendants",
      "chrome": "ws set plus run-owned isolated Chrome processes"
    },
    "missing_footprint_or_process_sample": "inconclusive; never substitute RSS",
    "aggregate": "sum same-sample process footprints then peak; no independent peak addition; not exact unique physical pages"
  },
  "default_replacement": {
    "legacy_gate": "Q_or_formal_R_latency_or_explicit_user_approved_new_policy",
    "historical_5s_correct_min": 109,
    "historical_units": 121,
    "holdout_5s_correct_fraction_min": 0.9,
    "matched_p50_ms_max": 1500,
    "matched_p95_ms_max": 3000,
    "short_onset_to_correct_p90_ms_max": 4500,
    "short_missing_is_infinity": true,
    "silence_captions_max": 0,
    "new_critical_errors_max": 0,
    "protected_losses_max": 0,
    "external_revisions_max": 0,
    "require_human_review": true,
    "require_independent_review": true,
    "production_switch_requires_explicit_user_approval": true
  },
  "readability": {
    "continuous_visible_min_ms": 600,
    "characters_per_second": 12,
    "character_counter": "sum(ch.isalnum() for ch in text_zh)",
    "required_duration": "max(600, character_count/12*1000)",
    "require_closed_visible_interval": true,
    "require_unoccluded": true,
    "multi_caption": "each necessary fragment qualifies; last necessary qualified first-visible timestamp",
    "latency_endpoint": "qualified interval first visibility; no dwell-time addition",
    "WS_and_DOM_scores_separate": true
  }
}
```

---

<!-- BEGIN prompts/EXECUTOR.md -->

# 交给实施AI的启动提示词

你负责在用户另行授权实施后，执行本包P3探索，不负责保证找到新赢家。先读README和完整方案；重点读00合同、03任务、08门槛、09接口与config。不要从研究附录里的某个旧建议直接跳到下载模型。

## 首次行动

1. 明确当前授权；若用户只是让你读包/评估，停留在只读。获得实施授权后检查仓库状态、77e24c3差异和相关AGENTS.md，建立独立worktree，保护用户改动。
2. 列出已存在P2能力、需要新增P3文件、缺失模型/音频/人工参考、每个阻塞的具体下一步。不要写“先安装所有候选”。
3. 跑本包当前离线测试与合成示例（实际数量见validation/BUILD-VALIDATION.json）；正确识别退出1/2的预期反例。这不计模型能力通过。
4. 按T00→T01/T02a→模型source/parity/质量→T06推进；合格者才做T02b在线整链。T01/T02a合计8工程小时、T02b整批8小时。先无模型TDD、再锁定材料、再capability；不预先花数天搭通用框架。P3 CLI尚不存在时先实现相应最小分支，不把文档接口当现成命令。

## 执行规则

- 原生产默认Reazon+Hy和旧P2收据不动。P3实验默认不push、不注册生产factory、不清理旧模型。
- 两主线R2T2与123M日语RNNT；第三席位Omni/FunMLT/Kana预选一；唯一条件槽S1或RNNT80。最多4正式配置、2 E2E；不同实验组串行，同组保持真实ASR+MT并行。
- R2T2主配置full llama/Q4_K_M+Q8mmproj/160/U1/S0/W16O8，无VAD强reset。日语RNNT固定M1-6/480ms/cache；部署不通不等于语言质量失败。
- 每次输出前冻结hypothesis、source/runtime/model/input/reference/参数/路线。null必需锁阻塞，拒绝latest与静默替换模型。
- 只用已到达PCM，不用原字幕/OCR/未来上下文/云/4090；外部中文不能回写，内部日文草稿不能送Hy。
- 使用本轮成对B0而非挑弱历史基线；两seed不是两个独立数据集。失败/过滤/missing/late保持全分母。
- P3 research_R_memory只用于研究，不代表旧正式R通过。默认2.5GiB使用完整树physical footprint，超预算只生成取舍报告，未经授权不长跑高资源档。
- 取消先封sink，底层worker返回/清理后才释放lease。30秒是单次native调用保护，不是session总时长。
- 15 pending-listen不得自动升human_verified。无法确认语义或时间，写needs_human；AI盲评只能provisional。
- 两天source gate、最多两次明确依赖修复等预算不可重置。缺权限、私有模型、深改框架、收费、资源/政策扩张先报告并请求新授权。

## 每阶段交付

产出真实命令/exit/日志/输入输出hash/环境/预算/判定，给出stage、score_status、stop_reason与下一允许动作。新增测试先RED后GREEN；候选适配先parity后质量，质量前提不成立不做长稳。每个任务完成后做独立review并修复重要问题。

最后填templates中的run、quality-review、decision和BATCH-REPORT。结论必须区分：运行阻塞、有效运行但质量失败、质量有利但成本失败、可研究晋级、完整验收待决。所有候选失败时保留默认，仍可正常结案。

禁止以包内helper的release_eligible、Schema通过、退出0、作者CER/RTFx或模型体积代替真实M1/Chrome验收；本包helper始终release_eligible=false。

<!-- END prompts/EXECUTOR.md -->

---

<!-- BEGIN prompts/REVIEWER.md -->

# 独立审查AI提示词

以独立上下文审查本轮实现和证据。只读，不替实施者修改或补造原始数据；不要再派生审查代理。先读00、08、09、config与本run manifest，再读所审代码diff、真实日志和判定。实施者总结不是证据。

## 必查项

1. 仓库基点、用户未提交内容、独立worktree、当前默认与历史P2收据是否受保护；实际依赖/工件是否等于锁，不以main/latest替代。
2. 是否遵守3主+1条件、2E2E预算；secondary失败后有没有偷偷换下一个；单例worker在清理期间是否仍busy。
3. R2T2是否完整音频链、第二返回值、U1隐藏、W16O8真实滚窗、外部累计前缀；日语123M是否真RNNT和cache，而非CTC/离线重算替代。
4. exact frontend/模型/后端/精度/转换来源和数值parity是否有证据；同名包/语言参数不能证明同权重或强制日语。
5. accepted/consumed/frontier、padding、VAD released水位与pending尾；有没有未来音频、参考端点、错当word timestamp、重复或丢包。
6. JA commit与中文发布是否分别不可回写；S1是否只读稳定整句，是否与S0重复翻译；source范围与真实200ms流映射。
7. 同轮B0、两个seed、固定分母、restored/lost/关键错/保护项、pending-listen；AI评分是否冒充人工，P95是否剔除了missing却省略分母。
8. 真1×与Chrome DOM、校准误差、可读时长、按钮/音频起点区别；offline RTF不能替代用户延迟。
9. 资源完整树同驻留，2.5GiB和R_memory同surface热态footprint、RSS仅辅助不混；系统GPU并非专属，多个峰值不相加；12GiB/压力/积压硬停真实有效。
10. 原正式R延迟与新research_R_memory拆开；内存探索成功不能自己授权默认切换。holdout是否真正独立，生命周期/60分钟/权限是否真测。

## 输出格式

- 已核验范围与未判断范围（每项附原因）。
- Critical：导致数据/安全/实验合同错误，必须修。
- Important：阻止可信判断/可执行性的问题，完成前修。
- Minor：不影响核心判断的改进。
- 每项给文件或证据路径、行/事件/单位ID、具体问题、影响和最小修正。
- 验证命令与实际结果，不只说“测试看起来合理”。
- Verdict：可交付研究结论／需修复／证据不足。对尚未实际运行的模型禁止给性能成功结论。

如果审查对象只是本P3文档包，不能把未来P3代码未实现当缺陷；应判断规格是否完整、工具是否兑现声明、未实测边界是否诚实。包内工具不是发布判定器。

<!-- END prompts/REVIEWER.md -->

---

<!-- BEGIN prompts/RESUME.md -->

# 中断后恢复提示词

先读README、10章和最后一个run的manifest/receipts/decision，不从零搜索、不重跑已通过步骤、不把旧失败覆盖成新成功。

1. 检查用户最新指令及授权是否变化；检查git状态和source/model/runtime/input/reference锁。
2. 找出本run拥有的服务与native worker，确认是否真的退出；清理中保持busy。不要按进程名批量杀进程，不启动第二个模型掩盖僵住任务。
3. 验证已完成步骤的输入fingerprint和输出hash。只有passed且完全匹配才复用，注明reused_receipt；其余另建attempt。
4. 读取已用工程/运行/墙钟预算、依赖修复次数、3+1槽和2E2E槽。恢复不重置预算。
5. 确认当时实验参数：R2T2 W16O8/U1/S0、或RNNT480/cache、所选secondary。任何改轴需新experiment_id；S1/80ms全局只一个。
6. 将缺材料、权限、人工语义、校准、模型runtime分别写blocked_data/permission/runtime或needs_human，不把未跑记0分。
7. 只执行下一项获准且门已满足的任务。发现合同/Schema/日志矛盾先停并解释，不选方便口径继续。

恢复汇报应包含：从哪个receipt恢复、复用了什么、没有复用什么及原因、剩余预算、当前阻塞、下一条准确命令。不得声称恢复过程中完成了新的M1性能验证，除非确有新run证据。

<!-- END prompts/RESUME.md -->
