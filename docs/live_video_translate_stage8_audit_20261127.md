# live-video-tanslate：Stage 8 最新代码、验证可信度与性能上限审计

- 审计日期：2026-11-27，北京时间。
- 固定版本：a7b039685704d07a40ea55a5095b8f30d1be4202，提交于北京时间 2026-11-27 16:09:18。交付前再次检查远端，HEAD 未变化。
- 项目：[lkyprogramer/live-video-tanslate](https://github.com/lkyprogramer/live-video-tanslate)。
- 目标机器：用户提供的 M1 Pro，10 核 CPU / 16 核 GPU / 32 GiB 统一内存。
- 范围：只读审查、受控最小复现、外部一手资料核查；未改生产代码、配置或远端仓库，未下载模型。
- 证据标记：**[仓库实测]** 指作者提交的报告；**[本轮复现]** 指在本轮环境执行的确定性检查；**[代码推导]** 指静态结论；**[待实机验证]** 指不能当作收益承诺的实验建议。

## 0. 结论先行

**没有证据表明已触及 M1 Pro 的硬件天花板；也没有证明当前模型与自研运行时的准确率已经封顶。当前更准确的定位是：已经显著改善的低资源实时方案，但仍有影响字幕正确性与验收可信度的缺口。可确认仍有软件正确性和质量探索空间，不能预先保证还剩多少硬件提速收益。**

这一轮值得保留的成果包括 KV 账本修复、普通截断终稿阻断、同条字幕预览防回缩、同场景 VAD 对照，以及不让失败的候选模型进入主链路。它不是无效优化。

但本轮发现了以下决定下一步顺序的事实：

1. 普通 length_limit 终稿被挡住，不等于已经发出的流式文字被撤销；失败句和下一句还能共用序号。
2. Kana 修复分支仍能把没有真正 EOS 的截断文本标成有效终稿。
3. T7 会将缺测的生命周期、swap 等数据当作正常值，导致 stability PASS；固定单元的 DOM 延迟计算实际上尚未接通。
4. chrF2 v2 与上游标准实现仍有差异；无效输出依然可能计入“质量分”和正常延迟。
5. 自研 RNN-T 解码器存在值得实测的搜索策略空间；“Oracle 切片错误率”不能直接解释为 619M 权重的不可突破上限。

**推荐顺序：先修终态/身份与评测缺口 → 用现有输出重算、完成小规模可信配对验收 → 深挖当前 ASR 解码和任务适配 → 最后只增加一个真正不同的流式模型候选。**

不建议下一步直接开双 ASR 仲裁、重写完整推理底座、重新扫一遍大模型，或继续宣称“只剩换更大模型”。

---

## 1. 这次具体审查了什么

### 1.1 新增提交

相对于上次审查的 e1542b90ff02c4eefe331d89dafb49bcc3f61693，远端新增：

| 提交 | 内容 | 本轮判断 |
| --- | --- | --- |
| aa3df8c | 指标/ExperimentKey、length_limit 终稿门禁、预览防回缩 | 有实际作用，但流式终态和 repair 路径不完整 |
| eed0434 | 同场景 VAD 2×2 与 max_segment_s 实验 | 纠正了部分旧实验口径；仍只是局部配置结论 |
| be8e3a8 | Index-Translate-2B 隔离首筛 | 隔离方式合理；小样本与有效输出计分需要修正 |
| a7b0396 | T7 聚合、验收器与 Stage 8 报告 | 能做冒烟诊断，暂不能承担严格发布验收 |

代码重点包括 engine/src/subtitle_service、engine/native/nemo_ane.mm、extension/src、p0/stage8_eval、相关测试和 Stage 8 计划/实验报告。

### 1.2 不能混淆的三种证据

- **作者 Mac 上的实测**：本报告中的 120 秒 Chrome、ASR CER、模型耗时等，引用最新提交报告，不是本轮重新在 Mac 上测得。
- **本轮确定性复现**：使用实际 Python 状态机、翻译控制逻辑、FastAPI/WebSocket、TypeScript 预览守卫、聚合和计分函数。MLX token 生成、ASR 和 C ABI 在相应测试中受控替身化，以触发边界条件。
- **尚未完成**：真实 M1 Pro / ANE / Metal 推理、完整 pytest、完整 Chrome 真机验收、跨节目盲评与长时间温度/内存压力测试。本环境缺少部分测试和模型依赖，未擅自安装。

远端未包含可供独立重算的 Stage 8 原始音频、全部逐句输出和完整遥测。因此可以确认代码与报告内容，但不能把作者表格自动提升为第三方重复实测。

---

## 2. 本轮优化的真实收益与边界

### 2.1 120 秒 Chrome 对照：更稳，不是全面更快

[仓库实测] 对照为 B-deployed 7ea1386 与候选版本，不是仅比较上述四个新提交的单因素效果。

| 指标 | deployed | candidate | 可支持的结论 |
| --- | ---: | ---: | --- |
| 发出 / 渲染 final | 31 / 31 | 31 / 31 | 发出与渲染事件计数各为 31，未逐 cue 对齐；不等于固定语义单元 100% 覆盖 |
| streaming 事件 | 76 | 51 | 更新次数减少 |
| streaming 长度回缩次数 | 10 | 0 | 该窗口、该指标改善 |
| streaming 擦除字符 | 74 | 32 | 下降约 56.8%，但并非全部改写消失 |
| ASR P50 | 54.77 ms | 51.75 ms | 接近；单轮不能确认稳定加速 |
| MT P50 / P95 | 302.38 / 592.96 ms | 297.78 / 554.96 ms | 当前短测略好，需重复 |
| segment end → WS P50 | 620.24 ms | 646.63 ms | 中位数慢约 26.39 ms |
| segment end → WS P95 | 1032.27 ms | 987.52 ms | 尾部快约 44.75 ms，单轮未证明统计显著性 |
| 视频掉帧 | 5.372% | 6.801% | 对 deployed 增加 1.429 个百分点；对 subtitles-off 7.237% 又低 0.436 个百分点，背景波动不可忽略 |

还需要限制两句话：

- “10 → 0”是相邻 streaming 更新的长度回缩指标；聚合器遇到 final 会重置相应状态，不能代表 preview → final、内容同长度反转、过期字幕撤销等全部闪烁都消失。
- end → WS 不是“开口 → 正确中文实际可读”的时延。runner 的 requestAnimationFrame 采样是下一次绘制前回调的代理点，也不能无条件叫物理屏幕完成绘制。

所以这轮可以称为**字幕显示稳定性改善**，不能直接称为“所有延迟更低、顺畅追剧验收完成”。

### 2.2 不升级模型的决定，目前仍然合理

[仓库实测]

- Qwen3-4B Q8：654 cue 对照下质量指标收益小，MT P50 从 931.42 ms 到 1080.58 ms，MLX peak 从 2762.39 到 4522.72 MiB。暂不替换 Q4 合理。
- Index-Translate-2B：相同 26 条、clean JP、0 context 的隔离首筛中 P50 为 201.83 ms，对照 Qwen 为 952.64 ms，约 4.72×；但有 2/26 Kana 无效输出，当前证据不足以支持晋级。
- 不能把 Index 的 201.83 ms 与线上 Qwen 的约 298 ms 混为同一实验，也不能用 RSS 与 MLX peak 直接比较总内存。
- Qwen3-ASR-1.7B、Whisper turbo 没在当前冻结 G3 样本展示足够的质量/成本优势。继续跳过 T6 双 ASR 是合理的。

这些是“当前证据下不晋级”，不是证明候选在所有节目、所有解码配置下永远更差。也不应因为评分器有问题就推倒一切，重新下载跑模型全集；先重算已有输出。

---

## 3. 运行时缺陷：已经复现，建议先修

优先级表示本项目修复顺序，不是安全漏洞等级。R3–R5 属于当前 HEAD 仍存在的旧边界问题，不能都归因于最新四个提交。

### R1 — 高优先：无效终稿没有结束可见 cue，下一段还能复用序号

**位置**：

- local_pipeline.py：_run_cached_prompt 的流式 on_delta；LocalSession.translate 的无效终稿返回。
- app.py：Connection.emit，delta 使用 self.seq + 1，只有成功 Caption 才推进 seq。
- preview_guard.ts：同 seq 的长 preview 会压住较短 final_token_stream。

**[本轮复现]**

用实际 Qwen3Translator、LocalSession 和实际 localhost WebSocket 路由，令 A 生成至 token 上限且没有 EOS，B 正常完成：

~~~text
A: stop_reason=length_limit, valid=false, gen_tokens=256
A: caption_delta(seq=1, start_sample 对应 10)
A: 没有 final，也没有 cue 级撤销
B: caption_delta(seq=1, start_sample 对应 30)
B: caption(seq=1, start_sample 对应 30)
~~~

实际 TypeScript 守卫同时确认：上一句长 preview 与下一句短 stream 复用 seq 时会拒绝更新；换成新 seq 则接受。

**影响**：截断文字已经到达客户端，随后却没有明确终态；旧 preview 可残留、下一句的早期 token 可被误认为回缩。这里不是要求“流式输出必须等 EOS”，而是要求每个可见 cue 都能正确完成或撤销。

**为什么现有测试没发现**：新增终稿拦截测试中的假翻译器不调用 on_delta，只证明最终 Caption 被挡住。

**最小修复方向**：

1. cue 首次可见前分配稳定身份，包含 session/epoch 与 segment/span generation。
2. preview、final_token_stream、final、discard 都绑定这一身份。
3. 无效终态不能 silent-drop，按身份撤销临时内容并记录原因；不要因单句失败全局清空下一句。
4. 合法 EOS 后的 final 仍可纠正此前错误草稿。

**验收用例**：A preview → A stream → A length_limit → B short stream → B final；A 后断连；seek 后迟到 A；连续两个失败句。必须无跨句串写、无旧 epoch 上屏，每个已可见 cue 有且只有一个终态。

### R2 — 高优先：Kana repair 分支可把另一份截断伪装为 EOS

**位置**：local_pipeline.py 的 _run_prompt（约 1151–1191）与 translate 的 repair 接受分支（约 1431–1444）。

_run_prompt 只返回字符串，无法区别真正遇到 EOS 与循环耗尽 token 上限。调用者看见非空且无 Kana 的修复文本，会强制标 valid=True / stop_reason=eos。

**[本轮复现]**

~~~text
原始生成：1 token 含 Kana，然后 EOS
repair：连续 256 个中文 token，没有 EOS
实际 WS：发送 256 字最终 caption
trace：stop_reason=eos, valid=true, gen_tokens=1
~~~

gen_tokens 还停留在原请求，既影响正确性也污染耗时/输出长度归因。

生产有 Kana token 屏蔽，这证明的是**可达的逻辑缺口**，不证明发生频率很高。

**最小修复方向**：repair 也返回结构化 TranslationResult，携带自身 stop_reason、eos_seen、token_count、latency；只有真实 EOS 且通过有效性判断才提交。父请求与 repair 的统计应分开，再给总耗时。

**验收用例**：repair EOS / token limit / cancel / exception / empty / 再次 Kana；不允许凭“字符串非空”重写 EOS 事实。

### R3 — 中高优先：队列背压等待被下一条正常消息提前打断

**位置**：app.py 的 Connection.stream.read（约 193–207）。

队列满时，同时等待当前帧入队和下一条 WebSocket 消息，原意是及时感知断连；但如果先来的是普通音频或 finish，只要当前帧尚未入队，就立即 overloaded。

**[本轮复现]** 将队列容量在诊断进程中缩小到一帧、消费者 75 ms，实际代码约 0.2 ms 即 overloaded，配置的 QUEUE_TIMEOUT 为 1 秒。它没有等到约定的 deadline。

生产触发需要实际积满缓冲；本轮没有证明正常 1× 播放会频繁触发。

**最小修复方向**：只把断连当立即终止；暂存至多一个已收到的合法后续消息，继续等 admission / 剩余 deadline。保持有界缓冲，禁止用无限积压掩盖问题。

**验收用例**：deadline 内排空应成功；超时应失败；等待时 disconnect 立即退出；等待时 finish 不丢已接受尾帧。

### R4 — 中优先：ANE → GPU fallback 把正确去重后的空串又换回旧文本

**位置**：local_pipeline.py 的 SpeechCppNeMoRecognizer.decode（约 959–977）。

有时间戳时，全部 token 属于已处理重叠区，过滤结果应为“没有新内容”。当前 if not text 又读取完整原文，重新引入旧字幕。

**[本轮复现]** 实际 decode + mock C ABI：唯一 token 中点为 10320，去重线为 12500，过滤得到空串，最终返回“前の区間”。ANE bridge 对应路径返回空串，两个后端契约不一致。

复现使用 ANE 不可用而进入 GPU fallback 的配置，未在真机上触发运行中 ANE 退役/热切换；确认的是 fallback 路径本身的逻辑缺陷。

**修复原则**：区分“没有可用时间戳”和“时间戳可用，但没有新 token”。

**验收用例**：all-old、partly-new、untimed 三种情况，以及真实 fallback 后相邻字幕不重复。

### R5 — 中优先：共享取消 Event 被下一份任务清除，旧 draft 可能复活

**位置**：local_pipeline.py 的 _run_ane_asr、_run_ane_mt_scheduler、final release；约 1928、1973–2007、2178–2188。

**[本轮复现]**

~~~text
A draft MT 正在执行
→ A final release 把共享取消标志 set
→ A 的重叠 final ASR 为空
→ B draft ASR 开始，把同一个 Event clear
→ A draft MT 迟到返回，看见标志未取消
→ 已取消的旧段 preview 仍被发出
~~~

这是并发时序缺陷，不是模型质量问题。实际调度器配合受控完成顺序可以稳定复现。

**修复原则**：每份 draft 使用不可被其他 generation 清除的取消令牌；提交时再次核对 generation、span 与 final 水位。已有 final 优先机制应保留，不需要新增复杂调度平台。

### 另一个待实机验证的生命周期风险

native 锁等待和 CoreML prediction 分别有较长有界等待，预测中无法即时抢占；连接直到屏蔽取消的 native 工作收回后才释放 lease。前端 busy 重试窗口较短。极端延迟下连续 seek/恢复可能先耗尽重试。

目前只是代码推导，**不列为已发生故障**。应补“native 延迟 + stop/seek + 立即重连”故障注入，再做一次真机验证。

---

## 4. 测试与验证：为什么目前不能接受 stability PASS

### V1 — 阻断发布认证：缺测被当成零故障

**位置**：

- run_chrome.mjs 摘要写出约 518–528；
- aggregate.py 约 268–270；
- acceptance.py 的 G0/G1/G3。

runner 没有写入完整 lifecycle、swap_growth、resource_abort；聚合器填 0 / False / 空对象，门禁进一步把缺失的生命周期计数当零。

**[本轮复现]**

构造实际聚合器可读的 run：

- 宣称 duration=120 秒，实际 current_time=0.25；
- 视频 total_frames=0；
- 无 manifest；
- 无 lifecycle / swap / resource 观测；
- 只有一条 streaming DOM，没有 final。

实际 evaluate_gates 返回：

~~~text
g0_identity_and_dom: PASS
g1_lifecycle: PASS
g3_resources: PASS
stability_verdict: PASS
g2/g4: BLOCKED
~~~

删掉掉帧对照字段，只保留 subtitles_off 角色，G3 仍 PASS。

**注意**：这并不说明作者 120 秒播放造假，或实际发生了 swap/断线；它说明验收器**不能区分“测过且为零”和“根本没测”**。

应将当前结论改为：**短程冒烟观测改善；严格稳定性认证待补测。**

### V2 — 固定单元的 DOM 验收链路尚未接上

aggregate_runs 接收 units，但只保存 hash 与 count，不生成 acceptance 依赖的 availability / dom_latency。增加 units 文件本身不能让真实 G2 工作。

另一面，门禁直接相信 summary 中的质量统计：本轮向报告放入 n=0 的 holdout、“verified=true”与几个质量数字，整体仍可 PASS。这里的测试数据是人工构造的反例，不是对实际报告的指控。

**最小修复契约**：

- 从逐单元 / 逐 cue / epoch 记录生成 coverage 和时延。
- 丢失、超时、无效 final 必须保留在冻结分母。
- 无效/未完成的 final completion latency 使用 +∞ 或明确 null+missing reason；不得混进普通成功时延。
- validate 数据规模、实际播放终点、唯一 run、hash、对照角色和 reviewer 对齐关系。
- 缺测 BLOCKED；测得超限 FAIL；统计不足 INCONCLUSIVE；证据齐全且满足合同才 PASS。
- G2/G4 不允许通过手工填一层 summary 来绕过底层证据。

### V3 — chrF2 v2 仍未与 SacreBLEU 对齐

**位置**：bench_mt.py 的 chrf2_standard_0_100，约 50–83。

当前实现只在 hyp/ref 两侧该阶 n-gram 数量都为零时跳过；SacreBLEU effective-order 只有两边都存在 n-gram 才纳入有效阶。

**[本轮复现]**

| 输入 | 仓库 v2 | 上游有效阶公式 |
| --- | ---: | ---: |
| hypothesis = 我；reference = 我们 | 27.7778 | 55.5556 |

现有测试只要求短不等长文本的分数处于 0–100，不能发现这一差别。

主要影响短句/短块分数，**不能据此说所有长文本 concat 数字都错**。建议直接锁定 SacreBLEU 版本与 signature，停止手写“近似标准实现”；保留 legacy 字段，现有原始输出重算即可。

### V4 — 不可交付的输出还可以得满分和正常延迟

**位置**：bench_mt.py 的 run_mt_cues，以及 run_p2_index2b_screen.py 的计分/adapter 输出路径。

当前会保存 valid=false，却仍以 text_zh 和实际尝试耗时计分。

**[本轮复现]**

~~~text
输入结果：valid=false, stop_reason=length_limit
text_zh 与参考完全相同，latency_ms=20
实际两个计分路径：valid_count=0, chrF2=100, P95=20ms
~~~

这不符合上线产品口径：网关不发布的 final，不能同时按“准确且 20 ms 交付”奖励。

应同时输出三层：

1. diagnostic raw text / attempt latency；
2. valid-only quality / success-only latency；
3. 固定分母的 delivered quality / completion availability / completion latency。

发布判断用第三层，前两层用于找原因。Index live-ASR 空输入也不能通过取交集消失，应保留失败单元。

### V5 — 配对、会话和可重复性还有缺口

- aggregate 对重复 candidate/deployed 角色静默取第一份，容易把错 run 配成一组。
- manifest 可缺失；部分 hash 仅“存在才比较”，不能证明实际 engine、扩展 bundle 和媒体与申报一致。
- trace 对齐未全面按 session 隔离。本轮最小跨会话示例中，真实 B 会话 200 ms 被汇总为 9200 ms；seek/重连长测尤其受影响。
- test_acceptance.py 在模块加载阶段读作者 Mac 的绝对路径，干净 checkout 导入即 FileNotFoundError。
- run_t5_batch2.py 的 --reuse-existing 仍先进入下载/注册路径；注册表写出仅保留部分模型项，会丢掉新增 Index 项。
- ExperimentKey 尚未完整纳入中文参考、确切 cohort、输出上限、scorer 版本；120 和 654 的 M2 key 可相同。复用证据前应校验，而非只看 summary 存在。
- runner 的超时/提前结束必须显式写终态；失败重跑不得复用旧 summary；不要让新 manifest 搭上旧结果。

这些不要求建设“通用大底座”：在现有 manifest、run summary、aggregate 和 acceptance 的几个接口里补齐即可。

### V6 — VAD 实验变好了，但 Oracle 仍不是模型天花板

新 168 cue / 14 scene / N=2566 对照比旧跨口径比较更可信：

| 配置 | CER | S | D | I |
| --- | ---: | ---: | ---: | ---: |
| VAD + pad500 | 0.3192 | 251 | 499 | 69 |
| Oracle + pad500 | 0.2907 | 225 | 497 | 24 |

差距约 2.85 个百分点，错误数相差 73：

- 替换差 26；
- 删除差 2；
- 插入差 45。

因此，剩余错误不能都归咎于“2.8 秒强切导致漏字”。本次更换端点只减少了 2 个净删除错误；没有逐错误配对，无法区分其中哪些删除被恢复、哪些又是新增的，不能把净差当作恢复量。

此外：

- Oracle 使用字幕时间轴，不是逐声学边界绝对真值。
- 当前 hybrid 会按输出字数/音频时长触发 beam，并倾向保留更长的候选；改窗长会改变触发条件，不只是边界。
- pad 会影响输入长度、特征归一化与 Core ML bucket，不能简单把所有差值叫“纯边界损失”。
- scene 外 orphan chunk 在当前 _score_scenes 中记录但不进入主要 CER；需人工区分字幕漏标与模型幻觉，不能直接忽略。
- 0.3082 来自旧 60 单元 G3，0.2907 来自新 168 cue 场景，不是同分母的再次提升。
- 3.6/4.0/4.5 秒在该 sweep 更差，支持保留默认值，不等于所有分段策略已探索完。

---

## 5. 到底到没到天花板：拆开回答

| 层次 | 当前证据 | 判断 |
| --- | --- | --- |
| M1 Pro 硬件算力 | 没有完整关键路径、内存带宽与长期热状态证据 | **没有证明到顶** |
| 当前短段 ASR 路径 | 相关实验报告 P50 约 40–55 ms，非独立 encoder 计时 | **在这一调用上省时的绝对量级较小；是否关键路径待查** |
| 当前自研 RNN-T 解码质量 | hybrid、beam 合并顺序、fallback 时间戳等仍有待查项 | **没有证明到顶** |
| 4B 翻译能力 | Q8 不值得晋级，Index 首筛不通过；不等于领域适配无空间 | **目前组合受限，但不是硬件上限** |
| 端到端字幕体验 | 防回缩有效；错误终态、准确性留出和真实可用时延仍不完整 | **尚不能认证稳定顺畅追剧** |

### 5.1 资源平均空闲，不等于单条请求可以等比例加速

例如“MT 花 335 ms、每 2.5 秒一段”得到的是任务占空比估算，不是 GPU 算力利用率实测。请求间 GPU 空闲，与请求内部受内存读取/同步限制可以同时存在。

同理，不能拿 M1 Pro 的标称 200 GB/s 直接断言 30B-A3B 跑到 300–450 ms；活动参数、路由、权重量化、上下文、输出 token 数、runtime 与 Chrome 共存都影响结果。

### 5.2 日语语序造成等待，但“1.9 秒物理下限”没有被证明

有些否定、被动、授受关系需要等到后文才能安全翻译，这是真约束。它是随句子和可接受改写程度变化的信息约束，不是所有台词统一 1.9 秒，也不是 2.3 秒首字永远不能降低。

要测“首次正确可用内容”和“最终稳定内容”两种时延，并记录改写/语义反转，不能只优化第一个字符出现时间。

### 5.3 不应再把硬件优化等同于单算子倍速

单次 ASR 中位数约 52 ms，即使孤立地减半也只是该调用约 26 ms 的收益；如果它不在关键路径，用户感知可能更小。不能用中位数相加减重构请求关键路径，此处只是量级说明。

更可能改善实际体验的是：不展示/保留错误临时字幕、少重复计算、减少无效请求、保证定稿质量，以及只对困难片段投入更多计算。

---

## 6. 仍值得投入的底层方向：少而明确

### 6.1 首选：验证自研 ASR 实现与参考实现的等价性

这是此前容易被“Oracle 仍然错，所以模型听不懂”掩盖的路径。

先选 20–30 个现存困难片段加正确样本，不换权重，核对：

1. PCM 声道、采样数与重采样输出；
2. log-mel、归一化、有效帧数、padding 和 mask；
3. Core ML encoder 输出与参考 encoder 的误差；
4. 相同 encoder 输出送入参考 decoder 和当前 C++ decoder；
5. greedy、beam、hybrid 的 token、分数、时间戳和失败类型。

先将“听觉权重限制”与“特征/转换/搜索限制”分离。只有参考实现同样错，且合法的解码配置改善已很小，才有资格缩小为当前权重的能力边界。

不能为了修安静说话人就盲目删除 CMVN 或改变预训练归一化；先诊断幅度、声道和混音条件。

### 6.2 一个具体的新实验：RNN-T 先合并同前缀，再做 beam 裁剪

**[代码推导，待实测，不列确定 bug]**

nemo_ane.mm 约 710–724 当前先对 expanded 路径排序、截断到 beam，再把相同 labels 用 logaddexp 合并。

数值例子，beam=2：

| 扩展候选 | 示例概率质量 |
| --- | ---: |
| A 路径 1 | 0.19 |
| A 路径 2 | 0.19 |
| B | 0.24 |
| C | 0.23 |

先裁剪得到 B、C；先合并得到 A=0.38，再保留 A、B。另一种情况是两条 A 路径占满 beam，合并后只剩一个唯一前缀。

这里的 expanded 候选可能已经包含前面步骤合并的概率质量，不一定是单一原始 alignment；数值例子仅用于说明裁剪与合并的顺序不交换。

这说明当前策略有**搜索质量探索空间**，不证明实现违反所选参考算法：有些实现刻意保留 top-B 路径再合并。

实验约束：

- 只合并同一迭代、相同 labels、相同声学时间索引的等价状态，不能跨时间粗暴合并。
- 保留并核对 blank 概率、每父节点 top-k、长度归一化、tie-break、max_labels 和独立 finished 选择。
- 保留代表路径的 frames/timestamps 契约；这些时间戳还用于 overlap 去重。代表候选选择及平分规则应显式固定。
- 使用同一 encoder 输出，分别比较 greedy / 当前 beam / merge-first beam。
- 观察 CER、专名/数词、删除/插入、边界重复、decoder P95；单列 Hybrid 实际触发 beam 的子集，避免把未走新逻辑的片段也归因给它。没有质量收益就不晋级。

这是小范围 native decoder 试验，不是重写整个推理引擎。由于改变搜索路径，应当归入“可改变输出的质量实验”，不能冒充无损 kernel 优化。

### 6.3 N-best 与受限词表：先测可恢复空间，再做 bias

当前 C++ beam 已有 predictor cache，不要把“加 beam/cache”重新当新计划。可以考虑导出少量 N-best 和分数：

- 先看正确答案是否已在候选中；
- 如果在，比较上下文重排与 top-1 的差距；
- 如果不在，仅 rerank 不会凭空听见缺失声音；
- 再评估片源专名、人物名、术语 trie / shallow fusion。

必须加近音干扰和正确样本，限制词表权重，避免把所有声音硬拉向已知角色。后验 N-best rerank 只能在已生成候选中选择；decode-time shallow fusion 可改变搜索和候选保留，但两者都不能当“补写剧情”工具。

NVIDIA 官方有 RNN-T/CTC word boosting 方法可借鉴，但其 GPU 实现不等于可直接搬到 M1 的 ANE 后端。

### 6.4 翻译：固定当前 4B，针对真实错误做任务适配

目前应该优先分离：

- 真人人工日文 → 中文：测 MT 本身；
- 冻结 ASR 日文 → 中文：测传播损失；
- 完整音频 → Chrome：测产品效果。

从既有失败簇中选数字/分数、否定、授受和被动、称谓、专名；同时给等量正确对照与未见节目。

先试少量可解释的词表/提示消融，不能对特定台词硬编码替换。只有相同错误在跨节目持续出现，再考虑小型日→中任务适配或蒸馏；训练集不得包含验收集。是否使用远程训练设备是另一项授权，不属于本次审计执行范围。

M2/Q8、Index-2B 不必立即重跑。它们已有的输出重算即可决定是否值得重新开启；没有新假设就保留淘汰结论。

### 6.5 真正值得保留的一个新架构候选：Nemotron 3.5 Streaming 0.6B

官方模型卡明确包含日语 ja-JP，并使用 cache-aware FastConformer-RNNT。它与不断重算重叠窗口的方案不同：可复用 encoder attention/卷积状态，按新音频块增量处理。

官方提供 80/160/320/560/1120 ms 块长选项，以及 NeMo-Speech.cpp 本地运行路径。这是机制不同的探索理由，不是“模型更大所以更准”。

重要边界：

- NVIDIA/NeMo-Speech.cpp 与当前项目固定的 nyosegawa/speech.cpp 不是同一个 runtime。
- 官方示例性能并非你的 M1 Pro 日剧实测，不能照搬 4090、英文或未明 CPU 的数字。
- FluidAudio 的 Nemotron 多语 Core ML 路线涉及 checkpoint/export 和 ANE 编译兼容差异，不是替换一个模型文件即可用。
- 先核对型号/版本/ja-JP 输出，再小样本离线测试；只有质量有优势且实时预算可行，再做单候选 sidecar 与 Chrome 共存测试。
- 初始不做双 ASR 仲裁，不让实验服务碰生产端口，不下载多个大型模型“陪跑”。

日本语 Parakeet 已在原计划候选中，且 Core ML CTC 制品不能等同原模型的 TDT 或真流式配置；不把它重新包装成已验证的新捷径。

### 6.6 浏览器底层：重采样相位表 + 环形缓冲

extension/src/resample.ts 目前对 16 kHz 的每个输出样本重新计算约 40 tap 的 sinc/余弦窗与权重，并维护可变数组。

静态量级约 64 万 tap 计算/秒，不是已测 CPU 占比。可先用 AudioWorklet profiling 判断：

- 48 kHz → 16 kHz 只有一个固定相位；
- 44.1 kHz → 16 kHz 有 160 个周期相位；
- 可预计算系数，使用定长环形缓冲，减少三角函数与 splice/分配。

必须保留原有 radius、cutoff、窗函数与归一化，使用 PCM golden tests、频响/混叠测试、seek/reset、EOF 和输出采样数验证。浮点累加顺序改变后要定义 int16 等价/容差。

晋级看 4K 播放时 AudioWorklet callback P95/P99、浏览器 CPU、掉帧和音频丢样；没有端到端收益时，不把“算术量少了”当用户可见加速。

### 6.7 ANE 与 Metal：只优化真实关键路径

复用现有 native timings，再按需要补足：

- frontend / encoder prediction / output copy / CPU decoder；
- MLX prefill / decode / repair / draft 浪费；
- GPU fallback 与 MT 锁等待；
- Core ML bucket、编译/预热、CPU fallback、功耗/温度状态。

Core ML 允许某些 compute units，不等于所有算子实际都落到 ANE；规划信息与实际 trace 也要区分。用 Xcode / Instruments 核实设备落点后，再决定是否值得修改静态形状、张量布局、缓冲复用或 decoder 分配。

禁止在不知道热点时批量重写算子，尤其不能为了“ANE 利用率看着低”而人为增加并发。

---

## 7. 建议下一轮只做四个批次

| 批次 | 内容 | 交付和晋级条件 | 不做什么 |
| --- | --- | --- | --- |
| B3.1 正确性收口 | R1/R2；R3–R5 的窄修复与确定性回归 | 每条可见 cue 有终态、无伪 EOS、无跨句/epoch 串写、无去重回退；原成功路径保持 | 不换模型，不改 VAD 默认 |
| B3.2 评测收口 | V1–V5；官方 scorer；现有输出重算 | 缺证据不得 PASS；单位对齐可计算；无效输出保留分母；fresh checkout 可复现 | 不建设新通用平台，不重下全部模型 |
| B3.3 基线确认 | 固定候选，短测多轮+留出+长跑 | 同源 A/B 顺序平衡；至少多轮短对照；独立 15+ 分钟留出；一次完整剧集/45 分钟+尾部静音；按既有冻结阈值判断 | 不在 holdout 上调参，不凭单轮 P95 宣称加速 |
| B3.4 单机制质量实验 | 首先 ASR parity/merge-first/N-best 之一；通过后才考虑新流式候选 | 错例有可解释修复，正确样本不受损，准确率改善在留出成立且 P95 不越合同 | 不同时改模型、窗长、prompt、采样器和 UI |

短对照建议采用 ABBA 或随机顺序，Chrome 音轨、播放速度、分辨率、标签页、冷/热状态固定。subtitles-off 每组保留，异常 run 不得静默删除。已有的阈值可继续使用，本报告不偷偷放宽门槛。

### 可交给开发 AI 的最小任务约束

1. 以审计 SHA 或明确的新基线建独立分支，不直接改运行中的工作目录。
2. 先为 R1/R2 与 V1/V3/V4 写失败测试，再修；优先级最高。
3. 将按会话/epoch/cue 的终态与关键日志扩展到已有协议，不另建消息平台。
4. 旧模型制品、推理参数、数据集、阈值一并冻结；记录每次代码与评估器版本。
5. 修复后只先跑无模型的确定性测试，再跑最小本机集成，最后全链路。
6. 每个实验只改变一个机制；质量改动与等价性能优化分开报告。
7. 不自动推送、不自动下载大模型、不上传音频、不改系统内存/功耗设置。
8. 产出失败原因、原始证据摘要、对照和回滚方式；没有有效证据就 BLOCKED，不帮候选找理由通过。

### 停止条件

- 正确样本出现新的关键否定/授受/数字反转；
- 丢字幕、跨句串写或旧 epoch 提交；
- 将失败单位滤掉才得到延迟改善；
- 长跑出现持续积压、swap 增长或超过冻结资源门槛；
- 优化只改善内部耗时，正确字幕可用时延/准确率没有对应收益；
- 当前数据不足以判断，则停止晋级而不是把“不确定”写成通过。

---

## 8. 本轮实际验证记录

### 8.1 已执行

| 检查 | 执行方式 | 结果 |
| --- | --- | --- |
| 普通截断的 delta/seq 问题 | 实际 Qwen 控制逻辑 + LocalSession + localhost WebSocket，受控 token 生成 | 复现 R1 |
| Kana repair 截断 | 同上，repair 无 EOS | 复现 R2，实际收到 256 字 final |
| 背压等待 | 实际 Connection.stream，缩小队列、受控消费者 | 复现 R3 |
| GPU fallback all-old dedup | 实际 decode + mock C ABI | 复现 R4 |
| draft 取消复活 | 实际 ANE 双调度器 + 确定性任务完成顺序 | 复现 R5 |
| preview_guard | Node 24 直接运行仓库 TS 函数 | 同 seq 短新句被抑制，新 seq 被接受 |
| T7 缺测/零帧/重复角色 | 实际 aggregate_runs / evaluate_gates | 复现误放行与未对齐 |
| 跨会话时延 | 实际 trace 汇总函数 | 应 200 ms，输出 9200 ms |
| chrF2 短不等长 | 实际 scorer 与上游公式 | 27.7778 vs 55.5556 |
| invalid final 计分 | 实际纯函数，隔离可选模型 import | 两路径均可 valid=0 / chrF2=100 / P95=20 ms |
| 已有 aggregate golden 测试 | 直接调用仓库测试函数 | 通过 |
| 固定分母无穷尾延迟 | 实际 nearest_rank_quantile | 通过 |
| runner JS 语法 | node --check | 通过 |
| test_acceptance 导入 | 干净快照直接导入 | 因作者绝对路径失败 |

### 8.2 未执行或不能据此声称

- 没有完整 pytest PASS；
- 没有 M1 Pro/ANE/Metal 实测；
- 没有对该次 120 秒 Chrome 原始录像进行重新播放；
- 没有在用户机器故意制造 swap、热降频或 native hang；
- 没有证明候选新模型更准/更快；
- 没有把上述 mock token 的字符串当作真实 ASR/MT 质量样本。

### 8.3 精简的可复核入口

源码固定到审计 SHA 后：

~~~bash
node --check p0/stage8_eval/run_chrome.mjs
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:engine/src python -c 'import p0.stage8_eval.tests.test_acceptance'
~~~

第二条在没有作者绝对路径的环境会暴露 V5。其余问题应按第 3/4 节的精确输入顺序构建回归，避免只断言“最后没有 Caption”而漏掉中间流式事件。

---

## 9. 对此前方案需要修正的判断

- 如果此前把 ANE 当成边缘候选而未优先做设备映射验证，那是方案优先级的问题；后来的真实收益说明当时不能宣称机器性能已近上限。
- “NeMo Oracle CER 约 0.26/0.31”不能当数学上限：语料、切片、归一化、转换和 decoder 都是条件。
- “加大模型是唯一高收益路径”不成立。此次发现的终态/去重/解码搜索与任务适配，都是不先换大模型的路径。
- “30B-A3B 只激活约 3B，所以能在 4B 延迟预算内达到 30B 智能”是过度推断，不应当选型结论。
- “日语 SOV 固定贡献 1.9 秒”没有被测量证明，不应当物理下限。
- “VAD 只要加长窗口就能修断词”已被当前局部 sweep 反证，应保留默认直到有新的、能定位错误类型的实验。

正确做法不是补一份更长的模型名单，而是让每条天花板判断都有可复现的条件和反例。

---

## 10. 最终决策

**保留当前轻量主力组合，暂停“质量升级已完成 / 达到天花板”的表述；先做正确性与测量的窄修复。**

对实际使用的判断：

- 可以继续作为个人试用的实时中文字幕方案，已展示较低资源占用和可观的响应能力。
- 不能仅凭 31/31 渲染和 120 秒 P95，把它认证为长时间、跨片源、可顺畅追剧的成品。
- 也不能仅凭 CER 就宣判不可用；CER 与关键语义错误并非一回事，应配合盲评和可见字幕时延。
- 下一轮最值得押注的是：**可靠终态 + 可信评测 + 当前 ASR 的实现/搜索质量**，而不是立刻更换整套框架。

真正可以合理停止的，是“已没有新机制假设、改动收益小于维护成本的某条路线”；不是凭几个失败候选就宣称整台机器到顶。

---

## 11. 源码与一手资料

### 项目固定源码

- [最新提交](https://github.com/lkyprogramer/live-video-tanslate/commit/a7b039685704d07a40ea55a5095b8f30d1be4202)
- [四个提交的比较](https://github.com/lkyprogramer/live-video-tanslate/compare/e1542b90ff02c4eefe331d89dafb49bcc3f61693...a7b039685704d07a40ea55a5095b8f30d1be4202)
- [Stage 8 实验报告](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/docs/experiments/stage8-2026-11-27/README.md)
- [运行主链路、MT 与调度](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/engine/src/subtitle_service/local_pipeline.py)
- [WebSocket 网关与背压](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/engine/src/subtitle_service/app.py)
- [原生 ANE 与 RNN-T 解码器](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/engine/native/nemo_ane.mm)
- [前端 preview guard](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/extension/src/preview_guard.ts)
- [AudioWorklet 重采样](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/extension/src/resample.ts)
- [T7 聚合器](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/p0/stage8_eval/aggregate.py)
- [T7 验收器](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/p0/stage8_eval/acceptance.py)
- [Chrome runner](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/p0/stage8_eval/run_chrome.mjs)
- [MT 计分](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/p0/stage8_eval/bench_mt.py)
- [Index 首筛](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/p0/stage8_eval/run_p2_index2b_screen.py)
- [VAD 同场景实验](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/p0/stage8_eval/run_p1b_vad_2x2.py)
- [原有设计约束](https://github.com/lkyprogramer/live-video-tanslate/blob/a7b039685704d07a40ea55a5095b8f30d1be4202/docs/live_video_translate_stage8_plan_20261126/02_design_spec.md)

### 外部一手资料（2026-11-27 核查）

外部检索围绕日语 ASR、字幕/MT、Apple 运行时三条主线；引用只取模型作者、项目源码、论文或官方文档。候选是否适合本项目，以本机验证为准。

- [SacreBLEU 官方 chrF 实现](https://github.com/mjpost/sacrebleu/blob/master/sacrebleu/metrics/chrf.py)：短序列 effective-order 对照。
- [NVIDIA Nemotron 3.5 ASR Streaming 0.6B 模型卡](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)：日语、增量状态与本地制品。
- [NVIDIA NeMo-Speech.cpp](https://github.com/NVIDIA/NeMo-Speech.cpp)：当前官方本地 runtime；不是项目现有 fork 的同义名称。
- [FluidAudio Nemotron 多语言集成](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/ASR/NemotronMultilingual.md)：Core ML / ANE 编译与版本差异。
- [NVIDIA word boosting](https://docs.nvidia.com/nemo/speech/latest/asr/asr_customization/word_boosting.html)：上下文 bias / trie 的方法参考。
- [Re-translation Strategies for Long Form, Simultaneous, Spoken Language Translation](https://arxiv.org/abs/1912.03393)：质量、延迟与重写稳定性的联合考量。
- [Apple：Deploy machine learning and AI models on-device with Core ML](https://developer.apple.com/videos/play/wwdc2024/10161/)：Core ML 集成与性能分析参考。
- [Apple Core ML 工具文档](https://apple.github.io/coremltools/docs-guides/source/mlmodel-utilities.html)：转换/精度验证工具。
- [MDN requestAnimationFrame](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame)：回调在下次重绘之前，不等同真实屏幕已完成绘制。

本报告未使用社区转述的跨硬件速度数字作为 M1 Pro 性能结论。

