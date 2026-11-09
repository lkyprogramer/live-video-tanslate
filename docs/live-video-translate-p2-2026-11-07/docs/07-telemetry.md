# 07 · 时钟、资源与端到端归因

本章定义拟实现的监控合同，不给尚未运行的候选填写性能。复用项目 `p0/monitor.py`，在相同播放负载下串行测试。

## 7.1 统一事件表

每条事件携带 run_id、experiment_id、repeat_id、clip_id、session_id、epoch、source_id（适用时）、host monotonic_ns、真实 sample 水位；原生事件另保留本地时钟和映射版本。不要用 UTC 墙钟计算延迟。

| 事件 | 目的与必要字段 |
|---|---|
| capture / pcm_accepted / pcm_consumed | 捕获、接纳、消费各自的累计真实 samples；检查丢帧和排队 |
| vad_release | 原段 start/end_sample、放行原因、人工 padding 数，区分句尾等待和计算 |
| asr_enqueue / asr_start / asr_end / source_commit | ASR 等待、计算、可提交原文时刻；原始文本、消费水位、是否 forced |
| mt_enqueue / mt_start / mt_first_token / mt_end | MT 排队、首 token 与完整译文；输入/历史 hash、token 数、finish_reason |
| ast_preprocess / ast_encoder / ast_prefill / ast_decode | AST 分阶段剖析；普通运行可只记整调用，缺细分时填 null |
| caption_filtered / caption_emit / ws_receive | 原始输出、中文过滤原因、实际协议 seq 与时间范围 |
| dom_visible / dom_hidden / dom_removed | 字幕确实可见、停留时间、被替换/遮挡原因；不能只看 DOM 节点存在 |
| cancel / drain / finished / worker_return | 用户生命周期与晚到输出；epoch 和仍占用的 worker |
| resource_sample | 进程树、GPU/ANE 可观测性、系统内存压力、队列、热状态 |

内部原生ASR可以输出 provisional / committed；只有经 MT/AST、过滤、协议提交并实际可读的中文计入最终指标。所有原文、翻译、事件必须可按 source_id 与 sample 范围互相追溯。跨多个字幕拼成一个语义项时，匹配时刻为足以表达该含义的最后一条字幕实际可见时刻。

可读性继承现有探索合同：每个表达必要事实的字幕片段须连续可见至少`max(0.6秒, 非标点字数 / 12字每秒)`。保存visible→hidden/removed及遮挡证据；仅有dom_visible或节点存在不足以证明可读。达到停留门槛后，延迟回溯到该次首次可见时刻，不能改成达到停留时刻；不足门槛或缺证据记显示损失/unknown。涉及多条字幕的语义项逐条满足，再取最后一个必要片段的首次可见时间。WS正确覆盖与可读DOM正确覆盖分列，不以WS成功代替上屏成功。

## 7.2 浏览器和后端时间对齐

1. 后端使用单调时钟；浏览器使用 performance.now()。记录 media currentTime、播放速度、音频 sample counter 与每次 seek 的 epoch。
2. 每轮开始做至少10次本地往返时钟采样，采用最低 RTT 的若干次估计 offset，并保存全部原始样本与不确定区间；结束再测漂移。不能直接相减两个进程的起点。
3. 回放音频以第一个真实 sample 的发送时刻为零，`sample / 16000` 只表示媒体时间，不代表该包实际被消费的时间。
4. 如果音频采集、系统重采样或事件队列引入延迟，分别报告 capture→accept→consume。对于 Chrome，校验播放起点与捕获样本起点偏移，不能用按钮点击时间替代声学起点。
5. 阶段时间使用同一后端时钟；WS→DOM 使用校准后的跨时钟事件。校准误差接近欲声称的收益时，结论为 inconclusive，不能写出虚假的毫秒精度。
6. seek/暂停恢复重建映射；不跨 epoch 用连续 mediaTime 推算延迟。真实浏览器同时报告“点击开始→首条正确中文”，与暖态语音起点指标分列。

MLX 的异步/惰性执行需要特别处理：分阶段微测应显式求值/同步并写清方法；正式端到端回放保持正常调度，以真实结果就绪和 DOM 为终点。不能把异步提交耗时当作GPU推理耗时，也不能拿重度同步插桩结果代表正常产品性能。

延迟R路线在候选首次输出前检查**正式主张**资格：现有seed29的B0只有18个正确项，且无人完成句末校时，因此当前D0的20个共同正确/人工校时前提不可满足。registry明确冻结`formal_claim_status=ineligible_current_references`。保持原延迟主指标，不事后改成内存。若R保守语义不退及独立AI文本复核通过，可按08章做有限的暂定1×工程测量，样本数/时间敏感性与资源照实报告，结论仅为描述，不能标R正式通过；Q仍按原门槛判定。较大、独立校时开发集只在候选首次输出前冻结，且不改变D0语义分母。

