# P3 仓库对接核查（只读）

核查基准：`lkyprogramer/live-video-tanslate@77e24c3e490a11721041a40ad4bd203f51f84039`（2026-11-09清理收尾提交）。通过已连接GitHub只读工具取得该pin的完整recursive tree（`truncated=false`），逐文件读取相关源码；保存于 `repo-snapshot/`，tree收据为 `repo-tree.json`。本次只执行已下载源码的argparse `--help`，没有安装依赖、下载模型、冻结run、运行推理、启动服务、访问封存集、修改产品或push。`cli-help-evidence.md`记录实际help输出与退出码。

## 1. 核心结论

1. P2不再是“待实现方案”。`p0/p2/cli.py`、freeze、runner、1×realtime、Chrome观测、资源采集、B1/B2 adapter、B3原生流式/AST、B4协议筛选均已经存在。旧2026-11-07交付包中“python -m p0.p2.cli拟新增/当前未实现”的表述已经过时。P3只能将它们标为既有可复用基础，不能重新把完成的P2任务分配一遍。
2. 已有P2 CLI的phase仅为B0–B4；没有 `p0/p3`、P3 CLI、Qwen3新路径、在线原生Vox→MT生产后端。P3文档若提出这些内容，必须标“拟新增/通过实现验收后可用”。不能把argparse支持`causal-stream`理解成所有候选都能在线实时跑。
3. 当前默认仍是ReazonSpeech INT8 CPU + Hy-MT2 1.8B4bit。B1–B3共8个核心候选均已有结果，全部未晋级；B4-1的Gemma中文only在G0退出，正式双轮NOT_RUN，另两个旧B4名额未使用但不是新一轮自动授权。
4. 2026-11-09已清理候选权重、隔离环境与下载源码。保留当前Reazon/Hy/Silero、engine/.venv、历史原始证据及源码锁。历史复现命令存在不代表清理后立即可跑；新的run必须先恢复准确revision、量化、依赖、下载收据。缺失已清理资产属于环境未恢复，不能记模型失败。

## 2. 已有真实入口与CLI边界

源码证据：`p0/p2/cli.py:45–196`，help实测详见同目录 `cli-help-evidence.md`。命令从原仓库根目录运行；下列均为现有接口说明，非本次实际模型运行记录。

| 已存在入口 | 参数/调用 | 实际边界 |
|---|---|---|
| `python -m p0.p2.cli` | 全局必须 `--run-dir RUN_DIR`；可选 `--registry REGISTRY` | 默认registry为仓库 `docs/live-video-translate-p2-2026-11-07/config/experiment-registry.json`；不是任意配置驱动的通用实验引擎 |
| `freeze` | `--phase {B0,B1,B2,B3,B4}`，默认B0 | 按phase代码分派；B1+采用旧收据并增加新锁；没有P3 |
| `preflight` | `--experiment ID` | B3/B4候选实际触发capability worker，**不是纯静态检查**；不得用它来做本次文档验证 |
| `probe` | `--experiment ID --mode {fixed-cuts,fixed-text,causal-stream}` | mode经phase再次校验；B0只实现fixed-cuts/text；B3 Vox的causal-stream为无1×节拍的离线因果包输入 |
| `score` | `--experiment ID` | B1+无adjudication时产生复核packet，有文件后算分；AI文本参考不等于人工听辨 |
| `replay` | `--experiment ID --repeat {1,2}` | 实现的是B0；B1–B4明确unsupported；不能拿它直接回放新候选 |
| `accept` | `--experiment ID --stage {engineering,holdout}` | help有holdout但B0明确拒绝；B1–B4无candidate accept；help枚举不是已解封验收权限 |
| `report` | 不带experiment | 按phase输出已有报告/评分packet |
| `python -m p0.p2.b3_vox_cache` | `--run-dir RUN [--capability] [--worker]` | 512MiB free-allocator缓存诊断必须显式启动；通用Vox CLI仍走默认缓存，不自动套用诊断 |
| `python -m p0.p2.b3_worker` | `--run-dir RUN --experiment {NATIVE-VOX-960,AST-G4-OFFICIAL} [--capability]` | 隔离解释器worker；不是产品服务 |
| `python -m p0.p2.resources` | `(--audio-seconds N | --requests N) --scope TEXT [--timeout-seconds N] OUTPUT -- COMMAND...` | 源码确认；选项放OUTPUT前（后方为REMAINDER）；macOS原生采集，不能把Linux上help/单测当资源实测 |
| `python -m p0.p2.export_resources RUN` | positional run | 只读已有原始采样、生成派生报告；不跑模型 |
| 产品 `subtitle-server` | `--config PATH` | `engine/pyproject.toml`注册到 `subtitle_service.cli:serve` |
| 产品 `subtitle-replay` | `subtitle_service.replay:main` | 与P2候选实验replay不是同一泛化能力 |

