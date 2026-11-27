# Stage 8 审计后优化方案与质量防线（Batch 4 规划）

- **制定日期**：2026-11-27
- **基线提交**：`a7b0396`（`main`）
- **输入真源**：`docs/live_video_translate_stage8_audit_20261127.md` 及对应源码核实结果
- **总体结论**：保留当前轻量主力模型组合（`ReazonSpeech NeMo-v2 619M ANE hybrid` + `Qwen3-4B-Instruct-2507-4bit`），撤回“已触及硬件/准确率天花板”与“严格稳定性认证已完成”的表述。按工程 ROI 分三个阶段推进：**Batch 4A（正确性收口 + 评测止血）→ Batch 4B（现有 NeMo 潜能摸底）→ Phase 3（条件触发的新流式候选 / 完整验收）**。

---

## 一、源码核实矩阵（R1–R5 & V1–V6 & 底层线索）

对照 `a7b0396` 源码逐项核实，GPT 审计指出的核心问题与代码事实均属实：

### 1.1 运行时缺陷（R1–R5）

| 编号 | 核实状态 | 源码真源位置 | 根因与实际影响 |
| --- | --- | --- | --- |
| **R1** | **属实（高优）** | `engine/src/subtitle_service/app.py`（L121–L144）<br>`engine/src/subtitle_service/local_pipeline.py`（`_run_cached_prompt`、`LocalSession.translate`）<br>`extension/src/content.ts`（L143–L152） | `CaptionDelta` 发送 `seq = self.seq + 1`，但只有合法 `Caption` 才会 `self.seq += 1`。当 A 句触发 `length_limit` 被拦截时，已发往前端的半截流式内容没有 cue 级撤销事件；下一句 B 复用同一 `seq`，会直接触发 `preview_guard` 被当作“同句长度回缩”拒绝更新。 |
| **R2** | **属实（高优）** | `engine/src/subtitle_service/local_pipeline.py`（L1151–L1191, L1431–L1444） | `_run_prompt()` 仅返回 `str`，无法区分正常 EOS 与 `max_tokens=256` 耗尽；Kana repair 分支只要收到非空且无假名文本，即强行覆盖 `valid=True, stop_reason="eos"` 且未累加 repair 的 `gen_tokens` 与独立耗时。 |
| **R3** | **属实（中高优）** | `engine/src/subtitle_service/app.py`（L194–L207） | 队列满时并发等待 `admit(frame)` 与 `self.ws.receive()`。只要下一帧正常音频或 `finish` 先于队列出队到达（1× 播放几乎必发），`pending not in done` 会立刻抛出 `BackendError("overloaded")`，导致配置的 `QUEUE_TIMEOUT` 失效。 |
| **R4** | **属实（中优）** | `engine/src/subtitle_service/local_pipeline.py`（L959–L977） | GPU/CPU fallback 启用时间戳过滤且全部 token 均落入已处理重叠区时，`_filter_tokens_by_sample()` 正确返回 `""`，但 L975 `if not text:` 无条件回退读取 `speech_result_text(res)` 全文，导致旧句重复上屏（与 ANE 后端契约不一致）。 |
| **R5** | **属实（中优）** | `engine/src/subtitle_service/local_pipeline.py`（L1928, L2001–L2020, L2182） | `_draft_cancel` 为会话级共享 `threading.Event`。A 句 final release 将其 `.set()` 后，若 A 的重叠 final ASR 为空、B 句新 draft ASR 立即执行 L1928 `_draft_cancel.clear()`，则仍在运行的 A 句迟到 draft MT 返回时会误判未取消并发出过期 `preview`。 |

### 1.2 评测与验收缺口（V1–V6）

