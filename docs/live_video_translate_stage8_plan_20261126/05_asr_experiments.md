# 05 · ASR：区分听不清、切坏、解码错误和字幕参照错误

## 1. 不把一次Oracle切片当硬上限

Stage 7 报告中的 `.2603` 与回答中 `.2682` 系规划期在 EP01 `0–360s`（`94` 条 JP SRT cue、`pad=350`）跑过的临时只读探针（未落盘归档 JSON），因此不能单独证明 619M 的理论极限：SRT 并非逐字转写、时间边界可能截尾、说话人切换/音轨/CMVN/pad/解码/精度均会影响结果。需在 A0 中做可复现正式归档。

先从真实音频选48–80个连续语义单元，覆盖：快语速、低声、背景音乐、短应答、词跨强切点、数字、人名、两人打断。记录人工逐字日文和可接受变体；不要为凑齐短切片借用未来SRT内容提示模型。样本用于dev归因，另有独立holdout。

## 2. A0：同模型2×2，定位边界与上下文

对同一目标区间固定PCM与解码配置，分两组进行：

| 轴 | 控制 | 挑战者 |
| --- | --- | --- |
| target切分 | 当前VAD/2.8s规则 | 人工语音/语义边界，仅离线归因 |
| input上下文 | 当前overlap/pad | 已到达的更长左上下文，总input≤4s/6s |

人工切分属于oracle诊断，**不计实时可部署收益**。同一种target规则内比较短/长input时固定相同target归属；不同切分规则之间必须重组到同一冻结scene/人工语义单元再比较，不能把不同target逐条硬配对。不能把长窗额外历史全文算进输出再和短句比CER。右侧真实音频另列0/160/320ms collar，等待如实计入。

然后只对明显困难case做greedy/beam/hybrid单因素；pad500与减少pad单列，禁止同时改四个参数后说“模型提升”。`hybrid`按字速触发beam且选长结果的当前行为记录下来，不能在新臂偷偷改排序。

诊断判定：

- 人工边界显著改善、同模型足够准：优先边界与归属，而非换模型。
- 同段输入下大模型改善、边界影响小：支持模型能力路线。
- 原生/转换版本与官方参考在同PCM上显著不同：先查转换/归一化/量化/解码一致性。
- 人类也不能确认内容：标不可评/歧义，保留音频难度，不造确定标签。

## 3. 前端与ANE的实际约束

当前 `nemo_ane.mm` 的frontend对整条带pad窗口做每Mel频带的均值方差归一化。多加上下文会改变目标区间的归一化特征；旁边更响的说话人可能反而影响轻声目标。因此“长窗必更准”“缓存旧特征无损”都不能预设。

现有固定桶S256/M560/L1024，10ms hop约对应2.56/5.60/10.24s Mel长度。两侧500ms pad占用桶容量；6–8s真实输入通常进入L桶。实际选择以native `bucket_mel/mel_frames/enc_frames` 为准，不按文字估计性能。

native已提供frontend/predict/output_copy/decode/total timings。新增greedy耗时、beam耗时、触发原因、候选字符率、最终选择原因。超过桶上限的行为必须明确报错/回退并记录，不能隐式把MT与GPU回退一起跑。

## 4. A1：有限模型×窗口对照

先同PCM同span，不先加各自特殊prompt。统一JA转写，不让Whisper做英译；所有推断均batch1，模型预热与编译另报。

| 顺位 | 精确候选/实现 | 已知事实 | 实验用途 |
| --- | --- | --- | --- |
| 0 | 当前NeMo ANE hybrid | 619M；native CPU RNN-T解码；已有时间戳/归属逻辑 | 短窗与因果左上下文基线 |
| 1 | 仓内适配器A3：`mlx-community/Qwen3-ASR-1.7B-8bit`，revision `a8379a2e2f9e313c9292cdf1af4055ab56d50d55` | 仓内代码已有适配（`Qwen3AsrRecognizer`）与GPU执行线程，**但本地 `.model-cache/hub/` 权重已在先前清理中删除**；decode只返回文本 | G3获下载授权拉取权重后，优先复用现有适配做错例互补 |
| 2 | `mlx-community/whisper-large-v3-turbo` | 809M、4层decoder；标准MLX常填充至30s encoder帧 | 独立离线挑战者，不能推断150–350ms |
| 3 | `kaiinui/kotoba-whisper-v2.0-mlx`，源`kotoba-tech/kotoba-whisper-v2.0` | 756M、完整large-v3 encoder＋2层decoder；需核转换 | 有预算且前两者不够再测 |

