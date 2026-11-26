# 11 · 独立审阅后补严的机器合同

本章消除前文简写可能带来的歧义；若旧段落与这里冲突，以本章的更严格合同为准。它不扩大本轮实施权限。

## 1. 三条基线不能混用

| 身份 | 固定内容 | 比较目的 |
| --- | --- | --- |
| B-deployed | 用户当下实际部署的engine/native/extension/profile/模型bundle，源码应核对7ea1386或记录偏差 | 全部发布门禁、用户体验差值、回滚目标 |
| B-corrected | B-deployed基础上，仅修复已复现的协议/测量问题，模型精度不变；单独patch/profile hash | prompt、Q8、模型替换等单因素归因 |
| C-candidate | 完整冻结的候选bundle；包括任何scheduler/边界/模板/模型变化 | G5最终候选，不能逐指标挑不同版本 |

没有证实协议问题时B-corrected可与B-deployed相同，但必须显式记录。修复测量时三者使用同一版本observer/scorer，不能通过为对照用旧计分器制造差异。部署基线在新schema上重新采集，而不是把旧summary强行拼进来。

G5报告C−B-deployed；C−B-corrected只在同输入的dev归因表中给出，不拿dev指标填holdout。G5不强制第三臂；`dev_attribution`可以为空，corrected bundle身份可以为空或明确alias deployed。若只有校正基线改善，诚实把收益归于协议/输入，不归给新模型。回滚恢复B-deployed完整兼容bundle。

## 2. 固定分母、缺失与删样本规则

正式分母是事前冻结的、已完整播放的、人工可辨识日语语义单元；不是“模型识别出多少条”。低声/短句不能因模型错了事后标不可辨识。SRT/人工切分/VAD不同的臂，最后都投射回同一scene/unit分母，不按输出条数配对。

每个unit每个arm必须有结果状态：`visible`、`empty`、`rejected`、`timeout`、`pipeline_error`、`censored_user_action`。后五类不能默认从评分里消失。

- missing/empty/rejected/timeout/pipeline_error：final或first useful延迟记+∞；JSON分位值用null表达无穷，不能用0或删记录。另提供success-only分位数，明确不用于门禁。
- 实质错误导致始终没有正确可理解内容：first useful记+∞；即使错误字幕确实显示过，结构性的final_visible仍可计1，语义错误独立计。
- 仅事前登记的交互测试中，用户seek导致尚未完整听到的unit可censor；只排除其尚未完成的unit。对应两臂censor集合须一致，否则该配对无效。
- 意外手动关闭、未知媒体中断或不一致censor：该run标INCONCLUSIVE，保留原始日志；候选自身崩溃/资源耗尽不是“用户中断”。

分位算法固定nearest-rank：升序（+∞排最后），第`ceil(q×N)`个；N=0为不可判。主门禁额外要求final_visible率≥.98、first_useful率≥.95、unit句末2000ms内final率≥.95，且相对部署基线覆盖率最多下降.005。这些是产品目标，不是本机当前已达事实；没到就不宣布发布通过。

若部署基线分位为+∞而候选为有限值，记录“消除缺失后的有限延迟”，不算百分比提速；准确性升级可继续检查绝对时延/覆盖率/质量门禁，原来的相对时延非劣门禁视为满足并标`NONFINITE_BASELINE`。若候选也为+∞则延迟门禁FAIL。若N=0或未测，属于UNKNOWN，G5为INCONCLUSIVE。基线≤0的分位不作百分比，非劣使用100ms绝对差上限；“实时性升级≥20%”路径要求两侧有限且基线>0，否则该路径INCONCLUSIVE，不能填写100%提升。

用户可见撤销对同caption跨kind统计，包含preview→final、提前remove；正常显示期结束的移除不计撤销。把token追加诊断与用户可见rev轨迹同时保留。

## 3. 时钟合同

事件的`ts_ns`只对应server monotonic或browser performance。媒体样本只写input/target/captured sample字段；同一个sample index在不同epoch不具有相同身份。

浏览器—服务端anchor记录四个时间b0/s1/s2/b3（都换算ns），估计server−browser offset：`((s1-b0)+(s2-b3))/2`；不确定性至少包括半个往返剩余时间与多次anchor残差。启动、每5分钟、pause/resume/seek后重新锚定，seek另开epoch。

媒体—捕获—DOM关系：诊断扩展记录audio frame样本范围、AudioContext/浏览器performance时钟、video currentTime与playbackRate。若对确定源片本地回放，使用源PCM与实际捕获PCM的相关性校准常量偏移和漂移；不能只假定音频捕获无缓冲延迟。无可验证映射时只报告同域计时，不能生成跨域E2E结论。

本包拟议最大跨域映射不确定性20ms；超过则该run的跨域发布延迟INCONCLUSIVE。不能把负延迟静默裁为0；保留原始值并查时间/标签问题。真正允许提前显示已可判断的最终单元时，单位句末到显示可为负数，明确说明而非修饰数值。

