# 04 · MT 专章：PLaMo 3 的有界探索与验收

**状态：实施规格，尚未在 M1 Pro 执行。** 本章不代表模型已通过接入、质量或性能验证。
统一任务入口、运行目录、日志合同和最终上线门槛服从 [03-phase-tasks.md](03-phase-tasks.md)；`python -m p0.p2.cli` 是拟新增入口，不是当前仓库已有命令。
本章只授权实验适配器与测试工件的后续实施，不要求修改产品默认模型；任何输出改善必须同时解释质量、实时性与资源代价。

## 1. 为什么测试，以及不预设什么

PFN 于 2026-11-05 发布 PLaMo 3 Translate，官方模型卡明确支持 `ja`、`zh-hans`、`zh-hant`；日→简体中文是合法方向。[S1][S2][S3]
官方 PFML3 提供来源上下文、源文/译文历史对和术语表，因此能够逐项检验当前字幕的语义歧义与词汇一致性。[S2][S4]
训练方法和多语种平均榜单不能证明它优于当前真实 ASR 输出上的 Hy-MT2；既有 Index-Translate-2B Q8 的负结果也不能替代本模型测试。
0.8B MLX 权重约 0.98 GB，2B 约 2.41 GB；约 0.57 GB 对应 0.8B 的 GGUF Q4_K_M。[S2][S3]
量化平均分接近 BF16，但并非零差异；不能据此保证每句同文、语义零损或更低运行内存。[S2]
本章不采用“手机 0.5 秒”“M1 Pro 单句 40 ms”作为验收值，也不预设上下文必然提高准确率。

## 2. 模型锁与运行环境

| 实验 ID | 官方仓库 | 固定 revision | 角色 |
|---|---|---|---|
| `MT-PLAMO08-H0` | `pfnet/plamo-3-translate-0.8b-MLX-4bit` | `88be1a175fb222489b0f1cf8a1bed26573249a81` | 小尺寸首筛 |
| `MT-PLAMO2-H0` | `pfnet/plamo-3-translate-2b-MLX-4bit` | `61c44c8a4008f8889a3a68ad293243c9ed434771` | 较大尺寸首筛 |
| `MT-PLAMO-GGUF-FALLBACK` | `pfnet/plamo-3-translate-0.8b-GGUF` | `0c4af2b003fdd94b6868aa955414c1c86ee96997` | 仅后备选 0.8B 时，Q4_K_M |
| `MT-PLAMO-GGUF-FALLBACK` | `pfnet/plamo-3-translate-2b-GGUF` | `d44589ecd5287cd64452e9cd1e2cfdad904fe216` | 仅后备选 2B 时，Q4_K_M |

**预算上限是两个 MLX 尺寸加一个尺寸的一种 GGUF 格式后备，不是四个常规候选。** 不扩至 8B/31B，不追加量化精度网格，不默认引入 draft model。
模型锁保存仓库、revision、下载文件列表及逐文件 SHA-256。使用别的 revision、社区再量化或其他模板即产生新候选，不能沿用本组结果。
GGUF 下载限定实际 Q4_K_M 文件；仓库总 `usedStorage` 可能包含多个精度及历史对象，不能作为单模型体积。

### 2.1 官方 MLX 自定义代码

下列文件属于同一锁定产物，必须一起审阅和保留，不能只复制 safetensors：[S2][S5][S6]

- `config.json`、`generation_config.json`、`tokenizer_config.json`、`special_tokens_map.json`。
- `modeling_plamo_mlx.py`、`tokenization_plamo.py`、`tokenizer.jsonl`、`chat_template.jinja`。
- 使用官方服务时的 `serve.py`、`server.py`、`serving_memory.py` 及相关许可。

配置中的参考栈为 `mlx==0.32.3`、`mlx-lm==0.32.0`、`transformers==5.19.0`、`numba==0.68.0`；`requirements-mlx.txt` 自身未锁版本。[S5]
在独立环境验证该栈，记录实际 wheel 和版本，不升级现行产品虚拟环境。安装成功只表示环境准备完成。
官方加载启用模型和 tokenizer 的 `trust_remote_code`；只从上述固定 revision 加载已记录代码。[S2]
输入加输出的官方训练长度为 32,768 token；配置的 `max_position_embeddings=262144` 不能当成已验证的 262K 翻译窗口。[S2][S5]
全流程同一时刻只有一个 MT 模型占用 GPU、一个推理请求在执行；顺序完成基线、0.8B、2B，切换模型前结束前一进程并确认释放。
离线 MT 首筛使用已冻结文本，不重跑 ASR；端到端阶段沿用该轮已冻结的 ASR 设置，不并行启动另一个 GPU ASR 候选。

