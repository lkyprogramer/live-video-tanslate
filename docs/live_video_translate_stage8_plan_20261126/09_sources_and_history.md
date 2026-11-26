# 09 · 来源、版本与失败路线台账

检索/访问日期：2026-11-26（北京时间）。GitHub源码使用固定提交；动态模型页在执行时须再锁revision/filehash。公开README、模型卡、厂商bench与独立用户实测不是同等级证据。

## 1. 本仓库一手证据

以下repo路径均相对 `lkyprogramer/live-video-tanslate`：

| ID | 固定来源 | 用途 |
| --- | --- | --- |
| R01 | [Stage7提交](https://github.com/lkyprogramer/live-video-tanslate/commit/7ea13865c8c3673a1463495058a200723b153111) | 确认已推送及本次审计锚点 |
| R02 | [Stage7报告](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/docs/stage7-p0-p1-p2-optimization-report-2026-11-26.md) | 作者总结；Oracle .2603仅有一句，不能当完整receipt |
| R03 | [Stage7 JSON](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/docs/experiments/stage7-p0-p1-p2-2026-11-26/stage7_verification.json) | Parts A/B/C指标与样本数量 |
| R04 | [Stage7验证脚本](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/p0/perf_probe/verify_stage7_p0_p1_p2.py) | 入样、legacy chrF、first delta edit、模板检查的真实实现 |
| R05 | [Stage6全片报告](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/docs/stage6-ep01-full-episode-verification-report-2026-11-26.md) | Chrome长跑/交互、已完成修复；遥测范围排除特权工具 |
| R06 | [Stage6 accuracy脚本](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/p0/perf_probe/analyze_stage6_stage3_accuracy.py) | ASR取样层与654行结果缓存问题 |
| R07 | [Stage6 timing脚本](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/p0/perf_probe/analyze_stage6_stage2_timing.py) | DOM/媒体/SRT/WS时间定义 |
| R08 | [local_pipeline.py](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/engine/src/subtitle_service/local_pipeline.py) | 1.6s门槛、token缓存/历史、Qwen A3 适配器、ANE去重与fallback/close |
| R09 | [nemo_ane.mm](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/engine/native/nemo_ane.mm) | CMVN、fixed buckets、CPU decoder、hybrid、内存/保活 |
| R10 | [实际样例配置](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/engine/config/profiles.example.toml) | Stage7参数与A3配置模板（本机 `.model-cache` 未留存 A3 权重）；不代表用户实际profile |
| R11 | [test_m1_pro_hq.py](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/engine/tests/test_m1_pro_hq.py) | 生命周期/回退注入覆盖；不等于新模型真机通过 |
| R12 | [Chrome harness](https://github.com/lkyprogramer/live-video-tanslate/blob/7ea13865c8c3673a1463495058a200723b153111/p0/perf_probe/verify_stage6_ep01_chrome.mjs) | 可复用参数、需避免固定路径覆盖旧归档 |
| R13 | [旧PLaMo实验](https://github.com/lkyprogramer/live-video-tanslate/blob/edefcbcf35f1ef80f90c1ef27710ea1e861b2d5f/docs/p2-b1-results-2026-11-08.md) | 小PLaMo已测试、不把新检索当首次发现 |
| R14 | [旧Tier2B MT实验](https://github.com/lkyprogramer/live-video-tanslate/blob/edefcbcf35f1ef80f90c1ef27710ea1e861b2d5f/docs/tier2b-mt-results-2026-11-23.md) | 同机Qwen8B、TranslateGemma、SeedX、PLaMo8B结果 |

历史报告被后续清理，不等于实验没发生。追溯使用旧commit permalink，不要求恢复所有旧文件到主分支。没有公开的原始媒体/本地日志不能从聚合JSON反推出来，须执行时由本地提供。

## 2. 推理协议与MT候选

| ID | 来源 | 证据与适用范围 |
| --- | --- | --- |
| M01 | [MLX-LM v0.31.3 generate.py](https://github.com/ml-explore/mlx-lm/blob/v0.31.3/mlx_lm/generate.py) | 锁定依赖的generate_step look-ahead；EOS/KV偏移（`+1..+4`）与滑窗退化已于2026-11-26在本机 `Qwen3-4B-4bit` 复现 |
| M02 | [当前Qwen4B Q4](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit) / [Q8](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-8bit) | 同底模量化候选；tokenizer/stop/revision仍需锁定 |
| M03 | [Qwen30B-A3B官方卡](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507) / [MLX Q4](https://huggingface.co/mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit) | 参数、专家、非思考架构，不保证实际翻译/延迟 |
| M04 | [MLX MoE layers固定commit](https://github.com/ml-explore/mlx-lm/blob/6fe0ce48c90cec11f2c305830ceba461051c33b2/mlx_lm/models/qwen3_moe/layers.py) / [gather实现](https://github.com/ml-explore/mlx-lm/blob/6fe0ce48c90cec11f2c305830ceba461051c33b2/mlx_lm/layers/moe.py) | 路由和量化gather；主线不是用户锁定runtime，不能直接算已合入优化 |
| M05 | [M1Pro14GPU oMLX结果1](https://omlx.ai/benchmarks/performance/7lnq46qh) / [结果2](https://omlx.ai/benchmarks/performance/ln5u9bzl) | 用户提交的原始跑分页面；v0.7.1rc1/1k Code prompt，TG约35.5–43.2、TTFT3.27–3.49s；不是本机字幕证据 |
| M06 | [Index2B官方卡](https://huggingface.co/IndexTeam/Index-Translate-2B) / [官方GGUF](https://huggingface.co/IndexTeam/Index-Translate-2B-GGUF) | 翻译专训、模板/量化制品，厂商总榜不等于JA→ZH日剧优胜 |
| M07 | [Index文本运行文档](https://github.com/bilibili/Index-Translate/blob/main/inference/llm/README.md) | 纯文本模板和服务接口；不迁移A100/MTP速度、不调用外部免费API |
| M08 | [PLaMo3 2B](https://huggingface.co/pfnet/plamo-3-translate-2b) / [发布者说明](https://www.preferred.jp/en/blog/tech/plamo-3-translate-weight-release) | JA/ZH、语境词表、新旧版本区别；个人/商用license需核，已有旧测不能忽略 |
| M09 | [Sakura14B](https://huggingface.co/SakuraLLM/Sakura-14B-Qwen2.5-v1.0-GGUF) / [GalTransl7B](https://huggingface.co/SakuraLLM/Sakura-GalTransl-7B-v3.7) | 轻小说/专用prompt、CC BY-NC-SA，非普通Qwen2.5-14B；不推断日剧或实时优胜 |

Index官方模型卡/制品标Apache-2.0；Qwen上游为Apache-2.0；Sakura和PLaMo有不同条件。下载前重新核查当前revision的LICENSE并写manifest；本文不是法律许可意见，不能把社区转换作者的许可替代基础权重协议。

## 3. ASR候选与研究

| ID | 来源 | 使用边界 |
| --- | --- | --- |
| A01 | [NeMo-v2](https://huggingface.co/reazon-research/reazonspeech-nemo-v2) | 619M日语主线；已适配ANE，不是待新增方向 |
| A02 | [QwenASR MLX 1.7B Q8](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit) / [官方流式实现](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_asr.py) | 仓内已有适配器，本地权重已清理需G3下载；vLLM累积/回退前缀不等于已支持MLX时间戳流 |
| A03 | [Whisper Turbo官方卡](https://huggingface.co/openai/whisper-large-v3-turbo) / [MLX](https://huggingface.co/mlx-community/whisper-large-v3-turbo) | 809M、4decoder层，多语转写；Whisper翻译任务非日→中 |
| A04 | [MLX Whisper transcribe](https://github.com/ml-explore/mlx-examples/blob/main/whisper/mlx_whisper/transcribe.py) | 标准帧填充与转写路径；短音频不代表等比例计算下降 |
| A05 | [Kotoba v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0) / [v2.2](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2) | 756M与后处理关系；不是参数翻倍的新识别器 |
| A06 | [Kotoba MLX](https://huggingface.co/kaiinui/kotoba-whisper-v2.0-mlx) / [社区CoreML](https://huggingface.co/yslinear/kotoba-whisper-v2.2-coreml) | 转换需一致性验证；CoreML制品的token timestamps限制 |
| A07 | [日语ASR原始对照数据](https://huggingface.co/datasets/yukobayashi500/ja-asr-leaderboard-2026) | T4 fp16、650FLEURS/1000Reazon、默认解码不同、训练域偏差；只用于反驳简单参数排名 |
| A08 | [ASRlance作者对照](https://github.com/shotafujie/asrlance) | 私有小样本与作者测量，不外推到用户机器 |
| A09 | [Turbo full-ANE制品与作者测试](https://huggingface.co/mashbox-labs/whisper-large-v3-turbo-coreml-ane) | M5英日时延不同；不拿英文176ms推日语M1 |
| A10 | [日语Parakeet](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja) / [Fluid欧洲多语CoreML](https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v3-coreml) | 不同模型与语言覆盖，不当作可直接替换的同一日语模型 |
| P01 | [Stable-Prefix / Dynamic Lagging论文](https://arxiv.org/abs/2609.05799) | Qwen3-8B，英→德/日/中；支持“等待策略可学”的方向，不是日→中M1实测 |
| P02 | [SubER论文](https://aclanthology.org/2022.iwslt-1.1.pdf) / [实现](https://github.com/AppTek/SubER) | 字幕分段/时序辅助评价，不能替全部语义审阅 |
| P03 | [A-TASC论文](https://aclanthology.org/2025.acl-long.157.pdf) | 分段/语言与评价口径相关，提醒不能混用片段匹配 |

## 4. 本轮检索范围与置信度

使用Exa做模型/框架/原始测量检索，并通过GitHub读取当前及历史代码。并行研究记录的检索请求结果配额合计146（主审15、MT96、ASR35；21次查询），**不是146篇独立来源，也不是146篇全文均审过**。本台账只列进入决策的一手代码、官方模型资料、原始测量或论文；社区转述不作为性能结论。

来源动态变化会使main链接内容改变；执行前记录访问时间与SHA。特别是MLX主线的新模型目录/支持能力不能自动视为用户锁定0.31.3已支持。若源码与模型卡矛盾，以实际固定制品和可复现实验为准，并保留差异说明。

证据标签建议：`REPO_OBSERVED`仓库归档/代码、`UPSTREAM_DECLARED`作者说明、`INDEPENDENT_MEASURED`外部原始测量、`INFERENCE`本报告推断、`TO_TEST`新假设。任何数字必须能落到具体run/来源，社区数字不得混入本机baseline列。

## 5. 失败路线重开记录模板

旧结论 → 新证据 → 变化变量 → 最多投入多少样例/资源 → 什么结果立刻停止 → 是否需要新授权。六项不完整就不重开。重开后旧结论继续保留，避免每轮换一个名字重复同一实验。
