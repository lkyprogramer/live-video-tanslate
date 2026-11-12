# 12 本地复核与交付验证边界

## 本次任务与来源

用户要求评审并直接完善P3方案，作为后续探索基础。本次只修改本目录规格、配置、文档辅助工具及测试；产品engine/extension、P2冻结代码与历史实验记录保持原状。没有安装依赖、下载权重、开启新模型或浏览器实验。

GPT Pro原始52文件保存在本机ignored的p0/evidence/p3-plan-review-20261110-144948/original/。原validation/INDEPENDENT-REVIEW.md和OFFLINE-TESTS.txt是随包历史回执，已标来源，不能替代本次复核。源码台账66条Git blob已与本地77e24c3逐项核对；这证明pin一致，不是本次逐文件重新精读或模型测试。

## 独立审查与修订

独立Astra使用gpt-6-astra/xhigh、独立上下文审查原稿、现有代码/历史结果和一手源码，初审发现3项P1、5项P2，见[独立初审](../validation/ASTRA-INITIAL-REVIEW.md)。主代理另发现pytest命令、prefix端点回退、缺可重建文档工具及worktree资产复用问题。问题、修法和保留边界见[修订台账](../REVIEW-CHANGES.md)。

本次补回可读DOM停留门、锁定R2T2实际driver/整块EOF/无时标S0、补raw-PCM前端因果门、明确同工件parity与暂定AI评分、统一内存footprint口径，并修正辅助器和可复验命令。R2T2与123M两主线和3+1配置/2E2E上限保留，P2正式R延迟门未改。

修订后的最终独立复核单独保存在validation/ASTRA-FINAL-REVIEW.md；没有该记录或仍有阻断项时，不能引用历史回执声称已完成最终审查。

## 实际可执行的本地检查

源文件、Schema、模板、五个合成例的预期退出码、阅读副本、相对链接与SHA清单由scripts/validate_package.py检查。它使用当前engine开发环境已有的jsonschema；三个evidence helper与离线单测本身仅需Python标准库。

~~~sh
engine/.venv/bin/python -B docs/live-video-translate-p3-2026-11-10/scripts/validate_package.py --refresh
engine/.venv/bin/python -B docs/live-video-translate-p3-2026-11-10/scripts/validate_package.py --check
~~~

实际执行数量/结果见[本地构建回执](../validation/BUILD-VALIDATION.json)和[原始测试输出](../validation/LOCAL-OFFLINE-TESTS.txt)。反例前缀预期exit1、未知必需锁预期exit2；release_eligible始终false。新prefix回归先在原helper上实际失败，再修复；同channel端点不回退，跨channel仍独立。

--refresh只能证明当前文档工具通过，--check不重新推理。维护分章/config/prompts后重新生成阅读副本，禁止手改两份权威文本。新增独立review附件只改变文档清单，也须更新清单并check；不把review通过转成模型成功。

## 明确未执行

没有在M1构建R2T2、运行123M RNNT或导出ONNX；没有任何P3中文准确率、1×/DOM、CPU/GPU/共享内存、能耗或60分钟新结果。权重revision、实际工件SHA、目标runtime/parity仍须按T00起逐阶段补齐。没有人工复听15项、解封test/、改默认、提交或push。模型许可与真实人审不能由本包Schema或AI代签。

当前完成的是可执行研究规格及工具校验。用户后续明确授权实施后才从T00推进；遇数据/运行时阻塞保留事实并停止该依赖分支，不据此给模型打0分。
