# M1 Pro日语实时中文字幕：P2探索与验证执行包

**本目录是P2测试的唯一计划入口，当前实验已收尾归档。** 研究快照2026-11-07，2026-11-08完成主代理及独立Astra xhigh审查。随后完成B0工程基线、B1四配置及2026-11-09 B2/B3各两配置；八个核心候选均未过Q/R质量前提，当前仍用Reazon CPU + Hy-MT2，产品默认保持不变。实际成绩与限制以[B0](../p2-b0-results-2026-11-08.md)、[B1](../p2-b1-results-2026-11-08.md)、[B2](../p2-b2-results-2026-11-09.md)、[B3](../p2-b3-results-2026-11-09.md)报告为准。

2026-11-09按用户要求清理未采用的实验模型、独立环境和下载框架，保留当前reazon-hy、原始证据、源码与测试素材，见[清理与验证记录](../local-runtime-cleanup-2026-11-09.md)。下列执行命令作为历史复现说明保留；重新推理前须恢复相应锁定下载与环境。不得因恢复会话或剩余名额自动重新下载、继续实验或改默认。

B4的`AST-G4-ZH`已使用`B4-1`名额并在G0退出：3语音均无合规中文字幕，结构有效但task失败；原88×2/34项评分与成本改善为NOT_RUN。见[预登记](../p2-b4-gemma-zh-protocol-2026-11-09.md)及[结果](../p2-b4-gemma-zh-results-2026-11-09.md)。剩余两个B4名额未使用，不能自动扩池；本次例外不代表B3质量通过。

## 后续AI从这里开始

1. 读[合同与真实基线](docs/00-contract-and-baseline.md)、[实验设计](docs/02-experiment-design.md)、[任务与接口](docs/03-phase-tasks.md)、[验收规则](docs/08-scoring-and-acceptance.md)、[执行/恢复](docs/11-ai-orchestration.md)。按模型任务再读04/05/06，不必逐次装入整份聚合文档。
2. 按[EXECUTOR](prompts/EXECUTOR.md)执行；B0/T00–T02、B1/T03–T05、B2/T06和B3/T07–T08的离线首筛与独立审查已完成。`python -m p0.p2.cli`支持B0及八组B1/B2/B3首筛，执行说明见[实验包](../../p0/p2/README.md)；Vox实测512MiB缓存配置使用单独冻结的诊断入口，见实验包；候选replay/accept及产品接入仍未实现。核心池已经结束，没有合格候选可自动进入B5；B4仅在原条件与预登记假设满足时分配，不重复失败配置追分。
3. 由[experiment-registry.json](config/experiment-registry.json)选择候选；[model-lock.json](config/model-lock.json)是研究锁模板，文件hash/local_path等须在本机preflight解析。只准备选中项，不下载全部菜单。未解析绑定只阻塞依赖项。

4. 每次新建`p0/evidence/p2-<run-id>/`保留原始输出与CPU/GPU/共享内存对照，不覆盖历史。阶段门槛与停止条件按02/03/08；中断读[RESUME](prompts/RESUME.md)，审查用[REVIEWER](prompts/REVIEWER.md)。

注册表保留原预登记菜单，不是运行状态数据库；实际执行状态来自各run的冻结锁、decision及阶段报告，不能将模板中的planned状态当作要求重跑已失败配置。

分章、config和schemas是维护真源；[完整阅读副本](P2-AI-EXECUTION-PLAN.md)由脚本生成，禁止单独修改。全部修订与取舍见[审查记录](REVIEW-WORKFLOW.md)，实际检查见[交付验证](docs/13-delivery-validation.md)与[BUILD-VALIDATION.json](BUILD-VALIDATION.json)。原始GPT Pro包另存于本机ignored证据，不作为第二份执行真源。

## 有界测试顺序

B0复现9PCM/88切片/62文本/34语义与20/18基线。B1：Reazon入口静音、日文标点、PLaMo0.8/2B H0；B2：SenseVoice及whisper-ja760；B3：Voxtral960连续因果流及Gemma4完整音频AST。共8个核心配置；27项登记包含条件菜单，不是27个必跑任务。后续B4全局最多3个正式消融，完整1×组合最多2个。

**当前无人复听，不启动人工任务，也不提前打开封存test/。** 独立AI文本复核、保守算术过Q/R门后可继续暂定1×与工程验证；最终语义、人工校时及正式发布仍待确认。Voxtral等延迟R路线在现有18项正确/未校时条件下不能作正式P95收益主张，允许的暂定工程测量不等于R通过。净增不足、关键错或保护项丢失仍按原规则失败。

纯音频→最终简体中文是本轮范围；字幕轨、OCR和未来音轨预读不执行。所有本机模型运行串行，依赖隔离；不修改engine lock为新模型让路。无赢家可以完成本轮，保留默认。历史109/121、独立集90%、正确匹配P50≤1.5s/P95≤3s、短句P90≤4.5s、可读DOM和60分钟等正式门槛不变。

## 现成工具与检查命令

从**本执行包目录**运行，Python 3.10+标准库可检查证据算术；三个示例均为synthetic，不是模型输出：

```bash
cd /Users/luo/Documents/github/jp-live-video-tanslate/docs/live-video-translate-p2-2026-11-07
python3 -B -m unittest discover -s tests -p 'test_evidence_tools.py' -v
python3 -B scripts/evidence_tools.py gate examples/synthetic-score.json
python3 -B scripts/evidence_tools.py timing examples/synthetic-timing.json
python3 -B scripts/evidence_tools.py events examples/synthetic-events.jsonl
```

从**仓库根目录**，用现有engine环境的jsonschema依赖复核整个包。没有此依赖时按engine开发环境约定安装，不升级生产锁：

```bash
engine/.venv/bin/python -B docs/live-video-translate-p2-2026-11-07/scripts/validate_package.py --check
```

维护本包后，用同一命令的`--refresh`替换`--check`，重建阅读副本、实际测试报告和MANIFEST.sha256，再`--check`确认。refresh会用engine开发环境运行全部辅助器及阅读副本链接回归测试/合成示例与Schema/引用检查，**不会下载权重、跑推理、开启Chrome或修改产品源码**。不需要ZIP即可在本仓库实施。
