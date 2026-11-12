# 10 执行授权 记录与中断恢复

## 先分清四类命令

| 类别 | 现在是否可运行 | 授权与限制 |
| --- | --- | --- |
| 本包三个helper及文档辅助单测（实际数量见BUILD-VALIDATION） | 是，Python3.10+，无模型 | 只读/合成材料验证，不下载不推理，不证明产品性能 |
| 当前仓库P2已实现命令 | 仅在原资产/环境和命令分支满足时 | 本次文档交付未授权重跑；P2 preflight可能真推理 |
| 09章P3 CLI/adapter | 否，待T02等实现 | 先开发和无模型测试，不能假装现成一键脚本 |
| M1模型运行、真实Chrome/holdout、生产切换 | 本轮未做 | 用户授权实施后仍受权限、许可、数据与阶段门约束；生产切换另行明确批准 |

不需要用户逐行批准本包未来获准范围内的普通实验实现，但有新增权限/私有权重/高资源预算/深度框架重写/默认政策变化时必须暂停相关分支。可并行做无关文档和审查，不能绕过阻塞继续该分支。

## 推荐run目录合同（由未来P3实现创建）

统一使用新run根 `p0/evidence/p3/<run_id>/`，目录在gitignore范围内。`run_id`含日期、候选和attempt，不使用旧run名称。相对目录如下：

| 路径 | 内容 |
| --- | --- |
| `run-manifest.json` | 阶段、模式、授权、候选、种子、预算、参考状态、路径 |
| `locks/materials.json` | 本包model-lock格式；当前阶段实际所需模型required=true |
| `locks/runtime-artifacts.json` | 代码commit、patch、依赖锁、VAD、tokenizer/frontend、许可、真实精度 |
| `locks/inputs.json` | PCM/切片/语义/保护项/提示/时标hash及来源授权 |
| `locks/hypothesis.json` | 首输出前选择的Q/正式R/探索R_memory、主指标与参数 |
| `history/` | 旧P2 receipt的只读副本或hash/绝对原路径清单；不修改它 |
| `raw/asr/`、`raw/mt/` | 未裁剪的真实结果、错误、termination，不只留过滤后字幕 |
| `events/` | 后端、WS、Chrome、native state、资源和clock校准原始日志 |
| `derived/` | helper结果、timing输入、规范化prefix、统计；都能从raw重建 |
| `review/` | 隐名评分、争议、待听审、人工身份/时间及独立review |
| `receipts/` | 每步骤输入fingerprint、实际argv、cwd、起止/exit、输出hash与状态 |
| `decision/` | 单候选判定与批次报告，失败/未做也需列出 |

在报告公开版删除token、私密profile、音频路径中的身份、用户视频内容；保留可核对的相对ID/hash。机读材料不能只存截图。源码快照、模型和原始音频的再分发还须许可，不能塞入公开文档zip。

## 锁定流程

`config/model-lock.json`是**材料登记模板**，不是已安装清单。Reazon、Hy revision已核验；所有文件列表/大小仍须目标机补齐。材料根可以是已明确登记的共享.model-cache而非新run目录；文件清单用相对该根的路径，并检查实际resolve目标仍在根内，保留当前Hy两级链接。无需为每个run复制一份1GB权重。候选权重revision未知为null。helper故意会返回exit2，不能为了绿灯删掉required条目。

每个阶段生成一个投影锁：共同必需的Hy/所需B0材料，加本阶段所选候选和机制控制；未选模型保持optional或不收入该run。必须由registry解析期望集合，并验证`expected_required_ids == actual_required_ids`。helper允许空或全optional集合，但这种ready是空集真，不代表任何模型存在，P3 check必须拒绝缺必需ID。

VAD文件不强塞进只有Git/HF revision语义的模型锁：其已知SHA256放在runtime-artifacts，实际大小/位置/来源receipt在目标机补齐。tokenizer、mmproj、frontend、转换图也必须完整枚举；一个GGUF文件hash不足以冻结整个声学链。若没有可信上游来源，则blocked_data，不伪造revision。

本包helper `check-lock`只验证字段完整性；加`--root`才检查本地字节。它不证明HF revision确实产生这些字节，不核查许可证，也不验证VAD和运行时，后几项由P3 check及人工审查承担。无`--root`的ready不得用于模型启动门。

每次执行前重复校验本地hash、选中配置和运行时，避免同路径被换内容。使用HF锁定snapshot，不默认trust_remote_code；确有需要则先审具体revision的代码并隔离，不执行从搜索摘要复制的shell脚本。

## 恢复与幂等

- 仅当step状态passed，输入fingerprint、代码/模型/参数、输出文件hash全同，才能复用已完成步骤。复用标`reused_receipt`而不是`new_measurement`。
- 失败、中断或timeout的attempt保留，下一次使用新attempt目录；原raw和评分不可覆盖。说明修复根因和预算剩余。
- 恢复前检查本轮服务/worker是否确已退出；未知进程状态不得贸然再启动第二实例。只有能验证属于本run的进程才可终止，不能按宽泛名字杀用户进程。
- 模型量化、块长、U、state reset、提交策略、backend/frontend改变均新experiment_id，并消耗相应配置预算。仅修bug也须保留补丁、影响分析及全部受影响回归，不能拿修复前分数混用。
- 参考时标或语义标签变化：新reference_version，B0和所有比较项重算；不能只改对候选有利的单位。
- 发生权限失败、需收费资源、私有模型、系统设置变化或预算耗尽，停止受影响路径，写下一项需要的明确授权。

## AI可独立做与不能代做

AI可完成代码、单测、静态锁检查、合法公开工件准备、结构对齐、暂定语义评分和证据汇总。人工听审/许可判断需真实责任人；AI不能把自己的判断标成`human_verified`。只有冻结AI参考时，独立逐项文本复核可形成score_status=provisional及保守研究筛选，15项仍pending-listen；无人复听不自动阻断其余有依据的研究步骤。连暂定语义或时标也无法确认的项单列needs_human/unknown，不计correct；正式质量、正式尾延迟和默认替换仍需真实人审，不能由AI签字补齐。

独立reviewer不应是同一份提示直接自我签字；至少独立上下文核对冻结前后的配置、所有收益/损失及raw样本。review发现问题先修证据或实现，再重新执行受影响测试；不能只改结论措辞。

## 停止后保留什么

所有路线不胜出时，交付默认保留、候选失败类型、已花预算、尚未确定问题和重新开启所需的新证据。值得再开应是具体变化，例如该日语权重发布官方cache ONNX、R2T2 full llama实现已验证的编码缓存、许可放开、或已冻结新场景显著改变错误分布；不是README多了“realtime”一词。

不能以“找到更强模型”作为无期限任务。当前包完成标准是形成可信可复核决定，不是保证找到新赢家。
