# B4 AST-G4-ZH preregistration review

Status: **PASS_WITH_EXECUTION_GATES**. Blocking findings: **0**. Reviewer: `astra_p2_document_review`, independent Astra lane. Review date: 2026-11-09.

只读核对预登记、工作包、06配方、registry与02/03/08门槛，并完成B4实现增量审查；共用代码与已审查B3冻结快照逐项对比。未运行模型、测试、下载、复听或读取封存test/；仅更新本审查记录。当前实现可用于已分配的一次B4协议对照；实际冻结、控制复现、新质量与资源结果仍须执行后验证。

## 初审 findings（均已解决）

### B4-PR1 · P2 · RESOLVED · 官方对照的首中文计时排除日文转写中的汉字

位置：`docs/p2-b4-gemma-zh-protocol-2026-11-09.md:15`；`WORK-PACKET.md:16`。

当前将首可读中文描述为内部流中出现中文字，但官方格式首先生成日文转写；其中汉字也会命中Han检测。若两侧都从raw开头取首次Han，官方对照会把日文阶段当成中文，破坏所登记的首中文对照。原06配方112–113、166明确全部日文成本必须进入中文等待，首日文token不可当成中文字幕。

冻结前明确：官方格式仅在实际`\nChinese:`分隔边界之后检测首个Han；ZH-only从正文开始检测。缺边界或中文的请求记missing，完整保留88分母；后续协议拒绝不得让它获得有效可读覆盖。该时点只证明候选翻译区间出现Han，不证明语义正确、稳定可读、简体完全合规或DOM上屏。实现完成后再按此边界审查。

### B4-PR2 · P2 · RESOLVED · 非语音失败后的完整跑分明确为失败诊断

位置：`WORK-PACKET.md:14`；06配方95–99；08章状态机。

工作包允许speech探针就绪后继续完整质量测量，并要求静音/音乐幻觉为零才可进入后续工程。保留完整负结果本身合理，但原配方的`ready_for_quality`要求G0结构与行为对照通过，不能因speech通过便把含有效非语音幻觉的G0标为全通过。否则总状态可能同时出现capability_pass与已观察到的安全失败。

冻结前明确独立的speech readiness和negative-control safety状态。若负对照产生有效幻觉，记G0/task safety failure；继续88×2只作为本次已授权协议诊断，保留同口径算术分数但不得据此产生候选晋级或工程资格。无需为此新增模型、提示词或重复试验。

## 已成立的设计边界

- registry的AST-G4-ZH触发明确允许“官方完成 + 单一预登记协议成本假设”，不要求失败的官方6/8冒充合格父配置。B4-1占一个全局名额，剩余两个未分配；Q-only与`resource_primary=null`一致。
- 唯一候选变量是固定输出协议。模型、运行时、640内部padding、解码参数、原88音频和seed映射保持；不输入参考日文、历史、词表、未来音频。完整锁须在首次输出前落地，这仍属于执行前待证事项。
- 当前Reazon88和两轮M0各62逐字复现、官方176输出/token/finish/filter/PCM/features逐项对账，能约束基线和输入漂移。官方完全一致时复用既有语义标签合理；若不一致，不应继续沿用6/8作为相同控制，须报告控制未复现并停止依赖比较。
- 主质量参照仍为B0的20/18；两轮23/21、零新增关键错、保护项无损都保留。B0原失败项上的新关键错也计入。相对官方6/8的改善、成本改善、协议有效率均不能替代Q门槛。
- 机械假设要求每轮全88的生成token总数与调用墙钟总数同时下降至少20%，且协议覆盖不下降；失败/截断及共同有效配对另列，不挑seed。该20%仅是预登记机械信号，不是R资格或统计显著性结论。协议有效检测也不证明翻译正确或完整简体。
- 原始输出保留，ZH日文字段为空，不按双语raw救分；34项×2独立AI文本复核与15pending保留，不声称人工真值或68个独立样本。
- 1h准备/诊断、1h质量/分析、2h总预算、30s调用、12GiB树footprint均有上限；模型及资源串行。执行器须固定计时起点并保存超限终态，不能通过新attempt重置总预算。原冻结模板的未分配slot状态不能被误当作本次执行账本。
- 无生产/默认/engine-lock/Chrome/封存集/复听/commit/push；有限离线协议对照与后续发布资格分开。

