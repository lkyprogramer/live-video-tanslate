# 分阶段任务与实现接口

> **For agentic workers:** 使用当前环境可用的 subagent-driven-development 或 executing-plans 流程逐任务实施；各任务完成后独立审查。缺少同名技能时按本章任务依赖和验收合同执行，不把工具名称作为停工理由。

**Goal:** 建立可复算、可恢复、能比较短切片级联、连续流式与AST的实验链，并只将真实获益的候选送入产品验收。

**Architecture:** 保留生产v1网关和默认profile。新增独立的 `p0/p2/` 实验包，以冻结数据、模型adapter、因果runner、事件日志和评分器连接各路线；每种运行时可在单独子进程/环境运行，但同机基准串行。只有晋级后才增加生产策略类型。

**Tech Stack:** 项目已锁定Python/uv、sherpa-onnx、MLX、Swift/CoreML（条件）、TypeScript/Chrome；辅助证据工具为Python标准库。

**Spec:** [目标合同](00-contract-and-baseline.md)、[实验设计](02-experiment-design.md)、[监控](07-telemetry.md)、[评分](08-scoring-and-acceptance.md)。

## 3.1 全局约束与审查重点

所有任务共同服从00/02章；新增关键错误=0、保护项无损失、数据分母不删除。模型运行串行，代码阅读/实现/审查可并行；共享代码修改由任务owner合并后再验证。不得自动推送或发布。

五个高风险输入及所属测试：

1. 真实尾帧不足一窗、人工静音、重采样余数：T01/T03/T07，任何字幕范围不得越过真实accepted_samples。
2. 否定、疑问、数值、人物关系和源文已有错误：T04/T05/T06/T08，流畅但改义不能算成功。
3. 暂停/seek/取消后仍有GPU调用返回：T01/T11，epoch改变后旧输出和历史缓存不可提交。
4. 没有输出、被过滤输出、未完成翻译：T01/T10，保留在覆盖分母中，P95不得仅报成功样本。
5. 原生流长会话、cache增长、默认token cap：T07/T12，短样本通过不等于60分钟可用。

## 3.2 文件职责与最小接口（均为拟新增）

| 文件 | 单一职责 |
|---|---|
| `p0/p2/cli.py` | 命令解析、任务状态、退出码；不包含模型实现 |
| `p0/p2/types.py` | 输入、输出、锁文件和事件的数据类型 |
| `p0/p2/freeze.py` | 仓库、资产、模型、种子与参考冻结 |
| `p0/p2/events.py` | 单调时钟事件、采样计数、因果约束 |
| `p0/p2/runner.py` | fixed-cuts/fixed-text/causal-stream/realtime-1x 调度 |
| `p0/p2/scoring.py` | 输出对齐、盲审材料与确定的算术；不自动宣称语义真值 |
| `p0/p2/commit_policy.py` | 内部ASR增量到有界MT请求的提交策略 |
| `p0/p2/adapters/reazon.py` | 现行Reazon与入口padding参数 |
| `p0/p2/adapters/punctuation.py` | 仅追加标点的文本变换 |
| `p0/p2/adapters/plamo.py` | 官方PLaMo模板/生成/会话历史 |
| `p0/p2/adapters/sensevoice.py` | sherpa日语识别及结构化标签 |
| `p0/p2/adapters/whisper_ja.py` | 自定义词表Whisper质量探针 |
| `p0/p2/adapters/voxtral.py` | 有状态音频流、队列、drain和取消 |
| `p0/p2/adapters/gemma_ast.py` | 多模态音频有效性与中文输出 |
| `p0/p2/adapters/optional.py` | 条件路线registry，具体大实现再独立成文件 |
| `p0/p2/integration.py` | 将已选adapter接到v1 backend的实验桥 |
| `p0/p2/tests/` | 仅针对数据/时钟/状态/模板风险的测试 |
| `p0/evidence/p2-<run-id>/` | 本轮私有原始证据，历史只读 |

避免引入一个跨所有框架的复杂推理平台。大模型依赖相互冲突时，adapter可启动隔离worker，通过有界JSONL控制消息和本地PCM文件传入，不能让整个产品环境跟着升级。权重不复制到文档包或Git。