| 编号 | 核实状态 | 源码真源位置 | 缺口事实 |
| --- | --- | --- | --- |
| **V1** | **属实** | `p0/stage8_eval/aggregate.py`（L268–L270）<br>`p0/stage8_eval/acceptance.py`（L56–L70, L132–L150） | 缺失 `swap_growth_mib` / `resource_abort` / `lifecycle` / 完整播放帧观测时，聚合器默认填 `0.0 / False / {}`，门禁将其按零故障放行为 `stability_verdict = PASS`。 |
| **V2** | **属实** | `p0/stage8_eval/aggregate.py`（L288–L295） | `aggregate_runs()` 读取了 `units` 并计算 hash 与 count，但未生成 `acceptance.py` 所需的 `availability` 与 `dom_latency`，G2 单位对齐链路尚未接通。 |
| **V3** | **属实** | `p0/stage8_eval/bench_mt.py`（L51–L79） | `chrf2_standard_0_100` 仅在 `n_hyp == 0 and n_ref == 0` 时跳过阶数，与上游 SacreBLEU `effective_order=True`（两侧均有 n-gram 才计入有效阶）在短不等长句上不一致；且当前 `engine/.venv` 未安装 `sacrebleu`。 |
| **V4** | **属实** | `p0/stage8_eval/bench_mt.py`（L213–L234）<br>`p0/stage8_eval/run_p2_index2b_screen.py`（L165–L198） | `valid=False`（如 `length_limit` 或 Kana 泄漏）的输出仍按 `text_zh` 和尝试耗时计入 chrF2 与延迟；`live_asr_track` 用交集过滤掉了空输入单元。 |
| **V5** | **属实** | `p0/stage8_eval/tests/test_acceptance.py`（L10–L15）<br>`p0/stage8_eval/aggregate.py`（L180–L216, L299–L302）<br>`p0/stage8_eval/run_t5_batch2.py`（L409） | `test_acceptance.py` 硬编码了作者本机绝对路径，clean checkout 导入即报错；`aggregate.py` 未按 `session_id` 隔离 trace 且对重复 role 静默取第一份；`run_t5_batch2.py` 在 `--reuse-existing` 时仍先走下载注册路径。 |
| **V6** | **属实** | `engine/native/nemo_ane.mm`（L1138–L1150） | hybrid 触发条件与音频时长耦合（`audio_sec >= 1.5 && g_chars < 3.0 * audio_sec` 且择长），且 P1-B（168 cues / 14 scenes）中 VAD vs Oracle 的 73 个错误差主要由插入（`+45`）与替换（`+26`）构成，净删除差仅 2；Oracle 受限于字幕时间轴、beam 触发和 pad bucket，不直接代表 619M 权重天花板。 |

### 1.3 底层线索核实

- **RNN-T Merge-First Beam**：`engine/native/nemo_ane.mm`（L710–L724）确实先执行 `std::stable_sort` + `expanded.resize(beam)` 裁剪，再在保留的 top-B 中对同前缀执行 `logaddexp` 合并，存在零下载的单变量搜索策略实验空间。
- **AudioWorklet 重采样**：`extension/src/resample.ts`（L29–L47）每输出样本现场计算 40-tap `Math.sin/cos` 核与 `this.buffer.splice(0, drop)`，但当前无掉帧实证瓶颈（120s 实测开字幕掉帧率低于关字幕），延后处理。

---

## 二、分阶段优化方案（按 ROI 排序）

### Phase 1 / Batch 4A（P0，立刻执行，零模型下载）：运行时正确性收口 + 评测止血

#### 1. R1 修复：可见 cue 生命周期与显式撤销
- **方案设计**：
  - 为每个活跃字幕生命周期分配稳定标识（如 `cue_id` 或由服务端统一管理递进 `seq`）。当某句已发出过至少一次 `CaptionDelta`，但最终因 `length_limit` / `cancelled` / `valid=False` 未产生合法终稿 `Caption` 时，禁止 silent-drop：显式下发 `caption_discard`（绑定该 `cue_id/seq` 与取消/截断原因）并推进 `seq`，同时在前端按 `cue_id/seq` 清除临时预览且绝不阻断下一句正常字幕。
  - 若尚未发送过任何可见 `CaptionDelta`，无效终稿不污染现有屏幕内容，但仍须保证下一句获得严格递增的 `seq` 与独立身份。
- **回归验证**：使用真实 `LocalSession` + 真实 FastAPI WebSocket 路由 + 前端 `preview_guard` 逻辑构建端到端回归，覆盖：
  1. `A preview → A stream → A length_limit → B short stream → B final`；
  2. `A` 后断连；
  3. seek 后迟到 `A`；
  4. 连续两个失败句后 `C` 正常上屏。

