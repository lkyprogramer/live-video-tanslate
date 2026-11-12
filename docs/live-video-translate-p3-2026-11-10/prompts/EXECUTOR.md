# 交给实施AI的启动提示词

你负责在用户另行授权实施后，执行本包P3探索，不负责保证找到新赢家。先读README和完整方案；重点读00合同、03任务、08门槛、09接口与config。不要从研究附录里的某个旧建议直接跳到下载模型。

## 首次行动

1. 明确当前授权；若用户只是让你读包/评估，停留在只读。获得实施授权后检查仓库状态、77e24c3差异和相关AGENTS.md，建立独立worktree，保护用户改动。
2. 列出已存在P2能力、需要新增P3文件、缺失模型/音频/人工参考、每个阻塞的具体下一步。不要写“先安装所有候选”。
3. 跑本包当前离线测试与合成示例（实际数量见validation/BUILD-VALIDATION.json）；正确识别退出1/2的预期反例。这不计模型能力通过。
4. 按T00→T01/T02a→模型source/parity/质量→T06推进；合格者才做T02b在线整链。T01/T02a合计8工程小时、T02b整批8小时。先无模型TDD、再锁定材料、再capability；不预先花数天搭通用框架。P3 CLI尚不存在时先实现相应最小分支，不把文档接口当现成命令。

## 执行规则

- 原生产默认Reazon+Hy和旧P2收据不动。P3实验默认不push、不注册生产factory、不清理旧模型。
- 两主线R2T2与123M日语RNNT；第三席位Omni/FunMLT/Kana预选一；唯一条件槽S1或RNNT80。最多4正式配置、2 E2E；不同实验组串行，同组保持真实ASR+MT并行。
- R2T2主配置full llama/Q4_K_M+Q8mmproj/160/U1/S0/W16O8，无VAD强reset。日语RNNT固定M1-6/480ms/cache；部署不通不等于语言质量失败。
- 每次输出前冻结hypothesis、source/runtime/model/input/reference/参数/路线。null必需锁阻塞，拒绝latest与静默替换模型。
- 只用已到达PCM，不用原字幕/OCR/未来上下文/云/4090；外部中文不能回写，内部日文草稿不能送Hy。
- 使用本轮成对B0而非挑弱历史基线；两seed不是两个独立数据集。失败/过滤/missing/late保持全分母。
- P3 research_R_memory只用于研究，不代表旧正式R通过。默认2.5GiB使用完整树physical footprint，超预算只生成取舍报告，未经授权不长跑高资源档。
- 取消先封sink，底层worker返回/清理后才释放lease。30秒是单次native调用保护，不是session总时长。
- 15 pending-listen不得自动升human_verified。无法确认语义或时间，写needs_human；AI盲评只能provisional。
- 两天source gate、最多两次明确依赖修复等预算不可重置。缺权限、私有模型、深改框架、收费、资源/政策扩张先报告并请求新授权。

## 每阶段交付

产出真实命令/exit/日志/输入输出hash/环境/预算/判定，给出stage、score_status、stop_reason与下一允许动作。新增测试先RED后GREEN；候选适配先parity后质量，质量前提不成立不做长稳。每个任务完成后做独立review并修复重要问题。

最后填templates中的run、quality-review、decision和BATCH-REPORT。结论必须区分：运行阻塞、有效运行但质量失败、质量有利但成本失败、可研究晋级、完整验收待决。所有候选失败时保留默认，仍可正常结案。

禁止以包内helper的release_eligible、Schema通过、退出0、作者CER/RTFx或模型体积代替真实M1/Chrome验收；本包helper始终release_eligible=false。