### 2.2 上游已核对的最小 Python 形状

以下 `load`、`apply_chat_template`、`generate` 和 `make_sampler` 调用已在官方固定版模型卡核对；代码仅展示接线形状，未在目标机运行。[S2]
`local_model_dir` 由模型锁解析；调用方不得通过未锁定的 `main` 自动补文件。

```python
from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler

model, tokenizer = load(
    local_model_dir, trust_remote_code=True,
    tokenizer_config={"trust_remote_code": True},
)
prompt = tokenizer.apply_chat_template(
    [{"role": "user", "content": current_ja}],
    source_lang="ja", target_lang="zh-hans",
    add_generation_prompt=True, tokenize=False,
)
raw_zh = generate(model, tokenizer, prompt=prompt,
                  max_tokens=256, sampler=make_sampler(temp=0))
```

此片段没有包含完整计时、缓存管理、过滤、日志或超时处理，不能直接当完整基准程序。
正式适配器必须核对固定版生成器的缓存生命周期；首轮每次调用使用全新请求缓存，禁止通过服务默认 LRU 复用上一条请求。
`256` 是本章建议的预登记输出上限；若主实验合同另有统一上限，开跑前统一修改配置并冻结，禁止看结果逐句加大。

## 3. 拟新增适配器契约

下列名字与签名是**待实现接口**，当前仓库未证明存在；实现位置为 `p0/p2/adapters/plamo.py`，由主 CLI 调度。
接口显式接收历史，避免模型对象或服务端隐式保存业务会话；模型可以常驻，历史与 KV 缓存生命周期必须分开。
以下为模型 worker 的低层函数；对 runner 暴露的 `PlamoAdapter.translate_one(unit: SourceUnit, session: object)` 必须遵守 03 章统一接口，将 unit/session 显式转换为这些参数，不建立第二套 runner 协议。

```python
def initialize(
    *, local_model_dir: str, model_repo: str, model_revision: str,
    runtime_lock_path: str, max_new_tokens: int = 256,
) -> "MTHandle": ...

def translate_one(
    handle: "MTHandle", *, current_ja: str, segment_id: str,
    session_id: str, epoch: int,
    mode: "Literal['H0', 'HS', 'HP', 'G0']",
    history: "Sequence[HistoryItem]" = (),
    glossary: "Sequence[GlossaryItem]" = (),
    cache_mode: "Literal['fresh', 'exact_prefix']" = "fresh",
    repeat_label: str, deadline_monotonic: float | None = None,
) -> "TranslationResult": ...

def reset_session(handle: "MTHandle", *, session_id: str, epoch: int) -> None: ...
def close(handle: "MTHandle") -> None: ...
```

`MTHandle` 保存已锁定模型/tokenizer、只读配置和显式缓存管理器；不保存未经传入的来源文本或历史译文。
`HistoryItem` 至少含 `segment_id, session_id, epoch, source_ja, available_at, committed_zh, committed_at, status`；HP 仅接受已提交项。
`GlossaryItem` 是 `{"source": str, "target": str}`；其来源、冻结文件 hash 和匹配规则在实验配置中记录。
`TranslationResult` 至少含原文输出、过滤后输出、生成 token 数、停止原因、错误、计时、prompt/hash、实际历史和缓存复用数。
`repeat_label` 只关联基线种子 11/29；当前温度为 0，不能把这个标签解释成两次独立随机抽样。
`deadline_monotonic` 来自主测试器的已冻结超时；到期按原策略取消并计失败，不额外重试以挽救该样本分数。
`translate_one` 返回后由既有过滤/提交层决定是否上屏；只有实际提交事件可以更新 HP 历史。
所有参数校验失败都产生结构化失败记录；不能返回空字符串并把样本从分母删除。

## 4. Prompt 与停止条件

