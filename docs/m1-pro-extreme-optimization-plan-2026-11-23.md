# M1 Pro 实时日语字幕性能上限评估与极致优化方案

- **编写日期**：2026-11-23
- **基线提交**：`e06ceea2f6f029e08a507454b75164c78c96b2e3`（`main`）
- **硬件与运行时**：Apple M1 Pro（10 核 CPU / 16 核 GPU / 32 GiB 统一内存 / macOS 26.7.2），`engine/.venv`（`mlx==0.32.4`、`mlx-audio==0.6.7`、`mlx-lm==0.31.3`、`sherpa-onnx==2.0.1`）
- **本文档目的**：
  1. 对 [M1 Pro 实时日语字幕的性能上限与底层优化评估.md](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/M1%20Pro%20%E5%AE%9E%E6%97%B6%E6%97%A5%E8%AF%AD%E5%AD%97%E5%B9%95%E7%9A%84%E6%80%A7%E8%83%BD%E4%B8%8A%E9%99%90%E4%B8%8E%E5%BA%95%E5%B1%82%E4%BC%98%E5%8C%96%E8%AF%84%E4%BC%B0.md) 进行源码级交叉评审，明确已实锤问题与原评估遗漏的底层瓶颈；
  2. 给出面向“极致性能与观影体验、可改造任何底层”的 **Layer 0 → Layer 4** 全栈重构蓝图、量化验收门槛与停止条件。

---

## 1. 对 GPT 评估文档的源码级交叉评审

GPT 文档的核心判断完全成立：**当前系统远未触及 M1 Pro (32 GiB) 的硬件上限**。但结合仓库真实源码与 `engine/.venv` 已安装依赖，还存在多处关键的源码级黑洞与架构取舍偏差。

### 1.1 GPT 抓准且已在本机源码 100% 实锤的问题