核心类型由T01定义，后续adapter遵守：

```python
@dataclass(frozen=True)
class AudioSpan:
    clip_id: str
    session_id: str
    epoch: int
    start_sample: int
    end_sample: int
    accepted_samples: int
    pcm_path: str
    pcm_sha256: str

@dataclass(frozen=True)
class SourceUnit:
    source_id: str
    span: AudioSpan
    text_ja: str
    metadata: dict

@dataclass(frozen=True)
class TranslationResult:
    source_id: str
    raw_zh: str
    text_zh: str | None
    finish_reason: str
    metadata: dict
```

固定切片ASR接口：`decode(span: AudioSpan) -> SourceUnit`。MT接口：`translate_one(unit: SourceUnit, session: object) -> TranslationResult`。AST接口：`translate_audio(span: AudioSpan, session: object) -> TranslationResult`。native streaming worker内部按单一执行者串行调用model session，向runner提交带真实sample范围的SourceUnit；不得让第三方线程直接发WS。

## 3.3 统一CLI合同

**B0与B1/B2六候选离线子集已实现。** B0使用`--experiment B0`；B1/B2分别用`freeze --phase B1|B2`和候选ID的fixed-cuts/fixed-text。B2的`B0-CURRENT`先逐字复现Reazon88，再回归两轮MT62。score消费显式逐项AI复核，不自动生成语义真值；资源有独立后处理。执行说明见[实验包](../../../p0/p2/README.md)。其他候选、候选replay/accept及holdout仍明确unsupported。

```text
python -m p0.p2.cli --run-dir PATH --registry PATH freeze
python -m p0.p2.cli --run-dir PATH --registry PATH preflight --experiment ID
python -m p0.p2.cli --run-dir PATH --registry PATH probe --experiment ID --mode MODE
python -m p0.p2.cli --run-dir PATH --registry PATH score --experiment ID
python -m p0.p2.cli --run-dir PATH --registry PATH replay --experiment ID --repeat 1|2
python -m p0.p2.cli --run-dir PATH --registry PATH accept --experiment ID --stage engineering|holdout
python -m p0.p2.cli --run-dir PATH --registry PATH report
```

MODE为 `fixed-cuts|fixed-text|causal-stream`。replay始终是真实1x；不能用probe时间冒充实时。preflight只验证已选择候选的依赖/模型，不自动下载registry全部模型。accept由状态机校验前置gate，不允许跳过质量与因果验证。

CLI退出：0=本次操作已正常完成（即使实验结论failed）；2=输入/证据不合法；3=缺模型/数据/权限或不支持；4=运行超时/资源止损。晋级只读decision.json，不根据进程exit=0判“有效”。

本包的固定入口为仓库根下 `docs/live-video-translate-p2-2026-11-07/`，不要移动或再复制一份计划。需要独立worktree时按项目规则放到 `/Users/luo/.codex/worktrees/` 的任务专属目录，并带入本轮尚未提交的执行包；本机ignored的PCM、模型缓存、raw证据不会随worktree出现，T00须从原工作区只读引用并校验，不能将缺失资产当成模型失败。以下命令从**仓库根目录**运行，且仅在T01实现后有效：

```bash
uv run --project engine --frozen python -m p0.p2.cli --run-dir p0/evidence/p2-20261108-a --registry docs/live-video-translate-p2-2026-11-07/config/experiment-registry.json freeze
```

若新模型需要不同依赖，preflight创建该候选的隔离环境并冻结其lock；runner记录实际python可执行路径。不要通过修改当前engine lock来使某一个候选“能跑”。

## T00：冻结现状并恢复可比较基线

**依赖：** 无。**文件：** 新建freeze.py；读取原p0/exploration.py、exploration_score.py、next_vad_packet.py及本地证据；不调用会覆盖历史目录的入口。

**输入/输出：** 输入仓库HEAD、实际profiles.toml、冻结PCM和参考；输出run-manifest.json、asset-manifest.json、reference-manifest.json、baseline-score.json、pending-listen.json。

