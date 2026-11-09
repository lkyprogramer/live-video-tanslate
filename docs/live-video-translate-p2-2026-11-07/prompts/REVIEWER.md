# 独立审查任务

读取实验hypothesis、locks、input hashes、raw、events、judgements、decision与相邻B0。审查目标是结论是否可重建，不是重写实现。

优先检查：实际代码/模型是否与锁一致；当前协议是否真的存在；音频是否因果；未来文本/参考是否泄漏；删失败样本/改变分母；Q/R事后切换；新关键错发生在原失败项；旧epoch晚到输出；padding越界；生成器未迭代/异步未完成计时；RSS/GPU内存相加；全机GPU冒充模型；短样本/重复seed冒充独立证据；native会话状态是否有界；B4额度是否超出。

复算两轮净增、restored/lost与2/3/5/15秒覆盖。P95同时看样本数和未匹配。核验原速实际时钟、DOM可见性及历史121/保留集。全部关键错和所有新增收益/丢失要复核；不具音频条件只能作结构审查。

输出review.json与简短review.md：blocking、material、editorial分开；每项附文件、证据、影响和最小修复。明确通过/不通过/无法判断的范围，不替代未运行的Chrome或人工听写。不要另开模型搜索和无关重构。
