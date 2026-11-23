# Apple M1 Pro（32 GiB）日文实时视频翻译：现状根因与 2025–2026 社区新方案升级调研报告

- **调研日期**：2026-11-23
- **目标环境（本机实测硬件）**：Apple M1 Pro（10 核 CPU / 16 核 GPU）、**32 GiB 统一内存**（`34359738368` bytes）、macOS 26.7.2（Build `25G332`）
- **当前默认基线（`reazon-hy`）**：`ReazonSpeech k2-v2 INT8`（159M Zipformer）+ `Silero VAD v4` + `Hy-MT2 1.8B 4-bit`（MLX）
- **文档性质**：技术调研与升级路线图（仅报告，不修改产品代码）

> **证据分级说明**：
> - **【本机实测事实】**：在本仓库或 `local-streaming-validation` worktree 中已于当前 M1 Pro 实际运行并有落盘记录的结果。
> - **【社区/论文/官方宣称（未本机实测）】**：来自 GitHub、Hugging Face、Reddit、论文或官方发布页的公开数据，尚未在本机 D0/D1 基准上验证。
> - **【推断与假设】**：基于上述事实与工程约束作出的架构判断或资源估算，需后续实验验证。

---

## 1. 核心结论（先给答案）

**结论：还没有穷尽所有探索方向，你的 M1 Pro 32 GiB 也远未触及硬件天花板。**

此前默认方案 `reazon-hy` 之所以在 20 余轮对比中“无人能打败”，并不是因为 `k2-v2 + Silero v4 + Hy-MT2 1.8B` 已经达到日语实时翻译的极限，而是因为过去所有本地实验都被锁死在 **四个结构性瓶颈** 中：

1. **【本机实测事实】32 GiB 机器上套用了 2.5 GiB 人为默认内存门禁**：
   Chrome 播放视频 + 本地推理服务常驻已占约 `2.17 GiB`，`2.5 GiB` 门禁只给模型切换留出约 `390 MiB` 裕量。这把绝大多数尝试死死压在 `100M–1.8B` 的极小模型区间，导致 M1 Pro 剩余约 **28+ GiB 统一内存** 长期闲置。
2. **【本机实测事实】误差预算被小 ASR 与小 MT 两头卡死**：
   在 34 项 D0 基准上，`reazon-hy` 实测 **20/34**（严格复核 **18/34**）；而把干净日文 Oracle 直接喂给 `Hy-MT2 1.8B` 可达 **28/34**（见 [optimization-results.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/optimization-results.md)）。这说明大约 **8 项错误来自 159M Zipformer ASR**（同音词、专有名词、切分边界误识别），**6 项错误来自 1.8B MT**。
3. **【本机实测事实】1.8B MT 缺乏抗上下文污染能力**：
   [local-streaming-validation-2026-11-23.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/local-streaming-validation-2026-11-23.md) 中的 **A3 C1** 实验证明，`Hy-MT2 1.8B` 无法把前 2 句日文仅当作消歧语境，而是会直接抄写或重复翻译历史句子，造成严重上下文污染。
4. **【本机实测事实】声学盲切 + 日语 SOV 后置否定结构冲突**：
   `Silero VAD v4` 的 `max_speech_duration=4s` 触发了 57/88 次强制硬切（`length_managed_pause`）；若单纯拉长到 10s（**A1b**），会带来 `+2.5–11s` 延迟及 ≥9 项质量回退；若在完整句前提前出草稿（**A2**），仅 1/34 提前可读且 P95 抖动达 6 次，甚至出现肯定/否定反转（如`成为管理人员` → `不是要成为管理人员`）。

此外，**P3 方案文档已规划的两条主线（`Confucius4-R2T2` 与 `123M 日语 Cache-Aware FastConformer RNNT`）以及 2025–2026 社区涌现的 `Qwen3-ASR-1.7B`、`ReazonSpeech-NeMo-v2`（619M）、`Smart Turn v3` 语义端点检测、`Seed-X-7B` / `TranslateGemma` / `PLaMo-3-8B` / `Sakura-7B` 均尚未在本机跑过**。

---

## 2. 历史本机实测全景复盘与“社区网红模型”祛魅（避免重复踩坑）