当前生产启动的真实命令（最新清理文档明确保留）：

```sh
uv run --project engine --frozen subtitle-server --config engine/config/profiles.toml
```

注意实际 `engine/config/profiles.toml` 是本机配置，不在tree；公开仅有 `profiles.example.toml`。新克隆不能假设私有origin/token/模型缓存就绪。

历史B3真实指令形态（只能在恢复锁定本机资产、获新授权后使用新RUN）：

```sh
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" freeze --phase B3
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" probe --experiment B0-CURRENT --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" probe --experiment B0-CURRENT --mode fixed-text
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" preflight --experiment AST-G4-OFFICIAL
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" probe --experiment AST-G4-OFFICIAL --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.b3_vox_cache --run-dir "$VOX_RUN" --capability
engine/.venv/bin/python -m p0.p2.b3_vox_cache --run-dir "$VOX_RUN"
engine/.venv/bin/python -m p0.p2.cli --run-dir "$VOX_RUN" probe --experiment NATIVE-VOX-960 --mode fixed-text
```

这些命令不能直接改ID来跑P3新模型：`b1.py/b2.py/b3.py/b4.py`有硬编码IDS、previous-run绝对相对路径、解释器路径、模型及运行时校验。

## 3. 基线与历史结果必须继承的事实

主证据：`docs/p2-b0-results-2026-11-08.md`、`docs/p2-b1-results-2026-11-08.md`、`docs/p2-b2-results-2026-11-09.md`、`docs/p2-b3-results-2026-11-09.md`、`docs/p2-b4-gemma-zh-results-2026-11-09.md`、`docs/experiments/local-runtime-cleanup-2026-11-09/summary.json`。

| 项目 | 已有实测结论 | P3不得误写 |
|---|---|---|
| B0 | 工程通过但参考为provisional；9PCM476.058s、88个切片、每seed62条主MT、34语义项；离线20/34、18/34；15项待复听 | 不得写成最终质量达标或零实测基线 |
| B0 1× WS | 两轮真实200ms输入；原句末5秒正确19/34、17/34；匹配正确P95 1.907/5.393s；WMF句末敏感性126.04s为20/18、1.815/1.905s | 两种句末口径均保留；未命中计无穷后的全单元P95无界，不能只用匹配尾延迟 |
| B0 Chrome | 真tabCapture同片8必要项：正确WS=正确可读DOM=3/8；原PCM回放4/8；11字幕实际显示10条达到可读时长；首正确DOM播放起点后约6.683s | 34项WS成绩不是Chrome成绩，缺词尚不能独归因AAC/重采样/VAD；点击起点未独立计时 |
| B0 调度 | ASR executor等待P95约0.247ms，MT队列等待0.502ms，ASR计算150ms，MT计算449ms，VAD放行628ms | 无证据断言当前瓶颈为GPU争用；各阶段P95不可相加 |
| B1四项 | PAD13/12；PUNCT18/18；PLaMo0.8和2均17/17 | 都已测而非待做；全部未过Q/R离线前提 |
| B2两项 | SenseVoice8/9；whisper-ja76016/15 | 不因低CPU或参数量重复列为未经试验的新候选 |
| B3两项 | Vox17/18，Gemma官方6/8；对照20/18 | 无候选晋级；Q门槛是双轮23/21且零新增关键错和同轮保护损失，不是超过失败候选即可 |
| B4-1 | Gemma只中文0/3合规G0，failed_task | 88×2、正式34项评分与完整成本收益为NOT_RUN，不能报0/34或已证实加速 |
| 最新清理验证 | 历史记录37项P2测试通过，当前模型离线烟测PASS；correctness_score=NOT_ASSESSED；realtime_or_browser=NOT_RUN | 是历史记录不是本次重跑，也不是新质量/实时验收 |

