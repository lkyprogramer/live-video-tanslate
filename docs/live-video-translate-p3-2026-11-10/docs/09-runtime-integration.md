# 09 待开发的 P3 接口与现有代码对接

本章所有 `p0/p3/*` 都是**未来实施目标**，不是本包已实现文件。包内仅 `scripts/p3_evidence.py` 的三个离线辅助命令可运行。先建无模型测试，再加候选，不把安装成功当作实现完成。

## 现有能力与新增边界

| 现存位置 | 可复用能力 | 不能直接假定 |
| --- | --- | --- |
| `p0/p2/types.py` | AudioSpan、SourceUnit、TranslationResult | 并无统一原生流适配器 |
| `p0/p2/events.py` | EventLog、部分因果水位检查 | 不证明前缀不可回写、DOM可读或真实worker取消 |
| `p0/p2/runner.py` | pcm_packets、B0分段与MT复现 | 现有run_probe只支持B0；feed_1x事件名须核对真实语义 |
| `p0/p2/b3_protocol.py` | StreamCommitter、确定性source_seed | piece区间是估计，不是音素／词级真实时间戳 |
| `p0/p2/b3_worker.py` | Vox持续session、UTF8跨槽、EOF等 | 未实现候选在线MT、1×、生产取消和长稳 |
| `p0/p2/realtime.py` | B0实际WebSocket 1×回放 | 不能换experiment ID就跑新候选；不是Chrome DOM验收 |
| `p0/p2/resources.py`、`p0/monitor.py` | 树内存、CPU、全机GPU、采样及部分守卫 | 需补持续压力和native owner观测；不重造另一套混口径采集 |
| `engine/src/subtitle_service/backend.py` | SubtitleBackend、SubtitleSession异步合同 | 新适配器未被生产factory注册 |

不得修改历史P2冻结代码或旧receipt来适配新run。若新P3只需调用旧稳定纯函数，锁住导入文件hash；如果必须改变行为，在P3显式包装并做回归。

## 原生接口规格

目标：`p0/p3/types.py` 与 `p0/p3/adapters/base.py`。下列为需要实现的Python类型规格；并非拷入本包后已经能加载模型。

```python
@dataclass(frozen=True)
class StreamContext:
    run_id: str
    session_id: str
    epoch: int
    clip_id: str
    sample_rate: int  # 固定16000

@dataclass(frozen=True)
class AudioPacket:
    sequence_id: int
    start_sample: int
    end_sample: int
    pcm_s16le: bytes
    accepted_at_ns: int

@dataclass(frozen=True)
class StablePiece:
    start_sample: int
    end_sample: int
    text: str  # 本次新定稿delta；不可重复包含旧文本
    alignment_kind: str  # 可证decoder区间或causal_input_envelope，不能混称词时标

@dataclass(frozen=True)
class NativeUpdate:
    consumed_samples: int  # 已完成native计算实际使用的真实PCM末端，不是内部buffer长度
    decoder_frontier_sample: int | None  # 无词时标时为None，不伪填consumed
    committed_text: str  # 整个session/epoch的累计已定稿日文
    pieces: tuple[StablePiece, ...]
    pending_text_start: int | None
    provisional_text: str | None  # 仅诊断，不允许进入MT
    finished: bool
    termination_reason: str | None
    diagnostics: dict

class NativeSession(Protocol):
    def push(self, packet: AudioPacket) -> NativeUpdate: ...
    def finish(self) -> NativeUpdate: ...
    def close(self) -> None: ...

class NativeAdapter(Protocol):
    def warmup(self) -> dict: ...
    def open(self, context: StreamContext) -> NativeSession: ...
    def close(self) -> None: ...
```

这些是同步native接口，由**单个所有者worker**串行调用。外层 `p0/p3/native_runner.py` 异步处理WS、PCM队列、取消和Hy任务；不得在事件循环直接执行重型同步推理，也不得多线程同时操作一个模型state。全链路资源把子进程／线程执行器及Hy计入。

构造、warmup、open、push、finish、close使用固定owner；是否必须单进程隔离由所选backend决定，并写入runtime-lock。实验如使用进程，超时后关闭/回收该worker有明确有界过程；若无法安全终止，保持清理中并停止本轮，不能谎报lease已释放。

不变量：