以下全部为 **【本机实测事实】**（基于同一台 M1 Pro 的 34 项 D0 基准与工作区验证文档，基线 B0 = **20/34** / 复核 **18/34**，干净日文 → `Hy-MT2 1.8B` = **28/34**）。凡本机已明确证伪的路径，后续不应再重复投入时间。

### 2.1 已测路线汇总表

| 类别 | 已测模型 / 策略 | 本机实测结果 | 关键证据与问题 | 结论 |
| :--- | :--- | :--- | :--- | :--- |
| **ASR** | `ReazonSpeech k2-v2 INT8`（默认） | **20/34**（复核 **18/34**） | 159M Zipformer；流式 CPU/内存可控（`1849–1877 MiB`） | 当前基线 |
| **ASR** | `Reazon k2-v2` FP32 / beam4 / hotwords | 19/19、18/18、18/16 | 均未超过默认 INT8 | 淘汰 |
| **ASR** | `Haas-ja ONNX` | 19/19 | 与基线持平，无净收益 | 淘汰 |
| **ASR** | `Parakeet-TDT-CTC-0.6b-ja`（CoreML/FluidAudio） | 17/18 | 略逊基线，日语口语边界未胜出 | 淘汰 |
| **ASR** | `Qwen3-ASR-0.6B-8bit`（MLX） | 11/11 | 0.6B 容量过小，准确率大幅落后 | **0.6B 淘汰**（1.7B 未测） |
| **ASR** | `Kotoba-Whisper v2.0 Q5_0`（whisper.cpp） | 12/34（P95 `1.04s`） | 延迟高且准确率明显低于基线 | 淘汰 |
| **ASR** | `Moonshine Small Streaming 123M` | 18/34（5s 窗口） | CPU 占用 `72.5%`，未能超过基线 | 淘汰 |
| **ASR** | `PengChengStarling Zipformer INT8` | 6/34 | 日语识别严重崩坏 | 淘汰 |
| **ASR** | `WhisperLiveKit small/turbo`、`WhisperKit CoreML small` | 4/12、3/12；turbo 在旧 PyTorch CPU 卡死在 20s | WhisperLiveKit 调度与旧 CPU 解码拖垮实速 | 框架路径淘汰（注：原生 `mlx-whisper` Metal 未测） |
| **ASR** | `Apple SpeechAnalyzer`（macOS 26.7.2） | 默认 final `1/8`（等 `12–15s`）；强切 `2/8` | 见 [p0-apple.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/p0-apple.md)，静音幻觉输出“啊” | **当前 OS 版本淘汰** |
| **ASR** | `SenseVoiceSmall INT8` / `faster-whisper-ja-760M` | 8/9；16/15（`4.95s/seg`） | 分别因准确率低与 CT2 CPU 过慢淘汰 | 淘汰 |
| **ASR** | `Granite 470M TurboCTC` / `Reazon ja-en` | 16–18；14–16 | 均未超过基线 | 淘汰 |
| **ASR** | `Voxtral-Mini-4B-Realtime-2602-4bit` | 17/18（footprint `4.54 GiB`） | 内存翻倍但未赢基线 | 淘汰 |
| **端到端 AST** | `Gemma 4 E2B-it 4-bit`（双语 / 纯中文 G0） | 双语 6/8（`4.94 GiB`）；纯中文 G0 `0/3` | 无法稳定直出有效中文字幕 | 淘汰 |
| **VAD / 调度** | `Silero v6.2` / `FireRed Stream VAD` / `6s 软切` / `邻句合并` / `±320ms padding` / `PAD-900` / `PUNCT-JA` | 13–17；14–17；丢 2 句；18/18（`+4.6s`）；17/18；13/12；18/18 | 均未改善净准确率或引入高延迟 | 淘汰 |
| **VAD / 调度** | **A1b**（10s 硬限整句提交） | ≥9 项质量回退，`+2.5–11s` 延迟 | 见 [local-streaming-validation-2026-11-23.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/local-streaming-validation-2026-11-23.md) | 撤回 |
| **VAD / 调度** | **A2**（同模型 `Hy-MT2 1.8B` 早期草稿） | 仅 1/34 提前可读，P95=6 次改写 | SOV 动词后置导致否定含义反转 | 撤回 |
| **MT** | `Hy-MT2 1.8B 4-bit`（默认） | 基线 **20/18**；干净日文 **28/28** | 单独翻译尚可，无法做上下文消歧/纠错 | 当前基线 |
| **MT** | `Hy-MT2 7B 4-bit` / `Index Translate 2B Q8` | 17/17；13/13 | 实测未超 `Hy-MT2 1.8B` | 淘汰 |
| **MT** | `PLaMo-3-Translate 0.8B / 2B 4-bit` | 17/17（3–4/62 假名残留） | 小参数版漏假名，未测 8B | **0.8B/2B 淘汰**（8B 未测） |
| **MT** | `Apple Translation`（macOS 系统） | **21/21**（净 `+1`，延迟 `84ms`） | [optimization-results.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/optimization-results.md) 唯一与基线持平略优的轻量 MT | 可作 Tier 1 备选 |
| **MT** | `M2-hit` 术语表 / **A3 C1**（2 句历史日文） | 22/20（20 补充集净 0）；C1 严重串台 | 1.8B 小模型会把历史句子直接翻译输出 | 1.8B 上下文方案淘汰 |

