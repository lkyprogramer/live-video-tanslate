# Astra Final Review

**Status: PASS_WITH_DOCUMENTED_LIMITS**

修订后的 P3 可以作为后续有界探索的执行规格。初审 3 项 P1、5 项 P2 在文档/配置层均已关闭；最终复核发现的 EOF 预算、旧 S0 适用范围和实际处理水位残余，也已修正并回读。当前没有未关闭的阻断 finding。

本结论不表示 P3 已实现、模型在 M1 可用、质量或资源门已过，也不授权下载、运行或更换生产默认。

## Reviewed Scope

- 仓库：`/Users/luo/Documents/github/jp-live-video-tanslate`，基点 `77e24c3e490a11721041a40ad4bd203f51f84039`。
- 评审目标：`docs/live-video-translate-p3-2026-11-10/` 当前修订版，对照本次 `original/` 稳定快照和 `astra-initial-review.md`。
- 实际核查：初审相关章节与 registry/evaluation/runtime 锁、提示词、schema、辅助脚本与新增测试；生成正文/链接/hash 检查；根代理本次构建回执。
- 本轮复用初审已经直接读取的固定官方源码证据，没有把包内原制作回执或作者成绩当成本机结果。未追加外部搜索或新的模型假设。
- 本轮只写本证据目录的最终报告；没有修改主规格、产品代码或 P2 冻结实现。

## Finding Closure

| 初审项 | 状态 | 当前关闭依据 |
|---|---|---|
| A-P1-1 可读 DOM 停留门 | CLOSED_SPEC | 07/08 与 evaluation-contract.readability 统一继承 max(600ms, alnum字符数/12字每秒)，要求闭合连续可见区间/无遮挡，跨必要片段逐条达门后回溯首次可见；WS 单列。 |
| A-P1-2 R2T2 驱动身份 | CLOSED_SPEC | registry 与04锁 Japanese、空 context、首320/后160ms、lookahead160、unfixed_chunk_num=0、U1、rollback=false、步预算2..4；finish显式4，不以None触发4096；步预算用尽与会话异常分开。 |
| A-P1-3 无词时标提交 | CLOSED_SPEC | 09 的 frontier允许None，StablePiece按alignment_kind区分可证decoder区间/因果包络；R2T2仅走 S0_UNTIMED_VAD_SNAPSHOT，不调用旧StreamCommitter。VAD屏障等待实际已完成native计算水位，内部buffer入队不推进consumed；只消费稳定prefix新后缀。 |
| A-P2-4 整块 EOF | CLOSED_SPEC | 保留pin的空buffer行为，不晋升U1隐藏尾；withheld_tail进入missing/partial，整块/短尾/重复finish被列为未来stub门；不会看分后额外解锁。 |
| A-P2-5 NeMo 原始 PCM 前端 | CLOSED_SPEC | 05区分全段特征模拟与原始PCM流；冻结跨包预加重/STFT支持域、feature-ready与encoder实际消费；同前缀不同未来反例；一次性参考只比较已成熟released frames，真实EOF另处理尾帧。 |
| A-P2-6 parity 口径 | CLOSED_SPEC | 04/runtime锁要求reference backend、同工件/frontend/input/schedule及容差；区分同工件回归、接线一致性、非量化诊断和最终质量；Q4与FP16差异不自动判Mac部署错误。 |
| A-P2-7 暂定评分 | CLOSED_SPEC | 无独立文本判定为unscored；冻结AI参考与独立逐项对账允许provisional研究筛选，15项待复听与最终人工/发布资格仍单列未过；没有相互矛盾的“无人即停止所有研究”CLI规则。 |
| A-P2-8 资源口径 | CLOSED_SPEC | 未执行的R_memory改为同surface、同hot input+drain、完整集合footprint峰值；WS/Chrome集合明确，逐采样汇总再取峰值，缺测inconclusive，RSS辅助；未修改P2正式R_latency。 |

根侧 R-1 至 R-4 也已检查：P2函数式测试示例使用pytest；prefix helper拒绝同channel source_end回退；维护真源与机械合并正文分开，有可重建/只读核验入口；worktree只读复用保留环境/材料，新B0 run不覆盖历史，D1缺失只阻断对应结论，控制成本计入父预算。这里的“关闭”是规格和文档工具层，不是未来模型runner测试已通过。

## Final Consistency Checks

- R2T2的旧“所有S0均调用StreamCommitter”“S0只隔离ASR质量”语句已被限定/删除。产品比较明确为模型与提交组合；基础Qwen机制比较须匹配同驱动/子策略，不能把不同切分的收益纯归因后训练。
- consumed是已完成native计算使用的真实PCM末端；R2T2尚未凑首320/后160ms时不能靠buffer长度越过VAD屏障。无词时标模型不伪填decoder frontier；pending=None不表示没有隐藏尾。
- NeMo的raw accepted、feature-ready和encoder actual consumed有独立记录要求；预处理完成或已入buffer不等于稳定token已产生。上述内容是实施门，尚未由真实模型验证。
- Q/R_memory的离线入口与T07完整显示/资源通过分开；质量前提最多允许研究晋级，不提前标内存路线pass。
- 3+1正式配置、最多2 E2E、工程与运行上限、原P2失败和默认Reazon+Hy均保留。所有模型未锁字段仍null/blocked，没有伪造已安装或已运行状态。
- 修改范围检查时，`git diff --name-only HEAD`为空，`git status --short`仅列本P3未跟踪目录；本次评审报告位于ignored证据目录。没有观察到产品/P2受版本管理文件的改动。