## 4. 调度身份、deadline与提交边界

生产共享类型放在`subtitle_service/quality_types.py`，评测代码单向导入；生产不可import p0。

`RequestKey(session_id, epoch, caption_id, revision, request_id)`是完整身份。取消使用session_id+epoch，caption_id也在session/epoch内唯一。所有deadline是**server monotonic ns**，不是媒体样本或浏览器时间。

每个候选manifest写独立`budgets`，不能从P95发布阈值倒推逐条期限：

- `final_request_timeout_ms=2000`：从该target真实末样本映射到server时刻计，超时发显式错误事件；这是运行超时，不等于1200ms发布P95。
- `preview_expiry_ms=900`：从当前snapshot末样本计，过期不再送显。
- `verify_max_extra_wait_ms=120`（默认型实验）；质量档可事前设300，不得事后加等待救分。
- `reserve_mt_p95_ms`与`reserve_transport_p95_ms`来自该MT候选在dev上的冻结测量，未取得值不得开启线上ASR复核。

`verify_cutoff = min(segment_release_time + verify_max_extra_wait, final_deadline - reserve_mt_p95 - reserve_transport_p95)`。

只有预测的GPU复核服务时间（dev p95）及当前排队能落在cutoff内才允许入队，否则直接用NeMo。已运行kernel不能保证可抢占，过期也要等安全边界再释放GPU；由此引起的超时照常计失败，不得改写时钟。

**提交定义**：

1. `final_token_stream`只是候选文字，UI仍标流式；不能写正式history或音频commit。
2. 完整MT通过stop/网关并再次验证key/epoch/span后，原子写`final_accept`；这一刻才写已接受MT history与逻辑accepted区间。
3. `ws_send`不等于看见；只有DOM记录可用于final_visible。网络/显示失败也要计缺失。
4. 同caption只accept一次；过期复核不得替换，preview与shadow不写history。
5. 维护两个概念：`resolved_until_sample`（已成功或显式失败结案的范围）与accepted-span集合（真正提交的范围）。失败不得伪造accepted，但必须resolve，允许后续句子继续，避免队头死锁。
6. 到deadline尚无有效final：清除该caption预览并发`timeout`，无history；后续结果丢弃。caption按音频序号推进，前条未resolve时后条可计算但不得越序accept；前条超时后再推进。记录order wait，超过后条deadline则其本身也timeout，不隐藏积压。
7. raw文本清洗/网关过滤不能导致history只有一半更新；使用同一原子accept动作。无DOM反馈时不能把history统计当上屏统计。

ASR token时间必须转成当前session/epoch绝对样本。当前NeMo发射帧映射用`input_start + frame×1280 − pad_samples`，它不是精确词首；不确定边界只允许延期或fallback，无timestamp挑战者只替换单个完整未提交target。不同切分实验最终按同一冻结语义unit评分。

## 5. Manifest、schema与CLI优先级

`schemas/contracts.schema.json`提供Manifest、Event、SemanticUnit、Judgment、ClockAnchor、Thresholds、Aggregate、GateDecision定义。使用JSON Schema Draft2020-12加T0/T1的跨字段验证；结构通过不等于数据/权限有效。

每个manifest只有一个arm，`models`按key列出主ASR、GPU fallback、可选challenger和MT，`roles`引用这些key；每个artifact记录hash/本地路径。观察器、engine、native、extension、profile分别锁身份。thresholds有绝对路径和hash。仅`arm.baseline_role=subtitles_off`允许models为空、四个roles为null；此时仍跑同Chrome/视频/诊断，只关闭字幕管线，用于掉帧对照，不能混入翻译质量分母。

`--arm/--out/--duration-s`仅核对manifest，不能覆盖；不一致退出2。试验参数变化需新manifest、新experiment key。`experiment_key`包含输入/模型/代码/observer/配置/阈值，不含开始时间和run输出目录；`run_key`覆盖完整manifest，区分ABBA的各次运行。

示例的template_only=true、REPLACE字段、未知asset/hash/stopIDs/license和权限都是BLOCKED；AI必须读取本地真实值，不能把schema里的例子直接当已验证配置。

## 6. 从原始事件到验收报告

T7新增 `aggregate.py`：

`manifest+events+clock anchors+semantic units+逐unit盲审/仲裁 → 校验hash与身份 → 匹配unit/DOM事件 → 缺失补齐 → paired scene统计 → aggregate_report.json → acceptance.py`。

deployed/candidate必须有明确身份；corrected只在存在dev归因数据时必填，没有修复时可alias deployed。aggregate缺原始事件、缺身份、缺人工最终judgment时不得自动补“通过”。AI初审可以生成临时summary；正式quality gate在个人单人项目默认需至少 1 位人类盲评+二次复核确认（`strict` 级需 2 位人类审阅及仲裁结果）。

