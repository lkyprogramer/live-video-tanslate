# 01 · 当前提交审计与 Gemini 方案裁决

## 1. 版本锚点

| 版本 | 作用 |
| --- | --- |
| `7ea13865c8c3673a1463495058a200723b153111` | 本报告当前审查基线，Stage 7 报告、JSON 与验证脚本 |
| `3f901ca3a372edf403071e98f2e718bfc9dc5b3f` | Stage 7 hybrid、draft guard、MT 四轮上下文与 prompt |
| `cdbf0a732cf1dead66df98d1696dfeb3390f9391` | Stage 6 归档完成、Stage 7 改动前的候选对照 |
| `ac7378f30b6da6d534a7df0314bbcd9092632514` | 空闲保活、回退串行与 KV 生命周期修复 |
| `edefcbcf35f1ef80f90c1ef27710ea1e861b2d5f` | 清理旧文档前的历史证据锚点，用于找回已失败实验 |

GitHub 的 `origin/main` 与本地 `HEAD` 已核实一致（`7ea13865c8c3673a1463495058a200723b153111`，ahead/behind `0/0`），但仓库本身不等于实际运行服务：`profiles.toml` 是本地未跟踪配置，样例默认仍为 `reazon-hy`（`m1-pro-nemo.enabled=false`）。本地 `.model-cache/` 已核实实际资产仅有：`Qwen3-4B-Instruct-2507-4bit`（`2.1G`）、`reazonspeech-nemo-619M-v2-Q8_0.gguf` + `libspeech.dylib`（`639M`）、`nemo-ane`（`fc_S256/M560/L1024_w8a16.mlmodelc` + `libnemo_ane.dylib`，`1.6G`）与 `silero_vad.onnx`（`632K`）；`Qwen3-ASR-1.7B-8bit`、`reazonspeech-k2-v2` 与 `Hy-MT2-1.8B` 的权重已在先前清理中移除，本机暂无缓存（其中 `Qwen3-ASR-1.7B-8bit` 仅仓库代码保留 `Qwen3AsrRecognizer` 适配）。G0 必须记录正在运行的模型、配置、二进制和版本。

Stage 7 样例：NeMo `hybrid/ane/6 threads`，draft interval `1.2s`，pause `180ms`，max segment `2.8s`，overlap `350ms`，pad `500ms`；MT Qwen3-4B-Instruct-2507-4bit revision `50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b`、context `4`。README 中 greedy/4.5s/两轮概述滞后，不作为当前基线。

依赖以 `engine/uv.lock` 为准：当前 pyproject 指定 MLX `0.32.4`、MLX-LM `0.31.3`、MLX-Audio `0.6.7`、Transformers `5.22.1`。不能无记录升级后把差异归因于模型。

## 2. 已完成，不应重复列为“待修”

- ANE keepalive 默认关闭；空闲/实际请求计时分开；销毁先结束线程。
- native 初始化/运行失败有回退路径，GPU 回退与 MT 共用串行保护。
- close 先等待工作结束，再释放 KV；有强制回退与关闭注入测试。
- Stage 6 真 Chrome 完整 45.3 分钟，加约 10 分钟交互；观测到全部 668 次 ASR 走 ANE、零回退、零断线/过载；三个 15 分钟段 final DOM P95 约 `.919/.908/.909s`。
- 暂停 180s 服务进程均值约 `0.39% CPU`，原生测量无额外 eval；这是真正比持续保活更有用的结果。

但旧测试不能替新配置和新模型自动背书；Stage 7 新路径仍须做配对 Chrome 与强制回退/取消复测。旧报告视频掉帧约 5.6–5.9%，没有相同视频“字幕关闭”对照，不宜写“对视频零影响”；无温度/功耗 trace，也不宜写“已证无热降频”。

## 3. Stage 7 归档事实与口径

