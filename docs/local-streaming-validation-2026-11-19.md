# 本地流式架构验证记录（2026-11-19）

A0 通过。A1a 的两轮离线因果模拟都在同一短片发生 EOF 识别回退，未通过等价性关卡。按[架构方案](local-streaming-architecture-plan-2026-11-12.md)的第 9、12 节停止依赖阶段。变更范围限于独立 worktree 内的 `p0/local_streaming/` 实验脚本和本报告，基点为 `29cd11a`。

## A0 基线和能力

| 检查 | seed 11 | seed 29 |
| --- | ---: | ---: |
| B0-new 实际 1× PCM | 9 段，2,382 包 | 9 段，2,382 包 |
| ASR 和评分用 MT 精确一致 | 88/88、62/62 | 88/88、62/62 |
| 正确语义项，沿用冻结文本评审 | 20/34 | 18/34 |
| 5 秒内正确 WS，原句末时标 | 19/34 | 17/34 |
| 5 秒内正确 WS，争议项按 126.04 秒计 | 20/34 | 18/34 |
| 服务回放 CPU 均值 / P95 | 12.63% / 91.45% | 11.45% / 89.60% |
| 服务回放峰值 physical footprint | 1,833.38 MiB | 1,869.08 MiB |

每轮 7,616,928 个样本。发送、接收、接纳和消费计数一致，所有线上 PCM 包哈希匹配，静音均为 0 条字幕。时序属于 1× WS 回放。34 项仍为冻结文本评审口径，15 项待复听，`wmf-04/153-155` 的 119.60 和 126.04 秒时标并列。

