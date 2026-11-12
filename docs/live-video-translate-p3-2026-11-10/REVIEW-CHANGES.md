# 2026-11-10 P3 本地联合审查与修订

范围为本P3目录，结合77e24c3当前实现、P2实测及11月9日清理状态。主代理与独立gpt-6-astra/xhigh分别核查，主代理统一修订。原稿52文件及独立原始回执保存在本机ignored的p0/evidence/p3-plan-review-20261110-144948/；没有改产品代码、启动模型测试或重新下载。

## 已修问题

| ID/级别 | 原问题与实际影响 | 修订位置/结果 |
|---|---|---|
| A-P1-1 | 可读DOM停留门遗失，瞬闪也可能计正确覆盖 | 07/08、evaluation-contract继承max(600ms, alnum字符/12字每秒)，要求闭合可见区间/无遮挡；逐必要片段合格后回溯首次可见 |
| A-P1-2 | R2T2只锁160/U1，默认Chinese、首块/步预算与token cap含义不明确 | 04/registry锁Japanese、空context、首320/后160、lookahead/U1、rollback=false与2..4步预算；finish预算4、正常步进和session终止分开 |
| A-P1-3 | 无词时标模型被要求生成旧提交器的frontier/pending，可能永久等待或错归属 | 02/04/09固定S0_UNTIMED_VAD_SNAPSHOT，消费release屏障时稳定prefix新后缀；因果包络不伪称声学词时标；B0比较明确为组合效果 |
| A-P2-4 | 整块EOF时pin不解锁U1尾，可能丢失或偷偷把草稿升final | 04/registry保留pin行为、withheld_tail及missing/partial；整块/短尾/重复finish须stub |
| A-P2-5 | 作者先全段提特征的缓存模拟被误当原PCM在线入口 | 05/T04补跨包PCM/预加重、STFT完整支持域和feature-ready水位，加入相同前缀/不同未来反例；公开normalize=NA不误报全段归一化 |
| A-P2-6 | parity未锁参照，Q4对FP16差异可能被误判Mac部署失败 | 04/runtime锁区分同工件/adapter接线/非量化诊断/最终质量，冻结reference与容差；缺参照明确未证 |
| A-P2-7 | score无人工判定即停，与允许AI暂评研究晋级冲突 | 09/10区分unscored、provisional、pending-listen；无独立文本判定先导出待审包，正式人审/发布不冒充通过 |
| A-P2-8 | RSS求和跨进程拓扑可制造内存收益，热态及Chrome范围不唯一 | 07/08/config将尚未执行的R_memory主指标统一为同surface热态完整集合footprint；RSS辅助，缺测inconclusive；不改P2正式R |
| R-1 | T02用unittest收集现有pytest函数，本机实际Ran0/exit5 | 03使用现有pytest；文档helper的unittest与产品/实验pytest分开 |
| R-2 | helper放过同scope/channel的source_end_sample回退 | 已实际复现90→10仍pass；先加失败反例，再修helper/schema；跨channel端点仍独立 |
| R-3 | 机械合并文件无可重建入口，修改后两份权威容易漂移；旧包回执来源混淆 | 新增validate_package.py及渲染回归；--refresh生成副本/实际测试/清单，--check只读核对；原包回执标历史，本地输出另存 |
| R-4 | 独立worktree没有ignored环境/资产；D1和机制控制成本执行落点不清 | 03/10明确基点只读环境、全新B0 run、材料根与Hy链接；D1冻结/缺失边界和控制预算纳入任务 |

独立初审完整问题、官方源码锚点、未实测边界见[ASTRA-INITIAL-REVIEW](validation/ASTRA-INITIAL-REVIEW.md)。最终独立复核记录在修订后另附；原包自称通过不能替代本轮最终复核。

## 保留的方案与尚未证明之处

保留R2T2与123M RNNT两条有界主线、一个预选备选及唯一条件槽，总计3+1配置、最多2 E2E。P2失败记录与20/18基线不改写；新候选仍须重建同轮B0、实际raw-PCM因果门和中文质量门。

R2T2首320/后160与no_reset/W16O8是本项目拟组合，不是已在M1验证的官方recipe。无时标S0改变MT分组，不能声称只换ASR；token预算和EOF缺口会真实计入漏译，不救分。日语123M的源码pin已核实，不等于.nemo权重、导出图或M1算子已锁定。

2.5GiB仍是原低占用档位目标；R_memory只是新研究门，不冒充原R_latency，也不凭内存收益自动换默认。没有增加新的云/4090/字幕/OCR路线，没有解封test/或代替人工复听。

## 本轮验证

实际测试及Schema/实例/链接数量见[BUILD-VALIDATION](validation/BUILD-VALIDATION.json)，完整输出见[LOCAL-OFFLINE-TESTS](validation/LOCAL-OFFLINE-TESTS.txt)。修订后的副本由脚本机械生成，原51项清单在快照内保留，新清单绑定当前文件集。所有工具只验证文档、部分结构与算数，release_eligible=false。

用户后续明确授权实施时，从README和T00开始；本次评审不启动T00模型环境准备、不提交或push。

## 最终独立复核

[ASTRA-FINAL-REVIEW](validation/ASTRA-FINAL-REVIEW.md)：PASS_WITH_DOCUMENTED_LIMITS，无未关闭阻断项。独立复核实际运行只读整包检查和端点回退反例；47项完整文档工具测试是主代理实际执行，reviewer仅回读日志，没有冒称重跑。封版时19份核心文件SHA256再次逐项匹配；最终附件/台账与清单封装未改变这些核心规格。原包检索计数沿用其历史台账，本轮没有重新发出全部45次查询，也没有据此制造新的M1成绩。