## 7.3 延迟拆分与统计

参考语义项 (i) 的人工句末为 (t_{end,i})，第一次稳定正确且可读的中文时刻为 (t_{zh,i})。原始延迟为 (L_i=t_{zh,i}-t_{end,i})。早于句末的值保留为负，另列提前量；历史采用 `max(0,L)` 的结果单独重算，禁止混用口径。

每轮同时输出：

- 首条任意中文、首条正确中文、首条稳定正确中文；按语音起点和点击启动分别计时。
- 全部分母的2/3/5/15秒稳定正确覆盖，以及完整回放结束时覆盖。
- 15秒内匹配项的 P50/P95、样本数、共同正确项的配对延迟。
- 全单元 P95：未命中按 (+∞)。JSON以 null + `all_units_p95_unbounded=true` 表达，不写非法 Infinity。
- 1–3秒短句从语音起点到首条可用中文的P90，未命中按无穷大。
- 每语义项的漏译、错译、过滤、迟到、显示损失、队列等待和终止原因。

Q/R中的“句末P95≤3秒”沿用**15秒内正确匹配项**口径，并与覆盖门槛共同验收；不能单独宣传为全体字幕P95。R若以延迟为主收益，必须在两组共同正确、人工校时的同一组单元上比较P95，并披露各自丢失项；共同单元不足20个只作描述。小类别不足20个时仍报列表/中位数/最大值，不给其尾延迟作稳定统计承诺。固定 nearest-rank 方法，不能临时改插值法让门槛通过。

`scripts/evidence_tools.py timing`是算术辅助：自动将超过15秒的匹配计为该窗未命中，并列late_units，原输入值不改写。它不验证音频真值、可读DOM或时钟校准；正式runner同时保存无窗原始事件和15秒视图。样本数足够、人类标签声明与正式尾延迟资格分开报告，辅助器永不独立授予formal_tail_claim_eligible。

## 7.4 资源口径与归因

每250ms采样，覆盖冷加载、暖机、正式音频和drain；正式表分开统计冷态与热态。每个 profile 仅加载一个完整组合，Chrome、视频分辨率、播放器、供电、低电量模式和背景负载固定。

| 指标 | 必须记录的口径 |
|---|---|
| CPU | 进程树每核100%；CPU时间/真实音频秒、均值/P95/峰值；不与整机归一化百分比混写 |
| 内存 | 各进程 RSS 与可用 physical footprint，汇总口径、采样峰值、热态中位值；不可将共享页简单相加作真实物理独占 |
| Metal | MLX allocated/peak（运行时可提供时）；整机GPU活跃均值/P95；播放only对照 |
| ANE/CoreML | 实际 Instruments/性能报告中的设备分派；不可观测填N/A，绝不写0% |
| 系统 | memory_pressure、压缩、swap-in/out、温度/thermal state；采不到就显式unknown |
| 能量 | 仅在已有授权、系统工具确实可测且口径一致时记录能量/音频秒；否则N/A |
| 队列 | pending samples、PCM最老等待、MT段数/最老等待、ASR native状态长度、cache大小 |

整机GPU包含浏览器渲染与视频解码。空闲/播放only差值可用于解释，不能把背景噪声为负的差值裁成“模型零GPU”。CPU/ANE/Metal共享内存带宽与功耗预算，不以独立进程证明隔离。[Apple硬件说明](https://www.apple.com/newsroom/2021/10/introducing-m1-pro-and-m1-max-the-most-powerful-chips-apple-has-ever-built/)

对GPU ASR候选，只在质量合格后做四个邻近控制：ASR单跑、MT固定文本单跑、实际联跑、单一有界执行器联跑。请求等待增加且与GPU计算重叠相关只能支持“竞争”假设；具体命令队列 Head-of-Line Blocking 需相应trace。没有证据时报告未证实，不预设根因。

## 7.5 回放与长稳顺序

对每个最终候选C，采用两次配对：B0→C，C→B0；多候选串行分别比较。预登记交错顺序、暖机输入和稳定条件，不挑最好的一轮。任何播放中断、过载或输入不足，保留尚未处理语义项为失败。

60分钟测试使用同一个实际会话，不能拼接12次五分钟run冒充。素材优先取至少一条足够长、获准本地使用的连续视频；固定的重复拼接只能作工程压力测试并标为loop。暖态前/中/后各10分钟比较，记录状态长度、队列、资源和DOM。

止损按02章；单独记录持续后台干扰。运行因干扰失效可重做一次，但原失败run保留，重做理由在看到语义分数前冻结。