Silero 的 88 次释放中，普通停顿 26 次、长度管理 57 次、EOF 5 次。依据为释放末点与已处理样本末点的差，分别匹配原生实现的 600 ms 与 100 ms 回退量，另以调用 `flush` 识别 EOF。相邻释放区间无样本重叠。[sherpa-onnx v2.0.1 检测器实现](https://github.com/k2-fsa/sherpa-onnx/blob/v2.0.1/sherpa-onnx/csrc/voice-activity-detector.cc)

真实 Reazon 解码的 4、8、12 秒窗口 P95 分别为 86.9、173.5、239.0 ms，每档 12 次。508 个滚动快照中 335 个出现了稳定前缀，句末标点为 0。稳定前缀属于探索信号，未获得可确认提前 500 ms 的完整必要日文。A0 逐项对照把 16 个至少一轮未正确的语义项初步归为 MT 4、边界附近 2、片内 ASR 6、跨切片 3、待定位 1。

A1a 采用独立 VAD 工作线程。候选揭示前冻结服务进程树 CPU 均值 70%、P95 220% 的预筛上限。实时资源和真实 drain 预算由候选 1× 实测判定。

## A1a 等价性

| 检查 | seed 11 | seed 29 |
| --- | ---: | ---: |
| 9 段全部结束且覆盖所有接纳样本 | 通过 | 通过 |
| ASR 真实窗口哈希与解码水位检查 | 613 次通过 | 614 次通过 |
| 88 条源文逐条一致 | 87/88 | 87/88 |
| 主评分集合 62 条 MT 文本逐条一致 | 62/62 | 62/62 |
| 累计重解码音频 | 2,217.348 秒 | 2,219.394 秒 |
| 最长模拟 drain | 454 ms | 404 ms |

两轮累计解码音频均为 B0 的约 5.9 倍。耗时按逐次真实模型调用驱动虚拟事件，最长 drain 只代表本次模拟。另有 8 个合成测试通过，覆盖同前缀不同未来、在途任务跨 tick、短尾与 EOF 顺序、超时、取消后丢弃晚到结果、长静音、新旧活动积压及重叠重复词。

两轮差异均来自 `first-photo/2`。B0 的 EOF VAD 释放结束于样本 111,616。A1a 以同一起点把已接纳的全部 PCM 解码到样本 112,000，多出的 384 样本约为 24 ms。B0 识别保留了姓名和内容相关信息，扩展输入后只剩「はい」。直接调用同一 Reazon 复现两次，再固定补 128 个零样本至下一个 Silero 块边界，仍出现回退。另两处 EOF 短尾在三种对照下均复现 B0 文本。实测将问题定位到 EOF 输入长度变化时的 Reazon 输出，内部模型机制尚未定位。

**阶段裁决** A1a 未通过。已确认的内容丢失阻止 A1a 进入受限真实 1×，因此候选 CPU、真实 drain 和网关 lease 回收仍未验收。A1b 与 A2 的前置未通过。A3 为可选，A4 的正式材料、默认采用、生命周期和 60 分钟长稳均未进入验收。

## D1 采音与显示对照

《重启人生》第 1 集的视频与 SRT 哈希与登记一致。用 FFmpeg 6.1.1 从日语 AAC 音轨生成 43,552,768 样本的整集控制 PCM。PCM 起点对应媒体 0 秒。用原 H.264/AAC 重封装 45.078 秒 1080p 摘录用于 Chrome 对照。

两次有效的 `tabCapture → Worklet → WS` 运行各接收 226 包，包哈希和网关收、接纳、消费计数相符。四个音频对齐锚点的归一化相关性为 0.973–0.996，两次采音分别映射到控制 PCM 的第 467 和 305 样本。共同区间未见削波。第一次有效采音与控制 PCM 对齐后，共用 719,533 个样本交给当前后端按 1× 重放，分别产生 6 条和 7 条字幕。相同 ASR 起止样本的一个语音段也产生了不同文本，说明差异至少包含输入波形对识别的影响。压缩封装、浏览器重采样和 VAD 各自的贡献保持待定位。

首次 Chrome 启动因独立 worktree 的 Python 路径失败，随后修正诊断入口。第一次有效跑完整链路时，1920×1080 测试元素超出视口，没有测到 DOM 可见字幕。最后一次保持 1080p 视频解码，缩放测试页面的视频元素至视口内。6 条 WS 字幕均在 DOM 连续可见，6 条达到文字停留时长要求。前后各 10 次校时，最大不确定度 1.90 ms，偏移变化 0.12 ms。Chrome 和服务进程树在该短片播放阶段的峰值 physical footprint 为 2,141.65 MiB。

D1 为工程对照。人工语义评分、语音句末校准、候选对照和正式保留集均为未运行。Chrome 原始事件、失败记录和对齐结果保存在 `D1/`，最终显示检查见 `D1/fit/dom-summary.json`。

### 对白密集片段补测

按日文 SRT 先冻结第 1 集 04:35.676–05:20.676 的 45 秒片段。选段保留至少两个说话人标签、对白字幕覆盖至少 31 秒的源视频关键帧窗口，再按对白条数和覆盖时长排序。选中段有 19 条对白、3 个说话人标签，字幕覆盖 43.797 秒。片头的同口径覆盖为 30.253 秒。SRT 仅用于离线选段。Chrome 播放重封装后的 H.264/AAC，媒体时长 45.111 秒。

真实 1080p 回放中，225 个 PCM 包的哈希和网关计数对账通过。四个波形对齐锚点的相关系数为 0.994–0.997，共同区间为 718,501 样本，两路均无削波。6 条 WS 字幕均达到 DOM 连续可读时长。播放阶段的监测进程树峰值 physical footprint 为 2,177.57 MiB。

使用同一后端实速回放共同区间，控制 PCM 和 Chrome PCM 分别产生 4 和 6 个 ASR 切段及字幕。只有 1 个 ASR 切段的范围和文本完全一致。已观察到采音条件下的切分及识别差异，人工语义质量未评分。

选段、重封装、`node --check <dialogue>/browser-driver.mjs`、Chrome 采集、PCM 对齐、两路 1× 回放和证据复算均通过。具体命令和退出码见 `D1/dialogue/commands.jsonl`、`D1/dialogue/run-commands.log`，汇总见 `D1/dialogue/summary.json`。

## 复现与证据

在独立 worktree 根目录执行。命令所用 Python 为已有 `engine/.venv/bin/python`，`PYTHONPATH` 显式指向该 worktree 的 `engine/src` 与仓库根目录。模型及运行时固定为本机已缓存版本，推理时启用离线环境变量。

| 命令主体 | 结果 |
| --- | --- |
| `python -m p0.p2.cli --run-dir <A0> freeze`、`preflight --experiment B0` | 通过 |
| `python -m p0.p2.cli --run-dir <A0> probe --experiment B0 --mode fixed-cuts` 和 `--mode fixed-text` | 通过 |
| `python -m p0.p2.cli --run-dir <A0> score --experiment B0` | 通过 |
| `python -m p0.p2.cli --run-dir <A0> replay --experiment B0 --repeat 1` 和 `--repeat 2`，外层用 `p0.p2.resources` | 通过 |
| `python -m p0.p2.cli --run-dir <A0> report` 和 `python -m p0.local_streaming.a0 <A0> <capability>` | 通过 |
| `python -m pytest -q p0/local_streaming/test_rolling.py` | 8 项通过 |
| `python -m pytest -q engine/tests/test_gateway.py p0/local_streaming/test_rolling.py` | 23 项通过 |
| `python -m p0.local_streaming.run_a1 <A0> <out> --seed 29` 和 `--seed 11` | 两次均退出 2，保留失败产物 |
| `python <A1a>/diagnose_eof.py <A0> <out-json>` 和 `python <A1a>/audit.py <run>` | 通过，复现失败并验证两轮输入与水位 |
| `python <D1>/prepare.py`、`zsh <D1>/run-browser.zsh`、`zsh <D1>/fit/run-fit.zsh` | 素材与最终 Chrome 采音通过，保留两次中间问题记录 |

原始证据位于 worktree 的 `p0/evidence/local-streaming-20261119/`。目录保留命令、退出码、原始 PCM、配置与模型哈希、来源快照和逐项诊断。A0 看 `A0/A0-summary.json`，A1a 看 `A1a/gate-decision.json`。