- [ ] 记录 `git status --short`、HEAD、工作区diff与实际使用源码哈希；HEAD不同于本文时列变更差异，保存新的B0，不回滚用户工作。
- [ ] 发现并读取适用AGENTS.md；将环境限制记入run-manifest，不自行扩大任务授权。
- [ ] 检查9PCM、88切片、62日文和34参考；缺失时按原prepare入口恢复同源同hash，不能伪造或拿新音频代替原资产。
- [ ] 用已有评分输出复算20/18；保留15项pending和时标敏感性。不把历史ai_reviewed转换成人工标签。
- [ ] 回归同配置ASR/MT：88日文、两轮62中文逐字比对；若不一致，先定位runtime/seed/模板/源码漂移，同轮重建控制后才能继续。
- [ ] 完成后独立审查输入哈希与分母，产出review.json；需要人工听写的项局部挂起，其他任务继续。

**完成：** 新目录可复算B0、无历史覆盖。仅公开汇总而无本地raw时，标blocked_data，并继续模型事实/模板能力审计。

T00先以`freeze.py`的最小可调用函数和已有raw完成静态资产冻结，再由T01提供CLI入口；不能要求先执行尚不存在的CLI。同配置推理复现安排在T01记录器可用后、首个候选输出前，计入B0预算。现有`exploration_score.py`是历史人工整理标签的复算入口，不能给任意新候选自动语义打分。新候选另产匿名材料、逐项AI文本判断及独立复核，保留pending状态。

## T01：实现有界runner、记录与证据校验

**依赖：** T00自动部分。**文件：** types.py、events.py、runner.py、cli.py、scoring.py及相应tests。复用p0/monitor.py，不重写系统采样器。

**接口：** `record(event: dict) -> None`；`run_probe(experiment_id: str, mode: str, manifest: dict) -> dict`；`validate_causality(events: list[dict]) -> list[str]`。

- [ ] 先写失败测试：输出range超accepted；511/512/513尾输入计数；cancel后晚到结果；漏输出留分母；candidate新增错误发生在baseline原失败单元；未知模型revision不得执行。
- [ ] 实现事件与不可变source_id，统一host monotonic_ns；保存原始模型输出、过滤后输出与终止原因。
- [ ] 实现模式分离；causal-stream每次只暴露已送入PCM，replay按单调时钟调度，禁止读取未来文件内容给模型。
- [ ] 实现状态写入的临时文件+原子替换；只有同fingerprint且全部产物hash匹配才resume，部分输出保留failed-attempt目录。
- [ ] 实现CLI上述接口、preflight按候选最小下载、score匿名化材料；未知/缺失字段失败关闭。
- [ ] 运行 `uv run --project engine --frozen python -m pytest p0/p2/tests -q`；再运行现有受影响后端/协议测试。审查错误路径后才能开始真实候选。

**完成：** 合成测试证明因果、时钟、分母和状态不会误报；不能将合成输出作为质量成绩。

## T02：建立最小端到端归因控制

**依赖：** T01。**文件：** runner.py、events.py，必要时仅实验构建钩子；生产默认逻辑保持。

- [ ] 给现行B0添加不改变请求语义的事件，比较插桩前后相同输出。
- [ ] 固定播放负载，依次测播放only、B0 ASR、B0 MT固定文本、B0全链路。
- [ ] 保存VAD放行、ASR/MT入队/开始/结束、WS、DOM时钟映射；详细方法见07章。
- [ ] 计算每类错误是missing/incorrect/late/filtered/overwritten，写attribution.json。
- [ ] 若无GPU等待证据，标“GPU竞争未证实”，继续语义路线。仅对后续GPU-ASR组合重测竞争。

**完成：** 可以逐项解释等待与错误来源；不能用高GPU利用率代替具体请求阻塞证据。

## T03：Reazon官方人工静音对照

**执行状态：** 2026-11-08已完成PAD-900双轮首筛与独立审查，13/34、12/34，未晋级；可选HEAD300未启动。方法、短句/静音与资源见[B1报告](../../p2-b1-results-2026-11-08.md)。下列清单保留为复现任务规格，不表示未来需要重新追分。

**依赖：** T00/T01。**文件：** adapters/reazon.py；tests/test_reazon_padding.py。