- packet无重叠无缺口，`len(pcm_s16le) == 2*(end-start)`；正常3200样本，短尾后只能finish。
- `0 <= consumed_samples <= accepted_samples`，单调不回退；可证的decoder_frontier存在时再要求0 <= frontier <= consumed且单调，无时标路径为None。送入native的音频哈希可追踪到真实已接纳PCM。
- `committed_text.startswith(previous_committed_text)`，本次pieces拼接恰好等于累计文本的新后缀；不通过Unicode归一化掩盖回写。
- 每piece按alignment_kind提供可证decoder估计区间或实际输入的因果包络，end不越过consumed；均不能代替独立语义参考时间。只有选择对齐桥接时才对未知位置保守等待；R2T2按下节已登记的无时标屏障快照，不等待不存在的word timing。
- R2T2内部重置／16秒滚窗不改变外部session/epoch的累计账本。只能内部记录新的utterance/window ID，不能换外部scope绕过prefix检查。
- pending尾部的位置、right context、padding和输入余数分别记录；padding不增加accepted/consumed真实样本。
- finish最多一次，close可重复且幂等。R2T2 step_decode_budget_exhausted是正常有界步进，保留state继续，不设finished；session_token_cap、timeout、overloaded或持续无进展是异常，不能冒充自然EOS。upstream未暴露真实finish_reason时记unknown，不从text推测EOS。所有终止状态保留。

## S0／S1 提交器

目标：`p0/p3/commit_bridge.py`。

1. 从现有VAD生成同音频的实际因果端点，带 `released_at_sample` 和 `flush`。离线可冻结端点列表，但runner在水位到达前不得让策略读取未来条目，更不能用参考语义句末替代VAD。
2. 仅具有可证frontier及piece区间的对齐S0调用被锁定的 `StreamCommitter.advance(accepted=..., frontier=..., pieces=..., eof=..., pending_text_start=...)`，或P3的等价包装。pieces转换为其真实 `(start,end,text)` 格式；先补严格的consumed／单调／重复验证，不能只依赖旧函数的有限检查。R2T2不执行本步，按下节无时标子策略；其余共同的真实VAD、只翻译一次和真实ASR约束仍适用。
3. 每个释放的SourceUnit只翻译一次。空原文记录missing/empty，不调用Hy制造中文。端点等待和coalescing必须进入日志。
4. S1只在已定稿日文中检测固定句末“。！？!?”，切出完整新句并维护已消费字符游标；同一文字随后遇到VAD端点不再次翻译。未带标点的尾部留给S0。原文span必须与已接纳音频对应，句末检测不能给它虚构声学时间。
5. 所有片段使用真实ASR文字进入现有Hy；禁止把正确日文、正确假名oracle或人工标点塞入产品评分路径。

对齐桥接的保守映射可能推迟提交；无时标R2T2按下面快照屏障，不假造对齐。S0测量实际模型/提交组合的中文质量与资源，机制归因须同子策略对照；S1另检提前稳定整句是否改善中文时效，两者不能混成一个分数。

### R2T2固定的无时标S0子策略

S0_UNTIMED_VAD_SNAPSHOT是该主配置的一部分，不直接灌入旧StreamCommitter。这里consumed仅在该次native解码实际完成并使用对应真实PCM后推进；push只追加缓冲、尚未凑够首320/后160ms时，不能推进水位或满足VAD屏障。每次实际VAD release记录release_watermark；只在native实际consumed覆盖该水位的首个已完成update上取累计稳定prefix相对已送MT字符水位的新后缀。覆盖多个release可合并但记录IDs，只提交一次；空后缀留empty，不等待无定义的最早pending位置。

迟稳定的U1尾字留到下一release/EOF，真实计入迟到或漏译。每delta保存生成时实际输入窗口包络和commit_at；聚合span为包络并集，alignment_kind=causal_input_envelope，不能证窄范围时保守[0,consumed]。这些区间是输入证据，不是词/语义时标；R2T2 decoder_frontier_sample为None，pending_text_start=None也不表示没有隐藏尾。语义延迟只用冻结独立参考与DOM。

源prefix/已送MT字符游标在内部W16O8裁切时保留。无模型反例覆盖跨VAD/后置否定、空release、同update多release、迟稳定尾、滚窗、整块/短尾EOF。不能证明因果包络则blocked_runtime并给reason。该策略改变MT分组；B0是完整产品组合对照，基础Qwen机制控制用同策略，不作仅ASR因素归因。

## MT、中文和安全生命周期

