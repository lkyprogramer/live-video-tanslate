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

目标文件为 `p0/p3/adapters/japanese_cache.py`；外部 adapter、异常与时间事件服从 [09 章统一契约](09-runtime-integration.md)，本章只规定模型内部检查。预期实施证据为 `japanese-cache-contract.json`、`frontend-parity.json`、`export.log`、`cache-trace.jsonl`、`rnnt-parity.json` 和锁文件。未生成的产物不得填成功占位值。

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
- [NeMo ASR 官方文档](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/models.html)：研究仅获得 cache ONNX 导出线索，未核实该权重完整 graph，实施时须核对与锁定版本对应源码。[只读日语汇总](../research/japanese.md)与[扩展结论](../research/expanded-review.md)没有现成 M1 cache-stream runtime 的已验证结论。