#### 2. R2 修复：Kana repair 结构化终态与真实 EOS 门禁
- **方案设计**：`_run_prompt()` 改为返回结构化结果（`text`、`stop_reason`、`eos_seen`、`gen_tokens`、`latency_ms`）。`LocalSession.translate()` 中仅当 repair 真正遇到 EOS、未取消且无假名泄漏时才允许标记 `valid=True, stop_reason="eos"`；父请求与 repair 的 token 数及耗时分开记录后再汇总总耗时，严禁仅凭“字符串非空”重写 EOS 事实。
- **回归验证**：覆盖 repair 的 6 种边界：正常 EOS、达到 `max_tokens` 截断、取消、异常、空串、再次含假名。

#### 3. R3 修复：队列背压保持真实 `QUEUE_TIMEOUT`
- **方案设计**：修改 `engine/src/subtitle_service/app.py` 背压等待逻辑：队列满时仅将 `websocket.disconnect` 作为立即终止条件；若等待 `admit(frame)` 期间先收到正常音频帧或 `finish` 控制消息，将至多 1 条后续消息暂存到 `following`（保持有界缓冲，禁止无限积压），并在剩余 `QUEUE_TIMEOUT` 内继续等待入队。
- **回归验证**：覆盖 4 种确定性场景：deadline 内排空成功、超时返回 `overloaded`、等待期间 disconnect 立即退出、等待期间收到 `finish` 不丢已接受尾帧。

#### 4. R4 修复：GPU/CPU fallback 对齐 timed 去重契约
- **方案设计**：在 `SpeechCppNeMoRecognizer.decode()` 显式区分 `used_timed_filter`（执行了时间戳去重但所有 token 均为旧内容，合法结果就是 `""`）与 `untimed`（无可用时间戳，才回退读取 `speech_result_text` 全文），与 ANE 后端保持同一契约。
- **回归验证**：覆盖 `all-old`、`partly-new`、`untimed` 三种 mock C ABI 用例。

#### 5. R5 修复：隔离每个 draft 的取消令牌与提交核对
- **方案设计**：每次生成 draft 任务时创建独立的 `threading.Event` 与单调递增的 `draft_gen`（禁止其他 generation 调用 `.clear()` 复用旧 Event）。无论是 `draft ASR` 还是 `draft MT` 完成返回，在提交 `CaptionDelta(kind="preview")` 前二次核对自身 `draft_event`、`draft_gen`、音频 span 与 `_asr_queue` / `_mt_queue` 水位。
- **回归验证**：构建确定性并发交错回归测试，复现审计报告中的“A draft MT 执行中 → A final release → A 重叠 final 为空 → B draft ASR 启动 → A 迟到完成”，断言 A 过期 preview 绝不发出。

#### 6. 评测轻量化止血（V1 / V3 / V4 / V5）
在不建设重型通用平台的前提下，直接修补导致误判的 4 处接口缺口：
1. **V1/V5（聚合与门禁）**：`aggregate.py` 缺失 `swap_growth_mib` / `resource_abort` / `lifecycle` / `total_frames == 0` / 缺失 `manifest` 一律保留 `None` 或标为 `missing`，由 `acceptance.py` 判 `BLOCKED`（不再默认 0 判 `PASS`）；发现重复 `baseline_role` 直接显式报错；`aggregate.py` 中 `model_trace` 按 `session_id` 隔离统计，消除 seek/重连跨会话串算；`test_acceptance.py` 移除本机绝对路径，改为相对 `__file__`。
2. **V3（chrF2 有效阶对齐与重算）**：在 `bench_mt.py` 中按 SacreBLEU 上游 `effective_order=True` 逻辑修正 `chrf2`（仅当 `n_hyp > 0 and n_ref > 0` 时计入 `eff_order`，并在 `engine/.venv` 允许时校验一致或锁定 `"我"` vs `"我们"` = `55.5556` 等黄金向量），保留 legacy 字段以便对照；对现有 Batch 2/3 保存的 JSON 输出做一次离线重算复核。
3. **V4（三层口径与失败分母保留）**：`bench_mt.py` 与 `run_p2_index2b_screen.py` 同时输出三层指标：
   - diagnostic raw text / attempt latency；
   - valid-only quality / success-only latency；
   - 固定分母的 delivered quality / completion availability / completion latency（失败/截断输出计空译文与 `None`/`+inf` 延迟，不得得分或计为 20ms 快速交付）。
   `live_asr_track` 保留所有样本单元（不以交集隐匿空输入）。
