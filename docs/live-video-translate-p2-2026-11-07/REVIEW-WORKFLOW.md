# P2执行包审查与处理记录

2026-11-08。状态：**审查修改完成；后续模型实验未启动**。仓库基线`7121c1bb4f869f6968e8b381c45117223c708917`。本次不提交、不推送、不切换产品默认。

本目录为后续测试唯一计划入口，先读README.md。原始GPT Pro包只读备份在本机ignored `p0/evidence/p2-doc-review-20261108/original/`；不以原包或旧聚合副本覆盖修订版。

## 分工与审查依据

主代理检查当前源码、协议、实验汇总、辅助器与可执行性，统一修改；独立`gpt-6-astra / xhigh`代理只读审查，另从固定上游源码核对模型/API。两者不共享评分选优或并行写文件。原始模型推理、人工复听、封存素材、Chrome、发布均不在本次任务中。

项目真源为现行`engine/src/subtitle_service/`、扩展字幕覆盖逻辑、v1合同、optimization/exploration计划与2026-11-07两批结果。特别保持20/18、15项参考待复听、v6补测13–17/12–15、历史Chrome全屏未验收的边界。源文中的不确定性区间不是统计置信区间；离线active资源不是1×持续占用。

## Astra独立审查：全部接受并修改

| 严重度 | 原问题及实际影响 | 处理与位置 |
|---|---|---|
| P1 | 缺少既有可读DOM停留门槛，瞬闪可被计为成功 | 07/08恢复每必要片段连续可见`max(0.6s,非标点字数/12字每秒)`；核对visible→hidden/removed，WS与DOM分列 |
| P1 | 连续ASR用ingested水位归属新文字；同水位还能继续吐字，可能零长range或错误提前完成端点 | 05区分accepted/ingested/decoder-progress，冻结delay/padding映射；新增同水位多步、pending端点及EOF断言，无法映射则blocked_alignment |
| P2 | Voxtral默认4096步含pad，约327.7秒，无法完成十分钟 | 05及registry预登记8192步用于短探针；60分钟必须另外冻结时长/cap及有界状态或轮换，不能只增cap冒充解决内存增长 |
| P2 | 保留集只有六来源，漏掉既有题材配额 | 02/03/08补回双人对话、长讲解、配乐/环境音各至少两个来源、单来源≤25% |
| P2 | T12强制拼接长文件，与07优先真实连续源冲突 | 优先真实连续60分钟；loop仅工程压力，不增加独立来源或证明内容泛化 |
| 接线修正 | Gemma示例嵌套chat_template_kwargs不是固定helper调用形状 | 06直接传`enable_thinking=False`；不把默认值恰好相同当作正确接线 |

