# 03 · 统一评测与遥测合同

本节是 Stage 8 的先决条件。没有此合同，不允许用新模型的漂亮数字替代真实体验。

## 1. 固定输入与分组

运行清单必须包含：repo/patch SHA、实际profile快照哈希、native dylib及CoreML桶哈希、GGUF/HF revision及文件哈希、runtime和系统版本、音轨选择/downmix/resample命令与PCM哈希、字幕/人工标注版本、随机种子、门限哈希、各运行输出目录。

数据分为：

- **D-dev：** EP01全文及18条历史难例；允许反复调参，不称泛化盲测。
- **D-regression：** 过去成功的短句、称呼、数字、语态、空白/BGM/重复、pause/seek；防新退化。
- **D-holdout：** 个人单人项目默认至少 1 个未用于调参的集数/节目，至少 10 个连续场景、累计 `>=15` 分钟（若升级至多贡献者/公开发布 `strict` 档，可扩展为 2 部集数 / `>=20` 场景 / `>=30` 分钟）；只用于最终候选一次比较。先锁 hash，不看候选输出选场景。缺资料为 BLOCKED，不用 EP01 后半段冒充未见测试集。
- **D-soak：** EP01完整45分钟，另加10分钟暂停/跳转/恢复；必要时追加不同片源60分钟。它衡量稳定性，不替代holdout质量。

同一语义单元可跨SRT cue，一条cue也可含两个人。保留原始SRT兼容分数，质量基准使用逐字日文转写与人工语义单元映射；两者不可混名。参考译文允许多种合理表达，不用单句字符串等号评价中文。

## 2. 事件身份与时钟

公共字段（JSONL）：

| 字段 | 规则 |
| --- | --- |
| schema_version / run_id / experiment_id | 固定为版本化schema；run唯一 |
| session_id / epoch | seek、换视频或重开session时epoch递增 |
| caption_id / revision / request_id / parent_request_id | caption稳定身份；rev单调；草稿/定稿/重试不同request |
| kind / event | kind=`preview`,`final`,`shadow`,`resource`；event见下 |
| clock_domain / ts_ns | 域仅`server_monotonic`,`browser_performance`；媒体样本使用独立sample字段，绝不冒充纳秒 |
| target_start / target_end / input_start / input_end / captured_end | 16k采样，左闭右开；资源事件可null |
| source_revision / text / text_hash | 关联具体ASR版本；私有原始日志可有text，公共聚合仅hash |
| model_key / variant / metrics | 模型身份及可选原生计时；不可用profile名替代实际模型 |

必需事件：`capture_frame`、`vad_start`、`vad_end`、`segment_release`、`asr_enqueue/start/end`、`asr_select`、`mt_enqueue/start/prefill_end/first_token/last_content_token/eos/end`、`ws_send/receive`、`dom_visible/dom_update/dom_remove`、`cancel/drop/error`、`resource_sample`。

浏览器用performance.now及requestAnimationFrame记录可见状态，服务用monotonic；每次启动和每5分钟以双向clock anchor估计offset/误差，并记录media currentTime、播放速率、卡顿。不要简单拿Date.now相减。所有跨域指标必须附clock uncertainty，时钟误差太大时标INCONCLUSIVE。

## 3. 必须分开的延迟

| 名称 | 起点→终点 | 用途 |
| --- | --- | --- |
| segment_first_delta_ws | seg_start→首WS文本delta | 兼容旧报告，不能代表读懂 |
| utterance_first_visible_dom | 人工语音起点→首非空DOM | 第一眼看到，不保证正确 |
| utterance_first_useful_dom | 人工语音起点→首条含正确可理解内容的DOM | 主要体验指标，需盲审事件/片段 |
| unit_first_stable_dom | 语义单元起点→第一次显示且后来不发生实质撤回的有用内容 | 对比早但乱跳 vs 稍晚但可靠 |
| unit_end_to_final_dom | 人工语义单元结束→终稿DOM | 主要定稿延迟；不等同切片end |
| segment_end_to_final_ws/dom | seg_end→final WS / DOM，分别报 | 便于诊断现有流水线 |
| queue_ms / compute_ms | enqueue→start / start→完成同步 | 调度与推理分别归因 |
| mt_ttft / last_content / stop | MTstart→首token / 最后有用中文字 / 停止 | 避免仅停止符/多余长尾拖高耗时 |

“稳定”不能靠等到final后把所有内容都追认从最早时刻存在。必须逐revision检验显示内容是否已正确且之后未被实质翻反；人工审阅可理解语义单元。自动LCP只是可复现代理，不能代替正确性。

首字缺失、拒绝、超时要保持在总分母；不能用final代替delta后不打标。正式门禁用固定参考单元、失败记+∞的分布及覆盖率门禁（JSON用null+missing状态表达∞），成功样本分位数仅为附表。具体规则见 [11](11_machine_contracts.md)。`P95(A)+P95(B)`不是链路P95，独立P50之差也不是因果贡献。

## 4. 撤销与闪烁的正确算法

对于同caption连续两次可见文本 `a,b`（用户可见统计包含preview→final、replace和提前remove；同kind仅作诊断切片）：