4. **V5（runner 复现修复）**：修复 `run_t5_batch2.py --reuse-existing` 在确认命中有效 `experiment_key` 之前先触发模型下载/注册的问题，确保跳过无谓加载。

- **Batch 4A 验收标准**：
  - 全量 Python 测试集 PASS（0 failed, 0 skipped）且在无私有路径的干净工作区可直接导入运行；
  - `extension` `typecheck` + `test` + `build` 全 PASS；
  - 单次 120s Chrome 1× 回归烟测确认主链路无回归（同时将结论严谨表述为“短程冒烟稳定”，待需要正式发布认证时再补全 V2/G4）。

---

### Phase 2 / Batch 4B（P1，最高 ROI 准确率摸底，零模型下载）：现有 NeMo ANE 潜能深挖

在修复完 R1–R5 并稳住基线后，先不急于下载或接入新大模型，而是排查当前 `0.2907` Oracle CER 究竟有多少来自工程实现与解码搜索：

1. **6.1 自研 ANE 实现与参考后端的逐级等价性校验（Parity Check）**：
   - 从 EP01 选取 30 条样本（20 条典型错误难例 + 10 条稳定正确对照），不更换模型权重，逐级比对：
     1. 输入 PCM 声道/幅度/采样数一致性；
     2. `nemo_ane.mm` 前端 log-mel、CMVN/归一化、padding 与有效帧数 vs 参考后端；
     3. Core ML ANE encoder 输出张量与参考 encoder 的数值误差；
     4. 将完全相同的 encoder 输出送入参考 decoder 与当前 C++ decoder，比对 greedy/beam token、对数得分与时间戳。
   - **决策产出**：明确拆解出多少错误是“声学前端/Core ML bucket/C++ 解码损失”，多少才是“619M 权重本身确实听不出”。

2. **6.2 RNN-T `merge-first beam` 单变量对照实验**：
   - 针对 `engine/native/nemo_ane.mm`（L710–L724），在保持同一 encoder 输出的前提下，对比：
     - 对照组：当前先排序截断 `beam`、后 `logaddexp` 合并同前缀；
     - 实验组：先合并同一声学时间步、相同 `labels` 前缀的等价路径（固定代表路径的 `frames/timestamps` 选择规则与平分 tie-break，保留 blank 概率、长度归一化及独立 `finished` 逻辑），再裁剪到 `beam`。
   - **报告指标**：整集 CER、S/D/I 分解、专名/数词表现、边界去重情况、decoder P95 耗时，并单独列出 hybrid 实际触发 beam 的片段子集效果。无清晰质量收益则回退，不强行合入。

3. **6.3 N-best 可恢复空间诊断**：
   - 从当前 beam 搜索导出 top-4 候选序列与分数，统计 20 条错例的正确假名/词条是否已在 N-best 候选中出现。
   - 若“在 N-best 内”占比高，后续可评估轻量级片源专名 trie / shallow fusion 或上下文重排（须附带近音干扰对照，防止过拟合乱改正常发音）；若“不在 N-best 内”，说明单靠后验重排无效，直接触发 Phase 3 新架构候选筛选。

---

### Phase 3（P2/P3，由前两步实测证据触发，不提前投入）

