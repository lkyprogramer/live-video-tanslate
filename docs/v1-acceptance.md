# v1 本机验收记录

运行环境：Apple M1 Pro、32 GiB 统一内存，Chrome 157.0.8103.38。安装与启动见[本机说明](local-setup.md)。阶段规则见[开发方案](development-plan.md)和[v1 合同](subtitle-backend-contract.md)。

| 阶段 | 本机证据 |
| --- | --- |
| D1 本地接口 | Python 定向测试验证 loopback、Host/Origin、令牌、PCM 帧、独占、队列上限、尾帧、取消。真实 Uvicorn WebSocket 复现并验证了旧会话清理前拒绝第二条连接、释放后接受新会话。 |
| D2 默认模型 | 1× 双人对话、带配乐的越位讲解和 10 秒静音，实际跑过 ReazonSpeech、Silero VAD 600 ms 和 Hy-MT2。分别收到 11、16、0 条中文事件，三段都收到 `finished`，服务接纳样本数与 PCM 一致。见 [D2 原始记录](../p0/evidence/v1-d2/replay/run.json)。 |
| D3 Chrome | 实际 Chrome 工具栏 action 触发采音和上屏。HTML5 样本显示「包含照片。」，暂停清空，连续拖动后恢复，容器全屏可见，原生视频全屏有提示。双标签接管、同页换片重开会话、1.5× 顺序、自然结束及重播、Worker 重启、服务断开及恢复、整页导航释放采音均有实测。[浏览器证据](../p0/evidence/v1-browser-final/result.json) · [截图](../p0/evidence/v1-browser-final/local-caption.png) |
| D4 后端切换 | 同一扩展、同一 v1 地址，在 Options 切换 `reazon-hy → whisper-small → reazon-hy`，最终复测三个实际会话分别上屏「包含照片。」「我的文件里包含照片。」「包含照片。」。上游 WhisperLiveKit 的 PCM、滚动快照与认证仅由服务适配器处理。见[切换记录](../p0/evidence/v1-browser-final/result.json)。 |

Chrome 运行时由一条 Web Audio 支路回送原声，另一条支路只供 Worklet 采样。自动化运行在独立 Chrome 测试配置中。复测在 [YouTube 日语听力视频](https://www.youtube.com/watch?v=sVROs8Mje3Y)实际显示了「早上好」，同轮本地 HTML5 样本启动到首条可用字幕为 6.70 秒。早期复测发现 Chrome 标签页更新事件会把同页换片误当成离页；修复后同页导航只重开字幕会话，整页导航才释放音轨。早期 Wikimedia 页面样本未等到指定字幕。原始记录保存在本地 `p0/evidence/v1-browser-lifecycle/result.json`。

## D5 观影质量

当前按原型交付。固定 ReazonSpeech int8 encoder/joiner、fp32 decoder、sherpa-onnx CPU 2 线程、Silero VAD 600 ms 和 Hy-MT2 1.8B MLX 4-bit。17 段共 29 分 45 秒，按 1×、200 ms PCM 供流，参考答案不进入服务。

| 指标 | 本机 WS 实测 |
| --- | ---: |
| 正常结束／积压中断／10 秒静音字幕 | 17/17 · 0 · 0 |
| 首条原始中文 P50／P95，16 段有声 | 4.97／13.54 秒 |
| 首条语义正确 P50／P95，有逐句标注的 9 段 | 18.00／77.92 秒 |
| 句末到可用中文 P50／P95，69 个成功配对 | 0.00／2.75 秒 |
| 5 秒内语义正确覆盖，门槛 90% | **54.5%（66/121）** |
| 15 秒内漏译、错译或过迟 | **43.0%（52/121）** |
| 四条 1–3 秒短句，门槛 P90 ≤ 4.5 秒 | 1/4 命中；照片句 2.545 秒，其他 3 条未命中 |
| 送帧最大迟到／EOF 最长排空 | 41.78 ms／0.39 秒 |
| 中文事件／相同范围且相同文本的重复／过滤告警 | 326／0／5 |

首条以片段播放起点为基准，含片头停顿；原始中文可能是语气词或错译。句末分位数只统计 15 秒内语义可用的配对。40 个单元在参考字幕末点前已输出，按 P0 规则将其延迟记为 0；这不等于零推理耗时。四条独立短句用标注的语音起点计时。

| 资源，250 ms 采样 | 空闲基线 | 全量回放 |
| --- | ---: | ---: |
| 服务与回放进程 CPU 平均／P95，100% = 一个逻辑核 | — | 8.46%／66.95% |
| 服务与回放进程物理内存占用峰值 | — | 1,889.96 MiB |
| 整机 GPU 平均／P95 | 22.19%／35% | 20.39%／84% |
| 整机 GPU 使用的统一内存中位／峰值 | 1,427／1,482 MiB | 1,461／3,226 MiB |

GPU 和它使用的系统内存是整机计数器，不能归因到单个模型；Apple Silicon 没有独立显存的逐进程读数。Chrome 最终复测在这轮资源采样结束后启动。60 分钟连续观影验收未运行，质量门槛已未通过。

语义错误分别定位到两个环节。识别端将「一句话」识成与毛发相关，将 2024 年识成 2004 年。翻译端把正确的日文「片假名」译成「卡塔卡纳」，把「副语言信息」译成「平行语言信息」。[逐句判定](../p0/evidence/v1-d5/verdict.json) · [语义复核说明](../p0/evidence/v1-d5/review/notes.md) · [完整指标](../p0/evidence/v1-d5/metrics.json) · [资源数据](../p0/evidence/v1-d5/resources/resources.json) · [原速运行记录](../p0/evidence/v1-d5/replay/run.json)