固定方向为 `source_lang="ja"`、`target_lang="zh-hans"`；不使用自动语种推断，也不沿用 Hy-MT2 的聊天模板。
MLX 模板不接受常规 `system` 消息；翻译要求不能塞入 system 或拼在待译源文中。[S4]
`form=plain/polite` 只用于目标日语，本任务不设置；GGUF 的专门 system 配置能力不能倒推为 MLX 支持。[S4][S9]
当前 ASR 文本原样保留；模型适配层不偷偷纠错、拼接、补标点或删除难译片段。
模板拒绝 PFML 保留标记时记录失败；不要静默转义后继续计作相同输入。
模型 BOS id 为 `1`，生成 EOS 为 `[1, 16]`；官方服务终止字符串为 `<|plamo:tag|>`、`<|plamo:bos|>`。[S2][S6]
Chat template 已包含 BOS；手写 Completions 示例依赖服务补 BOS。验收实际 token 序列恰有所需起始 BOS，不能混叠两条路径。
达到 `max_new_tokens` 记为截断失败，保留原始内容和停止原因；出现 PFML 标记或追加下一轮 input 应先判停止接线异常。
不能仅在输出尾部删掉异常标记，便宣称 EOS 接线已经正确。

### 4.1 M0：只验证接线

1. 验证模型锁、运行库、模板 hash、方向、BOS/EOS 和 `temperature=0`。
2. 用少量合成句检查否定、问句、数字、短片段、空输入及保留标记；这些句子不计主质量分数。
3. 顺序执行同句重复、A/B 会话交替、reset 后重复，H0 不应受前一个请求内容影响。
4. 检查 token 上限、超时、异常、过滤、取消和会话 epoch 的日志闭合；迟到结果不得进入新会话。
5. 只允许修复一次有证据的依赖/模板/加载问题；失败则记 `blocked_runtime`，不反复换 prompt 试分。

若采用 HTTP，官方 `serve.py` 默认 decode concurrency 为 4、prompt cache size 为 1、cache bytes 为 1GB，不能照抄默认值开展 H0。[S7]
官方 `server.py` 与 `serving_memory.py` 带有采样及缓存 materialize 修补；裸 `mlx_lm.server` 不等价。[S8]
不假设 `--prompt-cache-size 0` 必然关闭目标状态；应以固定代码行为和重复/隔离检查证明缓存关闭。
本章不提供未经核对的完整安装或服务命令；统一 CLI 的参数、依赖安装与 smoke 由主任务文档落实。

## 5. M1：两尺寸 H0 首筛

输入沿用已冻结的原 **62 条真实 ASR 日文文本**，评分沿用原 **34 个必要语义项**、顺序、过滤器及失败分母。
原基线种子 **11/29 的成绩为 20/34、18/34**；执行前由主任务确认基线和输入 hash，不能换测试集后继续套用这两个数字。
若重现基线失败，先报告测量条件差异；不得自选更低的一轮基线制造候选优势。
分别运行 `MT-PLAMO08-H0`、`MT-PLAMO2-H0`：温度 0、当前句单独输入、无历史/词表、无跨请求缓存、无推测解码。
每组按原两轮标签记录输出；greedy 两轮同文同分是重复性证据，**不是 68 个独立语义样本，也不是方差估计**。
同一语义项涉及多个源片段时沿用原聚合规则；不挑最好一条、不按生成次数扩充分母。
人工正确日文可另作诊断输入，帮助区分 ASR 与 MT 的影响，但不能替代真实 ASR 输入的晋级成绩。

### 5.1 两条资格路径

| 路径 | 必须同时满足 | 允许下一步 | 不能宣称 |
|---|---|---|---|
| 质量提升 | 每轮净增至少 3 项；新增关键错为 0；保护项损失为 0 | 单尺寸机制消融、性能与完整链路验证 | 已可替换基线 |
| 预登记资源赛道 | 每轮总质量不退；新增关键错为 0；保护项损失为 0 | H0 性能和完整链路验证 | 质量提升、仅凭省资源可替换 |
| 不满足任一路径 | 任一必要条件失败 | 归档诊断，结束该分支 | 事后修改门槛或删坏例 |

旧基线 20/18 对应质量晋级最低 **23/21**；若 greedy 两轮同分，至少 **23/34** 才同时过线。
“关键错”与“保护项”由主评价合同预先冻结，包括否定、数值、主体关系及项目已确认的脆弱语义，不能看候选结果后重定义。
逐项报告新增正确、失去正确、新增关键错、保护项损失；总分净增不能抵消保护项损失。
新增关键错按每个语义单元与同轮基线对照；原本已经失败的单元若产生新的否定/数值/主体等关键错，也阻塞晋级。不是只检查基线正确项，也不要求候选绝对零错误。
资源赛道必须在见分前登记，并单独标记结论；质量相当只给进入性能验证的资格，最终仍须通过主文档的联合门槛。
仅选择一个质量晋级尺寸进入 M2；两尺寸都不晋级，则停止历史/词表扩展，资源赛道保留的 H0 仍可按其预算验证性能。