| 优先级 | 任务项 | 触发条件 | 核心动作与边界 |
| --- | --- | --- | --- |
| **P2-A** | **新流式架构候选首筛：`NVIDIA Nemotron 3.5 ASR Streaming 0.6B`**（6.5） | Batch 4B 确认当前 619M 权重听觉能力已触及真实边界，且 N-best 中可恢复率低 | 1. 严格区分官方 `NVIDIA/NeMo-Speech.cpp` 与本仓 `nyosegawa/speech.cpp`，不直接在 M1 生产链混装；<br>2. 先做最小 30–60 句 `ja-JP` 离线精度与本地 runtime 可行性摸底（不启用双 ASR 仲裁，不轻信外站跨硬件速度）；<br>3. 若需替代对比，同步补充独立日语模型 `kotoba-whisper-v2.0-mlx` 小样本对照。 |
| **P2-B** | **完整 V2（units→DOM 对齐）与 B3.3 严格发布验收** | Batch 4B 或 P2-A 产生了拟晋级主链路的新解码策略或新模型 | 1. 补齐 `run_chrome.mjs` 的 `lifecycle` / `swap_growth_mib` 采集，并接通 `aggregate.py` 的固定语义单元 DOM `availability` / `dom_latency`（丢失/超时计 `+inf` 保留分母）；<br>2. 执行 ABBA 多轮短测 + 15+ 分钟未见剧集 holdout + 45 分钟完整剧集 + 尾部静音长跑 + 盲评。 |
| **P3** | **延后候选项**（6.4 / 6.6 / 6.7） | 仅当出现对应热点或长期误差证据时立项 | 1. `extension/src/resample.ts` 相位查找表 + 环形缓冲（仅当 AudioWorklet profiling 证实成为 CPU/掉帧瓶颈时）；<br>2. Xcode / Instruments Core ML ANE 算子真实落点剖面（仅当 ASR 成为端到端关键路径瓶颈时）；<br>3. 固定 4B MT 的轻量可解释术语/prompt 消融或跨节目持续错词任务适配（训练集严格隔离验证/留出集）。 |

---

## 三、停止条件与工程约束

以下任一条件出现时，立即停止对应方案晋级，维持 `FAIL` / `BLOCKED` / `INCONCLUSIVE`：

1. 正确对照样本出现新的关键**否定、授受、被动或数字/分数反转**；
2. 出现**丢字幕、跨句 `seq` 串写、旧 epoch 迟到上屏**或错误草稿无法撤销；
3. 必须靠**过滤失败/空输出/超时单位**才能显得延迟或质量改善；
4. 长时间播放出现持续队列积压、`swap_growth_mib` 超越阈值或视频掉帧率越过 `+1.0 pp` 门禁；
5. 优化仅缩短局部内部耗时，但固定分母的正确中文可用时延与准确率无对应改善；
6. 缺测、样本量不足或实验条件混杂时，结论只能标 `BLOCKED` 或 `INCONCLUSIVE`，严禁放行 `PASS`。

---

## 四、建议沉淀的项目硬规则草案（供确认后写入仓库 `AGENTS.md`）

1. **字幕 cue 生命周期与终态不变量**：流式可先发、但每个对外可见的 cue 必须绑定唯一递增标识与 session/epoch，且有且仅有一次终态转换（合法 EOS 终稿或显式撤销）；`length_limit` / `cancelled` / `repair 无 EOS` 绝不得静默丢弃而不通知客户端推进标识，也不得伪造 `eos` 状态。
2. **异步取消与降级契约隔离**：禁止在会话级复用可被下一任务 `.clear()` 的共享取消事件；所有降级路径（如 GPU fallback、带时间戳去重、背压等待）必须严格区分“过滤后合法为空”与“无数据可用”，并保持与主路径一致的协议与超时语义。
3. **评测与门禁零假绿原则**：评分器与验收门禁中，缺失数据、零帧观测、重复角色、跨 session 混算或失败输出一律显式返回 `None`/`BLOCKED`/`FAIL`，禁止 `.get(k, 0)` 默认放行；所有标准指标优先锁定权威实现或对等黄金测试向量，并同时输出 raw、valid-only 与固定分母 delivered 三层口径。
4. **测试真实性与提交前对抗复核**：端到端边界测试必须打通真实中间回调与真实 WebSocket/TS 状态机，禁止在测试替身中跳过产生副作用的流式钩子（如 `on_delta`）；测试文件禁止硬编码本机绝对路径；非 trivial 改动汇报前须执行一轮只读独立对抗评审。
