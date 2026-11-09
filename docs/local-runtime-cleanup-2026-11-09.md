# 2026-11-09 本地实验清理与归档

按用户要求结束当前探索，删除未采用的模型、独立实验环境、下载框架和构建产物。当前仍使用 **ReazonSpeech INT8 + Hy-MT2 1.8B 4bit（reazon-hy）**。实验源码、冻结配置、失败结果、CPU/GPU/共享内存记录及测试素材保留；未将清理理解为新的模型探索授权。

## 清理范围

| 对象 | 清理前 GiB | 清理后 GiB | 结果 |
|---|---:|---:|---|
| 项目 .model-cache | 31.704 | 1.110 | 只保留当前两组模型、Silero VAD及少量诊断/元数据 |
| 旧根目录 .venv | 1.348 | 0 | 删除；engine/.venv保留 |
| WhisperKit Swift .build | 0.274 | 0 | 删除可重建构建产物 |
| whisper.cpp与jp-live-subtitle-cn参考仓库 | 0.054 | 0 | 确认固定提交、工作树干净且各自无其他worktree后删除 |

按上述路径的du统计，占用合计减少约 **32.269 GiB**。该数字是目录占用变化，不等于APFS或共享硬链接的实际物理回收量。

删除的候选下载包括Whisper small/large-v3-turbo、WhisperKit Core ML、Kotoba、Apple Parakeet/FluidAudio、Nemotron、Qwen3-ASR、Moonshine、Haas、Granite、其他Zipformer、Index-Translate、Hy-MT2 7B、PLaMo、SenseVoice、whisper-ja760、Voxtral和Gemma4；同时移除对应独立环境、mlx-audio/mlx-vlm等下载源码、llama实验构建、分片传输目录及未引用共享blob。当前Reazon同revision下未采用的FP32 encoder/joiner也已移除，保留实际配置使用的INT8 encoder/joiner、decoder及tokens。

两个外部参考仓库分别是whisper.cpp@727d3e7666485e12450746b523bfb4e7f057cc5e、jp-live-subtitle-cn@c0aca604c7922c040b41fd74a6034262fb8cf976。WhisperLiveKit仍是当前Hy翻译实现的依赖，其engine安装和参考源码保留。没有修改engine依赖锁或卸载当前服务环境中的间接依赖。

## 保留与完整性

- 当前Reazon revision291488c8151be24d7da4bf7af26e533fad96e407、Hy revisione5c6fe56c7b3bc77fae5ae92db31f2178f1e6912及Silero VAD保留。沿两级符号链接保留Hy实际共享blob，避免删除仍在使用的权重。
- 清理前后47个保留路径的内容hash一致，包含模型/元数据、本机profile及engine锁；原有20,483份实验文件的内容或链接均一致。完整清理计划、逐项结果与校验保存在本机ignored目录p0/evidence/cleanup-20261109-211823/。
- p0/fixtures、封存test/、历史原始输出和资源样本、用户配置与令牌均保留。全局uv/HuggingFace缓存及macOS管理的Apple模型资产未清理，避免影响其他项目或系统服务。
- B4原始独立审查中引用的文档包hash对应收尾前版本；该完整包已另存于清理证据的plan-package-before-closeout/。新包只补充收尾/恢复边界并重新生成，不覆盖旧实验manifest、评分或审查收据。

## 验证

清理后实际执行：

| 检查 | 结果与边界 |
|---|---|
| engine/.venv/bin/python -m pytest -q p0/p2/tests | PASS，37项；验证此次交付的P2工具与协议 |
| 离线真实模型烟测 | PASS；当前LocalBackend预热，识别原csj-reproduction/1的6.738秒音频，与保存日文一致，再经当前Hy产生非空中文且无假名 |
| JavaScript/Python语法 | PASS；两个P2浏览器脚本通过node --check，P2 Python通过compileall |
| 结果表发布字节 | PASS；8份CSV与Git暂存内容逐字节一致。.gitattributes保留其CRLF，避免自动换行转换破坏历史SHA256 |
| 文档包与最终发布文件检查 | 结果见[机器汇总](experiments/local-runtime-cleanup-2026-11-09/summary.json)；仅本地文档、文件与提交范围检查 |

烟测设置HF_HUB_OFFLINE=1、TRANSFORMERS_OFFLINE=1，没有重新下载模型或启动HTTP服务。观察到既有Transformers rope_parameters warning，但模型加载、识别和翻译均完成。该烟测不重新评分，不证明新的实时延迟、Chrome上屏或长稳结果；这些均未重跑。服务维持清理前的停止状态。

## 复现与使用

当前服务的启动方式保持不变：

~~~sh
uv run --project engine --frozen subtitle-server --config engine/config/profiles.toml
~~~

各实验报告是历史实测结论，清理后的本机不再具备所有候选的即用复跑环境。需要复现时，先从该run的model/runtime锁、source-snapshot及报告恢复指定revision、量化、依赖与下载，再按新指令创建新attempt；不能把缺失的已清理模型当作新实验失败，也不能改写旧冻结锁绕过检查。

B4只使用B4-1，剩余两个名额保持未使用；没有候选晋级，B5/正式验收/封存集未因收尾自动启动。后续探索需新的用户指令。