| 记录 | 观测值 | 正确解释 |
| --- | --- | --- |
| Part A | 45m 音频约 90.8s 跑完，947 chunks、876 kept、33 纯标点跳过、947 ANE/0 fallback | 快速离线 ASR，不是 45m 热稳定播放 |
| Part A CER | 3679 / 9937 = `.3702`；hyp chars 8461 | Stage 6 4520/9937=`.4549`，但其入样层不同 |
| 输出长度 | 相比 Stage 6 hyp 6992 增加 1469 字符 | 不是“正确救回 1469 字”，须拆 S/D/I |
| Part A ASR | P50 `39.70ms`，P95 `160.28ms`；decode P95 `134.36ms` | 尾部比旧报告 ASR P95 `72.95ms` 更慢，须按相同音频长度/桶比较 |
| 漏 cue | 11/654 =1.68%；排除 6 个≤4字短 cue 后 5/654=.76% | 只检查 ±.3s 有非空 ASR 时间重叠，不是内容语义覆盖 |
| Part B | 654 条 Oracle JA→ZH；context4 | SRT cue 不一定是完整语义句；未验证每条人工逐字 |
| Part B MT | P50/P95 `466.5/701.9ms`，旧约 `301.4/444.0ms` | 同一组输入更可比，但 prompt/context/cache策略同时改变 |
| Part B legacy chrF | 30s blocks `.1974→.2019`；full concat `.3083→.3163` | 当前自定义分数，不直接称标准 chrF 提升多少个百分点 |
| Part C | 0–300s 1× WS；104 finals，44 draft_sent，272 deltas | 服务链回放，不是新一轮 DOM 全集 |
| Part C 首 delta | P50/P95 `2.294/3.583s` | 从 segment start 算，缺 delta 时有 final 代算，不等于用户第一次读懂 |
| Part C final WS | `.696/1.056s` | 从 segment end 算，与 Stage 6 final DOM 不能直接相减 |
| Part C ASR / MT | ASR `58.91/181.68ms`；MT `335.27/506.06ms` | 不代表654条 Oracle 或整个45m分布 |
| Part C CER | `403/1082=.3725` | 取 pre-MT ASR，不要求最终上屏 |
| Part C 编辑比 | heavy `32/103=31.07%`、P50 `.5` | first delta 到 final 的编辑距离，混入正常追加 |
| Guard | `draft_guard_counts={}` | 这轮未观察到 guard 触发，不能认定它贡献了改善 |
| 硬件记录 | RSS约1932MiB，CPU峰61.3% | 无GPU/ANE实测占用、无全机内存归因 |

Stage 6 ASR 文本取自最终字幕记录，Stage 7 Part A/B/C 的层次又不同。Stage 8 必须同时保留 `asr_raw`、`asr_selected`、`mt_raw`、`gateway_decision`、`dom_visible`，分别评分，不用一个汇总 CER 混盖整条链路。

## 4. Gemini 判断逐项处理

| 判断 | 裁决 | 文档中的替代方案 |
| --- | --- | --- |
| 增加模型/内存是“唯一高收益路径” | 不采纳“唯一” | 先测缓存一致性、成句输入、切分归属、精度与约束器；然后上探模型 |
| Oracle SRT `.2603–.2682` 证明 619M 硬上限 | 不成立 | 系 Stage 7 规划期在 EP01 `0–360s`（94 条 JP SRT cue、`pad=350`）做的临时只读探针（未归档 JSON），不能单独作 619M 理论极限；补录人工音频参照、边界/pad/解码对照并正式归档 |
| 约1.9s 是日语 SOV 物理下限 | 不成立 | 硬编码1.6s及字符/句尾门槛可消融；只对具体语义单元评估必要未来信息 |
| GPU13%、ANE<3%极闲置 | 未被 trace 证实 | 服务时间/切片时长不是芯片residency；记录全部草稿/回退/终稿与实际资源 |
| 再给10–16GiB很安全 | 不能直接批准 | 用全机压力、swap增量、模型常驻与Chrome共存门限，不用RSS推算 |
| MoE3.3B只读激活参数，延迟类似4B | 仅定性线索 | 所有专家要驻留，prefill访问专家并集且路由/带宽局部性不同；实测短句与缓存重播 |
| 30B-A3B代表“30B级能力” | 不作质量保证 | 盲评日→中实际错误，参数总量不等价稠密模型能力 |
| 6–8s长窗消除强切断词 | 条件成立，不保证 | 同一目标音频跨度、因果上下文、时间戳归属与commit水位线；代价计入重复计算和延迟 |
| 654句可做盲评 | 只能盲模型身份，不能当新测试集 | EP01是开发集；另取未调参剧集/节目留出 |
| 先MT、再ASR、最后合流 | 基本采纳 | 在前面增加 G0/G1 正确性与评测门禁，后面增加Chrome/资源/体验门禁 |