**接口：** `pad_for_decode(samples, sample_rate: int, head_ms: int, tail_ms: int)`；`decode(span)`。

- [ ] 先测试真实samples序列不变、dtype不变、900ms=14400个16k样本/边、输出时间仍在原span。
- [ ] 用原模型、2线程、greedy和原88切片运行PAD-900：头尾各900ms零值，不改变VAD。
- [ ] 接相同M0两种子，评分全部34项并保护静音与短句；记录新增处理音频秒与ASR耗时。
- [ ] 若有明确边界敏感性收益，最多开放PAD-HEAD300作机制诊断；没有收益则停止，不扫几十档。
- [ ] 独立审查是否把人工时间计入字幕或取回了额外真实音频，确保变量单一。

**完成：** padding可能有用或无用都有完整证据。VAD根本没输出的语音不能记为padding恢复。

## T04：日文标点恢复

**执行状态：** 2026-11-08已完成，18/34、18/34，全部62条新增位置可逆，仍有保护项损失和新增关键错，未晋级。详见[B1报告](../../p2-b1-results-2026-11-08.md)。

**依赖：** T00/T01。**文件：** adapters/punctuation.py；tests/test_punctuation.py。

**接口：** `restore_punctuation(text_ja: str) -> tuple[str, dict]`。仅允许在原字符后（含末字符）插入 `、。？`，原字符顺序与内容不变。

- [ ] 测试空输入、未知字符、原有标点、超510字符分支；保留原字符并按模型需求分块，记录块界。
- [ ] 固定AstroNier模型与词表revision，ONNX CPU 1线程；在原62条日文上运行PUNCT-JA。
- [ ] 保存逐字符插入位置；只撤销本次新增标点后须与原文完全一致，不能删掉原有标点来伪证一致；若插入/删除文字，适配器失败，不送MT。
- [ ] 接同M0两种子，分别统计问句→陈述、错误问号、句界与人物关系。
- [ ] 晋级按全部34项，不以最新v6候选的一条疑问错误替代基线收益。

**完成：** 明确标点是否改善当前B0；模型训练域或单人74句评测不能代替项目验证。

## T05：PLaMo两尺寸无历史首筛

**执行状态：** 2026-11-08两尺寸MLX H0均已完成，分别17/34、17/34（两轮），无合格WINNER；没有启动历史/词表/cache或GGUF。详见[B1报告](../../p2-b1-results-2026-11-08.md)。

**依赖：** T00/T01。**文件：** adapters/plamo.py；tests/test_plamo.py。详细规格见04章。

- [ ] 先通过官方模板、单BOS、EOS停止、无system消息、独立请求无污染检查。
- [ ] 依次运行MT-PLAMO08-H0、MT-PLAMO2-H0，仅替换MT；temperature=0、max_new_tokens=256、无跨请求cache。
- [ ] 固定原62条输入，不修正ASR错误，原过滤器保持；比较20/18同口径。
- [ ] 两次greedy相同输出作为重复性检查，不增大统计样本量。
- [ ] 通过Q的尺寸可竞争B4历史/词表名额；仅通过R的尺寸保持H0进入性能验证，不扩历史矩阵。选WINNER并写binding，不自动扩8B/31B。

**完成：** 一个可比较的两尺寸报告。上下文与cache收益不得提前计入H0。

## T06：CPU/日语专精ASR首筛

**执行状态：** 2026-11-09两配置均完成。SenseVoice为8/34、9/34，whisper-ja760为16/34、15/34；均有保护项损失与新增关键错，无晋级。原速/线程优化未启动，B2-N因缺可信Dnoise跳过。实际设置、耗时、资源与136项复核见[B2报告](../../p2-b2-results-2026-11-09.md)。

**依赖：** T00/T01。**文件：** adapters/sensevoice.py、whisper_ja.py及测试；详细见05章。

- [ ] ASR-SENSEVOICE-I8使用日语、CPU2线程，先剥离控制标签，保存emotion/event为metadata。
- [ ] ASR-WHISPER-JA760使用作者CT2转换、自定义词表兼容版本，固定一种精度/运行时，先验烟测解码与语言。
- [ ] 两者分别跑同88切片，再接同M0两种子，完成全部34项；不要为其中一个换VAD。
- [ ] 只有识别质量合格但运行资源不达标，才考虑一个明确的runtime优化；依赖失败不是质量失败。
- [ ] 专题材料就绪时按B2-N开启FunASR/GTCRN；无可靠噪声标注则该分支skipped。