保持Hy权重、M0模板、seed生成规则与中文过滤器不变。现有 `HyTranslator.new_session()/translate()` 的初始化和调用线程行为必须实测；不要复用已close的session。新日文范围或文字通过 `source_seed` 派生；同一输入使用旧index以保持可比。

`p0/p3/backend.py` 实现现有 `SubtitleBackend/SubtitleSession` 的**实验实现**，由 `p0/p3/server.py` 选择，禁止未经产品批准注册到main默认factory。无需为P3修改公开v1消息：开始、PCM、finish不变；取消沿用关闭WS，epoch仅为内部诊断。

取消处理顺序固定为：封住外部输出sink → 记录epoch关闭 → 停止接纳和发起新的native/MT任务 → 等待在途任务安全返回／进程回收 → 丢弃晚到结果 → close模型state → 释放全局lease。不能因asyncio task取消就立即允许新session。

队列上限仍是PCM64000样本、enqueue1秒、MT pending4+inflight1、drain8秒。必须观察队首年龄，严禁丢音频追赶。若ASR/MT同用Metal，应先记录单跑与联跑，不盲目另建线程以为能“物理隔离”带宽。

## 必须新增的无模型测试

这些测试在目标机下载模型之前完成，可用受控native stub，但stub成功不代表真实模型成功。

| 目标测试文件（待实现） | 必测场景 |
| --- | --- |
| `p0/p3/tests/test_types.py` | 200ms、短尾、奇数字节、重复包、漏包、浮点/布尔水位、越界 |
| `p0/p3/tests/test_commit_bridge.py` | prefix回写、日/中文独立账本、跨UTF8／标点、重复翻译、VAD未放行、pending跨边界、S1回退 |
| `p0/p3/tests/test_lifecycle.py` | 在途blocking stub取消、晚到结果、busy持续、异常close、满队列时断线、finish两次、epoch隔离 |
| `p0/p3/tests/test_adapter_contract.py` | 两适配器共同接口、内部window reset不清外部prefix、padding不扩大水位、步预算与session cap区分、R2T2累计返回只差分一次、整块EOF隐藏尾 |
| `p0/p3/tests/test_cli.py` | 静态check绝不加载模型；未知ID拒绝；未锁拒绝；模型动作须显式capability/probe；预算耗尽拒绝 |
| `p0/p3/tests/test_evidence.py` | 时间校准、missing分母、20项资格、provisional人审状态、source lock／配置变更失效 |

旧网关取消／背压、P2 B3提交器、短尾与未来音频测试仍须通过。集成完成后再补真实native取消、重复open/close和60分钟；两者不能互相替代。

## 拟实现 CLI 合同

目标 `python -m p0.p3.cli`，顶层 `--run-dir` 和 `--registry`。P2没有 `--phase P3`。下面只是验收规格，**完成T02并通过test_cli之后才允许执行**。

| 子命令 | 行为与边界 |
| --- | --- |
| `check --experiment ID` | 静态文件/锁/权限声明/预算检查，禁止模型导入和推理 |
| `freeze --experiment ID` | 创建新run不可变锁；拒绝已有不一致目录；不下载、不推理 |
| `capability --experiment ID` | 显式加载模型和G0短探针；必须在已授权、已锁定后调用 |
| `probe --experiment ID --mode causal-stream|fixed-cuts|fixed-text --repeat 1|2` | 按registry允许模式；quality-only，不能冒充1× |
| `score --experiment ID` | 缺独立文本判定时导出待审包、score_status=unscored；冻结AI参考+独立逐项对账后可provisional研究筛选，15项待听审单列；正式人审/发布资格仍未通过 |
| `replay --experiment ID --repeat 1|2 --surface ws|chrome` | 仅合格候选；必须实际1×、完整队列和对应显示证据 |
| `accept --experiment ID --stage engineering|holdout` | 分门校验；holdout只有显式授权/冻结后可打开；不发布 |
| `report` | 读取本run输出，列出not_run/失败/缺证，不引发重跑 |

`--help`必须与已实现分支一致；未实现的命令明确退出3及`not_implemented`，不得先写help再当成功功能。未来CLI统一机器输出 `status`、`evidence_paths`、`blockers`、`next_allowed_action`；权限阻塞不自动重试。P3 run锁格式采用本包schema，旧P2 `models` 格式显式转换为独立历史receipt，不能交叉传入两个validate函数。
