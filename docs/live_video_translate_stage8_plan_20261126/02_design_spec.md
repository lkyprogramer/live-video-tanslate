# 02 · Stage 8 设计合同

## 1. 目标与非目标

目标：在 M1 Pro 本地、Chrome 正常播放日语视频的条件下，减少实质听错/漏句/译义翻反，并更早提供可靠中文字幕；任何“更快”必须有对应正确率与显示稳定性，任何“更准”必须计入等待、队列和资源代价。

非目标：证明芯片理论上限；修改用户已完成的旧修复；无差别常驻多个大模型；把字幕内容上传云端；训练一个新基础模型；重新开发通用推理服务器；在无 profile 证据时重写 Metal/Core ML 内核。

三个可选方向中，采用 A 为基础、条件开放 B/C：

| 方向 | 成本/风险 | 决定 |
| --- | --- | --- |
| A：当前模型的测量、协议、输入与缓存校正 | 低到中；可能直接消除软件引入的错误 | 先做，作为全部选型前提 |
| B：替换一个翻译模型 | 中；质量可能提升，但内存/预填充/输出长度改变 | 有限对照，不预设MoE胜出 |
| C：ANE草稿＋选择性GPU ASR终稿 | 高；时间戳、重算与GPU争用复杂 | 只在同段实验证明互补时实现 |

## 2. 全局约束（执行计划必须遵守）

1. `BASE_SHA=7ea13865c8c3673a1463495058a200723b153111`；已有 Stage 7 档位默认不改。
2. 正式指标用真实 Chrome 1×、DOM事件与媒体采样时钟；WS/离线结果单独标注。
3. 采样率固定 `16000Hz`，音频样本区间使用左闭右开 `[start,end)`，禁止提前提供尚未捕获的真实音频。
4. 正式最终字幕提交后不可被后台复核静默改写；未提交预览可修订但必须有revision与来源。
5. 每个试验只加载一个 MT 主模型；每次最多增加一个 ASR 挑战者。不同 MT 候选不同时常驻。
6. Qwen 4B 的当前 runtime/精度/模板是控制变量；新 runtime 单列为 model+runtime 臂，禁止声称纯模型增益。
7. `trust_remote_code` 不自动开启；下载、外部代码执行、特权遥测、提交、推送、切默认均不在本次文档授权内。
8. 保持 ANE 空闲不计算、GPU 回退串行、关闭先join后释放这三项已修复行为。
9. 硬阈值统一读 `templates/thresholds.proposed.json`；实测前锁定副本与哈希，事后修改须开启新experiment_id。
10. 公共报告不得包含视频、完整SRT、私有绝对路径或访问令牌；原始证据保存在用户授权位置。

## 3. 数据与接口边界

以下为 Stage 8 **新增拟议**接口，采用标准库 dataclass/TypedDict 即可；不要为一次实验加消息总线或数据库。

### AudioSpan

`AudioSpan(session_id: str, epoch: int, target_start: int, target_end: int, input_start: int, input_end: int, captured_end: int, sample_rate: int=16000)`

- `target_*`：本条字幕有权提交的目标音频区间。
- `input_*`：送入识别器的 PCM，可包含已听过的左上下文。
- `captured_end`：当时真实已捕获的末样本；须 `input_end <= captured_end`。
- 合成零 pad 不算真实未来音频；其长度单独记录，不能移动语义时钟。
- 额外真实右侧音频的等待要计入 end-to-end，不用“只是上下文”排除。

### ASRHypothesis

`ASRHypothesis(span: AudioSpan, text_ja: str, model_key: str, variant: str, tokens: list[TokenSpan] | None, timing: dict, flags: list[str])`

`TokenSpan(token_id: int | None, text: str, start_sample: int, end_sample: int, time_kind: str)`；`time_kind` 为 `emission_approx` / `aligned` / `unknown`。NeMo 当前帧值是约80ms粒度的发射位置，不冒充准确词首；无时间戳的Qwen A3不能通过字符串差集推断可靠归属。

### TranslationRequest / Result

`RequestKey(session_id: str, epoch: int, caption_id: str, revision: int, request_id: str)`。

`TranslationRequest(key: RequestKey, kind: str, source_ja: str, history: list[Turn], span: AudioSpan, deadline_ns: int)`

`Turn(source_ja: str, target_zh: str)` 只可来自之前真实完成并接受的同场景字幕。`kind` 仅 `preview` 或 `final`；history 不可取当前/未来参考译文。`TranslationResult(text_zh: str, stop_reason: str, metrics: dict, valid: bool)`。