正式R主指标仍为 `paired_common_correct_dom_p95`；基线seed29仅18项正确，低于20个共同正确且人工校时的正式资格，当前不能登记正式R成功，也不能悄悄改为内存主指标。文件清理减少目录占用32.269GiB只是du路径占用差，不是APFS实际物理回收，也不是模型推理内存优化。

## 4. 已有类、函数与可复用边界

| 文件 | 精确入口/签名 | 可复用点与限制 |
|---|---|---|
| `p0/p2/types.py` | `AudioSpan(clip_id, session_id, epoch, start_sample, end_sample, accepted_samples, pcm_path, pcm_sha256)`；`SourceUnit(source_id, span, text_ja, metadata={})`；`TranslationResult(source_id, raw_zh, text_zh, finish_reason, metadata={})` | 已有sample-span校验；不是多模型统一异步adapter ABC |
| `p0/p2/events.py` | `EventLog(path: Path, run_id: str, experiment_id="B0", repeat_id="baseline")`；`record(event, *, session_id, epoch=0, **fields)`；`validate_causality(events)` | 现有host monotonic_ns、accepted/consumed/seq/closed epoch检验，可复用但P3需补自己在线事件与终止理由 |
| `p0/p2/runner.py` | `pcm_packets(audio)`；`async feed_1x(audio, accept, record, session_id, *, epoch=0)`；`run_probe(experiment_id, mode, manifest)` | 保留真实短尾，无补零扩大时间轴；run_probe只B0，不能调任意adapter |
| `p0/p2/realtime.py` | `replay_baseline(run, repeat)` | 真实WS1×基线；实验seed控制；验证server acceptance须join server trace，client_sent不是已接纳 |
| `p0/p2/freeze.py` | `validate_lock(lock)`；`frozen_fingerprint(run)`；`freeze(run, registry)` | 可借鉴sha/锁/状态；ROOT/PLAN/HISTORY均绑定旧布局，不应从P3目录复制后盲用 |
| `p0/p2/b1.py` | `freeze(run, registry, *, phase="B1", model_additions=None, runtime_lock=None, hypotheses=None, extra_sources=())`；`verify(run, *, runtime_reader=None, runtime_key=None)`；`execute(run, key, operation, output, *, verify_run=None)`；`bounded_translate(operation)` | 执行收据、冻结、30s SIGALRM；必须显式传P3依赖，不能改旧run锁来适应新代码；SIGALRM不是GPU硬抢占 |
| `p0/p2/adapters/reazon.py` | `pad_for_decode(samples,sample_rate,head_ms,tail_ms)` | 已实现PAD探针，失败历史保留 |
| `p0/p2/adapters/punctuation.py` | `Punctuation(snapshot).restore_punctuation(text_ja)`；`restore_with_labels(text,predict)` | 标签可逆验证与不改文字边界已有 |
| `p0/p2/adapters/plamo.py` | `Plamo(snapshot).translate(text)`；`prompt_h0(tokenizer,text)`；`encode_prompt(tokenizer,prompt)` | 具体PLaMo包装，不是统一MT协议 |
| `p0/p2/adapters/sensevoice.py` | `SenseVoice(snapshot).decode(samples)`；`clean_result(raw,fields,allowed_tags=())` | 已完成冻结词表的控制tag清洗，不能泛化未知tag |
| `p0/p2/adapters/whisper_ja.py` | `WhisperJapanese(snapshot).decode(samples)`；`collect_segments(generator)`；`generation_trace(...)` | 已强制独立tokenizer、惰性生成器穷尽及native生成元数据；P3无需重造 |
| `p0/p2/b3_protocol.py` | `ChineseProtocol.parse(self,raw,finish_reason)`；`StreamCommitter(cuts).advance(*,accepted,frontier,pieces,eof=False,pending_text_start=None)`；`validate_audio(samples)`；`source_seed(row,original)` | 冻结官方Gemma双语协议/因果提交；不是所有AST/ASR统一输出解析器 |
| `p0/p2/b3_worker.py` | `vox_session(model,pcm_path,cuts,clip,out)`；`vox(run,out,capability)`；`gemma(run,out,capability,*,instruction=None,parser=None,expected_prompt_sha256=None)` | 实际原生流和完整音频tower路径；离线短会话，未提供生产取消/在线MT |
| `p0/p2/resources.py` | `summaries(output,audio_s,scope,requests=None)`；`main()` | 重用p0.monitor，250ms采集及phase；已实现CPU峰值/CPU秒、RSS/footprint、压力swap与12GiB守卫 |
| `engine/src/subtitle_service/backend.py` | `SubtitleSession.accept_pcm(frame:bytes)`、`finish()`、`aclose()`；`SubtitleBackend.warmup()`、`available()`、`open(emit,*,record:DiagnosticSink|None=None)`、`aclose()`均async | 真实产品backend合同；P3试验可实现实验后端，但不能声称新候选已注册生产 |
| `engine/src/subtitle_service/local_pipeline.py` | `ReazonRecognizer(snapshot,vad_path,config)`；`decode(samples)`；`segments(vad,frame,segment_ids,record)`；`HyTranslator(snapshot).new_session()/translate(session,text_ja)`；`LocalBackend(cache:Path,profile).open(emit,*,record=None)`；`LocalSession(backend,vad,translation,emit,record)` | 当前CPU ASR/MLX MT是独立单worker池，不能为新全MLX组合沿用“无争用”结论 |