> 完整历史实验细节可追溯：[optimization-results.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/optimization-results.md)、[exploration-results-2026-11-07.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/exploration-results-2026-11-07.md)、[p2-b0-results-2026-11-08.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/p2-b0-results-2026-11-08.md)、[p2-b1-results-2026-11-08.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/p2-b1-results-2026-11-08.md)、[p2-b2-results-2026-11-09.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/p2-b2-results-2026-11-09.md)、[p2-b3-results-2026-11-09.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/p2-b3-results-2026-11-09.md)、[p2-b4-gemma-zh-results-2026-11-09.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/p2-b4-gemma-zh-results-2026-11-09.md) 及 [local-streaming-validation-2026-11-19.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/local-streaming-validation-2026-11-19.md)。

### 2.2 对社区“网红方案”的本机实测祛魅

- **`macOS 26 Apple SpeechAnalyzer`**：Reddit 与部分博客常宣传其 FLEURS 日语 CER `6.36%`、停顿约 `100ms` 出字。但 **【本机实测事实】**（[p0-apple.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/p0-apple.md)）表明，在连续视频对白下，其默认 `final` 需等待 `12–15s`（5s 窗口仅 `1/8` 可用）；强行调用 `finalize(through:)` 仅 `2/8` 可用且会在静音处幻觉出“啊”。在苹果更新 `ja-JP` 资产或提供可控分块 API 前，**不要再重测**。
- **`SenseVoiceSmall` / `Kotoba-Whisper v2.0` / `Qwen3-ASR-0.6B` / `Voxtral 4B` / `Gemma 4 E2B`**：均已在本机 D0 真实影视/对白集上落败，证明“短句朗读 benchmark 好看”不等于“连续日剧对白抗噪+低延迟好用”。

---

## 3. 全网（GitHub / Hugging Face / Reddit）2025–2026 新模型与新框架调研

> **重要声明**：本节所有性能数字与特性均为 **【社区/论文/官方文档宣称（未本机实测）】**，仅作为候选漏斗筛选依据，不代表已在本机战胜 `reazon-hy`。

### 3.1 新一代 ASR（直击约 8 项 ASR 识别错误）

1. **`Qwen3-ASR-1.7B` 及其日语微调版（`neosophie/Qwen3-ASR-1.7B-JA`、`Ruby-ASR-1.7B`）+ `moona3k/mlx-qwen3-asr`**
   - **关键变化**：此前本机只测过 `0.6B-8bit`（11/11 落败），从未测过 `1.7B`。社区多组基准显示 `Qwen3-ASR` 从 `0.6B` 升至 `1.7B` 后日语能力跃升明显，公开日语 CER 约 `5.7–8.5%`，且对**专有名词与日英混杂（Code-switching）**明显强于 159M Zipformer。
   - **MLX 流式实现**：GitHub 开源项目 `moona3k/mlx-qwen3-asr` 与 `Neywa-Labs/mlx-audio` 已实现纯 MLX Metal 滑窗上下文 + KV cache 复用，Q4/Q8 量化仅占约 **`1.5–2.3 GiB`**（【推断】完全在 M1 Pro 可承受范围）。