**完成：** 只有实际语义净收益或预登记资源资格才进入组合池。

## T07：原生流式Voxtral

2026-11-09：B3离线首筛已完成，连续会话/因果提交及Hy中文质量有实测；分组件资源和记录器修正见[B3报告](../../p2-b3-results-2026-11-09.md)。下列1×、增量MT、240ms与长会话条目仍按资格条件执行；没有赢家不自动补跑。

**依赖：** T01/T02；不依赖T06候选成功。**文件：** adapters/voxtral.py、commit_policy.py及测试。

- [ ] 在固定MLX源码上验证feed/step、线程所有权、实际输入采样率、缓存、最大token与EOF。
- [ ] 以连续9PCM跑NATIVE-VOX-960 causal-stream：不能每个VAD段重建模型session。
- [ ] 第一组继续使用B0 VAD端点决定何时提交MT，模型保持连续状态；保存端点到native已生成文本的映射与不可映射丢失。
- [ ] 若质量有资格，再单独测NATIVE-VOX-INCREMENTAL-MT：原生标点结束或Silero端点释放；最多8秒未提交源文后在可确定token边界强制释放并标forced，至少每400ms才允许新MT请求。不向浏览器显示partial。
- [ ] 只有960ms路线通过质量与持续速度，才做240ms延迟消融；逐项检查误提交、重写与中文语义。
- [ ] 处理session cap与raw/adapter缓冲：优先保持有界必要历史；若采用静音处重建，重建必须是预登记候选行为，并评估交界漏字/重复，不能静默重启掩盖问题。

**完成：** native连续质量、计算重用、提交等待、中文效果分别报告。VibeVoice仅作有界后备，其24k与首窗口几何按05章处理。

## T08：Gemma4完整音频路径与AST

2026-11-09：G0及原88切片双轮/34语义已完成，官方协议Q失败；保留128→640内部padding修复、BF16权重与FP32激活证据。未启动ZH/history/JSON/后备或1×，见[B3报告](../../p2-b3-results-2026-11-09.md)。

**依赖：** T01；可与T07开发并行，实跑串行。**文件：** adapters/gemma_ast.py及测试；详细见06章。

- [ ] capability审计证明音频特征、encoder、projector和LM都执行；换音频与去音频负对照不能仅凭非空字符串通过。
- [ ] 固定完整模型/processor/运行时；先测官方格式。zh-only是不同ID，仅在B4触发条件及名额满足时执行，不是必跑对照。
- [ ] 88切片、34语义与B0配对；阶段耗时包括先输出日文再输出中文的全部解码。
- [ ] AST首轮采用预登记Q-only，中文语义达到Q资格后才进入真实1x；ZH-only、history和JSON均是B4的可选菜单，未分配slot不跑。
- [ ] 只有Gemma4被明确runtime阻塞或触发后备条件时开放一个Gemma3n完整音频版本；不能换成不支持音频的GGUF。

**完成：** 明确AST是否改善真实日→中，不能以JA→EN或同音词示例替代项目分数。

## T09：按证据触发的后续机制

**依赖：** 对应主候选已通过Q/R首筛或有单一可验证故障。**文件：** 对应adapter与测试。

- [ ] PLaMo WINNER：从HS、HP、G0、CACHE中按证据选变量；与其他路线共享全局最多3个B4正式配置。CACHE也占slot，以完全相同prompt验证输出一致性与收益，不额外开放免费配置。
- [ ] SenseVoice情绪：先text-only后metadata提示；不得直接把标签翻译成字幕，新增语义仍按错误计。
- [ ] AlignAtt：仅在Whisper质量通过、实际等待占比明确、runtime能提供所需attention时开放；不能套到Reazon。
- [ ] Qwen CoreML：只在需要验证硬件分配时开放；完整WithoutMLX路径与computeUnits配置都需核对。
- [ ] GTCRN/FunASR热词：只测预登记的噪声/命中条件，不能以失败字幕手工决定什么时候启用。

