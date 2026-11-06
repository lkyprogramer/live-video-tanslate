# Nemotron 3.5 本机短测与资源对照

2026-11-06，M1 Pro（10 核 CPU、32 GiB 统一内存）、macOS 26.7.2（25G332）、接电、低电量模式关闭。串行回放原 P0 的 CSJ 双人对话（40.562 秒）、带配乐的越位讲解（97.420 秒）和全零 PCM（10 秒）。每 200 ms 按 1× 输入 16 kHz 单声道 s16le PCM，运行器复核 SHA-256。五组共享同一 Hy-MT2 1.8B 4-bit 简中翻译配置和缓存权重 revision `e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912`。

Nemotron 使用多语 `mlx-community/nemotron-3.5-asr-streaming-0.6b` BF16，固定 `ja-JP` 和 `[56, 6]`（560 ms 原生块）。使用 mlx-audio 0.6.4（`2d7700c299e66b93e28ed211fc6e4922d6b8e238`）和 MLX 0.32.4 独立环境。通过 `create_streaming_session` 在新音频到达时增量解码。日文终止标点、1.2 秒没有新词或 EOF 会触发翻译。Apple 重跑默认定稿与按句读提前定稿两组。WhisperLiveKit 重跑原 P0 的 `small` 和 `large-v3-turbo`，沿用 SimulStreaming、VAD、模型与队列设置。[NVIDIA 模型卡](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) · [MLX 权重](https://huggingface.co/mlx-community/nemotron-3.5-asr-streaming-0.6b/tree/e550040c0478027ed679b2b6b0d055502c103663) · [固定版流式接口](https://github.com/Neywa-Labs/mlx-audio/blob/2d7700c299e66b93e28ed211fc6e4922d6b8e238/mlx_audio/stt/models/nemotron_asr/session.py) · [Apple 提前定稿策略](p0-apple.md)

Nemotron 两段均完整，音频结束后约 0.34–0.42 秒完成整条链路。对话第一条非空日文在 4.84 秒出现，第一段直到 11.49 秒才提交翻译。原句「名字和一句话」识别成了「名前時言」，中文漏掉“一句话”。越位讲解首条将「オフサイド」识别成「オスタイド」，中文输出“Ostad”；体育场“观赛”也被识别成同音的“感染”。讲解第 36–66 秒连续输出无句末标点，累计成一条长译文。末尾单独出现了一个句号，也触发了一次翻译。静音对照没有产生 ASR 或 MT 事件。对话逐条输出（本地 `p0/evidence/nemotron-comparison/nemotron/csj-task.zh.jsonl`） · 讲解逐条输出（本地 `p0/evidence/nemotron-comparison/nemotron/offsiderule.zh.jsonl`）

## 字幕结果

| 引擎 | 首条中文 对话 / 讲解 | 对话 P95（命中数） | 对话漏译或错译 | 讲解 P95（命中数，估算） | 讲解漏译或错译（估算） | 静音 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Nemotron | 12.30 / 9.16 秒 | 8.20 秒（2） | 6/8 | 6.14 秒（2） | 10/12 | 无字幕 |
| Apple 默认定稿 | 13.19 / 16.22 秒 | 8.14 秒（3） | 5/8 | 8.75 秒（8） | 4/12 | 「啊」 |
| Apple 提前定稿 | 5.35 / 9.21 秒 | 0.44 秒（2） | 6/8 | 8.63 秒（5） | 7/12 | 「啊」 |
| Whisper small | 2.63 / 8.70 秒 | 2.01 秒（3） | 5/8 | 4.45 秒（2） | 10/12 | 无字幕 |
| Whisper large-v3-turbo | 4.41 / 7.17 秒 | 5.86 秒（4） | 4/8 | 4.96 秒（1） | 11/12 | 无字幕 |

对话 5 秒内可用译文依次为 0/8、1/8、2/8、3/8、1/8。讲解为 1/12、4/12、4/12、2/12、1/12。Nemotron 在讲解的首条可用参考译文出现在 37.58 秒，先前的 9.16 秒只是首条中文输出。按单元评审时，同一译文可以覆盖多个独立单元，单元外的错误另作观察。对话沿用原 P0 对「有很多不知道的人」的判定。

Nemotron、Apple 两组和 Whisper small 完整跑完两段。Whisper turbo 在两段中均报积压中断，讲解跑到约 28 秒。Whisper small 在上一次 P0 的同一讲解曾于约 47 秒中断，本轮完成；本轮送帧最大迟到 14.71 ms。当前 Nemotron 的吞吐和静音结果通过这轮短测，字幕覆盖未达到 90% 门槛，暂不选为插件主引擎。当前测试覆盖两个短片段和静音，完整 P0、长时间播放及 Chrome 上屏待验收。逐句判定（本地 `p0/evidence/nemotron-comparison/metrics.json`） · 回放记录（本地 `p0/evidence/nemotron-comparison/driver.log`）

## 计量口径

- 首条中文从播放起点到首条含汉字且无假名的新增译文。可包含错译，另用固定参考逐条判定。Whisper 以本机 WS 收到结果为终点，Apple 和 Nemotron 以同一翻译函数返回为终点。均包含切句和翻译排队，排除模型加载、预热和 Chrome 渲染。Nemotron 每段独立预热 ASR，再创建新的流式会话。
- 人工语义配对要求关键含义正确。句末 P95 使用 15 秒内首次可用的配对，采用 nearest rank，提前出现按 0 秒计。15 秒漏译或错译包含中断未处理的后续句子，另外列出 5 秒覆盖。CSJ 使用官方人工转记。越位视频没有原始字幕，使用本机全 MLX 离线 Whisper large-v3-turbo 的字词时间戳估计句末，再人工归纳 12 个核心语义单元，片尾配乐上的疑似重复词不计入参考。离线模型与其中一组实时 Whisper 同源，讲解结果只作辅助，两段分开计分。所有回放只输入 PCM。[CSJ 语料](https://clrd.ninjal.ac.jp/csj/sample.html) · [越位视频及授权，Simpleshow Japan，CC BY-SA 4.0](https://commons.wikimedia.org/wiki/File:Offsiderule.webm) · 离线时间戳（本地 `p0/evidence/nemotron-comparison/offsiderule-offline-timing.json`）
- 每 250 ms 采样进程树和整机，启动前采 16 点作空闲参考。进程 CPU 用 `proc_pid_rusage` 的用户态和内核态时间差，按 `mach_timebase_info` 换算，100% 表示占满一个逻辑核。进程内存是各进程 `phys_footprint` 之和。按片段汇总从播放准备完成到返回 EOF 或中断的区间，另保留含预热的整轮峰值。
- GPU 使用率和内存来自 `AGXAccelerator` 的 `Device Utilization %` 与 `In use system memory`，属于整机采样。GPU 内存增量用播放期中位数减启动前中位数，桌面和后台负载仍可能影响结果。Apple Silicon 的 GPU 使用统一内存。进程 footprint 与 GPU 内存有重叠。Apple 的 `localspeechrecognition.xpc` 由系统启动，单独列出可采数据，常规进程树不包含它。ANE 负载和逐进程 GPU 使用率未测。[Apple CPU 时间换算 API](https://developer.apple.com/documentation/driverkit/mach_timebase_info-c.func)
- 第一轮 Whisper 对照遇到合盖睡眠，送帧迟到和 GPU 查询超时使该轮样本失效。原始记录保存在 `interrupted-sleep/`，正式对照采用恢复接电后的重跑。采样器将单次 GPU 查询失败标为缺失，保留 CPU 和进程内存，输出有效 GPU 点数。

## 复现和后续采样

复用本机已有的 P0 环境。Nemotron 的独立环境由 `uv venv --python .venv/bin/python .model-cache/nemotron-venv` 创建，再用 `uv pip install --python .model-cache/nemotron-venv/bin/python .model-cache/mlx-audio` 安装上述固定 checkout。权重由 `huggingface_hub.snapshot_download` 下载到 `.model-cache`，revision 在 `p0/nemotron.py` 锁定。正式回放使用缓存和离线 Hub 模式。

```sh
evidence=p0/evidence/repeat-nemotron
mkdir "$evidence"
/usr/bin/caffeinate -i .venv/bin/python p0/monitor.py "$evidence/nemotron-resources" \
  .venv/bin/python -u p0/run_apple.py p0/nemotron.py "$evidence/nemotron" \
  csj-task offsiderule silence --asr-python .model-cache/nemotron-venv/bin/python
.venv/bin/python p0/monitor.py "$evidence/nemotron-resources"
```

第一条监控命令用一个新目录保存原始 `samples.jsonl`、片段边界 `phases.jsonl` 和分段 `resources.json`。第二条监控命令从保存的采样重新计算统计。Apple 传 Swift 可执行文件，提前定稿组加 `--sentence-finals`。WhisperLiveKit 使用 `p0/run.py <model> <output> --only csj-task offsiderule silence` 作为同一监控器的子命令，回放中断可返回 1，原始结果和资源采样仍保留。

```sh
evidence=p0/evidence/nemotron-comparison
.venv/bin/python p0/review_short.py "$evidence" --review "$evidence/review.json"
```

## 带配乐片段的资源对照

| 引擎 | 观察区间 | 进程 CPU 均值 / P95 | 进程统一内存峰值 | 整机 GPU 均值（启动前） | GPU 内存中位增量 / 整机峰值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Nemotron | 97.76 秒 | 6.2% / 41.3% | 3.89 GiB | 48.6%（3.6%） | +2.39 / 5.27 GiB |
| Apple 默认定稿 | 97.51 秒 | 3.2% / 17.0% | 1.43 GiB | 15.3%（4.6%） | +0.08 / 2.65 GiB |
| Apple 提前定稿 | 97.66 秒 | 3.5% / 34.4% | 1.46 GiB | 15.6%（7.9%） | +0.05 / 2.61 GiB |
| Whisper small | 98.12 秒 | 181.1% / 338.0% | 7.00 GiB | 64.4%（9.7%） | +2.91 / 6.34 GiB |
| Whisper large-v3-turbo | 28.01 秒，中断 | 58.4% / 200.2% | 6.29 GiB | 80.2%（18.9%） | +4.40 / 6.39 GiB |

同一前 15 秒中，Nemotron、Apple 默认、Apple 提前定稿、Whisper small、Whisper turbo 的进程 CPU 均值分别为 5.0%、0.7%、1.9%、82.4%、45.1%，整机 GPU 均值分别为 47.7%、8.4%、13.3%、65.4%、73.3%。Apple 提前定稿的系统语音进程在全片另占平均 1.84% CPU，footprint 峰值 30.42 MiB。默认组的系统进程没有单列采样。Nemotron ASR 的 MLX 活跃分配峰值为 2.01 GiB，包含 ASR 预热，进程树统一内存已包含 ASR 和翻译。共同区间采样（本地 `p0/evidence/nemotron-comparison/resource-common-prefix.json`） · Nemotron 分段资源（本地 `p0/evidence/nemotron-comparison/nemotron-resources/resources.json`）