依据包括本项目`docs/exploration-plan.md:168`、`docs/optimization-plan.md:37/47`，以及固定[Voxtral step/ingest](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/streaming.py#L517-L564)、[step计数](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/streaming.py#L693-L709)、[常量](https://github.com/Neywa-Labs/mlx-audio/blob/d52b8cde2ee28d583f173e1bc0eb424b425c11c4/mlx_audio/stt/models/voxtral_realtime/config.py#L8-L12)、[Gemma helper](https://github.com/Neywa-Labs/mlx-vlm/blob/a54f1a0d86fa45c466c00d819ac6ac5ba9d61ac4/mlx_vlm/prompt_utils.py#L966-L986)。这是源码机制及对待实现方案的风险判断，没有声称本机跑出了该故障。

Astra定向复核确认上述实质问题闭合，补充指出“zh-only像必跑”和“正式通过才能工程测试”两组残留文字矛盾。主代理已同步修正03/06/08：zh-only只按B4条件选做；暂定工程例外统一引用08章，不能升级为正式验收。审查者未独立重跑辅助器测试或读取最终重建聚合，包验证由主代理执行。

## 主代理审查：全部修正

| 原问题 | 处理 |
|---|---|
| CLI示例指向不存在的docs/p2目录；T00依赖尚未实现的CLI | 固定当前目录，明确仓库根/包目录；先静态冻结→T01→B0推理复现；worktree位于项目规定目录，ignored资产只读引用与验hash |
| 无人复听与必须人工确认形成执行阻断 | 保守算术和参考可信度分列，独立AI文本复核后可做有预算的provisional工程测试；正式语义/校时/默认替换继续待确认；不提前打开test/ |
| R延迟默认至少20共同正确项，seed29基线却只有18且未人工校时 | registry在输出前明确正式主张不可用；保持原延迟主指标，仅允许暂定描述，不事后换指标制造R通过 |
| 辅助器遇uncertain先返回provisional，掩盖Q净增不足 | 先判既定算术，增加arithmetic_eligible/reference_pending；不足仍不通过；15项pending不删除 |
| 保护损失只信调用者空数组；seed可任意换；measured缺注册引用仍被接受 | 冻结protected_unit_ids并推导baseline正确保护项损失，固定seed11/29；校验measured最小注册/manifest引用；真实性仍由T01与审查负责 |
| timing默认未裁15秒窗；仅靠人类标签声明就返回正式尾延迟资格 | 自动将>15秒匹配留在late_units、计该窗漏译；formal_tail_claim_eligible恒false，样本数/标签声明另列 |
| 标点约束不允许末字符后加标点；删除全部标点可能破坏原文 | 允许末字符，记录本次插入位置，只撤销新增位置来证明输入不变 |
| 产品接口描述忽略现有Whisper profile及backend warmup/available | 09/T11按现行factory/config/backend协议补齐；保持v1公开字幕合同 |
| 字幕轨/OCR/未来预读偏离用户纯音频需求 | 10章改为排除边界，不作为执行任务或后备预算 |
| 分章、完整方案、旧Linux验证与文件清单会各自漂移 | README确立唯一入口；新增可复现重建/校验脚本，生成阅读副本、本机实际报告与MANIFEST；原35项/Linux记录不冒充本轮实测 |

## 保留的路线与证据边界

仍保留8个核心首筛、B4全局最多3个正式配置、最多2个完整1×组合。27个注册项是含条件的菜单，不是27个必跑。未修改Q/R数值、关键错误零新增、保护项、历史109/121及最终保留集/短句/延迟等门槛。

主源核验支持PLaMo的ja→zh-hans/PFML3/历史/词表接口、Gemma完整音频AST入口、whisper-ja760的CT2兼容前提、FunASR日语/提示接口及Qwen无MLX入口，因此没有仅因未测就删除这些候选。核验接口不证明在M1上比当前方案更好；具体索引见12章与各模型配方。

## 实际验证与完成判据

主代理实际运行包内43项单元/CLI测试，全部通过；三个合成示例、四份Draft2020-12 Schema及适用示例、注册引用/无环、Markdown本地链接/围栏、生成阅读副本和SHA256文件清单通过。命令、环境和原始日志见BUILD-VALIDATION.json、BUILD-TESTS.log；重建/复核入口见README。

生产源码与默认配置未变。无新增模型质量、1×、CPU/GPU/共享内存成绩，无人工复听、盲测、Chrome/全屏或长稳新结论。后续实施从T00开始，先完成每阶段产物和验收再继续；此处完成的是审查后的计划交付。

该段记录最初文档审查的交付边界。随后实际执行已推进至[B0](../p2-b0-results-2026-11-08.md)和[B1](../p2-b1-results-2026-11-08.md)，不能用上述历史描述否认后续实测。B1源审、272条独立文本判定与主代理对账保留在该报告引用的raw run；未修改产品默认、无人复听、未打开holdout，B1四配置均未晋级。

2026-11-09完成[B2](../p2-b2-results-2026-11-09.md)：固定上游源码与wheel核验、两adapter源审、136项独立文本判定及主代理对账。两个候选均未晋级；A30额外语境和年份缺口保留待确认，没有为了扩大关键错误数把不完整参考当成完整音频真值。后续从B3核心任务继续。
