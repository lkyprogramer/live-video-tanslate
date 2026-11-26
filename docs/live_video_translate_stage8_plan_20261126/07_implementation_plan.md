# Stage 8 Quality/Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用可复现的同层证据，改进本机日语→中文实时字幕，而不破坏Stage7的ANE低延迟与生命周期保障。

**Architecture:** 先新增隔离的评测合同与缓存一致性测试，再做可独立拒绝的MT/ASR消融。仅对有证据的候选建立有限复核与稳定提交，不重写整个现有管线。

**Tech Stack:** Python3.12、现有MLX0.32.4/MLX-LM0.31.3/MLX-Audio0.6.7、Objective-C++/CoreML/Accelerate、TypeScript/Chrome诊断扩展、pytest；新依赖在隔离评测环境中锁版本。

**Spec:** [02_design_spec.md](02_design_spec.md)，指标 [03_measurement_contract.md](03_measurement_contract.md)，门禁 [08_acceptance_and_rollback.md](08_acceptance_and_rollback.md)。执行者须读这三份。

补充强制合同：[11_machine_contracts.md](11_machine_contracts.md)及`schemas/`。其中定义三基线、缺失+∞、调度身份/截止/提交、聚合路径；它们优先于本计划中的简写B0/epoch。

## Global Constraints

- `BASE_SHA=7ea13865c8c3673a1463495058a200723b153111`；已有 Stage 7 档位默认不改。
- 正式指标用真实 Chrome 1×、DOM事件与媒体采样时钟；WS/离线结果单独标注。
- 采样率固定 `16000Hz`，音频样本区间使用左闭右开 `[start,end)`，禁止提前提供尚未捕获的真实音频。
- 正式最终字幕提交后不可被后台复核静默改写；未提交预览可修订但必须有revision与来源。
- 每个试验只加载一个 MT 主模型；每次最多增加一个 ASR 挑战者。不同 MT 候选不同时常驻。
- `trust_remote_code` 不自动开启；下载、外部代码执行、特权遥测、提交、推送、切默认均不在本次文档授权内。
- 保持 ANE 空闲不计算、GPU 回退串行、关闭先join后释放这三项已修复行为。
- 硬阈值统一读 `templates/thresholds.proposed.json`；实测前锁定副本与哈希，事后修改须开启新experiment_id。

## Review Focus

- 连续短应答或多人重叠：不能因字符少或同音重复被当垃圾删除；T1/T4语义与归属测试覆盖。
- EOS/max_tokens/清洗不同分支：不能遗留多余token或污染下一轮；T2真实缓存对照覆盖。
- seek或关闭恰在异步完成时：旧epoch结果不可提交，GPU/KV不可use-after-free；T6取消注入覆盖。
- 回退ASR与新GPU模型同跑：不能绕开共享GPU执行权或导致final饥饿；T6压力测试覆盖。
- 元数据相同但内容变更：不能复用旧654行结果；T0内容hash测试覆盖。

## 实施约定

本文件是未来实施计划；以下标为Create的文件和Stage8 CLI现在不存在。当前会话仅交付此文档。后续实施先在隔离worktree，保留用户改动；每任务独立review，只有另获授权才创建commit，绝不自动push。本文中的“检查点”默认指本地diff与测试产物，不等于git提交。

代码目录仅新增 `p0/stage8_eval/`（实验runner与统计）和少量职责单一模块，避免继续把所有新功能堆入约两千行的`local_pipeline.py`，也不做无关大重构。

执行优先级优化（务实批处理）：由于 `T2`（Qwen KV double-EOS）已在 `7ea1386` 本机实测复现，Batch 1 合并落地 **T2 修复 + 轻量 T0/T1**（manifest hash 键、`asr_raw`/`asr_selected`/`dom_visible` 分层、LCP `erased_chars`、`cer_v2`、Chrome wrapper 参数化）；`schemas/contracts.schema.json` 作为产物的格式参照标准，不强求先写完 20 套完整 JSON Schema 校验器才修已证实的 P0 缓存 bug。逻辑依赖：`[T0+T1 轻量口径 & T2 P0 修复] → T3/T4（本地零下载消融，Mac计时串行）→ T5（授权后候选）→ T6（条件合流）→ T7`。T8底层优化为独立条件分支，完成后也必须回T7。

## T0：manifest与不可误命中的结果身份

**Files**

- Create：`p0/stage8_eval/__init__.py`、`contracts.py`、`preflight.py`、`tests/test_manifest.py`。
- Create：`engine/src/subtitle_service/quality_types.py`（生产共享类型）；生产模块不得反向依赖p0实验包。
- Reuse/read：`engine/config/profiles.example.toml`、实际未跟踪`profiles.toml`、`engine/uv.lock`。

