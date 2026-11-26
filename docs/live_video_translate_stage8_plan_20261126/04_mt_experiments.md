# 04 · MT：先证明当前链路正确，再用资源换质量

## 1. 固定数据与基础设置

基线分为B-deployed（部署的Stage7完整bundle）、B-corrected（修正已证实协议/测量问题、模型不变）和C-candidate。以下单因素B0指B-corrected；最终发布比较必须对B-deployed并同时报相对B-corrected的增量。greedy、模板、stop、最大输出长度、history、是否cache、精度和模型版本均入manifest，具体身份规则见 [11](11_machine_contracts.md)。

新候选阶段先使用12–24条当前成句难例＋保护例进行接口smoke，再跑D-dev/冻结noisy-ASR；完成集成、冻结唯一完整候选后才在G5解封holdout。禁止为了证明小模型不足只选它曾失败的18句，又拿此成绩称“全集准确率”。

## 2. M0：真实token/KV一致性与修复（已本机复现，不下载）

**2026-11-26 本机复现事实**：已在 `7ea1386` 本地 `Qwen3-4B-Instruct-2507-4bit` + `mlx-lm 0.31.3` 上复现 `generate_step` look-ahead 导致每轮向 `_prompt_cache` 多写入一个 `EOS (151645)`（Turns 1–4 `offset` 依次偏离 `+1/+2/+3/+4`），并在第 5 轮滑窗重放后出现缓存译文退化而 fresh canonical 正确的现象。因此 M0 从纯审计升级为 **审计 + 最小修复 + cached/fresh parity 门禁**。

实现 `check_cache_equivalence(messages, variant) -> CacheAudit`：记录实际喂给 model 的 token IDs 与 cache offset；对同一完整 conversation 构造 fresh canonical prompt，比较接续点 token 序列、next-token logits/greedy 结果。

必测：第一轮、第二轮 EOS、四轮后裁剪、420 extra token 边界、12s 断档、取消（prefill/生成/完成边界）、seek/new session、preview 后 final、空输出、kana 过滤、引号清洗、max_tokens 耗尽。HF model revision 与实际 tokenizer 决定 EOS 集合，不写死所有模型都用 151645。

原始缓存输出与清洗后历史不同有两种安全选择，实验后取最小实现：保留 raw token ledger 作为内部历史；或每次用 canonical 已接受 history 重建/仅复用严格相同 token 前缀。**不能通过向模型补无意义结束符来凑 offset 一致。**缓存若不通过，选型暂停；修复并重建 B-corrected 基线后，再评 M1–M4 候选。

输出：`cache_audit.json`、每个case的token hash/offset/stop reason、差异样例、fresh/cached最终文本、实际runtime。日志不必公开全部字幕或token内容。

## 3. M1：当前4B的廉价质量/时延归因

先做旧/新prompt × 旧/新context策略的2×2，以固定654 cue兼容集和新的语义单元集分别评估。context策略须包含真实retention/replay规则，不能只把`context_turns=4`视为唯一变化。

随后在胜出的接口上逐项比较，不做无穷笛卡尔积：

| 臂 | 变化 | 目的 |
| --- | --- | --- |
| C0 | 当前全部设置 | 可复现控制组 |
| C1 | 最短忠实翻译prompt；其他不变 | 是否长规则增加prefill又引入干扰 |
| C2 | history0/2/4，分别记录源文历史和双语历史 | 上下文帮助消歧，还是错误译文自我强化 |
| C3 | 当前repetition penalty1.08与默认1.0比较 | 最近32token含输入，是否误伤姓名/数字/正常重复 |
| C4 | kana hard mask对照，仅影子/离线 | 是否存在词表/语义副作用；不自动取消生产防漏规则 |
| C5 | 精确cache复用 vs fresh | 正确性与真实prefill节约，非只看字符串模板 |

默认最大输出先与B0相同，记录`stop_reason`。如试更短上限，作为单独臂：字幕长度上限不是许可截断否定/句尾；一旦hit_length_limit标invalid，不计“更快成功”。先不强塞18条答案到prompt，避免把背题当泛化。

## 4. M2：同底模Q8（零新 runtime 第一优先，隔离量化）

候选：[mlx-community/Qwen3-4B-Instruct-2507-8bit](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-8bit)。先锁定revision/文件hash与tokenizer同源关系。

固定M0/M1选中的输入、模板、history、停止/约束器、runtime与序列；只改权重量化，验证零新 runtime 成本下 Q8 能否减少临界翻译翻转。若无可复现关键错误改善，回到Q4，不因内存尚有余量就保留Q8。

模型进入G3前需后续执行授权；本包不下载。执行时优先检查用户本地已有缓存，避免重复下载。