## 5. B3原生流式：已做与未做

`vox_session`真调用 `model.create_streaming_session(max_tokens=8192, temperature=0., transcription_delay_ms=960)`，逐次读取6400 bytes（200ms）当前包，`s.feed(pcm.copy())`，`s.step(max_decode_tokens=min(4,remaining))`，视频EOF才`s.close()`；一视频一session，跨VAD端点不重置。

提交要求同时满足已接纳采样、原端点released watermark和decoder frontier；生成槽映射估算 `[k*1280,(k+1)*1280)`，不是声学字级时间戳。UTF8跨槽用联合区间避免字符漏归属；flush端点等native done；committer仅合并同时待提交端点，不回写已提交内容。pending PCM>64000、累计生成>8192、EOF drain前后>8s会失败。

资源确认后实际9条PCM/2382个feed、最大3223生成步、88源文含12空项；9次均audio_exhausted_no_eos。相同Hy另阶段执行，两轮各88记录、76个非空调用。`realtime_paced=False`，`mt_queue=NOT_RUN`，`full_chain_resource_pass=False` 已写在源码输出中。故P3需要新增在线native session runner、1×供流、真实MT队列与取消/重启，而不是再补一个同义的“原生流式adapter”就称实时实现完成。

Gemma实际调用feature extractor→audio tower→projection→LM；每请求fresh KV、模型常驻；`stream_generate`是token输出流，不是音频流式输入。已有padding128→640的单次部署修复，严格占位数=projection帧数仍检查。量化制品中BF16音频塔/投影与FP32激活均已记录；4bit模型名不代表全模块4bit。P3若研究进一步量化，必须另冻结artifact和实际张量dtype，不得把旧修复当成新收益。

## 6. 量化、缓存与资源的真实现状

`b3_vox_cache.py`必须读取 `locks/vox-allocator-policy.json`，检查cache_limit_bytes=536870912、targeted_vox_repair_number=1，再在隔离worker用 `mx.set_cache_limit`。只限制free allocator cache，未量化KV、未限制active memory、未改变模型权重。默认空闲缓存首G0触及12GiB进程树守卫，最后保存点11.216GiB，触发峰值未采到；不能补写“精确12GiB峰值”。

