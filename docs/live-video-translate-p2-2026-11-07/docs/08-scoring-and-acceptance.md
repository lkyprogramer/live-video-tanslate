# 08 · 语义评分、晋级和发布验收

## 8.1 可用中文的判定

以冻结语义项为单位，不按字幕条数或输出字符计分。每项预先列：必要事实、可接受表达、需保留的否定/疑问/数字/专名、说话人关系、可合理省略部分、音频可听清度和时标可信度。

评分标签为 `correct / partial / incorrect / missing / uncertain`。仅 correct 计入可用覆盖；partial保存诊断但不凑分。语言流畅不能抵消关键语义错误。不要求与参考逐字相同；同义表达和自然中文可接受。

每项错误用多标签：ASR omission/substitution、MT wrong sense、negation、question、number/unit、named entity、speaker relation、hallucination、duplicate、late、filtered、unreadable。ASR汉字错不自动判中文错；AST没有日文也不自动判失败。分别保存 raw 输出、过滤结果和实际可读DOM。

“新增关键错误=0”按错误内容配对：即使B0在某项已漏译，候选在该项新增反义、数字反转或人物关系错误也算新增关键错误，不能用“B0本来也没对”掩盖。保护清单冻结具体unit_id与必须保留的事实，不可在看到候选后删除保护项。

## 8.2 评审与参考不确定性

1. 实验执行器输出匿名化的音频、上下文、B0与候选文本；模型名称与先后顺序随机隐去。
2. AI评审可整理对齐和建议标签，但标 `ai_reviewed`。另一独立评审者检查新增收益、全部丢失、全部关键错误以及抽样稳定正确项。
3. 现有15项待复听分别保留保守/宽松结论，两个seed每次都必须在保守口径下过门；相关结论不具人工真值时仍标provisional。当前不派发人工复听；独立AI文本复核全部收益/损失/关键错，不能称人工复核。参考不确定和算术不达门槛分别记录，净增不足仍是未过门，不能只返回一个含糊的provisional。
4. 无音频听辨能力的执行环境不得根据已有文字自称复听。只暂停依赖该参考的最终质量认定，继续合法的自动与工程任务。
5. 有争议的同音词记录“当前音频是否含足够证据”；人工参考也可能不是唯一合理答案。
6. 冻结后的评审变更有审计记录，并同样重算B0与所有候选，不能只改单一候选。

统计单位是语义项与来源。两个seed是重复运行，不是两倍独立样本；同会议的大量句子有相关性。候选收益报告 restored/lost ID、分层分子分母，不只报平均值。最终保留集可按来源做配对区间估计；少量开发集不使用“显著优于全场景”措辞。

## 8.3 判定状态机

`planned → locked → capability_pass → quality_scored → realtime_scored → engineering_pass → holdout_pass → release_candidate`

任一步可进入 `blocked_data / blocked_download / blocked_runtime / failed_quality / failed_resource / inconclusive / needs_human`；这些都是有效产出。blocked不等于模型质量差，quality_pass不等于实时成功，release_candidate也不等于自动发布。

执行进度与证据可信度分列：`screen_decision`记录既定算术门槛；`reference_status`记录historical/ai_reviewed/human_verified及pending清单。保守算术通过、独立AI文本审查通过的候选可进入`provisional_realtime_scored`并继续T11/T12不依赖人工的工程检查；仍占最多两个完整组合预算，不标正式`realtime_scored/holdout_pass/release_candidate`。最终质量、人工校时及默认替换未满足时保持needs_human，不耗尽盲测来掩盖缺口。

**Q路线：** 两次离线/因果质量各净增≥3/34；新增关键错0、保护项无损失；两次1×稳定正确5秒覆盖各净增≥4/34，校准后正确匹配P95≤3秒、静音0、无持续积压。

**R路线：** 首次输出前登记主指标；两次离线质量及1×的5秒覆盖均不低于B0，新增关键错0、保护项无损失；完整链路预登记主指标各改善≥20%，并遵守02章其他资源/延迟容差。只要牺牲质量就不是R晋级。