每条Judgment关联run_id/unit_id、最终DOM事件、first useful/stable DOM事件（没有则null）、最大错误等级、根因类别；两个评审记录不一致时另出adjudicated记录，统计仅用唯一finalized adjudication。分段变化通过unit→多个caption映射解决，不按seq硬匹配。

保护集由SemanticUnit的`protected`及`protected_facts[{fact_id,description}]`锁定；盲审只标该输出的`protected_fact_errors`，不要求审阅人知道baseline角色。聚合计算候选错误而部署正确的fact数，得到`protected_key_regressions`；不能从“未写备注”推断0。

### G5各门禁的唯一数据组

| 门禁 | 唯一/必要cohort | 禁止混入 |
| --- | --- | --- |
| 关键错误下降25%与scene CI | 冻结holdout，deployed/C各一次（个人默认 `>=10` 场景/`>=15`m；`strict` 级 `>=20` 场景/`>=30`m） | EP01开发难例、ABBA重复、shadow |
| 主first useful/final DOM时延、availability | holdout；另对soak分别检查相同绝对/非劣与coverage门限，任一失败不发布 | 不能把两组混池稀释最差尾部 |
| 旧关键保护信息退化 | 锁定regression units、deployed/C盲审 | holdout缺错例时不能当保护已通过 |
| 长时内存、队列、断线和掉帧 | soak 45m＋interaction 10m分别汇总，资源全程 | 不以离线/5m均值代替 |
| 视频掉帧差值 | 同片源同播放设置soak的subtitles_off独立control与C | B-deployed不能替关字幕control |
| C−B-corrected增量 | 独立dev_attribution同输入配对，允许为空 | 不能跨split作为G5增量 |

schema中的`metrics`固定是holdout的deployed/candidate；`soak_metrics`另列两臂；`cohort_runs`逐项列run_id；`dev_attribution`可null，非null时须给同组corrected/candidate及差值。缺soak语义标注就无法得出first useful门禁，只能交付筛选结果，不自动用SRT/成功样本代替。人工标注/审阅成本不包含在串行播放250分钟里。

`aggregate_report.json`的schema字段只是最小机器接口，详细计数/分组和原始文件索引仍保留。acceptance每个硬条件都从数字/证据状态计算，不能仅信上游写的passed=true。阈值文件内的英文规则字符串是说明；数值字段与本章为算法定义。

aggregate的status只标数据证据完整性；最终发布状态由单独`GateDecision`给出，必须含`gate_scope=G5`、明确profile类别、每项check的证据键和值。`INCONCLUSIVE_QUALITY`等便于人的标签只用reason_code，不扩展四态枚举。缺数字/证据时passed=null，不得默认true。

## 7. 状态、资源趋势与holdout

- `BLOCKED`：未取得输入、授权、正确版本或运行资产；不等于模型失败。
- `FAIL`：有效测试中候选触发内存停止、崩溃、超时/质量/生命周期门限；保留停止前事件，不能通过提前终止删除尾部。
- `INCONCLUSIVE`：外部应用干扰无法归因、clock不可靠、统计样本或人类审阅不足。原因用独立reason_code，不创建第五种状态。
- `PASS`：指定门禁全部证据成立；单元测试PASS只覆盖该任务，不代表发布PASS。

持续增长操作定义：预热/载入结束后按60s桶统计；连续3桶swap/pageout累计增加且未在后续桶稳定，标`sustained_swap_growth`；同时另执行256MiB硬停止。队列连续3桶的pending/最老任务age中位数单调上升且末桶未清空，标`sustained_queue_growth`；这些是固定保守判据，不替代单次deadline失败。

环境可归因控制：同时记录字幕关闭/B-deployed的同类counter；若有无关软件更新等外部事件，标INCONCLUSIVE并重跑，不把它当模型必败，也不默认免责。

holdout在T5/T6保持封存，仅G5对冻结的完整C-candidate用一次。MT-only、preview-only通过自己的集成/生命周期测试即可进G5，不必实现双ASR。看holdout后调参的版本，必须换新留出集才可重新宣称独立验证。

## 8. 实际工作量和可分段交付

按个人单人项目默认门限（仅对通过 G4 的唯一最终候选运行 45m+10m soak 与 1 部 15m holdout，配 1 轮可复用 5m Stage 6/7 对照及 ABBA 复验），真实浏览器串行播放下限约 `100–130` 分钟（不含 `subtitles_off` 对照、编译、下载与人类盲评复核；若按 `strict` 级 2 部 30m holdout + 多轮 5m ABBA 则约 `250` 分钟）。

因此先并批交付 **T2（已复现 P0 KV 修复）+ 轻量 T0/T1 评测修正**，拿到真实 B-corrected 数据后再决定是否投入模型下载与完整 G5。缺 holdout 素材/人工复核不能阻断独立可做的 P0 缓存修复与偏差消融，也不授权 AI 自动抓取影视或虚构人工评价。
