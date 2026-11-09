# 09 · 实验运行时和产品接入

## 9.1 隔离层

第一轮只新增 `p0/p2/` 实验代码，不替换现有默认。每种候选依赖单独锁定；需要新MLX、transformers、Swift或CTranslate2时使用独立环境/worker。主runner不得在import时加载所有候选权重。

一个模型进程负责加载、暖机、顺序推理和关闭。PCM路径必须指向当前冻结资产/有界临时span；worker不能自行读取未来文件范围。原生流接口只传入已释放的样本；即使在同文件系统也不向其传整段可预读路径。

worker控制通道只传受验证的ID、样本范围、参数与JSONL结果。生产公开API不新增任意文件路径、模型名、外部URL或自由prompt字段。下载与执行代码只来自模型锁中已审阅的固定来源。

## 9.2 背压和取消

产品维持一个active/cleaning会话、PCM队列64000 samples、入队等待1s、待MT4+在途1。原生模型自己新增的pending PCM、KV、raw特征同样计入状态和资源，不能绕过外层队列成为无界缓存。

取消时先令epoch失效、停止接纳并清空待提交工作；阻塞推理未返回时保留busy/cleaning。晚到结果记录discarded，不向旧WS/新会话提交。不可假定Python线程取消会打断Metal kernel。

正常EOF只发一次；真实短末包可以补成模型所需长度，但人工补零不增加accepted_samples。drain最多8s，到期明确终止，未输出项仍计漏译。重启模型或轮换session是显式实验行为，不能无日志重试并覆盖失败。

## 9.3 正式策略扩展（仅T10后）

当前config的local_pipeline只接受现有Reazon/HyMT类型，顶层另有whisperlivekit_ws profile；不能删除已有分支。新增模型至少需要：

1. 在 `engine/src/subtitle_service/config.py` 增加受限discriminated配置，精确参数和默认值来自胜出registry。
2. 在factory注册新ASR/MT或整体AST backend；AST不伪装成先调用ASR再翻译两次。
3. 实现backend的`warmup/available/open/aclose`与session的`accept_pcm/finish/aclose`，保留session_id、seq、样本范围与中文过滤。
4. 使用既有sink提交最终中文，内部JA/情绪/partial不得出现在caption payload。
5. 实验profile默认disabled；只通过显式选择进行验证，默认切换另按用户授权执行。

模型输出错误描述、特殊token、JSON残片不能送入翻译或展示当作内容。原有无汉字/含假名过滤继续保存raw/filtered计数；调整过滤本身需要独立实验，不能并入MT模型收益。

## 9.4 必要工程测试

只为真实风险写测试：短末包与补零越界、连续消费水位、队列满取消、旧epoch结果、EOF幂等、模型异常、无效中文、历史/cache跨视频泄漏、native状态增长/轮换边界。模型单次文本与fixture逐字相同不是充分质量验证。

运行受影响后端与扩展协议测试后，T11再跑后端全套和扩展类型/构建门槛。诊断日志不得混进可发布extension bundle。Chrome验收使用隔离profile；保留播放器原声和用户的既有设置。

不为完成本计划进行系统升级、关闭系统保护、强制kill不相关应用或清理用户模型缓存。能力不满足时输出blocked原因和可完成的其余结果。
