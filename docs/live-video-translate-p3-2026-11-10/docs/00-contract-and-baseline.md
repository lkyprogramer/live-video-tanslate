# 00 目标 合同与已验证基线

## 交付性质与优先级

本包是2026年11月10日的P3执行规格，针对 live-video-tanslate 的M1 Pro本地日语音频到简体中文字幕。它替代旧P2包中“下一轮候选和待开发任务”的建议，不覆盖P2已测事实。远端核验基点为77e24c3e490a11721041a40ad4bd203f51f84039，提交时间北京时间2026年11月9日21:30:30。模型新结果均未运行；本包的辅助工具测试不代表M1验收。

优先顺序是：固定事实和证据底座 → R2T2低延迟稳定前缀 → 123M日语cache-aware RNNT → 最多一个次候选 → 最多两个完整链路验证。允许结论为“新路线运行不通”“无净收益”或“资源代价不合适”，不以找到赢家作为完成条件。

本轮只交付文档包；执行AI只有在用户另行授权实施后才能改仓库、下载模型或运行实验。未来实施默认可在独立worktree进行小范围实验代码、隔离环境和所选公开模型准备；不自动提交到main、push、发布、升级系统、修改生产默认或申请私有权重。

## 不可改变的产品合同

以下来自[项目v1合同](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/subtitle-backend-contract.md)。冲突时先报告，不静默放宽。

- 输入16 kHz单声道s16le；3200 samples即200 ms完整包，仅最后一包可短。网络包不等于ASR块或VAD窗口。
- 仅使用当前和过去已到达音频。原字幕、OCR、未来预读、云端ASR/MT、4090卸载均不进入本轮。
- 对外仅最终简体中文字幕；内部临时日文、情绪标记、翻译草稿不得上屏。日文和中文的不可回写分别验收。
- 每session seq递增且只发一次；结束样本非递减；0 <= start_sample < end_sample <=真实已接纳样本。生成的补零不增加水位。
- 全局至多一个活动或清理中的session；PCM待处理上限64000 samples，入队等待最多1秒；待译段最多4，另有一个在途MT。
- 浏览器bufferedAmount超过64000字节取消；正常drain最多8秒。暂停、seek、切源、取消后旧epoch不得显示。
- 保持127.0.0.1监听、Host/Origin/token校验和现有中文过滤器。禁止为测试关闭鉴权或上传私密音频。
- 模型、音频、带内容的原始日志只保留本地忽略目录；公开仓库只收去敏摘要、配置hash、判定与已授权代码。

目标机器为用户M1 Pro、32GiB。CPU/GPU核数、OS、供电、Chrome版本、显示器、视频分辨率、后台负载以preflight实际采集为准，不把其他机器宣传数字当作本机事实。

## B0 固定配置与分母

| 项目 | 固定起点 |
|---|---|
| ASR | ReazonSpeech k2 v2；INT8 encoder/joiner、FP32 decoder；CPU provider 2线程 |
| ASR revision | 291488c8151be24d7da4bf7af26e533fad96e407 |
| 运行时 | sherpa-onnx 2.0.1；现有engine/.venv与锁，不原地升级 |
| VAD | 现有Silero v4、600ms静音等待、4s最长语音、现有整包调用 |
| VAD SHA256 | 9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6 |
| MT | Hy-MT2 1.8B MLX 4bit，M0模板与现有采样策略 |
| MT revision | e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912 |
| D0资产 | 9段连续PCM，88个ASR切片，62条主MT输入，34个必要语义项 |
| 重复运行 | MT seed 11和29，不是68个独立样本 |
| 已有离线正确项 | 20/34与18/34 |
| 已有1倍速5秒覆盖 | 19/34与17/34，仅作为同口径复现参照 |
| 真值状态 | 15项待复听；现有AI暂判不能改成human_verified |
| 默认资源目标 | 热态完整进程树physical footprint峰值2.5GiB，ASR与MT共同测量 |

来源：[P2 B0](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/p2-b0-results-2026-11-08.md)。原速/离线/Chrome/旧评分版本分开列；如同轮基线漂移，先归因和重新冻结，不能选择历史较弱分数当对照。

B0记录ASR排队P95约0.247ms、MT排队约0.502ms，ASR计算约150ms、MT计算约449ms，VAD放行滞后约628ms。分项P95不能相加。现基线ASR在CPU、MT在GPU；没有证据把当前主要问题直接归因于GPU命令队列阻塞。正确来源日文直接送Hy仍有错误，ASR优化不保证中文同比改善。

## 已失败路线登记

| 已测内容 | 现行处置 |
|---|---|
| PLaMo0.8B/2B H0 | 均17/17，不能因日语专精宣传再开同配置 |
| SenseVoice INT8 | 8/9，接口清理后仍退化，不作为新候选 |
| whisper-ja760 CT2 | 16/15且CPU开销高，不重复 |
| Voxtral Realtime 960ms | 17/18，原生流已真实接入，不再称“尚未试真流式” |
| Gemma4官方双语AST | 6/8，音频塔实际运行，不归因于“没接音频” |
| Gemma4只中文 | G0语音0/3合规，正式34项未运行，不记为0/34 |
| Haas、Parakeet JA TDT、Moonshine日语Small、Nemotron、Apple SpeechAnalyzer、PengChengStarling | 既有结果保留；不同运行框架不自动获得新模型名额 |
| 真实扩边、补零、标点、上下文/术语、VAD重扫 | 无新错误机制证据不重开；不得用未来原文或字幕作提示 |

精确结果见11章来源。不同年代评分不拼成统一排行榜。

## 现有工具与清理状态

P2的p0/p2/cli.py、freeze.py、runner.py、realtime.py、events.py、scoring.py及B1至B4专用实现已存在。CLI虽然显示replay/accept，B1至B4候选分支仍抛unsupported；只有B0基线实时回放已实现。P3必须复用可用基础，不虚构所有候选已能实时接入。

最近清理保留了当前Reazon/Hy/engine环境和证据，删除了未采用候选模型与环境。[清理记录](https://github.com/lkyprogramer/live-video-tanslate/blob/77e24c3e490a11721041a40ad4bd203f51f84039/docs/local-runtime-cleanup-2026-11-09.md)不是恢复全部候选的授权；只恢复当前选定的一项。旧证据可以带hash引用，不能标成新推理。

版本发生变化时，记录repo-diff.md，区分无关文档变更、输入/评分变化和执行变化。不得为匹配旧结果改写历史冻结hash。