以下B1 fixed-text入口已实现并在2026-11-08本机跑完两尺寸，结果见[B1报告](../../p2-b1-results-2026-11-08.md)。变量须指向已经完成B1 freeze与当前控制复现的新run；不能直接用研究模板当本机锁。候选replay/accept仍未实现。

```bash
python -m p0.p2.cli --run-dir "$P2_RUN_DIR" --registry "$P2_REGISTRY" probe --experiment MT-PLAMO08-H0 --mode fixed-text
python -m p0.p2.cli --run-dir "$P2_RUN_DIR" --registry "$P2_REGISTRY" probe --experiment MT-PLAMO2-H0 --mode fixed-text
```

`freeze/preflight/score/replay/accept/report` 依主任务的顺序与参数合同执行；MT-only 使用 `fixed-text` 消费 62 条冻结文本，不与 ASR 的 88 个 `fixed-cuts` 混用。

## 6. M2：历史、术语和缓存分离

本节 HS、HP、G0、CACHE 是候选菜单，**与其他模型后续机制共享全局 B4 最多 3 个附加正式配置，不是四个必跑组**。开跑前在全局注册表分配名额；未获名额的组标 `skipped_budget`。
获准的 HS、HP、G0 分别与入选尺寸 H0 比较；所有输入按原视频顺序处理，每轮、每次回放、每个会话独立重置。
`WINNER` 是一个配置占位，不是另一个模型：开始前绑定 `winning_experiment_id/model_repo/model_revision/runtime_lock_hash/M1_report_hash`，保存解析后的配置；不能每个样本动态选较好尺寸。

| 组 | 本次请求构造 | 该组唯一新增因素 |
|---|---|---|
| `MT-PLAMO-WINNER-HS` | 当前 user；最近两条已收到 ASR 原文用换行连接后放入 `context` | 来源历史 |
| `MT-PLAMO-WINNER-HP` | 最近两条已提交 source/translation 组成 user/assistant 对，末尾当前 user；不另加 context | 译文历史及其反馈 |
| `MT-PLAMO-WINNER-G0` | H0 加预先冻结且当前句命中的 `glossary` | 术语约束 |

HS/HP/G0 均保持 `cache_mode="fresh"`；不得同时切换缓存、采样、分段或过滤器。
历史最多两条，辅助输入合计上限建议 512 token；超额按完整最旧条目删除，当前源文不截断，所有删除写日志。
HP 只能使用本组当时实际提交的译文，不能使用人工正确译文、未来字幕、Hy-MT2 历史或候选事后修订结果。
失败、过滤、取消或迟到而未提交的译文不进入 HP，但相关失败样本仍留在评分分母。
HS 可用已经收到的过去 ASR 内容；不得借批处理提前读取下一句来帮助消歧。
G0 词表在生成输出前冻结，来自独立资料或用户已有词表；不得从待评参考译文反向提取答案。
G0 同时检查命中句和未命中句，防止术语被强行插入无关字幕；匹配规则不得按错误逐句人工调整。
若历史补出一个流畅但无证据的主语，不计为改善；记录人称、主体关系及事实增补错误。
附加组须同时保持 M1 晋级门槛、相对 H0 无保护项损失/新增关键错，并至少新增 1 个净正确语义项，才有继续机制验证的理由。
至多注册一个组合组，且只组合已独立显示收益的历史方式与 G0；组合也占 B4 的一个名额。HS 与 HP 不默认叠加，不做参数排列组合。

### 6.1 C0/C1：有语义收益后再测缓存