**Interfaces**

- `load_manifest(path: Path) -> dict`；`validate_manifest(m: dict, *, require_local_assets: bool) -> list[str]`。
- `experiment_key(m: dict) -> str`：规范JSON的SHA256，包含input/model/config/runtime/code/threshold哈希，不含运行开始时间。
- `preflight(m: dict) -> dict`：不下载，只检查本地文件与身份/资源；结果PASS/BLOCKED，原因逐项列出。
- `quality_types.py`定义Spec的RequestKey、AudioSpan、TokenSpan、ASRHypothesis、Turn、TranslationRequest、TranslationResult、Stage8Event；`p0/stage8_eval/contracts.py`仅做manifest/schema/序列化校验并导入这些类型；保留schema_version。

- [ ] **写失败测试**：`test_same_row_count_different_pcm_invalidates_key`断言654行数相同但PCM或prompt hash变更key必不同；`test_placeholder_manifest_is_blocked`对示例所有REPLACE字段返回BLOCKED；`test_future_audio_is_rejected`令input_end>captured_end则报错。
- [ ] **运行红测**：`PYTHONPATH=.:engine/src engine/.venv/bin/pytest p0/stage8_eval/tests/test_manifest.py -q`；预期新模块缺失/断言失败，不能伪报PASS。
- [ ] **实现最小接口**：使用已有hash和路径库，未知model revision不得填main；资源采集无权限写UNKNOWN；不复制大模型。
- [ ] **运行绿测与只读preflight**：同命令PASS；新增CLI `python -m p0.stage8_eval.preflight --manifest <path>` 对无效示例退出2、合法就绪输入退出0，检查报告不改用户profile。
- [ ] **独立检查点**：归档`manifest.json`、`preflight.json`；无私有媒体路径泄漏到公共报告。

## T1：双层ASR、真实DOM、草稿来源与可靠统计

**Files**

- Create：`p0/stage8_eval/events.py`、`score.py`、`tests/test_events.py`、`tests/test_scores.py`、`run_chrome.mjs`。
- Modify：`engine/src/subtitle_service/local_pipeline.py`、`app.py`、`backend.py`的诊断字段；`extension/src/protocol.ts`、`offscreen.ts`、`content.ts`、`extension/test/protocol.test.ts`。
- Reuse：`p0/perf_probe/verify_stage6_ep01_chrome.mjs`中的播放/CDP/时钟逻辑，`analyze_stage6_stage2_timing.py`的现有对齐；不修改旧数据。

**Interfaces**

- `validate_event(e: dict) -> list[str]`；`erased_chars(previous: str, current: str) -> int`。
- `score_asr(reference: str, hypothesis: str, normalizer: str) -> dict`返回S/D/I/N/CER；`summarize_run(events: list[dict], units: list[dict]) -> dict`保留失败分母、来源与时间域。
- 新runner读取manifest；写run独占目录，禁止覆盖旧`docs/experiments/stage6...`；Chrome临时profile使用唯一临时目录，不使用用户profile。

- [ ] **写失败测试**：`test_append_is_not_retraction`断言“今天→今天天气很好”=0；`test_replacement_is_counted`断言“今天→昨天”=2；`test_preview_not_final_stream`独立统计44/103类别；`test_missing_output_stays_in_denominator`保留缺失项。
- [ ] **补边界红测**：`test_cer_keeps_long_vowel_mark`保留ー；`test_asr_raw_not_filtered_by_mt`即使网关拒绝仍有ASR分数；`test_short_reply_has_own_unit`不忽略≤4字；`test_clock_domains_cannot_subtract_without_anchor`无anchor拒绝跨域延迟。
- [ ] **实现诊断协议兼容**：沿现有record接口增加provenance，不改旧协议必选字段；必要的optional字段由新旧客户端测试覆盖。DOM记录在rAF可见事件，预览/终稿token流各有kind。
- [ ] **验证**：`PYTHONPATH=.:engine/src engine/.venv/bin/pytest p0/stage8_eval/tests/test_events.py p0/stage8_eval/tests/test_scores.py engine/tests/test_gateway.py -q`全部PASS；扩展使用已锁定package scripts完成test/build，日志归档。
- [ ] **计量重放**：人工构造追加/撤回/空输出/seek的微小事件fixture；统计值与手算一致；用新runner跑授权的短Chrome样例并核对WS→DOM链路。

## T2：Qwen真实cache协议与停止一致性

