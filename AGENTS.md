# Repository Engineering Rules

## 1. 字幕 Cue 生命周期与终态不变量
- 流式 `caption_delta` 可以先发，但每个对外可见 cue 必须绑定唯一递增 `seq` 与 `session_id`/epoch，且有且仅有一个终态：合法 EOS `caption`，或显式 `caption_discard`。
- `length_limit`、`cancelled`、`valid=False` 或 repair 未见真实 EOS 时，若此前已下发流式内容，禁止静默丢弃而不撤销、禁止跨句复用同一 `seq`、禁止仅凭返回非空字符串将 `stop_reason` 改写为 `"eos"`。

## 2. 异步取消隔离与降级契约
- 禁止使用会话级可被下一任务 `.clear()` 的共享取消事件；每次 draft 必须持有不可复用的独立 `threading.Event` 与单调递增的 `draft_gen`，并在提交前核对生成代与队列水位。
- 所有降级与边界路径（GPU/CPU fallback、时间戳去重、WebSocket 背压等待）必须严格区分“过滤后合法为空”与“无可用数据/时间戳”，并保持与主路径一致的超时与协议语义。

## 3. 评测与门禁零假绿原则
- 聚合器与验收门禁中，缺测字段、零视频帧、`manifest` 缺失、重复 `baseline_role` 或样本不足一律保留 `None` 并返回 `BLOCKED` / `FAIL` 或抛错，严禁 `.get(k, 0)` 默认放行 `PASS`；trace 耗时统计必须按 `session_id` 隔离。
- 标准指标必须对齐权威公式并锁定短不等长黄金向量；离线评测须同时报告 `diagnostic_raw`、`valid_only` 与固定分母 `delivered` 三层口径，严禁过滤失败或空输入单元来虚增分数或压低延迟。

## 4. 测试真实性与提交前独立对抗评审
- 端到端与并发边界测试必须打通真实中间回调（如 `on_delta`）及真实 WebSocket / 前端状态机，禁止使用跳过关键副作用的瘦 mock 制造假绿。
- 源码与测试禁止硬编码本机绝对路径（一律使用基于 `Path(__file__).resolve()` 的相对路径或显式参数）。
- 非 trivial 改动汇报或提交前，须由独立只读 subagent 按上述红线完成一轮对抗评审。