使用 `MT-PLAMO-WINNER-CACHE`，配置记录获益上下文组及同一模型绑定，C0/C1 是该实验内的两个缓存条件。
该实验占 B4 一个附加正式名额；若 HS/HP/G0 已耗尽全部 3 个名额，本轮不再开 CACHE 或组合组，不能借“性能检查”绕过配置预算。
缓存不是历史；完整当前 prompt 必须显式包含该次获准历史，KV 只复用 token 级相同前缀。
先冻结获益组生成的逐请求 prompt 列表，再比较 `fresh` 与 `exact_prefix`，保持模型、模板、输出上限和请求顺序一致。
要求逐字输出和停止原因一致；出现差异先记录为缓存实现/数值路径待诊断，不能直接用提速覆盖质量差异。
记录实际复用 token 数、TTFT、完整响应时间、峰值内存和长跑增长；不可观测复用数记 null，不估算。
重复同一完整 prompt 的热跑只说明请求重放成本，不能冒充真实连续新字幕吞吐。
缓存身份至少包含模型 revision、tokenizer/template hash、语言方向、session_id 与 epoch；seek、视频切换、字幕关闭/重开均清业务状态。
滚动删历史或更改词表可能使公共前缀缩短；不能预设全部历史每轮都可复用。
异步结果提交前重新核对 epoch 与源片段版本；取消请求的缓存状态和译文不能跨会话提交。

## 7. M3：性能、格式后备与停止

只有通过 M1 对应资格的配置进入真实 1× 播放；维持冻结 ASR 与字幕策略，只换 MT，按主文档测稳定正确中文字幕、句末 P95、覆盖率和资源。
分开记录排队、模板/tokenization、prefill、首 token、解码结束、过滤与实际提交；完整延迟不能用 TTFT 或单 token 时间代替。
冷启动/编译与热态服务分开，权重文件大小与进程/系统峰值分开；不把两个独立实验峰值相加冒充组合资源。
GGUF 后备只在 MLX 接线明确受阻，或质量过线但 MLX 性能/内存确实失败时启用；只选一个尺寸的 Q4_K_M。
固定 llama.cpp SHA、实际架构支持、Metal/CPU 层配置、模板、BOS/EOS 与 RoPE；旧 Index 实验的引擎不能默认支持新 PLaMo。
GGUF 先重跑 M0/M1；它不是 MLX 的无损格式切换，不能继承 MLX 质量分数。[S9]
达到预算后停止；无结果时输出 `no_candidate_passed` 或具体 `blocked`，不能自动扩大模型、上下文或更换评估集。

## 8. 最小日志与完成条件

每条保存 `run_id/experiment_id`、代码/数据/模型锁、轮次标签、`session_id/epoch/segment_id`、真实源文 hash 和原文。
保存 prompt/hash、当前/辅助/总 token 数、历史 id 与实际译文、词表 hash、温度、上限、缓存模式和复用 token。
保存单调时钟的入队/开始/首 token/解码结束/最终响应/提交时刻，以及 raw/filtered 中文、过滤原因、停止原因和异常。
完成报告必须给出 34 项逐项对照、两轮总分、两条资格路径判定、保护项/关键错、未执行项及理由，再给真实链路的性能资源结论。
本章完成指实施者提交可复核产物；未通过联合门槛的候选不修改产品默认值，负结果也保留模型锁、原始输出与判定理由。

## 9. 核实来源

- [S1 · PFN 正式发布](https://www.preferred.jp/en/news/pr20261105)；[训练与量化说明](https://www.preferred.jp/en/blog/tech/plamo-3-translate-weight-release)。
- [S2 · 固定 0.8B MLX 官方模型卡](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/README.md)。
- [S3 · 固定 2B MLX 官方模型卡](https://huggingface.co/pfnet/plamo-3-translate-2b-MLX-4bit/blob/61c44c8a4008f8889a3a68ad293243c9ed434771/README.md)。
- [S4 · 固定 MLX 模板](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/chat_template.jinja)。
- [S5 · 固定模型配置](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/config.json)。
- [S6 · 固定生成配置](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/generation_config.json)。
- [S7 · 固定服务入口](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/serve.py)。
- [S8 · 固定 server.py](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/server.py)；[固定缓存补丁](https://huggingface.co/pfnet/plamo-3-translate-0.8b-MLX-4bit/blob/88be1a175fb222489b0f1cf8a1bed26573249a81/serving_memory.py)。
- [S9 · 固定 2B GGUF 官方模型卡](https://huggingface.co/pfnet/plamo-3-translate-2b-GGUF/blob/d44589ecd5287cd64452e9cd1e2cfdad904fe216/README.md)。

许可为 [PLaMo Community License](https://plamo.preferredai.jp/info/plamo-community-license-en)，非 Apache；个人非商业研究无需先注册，商业分发按官方完整条款另行处理。