Kotoba v2.2的价值主要是说话人分离/标点pipeline；不是另一个1.5B基础ASR。社区CoreML v2.2制品明确关闭token timestamps，不能直接替换当前依赖归属的ANE路径。官方Qwen的vLLM流式接口也不等价现成的MLX时间戳流式服务；获授权重新拉取 A3 权重即可复用现有 MLX 适配，无需先换 runtime。

公开作者原始数据提供反例：Qwen1.7B在FLEURS朗读CER5.36%，电视广播25.98%；NeMo为7.46%/11.00%。此测试是T4、不同默认解码，NeMo训练域有优势，**不是用户日剧排名**，只说明参数量/朗读总榜不能替代本地片源选型。原始来源见 [09](09_sources_and_history.md)。

## 5. A2：边界保护与提交归属

复用现有sample clock，显式分离input span和target span，维护 `committed_until_sample`。每次输出必须证明没有越过当前captured end、没有重复提交已FINAL内容。

初始实现优先保守：

1. 当前ANE结果仍是fallback。
2. 有时间戳且通过边界验证的NeMo长窗可只取target的token；当前约80ms发射时间要做容差/错误评估，不当真实词边界。
3. Qwen仅对一个完整未提交target重识别；没有可靠alignment前不做多target滚动全文输出。
4. 强切末尾可以短暂保持未提交，右collar最多当前实验指定的320ms；不为了“凑8秒”延迟整段。
5. 选稿根据dev校准的质量规则；字符变长、编辑距离更大、两个模型一致均不是真值。无法判断时回退并归档，不默默拼接两段全文。

不引入歌词/字幕参考文本到实时模型；术语表只可使用播放前允许的角色名或过去已经确认的内容，记录来源。不得把EP01后文答案写入“上下文”解决当前难例。

## 6. A3：选择性复核，而非每句双跑

触发候选特征：低字速、greedy/beam分歧、draft长度塌缩、强切处能量/音素仍连续、异常数字/重复、BGM段。它们是筛选信号，需报告触发率、召回、误触发率以及每次成功修复的额外GPU毫秒。

先shadow：真实1×播放，后台计算但不改变输出，比较“若采用”的质量与deadline是否赶得上。shadow本身占GPU，应另跑无shadow基线并报告干扰；不把shadow数据的延迟算成生产无额外成本。

再限量启用：一个session最多1个pending复核；当前span明确选稿截止时刻，过期结果只记日志。GPU final MT优先，复核预算不足立刻用NeMo，禁止等待积压。期望负载：

`rho = λ_final×E[S_MT_final] + λ_preview×E[S_MT_preview] + λ_flag×E[S_GPU_ASR] + 其他GPU回退负载`。

均值rho<1是必要但不充分条件；突发对白与长尾仍可能过载。初始计划以0.7为**保守观察目标**，不是硬件测得上限。记录真实dwell/deadline miss；Stage7的104final/300s意味着每条重扫8s已相当于2.77s音频/真实秒，尚不含草稿。

## 7. 可暂停的扩展

日语专用 `nvidia/parakeet-tdt_ctc-0.6b-ja` 可观察，但不要把FluidAudio欧洲多语v3 CoreML模型当作它的日语实现。新encoder转CoreML、全ANE Whisper、缓存encoder块，只有在A0/A1显示质量有必要且实际热点有收益时再立项。

满足 [08](08_acceptance_and_rollback.md) 才晋级。若所有挑战者只是速度慢、关键错误没降，结论是“本轮这些候选在该合同下不优”，不是“ASR领域再无突破”或“M1物理极限”。
