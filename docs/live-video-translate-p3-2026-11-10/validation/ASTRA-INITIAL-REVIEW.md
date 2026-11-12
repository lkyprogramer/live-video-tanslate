# Astra Initial Review

结论：两条主线有真实的一手来源，值得保留为有界可行性探索；原包尚不能直接作为可靠执行合同。需要先补齐可读 DOM、R2T2 驱动与无时标提交语义、NeMo 原始 PCM 前端，以及暂定评分与 parity 的执行边界。这里没有发现足以证明任一新模型已在 M1 可用或能超过 Reazon+Hy 的证据。

## Scope and Evidence

- 只读评审输入：`original/` 中 52 文件稳定快照；项目基点 `77e24c3e490a11721041a40ad4bd203f51f84039`。位置均相对该快照，修订目标为 `docs/live-video-translate-p3-2026-11-10/`。
- 实际阅读：P3 主规格、配置、schema、辅助脚本、提示词和研究附录；现有 P2 的评分/提交器及 B0–B4、清理报告相关部分。没有读取封存 `test/` 或音频素材。
- 本轮独立获取官方 R2T2 pin 源码、官方论文、123M 模型卡、作者源码/公开 M1-6 hparams、NeMo v2.5.3 frontend 与 streaming buffer 源码。作者仓库当前核验 commit 为 `cc1cdf12a3133b22349c520e5b81f0334a01e5ad`；公开 hparams 不是下载后的 `.nemo` 内部 config，二者等价性仍待实施核验。
- 本评审未运行模型、编译/导出、安装依赖、下载权重、复听或产品测试；没有把原包 `validation/` 中自称的审查/测试结果当成本评审的实测。仅在此证据目录写报告。

## Findings

### P1-1 — 可读 DOM 的停留门槛丢失，会把瞬闪字幕计成合格覆盖

**位置：** `docs/07-observability-and-scoring.md:17`、`docs/08-gates-and-stop-rules.md:33`、`config/evaluation-contract.json:43`。

P3 只要求实际 DOM 可见且表达充分，未继承 P2 的连续可见时长。旧 `docs/live-video-translate-p2-2026-11-07/docs/07-telemetry.md:23`、`docs/08-scoring-and-acceptance.md:55` 明确规定：每个必要字幕片段连续可见至少 `max(600ms, 非标点字数 / 12字每秒)`；缺结束证据、遮挡、瞬闪不能通过。现有 `p0/p2/scoring.py:28–33` 已实现停留门槛。

**触发与影响：** 一个正确中文片段只闪现数十毫秒，或被后一条迅速覆盖，按新文字仍可能记为 5 秒正确覆盖和低 P95，直接放宽既有产品验收。

**最小修法：** 改文档和数值合同，复用旧可读性门。保存 visible→hidden/removed 与遮挡证据；多字幕逐必要片段判定。达到停留门后，延迟回溯该连续区间首次可见时刻，不能把等待停留的时长加进延迟。WS 与可读 DOM 分数继续分列。

### P1-2 — R2T2 的 160/U1 不足以冻结可复现驱动，语言默认与 token-cap 规则会直接改变实验

**位置：** `docs/04-r2t2-mac-spike.md:5–7,31,48–50`、`docs/09-runtime-integration.md:81`、`config/experiment-registry.json:60–79`。