2. **`ReazonSpeech-NeMo-v2`（619M FastConformer-RNNT）**
   - **关键变化**：与当前基线 `k2-v2`（159M）使用同一份 35,000 小时日本电视广播语料训练，但参数量放大 **4 倍**（619M），社区实测真实广播/口语日语 CER 约 `9–12%`。此前本机从未测试过 NeMo 版本。
   - **Apple Silicon 运行路径**：可通过 `reazonspeech.nemo.asr`（PyTorch MPS）、导出 ONNX（`sherpa-onnx` / `onnxruntime`）或轻量 C++/Metal `speech.cpp`（`ggml`）运行。
3. **P3 规划中尚未实测的双主线（文档已就绪，从未运行）**
   - **`Confucius4-R2T2`**（网易有道 `netease-youdao/Confucius4-R2T2-GGUF`，见 [04-r2t2-mac-spike.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/live-video-translate-p3-2026-11-10/docs/04-r2t2-mac-spike.md)）：基于 `Qwen3-ASR-1.7B` 做 Longest Stable Prefix + Wait 后训练，论文宣称 FLEURS JA `160ms/U1` CER 从 `36.07%` 降至 **`8.66%`**。**工程风险**：当前 GGUF `generate_once` 会清空 KV 重编码、Mac 构建与对齐尚未验证、且为网易自定义许可。
   - **`123M 日语 Cache-Aware FastConformer RNNT`**（`streaming-ja-asr-anon/parakeet-streaming-ja-fully-open` / `M1-6.nemo`，Interspeech 2026，见 [05-japanese-cache-aware.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/live-video-translate-p3-2026-11-10/docs/05-japanese-cache-aware.md)）：原生 cache-aware 流式架构（右上下文 `480ms/80ms`），5 域 CER 宣称 `12.65%/13.43%`。**工程风险**：`CC-BY-NC 4.0` 非商用协议，且必须验证导出 ONNX 后是否保留 cache 状态。
4. **原生 `mlx-whisper`（Metal）下的 `whisper-large-v3-turbo` / `anime-whisper` / `kotoba-whisper-v2.2`**
   - **澄清历史误区**：此前 `large-v3-turbo` 仅在 WhisperLiveKit 旧版 PyTorch CPU 解码下卡死在 20s，从未测过原生 **`mlx-whisper`（Metal GPU 加速）**；M1 Pro 上 `mlx-whisper` 通常可跑 `5–8×` 实时倍速。其中 `anime-whisper` 专门针对二次元/影视口语对白微调（贴合《重启人生》场景）；而 `kotoba-whisper-v2.2` 虽是 `v2.0` 的升级版，鉴于 `v2.0 Q5_0` 在本机仅得 `12/34`，对其保持低预期。
5. **P3 次级候选（见 [06-secondary-candidates.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/live-video-translate-p3-2026-11-10/docs/06-secondary-candidates.md)）**
   - `Fun-ASR-Nano`（~800M Audio Encoder + Adaptor + LLM）与 Meta `Omnilingual-ASR-CTC-300M-v2` 尚待验证；注：社区热议的 `FireRedASR2` 主攻中文普通话及方言，不作为日语优先候选。

### 3.2 语义端点检测与新一代 VAD（直击 Silero 4s 盲切与 A1b 延迟）

- **`Smart Turn v3 / v3.2`**（`pipecat-ai/smart-turn`、`onnx-community/smart-turn-v3-ONNX`）
  - **机制**：基于音频原生的语义回合结束检测器（Whisper-Tiny encoder + 分类头），**INT8 ONNX 仅 8 MB（CPU）/ 32 MB（GPU）**，官方明确支持 **日语** 在内的 22 种语言。
  - **价值**：只在 VAD 检测到 `100–300 ms` 短停顿时触发一次推理，判断日语当前分句在语调与语义上是否完整（`complete` vs `incomplete`）。这样既不用像 Silero 那样 4s 一刀盲切（57/88 次 `length_managed_pause`），也不用像 A1b 那样死等 10s，更不需要在自然停顿后干等 600 ms。
