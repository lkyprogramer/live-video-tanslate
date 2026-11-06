# 本地字幕服务 v1 合同

按[开发方案](development-plan.md)执行。实现服务和浏览器时读取「浏览器协议」与「播放生命周期」。接入或替换引擎时读取「后端策略」与「新增后端」。

## 浏览器协议

默认服务地址为 `127.0.0.1:18765`。HTTP 与 WebSocket 共用同一端口。

| 接口 | 合同 |
| --- | --- |
| `GET /v1/health` | 返回 `{"status":"ready"}`（200）或 `{"status":"starting"}` / `{"status":"unavailable"}`（503）。配置校验通过后启动监听，默认策略在后台预热。预热完成后才返回 `ready`。详情只写本地诊断。 |
| `GET /v1/profiles` | 需要 `Authorization: Bearer <token>`。返回 `default_profile_id` 和 `profiles` 数组。条目只有 `id`、`label`、`available`。禁用项不公布，已启用但上游未就绪的项显示不可用。错误令牌返回 401。 |
| `WS /v1/captions` | 一条连接对应一个字幕会话，文字帧为 JSON，音频帧为原始 PCM。握手后先收 `start`，通过认证后才申请会话、调用策略。 |

服务只绑定 IPv4 loopback，校验 `Host` 为配置端口下的 `127.0.0.1`。WebSocket 升级前校验精确 `Origin: chrome-extension://<extension-id>`。HTTP 有 Origin 时用同一白名单处理普通请求和 CORS 预检，允许 `GET` 与 `Authorization`。本地 CLI 在 HTTP 可省略 Origin，WS 回放显式传与测试配置一致的 Origin。忽略转发头。令牌由本机启动器生成并保存在仅当前用户可读的文件，扩展 Options 由用户录入，存在仅受信任扩展上下文可读的本地存储。浏览器把令牌放进首条 `start`，上游连接使用各自的服务端认证。日志与故障响应只包含错误码、计数和耗时。[Chrome 扩展存储访问级别](https://developer.chrome.com/docs/extensions/reference/api/storage#type-AccessLevel) · [WebSocket Origin](https://www.rfc-editor.org/rfc/rfc6455.html#section-10.2)

### 从连接到结束

1. 客户端连接后 2 秒内发一条 `start`。服务验证 token、版本、配置和格式。服务全局最多一个活动或清理中的会话。可用时初始化所选策略，再发 `ready`。客户端等 `ready` 最多 20 秒，等待时保留原声播放并跳过尚未开始送入的 PCM。
2. 客户端从 `ready` 后的第一帧开始计时。每 200 ms 发 3,200 个单声道 s16le 采样，即 6,400 字节。静音照常送入以保持音频时钟。正常末尾可发一帧 2–6,398 字节的偶数长度 PCM，随后只允许 `finish`。空帧、过长帧、奇数字节或短帧后继续送音频视为协议错误。
3. 正常结束发 `finish`。服务停止接收新音频，处理所有已接纳帧，依次 flush VAD、ASR、MT，然后发 `finished`，关闭码 1000。单会话 drain 最多 8 秒。用户停止、暂停、拖动或断连时客户端关闭 WS 并取消，服务停止提交新字幕，释放队列及引擎会话。

以下为同一连接的消息示例。ID 与令牌都是示例值。

```jsonl
{"type":"start","version":1,"profile_id":"reazon-hy","token":"<token>","audio":{"format":"s16le","sample_rate":16000,"channels":1}}
{"type":"ready","version":1,"session_id":"s-1","profile_id":"reazon-hy","language":"zh-Hans"}
{"type":"caption","session_id":"s-1","seq":1,"start_sample":63520,"end_sample":87040,"text_zh":"我这边有照片。"}
{"type":"finish"}
{"type":"finished","session_id":"s-1","accepted_samples":96000}
```

| 字段或事件 | 语义 |
| --- | --- |
| `session_id` | 服务生成，绑定到当前连接。通过 `ready` 建立关联，之后的服务端事件携带同一值。 |
| `seq` | 字幕从 1 开始逐条递增，与 `session_id` 一起唯一标识字幕。每条字幕只发一次。渲染器忽略已显示序号及失效会话的消息。 |
| `start_sample`、`end_sample` | 该段在会话 PCM 里的半开范围。首个接纳样本位置为 0，满足 `0 ≤ start_sample < end_sample ≤ 已接纳样本数`。提交的 `end_sample` 非递减。适配器可报告近似范围，多个字幕可共享相同末点。 |
| `text_zh` | 完成翻译的简体中文，允许中文句中的拉丁字母、数字、标点和专名。ASR 日文及未完成的译文留在服务内部。 |
| `warning` | 可继续播放。例如 `{"type":"warning","session_id":"s-1","code":"translation_invalid"}`。无效译文和无法安全对齐的上游快照记为丢失，仍处理后续音频。 |
| `error` | 终止连接。例如 `{"type":"error","code":"profile_unavailable"}`。若已发 `ready`，事件包含 `session_id`。 |

错误码分组用于浏览器状态与契约测试。

| 错误 | 行为 |
| --- | --- |
| 错误 Host 或 Origin | 升级前拒绝。 |
| `unauthorized` | 关闭 4401。 |
| `invalid_message`、`unsupported_version`、`invalid_audio`、`unknown_profile` | 关闭 4400。 |
| `profile_unavailable`、`backend_error`、`drain_timeout` | 关闭 1011。`profile_unavailable` 携带 `reason`，取 `disabled`、`not_ready` 或 `upstream_unreachable`。 |
| `busy`、`overloaded` | 关闭 1013。 |

浏览器 WS `bufferedAmount` 超过 64,000 字节即取消，显示处理跟不上。服务最多排队 64,000 个待处理样本，等待入队最多 1 秒。默认本地策略最多排队 4 个待译段，另有最多一个翻译在途。满队列先结束该会话并报告 `overloaded`。断连和取消要能绕过满队列唤醒等待者。后台同步推理尚未返回时保留会话占用，丢弃它的晚到输出，释放引擎后才接纳下一会话。

## 播放生命周期

- 第一次点击在被授权标签页开启采音，固定本次启动时选择的 profile。Offscreen 先接通原声支路，再按播放器状态启动 WS。标签页音频为混合音源。暂停和跳转时保留原声支路与采音授权，关闭旧 WS、增加 `epoch`、丢弃旧 Worklet 尾帧、清空字幕。
- `pause` 或 `seeking` 使会话失效，自然结束走下一条。视频满足已连接、`paused=false`、`seeking=false`、`readyState ≥ 3` 时，在 `playing` 或 `seeked` 后从当前位置建新 WS。连续触发只启动最新一代。切换媒体源和 SPA 更换主视频采用同一规则。播放中的自然静音照常送入 VAD。
- 自然结束时浏览器可能先发 `pause` 再发 `ended`。`pause` 处理先读 `video.ended`，与 `ended` 共用幂等的收尾分支。Worklet 停止本次供流，送出尚未提交的尾帧，再发 `finish`。收到尾字幕后按 4 秒计时清空，保持扩展开启以处理重播或下一视频。用户停止、导航离开、标签页关闭、轨道结束和异常断线立即失效并释放音轨、WS、Worklet 及浮层。需要重新授权的操作由下一次用户点击触发。[HTML 媒体播放事件顺序](https://html.spec.whatwg.org/multipage/media.html#playing-the-media-resource)
- 每个异步启动步骤返回后再次检查 `epoch`。收到旧会话结果、旧播放器事件或旧计时器时丢弃。主动关闭旧 WS 后保留本次采音。自动重建若遇到自己的上一会话还在清理，遇 `busy` 后延迟 500 ms 重试一次，仍失败就提示。原声回放只接一条支路，Worklet 的采音输出保持静音。Content Script 只接收已验证的中文、清空和状态事件。

## 后端策略

`profile_id` 只选服务端已声明的配置。浏览器端只保存 ID，设置更改在下一次工具栏重新启动时生效，暂停和跳转仍沿用本次启动的 profile。缺少所选配置时提示重新选择。只有从未选择过时使用服务默认值。配置文件修改后重启服务。会话中切换和失败后自动改用另一种引擎留到有明确需求时处理。

服务端在一个类型工厂里根据 `type` 构造策略。每个类型有独立配置校验。所有策略共用以下生命周期，示意中的类型名由实现补齐。

```python
class SubtitleBackend(Protocol):
    async def open(self, emit: CaptionSink) -> SubtitleSession: ...

class SubtitleSession(Protocol):
    async def accept_pcm(self, frame: bytes) -> None: ...
    async def finish(self) -> None: ...
    async def aclose(self) -> None: ...
```

- `open` 只建立会话状态。默认模型在服务启动时加载、预热，后续会话复用权重。网关持有受限音频队列，一个消费者顺序调用 `accept_pcm`。后端可在内部并行推理与接收，并通过异步 `emit` 提交中文及样本范围。网关独占 WebSocket 写入、编号、认证和跨会话检查。
- `finish` 在最后一次 `accept_pcm` 返回后调用，返回前提交所有完成的字幕。`aclose` 可重复调用，负责取消和关闭引擎资源。取消时先关闭 sink。服务等待同步推理 worker 安全退出或其当前调用安全返回后再释放会话占用。
- `local_pipeline` 组合 ASR 和 MT。ASR 接收 PCM 并提交 `RecognizedSegment(start_sample, end_sample, text_ja)`，自带 VAD 或原生端点。MT 接收日文段，固定目标 `zh-Hans`，输出中文文本。默认 Hy-MT2 adapter 把 `zh-Hans` 映射为 `zh-cn`，使用已锁定版本的 `MlxLlmTranslation`。每会话用 `new_session`，私有的 `_translate_text` 只在一个 adapter 调用并加回归测试。ASR 与 MT 分别在受限的 worker 执行，避免同步推理阻塞 WS 事件循环。[现有 ReazonSpeech 与 Hy-MT2 验证](p0-asr-selection.md) · [Hy-MT2 调用源码](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/translation_mlx_llm_mt.py)
- 已内置翻译的外部后端实现 `SubtitleBackend`，在 adapter 内固定目标简中。它直接提交其中文字段，按相同协议上送。内部 ASR/MT 策略只用于可拆开的后端。
- 统一输出入口 trim 文本并拒绝空白、占位符残留、无汉字或带平假名/片假名的译文，发 `translation_invalid` 并计入漏错。输出只能来自已配置的翻译步骤。汉字检查只拦截明显异常，中文语义和简繁以模型目标、中文测试样例与逐句验收为准。合法拉丁专名可保留。发生错误时显示状态，字幕字段始终只承载译文。短测中曾计为可用的「ショー将解释越位规则」会在 v1 被过滤，D2 与 D5 以 v1 实际输出重算。

### 配置示例

配置按 TOML 的 `type` 区分。路径相对于配置文件，`cache_root` 指向现有模型缓存。模型 revision、VAD 文件路径和校验值从实测报告锁定。服务令牌路径由 `SUBTITLE_TOKEN_FILE` 传入，外部服务令牌由相应的环境变量传入。

```toml
schema_version = 1
default_profile_id = "reazon-hy"
cache_root = "../../.model-cache"

[server]
port = 18765
allowed_origin = "chrome-extension://<extension-id>"

[profiles.reazon-hy]
label = "ReazonSpeech · 本地简中"
type = "local_pipeline"
enabled = true

[profiles.reazon-hy.asr]
type = "reazon_sherpa"
model_revision = "291488c8151be24d7da4bf7af26e533fad96e407"
threads = 2
vad_pause_ms = 600

[profiles.reazon-hy.mt]
type = "hy_mt2_mlx"
model_revision = "e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912"

[profiles.whisper-small]
label = "WhisperLiveKit · small 对照"
type = "whisperlivekit_ws"
enabled = false
url = "ws://127.0.0.1:18766/asr"
token_env = "WLK_API_TOKEN"
```

启动时拒绝未知类型、类型不对应的字段、无效来源、未启用的默认配置和缺失的默认模型。外部服务 URL 首版只接受 `ws://127.0.0.1:<独立端口>/asr`。选择只传 profile ID，网页和浏览器均无权指定外部 URL、模型路径和语言参数。

## 新增后端

1. 若只替换本地识别或翻译，新增对应 ASR 或 MT 策略及类型配置。若要接入整体服务，在后端 adapter 中实现 `open`、`accept_pcm`、`finish`、`aclose`。为它配置固定简中目标，把时间戳归一到本会话的 PCM 采样位置。
2. 在服务端类型工厂注册该实现并增加一个 profile。用同一套契约 fixtures 测试鉴权、状态、正常 drain、取消、乱序晚到及翻译字段。以真实原速音频和静音验收后再启用该 profile。
3. 核对前端差异为零。只有后端确实需要新增用户可见能力时再设计协议版本。

### WhisperLiveKit 适配

服务端连接固定 revision 的 WhisperLiveKit，使用 `/asr?mode=full&language=ja&target_language=zh-cn` 和 Bearer header，确认上游 `config.useAudioWorklet=true` 再向浏览器发 `ready`。向上游转发 PCM，收到 `finish` 后向上游送空二进制帧，等待 `ready_to_stop`。上游错误、翻译失败、异常断连和缺失 EOF 映射到 v1 的服务端错误。启动对照时固定 `--retention-seconds 30`，默认外部端口 18766。[固定版 WS 实现](https://github.com/QuentinFuxa/WhisperLiveKit/blob/13ee8515f136e0fd0bf31fb8ab0b63ab10670fa2/whisperlivekit/basic_server.py)

只读取 `lines[*].translation`，跳过静音行。把 `H:MM:SS.cc` 解析成样本位置，约束在已接纳的范围内。按时间顺序比较上一份可对齐的译文和当前快照。首次发有效翻译。以后只提交能确认的新追加部分，按以下次序判断。

1. 文本相同或只是删掉前部历史时保持静默。
2. 有唯一的旧尾部与新前部重叠，并且行末时码一致或前进时，剥掉已提交部分，输出新增中文。按所属行范围映射新增内容。
3. 如果整批新行都在已提交末点之后，视为新批次提交。
4. 无法对齐或已有译文被改写时发 `adapter_unaligned`，保留最后可确认的进度，等待下一个可对齐快照。对端滚动裁剪会改变行的 `start`，该值只能作为对齐线索。

至少用已录的快照覆盖前缀增长、裁剪后重复、迟到的旧行、两段相同台词但时间不同、回写旧译文和多行增长。确保每条已确认译文只发一次，且旧范围无法覆盖当前字幕。若仍有歧义，将该 profile 保持为实验配置并报告丢失，后续可用上游稳定句段事件替换适配逻辑。[原 P0 的快照与裁剪证据](p0.md#计分)
