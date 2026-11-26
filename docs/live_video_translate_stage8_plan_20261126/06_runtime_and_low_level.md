# 06 · 软件层与底层推理的优化范围

## 1. 先画同一请求的时间线

记录音频到达、释放、ASR前端/encoder/decoder、GPU等待、MT未命中prefill、decode、网关、WS、DOM。对每条请求找关键路径；不要拼不同样本的中位数。

例如某一请求端到端700ms，其中ASR40ms，即使ASR翻倍只省20ms，约2.9%。这是说明收益上限的假设算例，**不是对现有P50的因果拆解**。若难例尾部CPU beam实际耗200ms，优化目标会不同。

GPU/ANE平均空闲可能因为在等音频，不代表单次矩阵乘可以用空闲时间瞬间完成。单条性能取决于关键路径、访存与kernel调度；吞吐余量可用于选择性复核，但要计新增队列。

## 2. 最值得先做的低风险软件工作

| 项目 | 为什么 | 证明方式 |
| --- | --- | --- |
| canonical token与KV身份 | 防止手工模板、EOS和清洗历史偏差 | fresh/cached真实token/logit parity，不仅字符串等号 |
| 预览独立事务 | 防止草稿污染最终历史或取消后残留 | preview→cancel→final与从无preview参考一致 |
| 精确失效与重播计时 | 当前history裁剪会重放；大模型对此更敏感 | N_reused/N_prefill/N_out、replay原因、每次时间 |
| 合并无意义draft工作 | 短增量/过期语句不必全量翻译 | 相同可用首条、少算请求且无新漏句 |
| 有界队列/epoch | 防止更强模型加入后积压和旧字幕穿越seek | 压测、超时、取消、late-result注入 |
| 结果缓存内容寻址 | 防止654条旧cache误命中 | 任一语料/模型/prompt/runtime变化均失效 |

## 3. NeMo native可研究的真实热点

已有：vDSP FFT、Accelerate `cblas_sgemv`、CoreML固定桶和缓冲、beam predictor cache、同步与watchdog。不能把这些已经存在的能力重复包装成新优化。

需要新增分解计时：

- frontend预加重/FFT/filterbank/CMVN/分配；predict与output copy。
- greedy是否重复执行、hybrid触发率与耗时。
- predictor cache命中、`pred_step`、`joint_logits`、logsoftmax、候选top-k、prefix map查找、hyp分配/复制、合并/排序。
- cancellation在各阶段的最坏等待时间；不要以去掉取消检查换看起来更快。

只有某项在困难片段关键路径占比≥20%，或p95可归因耗时≥50ms时，才进入对应原生优化（拟议立项门限，不是当前观测）。优先尝试：

1. **scratch复用/arena：** 减少每个hyp分配和prefix vector复制；session隔离，取消后回收，不能跨请求复用错误LSTM状态。
2. **prefix节点索引：** 在保留等价labels、score_norm、tie-break、logaddexp和beam剪枝顺序的前提下，降低map/vector键开销。现有已经有cache；这是降低cache管理成本。
3. **批量joint计算：** 同一步活跃hyp能合并成小GEMM时试Accelerate；小batch未必更快，记录pack/unpack成本。浮点归约顺序改变需要单独数值与输出一致性验收。
4. **固定桶选型：** 观察实际输入/pad落在哪个桶；不要为速度截掉有效音频，也不要删pad后不重测质量。

每次只改一项；固定PCM golden集检查token、frame、score/排序与参考。若数值不同导致输出改变，先按算法变化而非纯等价优化评估。beam截断/合并的次序不能凭直觉“修正”，须先与官方算法/已有参考对照。

## 4. MLX/Metal优化的条件

M1 Pro峰值200GB/s不等于所有MLX量化kernel稳定140–160GB/s。记录模型文件、实际resident量、prefill/decode区分、prompt/输出token数、异步同步点，才讨论带宽或算力瓶颈。

优先消除无效prefill与错误重播，其次减少不必要的Python↔设备同步/短小kernel launch。若方案依赖编译图、融合算子或更新MLX，只在隔离环境锁定版本A/B。基准结束须同步到真实完成，不能计异步提交返回时间。

任何“替换attention/quantized matmul/路由gather”都要有稳定热点与数值参考；若占总延迟很小，直接停止。不要先接入完整oMLX/Splash/另一个服务器来追求名称上的底层升级；运行时整体替换会同时改变template/cache/scheduler与stop，归因成本远高于单项补丁。

## 5. 为什么不先把所有东西迁到ANE

当前ASR encoder走CoreML的CPU+NeuralEngine允许集合，不表示每个算子一定在ANE，也不表示CPU RNN-T decoder没成本。NeMo短段已经几十毫秒级，剩余链路常有等待、MT与语义问题。对新ASR/MT做CoreML转换需要模型支持、固定shape、状态/KV、精度/量化验证；跨CPU/ANE/GPU搬运与编译也有成本。

先复用当前ANE路线，把资源投入有用的质量对照。仅当另一模型在本片源明显更准且GPU争用成为明确瓶颈，才考虑它的ANE制品或转换。不要再以“ANE使用率很低”本身作为改造理由。

## 6. 内存与功耗边界

预检：32GiB实际内存、Chrome当前占用、磁盘可用空间、现有swap；记录稳定播放2分钟基线。候选载入后重新测系统压力、页面换出、MLX active/cache/peak和process footprint。

- 模型工作集12GiB起步，18GiB只是条件预算；总系统有约6GiB余量是规划目标，不把macOS cache记账误当硬性free判据。
- swap used从载入前增长≥256MiB，或memory pressure持续黄/红≥10s，立即停止该实验臂并保留证据；先排除其他应用变化再决定是否重试。
- 严格默认档长跑要求没有候选引起的持续swap增长，后半段无持续内存/队列上升趋势；不能仅看测试结束的RSS。
- 不为了测大模型自行关闭用户浏览器、清理模型缓存或调整系统wired限制；必须保持真实使用工作负载。
- power/thermal只有在授权采集时作定量结论；未测时写UNKNOWN。质量档不能因速度好就省略功耗与续航不确定性。

## 7. 明确停止条件

若cache/输入语义已正确、同片源候选不降低关键错误、或增加计算使deadline/内存门禁失败，停止该路线并保持ANE轻量档。接受“当前最好的可复现折中”，但不使用“物理极限”“模型绝对天花板”措辞。

未来真正跨档的方向可能是日→中稳定前缀训练、领域蒸馏、因果ASR或更强翻译专模；它们是独立研究，不是本轮代码优化一定可拿到的收益。
