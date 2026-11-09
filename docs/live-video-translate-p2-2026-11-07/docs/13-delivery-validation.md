# 13 · 本次审查与实际验证范围

## 13.1 事实与历史来源

原始GPT Pro交付声称在Linux/x86_64、Python3.12.14运行35项辅助器测试，并检查ZIP。那是上游记录，不能作为本机模型或浏览器验收。本次原包已备份在本机ignored `p0/evidence/p2-doc-review-20261108/original/`，没有覆盖历史模型实验。

2026-11-08最初文档审查由主代理结合实际仓库和已有raw/汇总执行，另派独立`gpt-6-astra / xhigh`只读审查固定主源，见上级REVIEW-WORKFLOW.md。当时只确认Apple M1 Pro、32GiB、macOS26.7.2，没有新模型成绩；后续B0已核实16核GPU，B1新增候选实测见13.3。

## 13.2 本机包验证

最新实际结果见上级BUILD-VALIDATION.json，原始辅助器测试日志见BUILD-TESTS.log。包内`validate_package.py`可重建并检查：

- Python辅助器单元/CLI测试及三个合成示例；不把synthetic当实测。
- JSON解析、四个Draft2020-12 Schema自身与适用示例校验。
- 注册表唯一ID、模型/runtime/parent引用、依赖无环、核心8席位及B4预算。
- 本地Markdown链接、围栏、分章与生成阅读副本一致性、完整文件SHA256清单。

这些检查只证明文档和辅助工具内部一致。JSON Schema不能核验音频真值、注册时序或模型文件真实性；模型与runner检查要读取各阶段的真实验证报告。本包没有生成ZIP，不声称已做ZIP CRC检查。构建脚本只在实际测试成功后写报告。

## 13.3 后续实际执行状态

- [B0](../../p2-b0-results-2026-11-08.md)完成原模型复现、两轮真实1×、四个前台Chrome归因组及CPU/GPU/共享内存；不是人工语义或正式发布验收。
- [B1](../../p2-b1-results-2026-11-08.md)完成四组离线候选、同轮M0复现、六组串行资源记录、两轮272项AI文本判定与独立复核。Reazon两侧900ms补零、ONNX日文标点和官方PLaMo0.8/2B MLX H0均能运行，但均未通过Q/R离线前提。
- [B2](../../p2-b2-results-2026-11-09.md)完成SenseVoice与whisper-ja760两种CPU配置、同轮Reazon88与M0两轮62逐字复现、六组资源及136项独立文本复核。两配置都能运行但未达到Q/R前提；实际native长度上限、标签隔离及非语音限制单列。
- B0 CLI与B1/B2离线分派/adapter已实现，源码在`p0/p2/`。当前没有候选replay/accept/holdout实现，没有新增生产策略类型；不把这些后续入口当成已运行功能。
- B3的Voxtral、Gemma仍未运行；B1/B2没有启动候选真实1×、Chrome/全屏/60分钟。没有复听15项参考、人工校时或打开封存test/。
- 未改当前默认后端或engine锁；B1/B2未修改Chrome产品代码，未提交、push、发布或部署。

研究锁中的未执行项仍需preflight，不能用latest补齐；B1/B2真实模型/源码/数据SHA256与实际运行时保存在报告指向的raw run。文档包辅助测试、runner测试、真实推理完整性和语义晋级分别报告，均无合格候选也允许结束。