**Files**

- Create：`engine/src/subtitle_service/mt_cache_contract.py`、`engine/tests/test_mt_cache_contract.py`、`p0/stage8_eval/check_mt_cache.py`。
- Modify：`local_pipeline.py`的Qwen translator/session最小范围；原生生成逻辑先加日志，确认问题后再改。

**Interfaces**

- `canonical_prompt_ids(tokenizer, messages: list[dict]) -> list[int]`。
- `common_prefix_len(a: list[int], b: list[int]) -> int`。
- `CacheAudit`含expected_ids_hash、actual_ids_hash、expected_offset、actual_offset、stop_reason、first_divergence、parity_pass。
- `audit_cache_case(translator, case: dict) -> CacheAudit`使用本地锁定模型与tokenizer；不自动load远程模型。

- [ ] **写失败测试**：构造会在yield前consume EOS的fake generator，`test_eos_cached_exactly_once`验证连续两轮没有double-end；`test_cleaned_history_cannot_reuse_raw_prefix`验证引号清洗导致前缀变化时正确失效。
- [ ] **补终止红测**：`test_max_tokens_not_normal_eos`、`test_cancel_does_not_commit_history`、`test_preview_rollback_preserves_final_cache`、`test_trim_and_replay_equals_fresh`、`test_new_epoch_forgets_old_history`。
- [ ] **实现最小修正**：从真实token ledger决定复用长度；任何不匹配先安全重建，不能凭文本切片猜offset；正常stop与length_limit明确分开。无需引入通用分布式cache。
- [ ] **单元验证**：`PYTHONPATH=.:engine/src engine/.venv/bin/pytest engine/tests/test_mt_cache_contract.py engine/tests/test_m1_pro_hq.py -q`；所有既有生命周期测试仍PASS。
- [ ] **真实小模型验证**：新增 `python -m p0.stage8_eval.check_mt_cache --manifest <path> --out <dir>`，对EOS/裁剪/取消/清洗至少12个case输出audit。以canonical IDs一致为硬前提；同一精度next-logit差异记录max/RMS与top1，采用FP16路径相对容差1e-3、绝对1e-3作为诊断起点，超出须定位而非仅放宽；最终greedy差异需逐例解释。
- [ ] **决定**：真实协议失败则BLOCK所有模型比较；若只是浮点路径差异且文本偶发不同，保持fresh基准并按质量变化处理，不能声明位级等价。

## T3：重建B0与MT廉价消融

**Files**

- Create：`p0/stage8_eval/bench_mt.py`、`model_adapters.py`、`tests/test_mt_runner.py`。
- Modify：`local_pipeline.py`仅增加实验开关；`config.py`仅在需要时添加有默认值的非生产开关。
- Reuse：`p0/tier2_mt/bench_mt.py`的数据和历史错误类型，不复用仅按行数命中的缓存。

**Interfaces**

- `MTAdapter.translate(request: TranslationRequest) -> TranslationResult`；`load_mt_adapter(model_key: str, manifest: dict) -> MTAdapter`仅resolve本地许可模型。
- `run_mt_arm(manifest: dict, arm_id: str) -> dict`输出完整input/output/stop/timing记录、标准chrF signature和legacy分数；manifest唯一key。

- [ ] **写失败测试**：同词数但不同prompt不能命中；fresh/cache同case必须关联同semantic_unit；长度截断和gateway reject不是成功；history只有过去已接受的同epoch条目。
- [ ] **实现runner**：CLI `--manifest --arm --out`；一个manifest只含一个arm，CLI仅核对不覆盖锁定字段，冲突退出2；先实现当前4B adapter即可，模型未知退出2；所有计时包含设备完成同步。
- [ ] **验证**：`PYTHONPATH=.:engine/src engine/.venv/bin/pytest p0/stage8_eval/tests/test_mt_runner.py -q` PASS；smoke输出条数等于输入条数且无被静默过滤。
- [ ] **执行**：依[04](04_mt_experiments.md)顺序固定654cue兼容集＋成句集跑2×2，再逐项约束器消融；新B0与旧B0分开，不把修cache后的收益归给Q8。
- [ ] **检查点**：`mt_ablation.json`、tokens/TTFT/replay分布、盲审表、是否继续下载候选的decision record。

## T4：NeMo边界、窗口与归属验证

**Files**

- Create：`engine/src/subtitle_service/audio_span_contract.py`、`engine/tests/test_audio_span_contract.py`、`p0/stage8_eval/bench_asr.py`、`tests/test_asr_runner.py`。
- Modify：`local_pipeline.py`的input/target span诊断；`engine/native/nemo_ane.mm`只增加hybrid/beam分解计时，第一轮不改数学行为。