observer已有weakref生命周期修复：强session引用延迟KV释放的插桩峰值5.7135GiB，修正后4.5369GiB；全9 token序列/2382包/88源文一致才复用旧MT判定。这是同候选测量修正，不是相对B0的R收益。返回后end_active在局部模型引用释放之后，也不是模型常驻内存。

资源已有：p0.monitor macOS task_info进程树footprint、CPU；P2补RSS、CPU秒、CPU峰值；系统GPU/共享内存、memory_pressure/swap/vm_stat；250ms点采样、系统压力每4s更新。`resources.py`会因总时长超限或进程树footprint≥12GiB抛出停止，但没有实现持续critical压力自动停止。ANE、独占VRAM明确N/A。全机GPU减idle仍不是模型专属占用；RSS、footprint、MLX、GPU共享内存有重叠不相加；ASR/MT分开峰值不可相加成联跑峰值。

P3如增加KV量化、缓存上限、自适应调度、压力守卫，应分别记录权重/运行时精度、KV/cache policy、active/free/peak、加载/热态/释放阶段、进程树/全机scope，不能只给一个“显存”字段。

## 7. 生产调度、背压与取消合同

主合同为 `docs/subtitle-backend-contract.md`、`protocol/v1.schema.json`；实现为 `engine/src/subtitle_service/app.py`、`local_pipeline.py`。v1只有start、PCM、finish；用户取消通过关闭WS，不存在新增v1 `cancel`控制消息。`epoch`是实验日志字段，不是公开WS schema字段。

| 层 | 已有实际行为 | P3需保持/另验 |
|---|---|---|
| 网关 | s16le/16k/mono；正常3200 samples，尾包1–3199 samples；短尾后只能finish | native内部padding不能扩大accepted范围；统一时间轴仍为已接纳真实采样 |
| PCM背压 | `QUEUE_FRAMES=20`，64000待处理采样；等待入队≤1s；满队列仍监视disconnect | 不得用丢旧PCM/静默跳帧让新模型看似追平；新增策略必须预登记与可审计 |
| 当前ASR/MT | 两个ThreadPoolExecutor各max_workers=1；MT pending maxsize=4，另最多1在途；QueueFull→overloaded | 这不是全局MLX scheduler；两个新MLX模型并行需另处理owner/thread/显存与优先级 |
| EOF | `finish`处理已接纳队列，flush VAD/ASR/MT，8s drain后可报drain_timeout | 超时错误≠同步GPU立即停止；必须保留清理中状态 |
| 取消 | `Connection.active=False`先封sink；cancel asyncio tasks；`LocalSession.aclose()`close translation、cancel MT task、shield等在途ASR/MT完成 | Python取消不能杀同步worker；晚到输出不能泄漏；清理未完不能接纳下一会话 |
| 占用 | 网关全局单lease，`await session.aclose()`之后才释放lease | 新native后端必须同样保留busy直到安全退出，不得提前ready新会话 |
| 字幕 | `seq`递增、end不回退且end≤accepted；无汉字/带假名等被filter并warning | 不回写旧字幕；不能借过滤遮蔽raw错误；P3内部revision实验不自动改v1 |

已有测试：`engine/tests/test_gateway.py::test_overload_and_drain_timeout`、`test_cancel_releases_and_isolates_sessions`；`p0/p2/tests/test_contract.py`的未来音频、closed epoch晚到输出、短尾511/512/513、人工静音不扩大span、DOM可读与不完整证据检查；`test_b3_protocol.py`覆盖端点/decoder双watermark、UTF8跨端点、flush等native终止、弱引用生命周期、stub causal reader。已有这些不是“完整新候选取消测试已通过”，P3必须为新native路径补真实阻塞/取消/重开/泄漏测试。

## 8. 推荐P3新增/扩展位置（全部尚未实现）

避免修改历史P2推理源和锁来“兼容当前checkout”。建议新增独立 `p0/p3/` 与 `docs/live-video-translate-p3-YYYY-MM-DD/`；下表是文档规划，不是当前仓库路径。