## 初审输入摘要（保留原观察版本）

```text
3272f7bf1a3587c2e1be073160dcbddae00728691733804148bb505feb7ecb0e  docs/p2-b4-gemma-zh-protocol-2026-11-09.md
6d842adb112034c6db8ca1312c244aef8354ac0fbfd4369af25254f6cedcba62  p0/evidence/p2-b4-gemma-zh-20261109-152419/WORK-PACKET.md
f06159db9ba1ee745408f71da8d850a0233e62f7239c6039d7a047fa75c796f1  docs/live-video-translate-p2-2026-11-07/docs/06-ast-recipes.md
db77de7b4f66c5de955d4f3f1d11452936f86db00047b4badb464d13c3ab0b22  docs/live-video-translate-p2-2026-11-07/config/experiment-registry.json
eac81b52496156afabe25b071baae7ab6708157a5b2c1a517f87593165238b25  docs/live-video-translate-p2-2026-11-07/docs/02-experiment-design.md
055208f98cc44c87b5eba51f0c13a44d9bff4075b31b91cde0af3503f24ef30f  docs/live-video-translate-p2-2026-11-07/docs/03-phase-tasks.md
d5668283e06cf5382b11d81bc726e35a9d3db27b86a81800ae99ab4a2bf99826  docs/live-video-translate-p2-2026-11-07/docs/08-scoring-and-acceptance.md
ccc9916f88b9672b753911246dcaaf6d02297cbfed99c5c1fe310d3a722a80aa  docs/p2-b3-results-2026-11-09.md
```

初审要求的两点已在下述增量审查中关闭；不要求增加实验配置。


## 实现增量审查

复审时间：2026-11-09T15:42:19.914261+08:00

结论：**PASS_WITH_EXECUTION_GATES**。无未关闭的实质finding；只允许当前B4-1、AST-G4-ZH、Q-only与已声明离线诊断范围。

1. **PR1关闭。** 两份预登记均补齐官方协议分隔边界。`stream_chinese_time`的`^Chinese: `与原`ChineseProtocol`一致，先累积原事件再只检索翻译区间；ZH检索正文。导出同时保存raw事件时间与最终协议有效的首中文时间，拒绝/缺失保留为None并保留88分母。`generation_end_s`记录在实际生成返回及`mx.synchronize()`之后、解析/证据文件写入之前；没有改模型输入或重新解码。顺序及提示长度差异已声明，不能将全部时间变化单归因于日文token。
2. **PR2关闭。** B4 worker单列`speech_structure_ready`与`safety_pass`。非语音有效字幕使状态变为`ready_for_offline_diagnostic_with_safety_fail`；只有speech/structure通过才允许完整诊断。score单列`G0_safety_pass`，以`Q and safety_pass`计算暂定工程资格，安全失败时明确停止工程；Q仍可作为离线算术结果保留。没有语音的元回应属于不应显示的非语音字幕，未自动归为编造语音。
3. **控制与评分隔离。** 新probe要求Reazon完成与两轮M0逐字parity，ZH进一步要求官方176逐项parity。官方检查覆盖原始输出、JA/ZH、过滤、finish、tokens、PCM范围/hash及完整audio_audit；不匹配会终止依赖步骤。评分仍读取从已接受B0复制并冻结的`baseline-score.json`与保护集合，34项显式判断和新增关键错规则不变。官方6/8只进入配对材料与说明；AST-G4-ZH不会获得Vox的R资格。
4. **锁与名额。** 固定ZH指令在freeze再次比对；官方渲染hash已实际读取并与B3的`a86c86c838f14cd9a35beaf2aff4dde69a1e2710e1464a14e1251fd3188769a3`一致。ZH渲染hash为`5e8ef4fa7bc8205d843d4757d0b6696dc3b3554b153d9f689e718aadf048c5aa`；两个保存文本的hash均由本审查以标准库重新计算吻合。准备入口使用实际Gemma4Processor，不加载LM。运行时重新渲染后必须匹配已锁hash。reserve_slot同ID重试幂等、重复占位/满池拒绝，并在首次模型输出前用既有`.writing`+replace落盘；当前b4.freeze进一步限定B4-1。依据本轮单控制者/串行执行约束，不扩成并发预留系统。
5. **协议与输入。** ZH解析器不提取双语字段、不剥语言标签，拒绝未stop、空白、控制标签/围栏、已列标签与假名，无生成日文时字段为空。该规则是冻结语法边界，不能证明全量简体或语义正确。共用Gemma调用仍固定原音频、640无效尾padding、参数、fresh KV、seed映射；instruction/parser/hash为新增注入点，B3默认保持官方协议。
6. **共用改动。** 已与B3生命周期修复尝试的source-snapshot比较b3_worker、b3_postprocess和cli。worker仅新增协议参数、渲染断言与生成结束时间；postprocess仅增加验证器/候选/阶段/配对官方输入，原B3评分条件保持；CLI仅新增B4分派并明确不支持候选replay/accept。B3材料中新增`paired_official=null`为诊断输出的附加字段，不改变原候选分数。
7. **验证边界。** 控制者报告20项定向PASS（B4 3项、原B3 8项及相关9项）。本评审阅读了相关新增测试源码与既有实现，不冒称自己运行了这些测试，也未取得独立完整测试日志。没有运行模型或复现任何新质量成绩。

