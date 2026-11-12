# 07 计时 资源与语义评分

## 复用并补足事件

复用p0/p2/events.py的EventLog及validate_causality，但它只检部分水位和顺序。现有source_commit范围校验只到accepted，P3还要检查consumed、不可变前缀、重复翻译、epoch晚到事件和scope切换。客户端记录的“pcm_accepted”实际可能只是sent，必须与服务端trace分开，不能当服务器已经接纳。

必要事件：pcm_sent、pcm_accepted、pcm_consumed、vad_release、asr_start/end、ja_commit、mt_enqueue/start/first_token/end、caption_filtered/emit、ws_receive、dom_visible/hidden、cancel/worker_return/finished、resource_sample。每项带run_id、session_id、epoch、clip_id、source_id、host monotonic_ns、真实样本水位和具体来源。JA临时输出另留provisional但不得进入MT。

R2T2另记每步累积音频秒数、encoder实际重算秒数、prompt token数、KV清空次数、单步计算和最老等待；RNNT另记cache各tensor形状/字节、有效长度、chunk/shift/right context、predictor状态大小和解码步数。只记“streaming=true”不够。

包内prefix-audit读取规范化的累计已提交文本快照，按(scope_id,channel)检验只追加、sequence严格递增和source_end_sample不回退。scope固定为session/epoch，utterance/window只作为内部诊断ID，不能为了掩盖回写而换scope。channel=zh检的是已发布字幕串的累计账本，不是正在显示的单条字幕；正常换行显示不等于回写，旧条目内容改动才是违约。工具不证明scope归属、真实时钟或语义，完整生命周期另由集成测试验证。

## 时间与可读终点

所有媒体样本以16kHz计；真实时间采用后端单调时钟，Chrome performance.now需每轮前后至少10次往返校准，保存最低RTT估计和误差区间。seek/新epoch重建映射。不得用Date.now相减、按钮时间替代语音起点或直接把sample/16000当已消费墙钟。

每个语义项的第一次稳定正确可读中文时刻，以实际DOM可见且内容足以表达语义为准。跨字幕拼合取最后必要部分出现时间。每个表达必要语义的字幕片段必须拥有实际连续可见区间，时长至少max(600ms, sum(ch.isalnum() for ch in text_zh)/12*1000ms)，与现有p0/p2/scoring.py口径一致。瞬闪、遮挡、缺hidden/removed结束证据或未知可见性不能计可读成功。跨字幕逐必要片段审核，均达门后取最后必要部分合格区间的首次可见时刻；回溯该时刻，不额外加停留等待时长。WS和可读DOM覆盖分列。节点存在、首个汉字、MT首token、WS收到都不能代替。MLX分阶段测试显式同步，正式链路不插入改变调度的大量同步。

定义L=stable_correct_ms-source_end_ms，允许负值。分别报告：

- 语音起点和按钮启动到首条任意/正确/稳定正确中文。
- 全分母2/3/5/15秒句末覆盖，以及回放结束覆盖。
- 15秒窗内匹配项P50/P95及N；共同正确项配对P95及N。
- 全体P95，missing或超过15秒视为无穷；JSON写null和unbounded=true。
- 1–3秒短句从起点到可用中文P90，未命中按无穷。
- 漏错/过滤/迟到/渲染丢失的unit_id和原因。

固定nearest-rank，不事后切换插值法。正确匹配或共同正确不足20项，尾分位只能描述，不能用于正式R延迟胜负；D0 seed29基线18项使共同正确20项本来就不可达。欲测R延迟，必须在候选首次输出前冻结更大计时开发集和资格，不能跑完后换分母或主指标。

包内timing命令仅算数，不证明stable_correct标注、时间校准或DOM来源。必须同时保存未裁剪raw结果和15秒视图。

## 资源口径

每250ms采样，冷加载、暖机、正式输入和drain分开。每配置包含ASR、MT和它们的子进程；组件峰值不可相加伪造总峰值。记录播放only对照、Chrome/OS/功耗模式和后台干扰。

CPU使用每核100%口径，并报告CPU秒/真实音频秒；RSS与physical footprint同时保留，树RSS可重复计算共享页，比较固定同一种口径。默认2.5GiB资格固定为完整树热态`tree_physical_footprint_peak_bytes`，footprint不可得时为inconclusive，不换RSS凑通过。P3尚未执行的R_memory在本次修订预登记为tree_physical_footprint_peak_bytes，RSS仅辅助；不修改P2正式R的paired_common_correct_dom_p95。逐个采样时刻汇总完整目标进程集合再取峰值，不相加各组件独立峰值，也不称系统唯一物理页精确去重。

热态统一为warmup完成后、第一包正式输入前开始，至正常drain和在途native/MT完成；冷加载/暖机另报且仍受12GiB硬停。WS集合为本run server/native/Hy及可归属子进程；Chrome集合另包含本run隔离浏览器进程，播放only单列。基线/候选只在相同surface、阶段和采样定义配对，不能跨WS与Chrome或拿峰值相减称模型独占。缺footprint、必需进程样本或归属不明时inconclusive，不能用RSS补过。

GPU为整机活跃率，包含视频解码/渲染；ANE采不到则null而非0%；模型文件大小不等于内存。能量需现有权限及有效测量，不能请求不到后改成CPU%作为同一指标。队列同时记PCM真实秒数/最老年龄和MT条数/最老年龄。GPU竞争只能在ASR单跑、MT单跑、联跑的有界配对证据后讨论，不能预设命令队列阻塞。

## 语义与评审

每个冻结unit用correct/partial/incorrect/missing/uncertain，仅correct计覆盖；保留原文、raw中文、过滤后中文和实际DOM。否定、数字、专名、人物关系等关键错误不被流畅中文抵消。

新增关键错按错误内容判定，即使B0原来missing，候选新增反义也算新增关键错。保护项在输出前按同轮B0和已冻结语义规范确定，不事后删。每轮给出restored/lost、新关键错与保护项列表。

独立评审隐藏模型与顺序，核查全部收益、损失、关键错及抽样正确项；AI可辅助对齐但不可代称人工复听。15项争议保持保守/宽松视图，保守过线也只叫provisional。不能由CER单独晋级，也不能由ASR汉字错直接推定中文必错。

D0/D1只是开发证据。独立保留集一旦用于调参便转为回归集；需要另取未看过材料才能再次称holdout。