| 拟新增位置 | 责任 | 复用/对接 |
|---|---|---|
| `p0/p3/cli.py` | P3显式phase/experiment分派、静态校验与会推理的capability分开、状态/预算出口 | 借鉴P2 CLI，但不得声称P2 `--phase P3`已存在；须先help/unsupported回归再提供新命令 |
| `p0/p3/freeze.py` | 新run，采用immutable历史receipt+新source/runtime/model/precision lock；generic prior引用 | 复用digest/validate_lock思想；不要照搬b3.PREVIOUS固定本机路径 |
| `p0/p3/adapters/` | 新候选的实际模型API、输入/输出、termination、native state、dtype | B3Vox路径优先薄包装；Qwen等新模型确需新增，不把token流当音频流 |
| `p0/p3/native_runner.py`（名称建议） | 1×输入、独立accepted/ingested/frontier、在线MT、背压、cancel epoch、EOF与安全释放 | 对接SubtitleBackend/Session合同及EventLog；B3Vox离线源文可作回归oracle |
| `p0/p3/resources.py`或扩展wrapper | 新MLX并行调度、KV/cache/pressure字段，热态联跑 | 原生计数已有，优先增加字段/phase与导出，不再写一套混scope“显存” |
| `p0/p3/tests/` | adapter stub、取消在途、旧epoch晚到、满队列断连、模型owner、KV释放、EOF/长会话 | 先无模型测试；设备实测只在新用户授权后执行 |
| `docs/p3-*-results-YYYY-MM-DD.md` + `docs/experiments/p3-*/` | 新结果与紧凑摘要 | 历史B0/B1/B2/B3/B4不覆盖；模型/PCM/token/raw继续ignored |

后续若获准晋级产品，再分别评估 `engine/src/subtitle_service/config.py`类型配置、`factory.py`后端注册及新backend实现；不是本次文档包内授权的产品改动。依赖不应无条件升级engine锁：当前 `engine/pyproject.toml` Python>=3.12,<3.13、MLX0.31.2/MLX-LM0.31.3、WhisperLiveKit pin 13ee8515…；B3独立MLX0.32.4与transformers5.22.1不能混作同一环境。

## 9. 计划易踩坑清单

- 最新code与历史报告叙事的时间不一致：B0报告当时说仅B0实现，当前pin已有B1–B4；只用旧README会产生错误任务图。
- `preflight`在B3候选会跑G0；文档验收脚本不得批量调用它自称“无模型静态校验”。本次仅help。
- B1/B2/B3固定本机PREVIOUS/解释器/源码路径；不要用新registry绕过锁或“修复旧run路径”来重写历史。
- P2 CLI的`replay/holdout`help展示不能当新候选已支持/可解封；P3接口实现前只给步骤说明，不能发布虚假一键运行命令。
- 1×只表示包按时进入；要同时观测accepted、consumed、native frontier、MT backlog、WS/DOM，不能从offline RTF推断首正确字幕。
- B3原生Vox“跨VAD保session”已实现，但在线MT/生产取消/10分钟或60分钟有界未验证；无需重做前者，却不能跳过后者。
- 8192累计生成步为短探针上限，增长上限不是长稳/可无限流证明；硬截断必须标cap不是EOS。
- 512MiB free cache不等于512MiB模型占用或KV量化；observer弱引用修复不等于模型内存收益。
- 30s SIGALRM和8s检查不是可抢占GPU；清理中的lease不能提前释放。新增任务取消要测底层调用安全返回。
- 模型名4bit、FP16不能覆盖音频塔/projector/KV/激活真实dtype；进一步量化必须用制品检查和实测证明。
- 系统GPU/共享内存不能称独占，多个组件峰值不相加；压力level与swapout0也不等于全程无交换。
- 新参考/词表/上下文实验改变输入和seed映射时，不能无条件继承旧20/18或“逐字相同”分数；必须声明采用边界与回归条件。
- 本机候选缓存已清理；恢复下载与依赖在测量前完成，不能把安装/下载资源夹进热态收益，不能误删当前模型共享blob。
- 本文是只读对接核查，不构成P3模型运行、候选扩池、人工任务、封存集、产品修改或发布授权。