- **`TEN VAD`（`TEN-framework/ten-vad`，已集成进 `sherpa-onnx`）+ `TEN Turn Detection`**
  - 提供比 Silero VAD 更低的尾部检测延迟，可作为前置语音活动检测器配合 `Smart Turn v3` 使用。

### 3.3 JA→ZH 翻译升级 + 联合 ASR 同音纠错（释放 32 GiB M1 Pro，4B–14B 档位）

【推断】这是本次调研最具决定性的架构升级点：**1.8B 小翻译模型（`Hy-MT2 1.8B`）只能做“字面直译”，一旦给上下文就串台，也无力修复 ASR 的同音错字；而将内存预算放宽到 4B–8B 级别（占 2.5–5 GiB MLX 4-bit），可以在单次前向中同时完成：`ASR 同音纠错 + 前 2 句只读语境消歧 + 自然口语中文字幕生成`**，从而同时收割掉“~8 项 ASR 错”和“~6 项 MT 错”。

| 候选模型 | 规模与 M1 Pro 4-bit 内存估算 | 核心优势（社区宣称） | 适用定位 |
| :--- | :--- | :--- | :--- |
| **`Seed-X-Instruct-7B` / `Seed-X-PPO-7B`** | 7B，~`4.5 GiB`（MLX/GGUF） | 字节跳动开源，覆盖 28 语种，FLORES-200 / WMT-25 中日英互译宣称比肩 GPT-4o / Gemini 2.5 | 专精高准确度 JA→ZH 翻译 |
| **`TranslateGemma 4B / 12B`** | 4B ~`2.5 GiB` / 12B ~`7 GiB` | Google 专用 SFT+RL 翻译模型（55 语种），指令遵循稳定 | 轻量高质 / 高上限双档可选 |
| **`PLaMo-3-Translate 8B`** | 8B，~`5 GiB`（`pfnet/plamo-3-translate-8b-MLX-4bit`） | PFN+NICT 专用翻译模型，原生支持上下文、术语表与风格控制（本机仅测过 0.8B/2B，未测 8B） | 解决 0.8B/2B 漏假名与上下文需求 |
| **`Qwen3-4B` / `Qwen3-8B`（no-think 模式）** | 4B ~`2.5 GiB` / 8B ~`5 GiB` | 关闭思考模式后 TTFT 极快，中日双语与指令遵循极强 | **联合同音纠错 + 抗污染语境翻译** |
| **`Sakura-GalTransl-7B / 14B`（v3/v4）** | 7B ~`4.5 GiB` / 14B ~`8.5 GiB` | 日中 ACG/日剧口语对白专用微调，人称代词、语气词与口语化中文字幕表现突出 | 贴合《重启人生》等影视对白 |

### 3.4 端到端 AST / Omni 与 Apple Silicon 开源生态框架盘点

- **明确排除项**：腾讯开源的 **`Hy-SimulMT-30B-A3B`**（StreamLAAL ~3s）**仅支持中英互译（ZH↔EN），不支持 JA→ZH**，不要在此浪费验证时间。
- **端到端观察项（低优先级）**：
  - `Qwen3-Omni`（30B-A3B MoE，4-bit 约 `16–18 GiB`）：32 GiB M1 Pro 能装下，但与 Chrome 视频硬解/渲染并发时显存与内存带宽压力很大，不作为首选。
  - `LFM2.5/3-Audio`（~1.5–2B，MLX/MetalRT）：端到端音频架构很新，但日语→中文字幕质量尚无可靠公开证据。
  - `Gemma 4 E4B-it`（~5 GiB）：鉴于 `E2B` 本机仅得 `6/8` 且纯中文 `0/3`，优先级排后。
- **可参考的 Apple Silicon 实时语音/字幕开源工程**：
  - 推理库：`moona3k/mlx-qwen3-asr`、`Neywa-Labs/mlx-audio`、`soniqo/speech-swift`、`speech.cpp`（`ggml` Metal）。
  - 参考应用：`Shuichi346/kotonoha`（macOS 实时翻译字幕）、`qluto/echo`（Rust + `mlx-rs`）、`axot/OpenSuperMLX`、`baijunjie/realtime-translator`、`Lightning-SimulWhisper`。

