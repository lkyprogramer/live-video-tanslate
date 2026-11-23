核查日期：2026 年 11 月 23 日。项目固定在 `e06ceea2f6f029e08a507454b75164c78c96b2e3`。硬件按项目记录：M1 Pro，10 核 CPU、16 核 GPU、32 GiB 统一内存，macOS 26.7.2。本次为源码、归档实验和公开实现的交叉审查，没有在你的 Mac 上新增运行测试，也没有改动项目。

本轮检索覆盖 GitHub 上游代码、发行与 issue，Hugging Face 模型卡及配置，论文、日文开发者记录和 Reddit 一手使用反馈。只把可读到的源码、模型与同口径实验作为技术判断依据；论坛试听和作者在 M4/M5 上的成绩仅作候选线索。没有找到能直接替代你项目数据的“M1 Pro、日语影视、1× 播放、ASR 与 MT 同时运行”的外部完整基准。这不是对整个互联网的穷尽证明。

# 结论

**不能认定已经达到本机识别上限；现有证据反而显示，系统还存在可定位的软件开销、分段等待和验收盲点。** 但这不等于继续优化必然能同时大幅改善低占用、实时性和准确率。

应分开判断三件事：

| 目标 | 当前判断 | 下一步最有价值的方向 |
| - | - | - |
| 相同模型输出更快、更省 | 没到实现上限 | 消除重复预处理，测量内存分配与同步，替换已证明有质量价值的模型后端 |
| 中文出现得更及时、更完整 | 没到产品上限 | 音频接收与推理解耦，保留声学上下文的分段与提交，真实 DOM 可见时延和覆盖率 |
| 相同短音频识别得更正确 | 已有明显模型与上下文约束，不能靠内核提速直接解决 | 日语领域适配、更好的声学模型、受控上下文或新的低延迟后训练 |

当前低占用基线可以继续保留；新增 HQ 路线值得完善，但尚不适合宣布为 M1 Pro 的最终最优解。最值得押注的是 **已经在你的样本中显示出更好识别质量的 NeMo 619M 的原生后端**，以及 **当前 Qwen 路径上可归因的端点、调度和预处理改造**。不是继续无差别替换十几个模型。

# 最新提交真正改变了什么

最新代码的两条本地路径不同：

| 路径 | ASR | 翻译 | 仓库配置状态 |
| - | - | - | - |
| `reazon-hy` | ReazonSpeech k2-v2 INT8，sherpa CPU 两线程 | Hy-MT2 1.8B 4-bit，MLX | 示例默认路径 |
| `m1-pro-hq` | Qwen3-ASR 1.7B 8-bit，mlx-audio | Qwen3 4B Instruct 2507 4-bit，MLX | 已实现，示例中仍为 disabled，需本地显式启用 |

HQ 的两个 MLX 模型在同一个常驻执行器线程构建和运行，Silero VAD 在 CPU 执行器运行。ASR 加入生成长度限制、重复惩罚及重复文本收缩，MT 加入更强的中文格式提示与一次假名修复生成。不能仅凭拉取 main 推断你本地已切换 HQ。

