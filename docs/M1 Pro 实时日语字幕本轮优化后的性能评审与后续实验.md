结论：本轮已经跨过明显的软件效率拐点，但还不能称为 M1 Pro 的性能或识别质量天花板。应保留 CPU NeMo 加 Metal 翻译的现用路线，下一轮先保证音频完整和同模型识别保真，再验证两个窄范围底层候选：纯 CPU 解码线程池、MT 短预填矩阵内核。继续全面重写引擎的收益依据不足。

评审固定于 2026 年 11 月 23 日的 `3e506514a6d36780100dc2e8fa1de1a2897b1b51`，相对上一轮 `e06ceea2` 新增六次提交；收尾时远端 main 仍是这个版本。环境按仓库记录为 M1 Pro 10 核 CPU、16 核 GPU、32 GiB、macOS 26.7.2。下文性能数值来自该机归档；切分器的确定性复现是在独立环境运行的纯逻辑探针，不是重新执行 M1 模型基准。没有改动产品代码或现用环境。

# 本轮实际取得的收益

| 对照项目 | 归档结果 | 能支持的结论 |
| - | - | - |
| 同批 91 个冻结片段的 Qwen ASR 加 MT 计算墙钟 | 106.4501 → 71.5701 秒，耗时降低 32.77%，约 1.49 倍速度 | 固定提示 KV、调度与算图清理确实有收益；不是端到端字幕快了 32.77% 的证明 |
| Qwen ASR A0 → A3 | 38.8567 → 37.5830 秒，降低 3.28%；90/91 条同文 | 容易消除的重复工作已经处理；继续只改预处理大概率收益有限 |
| Qwen 4B MT 热态 M0 → M1 | P50 569.6 → 324.6 ms；首 token P50 396.0 → 150.5 ms | 本轮主要推理收益来自避免每句重算固定系统提示 |
| 旧 HQ 与新 HQ 的同一 D1 实时回放 | 服务端分段末延迟 P95 4.188 → 1.581 秒，字幕从 3 条到 13 条 | 消除了明显的长段等待；切分和统计单位也改变了，不能只归因于内核 |
| 最后一次 CPU 6T NeMo 验证 | D1 45 秒，13 条、0 警告；P50 0.954、P95 1.335 秒 | 当前推荐配置确实已到一秒级的服务端出字幕延迟 |
| Chrome 实播验证 | 1080p 跳转前后 12 加 4 条；4K 11 条，4K 本次 1049 帧中 0 掉帧 | 不只是离线 benchmark，字幕进入了真实页面；不是长时间播放和全部内容正确的验收 |

[联合原始结果](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/perf-probe-2026-11-23/mlx_combined_summary.json)、[性能报告](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/perf-probe-results-2026-11-23.md)、[6T 最终验证](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/step2-3-e2e-2026-11-23/nemo_fixes_verification.json)、[Chrome 原始记录](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/step2-3-e2e-2026-11-23/chrome_e2e_summary.json)。

已经落地的收音与推理解耦、HQ 单 MLX worker、NeMo CPU 与 MT GPU 分工、固定提示 KV、双行字幕，不应重新列成待开发项目。这里的 NeMo 是 ReazonSpeech 的 FastConformer RNNT 模型，不是之前试过的 Nemotron。

# 为什么还不能称为机器上限

首先，硬件算完一段音频所需的时间，与观众等待一句正确中文字幕的时间不是一回事。后者还包括收集足够上下文、VAD 或强切决策、排队、翻译、修复、传输与显示。日语句末的否定、谓语及省略信息尚未出现时，提前输出只能靠预测或允许改写；更快的矩阵乘法不能补出尚未听到的内容。

其次，当前还有可定位的软件保真缺口，且同模型原生迁移后的质量差距没有解释清楚。这意味着实现、模型能力与硬件限制尚未被隔离，不能把剩余误差都归给 M1 Pro。

最后，低平均 CPU 占用或 RTF 小于 1，不等于内核达到了带宽上限，也不等于可以无代价地继续提高识别质量。尚缺的是当前部署配置的热点时间、真实正确字幕覆盖率和热稳态能耗，而不是更多型号之间的宣传跑分。