离线主评分基线是20/18，不能把旧20/20、历史1×19/34或更早的20项表混在同一列。同轮基线漂移则重建控制。正式工程晋级须Q/R通过；当前人工缺口仅允许上述provisional工程例外，不放宽算术、关键错和保护项门槛。没有算术过门者保留默认并结束候选矩阵；有暂定改善但未完成正式验收者保留实验档和缺口，不能改评分规则产生赢家。

## 8.4 最终默认替换的硬门槛

以下继承项目已有[优化验收](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/optimization-plan.md)和[探索验收](https://github.com/lkyprogramer/live-video-tanslate/blob/7121c1bb4f869f6968e8b381c45117223c708917/docs/exploration-plan.md)，不是本轮已达到的成绩。

| 范围 | 门槛 |
|---|---|
| 历史17段、121语义项 | 5秒内稳定正确可读DOM至少109/121；逐项新旧参考并列 |
| 独立保留集 | 至少6来源、60语义项；双人对话、长讲解、配乐/环境音各至少2个独立来源；单来源≤25%；5秒正确覆盖≥90%，分类型列数 |
| 正确匹配句末延迟 | 1× P50≤1.5s、P95≤3s；覆盖与未匹配全分母同时公开 |
| 1–3秒短句 | 至少20条；语音起点→正确中文P90≤4.5s，未命中按无穷大 |
| 语义安全线 | 新增关键错误0；冻结保护项无损失；重复/过期/臆造不得隐藏 |
| 非语音 | 原10秒静音0字幕；纯音乐/笑声专题单列真实误触发 |
| 生命周期 | 暂停、seek、取消、切源、断线、EOF、busy、后台/全屏行为可复现通过 |
| 60分钟 | 同一会话无持续积压、串线、原声中断；最老队列>2s持续10s即失败 |
| 内存稳定 | 末个热态10分钟窗口footprint中位增幅≤max(首窗口10%,128MiB)；超限定位复测 |
| 低占用默认 | 热态进程树峰值≤2.5GiB目标；若超标只能建议更高资源档，不伪称默认低占用升级 |

以上“可读DOM”逐必要片段满足07章连续可见`max(0.6秒,非标点字数/12字每秒)`；瞬闪、遮挡、过早覆盖或缺少可见结束证据不能默认为通过。

同一来源的素材分割不能凑6个独立来源。原121项只覆盖历史套件中的部分音频，不给没有语义参考的段杜撰分母。候选若改善但尚未达以上目标，报告“开发集改善，默认替换未验收”，可保留实验profile。

历史Chrome全屏 `TypeError: not granted` 是未通过或未到达，不得继承为pass。按现有Chrome测试实际环境定位；权限或环境不能完成时标blocked，继续其他验收，最终报告明确缺项。

## 8.5 报告的最低内容

每个候选一页decision：假设与唯一变量、版本/文件hash、运行状态、两轮B0/候选分数、恢复/丢失列表、新关键错、2/3/5/15秒覆盖、正确匹配/共同匹配/全单位P95、CPU与内存口径、未知指标、失效/停止原因、下一步。

最终总报告必须分开“源码证实”“作者/论文声称”“本机测量”“推断”。保留全部失败run；完整原始日志本地保存，分享摘要不带访问token、音频私密内容或机器路径秘密。

## 8.6 包内辅助工具的正确用法

现成工具只计算受约束证据，不自动判断翻译意义，不负责模型运行，不颁发发布许可：

```bash
python3 scripts/evidence_tools.py gate examples/synthetic-score.json
python3 scripts/evidence_tools.py timing examples/synthetic-timing.json
python3 scripts/evidence_tools.py events examples/synthetic-events.jsonl
python3 -m unittest discover -s tests -v
```

示例全部是 `synthetic`，即使算出quality_screen_pass也没有模型收益含义。gate的exit=0只表示算术操作完成；必须同时读取screen_decision、arithmetic_eligible与reference_pending。所有返回值 `release_eligible=false`。该辅助器只核对提供的ID/标签与算术；保护事实、评审完整性、注册时序和hash真实性由T01与独立审查证明。

完整runner尚需实现：schema校验、模型/文件hash核验、人工语义标签审计、保护清单真实性、时钟校准、窗口裁剪、R资源比较、DOM与保留集验收。不能把脚本的小部分检查当作整套验收已自动化。
