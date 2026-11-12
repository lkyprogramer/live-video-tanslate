# 中断后恢复提示词

先读README、10章和最后一个run的manifest/receipts/decision，不从零搜索、不重跑已通过步骤、不把旧失败覆盖成新成功。

1. 检查用户最新指令及授权是否变化；检查git状态和source/model/runtime/input/reference锁。
2. 找出本run拥有的服务与native worker，确认是否真的退出；清理中保持busy。不要按进程名批量杀进程，不启动第二个模型掩盖僵住任务。
3. 验证已完成步骤的输入fingerprint和输出hash。只有passed且完全匹配才复用，注明reused_receipt；其余另建attempt。
4. 读取已用工程/运行/墙钟预算、依赖修复次数、3+1槽和2E2E槽。恢复不重置预算。
5. 确认当时实验参数：R2T2 W16O8/U1/S0、或RNNT480/cache、所选secondary。任何改轴需新experiment_id；S1/80ms全局只一个。
6. 将缺材料、权限、人工语义、校准、模型runtime分别写blocked_data/permission/runtime或needs_human，不把未跑记0分。
7. 只执行下一项获准且门已满足的任务。发现合同/Schema/日志矛盾先停并解释，不选方便口径继续。

恢复汇报应包含：从哪个receipt恢复、复用了什么、没有复用什么及原因、剩余预算、当前阻塞、下一条准确命令。不得声称恢复过程中完成了新的M1性能验证，除非确有新run证据。
