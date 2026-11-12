# 08 晋级 状态与停止规则

## 各状态的含义

planned → locked → capability_pass → quality_scored → realtime_scored → engineering_pass → holdout_pass → release_candidate。

异常终态包括blocked_data、blocked_runtime、blocked_permission、failed_task、failed_quality、failed_resource、inconclusive、needs_human、not_run。能力测试失败不是0/34，未完成的正式质量run保留计划34项但不能作为完整可比成绩。source/import/load通过只表示运行能力，不等于质量合格。release_candidate不是自动发布。

所有报告分别给出已完成步骤、未到达步骤和阻塞原因。退出码0只是命令完成，必须读取业务status；本包三个辅助命令始终release_eligible=false。

## 默认预登记路线

R2T2主配置默认Q。123M RNNT480默认Q和P3新增探索路线R_memory同时登记，后者主指标为同surface完整链路热态tree_physical_footprint_peak_bytes（定义见07章）。第三席位默认Q；若选Omni可在首个输出前同时登记同一R_memory。不得见质量失败后追加路线。S1消融默认Q；如计划R_latency，必须先拥有合格冻结的较大计时开发集。

**政策边界：** P2正式R的主指标仍是`paired_common_correct_dom_p95`，本包不追溯更改。R_memory只支持本轮低占用假设筛选和研究性E2E晋级，不代表“通过原正式R”，也不独立获得默认替换资格。其结果用`research_R_memory_pass`单列；进入release_candidate仍须通过原正式Q或R_latency及完整验收，或者由用户明确批准新的替换政策。不能把收到文档包当作批准放宽旧合同。

| 路线 | 质量前提 | 1倍速及显示晋级 |
|---|---|---|
| Q | 同轮两次D0各净增至少3/34；新增关键错0，保护项无损失 | 两次5秒覆盖各净增至少4/34；15秒内正确匹配句末P95<=3s；静音0，无持续积压 |
| R_memory | 同轮两次正确数均不低于B0；新关键错0，保护项无损失 | 两次5秒覆盖不低于B0；登记内存主指标各改善至少20%；其他指标满足下列容差 |
| R_latency 条件 | 同R，且输出前取得共同正确至少20项的可行计时集 | 同一批共同正确单元、两轮校准后DOM P95各改善至少20%，各自漏错仍按全分母公布 |

当离线B0仍20/18时，Q对应23/21；不得把旧20/20或1x19/17混入。连续causal-stream重新生成边界时同轮B0必须保留，20/18只是历史参照。相同seed结果不独立，23/23是可重复，不是68样本显著性。

R其他容差：CPU秒/音频秒不恶化超过10%；整机GPU均值不增加超过3个百分点；句末P95不恶化超过max(100ms,10%)。R_memory的P95仅作探索guard，可在N不足20时连同N和不确定性描述，用全分母5秒覆盖非劣共同约束；这不能变成正式R_latency胜负。若时钟/人工句末不足以计算guard，则inconclusive，不把缺值当0或pass。正式R_latency仍要求冻结的共同正确N>=20。20%是工程筛选阈值，不是统计显著性结论。

T06离线阶段仅判断Q质量门或R_memory质量前提，后者写quality_entry_eligible而非research_R_memory_pass；完整配对资源和显示须到T07才可判。

S0只有质量不低于B0但未达Q/R时，可以用唯一追加席位试S1；必须先无新关键错/保护损失且资源可承受。父配置失败不会自动开放所有子项。所有候选都不通过时，结束本轮并保留默认，不临时降线。

## 默认替换验收继承

| 验收 | 要求 |
|---|---|
| 历史17段121语义项 | 5秒内稳定正确DOM至少109/121 |
| 独立保留集 | >=6独立来源、>=60语义项，5秒正确覆盖>=90% |
| 句末延迟 | 1x正确匹配P50<=1.5s、P95<=3s，同时公开全部分母与missing |
| 1–3s短句 | 至少20条，起点到正确中文P90<=4.5s，missing为无穷 |
| 语义与静音 | 新关键错0、保护项无损失、原10s静音0字幕 |
| 可读字幕 | 继承07章连续停留公式、结束/遮挡证据与多片段规则；缺测不计正确DOM，WS另列 |
| 生命周期 | 暂停、seek、切源、取消、EOF、busy、断线、后台与全屏真实验证 |
| 长稳 | 同一实际session60分钟，无串线/积压/原声中断 |
| 内存稳定 | 最后热态10分钟中位footprint相对首个热态10分钟增加不超过max(10%,128MiB) |
| 默认低占用 | 完整树热态physical footprint峰值<=2.5GiB目标；缺footprint为inconclusive；超预算只能单列取舍，不能默认替换 |

历史Chrome全屏TypeError: not granted属于未通过或未到达，不能继承为pass；没有用户授权不得绕过权限。纯音乐/笑声/BGM必须单列；没有对应可信标注则不宣称该域更强。

若精度获益但超2.5GiB，报告“高资源候选、默认不合格”，不继续长稳耗费，除非用户另外同意研究该档。12GiB、压力/交换与队列硬停止仍按02章。

## 自动决策的边界

未来p0/p3/decisions.py须校验全部证据、锁、配置和人工状态，不能只用总分。多阶段fail-closed：有未评分、缺hash、未知语义、缺DOM或许可风险时不输出release_candidate。

工具算数通过、Schema通过、脚本测试通过、作者论文优秀都不是发布门。最终独立review签字和用户明确选择才允许生产切换；单次成功不自动合并、推送或删旧模型。