**Interfaces**

- `validate_span(span: AudioSpan) -> None`失败抛ValueError；`owned_tokens(tokens: list[TokenSpan], span: AudioSpan, committed_until: int) -> list[TokenSpan]`只返回有合法归属的token。
- `ASRAdapter.transcribe(pcm, span: AudioSpan, options: dict) -> ASRHypothesis`。
- `run_asr_arm(manifest: dict, arm_id: str) -> dict`与MT runner同样输出逐条证据和全部失败。

- [ ] **写失败测试**：`test_context_not_committed_twice`、`test_future_pcm_rejected`、`test_unknown_timestamps_cannot_trim_by_guess`、`test_right_collar_is_charged_to_delay`、`test_short_repeated_reply_not_deduped_across_speakers`。
- [ ] **实现same-span离线runner**：锁定target，input短/4/6s，collar0/160/320ms单列；无时间戳挑战者只使用整target替换；禁止全窗全文直接拼接。
- [ ] **验证**：`PYTHONPATH=.:engine/src engine/.venv/bin/pytest engine/tests/test_audio_span_contract.py p0/stage8_eval/tests/test_asr_runner.py -q`；固定PCM单元通过；native扩展编译在目标Mac另行授权执行。
- [ ] **执行归因**：48–80单元的边界×context；命中问题再做beam/pad单因素；所有长窗bucket与重算比例入表。
- [ ] **检查点**：ASR S/D/I与错误分类、目标区间逐例复核、是否有必要调用现成A3/Turbo的决定。

## T5：候选加载与有限挑战者评测

**Files**

- Extend：`p0/stage8_eval/model_adapters.py`、`bench_mt.py`、`bench_asr.py`、`tests/test_mt_runner.py`、`tests/test_asr_runner.py`。
- Create：`p0/stage8_eval/model_registry.json`（只记录获准且已锁定的模型/本地artifact）。

**Interfaces**

- `resolve_model(model_key: str, registry: dict) -> dict`必须验证revision/filehash/license/本地存在；无许可或下载未完成返回BLOCKED，不能隐式联网。
- Index GGUF adapter（仅在启用 M4 sidecar 备选时）通过localhost隔离服务实现同一MTAdapter合同，不能把请求转发官方在线API。

- [ ] **写失败测试**：未批准模型不启动、不联网；custom code默认拒绝；HF main不是有效pin；选文本Index不得请求mmproj；量化/模板身份不一致禁止继续。
- [ ] **经授权后准备候选**：MT 优先零新 runtime 的 MLX 候选（`Qwen3-4B-8bit` -> 条件 12–24 难例探 `Qwen3-30B-A3B-4bit`），`Index-Translate-2B-GGUF` 仅在 MLX 未达标时作可选 `llama.cpp` sidecar 备选；ASR 获下载授权后按 `Qwen3-ASR-1.7B-8bit`（复用仓内适配，本地当前无权重）与 `whisper-large-v3-turbo` 顺序首筛。只装一个候选所需依赖，不运行下载脚本中的任意post-install代码。
- [ ] **最小协议smoke**：12–24条，校验官方模板、EOS、无思考、数值/语态、每条输出完整、实际CPU/Metal后端；BLOCKED与质量FAIL区分。
- [ ] **验证runner**：相关pytest PASS后再跑D-dev；所有结果明确model+runtime；通过才进入G4集成回归，不在此解封holdout。
- [ ] **独立决定**：每个候选一个decision_record；无效果停止，不为凑完整矩阵继续30B/14B下载。

## T6：可选合流调度与稳定前缀预览

只有G3显示互补收益才做。单MT替换无需实现双ASR复杂架构。

**Files**

- Create：`engine/src/subtitle_service/quality_scheduler.py`、`preview_policy.py`、`engine/tests/test_quality_scheduler.py`、`test_preview_policy.py`。
- Modify：`local_pipeline.py`、`config.py`、`profiles.example.toml`，新增disabled实验档，不改旧默认。

**Interfaces**

- `QualityScheduler.submit(key: RequestKey, kind: str, deadline_ns: int, job) -> None`；`cancel_epoch(session_id: str, epoch: int) -> None`；`select_before_deadline(key: RequestKey, fallback: ASRHypothesis) -> ASRHypothesis`。`RequestKey`由quality_types定义，字段session_id/epoch/caption_id/revision/request_id，详见11。
- `PreviewPolicy.decide(source: ASRHypothesis, previous: str, now_ns: int) -> dict`返回emit/hold、原因、允许revision；0.8/1.2/1.6s为独立实验参数，未通过不改1.6s旧策略。