## 5. M3：MoE质量上探（零新 runtime，不预定替换或延迟）

候选：[mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit](https://huggingface.co/mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit)。总30.5B、激活3.3B，每层128专家取8；Q4文件约17.2GB十进制≈16.0GiB，非总工作集。

条件：M0通过、B-corrected一致、M1/M2仍有关键错误、磁盘/内存足够、已获新增下载/执行授权。先释放旧MT模型；**不与4B一起常驻做“4B草稿+30B终稿”**。首筛只做12–24条、记录真实输入/输出长度。质量不明显更好就停止；不为“用满内存”继续。

必须测四种路径：新session短请求、warm不同请求、同前缀真实cache命中、history裁剪重播；随后Chrome播放+ANE共存（执行256MiB swap硬门限），再讨论每条翻译常驻运行。不得用重复同一句缓存或长批处理吞吐代替在线字幕延迟。

相近M1 Pro 14GPU社区成绩为35.5–43.2 tok/s，1k前缀TTFT约3.27–3.49s；不同硬件/采样/缓存设置不能直接迁移。即使20token输出，仅首token后19步已约0.44–0.54s，尚未含prefill/排队。所以300–450ms只能是某些短输出的可能值，不是设计预算。

第一轮不扫3bit：尚未核实同口径具体制品；降低精度也可能重新损失关键语义。只有Q4质量胜出、内存明确阻塞，才单开Q3转换/精度实验与许可证核对，不能先假定“13GB更稳”。

## 6. M4：可选独立 sidecar 专用翻译备选（仅在 MLX 候选未达标时启用）

候选：[IndexTeam/Index-Translate-2B-GGUF](https://huggingface.co/IndexTeam/Index-Translate-2B-GGUF)，首筛Q4_K_M，约1.31GB文件。仓库当前无原生 `llama.cpp` LLM server 工具链；若引入独立 sidecar 会同时改变模型与运行时并增加服务生命周期管理复杂度，因此排在原生 MLX 候选（M2/M3）之后作为第二梯队隔离验证，不因总榜认定更懂日剧。

若启用：按发布者纯文本模板`请将以下文本翻译为中文，直接输出翻译结果，不要进行任何解释。\n\n{source}`，指定chat template与non-thinking、greedy；不加载mmproj，不调用官方在线API，不加图像分支。须使用支持Qwen3.5架构的**锁定版本**原生Metal `llama.cpp` server，不能自动pip升级生产MLX。

第一阶段仅离线测量，不接生产；新runtime的内存/启动/prefill/IPC传输计入对应实验。先查官方metadata/license并锁文件名；只有模板/stop回归通过后评质量。若GGUF明显胜出再单独考虑社区MLX转换并完整重校验tokenizer/logits一致性。

## 7. 已测路线的重开条件

| 路线 | 历史结果 | 允许重开条件 |
| --- | --- | --- |
| Qwen3-8B Q4 | 同机M0约1.49/3.42s，clean30/34低于4B32/34；M_CTX约4.01/6.86s，已关thinking | 当前完整语义单元、严格短输出/缓存新假设下12–24例，不重跑整轮泛选型 |
| PLaMo3 0.8/2B | 官方模板/stop H0已测；各17/34，对照20/18；丢4条保护信息、新增2条关键错误 | 当前成句/语境/词表构成明确新假设；先小规模paired，旧失败保留 |
| PLaMo3 8B | 自定义模型加载被拦，不是准确性FAIL | 新的已审计native支持/官方GGUF路径；仍需tokenizer/stop验证，不默认trust_remote_code |
| TranslateGemma4B | 旧轮end_of_turn循环、P50>5s | 先证明停止/模板正确，小型协议probe；不把循环耗时当模型硬速度 |
| Seed-X7B | 旧轮s标签循环、79/183通过网关 | 同上，且先解决低可用率；非第一批 |
| Sakura14B/Qwen2.5-14B | 非同一模型，不能互换名字 | 质量优先备用；Sakura轻小说域与CC BY-NC-SA限制、当前片源迁移需单验 |

当前门禁最重要的不是列表长，而是失败后不重复。每重开一条必须在decision record写明“新证据/新假设/小规模上限/停止标准”。

## 8. 晋级依据

所有模型用统一MQM-lite语义错误、标准与legacy分数、gateway完整率、first useful DOM和final DOM、queue、内存压力。依据 [08](08_acceptance_and_rollback.md) 晋级。

单纯chrF上升而否定/角色错误不降，不替换；质量明显更好但延迟不达默认门禁，可形成明确标注的opt-in质量档，不伪装成全面更优。若资源/授权不足，只产出阻塞原因与已完成的小模型消融，不替用户决定购买硬件或调用云端。
