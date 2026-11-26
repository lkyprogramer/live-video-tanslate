# Stage 8 数据Schema

此目录只有格式合同，不含已实现的benchmark。`contracts.schema.json`含Manifest、Event、SemanticUnit、Judgment、ClockAnchor、Thresholds、Aggregate、GateDecision及辅助定义。聚合metrics固定holdout；soak_metrics另列；dev_attribution可空。关字幕control有独立角色和掉帧字段，不能充当翻译基线。

校验单个类型时，建立根schema `{"$schema":"https://json-schema.org/draft/2020-12/schema","$ref":"#/$defs/Manifest","$defs":<原文件$defs>}`，其余类型替换Manifest。使用已有评测环境中的JSON Schema Draft2020-12 validator；没有依赖则先取得安装授权。不能因为根文件只有$defs就直接validate(data, root)并误认为已校验。

跨字段检查必须另外实现：sample区间顺序/因果性、roles引用models、artifact真实hash、CLI与manifest一致、同unit分母、事件ID关联、clock映射、模型/许可状态、占位拒绝，以及aggregate计数<=eligible_n。结构通过不等于实验许可、质量或性能通过。

百分位null表示门禁分布含足够missing而为正无穷，不能默认0；未采集的未知值则同时令proof/status不可判，二者要依据missing_n与证据区分。私有日志允许text，公共产物脱敏后不能假称可重算全部语义。