## 5. 源码风险与优先级

### P0：真实缓存状态缺陷（已于 2026-11-26 本机复现）

位置：`engine/src/subtitle_service/local_pipeline.py` 的 `_next_turn_prefix`、`_run_cached_prompt`、历史重放；上游锁定 MLX-LM `v0.31.3/mlx_lm/generate.py` 的 `generate_step`。

**已复现证据（2026-11-26，`7ea1386`，本机 `Qwen3-4B-Instruct-2507-4bit`）：**
- `mlx_lm.generate.generate_step`（`L153: next_y, next_logprobs = _step(y)`）在 `yield y.item(), logprobs` 前已将下一个 token（遇到 `EOS` 时即 `151645`）送入 `_prompt_cache`；调用方遇到 `EOS` `break` 后，`_next_turn_prefix` 又手工拼入 `<|im_end|>`，造成连续 double-EOS。
- 实测 `context_turns=4` 连续 4 轮对话，`cache[0].offset - (canon_prompt_len + out_ids_len)` 精确偏移为 **`+1, +2, +3, +4`**（完全对应多写入的 EOS 数量）。
- 第 5 轮触发滑窗裁剪保留后 2 轮时（丢弃前两轮含 `保育士さん`/`おかえりなさい` 场景），`迎えに来た親におかえりなさいって。` 的缓存输出退化为 `'回去跟来接你的父母说声“谢谢”。'`，而 fresh 4 轮 canonical 为 `'对来接孩子的家长说“欢迎回家”。'`。

另外，`_clean_output` 可能移除包裹引号/think 尾部，重放的 history 不一定等于 KV 中原始 token；`max_tokens` 用尽时也不能假装正常 EOS 已完成。**G1 须先修复上述已复现协议缺陷**，记录实际喂入模型的 token ledger、offset、stop reason，并与 `apply_chat_template` 的 canonical token 序列及 next-token logits/greedy 校验一致后，重新跑 B-corrected 基线才评候选模型。

### P1：beam 的“更长即更好”及 draft 拼接

native hybrid 在音频≥1.5s且字速<3时尝试 beam，并偏向更长结果。draft guard 还可能用长度塌缩/尾部规则合并。长度只可作异常信号，不是正确性证据；必须验证插入、BGM 幻觉、重复与目标音频归属。现有 beam 已有 predictor cache，不要重复提出“增加 predictor cache”。

### P1：缓存身份、回放结果与线上实验污染

Stage 6 Oracle 结果复用有仅按 654 行数判命中的路径；Stage 8 key 必须包括语料、模型、prompt、解码、上下文、runtime和代码哈希。不同试验中同名文件不能覆盖，旧模型的“warm结果”不能算新模型结果。

### P2：草稿门槛与短句

当前 preview 最早1.6s，另有≥8日文字、增长≥5字符、前缀/句尾条件。可尝试0.8/1.2/1.6s的受控比较，但“ありがとう”“そうです”等短句的提早显示与复杂长句的稳定性要分别评；严禁仅以首两个中文字刷新就宣称延迟改善。

### P2：底层热点

当前原生 frontend 每次有多组 vector 分配，beam 有 prefix vector/map/shared_ptr/排序开销，值得归因后优化；已有 vDSP/BLAS，是否值得自写内核要由 critical-path trace 判断。CMVN 跨整窗会影响加上下文的特征，不得把全局缓存或截断 encoder 输出假装无损增量推理。

## 6. 本轮边界

本轮只读了固定提交、归档和公开模型/框架来源；未运行本机 Core ML/Metal，未复核个人视频逐句真值。因此本报告可以指出测量和源码风险，不能发出“Stage 8 已快多少”“4B错误已被修复”的结论。所有后续实验使用 [统一测量合同](03_measurement_contract.md)。