`erased_chars = len(a) - len(LCP(a,b))`；只向末尾追加时为0。报告每分钟撤销字符数、发生撤销的caption比例、每caption撤销总量、显示后短于阈值就被替换的比例。为诊断可同时保留旧Levenshtein指标，但命名`legacy_first_delta_edit_ratio`。

例：`今天→今天天气很好`，erasure=0；`今天→昨天`，erasure=2。标点/排版变化单列；含义反转要另以MQM式错误判断。部分词前面插入修饰会导致LCP较短，仍不必然是含义变化，报告中说明代理局限。

必须把 `preview` 与 `final_token_stream` 分开。多个final token delta不是多次ASR草稿；不能再以103个通用delta配对当作44次真实preview的推翻率。

正常达到预定展示期后移除不算撤销；提前移除、被错误新revision覆盖则计撤销并记录原因。最终机器字段、clock误差上限和标注关联见 [11](11_machine_contracts.md)。

## 5. ASR评分

- `legacy_cer` 复现历史NFKC/去符号逻辑，明确包括旧实现去掉`ー`的问题。
- `cer_v2`：NFKC、统一空白、去确定清单的标点，**保留长音符号ー、否定、数值、字母内容**；不自动把所有数字换成读音。不确定等价写法可追加宽松分数，但不能替主分数。
- 同时给S（替换）、D（删除）、I（插入）及分母；按同一固定场景、语义单元做配对，重复历史字幕记I。
- raw ASR、selected ASR、最终上屏对应源文分开评分；网关过滤造成漏句不能从ASR基准里删掉，也不能把它误判为模型没听见。
- 另报关键实体/数字/否定/语态错误、完全漏掉语义单元、短应答丢失、BGM幻觉、说话人串句。时间重叠覆盖只叫`temporal_coverage`。
- 加长窗口应同时计input秒数/target秒数、bucket、beam触发、frontend/predict/copy/decode时间，避免靠多看未来音频作弊。

## 6. MT评分

Arm B-clean：人工确认的日文语义单元→中文。Arm B-noisy：冻结的真实ASR输入→中文。Arm E2E：实际音频→最终可见字幕。三者分开；只用SRT cue还可做历史兼容Arm，不替代完整语义单元。

现有 `chrf_score(max_n=4,beta=2)` 按每阶F值平均且去标点，保留为`legacy_chrf4_fmean_0_1`。新增标准SacreBLEU chrF（记录版本、完整signature、order、beta、word_order、0–100尺度），不要混用0–1和0–100。字幕跨句对齐采用预先冻结的语义映射；SubER可辅助看分段与时序，但不是全部质量真值。

首要人工rubric：

- E4严重：主体/受体颠倒、否定反转、关键数量/时间错、核心剧情事实编造或遗漏。
- E3重要：关键名词、社会关系、因果、语气导致理解偏离。
- E2轻微：不够自然、称呼风格、非关键冗余。
- E1表面：标点、可接受同义表达；不计关键错误。

以`critical_error_rate=(含E3或E4的语义单元数)/(全部可评语义单元)`为主；完全无输出若有可听明语义计严重遗漏。多个错误可另记错误总数，但主分母按单元，不双重计算。

个人单人项目默认由 1 位懂日中语境的人类审阅者盲模型身份并随机顺序完成主评，并对临界与含 E3/E4 单元执行二次复核（若升级为团队/公开发布 `strict` 级标准，则需 2 位独立审阅者及分歧仲裁）。AI 评分仅辅助排序与初审，记录 `human_verified=false` 时不能通过最终质量门禁。18 条旧难例是回归保护，不可被称完整准确率。

## 7. 样本配对与统计

- 短时性能用同PCM/同语义单元的ABBA，至少3轮，每轮重新创建session，模型预热与首次编译另记。不同候选按顺序计时，不并行占GPU。
- 保留cold-first、warm-distinct-request、cache-hit和history-replay四种请求类型，不只重复同一句来制造命中。
- 同一场景内片段高度相关；95%置信区间按scene配对bootstrap 2000次、seed固定。少于20个holdout场景不作稳定泛化结论；短测只叫筛选。
- 若baseline关键错误太少，比例门槛会不稳定；固定保护集逐项复核并标INCONCLUSIVE，不为了多算错误改测试集。
- 用同层、同样本、同窗口比较；必要的缺失配对也要归档，不能只交集筛掉最差片段。

## 8. 资源与可观测性

采样全机内存压力、swap used/pageout变化、进程RSS/footprint、MLX active/cache/peak、Chrome进程树、CPU负载、视频播放/掉帧。标出共享/重叠计数，**不能把RSS+MLX+GPU三份相加当总内存**。

`nemo_ane_wired_mb()` 当前调用host_statistics64得到全机wired memory，不是ANE模型独占内存。记录名必须体现范围。ANE路由次数不代表ANE百分比；无授权硬件计数器则报告UNKNOWN，不用占空比推断。

可先用无需特权的系统/进程观测与模型native计时。`powermetrics`、Instruments附加权限等另行授权；工具不可用不应阻塞功能正确性评测，但必须阻止“已经接近硬件极限”的断言。