## Validation Actually Performed by Reviewer

1. 在仓库根实际执行：
   `engine/.venv/bin/python -B docs/live-video-translate-p3-2026-11-10/scripts/validate_package.py --check`
   最终回读后结果 **exit0 / PASS**：5个schema、7个实例、100个本地链接，生成正文与清单一致。该命令不重新跑模型或推理。

2. 用 `engine/.venv/bin/python -B -` 直接导入已检查的helper并执行两项合成断言：同scope/channel端点90→10，累计文本仍增长时，实际抛出 `source_end_sample regressed within scope/channel`；不同channel端点90/10仍合法且 `release_eligible=false`。这是无模型算数/结构验证。

3. 实际读取根代理最新 `validation/BUILD-VALIDATION.json`（`2026-11-10T07:22:50.908365+00:00`）及 `LOCAL-OFFLINE-TESTS.txt`：根代理47项文档工具测试通过，5组示例退出码依次0/0/1/2/2。本 reviewer 没有重复运行整套47项，因此不把其表述为 reviewer 独立实跑。

4. 实际读取并计算下表核心文件SHA256，采集时间 `2026-11-10T07:23:28.894353+00:00`。这些哈希绑定本次已审内容；后续加入本报告附件及刷新清单属于封装步骤，不得借此改变核心规格。生成阅读副本哈希记录的是加入最终审查附件前已核验版本。

## Unverified and Required Later

- R2T2 arm64编译/链接/算子、Q4+Q8数值、真实滚窗/EOF/单worker生命周期均未运行。
- 123M实际.nemo内部配置、保缓存ONNX、原始PCM前端及逐块RNNT parity均未运行。
- 未产生P3中文语义、1×/Chrome可读DOM、内存/功耗或60分钟数据；2.5GiB与20%内存门只是冻结要求。
- 未复听15项，未读取封存test/或音频素材，未打开holdout，未获得商业许可结论。
- 无模型stub接口测试属于未来实施工作；本轮schema/helper通过不能代替它们，更不能代替真实设备验收。
- 若实施无法建立可比reference、因果前端或完整进程集合，按规格保留blocked/inconclusive，不能将未知写成模型0分或通过。需要扩预算/改策略时重新登记，不用正式输出救分。
- 没有提交、push、安装依赖、下载权重、修改默认或启动P3执行。

## Reviewed Core SHA256

路径相对 `/Users/luo/Documents/github/jp-live-video-tanslate/docs/live-video-translate-p3-2026-11-10`。

| File | SHA256 |
|---|---|
| `docs/00-contract-and-baseline.md` | `3b4a955452c590f1ccbd1f715b41dd25e3ebca62c71948f134984362b96b3a62` |
| `docs/02-experiment-design.md` | `a662e0c4c51dbba955ccdcdc678971dd55ff475efdf97d244ba1d0d4dd7fb292` |
| `docs/03-phase-tasks.md` | `16a85ba77886006ea32a9deefccd24f185b98b4367a8af018b67418c85788564` |
| `docs/04-r2t2-mac-spike.md` | `542d73eb86107ba4af6979664eeac693bab16cc6d6082fd931b49482b10b424f` |
| `docs/05-japanese-cache-aware.md` | `4e56de09aaaaf89d7d91254e0604d28d419caa5c4f94b57c7636d9a4799dd246` |
| `docs/07-observability-and-scoring.md` | `777d47793ab1a70b85bae210aa072356430cb30e7959e5c0d048c50239589d6a` |
| `docs/08-gates-and-stop-rules.md` | `52c729509e09aa0835d644029b972d5c1a7ecf146570ff5d546fa65356ae0087` |
| `docs/09-runtime-integration.md` | `2c554ba3379c77583c432edd572b0c3bbff1af22be893c3cbf3263732b2e4c3d` |
| `docs/10-ai-execution-and-recovery.md` | `0d1a91a4a25f21e01eeeb5e82a0b0759b3f70e83a5db35071acf540107401447` |
| `config/experiment-registry.json` | `7a6a1329b54811260a0573fec7a75d288430ce70a53d664e29ebd3dd34b25ff6` |
| `config/evaluation-contract.json` | `7105bface9eb3a5251f75bf6345e4b5ee632a67b623f5ace82ee9b2ce874e3b0` |
| `config/runtime-artifact-lock.json` | `8756a3f9a1bf3d6db0e66b413bca783b5e103b9183793fabce6e5c25212bdda8` |
| `config/model-lock.json` | `9a8c8f0a0fc5bb40a650b5b35d64e66b39791cda7a82d516d6329d0f405fb615` |
| `scripts/p3_evidence.py` | `8220ffc6af908f38c6587178c1416384586d5e53b39029f6ee535e4a1cb6a383` |
| `scripts/validate_package.py` | `a27c7b2f14fb7b94b63e85f17e8c4e59297d6e212a031f69889b2d9f758100ed` |
| `schemas/prefix-audit-input.schema.json` | `69255c9994df6b84af0ac553a2c339424b26bd61131bd568046556613a4546d9` |
| `tests/test_p3_evidence.py` | `7edafab1677c2213d03a5faa3ec940c62c4fe654904a7a58e209e596586294fa` |
| `tests/test_package_render.py` | `cbf7cb2f2d8a8e7588769bd291ee77122aa6f53e0d599625dcec98f0b579156c` |
| `P3-AI-EXECUTION-PLAN.md` | `1a6dfb9caa0965f30037a1a1dcfe47341a95da4bf4082dc9bb63cf8ffe7396d6` |