预览使用可撤销临时缓存，不能污染最终历史。模型切换、session/epoch变化、12s断档策略变化、历史裁剪、模板/量化变化均需验证cache身份并按实际token前缀重建。不得把“保留四轮”当作“四轮100%命中”。

### 评测事件

`Stage8Event` 的字段、时间域、事件集合由 [03](03_measurement_contract.md) 定义。输出JSONL，一条真实事件一个record。每次run有唯一manifest及哈希；结果全量保留失败/空输出/超时/网关拒绝。

## 4. 字幕状态与音频状态分离

只需要三个字幕状态：`OPEN → PREVIEW → FINAL`，OPEN可直接FINAL；`CANCELLED`为终止态。revision只在OPEN/PREVIEW递增。任何epoch变化取消旧任务、清除旧预览与历史；FINAL一旦接受，不能后台偷偷换文案。

音频捕获继续使用既有VAD与切片。**先不重构整个VAD**：第一轮固定 Stage 7 target spans，对同span做不同解码/上下文比较。确认边界问题后，才让截断处保持有限未提交尾部：

- 左上下文先试输入总长度上限4s/6s；不会为了凑满窗口而等待。
- 强切点右侧额外真实音频试 `0/160/320ms`，每档独立实验；到等待上限或新的session/seek时立即结束/取消。
- ownership只允许提交target区间及被明确延期的尾部，绝不重复提交以前FINAL覆盖的区间。
- 无可靠词/token归属的ASR只可替换一个完整、尚未提交的target span；不得直接输出滚动6–8s的全文。
- 当前2.8s强切与语义结束不是同一概念；质量评测以人工标注的语义单元对齐，而延迟仍保留每次真实采集和提交时间。

本阶段不承诺“任何边界都无损”。无法确认归属时回退当前NeMo结果并记原因；不通过拼更长文本伪造召回。

## 5. 调度和内存设计

ANE请求与现有MT可异构并行，但CoreML运行、CPU beam、Metal共享统一内存与系统调度，不能视作完全独立资源。

若引入GPU ASR，沿用一个GPU执行权控制器，不创建第二个无锁模型线程。优先级：**已选源文的final MT > 当前未提交span的ASR复核 > preview MT**。ASR复核在final选稿截止前完成才有资格被选；未完成时按期使用NeMo，不等待慢队列。正在运行的GPUkernel不保证可抢占，只在安全边界取消。

- pending preview最多1个，后来的覆盖旧的。
- 每个session最多1个pending ASR复核；过期、旧epoch、旧revision丢弃并记录。
- final不得silent-drop；超时必须以独立状态计入失败/可用率，不用删除样本美化P95。
- 选ASR在一次MT最终提交之前完成；高精结果到晚了，仅用于shadow评估，不能污染历史。

模型侧工作集可从12GiB预算开始，有证据再提高到18GiB；**这不是整机安全承诺**。大MoE单独常驻MT试验，先卸载4B。若内存压力异常、swap持续增长或Chrome显著掉帧，结束该臂，不通过关掉视频或隐藏后台应用完成“线上通过”。磁盘82GiB是用户描述，preflight必须重测，并计下载缓存、转换临时文件、结果和旧模型，不盲目复制所有格式。

## 6. 实验分层与决策

`G0资料完整 → G1协议正确 → G2当前模型消融 → G3候选质量/时延 → G4合流shadow或单组件集成回归 → G5Chrome留出/长跑`。

MT和ASR离线探索在G1后可并行做，但**同一台Mac不同时计时**，避免争用导致假比较。单组件胜出不等于组合胜出，G4/G5必须重测。

结果状态统一：`PASS`、`FAIL`、`INCONCLUSIVE`、`BLOCKED`。模型下载受限、人类审阅缺失、原始媒体缺失都不是“模型失败”；版本/素材错误也不是可接受的性能数据。

全链最终合同参见 [11_machine_contracts.md](11_machine_contracts.md)：区分部署/归因/候选基线、明示任务截止与提交水位、缺失计分、数据schema。holdout仅在G5对冻结的完整候选解封；G3/G4不能使用它调参。无双ASR的MT或预览改动可跳过双ASR代码，但不能跳过生命周期/DOM集成回归。

## 7. 延后方向

- 日→中稳定前缀训练：在准确的prefix轨迹和许可语料到位后另建Stage9研究；不把英→日/中论文直接称日→中实证。
- 原生流式encoder改造：需要训练/注意力/CMVN因果性证据，不是把上次hidden state缓存即可。
- 更多ANE模型/整段MT迁ANE：先证明当前瓶颈在可迁移算子，而非语义等待、CPU decoder、KV replay或MT输出长度。
- 4090训练/远程推理、云API：可独立另选，但不算本机M1 Pro路线的提升，本包默认不启用。