目前合理的判断是：同模型固定切分下的纯工程提速已进入收益递减区；质量、边界和输出时机仍有更值得验证的空间。不存在可靠的“已经达到硬件 90%”或“还可再提高 50%”估计。

# 先修音频覆盖而不是继续缩短切片

## 强切路径可能漏掉前导音和短尾

`_SmartSegmenter` 等到一帧结束时 VAD 已判定有语音，才以该帧的 `total_samples` 开始缓存；Sherpa 自己则会回溯语音起点。只要确认发生延迟，首次强切所用缓存就可能缺少回溯的音频。后续自然结段又只发送强切点以后的尾段，之前遗漏的开头不会补回来。自然段仅超出强切点不足 250 ms 时，尾部直接不发送。[当前切分源码](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/src/subtitle_service/local_pipeline.py#L143-L197)、[Sherpa 起点回补](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/voice-activity-detector.cc#L94-L105)。

原样提取代码、使用 16 kHz 和每帧 3200 样本的延迟确认模拟 VAD，得到：

| 场景 | 原自然段 | 实际送 ASR 的样本区间 | 结果 |
| - | - | - | - |
| 默认 4.5 秒与 350 ms overlap | [0, 96000) | [3200, 58880) 与 [53280, 96000) | 前 200 ms 没有进入任何识别片 |
| 自然结尾仅超强切点 100 ms | [0, 60480) | [3200, 58880) | 除前导部分外，末尾 1600 样本也没有进入识别 |
| 合法配置 max_segment_s 为 1 | 内部波谷搜索仍以至少 2 秒为起点 | 切点可越过数组末尾 | 时间戳与真实样本区间错位；默认 4.5 秒不受此特定配置问题 |

这是可重现的路径问题，不代表模拟中遗漏的所有样本都包含音素，更不代表 D1 每句实际都触发。Sherpa 的 `max_speech_duration=4` 也不是硬截断：超过它后将阈值改成 0.90、最短静音改成 0.1 秒，连续高置信语音仍可抵达外层 4.5 秒强切。[上游实现](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/voice-activity-detector.cc#L50-L65)。

改造方向是用一个带绝对样本编号的 ring buffer 保存连续 PCM，分别维护“已解码范围”和“已提交范围”。首次段从真实 VAD 起点取音频；短尾可以合并或延迟提交，但不能无记录丢弃。波谷搜索必须约束在数组内。对自然段与全部识别片的区间并集做守恒检查，允许明确记录的 overlap，不允许静默空洞。现有只检查 end 单调的测试不足以覆盖这一点。

## 字符相等不足以证明 overlap 重复

当前去重依赖 2 至 12 个归一化日文字符的首尾匹配，只在 overlap 段上执行。这可能删掉真实的新一轮回应：`はいはい` 后面的 `はい、次に行きます` 会变成 `次に行きます`；全句相同又因 fallback 保留，不能保证消重。[去重源码](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/src/subtitle_service/local_pipeline.py#L41-L61)。

优先借用 speech.cpp 已有 token 时间跨度，按音频 overlap 和 token 对齐确定哪部分已经提交，而非单凭两字符命中。补合成静音后还需把时间偏移正确换回原 PCM 时轴。没有可靠对齐时宁可保留或标记不确定，不要强行删除。

# 原生 NeMo 的同模型质量差距值得优先排查

| 冻结 91 片的配置 | ASR P50 | D0 CER | D1 CER |
| - | - | - | - |
| 旧 ReazonSpeech Python 官方封装，CPU | 2.073 秒 | 15.46% | 28.74% |
| speech.cpp Q8 CPU 8T beam | 0.2229 秒 | 21.00% | 34.43% |
| speech.cpp Q8 Metal encoder 加 CPU beam | 0.1167 秒 | 21.00% | 35.03% |
| speech.cpp F16 Metal beam | 0.1258 秒 | 20.54% | 35.33% |

[旧 NeMo 原始结果](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/tier2-asr-2026-11-23/asr_nemo_summary.json)、[原生七配置结果](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/perf-probe-2026-11-23/speech_cpp_nemo_summary.json)。

P50 的约 9.3 倍提速是实际归档，但不能表述为“同质量的后端提速”。当前部署已改为 6T，表中完整冻结评测是 8T；不能把它们拼为一个配置。原报告中 D0 最佳 20.54% 与 D1 最佳 34.43% 也来自不同配置。

这里还有评分局限：D0 某一讲解片占约 72.8% 参考字，另一个片的部分说话轮没有完整标注；D1 只有同一段日剧，片尾 SRT 跨出了裁剪末点。上述数值是仓库评分口径下的差距，不是无偏的场景准确率。评分器已去掉多数标点，所以单纯标点差异不能解释 5 至 6 个百分点的差别。[计分源码](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/p0/tier2_asr/metrics_utils.py)。

## 已发现一个可以立即证伪的输入差异

旧测试调用的是 `reazonspeech.nemo.asr.transcribe`，不是裸 `model.transcribe`。[旧调用](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/p0/tier2_asr/bench_nemo.py)。公开官方封装会执行 `pad_audio(norm_audio(audio), PAD_SECONDS)`，其中 `PAD_SECONDS=0.5`，即对 16 kHz 单声道在前后各补 8000 个零；当前生产 ctypes 路径直接提交原始数组，没有这一步。

[官方 transcribe](https://github.com/reazon-research/ReazonSpeech/blob/2d4d4762e7ee294ac8e47a177ac2e9b0e8d0d43f/pkg/nemo-asr/src/transcribe.py)、[pad_audio](https://github.com/reazon-research/ReazonSpeech/blob/2d4d4762e7ee294ac8e47a177ac2e9b0e8d0d43f/pkg/nemo-asr/src/audio.py)、[PAD_SECONDS](https://github.com/reazon-research/ReazonSpeech/blob/2d4d4762e7ee294ac8e47a177ac2e9b0e8d0d43f/pkg/nemo-asr/src/decode.py)、[生产 adapter](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/src/subtitle_service/local_pipeline.py#L444-L471)。

这不是已证实的 CER 病因。旧环境安装的 wrapper revision 和历史微基准临时 C++ runner 都要核对；后者源码没有进入本次可审查的 Git tree，不能断言它当时是否另行补零。

最小实验顺序：

1. 固定相同原始 PCM、模型来源、ALSD beam 参数、线程数，比较原生 raw 与两端各 0.5 秒合成静音；保留未经重复过滤、哔声过滤的原始输出。

2. 核对旧 wrapper 安装版本，做裸 PyTorch 与 wrapper 的同 PCM 对照。D1 只计完整落入音频的参考话轮；D0 修正未标说话轮。

3. 若质量差仍大，选最早分叉的十余段，先检查 mel、帧数、mask、encoder 投影，再检查 beam 排序和归一化。用同精度 F16 或少量 F32 诊断，不先把所有差异归为 Q8。

4. 最后回到真实新切分和未见过的对白验证，不能用旧 91 片替代新 4.5 秒分段质量。

合成补零不需要等待现场未来音频，但会增加推理量；RTF 分母仍用原始音频时长。这个方向有机会同时保住原生速度并回收质量，收益需要实验决定。

# 两个有源码依据的底层提速候选

## speech.cpp 纯 CPU 后端的持久线程池

固定 v0.11.2 中，`Recognizer::OnCpu` 对 GPU 模型的 CPU decoder 分支显式创建并绑定持久 ggml 线程池。但模型直接加载到 CPU 时，`on_cpu_` 分支直接调用 device decoder，未经过这个持久池。底层 ggml 在没有池时会为图创建 disposable pool，执行后释放；非 OpenMP 的多线程构建可能由此在 beam 的多个小图之间反复起停工作线程。

[Recognizer 源码](https://github.com/nyosegawa/speech.cpp/blob/cc16a71e46ee3324ca07f9df4d90ce43622ae598/src/families/fastconformer/recognizer.cpp)、[ggml 空池路径](https://github.com/nyosegawa/ggml/blob/79b50d239d0b88852814c873db0d85d43ed66d9b/src/ggml-cpu/ggml-cpu.c#L3415-L3425)。

这是比“再试一个框架”更具体的 fork 候选，但要先在实际 CMake 配置和线程 trace 中确认。若构建用了 OpenMP，或线程起停不在热点，停止此方向。若命中，让 CPU backend 持有同寿命、可休眠的池，绑定后复用；关闭时等待图结束再释放。逐条比较 token 和时间戳，并检查空闲时是否因为忙等增加功耗。

上游已有 [fastconformer-timing](https://github.com/nyosegawa/speech.cpp/blob/v0.11.2/measure/fastconformer-timing.cpp)，能拆 frontend、encoder build/compute/read、decoder、graph 数。它不是线上 6T C API 的直接等价工具，需要用同音频、同线程并在真实路径交叉校准。先复用该工具和 Instruments，而不是新建一个庞大的监控框架。

## MLX 的小矩阵预填内核

[MLX PR 4787](https://github.com/ml-explore/mlx/pull/4787) 为无 NAX 的 GPU 增加 affine qmv_mma 路径，重点是特定 3 至 16 行矩阵、4 或 8 bit、受支持的 group size 和矩阵大小。M1 被按架构启用，但作者没有测 M1；M2 Pro 的 batch 8 吞吐提升不适用于当前单用户逐 token decode。

固定 `mlx v0.32.4` 不包含该 PR，可由[提交关系](https://github.com/ml-explore/mlx/compare/ba8c005c1b687227ff79a55da5b8aa062d92541f...v0.32.4)与 tag 源码确认。当前固定前缀后剩下的短 suffix prefill 可能命中，但许多日文句子加模板超过 16 行，不能预设有收益。

先记录生产输入矩阵形状和 kernel 时间占比。若不命中，停止；若命中，在隔离环境比较同父版本加或不加这项修改，避免把后续几十个提交混为一项增益。不要为触发 batch 路径等多条实时字幕凑批，也不要把 batch 8 的 2.89 倍宣传为本项目单流提升。

缓存后孤立 MT 的总计时中，TTFT 约占 44.1%，之后生成约占 55.8%。按该旧切片归档推算，即便把后半段再缩短 10%，全 MT 也仅省约 5.6%，平均约 20 ms 每句；它不是声到屏 P95 的预测。这说明后续内核优化可能有价值，但不应承诺再次复制秒级大幅改善。

# 当前性能测量需要补上的几处边界

这些修正用于选择热点，不否定已经观察到的提速。

1. **A3 encoder 亚毫秒是归因假象。** 探针 A1 显式 eval encoder，A2 eval 合并输入，A3 则只构建惰性图；没有物化的计算被推迟到首 token。无参 `mx.synchronize()` 也不会执行未求值图。因此 A3 encoder P50 0.815 ms 不能与旧 51.76 ms 直接比较。MLX-LM 还有单独 generation stream，输出 ID 可读的墙钟与精确 GPU 阶段耗时应分开报告。[探针](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/p0/perf_probe/profile_and_bench_mlx.py#L243-L305)、[MLX 惰性求值](https://github.com/ml-explore/mlx/blob/v0.32.4/docs/src/usage/lazy_evaluation.rst)、[MLX 流同步](https://github.com/ml-explore/mlx/blob/v0.32.4/python/src/stream.cpp#L213-L230)。

2. **MT 42% 总耗时降幅不是完全一致的后处理口径。** M0 包括修复，M1 的 total 在 trim、decode 与修复前结束。TTFT 和联合墙钟仍支持明显提速，但之后应该把主译、修复、取消分别计时。[MT 探针](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/p0/perf_probe/profile_and_bench_mlx.py#L518-L580)。

3. **两轮 72/91 不证明两轮相同。** 脚本每轮只计算与 M0 相同多少条，没有逐句比较两轮。当前单工 KV 所有权和 finally trim 是正确的；没有证据指认污染，也没有足够证据说零污染。保存 A/B/A、乱序、取消和异常后的输出 token 与各层 cache offset。[两轮比较](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/p0/perf_probe/profile_and_bench_mlx.py#L776-L805)。[MLX 4819](https://github.com/ml-explore/mlx/issues/4819) 的其他模型复现表明矩阵形状改变归约顺序可能让 greedy 输出漂移，这只是待验原因。

4. **并发数字样本过少且配置不同。** CPU 8T Q8 greedy 的 MT 只有 4 次，GPU F16 greedy 只有 2 次；ASR solo 与 concurrent 子集也不同。+3.79% 与 +33.75% 支持进一步测争用，不能证明现用 6T beam 零争用或所有 GPU 路线更慢。[原始并发记录](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/perf-probe-2026-11-23/concurrency_contention.json)。

5. **P95 的终点还在服务端。** 当前 live 分析按分段末样本到服务器 ws_sent 计算；Chrome 记录证明确有 DOM 字幕，却缺少 offscreen 收音与 WS 接收关联。当前行移动到上一行也被记为 dom_removed，所以不能据此计算真正双行驻留时间。[分析函数](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/p0/tier2_mt/verify_m1_pro_hq_live.py#L141-L165)、[DOM 诊断](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/extension/src/content.ts#L52-L125)。

6. **对照实现会随 main 漂移。** 目前 combined baseline 直接调用已改成 cached 的生产 Translator，今天重跑不再等同历史 A0 加 M0。把 uncached 对照固定为独立 runner 或明确历史版本。[对照调用](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/p0/perf_probe/profile_and_bench_mlx.py#L890-L924)。

当前 draft 0.6B 的推测解码仍不值得优先复测：耗时 90.76 秒明显不划算。但其脚本只保留最后一段流式文本，0/91 不能作为质量结论，33.44% 是 from_draft 输出占比而非标准草稿接受率。修正报告口径即可，不因此重新打开整条推测路线。

# 正确字幕和快速字幕必须在同一次测试里验收

D1 完整落在音频内的 cue 87 是“絶対キレられる”，含义是“肯定会被骂”或“对方肯定会发火”。新 NeMo 回放出现“绝对放弃”，但 warnings 仍为零。此前较长切分的 NeMo 日文也曾听错此处，因此不能把这一个错误直接归因于新强切或翻译模型。

[完整参考](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/tier2-asr-2026-11-23/d1_srt_cues.json)、[本次输出](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/step2-3-e2e-2026-11-23/live_comparison.json)、[旧日文对照](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/docs/experiments/tier2b-mt-2026-11-23/d1_drama_comparison.json)。同一句在 cue 93 再次出现，但音频只保留其开头约 556 ms；不能拿完整 SRT 尾句惩罚这个裁剪样本。

下一轮用固定参考话轮作分母，而不是让模型切成多少片就算多少条。每轮都保留：音频区间、原始 ASR、去重后 ASR、中文、修复触发、实际页面可见时间。分别记录：

- 标注话轮在 5 秒内出现可用中文的覆盖率，并同时记录句末到首次正确可见的 P50/P95；遗漏不得从分母消失。

- 否定、数字、人名、关系与动作是否正确；CER 之外保留关键含义错误数。

- 稳定上屏后的反复修改、重复、提前清屏及上下行阅读驻留。

- CPU/GPU/ANE 能耗、热稳定后的延迟、掉帧；用同片无字幕播放作能耗与掉帧对照。

对每类失败样本做两个廉价隔离：相同音频较完整上下文的离线识别，以及人工正确日文直接进现有 MT。前者判断边界损失，后者判断即使 ASR 完美时翻译还错多少。若大头是 MT 的断句与省略，应该试有限历史上下文，而不是继续换 ASR。

有限上下文可从前一至两条已确认日文开始，固定最大 token 数、只翻当前段、不加入尚未到达的未来信息。比较含历史、无历史与误导性历史三组；关注人称、专名、短应答和否定，防止把之前的错误扩散。固定系统提示仍可缓存，但这是新的 prompt 合同，需要重新验输入 token 与质量，不能当无损加速。

# 仍然值得保留的其他方向

| 方向 | 能解决的问题 | 当前条件和排序 |
| - | - | - |
| 相同 Q8 beam 的 CPU 与 Metal encoder 公平比较 | CPU 之外是否还有实际低延迟部署点 | 原始单段 CPU8T 222.9 ms、Metal encoder 加 CPU decoder 116.7 ms，说明空间值得检查；用同 6T、同 PCM、同 MT 输入比较串行或受控重叠，不用不同精度结果推导 |
| 仅对模糊边界增加声学上下文 | 省掉不必要的全量重识别，恢复断词与短句 | 先有 token 时间与未提交游标；限额触发局部重解码，不默认所有片双跑。测正确字幕覆盖和计算预算；不能用裸字符 LocalAgreement 代替时间对齐 |
| Ruby-ASR subtitle 或 verbatim | 日语书写与实际读音保真 | 作者五套日语集合 raw CER：Qwen3-ASR 10.78%、subtitle 8.51%；是作者跨数据集加权结果，不是本机收益。主权重结构兼容 Qwen3-ASR，但 MLX 转换与 bracket 输出需验证，生成 token 还可能增加。列为质量候选，不替换现有推荐默认 |
| 123M 日语 cache-aware FastConformer | 从训练架构层面减少等待与重复计算 | 支持 0、80、480、1040 ms 的配置 lookahead；这是算法右上下文，不是中文端到端延迟。需要本地流式缓存实现、日剧质量和许可证验证；模型名 M1-6 不是 Apple M1 实测 |
| ANE encoder | 低功耗与长时间播放时的 CPU 余量 | 当前 ASR 已不占 Metal，原来“给 MT 腾 GPU”的主要理由减弱。先看 CPU 能耗是否仍占大头，再做 encoder 单组件导出；不承诺准确率提升 |
| 4090 离线辅助小模型适配 | 纯算子优化无法解决的固定领域错误 | 只作为以后选项：用已有 4090 做授权语料整理、teacher 标注或小范围微调，最终运行仍在 M1；必须按作品、说话人隔离测试集，不能用 D1 测试样本训练 |

Ruby 一手：[模型卡](https://huggingface.co/hshispeech/Ruby-ASR-1.7B)、[作者代码](https://github.com/hshi-speech/Ruby-ASR-1.7B)。CTC mora 分支只输出读音，不等同有汉字和标点的可直接翻译文本；不能跳过这项质量问题。123M 一手：[模型卡](https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open)，许可为 CC-BY-NC 4.0，商业用途不能直接采用。

[mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) 可以借鉴缓存所有权、回滚和前端 parity 测试，但其公布的 M4 Pro、窗口重解码等成绩不能替代现在 M1 Pro 的 NeMo 测试。现有离线模型的 encoder 含右侧上下文，不能只缓存旧输出就宣布变成无损原生流式。

[Parakeet CoreML encoder PR 199](https://github.com/Blaizzy/mlx-audio-swift/pull/199) 有 M1 Max 英语 TED-LIUM 的功耗实测，且说明静态 shape、stride 处理、CPU 拷贝等代价；PR 最终关闭，不能当成上游已经默认集成。其模型是 Parakeet TDT，不是这里的日语 NeMo RNNT。可借鉴导出和验证方法，不能直接搬用功耗倍数。

外部检索覆盖 GitHub 发布、Issue 与源码，Hugging Face 模型卡、作者实验和社区经验，并专查了 Reddit。没有找到同 M1 Pro、同日语视频、同现用量化与解码配置的 Reddit 可复现对照，因此没有拿论坛印象覆盖本机归档。API 形式的最新 ASR 进展也不自动变成可在 M1 部署的开源权重。

# 下一轮建议执行顺序

以下是实验优先级和取舍标准，不是已经测得的增益。保留当前生产配置，所有变更在独立分支与隔离依赖环境进行。

| 顺序 | 工作范围 | 交付物与停止条件 |
| - | - | - |
| 0 固定证据 | 锁实际 6T beam 配置、动态库与模型哈希、依赖、同一 PCM 和参考话轮；补 cut_reason、排队和页面事件 | 能复现当前正常播放；不重新搭建大型评测平台。uv.lock 与 pyproject 当前不一致，先修可复验性 |
| 1 保住音频与输出 | 修首尾样本覆盖、短尾处理、切点范围；区分 native 错误和静音；验证语义重复不被删除 | 确定性样本守恒测试通过，真实新对白不丢音；这类正确性修复不以测速获胜才采用 |
| 2 输入 parity | 原生 raw 与补零、旧 wrapper 与裸模型；少量同精度中间张量分叉诊断 | 约一至两天初筛。若补零两组改善都小于约 1pp，停止继续扫补零长度，转首次分叉；若明显回收差距且不损害其他域，进入外留集验证 |
| 3 原生 CPU 小改造 | 确认线程池创建销毁热点，命中才试持久池；记录 idle 与热态能耗 | 未命中立即停止。若仅孤立微基准快、正确字幕 P95 与能耗都不改善，不维护 fork |
| 4 MT 小预填与取消 | 形状 trace 决定是否试 MLX 4787；给 repair 接入取消；验证随机顺序、异常后 KV 状态 | 不命中内核就停止；repair 修复解决的是 seek 长尾，普通无 repair 句子不应被声称提速 |
| 5 端到端胜者复核 | 外留对白、讲解、BGM 与抢话，至少三轮 AB/BA；每种部署路径至少 100 次 MT，随后 30 至 60 分钟真实播放 | 用正确话轮、DOM、能耗、掉帧共同验收。若变化在波动内或用漏译换速度，则拒绝 |
| 6 结构性研究 | 仅前五项稳定后，挑 Ruby 或真正 cache-aware 日语模型之一 | 先证明同域质量或算法等待更优，再投入 Mac 移植；不要同时启动五个模型适配项目 |

对需要长期维护的性能 fork，可以预注册一个务实门槛：正确字幕的真实可见 P95 改善至少约 100 ms，或在质量与延迟非劣的前提下每分钟播放的整机能耗下降至少 10%。这些是投入产出门槛，不是统计显著性的替代；按独立片段或作品分组比较，不能把同一段切出的几十片当几十个独立样本。

不可放松的门槛是首尾/否定/数字/人名不出现新的严重错误，遗漏与超时计入分母，静音误字幕不增加，seek 不串旧 epoch，热稳态不积压。质量提高可以接受少量计算增加，但必须事先声明可接受的延迟与功耗预算，不能事后挑指标。

# 两处应顺手补齐的工程缺口

正常 MT 已支持协作取消，但假名修复走 `_run_prompt` 的高层同步 generate，最多 256 token，不检查 cancel_event。关闭要等真实 worker 才释放 lease；150、350、700 ms 的重试不能保证覆盖修复长尾。测试需要在 ASR encoder、beam、MT prefill、decode、repair 确认正在计算后再 seek，记录 cancel、worker_exit、lease_free、new_ready 和首条新字幕。50 ms 后重放成功不等于取消总耗时只有 50 ms。[修复与取消](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/src/subtitle_service/local_pipeline.py#L546-L616)、[关闭](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/src/subtitle_service/local_pipeline.py#L919-L934)。

原生 C API 的非零错误码和空 result 当前也会返回空串，与静音或取消混在一起，可能出现“0 警告但漏句”。应检查 set_audio、set_string、set_progress 和 transcribe 返回值，记录可区分的状态；静音过滤继续保留，但不得隐藏推理故障。[原生调用](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/src/subtitle_service/local_pipeline.py#L444-L471)。

`pyproject.toml` 声明 MLX 0.32.4、mlx-audio 0.6.7，`uv.lock` 仍是 MLX 0.31.2 且没有 mlx-audio；模型安装也缺少实际文件哈希验证。它们不是新性能方向，却决定别人或下次干净安装能否重现这次成果。[依赖声明](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/pyproject.toml)、[锁文件](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/engine/uv.lock)、[安装脚本](https://github.com/lkyprogramer/live-video-tanslate/blob/3e506514a6d36780100dc2e8fa1de1a2897b1b51/scripts/setup_speech_cpp_nemo.sh)。

# 最终取舍

本轮成果应当保留。新基线已经足够快，后续不应该再以“看到任何中文就算更实时”为目标，而应以“相同内容、相同正确性，更早稳定出现”为目标。

最优先的三件事是：音频样本覆盖守恒、原生 NeMo 输入与质量 parity、纯 CPU beam 的实际线程池热点。MLX 小矩阵内核是有条件的第四项；ANE、全新原生流式模型、定向训练属于更高投入的后续分支。

现阶段既不该宣布 M1 Pro 已到顶，也不该继续保证还能靠换框架获得另一次数倍提升。能够合理争取的是减少可解释的识别损失、消除特定尾延迟，并用真实热点决定是否维护一个小而明确的底层优化。