| 问题 | 真源位置 | 源码实锤事实 |
| :--- | :--- | :--- |
| **`mlx-audio` 重复预处理与频繁清缓存** | [qwen3_asr.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/.venv/lib/python3.12/site-packages/mlx_audio/stt/models/qwen3_asr/qwen3_asr.py#L974-L1095) | `_generate_single_chunk()` 首 token 后为计算 `prompt_tokens` 再次调用 `_preprocess_audio()` 与 `_build_prompt()`；`stream_generate()` 内部连续 3 次 `mx.eval()` + 3 次 `mx.clear_cache()` 打断 Metal 缓冲池复用。 |
| **收音消费被 GPU ASR 串行阻塞** | [local_pipeline.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/local_pipeline.py#L331-L350) | `accept_pcm()` 在 CPU VAD 释放切片后直接 `await` 挂在 `mt_pool` 上的 GPU ASR `decode()`，等待期间暂停向 VAD 投喂后续 PCM。 |
| **`max_speech_duration=4` 非硬切** | [local_pipeline.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/local_pipeline.py#L47) | `sherpa-onnx` 仅在超过 4s 后放宽静音判停条件，连续对白仍攒出 `14.13s / 20.82s` 超长片段（D1 45s 仅出 3 条 82/48/118 字长字幕）。 |
| **Chrome 验证脚本选择器错误** | [verify_chrome_1080p_4k.mjs](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/verify_chrome_1080p_4k.mjs#L148) vs [content.ts](file:///Users/luo/Documents/github/jp-live-video-tanslate/extension/src/content.ts#L39) | 脚本在 Shadow DOM 中查 `div`，而产品真实字幕节点为 `span`，导致 `chrome_e2e_summary.json` 中 `captions: []`。 |
| **锁文件漂移** | [pyproject.toml](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/pyproject.toml#L7) vs [uv.lock](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/uv.lock#L398-L449) | 实际运行环境为 `mlx==0.32.4`、`mlx-audio==0.6.7`，而 `uv.lock` 仍记录 `mlx==0.31.2` 且缺少 `mlx-audio`。 |

### 1.2 GPT 遗漏或低估的 6 个硬核底层黑洞

1. **【黑洞 1】`qwen3_asr.py` 每次前向都做 CPU 同步 + Python `for` 循环逐 token `mx.stack`**
   - 位置：[qwen3_asr.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/.venv/lib/python3.12/site-packages/mlx_audio/stt/models/qwen3_asr/qwen3_asr.py#L660-L694)（`_build_inputs_embeds`）。
   - 源码将 `audio_token_mask` 拉回 CPU（`np.array(flat_mask)`），再用 **Python `for` 循环遍历整条序列（20 秒音频约 300+ 位置）**，逐个 append `audio_features[audio_idx]` 或 `flat_embeds[i]`，最后对 300+ 个一维张量执行 `mx.stack`。
   - 实际上音频占位符在 prompt 中恒为连续区间，完全可以预计算固定前缀/后缀 embedding，用单次 **`mx.concatenate([prefix_emb, audio_features, suffix_emb], axis=0)`** 在 O(1) 图节点、零循环、零 CPU-GPU 同步下完成。
   - 同文件 `AudioEncoder.__call__`（[qwen3_asr.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/.venv/lib/python3.12/site-packages/mlx_audio/stt/models/qwen3_asr/qwen3_asr.py#L354-L430)）中还存在多处 `np.array(...)` 同步、未使用的死代码 `padded_mask_after_cnn`（L405-L409），以及 CPU `WhisperFeatureExtractor` 计算 STFT/Mel。

2. **【黑洞 2】`Qwen3Translator` 每句重复 prefill 固定 `SYSTEM_PROMPT`（未启用 MLX Prompt KV Cache）**
   - 位置：[local_pipeline.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/local_pipeline.py#L141-L191)。
   - `SYSTEM_PROMPT` + 用户指令模板（约 95 tokens）每句完全不变，当前却每次从头 prefill。
   - 已安装的 `mlx_lm==0.31.3` 原生提供 `make_prompt_cache`、`trim_prompt_cache` 与 `generate_step(..., prompt_cache=...)`。预计算并复用固定前缀 KV Cache 后，每句仅需 prefill 10–30 个日文 token，且为后续极低成本引入“前 1 句微语境纠错”奠定基础。

3. **【稳定性硬雷 3】Seek 必撞 `busy` 断流的确定性竞态（不可中断 GPU 推理）**
   - 位置：[app.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/app.py#L286-L319)、[local_pipeline.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/local_pipeline.py#L356-L366)、[offscreen.ts](file:///Users/luo/Documents/github/jp-live-video-tanslate/extension/src/offscreen.ts#L134-L141)。
   - 用户 seek 时，旧 WS 断开触发 `LocalSession.aclose()`，其中 `await asyncio.shield(work)` 必须死等正在执行的 1.5s–2.5s GPU ASR/MT 跑完才能释放 `leased = False`；而扩展收到 `busy` 仅在 500ms 后重试一次。只要 seek 落在 GPU 剩余耗时 >500ms 的窗口内，字幕即报错中断。
   - 根治方案：**底层统一下沉至逐 token `generate_step`，每步检查协作式取消事件（~15ms 内中止废旧计算）并支持新连接抢占/快速释放 lease**。

4. **【稳定性硬雷 4】入口 4 秒硬缓冲 + 阻塞消费易撞 `overloaded` + 同步盘 trace 干扰**
   - 位置：[app.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/app.py#L21-L23)（`QUEUE_FRAMES=20` 即 4.0s，`QUEUE_TIMEOUT=1s`）及 [app.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/app.py#L71-L74)。
   - 若某段遇到长 ASR + MT + 假名修复二次生成（合计 >5s），串行阻塞的 `accept_pcm()` 会直接撑爆入口 4s 队列并触发 `overloaded` 杀会话。同时 `Connection.record()` 在事件循环主线程里每 200ms 同步 `open/write/close`，需改为内存缓冲 + 异步刷盘。

5. **【体验硬顶 5】前端单槽硬覆盖 + 4 秒死定时，与“降延迟/短切分”直接冲突**
   - 位置：[content.ts](file:///Users/luo/Documents/github/jp-live-video-tanslate/extension/src/content.ts#L77-L84)。
   - 当前只有一个 `<span>` 且固定 `setTimeout(..., 4000)` 清空。一旦后端把 15–20s 长段拆为 3–4s 短句，新字幕会瞬间盖掉未读完的旧字幕；长句又会在 4 秒被强行清除。**后端短切分必须与前端双行滚动字幕 + 按字数自适应停留时长同步改造**。

6. **【路线判断偏差 6】`speech.cpp` 双 Metal 争用风险、`TranslateGemma` 与过重的第一阶段门槛**
   - `speech.cpp`（`ggml-metal`）与 `MLX`（`MLX Metal`）各自维护独立的 Metal 命令队列与缓冲池，并发时存在显存双份驻留与 GPU 争用风险；相反，若 `speech.cpp` **纯 CPU（利用 M1 Pro 8 颗性能核）**能跑进 `<350ms`，则可与 GPU 上的 MLX MT 构成**零争用真并行异构架构**；若必须走 GPU，则应评估将其 FastConformer-RNNT 619M 直接移植到纯 MLX 统一调度。
   - `TranslateGemma` 即使修好 EOS，在 Tier 2B 也已把 `パラ言語情報` 错译为 `"语言信息"`，无需再投入；60 分钟长压测与全量新留出集应作为架构定型后的毕业验收，而不应阻塞前期的底层剖析与微基准验证。

---

## 2. 极致优化五层架构蓝图（Layer 0 → Layer 4）

### Layer 0：轻量可复现基建与真实 E2E 剖面（半天落地）
- 同步 `engine/uv.lock` 与 `engine/pyproject.toml`。
- `Connection.record()` 改为内存列表 + 异步批量落盘；为 MLX 推理建立精确同步计时（`mx.synchronize()`），拆解 `vad_ms / queue_wait_ms / asr_mel_ms / asr_enc_ms / asr_dec_ms / mt_prefill_ms / mt_dec_ms / ws_to_dom_ms`。
- 修复 [verify_chrome_1080p_4k.mjs](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/verify_chrome_1080p_4k.mjs#L148) 的 `div` → `span` 选择器，接入 `SUBTITLE_DIAGNOSTICS` 的 `first_pcm_sent` / `ws_received` / `dom_visible` / `dom_removed` 事件链，用**“语义句末 → DOM 首次可见时延”**与**“5 秒内正确可读覆盖率”**作为主验收指标。

### Layer 1：异步三段解耦 + 15ms 级可中断单 GPU 优先调度器
- **彻底解耦收音与推理**：`WS read` → `CPU Silero VAD`（永不阻塞，实时消费每帧 200ms PCM）→ **按音频秒数限制的有界段队列（如 `max 16s`）** → **单线程 MLX GPU 优先调度器（`MT 优先级 > ASR 优先级`）**。
- **Per-Token 协作式取消（Cooperative Cancellation）**：ASR 与 MT 全部下沉至 `generate_step` 逐 token 检查 `cancelled` 标志，断连或新 seek 进来时 **~15ms 内中止废旧 GPU 推理并立即释放 lease**，根治 seek 撞 `busy` 与队列 `overloaded`。

### Layer 2：剥离 `mlx-audio` 黑盒，构建专用极速纯 MLX 内核
在 `engine/` 内实现专用的 `Qwen3AsrFastEngine` 与 `Qwen3MtFastEngine`（硬门槛：**与原路径输出文本/token 严格对齐、零质量回归**）：
1. **ASR 端**：
   - 消除重复 `_preprocess_audio` + `_build_prompt`。
   - 预缓存前后缀 text embedding，用单次 `mx.concatenate` 替代 `_build_inputs_embeds` 中的 `np.array` 同步与 300 步 Python `mx.stack`。
   - 移除中间 3 次 `mx.eval()` / `mx.clear_cache()`，通过 `mx.set_cache_limit()` 稳住 Metal 分配池，清理 `AudioEncoder` 内冗余同步与死代码。
   - **纯 MLX GPU Log-Mel**：直接加载模型内置 `mel_filters`，在 GPU 上完成 STFT/Mel/Log10 归一化，替代 CPU `WhisperFeatureExtractor`。
2. **MT 端**：
   - **固定 `SYSTEM_PROMPT` KV Cache 预计算与 `trim_prompt_cache` 复用**，每句仅 prefill 日文片段。
   - 实测评估 **`Qwen3-0.6B-4bit` 投机解码（Speculative Decoding）**（`temp=0.0` 下数学等价）与 `mx.compile` 固定长度桶的实际收益，仅在有净收益时合入。

### Layer 3：打破“20s 片内等待 + 4s 覆盖”的短切分与双行字幕引擎
- **环形历史缓冲 + 能量/VAD 概率谷强切 + 重叠去重**：保留 `Silero VAD` 对正常停顿（`350–450ms`）的切分；当连续语音超过 `3.5–4.5s` 时，回看最近 `1.0s` 内的语音概率与短时能量，在局部谷底强制切出前段，并为后段保留 `300–400ms` 左侧声学重叠，解码后做边界短语去重。
- **低成本前 1 句微语境纠错**：依托 Layer 2 的 MT KV Cache，在长段拆短后附带上一句已定稿短台词（≤30 字）作为纠错参考（须通过 A3 C1 零抄写回归）。
- **双行滚动 + 自适应停留 UI**：改造 [content.ts](file:///Users/luo/Documents/github/jp-live-video-tanslate/extension/src/content.ts) 为 2 行滚动字幕槽（上行已定稿前句 + 下行当前句），按字符数动态计算停留时长（如 `clamp(2.2s, chars * 0.16s, 6.5s)`）；视后续延迟实测决定是否增加可选 `caption_draft` 流式推送。

### Layer 4：准确率天花板精准探针（实测决策，不撒网）
1. **`ReazonSpeech-NeMo-v2` (619M) `speech.cpp` 探针**：
   - 同口径对比 `Q8_0`（Metal vs 纯 CPU 4/8 线程）与 `F16` 的 D0/D1 CER、P50/P95、RTF、RSS 及与 MLX `Qwen3-4B` 并发时的资源争用。
   - 若纯 CPU `speech.cpp` 能跑进 `<350ms`，可与 GPU MT 形成零争用异构并行；若依赖 Metal 且争用明显，则评估将其 FastConformer-RNNT 权重直接移植为纯 MLX 模型纳入单 GPU 调度器。
2. **`Ruby-ASR 1.7B` 探针**：同为 `Qwen3-ASR` 主干，可直接复用 Layer 2 极速引擎，验证日语影视对白上的稳定净收益及注音/长度上限处理。

---

## 3. 执行顺序、量化门槛与停止条件

| 阶段 | 核心交付 | 硬性通过门槛 | 停止/回退条件 |
| :--- | :--- | :--- | :--- |
| **Step 1（本轮）** | MLX 全阶段耗时剖析 + `A1–A4 / M1–M3` 底层提速微基准 + `speech.cpp` NeMo-v2 探针 | 精确拆出每一毫秒耗时；优化内核与原输出文本/CER 一致；产出 `NeMo-v2` CPU/Metal 实测数据与并发争用比对 | 若某项微优化（如投机解码或纯 MLX Mel）无净提速或引起 CER 退化，立即剔除该项 |
| **Step 2** | 落地 Layer 1（异步解耦 + 15ms 可中断优先调度）+ Layer 2 胜出极速内核 | `seek` 0 次撞 `busy`；长密对白 0 次 `overloaded`；纯计算端到端 P95 显著下降且 80+ 单测全过 | 若跨线程/取消逻辑出现任何 Metal 采样漂移或 KV Cache 污染，立即回退检查单线程边界 |
| **Step 3** | 落地 Layer 3（能量谷/重叠短切分 + 双行自适应字幕 UI）+ Layer 4 胜出 ASR 接入（视 Step 1 结论） | 修复后 Chrome 1080p/4K E2E **句末→DOM 可见 5s 覆盖率**显著提升，无丢帧与可读性回归，最终通过长时播放压测 | 若短切分导致漏句或严重错译上升，调优重叠窗口或中止流式草稿 |