- [ ] **写失败测试**：pending preview最多1，ASR复核最多1；final优先；超时使用fallback且只commit一次；late result/旧epoch永不送显；final不可回写；关闭先等待执行安全退出。
- [ ] **补争用测试**：`test_ane_fallback_shares_gpu_lane`；mock延迟GPU任务、快速seek、持续短句burst，检查无silent final-drop与无跨session KV污染。
- [ ] **实现最小策略**：沿用现有GPU执行权，不开并行无锁Metal线程；按11的每任务预算与时钟锚点推导deadline，不从发布P95倒推；不偷看后文。final接受、DOM接收、history和处理/提交水位分开记账。
- [ ] **验证**：`PYTHONPATH=.:engine/src engine/.venv/bin/pytest engine/tests/test_quality_scheduler.py engine/tests/test_preview_policy.py engine/tests/test_m1_pro_hq.py -q`全部PASS。
- [ ] **shadow→限量live**：先测触发率/收益/额外GPU时间与排队，再启用deadline内选稿；阈值只在dev校准，holdout不调。

## T7：发布证据与独立复核

**Files**

- Create：`p0/stage8_eval/aggregate.py`、`acceptance.py`、`tests/test_aggregate.py`、`tests/test_acceptance.py`、`docs/experiments/stage8-2026-11-26/`的脱敏报告。
- Update（获准后）：README与example档位说明；不改个人profile。

**Interfaces**

- `aggregate_runs(run_dirs: list[Path], units_path: Path, judgments_path: Path, thresholds_path: Path) -> dict`：校验各文件hash、三基线身份与schema，按11构造聚合报告。
- `evaluate_gates(report: dict, thresholds: dict) -> dict`返回每条门禁及PASS/FAIL/INCONCLUSIVE/BLOCKED，不能空值默认PASS。缺失延迟按+∞，缺字段拒绝，不能裁掉慢/错样本。

- [ ] **写失败测试**：缺human review、缺holdout、缺DOM、swap超限、关键保护回归、新E4、所有超时被过滤任一均不能PASS；quality-only通过不切默认。另测`test_missing_latency_is_infinite`、`test_no_baseline_cherry_pick`、`test_holdout_used_once`、`test_cli_cannot_override_frozen_manifest`、`test_candidate_resource_abort_is_fail_not_blocked`。
- [ ] **跑完整验证**：依[08](08_acceptance_and_rollback.md)执行配对Chrome、holdout、45m+交互、掉帧空白基线及强制回退。没有目标机器就停在BLOCKED，不用开发机结果替代。
- [ ] **独立review**：Reviewer重算关键指标并抽查原始事件与错例，确认无未来音频/标签泄漏、没有把mock当真机。
- [ ] **打包handoff**：manifest、逐臂summary、盲审和仲裁、raw日志清单及hash、失败决定、唯一推荐档及回滚步骤。
- [ ] **人类发布决定**：明确是否仅实验档、是否切默认；批准前保持B0，提交/推送仍需原任务权限。

## T8：条件底层补丁（不是必做）

**Files**：按热点仅修改`engine/native/nemo_ane.mm`或实际profile指向的MLX适配层；新增`p0/stage8_eval/bench_native.py`、对应golden测试，不同时重写两端。

**接口/门槛**：`run_native_case(pcm, config) -> dict`包含stage timings、token/frame/score hash；热点关键路径占比≥20%或p95≥50ms，才立项。

- [ ] **先固定红/性能基准**：证明当前热点和目标case；性能目标为同困难case p95减少≥15%且E2E可检测，不以微基准替代主链。
- [ ] **单项补丁**：scratch/arena、prefix索引或batch joint三选一；保持score/merge/tie-break语义；数值变化另进质量审查。
- [ ] **验证取消/隔离/等价**：golden token/frame、memory growth、cancel最坏等待、不跨session复用state。
- [ ] **回T7**：最终上屏若未改善，保留研究报告、不合入生产；不要为底层“更快”牺牲可维护性。

## 自审覆盖映射

Spec1/2权限与目标→T0/T7；Spec3接口与时钟→T0/T1；Spec4归属/提交→T4/T6；Spec5资源/调度→T5/T6/T7；Spec6分层→全部门禁；Spec7延后研究→仅decision record。五项Review Focus均有命名测试。所有新CLI在[10](10_commands_and_handoff.md)标注为待实现，未把它们写成当前仓库已有能力。