执行门槛保留：首次输出前完成不可变manifest及slot锁；实际Reazon/M0/官方176一致性通过后才运行ZH；首筛及独立语义复核完成前不宣称Q通过。30s调用限制在共用worker中执行；12GiB树footprint与1h/2h预算由控制者现有资源supervisor/计时执行，B4模块本身仅登记这些全轮限制，不能声称它新增了自动全轮截止。失败/超限保留收据，不重置总预算。实际资源采样、完整176请求、68项AI文本复核、最终汇总一致性均为尚未执行或尚未审查事项；测试、源码与此预审不替代它们。

## 增量审查输入摘要

```text
beb61aac539ac877eb7550b67ad7dcee1500c0a01301552d45b047b172bb1d8e  docs/p2-b4-gemma-zh-protocol-2026-11-09.md
43b57cfc409da445d574a88bbdb20a95bc916c6858c2295e8158fbe9126d5824  p0/evidence/p2-b4-gemma-zh-20261109-152419/WORK-PACKET.md
765c9f5ae1abf55b1ae524e0a68afb76007fa13580dc74f2fc81773f62c5e98a  p0/p2/b4.py
16338d10ab0ef2af650381e95a80b784c0a49886db81b3fa5fedb9e5ddec8a68  p0/p2/b4_worker.py
1386865d0f885cae2e2c1e7c5bac7f11de9804bf1ed0dfc3bd5d14ee3a064266  p0/p2/b4_protocol.py
9ff8626b5e5beb322c784b80d112254ce81db7dd25364a0a7ee14f433ed778b5  p0/p2/b4_analysis.py
e2f30195dfda37bfb3bc2f4ca608f27f3a274f4e607db90ddc546fdbd7fc78b3  p0/p2/b3_worker.py
68155ac27422f2f05bdedc32c8891a86ac136eaa6b3ca3e4b1b99bc7fb1a640e  p0/p2/b3_postprocess.py
5fe87af6a923c6eeb2ba1da20ef9cb01ee97c10e67c682e112dd420104b96d0e  p0/p2/cli.py
1356f24dbdf319d7a152b457f27ab052a35be11d41422a17af263a9b4bca3559  p0/p2/tests/test_b4_protocol.py
702bb9c50dde32f19ca912f176b3ae4e0cb64b7ea0001e909d651065aa0bea82  p0/p2/b1.py
76a73f3adb657aaf995400b4c70214e29c735d6c696a9ffe485f43eb47ac6896  p0/p2/b2.py
4358a4d8319f00539680c6a827c68a09882abee9b9d3db0de25c9660ffb7dc23  p0/p2/freeze.py
440e7ca4d7b76d10085708b9cf4d0af54d99afd278c3eda1744c78d831fcc074  p0/evidence/p2-b4-gemma-zh-20261109-152419/locks/protocols.json
```
