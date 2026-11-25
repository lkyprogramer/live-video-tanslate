# Stage 6：《重启人生》EP01 全集真浏览器深度验证方案（2026-11-25）

> **状态**：方案定稿，待分阶段执行（Stage 0 前置修复 → Stage 1–3 全集真浏览器实测与三段归因 → Stage 4 验收决策）。

---

## 1. 背景与问题诊断（源码复核结论）

Stage 5 已完成 ANE + Metal GPU 物理异构并行改造并在 D0/D1 45 秒切片与 1× WebSocket 回放上验证了低延迟与低 CPU 占用（[Stage 5 实测报告](stage5-ane-heterogeneous-verification-2026-11-24.md)）。但真实观影体验表明，**“切片结束到 WebSocket 发包更快”并不等同于“完整日剧播放实时跟嘴且翻译准确”**。对照最新 `main` 源码与 [stage5a2_live_verification.json](experiments/stage5a2-2026-11-24/stage5a2_live_verification.json)，差距来源分为两类：

### 1.1 已复核属实的代码缺陷（需在长测前作为阶段 0 先修）

1. **ANE keepalive 导致暂停或会话结束后仍每 1.5s 后台空转**：
   - 在 [nemo_ane.mm](../engine/native/nemo_ane.mm#L762-L786) 中，`keep_loop()` 每 1.5s 检查 `idle > 180000.0` 并调用 `eval_bucket()`，而 `eval_bucket()` 第 762 行又把 `last_use_ms` 重置为 `now_ms()`。
   - 结果：`idle > 180000.0` 永远不成立，视频暂停或字幕关闭后后台仍持续执行 bucket 0 编码器推理。且 Stage 5A-1 原型实测已证明 M1 Pro ANE 2s 间隔无显著唤醒惩罚（`cold_wake` 中位数 `32.53ms` vs `keepalive` `34.91ms`）。
2. **ANE 回退 MTL0 GPU 后调度器未切回串行**：
   - 在 [local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py#L1207-L1208) 中，`LocalBackend.uses_ane_asr` 在初始化时仅凭配置 `profile.asr.device == "ane"` 静态判定。
   - 运行时一旦遇到缺失 ANE 资产（[L676-L682](../engine/src/subtitle_service/local_pipeline.py#L676-L682)）、超过 10.24s 的切片触发 `_AneFallback` 或 ANE `retired`（[L785-L796](../engine/src/subtitle_service/local_pipeline.py#L785-L796)），`SpeechCppNeMoRecognizer` 会回退到 `speech.cpp` MTL0 GPU，但 `LocalSession` 仍运行并发 `_run_ane_asr` + `_run_ane_mt_scheduler`，重新引发 ASR 与 MLX MT 争抢 Metal GPU。
3. **关闭/Seek 路径上的 KV Cache 跨线程竞态与线程 detach 风险**：
   - 在 [local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py#L1792-L1800) 中，`LocalSession.aclose()` 在 `await asyncio.shield(self.mt_work)` **之前**即调用 `self.translation.close()`，而后者会 `_reset_cache()` 裁剪共享的 MLX `Qwen3` KV cache，存在与仍在运行中的 `mt_work` 并发写同一 KV cache 的风险。
   - 在 [nemo_ane.mm](../engine/native/nemo_ane.mm#L886-L897) 中，`nemo_ane_destroy()` 等待 2000ms 超时后执行 `keep_thread.detach()` 并立即 `delete ctx`，若保活线程尚未退出，服务退出时存在释放后访问风险。

### 1.2 真实日剧“实时性与翻译准确性”体感差距的 5 个结构性原因

1. **指标口径遮蔽了观影体感**：`491ms` 仅衡量 `VAD 切片结束 -> WebSocket 发包`；观众感知的是从 **角色开口（官方日文 SRT `cue_start`）到浏览器 DOM 画出可读中文（`dom_visible`）**。当前 Draft 需等待 `min_speech_s >= 1.4s`（[local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py#L317-L320)）+ 从句/字数门控 + ASR + MT，首字实际落在开口后 `2.1s–3.4s`，定稿常在整句说完后 `0.8s–1.2s` 才上屏。
2. **Draft 频繁推翻语义**：日语 SOV 语序谓语在句尾，强行对残句出草稿会导致同一句话在数秒内反复改写甚至核心意思反转（如 D1 样本中“保育员对来接孩子的家长说‘欢迎回来’”连续改写 4 版仍落成“让接孩子的家长来取孩子的”）。
3. **日剧口语/抢话上游 ASR CER 偏高**：D0 讲座 CER 为 `15.85%`，而 D1 日剧对话 CER 达 `29.04%`（`そのまま仕事に行くからだ` → `そのまんま絞った`、`いってらっしゃい` → `スクープ`）。上游听错后下游无法补救。
4. **VAD 切分破坏日语句界**：日剧快节奏对白句间距极短（~60–100ms），`vad_pause_ms = 350` 易把两人接话合并，直到撞上 `max_segment_s = 4.5s` 能量谷强切，把句尾动词/否定词切到下一段。
5. **翻译上下文窗口过短且频繁硬归零**：[local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py#L1086-L1094) 将 `context_turns` 限制为 `2`，且 `turns >= 2`、KV 增量 `> 180` tokens 或静音 `> 5.0s` 就整段清空 KV cache，导致连续日剧对白主语指代与语气承接频繁断裂。

---

## 2. 基准测试素材与参考中文字幕核验结论（已实测确认）

本机《重启人生》EP01 三件套完整齐备，已通过脚本完成文件完整性、时间轴对齐精度与字幕文本质量核验：

| 素材角色 | 本地路径（不入库） | 实测规格与质量结论 |
| :--- | :--- | :--- |
| **测试视频** | `/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.mkv` | `1,013,146,782` bytes（~966 MiB），1080p H.264 + AAC 2.0，总时长约 `45.3` 分钟 |
| **权威日文时间轴真值** | `/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.srt` | `68,704` bytes，`utf-8-sig`，共 **`736` 条 cue**（`00:00:13.948` – `00:44:49.054`），含 `17` 条纯 `♪～` 音乐标注与 `719` 条非音乐 cue（含说话人 `(麻美)`、叠音 `>>`、旁白 `⟨...⟩`） |
| **黄金中文译文参考** | `/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.chi.srt` | `59,063` bytes，`utf-8-sig`，共 **`804` 条 cue**（`00:00:13.813` – `00:45:22.073`），`0` 假名残留、`0` 样式标签、`0` 繁体字命中 |

### 2.1 `chi.srt` 时间轴与文本质量核验详情

- **台词时间重叠覆盖率**：`719` 条非音乐 JP cue 中，**`705` 条（`98.1%`）** 与 `chi.srt` 存在直接时间重叠；未重叠的 `14` 条全部为纯拟声/音效/叹词/曲名（如 `(ドアが閉まる音)`、`(車の急ブレーキ音)`、`あ～｡`、`♪～ “ﾎﾟｹﾍﾞﾙが鳴らなくて”`），**真实可翻译对白覆盖率接近 100%**。
- **起播时间差 `start(ZH) - start(JP)`**：中位数 **`+0.050s`**、均值 **`+0.104s`**；`|delta|` **P50 = `0.365s`**、**P90 = `1.571s`**（主要源于双语分句粒度差异：804 条 vs 736 条）。
- **全集漂移检查**：
  - `0–15 min` 中位差 `-0.020s`（`|delta|` P90 `1.362s`）
  - `15–30 min` 中位差 `-0.048s`（`|delta|` P90 `1.658s`）
  - `30–45 min` 中位差 `+0.242s`（`|delta|` P90 `1.755s`）
  - 结论：无帧率/时间基漂移。**延迟基准一律以官方日文 `.srt` 为准，`chi.srt` 作为翻译语义黄金参考。**
- **译文质量与预处理规则**：
  - 译文为高准确度人工精翻/流媒体级简体中文字幕（带台译简中用词习惯，如 `阿嬷/阿公`、`市公所`、`是喔`、`少跟我装熟`），人物称谓前后一致（`麻美`、`小遥`、`玲奈`、`洋子老师`）。
  - **规则 1（过滤屏幕花字）**：`chi.srt` 中包含 `6` 条纯括号屏幕注释（如 `(北熊谷市公所)`、`(服务台)`、`(你要是敢外遇 我就讲出去)`）及少量行内花字注释；评测时先剥离括号花字，避免将无声画面字计为漏译。
  - **规则 2（锁定正片主窗口）**：主对齐与评测区间固定为 **`00:00:00 – 00:44:50`**（正片）；片尾 `44:49 – 45:22` 的 `©NIPPON TV` 与下集预告（`chi.srt` 多出 `14` 条 cue 而 JP SRT 未收录）单独切出，不计入主指标对齐。
  - **规则 3（语义等价优先于字面重合）**：采用**语义等价性 + 结构化错因分类（`E1–E6`）**作为主评判标准，`chrF++` 仅作辅助趋势参考，防止 `阿嬷/奶奶`、`市公所/市政府` 造成虚假扣分。

---

## 3. 阶段 0：前置代码缺陷修复与对照补齐（首步执行）

在启动 45 分钟全集实测前，先完成以下最小必要修复与离线消融对照，确保长测反映干净的系统行为：

1. **修复 ANE keepalive 与安全销毁（[nemo_ane.mm](../engine/native/nemo_ane.mm)）**：
   - 默认关闭 `keep_loop` 后台保活（或改为显式 opt-in 开关，并区分 `last_real_request_ms` 与保活时间，确保空闲/暂停后 ANE 推理归零）。
   - 移除 `keep_thread.detach()` 后直接 `delete ctx` 的路径，保证析构安全阻塞 join，不留释放后访问风险。
   - 在原生层埋点输出单次 `nemo_ane_transcribe` 的 4 段细分子耗时（Frontend / CoreML 预测 / 输出拷贝 / RNNT 解码），为后续定位热点提供真实数据。
2. **修复 ANE 回退 GPU 后的调度协同与真实路径上报（[local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py)）**：
   - 在 `SpeechCppNeMoRecognizer` / `LocalBackend` / `LocalSession` 追踪真实生效的执行后端（`ane` vs `gpu_fallback`）及 `fallback_reason`，并写入服务端 bench trace。
   - 若启动期 ANE 资产缺失/加载失败，或运行期 ANE `retired`，会话自动切换至已验证的 GPU 串行优先调度策略，杜绝回退后重新出现 ASR 与 MT 争抢 GPU。
3. **修复 `LocalSession.aclose()` 生命周期顺序（[local_pipeline.py](../engine/src/subtitle_service/local_pipeline.py)）**：
   - 统一收紧为：`设置 cancelled -> 取消任务 -> 等待 in-flight asr_work / mt_work 完成 -> 调用 translation.close()`，彻底消除 Seek / 暂停 / 关会话时与 MLX KV cache 的跨线程竞态。
4. **定向回归单测（`engine/tests/`）**：
   - 覆盖暂停/空闲后无 ANE 后台调用、强制注入 ANE 失败触发 GPU 回退并验证调度切换、MT 进行中立即关闭会话无 KV cache 竞争。
5. **补齐 `GPU + greedy` 消融对照**：
   - 在 D0 + D1 冻结切片上补跑一次 `speech.cpp MTL0 GPU + greedy`，将“ANE 硬件卸载”与“beam → greedy 解码切换”的各自延迟/CER 贡献拆解清楚。

---

## 4. 阶段 1：《重启人生》EP01 完整 45 分钟真浏览器端到端实测（一次性跑完）

复用仓库现有双端诊断基础设施——扩展诊断构建 `SUBTITLE_DIAGNOSTIC_DIST`（[extension/build.mjs](../extension/build.mjs)、[worklet.ts](../extension/src/worklet.ts#L50-L56)、[offscreen.ts](../extension/src/offscreen.ts#L53-L63)、[content.ts](../extension/src/content.ts#L54-L90)）与服务端 `SUBTITLE_BENCH_TRACE`（[app.py](../engine/src/subtitle_service/app.py#L62-L89)），基于 [verify_chrome_1080p_4k.mjs](../p0/tier2_mt/verify_chrome_1080p_4k.mjs) 扩展全集自动观测脚本。

### 4.1 实验准备与采集边界
- **容器无损封装**：使用 `ffmpeg -c copy` 将 `EP01.mkv` 无损封装为临时 `.mp4`（不重编码音视频，保持原始 1080p H.264 + AAC 流与时间基）。
- **诊断构建**：构建临时 `SUBTITLE_DIAGNOSTIC_DIST` 目录，完整记录 `first_pcm_sent`（含 `estimated_first_input_t` 用于将音频样点精确映射回 `video.currentTime`）、`ws_received`、`dom_visible` 与 `dom_removed`。
- **遥测范围（按用户确认边界执行）**：**仅采集进程级 CPU%（均值/峰值）、RSS（MiB）、wired memory 增量、Chrome `droppedVideoFrames` 以及分段延迟漂移**；**不执行 `sudo powermetrics`**，在最终报告中将整机功耗与温控指标明确标注为“按范围排除，未运行”。

### 4.2 实测场景（一次运行连续覆盖）
1. **Run A — 完整 45.3 分钟 1× 连续播放（约 46 分钟墙上时间）**：
   - 真实 Chrome 打开 1080p 剧集从 `00:00` 连续播放至结束，挂载诊断版插件与真实 `m1-pro-nemo` 服务，全自动无干预。
   - 逐条记录全部 `caption_delta` 与 `caption` 的服务端事件（`vad_released` / `asr_start` / `asr_end` / `asr_text_ja` / `mt_start` / `mt_end` / `mt_text_zh` / `ws_sent` / `gateway_filtered`）及浏览器端事件（`ws_received` / `dom_visible` / `dom_removed` / `video.currentTime`）。
   - 每秒采样一次服务端与 Chrome 进程组 `CPU%`、`RSS` 与 `video.getVideoPlaybackQuality()` 掉帧数。
2. **Run B — 交互切换与暂停待机稳定性验证（约 10 分钟）**：
   - **随机 Seek × 10**：覆盖密集抢话段（`04:35`）、群像对话段（`12:05`）、带 BGM 对白段、片头曲 `♪～` 段（`00:55–01:22`），核验每次 Seek 后新 epoch 会话快速建连且无旧字幕串台。
   - **倍速播放（`1.25×`、`1.5×` 各 60 秒）**：验证高语速流下是否触发 `overloaded`、背压或尾延迟失控。
   - **快速开关插件 × 5**：验证 `LocalSession.aclose()` 与 `Busy` 重试路径零报错、无泄漏。
   - **暂停静置 3 分钟**：视频暂停期间持续采样服务端 CPU、ANE 调用计数与 RSS，验证已修复的空闲行为（CPU ≈ `0%`，ANE 推理 = `0` 次）。

---

## 5. 阶段 2：全集时间戳对齐与“真实实时性”深度评估

利用 Run A 采集的 `first_pcm_sent` 时钟锚点，将服务端音频样本区间 `[start_sample, end_sample]` 与浏览器 `dom_visible` 时刻映射回权威媒体时间轴，与官方日文 `.srt` 的 `[cue_start, cue_end]` 做全集（`00:00:00 – 00:44:50`）逐句对齐，输出 **P50 / P90 / P95 / P99** 与分布直方图：

| 评估维度 | 定义与计算口径 | 回答的核心问题 |
| :--- | :--- | :--- |
| **真实首字响应延迟** | `T_first_dom_visible - srt_cue_start` | 角色开口说话后，观众眼睛实际等多久才看到第一行中文？ |
| **真实定稿滞后延迟** | `T_final_dom_visible - srt_cue_end` | 角色说完这句后，最终正确字幕还滞后多少秒出现？ |
| **端到端 6 段拆解** | 采集+WS / VAD+凑句等待 / ANE ASR 四细分 / GPU MT / 排队等待 / IPC+DOM 绘制 | 每条延迟到底烧在哪个环节（识别最大真实瓶颈）？ |
| **跨句错位率** | ① 定稿上屏时本句已结束占比 ② 定稿上屏时下一句已开始占比 | 有多少句字幕属于“慢半拍”甚至“串到下一句才出来”？ |
| **草稿抖动与阅读体验** | 单句 Draft 改写次数分布、Draft 与 Final 语义反转率、DOM 实际可读停留 `< 1.2s` 的短闪字幕占比 | 字幕是否在剧烈跳变、误导观众或一闪而过？ |
| **45 分钟长跑漂移** | 按 `0–15m / 15–30m / 30–45m` 分段对比延迟 P50/P95、待翻译队列深度、警告/过滤数、`RSS`、`CPU%`、掉帧数 | 播久了是否出现积压拖尾、内存泄漏或性能衰减？ |
| **BGM / 静音误触发** | 对全集 `17` 段 `♪～` 音乐区间及长静音区间统计误吐字幕条数 | 音乐和无人声画面是否乱出幻觉字幕？ |

---

## 6. 阶段 3：全集翻译准确性评测与三段错误归因

为避免把所有问题混为一谈，采用 **三路对照 + 全集自动指标 + 分层人工深审**：

### 6.1 三路对照设计（`00:00:00 – 00:44:50`）
- **Arm A（真实直播链路）**：阶段 1 Run A 在 Chrome 画面实际显示的 `dom_final_zh` 与引擎记录的 `asr_ja`。
- **Arm B（Oracle-ASR 上限）**：将官方日文 `.srt` 原文直通当前 `Qwen3Translator`（保持 `context_turns = 2`）重跑全集翻译。
- **Gold Reference**：按第 2 节规则预处理后的 `Brush.Up.Life.EP01...chi.srt`。
- **归因隔离逻辑**：
  - `Arm A vs Arm B` 的质量落差 = **ASR 听错 + VAD 断句截断/跨人粘连**造成的损失；
  - `Arm B vs Gold` 的质量落差 = **Qwen3-4B 4-bit 翻译能力 + 提示词 + `context_turns=2` 短记忆**造成的损失。

### 6.2 全集自动统计指标
- **ASR 维度**：全集 CER（对照 JP SRT 清洗文本）、全集漏句率（官方有对白但对应窗口无任何字幕输出的比例）、BGM 幻觉条数、网关拦截数（`translation_invalid` / `adapter_unaligned`）、假名残留数。
- **MT 维度**：Arm A 与 Arm B 相对预处理 `chi.srt` 的辅助相似度（`chrF++`），以及分段准确率变化（核对密集对话段 vs 平缓段差异）。

### 6.3 分层抽样深度审查与 Top 20 案例表
从全集按 5 类典型场景分层抽取 **`100–120` 条代表性 cue**（家庭餐桌快节奏接话、多人群像抢话/KTV 聚会、大段独白旁白、带 BGM 对白、高频人名与专有名词），逐条打标以下唯一主错因：
- `E1`：ASR 听错 / 漏听（如叠音、吞音、同音词错识）
- `E2`：VAD 切分坏边界（多人台词粘连、4.5s 强切把句尾动词/否定切走）
- `E3`：译文语义失真（否定翻反、固定搭配误译、丢核心谓语）
- `E4`：上下文指代与人名漂移（主语省略翻错对象、同一人名译法前后不一）
- `E5`：幻觉与异常过滤（BGM/呼吸声幻觉，或整句被网关判无效吞掉）
- `E6`：Draft 严重误导（草稿早期给出相反或毫无关系的语义再改写）

最终报告交付：**6 类错因量化占比表 + Arm A/B/Gold 完整对比的 Top 20 典型翻车案例明细表**。

---

## 7. 阶段 4：验收门禁与数据驱动的后续优化决策树

在拿到阶段 1–3 全集实测数据后，按明确门禁判别系统状态，并依据主归因结果决定下一轮工程投入：

### 7.1 观影级验收基线参考
- **长跑与待机稳定性**：45 分钟全集无堆积漂移（`30–45m` vs `0–15m` P50/P95 增幅受控）、无 `RSS` 持续泄漏、Chrome 零显著掉帧增长；暂停 3 分钟服务端 CPU ≈ `0%`、ANE 调用 = `0`。
- **实时同步度**：`CueEnd -> Final DOM` P50 `<= 0.7s` / P95 `<= 1.2s`；`CueStart -> 可读首字 DOM` P50 `<= 1.5s` 且无高频反转式 Draft 闪烁。
- **可理解准确率**：全集对白漏句率 `< 5%`；关键语义严重错误（`E1–E5` 中导致理解反转或核心信息丢失）占比控制在可接受观影阈值内，BGM 幻觉接近 0。

### 7.2 按实测瓶颈触发下一轮定向改造（不做无依据微调）
- **若时间瓶颈主导在于 VAD/凑句与 Draft 策略** → 优化 Silero 断句窗口、调整强切保护避开句尾助动词/否定词，或按日语语义完整度重构 Draft 触发门控，不再优先挤压编码器数毫秒耗时。
- **若归因大头为 `E1/E2`（ASR 错听与切分丢失）** → 基于 EP01 实测片段评测不同解码/前端策略（例如 `greedy` vs `beam` 全集代价收益、轻量响度归一化/人声分离、或重叠窗拼接改善），用全集 CER 与错位率共同验收。
- **若归因大头为 `E3/E4`（翻译失真与短上下文遗忘）** → 改进 `Qwen3Translator` 滑动窗口 KV cache 策略（保留关键近期轮次与人名/设定锚点而非每 2 句/5s 一刀切归零），并针对日剧口语优化系统提示词与 Draft 提示约束。
