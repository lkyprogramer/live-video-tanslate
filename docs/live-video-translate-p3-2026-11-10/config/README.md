# 配置文件如何使用

这些JSON是执行规格，不是某个已经存在的通用P3 runner可直接加载的成功配置。后续T02的check/CLI必须严格实现约束，未实现前不得靠读取JSON就运行未知模型。

- `experiment-registry.json`：固定主配置、备选菜单、唯一条件槽和预算。`secondary.selected_choice=null`表示尚未选择，默认建议不是同时运行授权。R2T2和RNNT为研究主线，`measurement_status=not_run`。
- `evaluation-contract.json`：唯一数值门槛源；若章节或代码与之冲突，先停止并审查，不自动采用最宽值。它不包含任何新测量。
- `model-lock.json`：符合本包helper schema。已知HF revision保留；文件清单待目标机恢复并hash，所以现在exit2是正确结果。候选变成当前阶段所需时必须设置required并校验集合，不能把selected候选保持optional来绕过门。
- `runtime-artifact-lock.json`：独立存代码pin、VAD、依赖/编译/精度/前端/许可证。不是model-lock输入，不传给`check-lock`。未来P3 check负责完整核验。

`model-lock`中的Qwen入口是上游身份，不是已经验证的GGUF下载源。若为机制控制转换，记录上游revision与每步转换代码/argv/hash，生成本run实际工件清单；没有同后端/同量化控制时，允许仅作产品比较，但不得声称隔离证明后训练收益。

同理Omni必须证明ONNX来自v2；Nemo原包与导出ONNX不能共用一个“已验证”标签；mmproj/tokenizer/config也必须有hash。路径为本run材料根下规范相对路径，不能用`../`或symlink指向外部来蒙混字节检查。

JSON Schema仅管结构。人工真值、许可、原声时钟、未来音频、state归属、代码行为和指标胜负均不能靠Schema自动证明。template/synthetic/measured分类要保留，绝不把模板0或null当实际结果。

本次本地复核在P3尚无输出时补齐Japanese/首块/步预算/EOF及S0_UNTIMED_VAD_SNAPSHOT，R_memory统一为同surface热态完整集合physical footprint，RSS仅辅助；旧P2 R_latency不变。数值停留门也编码在evaluation-contract.readability；模板只占位，不代表人审或运行通过。