官方 `example_llama.py:80` 的语言默认是 **Chinese**，并非日语或自动检测；现有 registry 没有 R2T2 language。官方驱动首块 320ms 也不是 U1 自动提供：`example.py:104–110,125–135` 显式设置 `unfixed_chunk_num=0`，首轮将 state chunk 改成 320ms，之后改回 160ms。`no_reset` 首次裁 49 个 chunk 的硬编码依赖这套调度。[官方 llama 入口](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/example_llama.py#L80)、[官方驱动](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/example.py#L104)。

驱动故意使用很小的每步生成预算：160ms step +160ms lookahead 下，首轮 `max_new_tokens=4`；有新增定稿后下一步 2；无新增则加 1，封顶 4。`rollback_punctuation` 默认 false。脚本中的 `total_new_asr_tokens` 未更新，相关汉字检测不能被当成有效调度依据。相反，`max_new_tokens=None` 可落到 llama wrapper 的 4096 默认值。P3 将 `token_cap` 一律视异常终止，却没有区分正常“本步预算用尽，等待下一音频块”和会话级上限/无进展故障。[生成预算](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/example.py#L118)、[llama adapter](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/model.py#L121)。

**触发与影响：** 执行者按默认示例加载会强制中文；按单一 160ms state 驱动会使首次滚窗文本裁切不匹配；将每步 length cap 视为异常会拒绝正常流式进展；走 None 分支则可能放大每步工作量。

**最小修法：** 改文档/registry，在 G0 前明确锁定 `language=Japanese`、空 context、首块 320ms/后续 160ms、`unfixed_chunk_num=0`、U1、`rollback_punctuation=false`、明确且有界的 step token 调度。将 step budget exhaustion 与 session/global cap、超时、持续无进展分开。上游官方 driver 调用的是 standard streaming，W16O8 是本项目组合，不能标成已由官方整套验证。

### P1-3 — R2T2 无词时标，旧 StreamCommitter 所需 frontier/pending 语义不能靠累计文本推导

**位置：** `docs/09-runtime-integration.md:43–53,73–95`、`docs/02-experiment-design.md:19`、`docs/04-r2t2-mac-spike.md:42`。

上游明确无 timestamps；`no_reset` 第二返回值是 session 累计 `last_fixed_text`，滚窗只裁内部 `chunk_text`。现有 `p0/p2/b3_protocol.py:37–59` 只消费已提供的 piece 区间、frontier 和 pending 起点，不负责从无时标日文建立它们。P3 “无法安全归属时等待”尚未给出可终止的映射规则：把 consumed 当词结束 frontier 会错归属；把隐藏 U1 尾部起点长期视作未知最早位置，会使端点一直等到 EOF。[上游 no_reset 返回/维护逻辑](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2/r2t2_asr.py#L752)。

**最小修法：** 预登记一个明确的 `S0_UNTIMED_VAD_SNAPSHOT` 子策略，不直接套旧 StreamCommitter：真实 VAD release 登记水位，等 native 首次实际消费到该水位的已完成 update，再取累计稳定 prefix 相对已送 MT 水位的新后缀；空后缀记 empty。后来才稳定的 U1 尾字留给下一 release/EOF，真实计入延迟或漏译，不用虚构 pending 时标锁死端点。每个 delta 保留其生成时真实输入窗口包络，聚合 span 为包络并集并标 `causal_input_envelope`；没有窄范围证明时可保守覆盖 `[0,consumed]`，不叫词级声学对齐。语义延迟仅用独立冻结参考与 DOM。

此策略改变 MT 分组，必须在首次正式输出前明示；与 B0 是完整组合的产品比较，不能将收益纯归因 ASR。基础 Qwen 机制控制须用同策略。无法证明因果包络时用现有 `blocked_runtime` 加明确 reason，无需新增状态枚举。跨 VAD、后置否定、空 release、延迟稳定尾字、滚窗、EOF 应有无模型反例。

### P2-4 — 已知整块 EOF 行为没有冻结，会导致尾 token 被静默丢失或擅自晋升

**位置：** `docs/04-r2t2-mac-spike.md:33,50`、`docs/09-runtime-integration.md:81`。

固定 pin 的 `finish_streaming_transcribe_no_reset` 在 `state.buffer` 为空时直接返回 `last_fixed_text`；只有存在短尾时才再次推理并追加。因此恰好结束在整块边界的 U1 隐藏尾不会自动解锁，不能泛称 finish 一定完成全部尾文。[源码 807–810](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2/r2t2_asr.py#L807)。

**最小修法：** 首轮保留 pin 行为，登记 `withheld_tail`，未释放语义按 missing/partial 计入完整分母，禁止把 provisional 直接转 final。对整块 EOF、短尾 EOF、无新增文本与重复 finish 固定反例。若将来修 EOF，必须先登记具体有证据的微修及行为差异，不能看正式分数后补救重跑。这个限制不能被误写为模型质量或 runtime 已失败的实测结论。

### P2-5 — 123M 的作者脚本证明特征缓存模拟，不证明原始 PCM 的因果流前端

**位置：** `docs/05-japanese-cache-aware.md:16,25–30,35–39`。

作者入口先 `append_audio_file` 整段，再迭代 feature chunks；NeMo v2.5.3 对整段音频先调用 preprocessor，随后才做 encoder cache 步进。该版本 `FilterbankFeatures` 默认 `exact_pad=False`、STFT `center=True`，预加重还依赖上一 PCM 样本。每 200ms 包独立做同名 frontend 会在边界重置/补零；提前整段提特征则可能把未到达的半窗信息带入所谓在线块。[作者入口](https://github.com/yoinncyan/my-interspeech-2026/blob/cc1cdf12a3133b22349c520e5b81f0334a01e5ad/scripts/infer_streaming.py#L386)、[NeMo buffer](https://github.com/NVIDIA-NeMo/NeMo/blob/v2.5.3/nemo/collections/asr/parts/utils/streaming_utils.py#L1671)、[NeMo STFT](https://github.com/NVIDIA-NeMo/NeMo/blob/v2.5.3/nemo/collections/asr/parts/preprocessing/features.py#L386)。

公开 M1-6 hparams 为 `normalize=NA`、25ms window/10ms hop/FFT512、8x causal downsampling、chunked_limited attention；目前**没有该公开配置存在全段归一化泄漏的证据**，不得泛化指控。实际 `.nemo` 配置尚未下载核验。[公开 M1-6 hparams](https://github.com/yoinncyan/my-interspeech-2026/blob/cc1cdf12a3133b22349c520e5b81f0334a01e5ad/logs/M1-6/FastConformer-Hybrid-Transducer-CTC-BPE-Streaming/2026-02-10_21-58-56/hparams.yaml#L114)。

**最小修法：** 补显式 raw-PCM frontend gate：保留跨包 PCM/预加重状态和所需半窗，整个 frame 支持域已到达才放行；EOF 补零另记，不扩大真实水位。增加“相同已到达前缀、不同未来尾部”的已释放 feature/token 不变反例，记录 feature-ready watermark。已要求的 frontend parity/cache 清零反事实应保留；无须再造通用音频框架。

### P2-6 — R2T2 parity 的参照对象与接受准则未冻结，量化差异可能被误归类为部署错误

**位置：** `docs/04-r2t2-mac-spike.md:32,54`、`config/runtime-artifact-lock.json:78`。

Q4 decoder+Q8 mmproj 与 vLLM/hybrid/非量化输出不保证逐 token 相等；原包同时引用已知 full llama quiet-onset 差异，却只笼统写逐项比较 token/定稿前缀。没有明确参考 backend、权重、前端、驱动/状态策略与阈值时，既可能把合法量化差异判 `blocked_runtime`，也可能以“有日文”草率通过。

**最小修法：** 冻结 reference 的 backend/artifacts/input/frontend/decode schedule，以及实际比较层级和容差；区分同工件跨平台回归、非量化诊断和最终语义质量。没有可比参照则 parity 未证/`blocked_data`，不虚构成功，也不自动把 Q4 对 FP16 差异归类为 Mac 实现错误。该检查留在既有 16 小时内，不借此展开量化矩阵。

### P2-7 — 无人工判定即停止 score 与允许 AI 暂定研究晋级互相冲突

**位置：** `docs/09-runtime-integration.md:132` 对比 `docs/03-phase-tasks.md:107–110`、`docs/07-observability-and-scoring.md:46`、`docs/10-ai-execution-and-recovery.md:58`。

CLI 规格写“没有人工判定则导出待审包并 needs_human”，其余章节允许冻结 AI 参考、独立文本复核后的 provisional 研究晋级。15 项仍待复听时，按前者实现会停掉后者明确允许的有界 1× 工程探索。

**最小修法：** 分离 `score_status`、reference truth 和 `screen_decision`。没有独立文本判定先 unscored+待审包；有冻结 AI 参考和独立对账时可 provisional 研究筛选，保守/宽松及争议时标并列。人工真值、正式尾延迟/发布资格保持未通过。根代理已独立确认并计划修复此项。

### P2-8 — RSS 探索主指标与 footprint 资格使用不同口径，进程拓扑变化可能制造内存收益

**位置：** `docs/07-observability-and-scoring.md:34–36`、`docs/08-gates-and-stop-rules.md:13–25`、`config/evaluation-contract.json:75–85`。

原稿承认 RSS 求和可重复计算共享页，却将其作为跨 Python/原生子进程候选的 20% 内存探索主指标；footprint 只设 2.5GiB 绝对门。由此可能出现 RSS 改善 20%、footprint 仍在门内但相对 B0 退化，仍被判内存研究胜出。进程树是否包括 Chrome、何谓热态、是否同一 surface 也尚未唯一化。

**最小修法与评审意见：** 同意根代理提议：在本轮尚未输出前，将新的 `research_R_memory` 主指标冻结为同 surface、同热态区间的完整目标进程集合 physical-footprint 采样峰值，RSS 留辅助，不修改 P2 已冻结 `R_latency`。WS 为 server/native/Hy 及其可归属子进程；Chrome 另包含本 run 隔离浏览器进程集合。播放 only 单列，不能做峰值相减后宣称模型独占物理内存。声明这是同口径各进程 footprint 的汇总观测值，不称系统唯一页精确去重。逐采样时刻汇总后取峰值，不相加组件独立峰值；缺必需进程样本、缺 footprint 或无法解释进程归属时 inconclusive，不回退 RSS。

本次旧证据核对：`docs/p2-b0-results-2026-11-08.md:48–59` 已区分 Chrome playback 与无 Chrome PCM whole_run，完整 Chrome 峰值约 2168.38MiB。它是方法参照，不能与 P3 新的其他采样阶段直接算百分比。该调整属于修订尚未执行的研究合同，不能回填为 P2 正式 R 的新胜负结论。

## Preserved Design

- full llama 是真实音频编码→投影→文字解码路径；源码存在 Metal 支持不等于 arm64 构建/算子/量化正确。保留有界 source/import/load/parity gate，不切 hybrid MPS、不深改 encoder cache。
- R2T2 固定源码的第二返回值已累计全 session；外部只做 suffix 差分和单调检查，不能再按窗口拼累计全文。16/8 滚窗、清 KV 重算的实测成本必须保留，不把 160ms 当中文延迟。
- 123M 模型卡和公开配置支持日语 hybrid RNNT/CTC、cache-aware 与多右文条件；480ms 优先、RNNT 不被 CTC 替代、缓存图/前端/parity 先行是合理设计。没有本机导出/性能证据。
- 同轮 B0、双 seed 不冒充独立样本、34 全分母、P2 失败不改写、B4 0/3 合规失败与未跑 34 项分开，均应保留。
- 3 主配置+1 条件配置、最多 2 E2E、基础控制不能争取产品胜负、`research_R_memory` 不冒充旧正式 R 或默认替换资格，均应保留。基础控制与能力诊断的成本需显式包含在既有路线预算内，避免只记账却无上限。
- footprint 缺失为 inconclusive、ASR+Hy 联跑、取消后真实 worker 返回才释放 lease、公开/私有证据分层，方向正确。完整进程集合、热态统计区间、Chrome 单列/包含关系还需在最终修订中唯一化；不能将组件峰值相加。

## Minimal Follow-up Verification

1. 根代理完成文档修订后，核对章节、registry/evaluation-contract、模板、提示词与机械合并正文一致，重建清单而非手工维持两份权威文本。
2. 本轮仅运行文档 helper 的相关合成反例/现有离线测试和 schema/链接/清单核验；不启动 P3 模型执行。
3. 未来实施优先用无模型 stub 固定 R2T2 首块/步预算/整块 EOF/累计前缀与 untimed-S0，再做少量真实能力探针。NeMo 必须先证原始 PCM 前端支持域与 cache，而不是直接把作者整段特征模拟脚本当实时 runner。
4. 本报告未亲自运行任何产品/模型测试。根代理实际检查结果应单独记录；修订后的最终复核不能继承原包自称的独立审查通过状态。