**完成：** 每个机制有独立控制。总附加正式配置最多3个，超过时停止并在报告中排序剩余项。

## T10：组合与真实1x验证

**依赖：** 至少一个Q/R候选；所有拟组合组件已独立筛选。**文件：** runner.py、scoring.py、实验integration.py。

- [ ] 冻结最多2个完整组合，组合单独跑离线或causal-stream语义；不得相加单项收益。
- [ ] B0与每候选各两次真实1x回放，按AB/BA顺序交错、串行执行，固定播放负载和暖态。
- [ ] 采用真实ASR/AST输出完成MT，记录第一条任意/正确/稳定中文、全分母2/3/5/15秒覆盖与队列。
- [ ] 同时报告共同命中延迟及缺失项，重算时标敏感性；没有可靠句末标注只能provisional。
- [ ] 按Q或预登记R路线判定；资源越界者只能保留较高资源档。

**完成：** realtime-comparison.json可从原始events、judgements和时钟映射重建。

## T11：接入生产策略但保持实验profile

**依赖：** T10达到正式工程资格，或符合08章保守筛选+独立AI文本复核的provisional工程例外；后者不能标正式质量/发布通过。**文件：** engine/src/subtitle_service/config.py、factory.py、对应新adapter模块、engine/tests；仅必要时修改local_pipeline.py。

- [ ] 用严格的discriminated类型加入新ASR/MT或整体AST backend；不让网页传模型路径、任意URL或语言。
- [ ] 保持backend warmup/available/open/aclose与session accept_pcm/finish/aclose，使用当前sink与中文过滤；实验profile默认disabled。
- [ ] 对阻塞模型建立可取消结果的worker、会话占用和有界队列；取消不是强杀仍在运行的MLX内核。
- [ ] 测满队列取消、晚到结果、重复EOF、短末包、busy重试、无效中文、源范围、静音。
- [ ] 跑后端全套与受影响扩展协议测试；独立审查注册/config/session生命周期，记录所有失败。

**完成：** 工程接口可用但不修改default_profile_id。质量不足不能靠前端隐藏错误过关。

## T12：Chrome、60分钟、独立材料验收

**依赖：** T11；语义参考尚待人工时允许先做工程部分。

- [ ] 原17段121项历史回归，保留旧新参考差异。
- [ ] Chrome播放only/播放+ASR/完整字幕各两轮；WS与DOM按session+seq配对，记录可见时长。
- [ ] 优先选择获准使用的连续60分钟视频；素材不足时才预生成单一loop文件并冻结拼接清单/偏移。loop仅作同一WS的工程压力测试，不增加独立来源数，不证明真实连续内容的质量泛化。
- [ ] 检查前/中/后10分钟，内存中位增长不得超过max(10%,128MiB)；队列连续10s超2s失败。
- [ ] 单独覆盖暂停、拖动、SPA换片、1.5x、全屏、Worker重启、断开、EOF；未获全屏条件记blocked，不能算pass。
- [ ] 具备参考条件后才打开封存集：至少6独立来源、60语义、20短句；双人对话/长讲解/配乐或环境音各至少2来源，单来源≤25%，`test/`计一个来源。当前无人复听时仅完成工程部分，不消费盲测。
- [ ] 最终按08章发布门槛判断；人工缺口精确挂到项目，完整工程证据照常交付。

## T13：归档结论与执行交接

**依赖：** 所有已开放任务终态；未开放路线可skipped且写原因。

- [ ] 独立review核对输入、候选数、失败保留、关键错误、分母、时标、实际运行环境和资源。
- [ ] 生成逐项结果、Pareto对照、失败/blocked清单、下一入口和精确复现命令。
- [ ] 标明“研究可行/本机已测/工程通过/人工参考待补/可替换”各层，禁止把安装成功写成效果成功。
- [ ] 如果无候选满足目标，完成本轮负结果报告并停止当前矩阵，不自动追加模型。
- [ ] 默认替换、合并、推送、发布按用户已有授权范围执行；本轮仅生成文档时不做这些操作。