关键提交为 [f2f9534 高质量路径实现](https://github.com/lkyprogramer/live-video-tanslate/commit/f2f9534231848fee45345b5c20a0e487c67ab925)、[00f8109 翻译对照实验](https://github.com/lkyprogramer/live-video-tanslate/commit/00f81095ce5d7f55575a76a07ddbee4329cfb1d1)、[e06ceea 实时和浏览器验证归档](https://github.com/lkyprogramer/live-video-tanslate/commit/e06ceea2f6f029e08a507454b75164c78c96b2e3)。配置见 [profiles.example.toml](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/engine/config/profiles.example.toml)，实现见 [local_pipeline.py](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/engine/src/subtitle_service/local_pipeline.py)。

这轮确实出现了质量信号，而不只是测试数量增加：

| ASR | D0 聚合 CER | D1 日剧 CER | 热态解码 P95 |
| - | -: | -: | -: |
| Reazon k2-v2 INT8 | 21.54% | 75.75% | 本轮表中未给同口径数字 |
| Qwen3-ASR 1.7B 8-bit | 22.08% | 36.23% | 0.866 秒 |
| ReazonSpeech NeMo 619M CPU FP32 | 15.46% | 28.74% | 3.063 秒 |

- D1 为日剧 45 秒片段，SRT 参考 334 个规范化字符。上述 CER 是离线比较；SRT 并非逐字完整听写，不能把 CER 直接解释成漏译比例或整体观影正确率。D0 中 `csj-task` 的参考缺少第二说话人，不能机械地按 D0 聚合 CER 排名。解码 P95 混合 D0 的 88 段与 D1 的 3 段，不是 Chrome 延迟。

- 34 单元的人工日文参考输入翻译，即 Oracle MT：Qwen 4B 为 32/34，Hy 1.8B 为 28/34。它说明翻译端有改善空间，不等于完整实时系统达到 32/34。

- HQ 的 1× WS 回放已能跑完所测短片、静音和一次断开重连。wmf-04 修补后的切片结束到服务端输出 P95 为 3.134 秒；日剧为 4.188 秒，但日剧只有 3 条输出，这个百分位实际上取三条中的最大值。

质量评审仍以 AI 判定为主，D0 包含不完整说话人参考，样本反复用于调优；产品的提示、重复惩罚和运行时版本也不完全等于离线 MT 对照。应保留这些改善信号，但不能宣称跨场景、统计充分的胜出。

来源：[Tier2 ASR 报告](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/tier2-asr-smart-turn-results-2026-11-23.md)、[Tier2B MT 报告](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/tier2b-mt-results-2026-11-23.md)、[HQ 验证报告](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/m1-pro-hq-verification-2026-11-23.md)。

# 为什么现在还不能宣称达到实时上限

## 切片结束后的低延迟掩盖了片内等待

D1 的 Tier2 离线冻结切片在报告中明确列为 1.16–15.29、15.37–24.09、24.17–44.99 秒，长度约 14.13、8.72、20.82 秒。另一个 HQ 1× 回放归档只产生 3 条中文；文本长度分别为 82、48、118 个字符，含标点。后者摘要本身没有记录每条音频边界，不能把离线边界冒充为实时 trace。产品 `show()` 每次设置最多 4 秒后清除，后续字幕还可能提前覆盖。

因此，所谓切片结束后 P95 4.19 秒，不能回答前半段台词等待了多久、是否完整出现、是否有足够阅读时间。脚本实际公式为 `server_ws_sent − session_ready − end_sample/16000`，既不是浏览器收到时间，也不是语义句末到 DOM 可见时间。即使把推理时间降为零，当前 final-only 路径仍须等切片结束才提交内容；如果 1× 回放重现这些长边界，片内等待依然以十多秒计。长字幕生成后再按标点分成几行，能够改善排版，但不能追回等待时间。

来源：[Tier2 D1 切片边界](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/tier2-asr-smart-turn-results-2026-11-23.md)、[HQ 实时输出归档](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/experiments/m1-pro-hq-live-2026-11-23/live_summary.json)、[计时公式与百分位](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/p0/tier2_mt/verify_m1_pro_hq_live.py#L139-L177)、[字幕显示实现](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/extension/src/content.ts)。

## max speech duration 4 秒不是硬上限

产品设置 `max_speech_duration=4`，但精确依赖 sherpa-onnx 2.0.1 的 C++ 实现并不在第 4 秒直接裁切：超出长度后改为更短的静音要求和更高的判停阈值，仍等待 VAD 状态判停。一次 `AcceptWaveform()` 还会把包含的多个窗口用 OR 汇总成有话状态。

这解释了参数为 4 秒而实际出现 20 秒片段；它是可核实的库语义，不是 M1 算不动。早期项目的 ASR 选择说明正确描述过这个软限制，后来的升级文档却把它称为强制硬切，应统一术语。

不能简单改成每 3 秒 flush：既有 A1b、SmartTurn S2 已展示边界变化会损伤质量。探索应该让音频历史连续保留，在低能量或可信边界提交、必要时带重叠并去重，或者选择真正维护声学缓存的流式模型；每种策略都要用中文正确覆盖率验收。

来源：[精确 sherpa C++ 实现](https://github.com/k2-fsa/sherpa-onnx/blob/af2d8d81dbfa61c4cea4c568da78f3982edca005/sherpa-onnx/csrc/voice-activity-detector.cc#L50-L125)、[原项目对该参数的说明](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/p0-asr-selection.md)。

## 收音消费者仍会等待完整 ASR 任务

HQ 的 `accept_pcm()` 先等待 CPU VAD，再向同一个 MLX 执行器提交 ASR，并等待它完全返回。在这个消费者等待期间，不继续喂后续音频给 VAD；如果 MLX 执行器正在翻译，还要先等 MT。入口 PCM 队列和下游 MT 队列有界，因此持续高密度语音、慢字幕或修复生成存在积压风险。当前短测没有证明发生了溢出，不能把静态风险写成已复现故障。

优先解耦音频接收、VAD 和推理调度，而不是先把 GPU 线程数翻倍。保留每个模型的固定所有者，按音频秒数限制队列，记录最老未处理音频年龄；在单一 GPU 调度器上先尝试明确的任务优先级和取消点。若之后尝试独立线程或进程，必须重新测试数值一致性、内存、争用和长尾。

现有 `asr_start` 在提交执行器之前记录，包含等待；`mt_start` 在线程内记录，不包含此前排队。两者不可直接当作内核耗时比较，也不能把各阶段 P95 相加成端到端 P95。

生命周期还需补强压测：断开重连测试发送 8 秒后关闭连接，但没有断言当时 GPU 正在推理；服务端会等待被保护的任务结束后释放会话占用，而扩展 seek 遇到 busy 只在 500 ms 后重试一次。手动停止、seek 的丢弃语义与播放 ended 的刷尾语义也不同。这是应主动构造的待测竞态，不是已复现的用户故障。

来源：[LocalSession 调度与 trace 位置](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/engine/src/subtitle_service/local_pipeline.py#L279-L366)。

## 浏览器验收存在可修复的证据缺口

`verify_chrome_1080p_4k.mjs` 在 Shadow DOM 中查 `div`，而产品字幕节点实际为 `span`。归档 `chrome_e2e_summary.json` 中，1080p、4K 的 `captions` 都是空数组，截图状态也为 false。另有一张 4K 静态截图和 WS 日志，不能据此断言字幕没有显示，也不能把 WS 到达统计当成完整 DOM 可见验收。

产品已经提供带 `session_id/seq/epoch` 的 `dom_visible`、`dom_removed` 诊断。应直接连接这条证据链到音频样本时钟，保存 seek 前后、首条、末尾与清除事件，并对丢字幕、旧 epoch、假通过建立断言。短时 4K 0/1049 掉帧是有价值的观测，但不是 60 分钟功耗、GPU、热降频和字幕可读性的证明。

来源：[错误选择器](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/p0/tier2_mt/verify_chrome_1080p_4k.mjs#L139-L159)、[真实节点与可见事件](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/extension/src/content.ts#L25-L83)、[浏览器归档](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/experiments/m1-pro-hq-browser-2026-11-23/chrome_e2e_summary.json)。

# 可以真正修改的底层位置

| 优先级 | 位置和机制 | 可能改善什么 | 必须守住的边界 |
| - | - | - | - |
| 高 | mlx-audio 重复音频预处理 | 相同输出下减少 CPU 工作与 GPU 同步 | 不变更 mel、prompt、token 或采样语义 |
| 高 | 入口消费和推理调度解耦 | 避免推理阻塞音频分析，降低积压尾延迟 | 有界队列、epoch、取消、EOF，不丢音频 |
| 高 | NeMo 619M 换原生 Metal 后端 | 让已见质量优势的模型更接近实时预算 | 保持 checkpoint，先分离精度与 beam 的影响 |
| 中 | MLX allocator cache 与同步边界 | 减少反复分配和缓存抖动 | 内存水位可控，不把缓存驻留或碎片隐藏掉 |
| 中 | 流式窗口和不可变前缀复用 | 长语音减少重复声学编码及 prefill | 只缓存不会变化的声学区域，正确处理归一化和尾部 |
| 低至中 | sherpa 当前段快照复制 | 减少连续长段的累计内存复制 | 先 profile；保留上游对 current segment API 的语义 |
| 条件性 | CoreML encoder 卸载到 ANE | 减少 GPU 工作，为 MT 留出余量 | 必须证明系统端到端收益，而非只看 Python 进程 RSS |

## 重复预处理是当前版本里可直接验证的开销

精确 `mlx-audio v0.6.7` 的 `_generate_single_chunk()` 调用 `stream_generate()`；后者已经调用 `_preprocess_audio()` 并构建 prompt。返回首个 token 后，前者又为了得到 `prompt_tokens` 再次调用 `_preprocess_audio(audio_chunk)` 和 `_build_prompt()`。这是重复提取音频特征，不是第二次完整 encoder 推理。

最小修改是把第一次计算的 prompt 长度一并返回，而非为统计重新计算。以完全相同输入和生成 token 序列验收，再测 1、2、4、8、20 秒音频的 wall time。收益大小尚未测量；这类改造不能直接修复同音汉字错误。

同一段路径还在 encoder、embedding 和 chunk 结束等位置多次 `mx.clear_cache()`，并有显式 `mx.eval()`。应分别测试 allocator cache 上限与必要的同步边界。`clear_cache` 清的是可回收分配缓存，不等于删除模型权重或 KV；不能为了跑分一次性全部删除、也不能用仅 MLX active memory 宣称总占用很低。

来源：[mlx-audio v0.6.7 精确代码](https://github.com/Neywa-Labs/mlx-audio/blob/204689f56ad2f8cda677eaa3b0d64014bca65bc1/mlx_audio/stt/models/qwen3_asr/qwen3_asr.py#L974-L1098)。

“从底层做”也不等于重做已经存在的优化：这一版本已有 GQA、fast SDPA、按 256 token 扩容的 KV、分块 prefill 和异步下一 token 提交。先测额外的缓存扩容、CPU 同步、GPU dispatch 和 shape 编译尖峰；只有对应部分确实占关键路径，才考虑固定容量、长度桶或编译纯函数。整个流水线改写 Swift 或自写 Metal kernel 并不会自动叠加这些收益。来源：[已有 attention 路径](https://github.com/Neywa-Labs/mlx-audio/blob/204689f56ad2f8cda677eaa3b0d64014bca65bc1/mlx_audio/stt/models/qwen3_asr/qwen3_asr.py#L466-L537)、[KV 实现](https://github.com/Neywa-Labs/mlx-audio/blob/204689f56ad2f8cda677eaa3b0d64014bca65bc1/mlx_audio/lm/models/cache.py#L104-L135)、[生成实现](https://github.com/Neywa-Labs/mlx-audio/blob/204689f56ad2f8cda677eaa3b0d64014bca65bc1/mlx_audio/lm/generate.py#L270-L361)。

## 最值得先验证的后端替换是 speech.cpp 的 NeMo 619M

本轮 Tier2 中，NeMo 619M 在 CPU FP32 下的解码 P50/P95 为 2.073/3.063 秒，RTF 0.4718，进程峰值 RSS 为 5734.28 MiB，约 5.60 GiB。这组时延统计混合 D0 与 D1 的 91 个切片，不是 D1 单独统计，也不是实时中文字幕延迟。

[nyosegawa/speech.cpp](https://github.com/nyosegawa/speech.cpp) 已有该模型的 FastConformer、RNNT、beam search 和 GGUF 权重，不只是未来的转换计划。[专用 Q8_0 权重](https://huggingface.co/sakasegawa/reazonspeech-nemo-v2-GGUF) 约 0.66 GB，F16 约 1.24 GB。Q8 默认 encoder 跑 GPU、小型 RNNT 解码跑 CPU，避免许多小算子频繁往返 GPU；F16 的默认解码设备不同，可单独对照。

作者提供了 M5 上的逐层一致性检查和日语 Common Voice 质量、速度数据，足以证明路径值得试，不足以预测 M1 Pro 的具体倍数。GGUF 布局为 speech.cpp 专用，不能当作普通 llama.cpp 或 wllama 权重加载。

试验先保存原 NeMo FP32 实际解码配置；若有可靠 F32 转换路径，增加原生同精度、同解码策略对照。之后分别比较原生 F16 beam、Q8 beam、Q8 greedy 与 CPU/GPU 解码设备。若直接从 PyTorch FP32 跳到原生 F16，这一步是后端加精度的组合变化，不能把差值全部归给 Metal。先复用当前冻结音频，之后再进入真实 1× Chrome + Qwen MT 对照。这个模型仍是分段离线识别，不会因为 C++ 化就变成原生增量流式。

作者也明确记录长段拼接会漏掉整句，因此不能为了减少调用次数无限加长输入。固定候选 `speech.cpp v0.11.2 / cc16a71`。来源：[FastConformer 实现和限制](https://github.com/nyosegawa/speech.cpp/blob/cc16a71e46ee3324ca07f9df4d90ce43622ae598/docs/models/fastconformer.md)、[本机 NeMo 归档](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/experiments/tier2-asr-2026-11-23/asr_nemo_summary.json)。

## Qwen 的跨块缓存有可借鉴实现但不是免费增量

[moona3k/mlx-qwen3-asr v0.4.4](https://github.com/moona3k/mlx-qwen3-asr/releases/tag/v0.4.4) 复用已经完成的 8 秒 encoder attention windows 及其对应的 decoder 音频 prompt KV，继续重算尚会变化的尾部和文本部分。作者在 M4 Pro、0.6B FP16、10 段 75 秒长音频的滚动识别对照中报告 RTF 从 0.104 到 0.083；这不是当前 1.7B 8-bit 的同机结果，也不是整条字幕链路提升 20%。

项目当前每段独立 `generate()`，即便一段超过 8 秒，单次 final 推理也没有跨次重用机会；短于 8 秒的段更没有完整块可复用。只有引入持续 rolling 后才打开这个实验，并验证 mel 全局幅度归一化、尾部 STFT、窗口滚动和语言头是否使旧状态失效，不能为了凑缓存命中额外等待 8 秒。

该仓库早期的“每个新块直接追加 decoder KV”方案曾把多语误差从离线 9.5% 拉到约 56%，随后改成整窗重喂并仅复用严格不可变区域。这是反例：缓存更多不等于识别更正确，必须先保证模型计算语义。

来源：[基准和失败记录](https://github.com/moona3k/mlx-qwen3-asr/blob/41878a11cf338c59e13edc84bf4cb35f4b0f3ff6/docs/BENCHMARKS.md)、[流式实现](https://github.com/moona3k/mlx-qwen3-asr/blob/41878a11cf338c59e13edc84bf4cb35f4b0f3ff6/mlx_qwen3_asr/streaming.py)。

# 新搜索结果如何进入或退出候选名单

| 方向 | 当前证据 | 本项目的处理 |
| - | - | - |
| ReazonSpeech NeMo 619M 的 speech.cpp 后端 | 同款模型已有 Metal 与专用量化实现，本机 CPU 版本质量有信号 | 优先验证，目标是质量可用性与计算成本，不是宣称新模型 |
| 专用 Qwen MLX 实现和不可变窗口缓存 | 有源码、版本化质量门和失败记录 | 在相同权重与切片上比较，再决定移植哪些优化 |
| Nemotron 3.5 的 NVIDIA NeMo-Speech.cpp 后端 | 原生缓存流式、CPU/Metal 路径存在 | 只作受限后端和提交策略探针；同一多语模型已在本机失败 |
| Confucius4 R2T2 的 Swift MLX 移植 | 有 1.7B 8-bit 实现及 append-only 输出协议 | 继续已有 P3 的小探针；160 ms 是输入块，不是中文字幕延迟保证 |
| 123M 日语 Cache-Aware FastConformer | 模型卡有真实 encoder cache-aware 架构与 80/480 ms lookahead | 与 R2T2 二选一探针；先证明 cache 导出和 M1 运行，留意非商业许可 |
| kodama 日语 streaming-small | 独立日语微调，ONNX 三图；有噪音与流式退化报告 | 低占用备选，不作为高质量主线；与已测 Moonshine 权重区分 |
| Ruby-ASR 1.7B | 同 Qwen 架构的日语微调，字幕版及逐字版，有五域成对评测 | 准确率支线优先于无定量证据的术语微调；先验证主权重与 MLX 输出契约 |
| neosophie Qwen3-ASR 1.7B JA | 有权重，专门训练日语专名和技术词 | 错误分布确实偏术语时再测；没有证据证明日剧整体更好 |
| Haas 和日语 Parakeet | 本机已有相同切片语义对照 | 不再包装成新替换方案；可借鉴 Haas 的受保护词表偏置机制 |
| ANE Qwen encoder | M1 Max 有实际卸载测量，但整条转写墙钟未变 | 只在 GPU 争用已被 trace 证实时提升优先级 |
| TranslateGemma | 离线测试反复生成结束标记，属于协议异常 | 独立排查 tokenizer 与 EOS，再决定是否重新评估质量 |

Nemotron 历史测试使用的正是 `nemotron-3.5-asr-streaming-0.6b`、`ja-JP` 和 560 ms 原生块，不能用“这次才找到支持日语的版本”为由重测。此前首个非空日文 4.84 秒，但首个提交 11.49 秒，说明提交策略和模型质量都需分开归因。NVIDIA 原生运行时的公开高速表主要来自其他硬件和英语旧模型，不能引用为 M1 日语成绩。来源：[项目 Nemotron 记录](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/p0-nemotron.md)、[NVIDIA 原生运行时](https://github.com/NVIDIA/NeMo-Speech.cpp)。

Haas 的 19/19、日语 Parakeet 的 17/18 是两个 seed 下、每轮分母均为 34 的中文语义正确数，不是“19 条全部成功”或 ASR CER。来源：[原优化对照](https://github.com/lkyprogramer/live-video-tanslate/blob/e06ceea2f6f029e08a507454b75164c78c96b2e3/docs/optimization-results.md)。

R2T2 Swift 移植默认约 2.5 GB 权重，M5 Max 的驻留和激活报告远非零成本；每个新块仍重喂滚动窗口，不是完整声学缓存流式。123M 模型卡为 CC-BY-NC 4.0，不能默认成为可商用分发方案。kodama 的模型卡主动报告：流式 CER 比对应离线高 5.38 个百分点，噪音与专名是弱点，其三图 ONNX/ORT 布局也不能直接装入官方 Moonshine 的六图运行时。来源：[R2T2 移植](https://github.com/xocialize/mlx-r2t2-stt-swift)、[123M 模型卡](https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open)、[kodama 模型卡](https://huggingface.co/ayousanz/kodama-ja-streaming-small)、[日语专名 Qwen 权重](https://huggingface.co/neosophie/Qwen3-ASR-1.7B-JA)。

# ANE 和量化能带来什么

## ANE 有实际路径但不应被当成剩余算力的免费开关

[ax0t/Qwen3-ASR-1.7B-CoreML-INT8](https://huggingface.co/ax0t/Qwen3-ASR-1.7B-CoreML-INT8) 的 encoder 已在 M1 Max、macOS 27 测过：8 秒窗口平均 16.3 ms，CPU-only 为 59.4 ms。这里没有拿 16.3 ms 与 MLX GPU encoder 做同口径速度比较。其应用对 43 秒录音的实测是 GPU busy 4.97→4.43 秒，整体转写时间不变；部分内存移到进程外的系统 wired memory。你的 M1 Pro、macOS 26.7.2 和日语输入仍需单独验收。

另一个完整 Qwen ANE 实现 [std_qwen3asr_ane](https://github.com/standard-voice/std_qwen3asr_ane) 在 M5 Max 的同机结果明确显示：ANE 8-bit 比 MLX 8-bit 更慢、完成相同音频所需能量更高。低瞬时功率和低调用进程 CPU 不等于低总能耗。

因此，ANE 应定位为“可能减轻 GPU 争用的 encoder 分流”，而不是预设 ASR 全搬 ANE、MT 独占 GPU 就一定更快。CPU、GPU、ANE 仍共享内存系统；真实设备放置、跨后端传递、短块 padding 和系统服务占用都要计入。

## 混合量化可以试但不能照搬英文结论

已有音频 encoder 8-bit、文本 decoder 4-bit 的 Qwen 转换与对照。然而 [Alkd 的日语消融](https://huggingface.co/Alkd/Qwen3-ASR-1.7B-audio8-text4-mlx) 中，少数长重复循环显著抬高了原始 CER；把 embedding 提到 8-bit 在该卡里仍是预测方案，不是已验证修复。该卡还使用过按参考长度裁剪输出的诊断统计，部署时无法获得参考长度。

本项目当前使用 8-bit，并已加入重复惩罚和长度上限。混合量化必须以真实短句、长段、静音、数字和人名为回归集，报告未裁剪原始输出、截断率和严重误译。不以平均 CER 掩盖少数严重循环，也不把训练或参考文本泄露进运行时规则。

# 准确率的下一步不是一味加快内核

## Ruby-ASR 是一个有依据但尚未在本机验证的提质方向

[Ruby-ASR 1.7B](https://huggingface.co/hshispeech/Ruby-ASR-1.7B) 提供 Qwen3-ASR 的日语字幕版 `subtitle/` 和逐字版 `verbatim/`，主权重保持原架构，另附独立 CTC 发音头。作者在五套日语数据的字符加权评测中报告：基础 Qwen raw CER 10.78%，Ruby 字幕版 8.51%、逐字版 9.10%。这比只给几个专名示例的微调更值得安排小样本验证，但不是日剧 1× 双模型实时成绩。

先测主权重和正确提示，不先移植额外 CTC 分支。Ruby 会输出括号注音，必须分别保留 surface 与 reading，明确下游 MT 输入；注音还会增加生成 token 数，原先的长度上限不能不经验证直接照搬。主权重兼容官方 Qwen 工具不等于已验证 MLX 量化转换，应先做非量化参考与 8-bit 的文本一致性。CTC 读音可以辅助检查错音、数值，却不能凭发音区分两个完全同音词，更不能保证修复所有汉字选择错误。

用独立节目中的 12–20 个已确认失败片段先排除明显回归，只有语义、遗漏和资源同时达到门槛才扩到留出集。这条属于已有调研提及但未见本机结果的路线，不冒充全新发现。来源：[作者代码和评测](https://github.com/hshi-speech/Ruby-ASR-1.7B)。

## 先确定错误发生在哪一层

HQ 归档中仍有把日剧的愤怒或被责骂含义译成“被切断”“能顺利结束”的输出。需要对照对应原始日文 ASR 才能判断错误来自识别还是翻译；`warnings=0`、包含汉字、没有假名都不能证明语义正确。

建立四类成对试验，比继续看总分更有用：

1. 相同模型、相同音频，短上下文与较完整上下文对比。如果后一种明显好，优先解决边界与未来信息，而不是换内核。

2. 原始识别文本与人工正确日文分别送同一个 MT。差值用于识别因果路径，不把两个总分直接相减当成严格的 ASR 错误数量。

3. 同一 checkpoint、同一精度与解码策略，在不同后端运行。先检查 token、文字和关键实体差异，再判断是否可归因于实现。

4. 按节目、说话人独立留出的语料，比较基础 Qwen 与日语领域微调。只有专名、口语、数字等目标类别获得稳定净收益，才考虑更进一步的训练或蒸馏。

如果离线完整上下文仍反复识别错，瓶颈更接近模型或数据；如果离线正确、短块错误，瓶颈更接近流式上下文；如果文本都正确但迟迟上不了屏，才重点看队列、计算和呈现。

日语评测还应区分可接受表记变化与真正实体错误。[ADLIB](https://github.com/holotherapper/adlib) 提供 exact/flexible 术语评分和成对置信区间的可借鉴思路，但其现有开发术语数据不是日剧评测集，不能直接充当项目的主分数。

# 后续实验顺序与停止条件

## 第一阶段修好测量与复现

- 冻结生产模型 revision、运行时、提示、解码参数与输入 PCM SHA。产品声明 MLX 0.32.4、mlx-audio 0.6.7、mlx-lm 0.31.3，而 `uv.lock` 仍有 MLX 0.31.2 且缺少 mlx-audio；Tier2B 使用的 mlx-lm 又是 0.32.2。先核实际安装并统一可复现环境，不用临时环境结果自动证明正式安装可复现。

- 精度按 encoder、decoder、embedding、输出头分别记录。模型名中的“8bit”不是所有组件均为 8-bit；当前 mlx-audio 的量化 predicate 排除 `audio_tower`。不要把换量化布局与换模型一起比较。

- 给每个请求建立收音、VAD、入队、实际开始计算、encoder、prefill、decode、MT、WS、DOM 可见的时间链。MLX 的 GPU 计时需完成事件或明确同步；不要只测 Python 提交时间。

- 主指标为“语义单元结束后 2 秒和 5 秒内首次正确、完整、可读的中文字幕覆盖率”；同时给出最终正确率、遗漏率、严重错译和阅读时长。错误或永远没有显示的单元不得从统计中消失。

- 延迟区分等待音频、排队、计算、呈现；冷启动、预热后、seek 后分别统计。P95 只对正确字幕统计时，必须并列报告其覆盖分母，不能以丢弃困难字幕换取好看的 P95。

- 建立独立节目与说话人的留出集，补全 D0 缺失参考；关键数字、否定、问句和人名做人工复核。34 个反复使用的单元保留为回归，不继续当泛化结论的唯一证据。

- 至少一次 60 分钟真实 1× 播放，分别带 1080p 和 4K 背景；测整机、进程及 GPU/ANE 相关内存与能耗，记录热状态、送帧迟到、掉帧与积压年龄。不要把统一内存重叠的数字直接相加。

这是测试设计，不要求先重写框架。端到端评价应包括切分、运行时和输出修订成本，相关研究也明确区分首次稳定译文与模型吞吐。[低延迟端到端评价框架](https://arxiv.org/abs/2308.03415)、[计算感知时延的评测陷阱](https://aclanthology.org/2025.findings-naacl.393/)。

## 第二阶段只做三组高信息量实验

| 实验 | 冻结什么 | 只改变什么 | 继续条件 |
| - | - | - | - |
| 同模型实现开销 | HQ 权重、切片、prompt、token 输出 | 重复预处理消除；再单独测缓存水位 | 数值/语义回归通过，真实时延或能耗有稳定净收益 |
| 高质量模型后端 | NeMo checkpoint、切片、MT | F16/Q8、beam/greedy、CPU/GPU 分别消融 | 保留质量优势，同时达到该配置的实时和资源预算 |
| 长段及时提交 | 输入音频、模型和最终质量门 | 接收解耦、上下文保留、提交边界 | 正确且可读的 5 秒覆盖提高，漏句/严重错译不增加 |

对延迟和资源做交错 A/B 多轮；预先写明门槛，而不是看完结果再挑指标。可把端到端 P95 降低 15% 或 5 秒正确覆盖提高 5 个百分点设为初始“值得继续”门槛，但它们只是工程筛选门，不是预期收益或统计显著性保证。若区间重叠、质量不确定，就补数据或停止，不宣布胜出。

始终分别保留低占用档与 HQ 档。旧的 2.5 GiB 目标是产品预算，不是 32 GiB 的物理容量；放宽预算后出现的质量提升不能算同成本优化。HQ 的预算也应包含 Chrome 和系统服务，不能仅以 Python RSS 为准。

## 第三阶段再选一个高风险方向

前两阶段有清晰结果后，从 R2T2 后训练路径、123M 原生缓存模型、日语领域适配、ANE encoder 分流中只选一个。每条都需要一个明确的新假设；若没有，就保留现状。

123M 日语模型中的 `M1-6.nemo` 是作者模型编号，不是已经为 Apple M1 优化的证据；其 480/80 ms 是右侧声学上下文，不是首条中文承诺。先用 CPU 参考验证跨块 encoder/RNNT 状态，再决定移植 ONNX。R2T2 则可以验证 append-only 文本，但 Swift 端仍以音频窗重喂，不能因为输入间隔为 160 ms 就宣称解决了重复声学编码。前者受非商业许可约束，后者还需分别核对移植代码与模型权重许可。

先不要重新开展已失败的 SmartTurn 默认替换、无条件增大窗口、同模型高频重复翻译草稿、通用历史拼接、Haas/Parakeet 再排名。重新进入候选必须有新的 checkpoint、已验证的实现缺陷修复，或新的受控分段/提交策略。

TranslateGemma 是例外：其重复结束符属于需要先隔离的协议问题，不应与正常完成生成的模型直接比较速度。先验证模板、EOS、真实输出 token 数和 finish reason；如果修复后仍不赢，就正式关闭这条路线。Seed-X 的 BOS 循环不能简单当 EOS 处理；PLaMo 的自定义代码加载受阻应归为未评估，而不是质量失败，且不能绕过现有执行策略。

更具体地，所查 TranslateGemma 4B MLX 快照没有显式补充模型的 turn-end 停止 ID，别的尺寸已有上游同类重复报告。它支持“先查有效 EOS 集”这一假设，尚不能证明本机故障必定由此引起；禁止从别的模型硬抄 token ID 作为修复。先记录实际加载 revision、tokenizer 终止集合、未清洗输出 IDs、第一次有效译文和 finish reason，再比较修正前后。来源：[所查量化快照](https://huggingface.co/mlx-community/translategemma-4b-it-4bit/tree/5788ec08c047f3f2e17808101b8d9566ac930d58)、[上游类似问题](https://huggingface.co/google/translategemma-27b-it/discussions/3)。

# 最终建议

下一轮不应该把目标写成“继续搜索一个更强模型”，而应写成：**在独立留出集上，提高正确且可读的中文字幕按时覆盖率，并能说明收益究竟来自哪里。**

最优先的投入顺序是：真实端到端测量和安装复现 → HQ 的重复预处理与音频消费解耦 → NeMo 619M 原生后端 → 保留上下文的及时提交。ANE、量化和领域微调在各自瓶颈被证实后进入。

这条路线仍可能带来明显的使用体验改善，尤其是长段等待和高质量模型的可用性；纯粹清理内核或 Python 开销更可能是增量收益，不能承诺准确率同步跃升。等到相同语义质量下的计算路径、实时策略和长期资源预算都经过成对测量，才有依据说“在当前约束下，继续优化的回报已经很低”。现在还没到那个证据阶段。