---

## 4. 面向 M1 Pro 32 GiB 的分档升级实验路线图（按 ROI 排序）

建议不再用单一 `2.5 GiB` 门禁捆绑所有用户/硬件，而是明确拆分为 **“轻量默认档（≤2.5 GiB，保留 `reazon-hy`）”** 与 **“M1 Pro 32 GiB 高精度观影档（总内存预算 5–8 GiB，含 Chrome + 服务进程后约占 7–10 GiB，仍留 20+ GiB 富余）”**。

| 优先级 | 档位与目标 | 核心组件组合 | 预计总内存（含服务/Chrome） | 解决的核心痛点 |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1** | **最低成本实时性/切句修复**<br>（不破 2.5 GiB 门） | `Reazon k2-v2 INT8` + **`TEN VAD` + `Smart Turn v3`（8MB ONNX）** + `Hy-MT2 1.8B`（或已验证 `21/21` 的 `Apple Translation`） | `~2.2 GiB` | 直接替换 Silero 4s 盲切，降低跨词截断与尾延迟 |
| **Tier 2（首选）** | **M1 Pro 32 GiB 高精度观影档**<br>（主攻准确率跃升） | **Step 2A 盲测选优 ASR**：`Qwen3-ASR-1.7B`（`mlx-qwen3-asr`）/ `ReazonSpeech-NeMo-v2`（619M）/ `mlx-whisper`（`large-v3-turbo` & `anime-whisper`）<br>+ **`Smart Turn v3`**<br>+ **Step 2B 盲测选优 MT+纠错**：`Seed-X-7B` / `PLaMo-3-8B` / `TranslateGemma 4B/12B` / `Qwen3-4B/8B` / `Sakura-7B` | `~6–9 GiB` | 释放 32 GiB 算力与内存，同时攻克 ~8 项 ASR 同音错与 ~6 项 MT/语境错，冲击 >28/34 并在《重启人生》45s 19 句密集对白中稳定输出 |
| **Tier 3** | **前沿原生流式探针**<br>（复用 P3 既有方案） | `123M Cache-Aware RNNT (M1-6.nemo)` ONNX cache 验证；`Confucius4-R2T2` Mac 构建与 KV-cache 对齐探针 | `~2.2–3.5 GiB` | 探索 160–480ms 极低延迟原生流式字幕（需单独核查协议与导出可行性） |

---

## 5. 验证顺序、验收门禁与工程避坑清单

1. **严格先离线质量门禁、后 1× 实速资源门禁**（先跑出真实分再动产品代码）：
   - **Stage 1（离线盲测）**：统一在现有 **9 段 PCM 34 项 D0 基准** + **《重启人生》第 1 集 45 秒 19 句密集对白（D1）** 上做离线对比与人工盲评，只有显著超越 `20/34`（且 D1 误译率/漏句明显下降）才晋级。
   - **Stage 2（1× 实速与生命周期门禁）**：沿用 `local-streaming-validation` 的实速时钟回放与内存/CPU 探针，检查端到端延迟、P95、Unified Memory Footprint 与连续播放下的热降频稳定性。
2. **必须规避的已发现工程坑点**：
   - **【本机实测事实】MLX 跨线程采样漂移**：此前 `rt-seed11` 出现 21 处 MT 差异，根因是 `HyTranslator` 在主线程初始化却在临时线程池调用；后续任何 MLX ASR/MT 必须绑定**单一常驻单线程执行器（persistent single-thread worker）**（见 [local-streaming-validation-2026-11-23.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/local-streaming-validation-2026-11-23.md)）。
   - **Metal 带宽与显存竞争**：Chrome 4K/1080p 视频硬解同时运行 `Qwen3-ASR-1.7B` + `7B–8B MT` 时，需实测 TTFT（4B–8B 目标 `<300–600 ms`）是否因并发 Metal 调度出现长尾抖动。
   - **开源协议边界**：`M1-6.nemo` 为 `CC-BY-NC 4.0`（非商用），`Confucius4-R2T2` 为网易自定义许可；若未来有分发需求，优先选 Apache-2.0 / MIT 家族（如 `Qwen3`、`Smart Turn`、`TEN VAD`、`Seed-X` 等）。
