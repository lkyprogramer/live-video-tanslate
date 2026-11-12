# 04 R2T2：有上限的 Mac source/import/load/parity spike

## 决策与边界

本章是规划，不是 Mac 编译／实测成功记录。正式主配置预先固定 **Confucius4-R2T2，full `stream_llama`，Q4_K_M decoder + Q8 mmproj，160 ms，U=1，S0，`streaming_transcribe_no_reset` 的 W16O8（16 s 阈值／丢最早 8 s）**，不由 VAD 强制 reset。基础 Qwen3-ASR-1.7B 仅作同后端、同量化、同块长、同 U、同状态策略的机制控制，不是第三条主线。论文日语 FLEURS 的 36.07%→8.66% CER 只能支持后训练值得验证，不能算本项目基线收益。

冻结产品输入为 16 kHz、单声道 s16le、200 ms 到达包；adapter 只能从已到达样本重分块，不改变采集链路。160 ms 是模型喂入块长，不是计算耗时或中文端到端延迟。本轮驱动显式锁首轮320 ms、之后160 ms；它来自example的调度，不是U1自动设置，U1是独立的隐藏尾token策略；200 ms 到达包还会造成实际可见时刻量化，必须按真实样本时钟记录，禁止预取补足。

本章不修改 main，不替换默认 Reazon+Hy；不使用云、未来音频、OCR 或原字幕。不同实验组及其下载／构建不重叠；同组播放、ASR、Hy 与采样按生产调度并行，不把串行要求错误施加于真实联跑。预算为至多三个主候选配置，加另一个预登记条件消融，合计最多四个 `experiment_id`；S1 与 RNNT 80 ms 竞争唯一条件槽，E2E 最多两个候选。基础 Qwen 控制不计候选但计成本，不自动获得 E2E。量化／chunk／U／状态策略衍生均算配置。ASR+MT 约 2.5 GiB 是低驻留目标，权重文件大小不能代替进程树驻留测量。

## 驱动身份与提交语义

registry同时锁language=Japanese、空context、lookahead160ms、unfixed_chunk_num=0、首320/后160ms、U1及rollback_punctuation=false。官方llama入口默认Chinese，必须显式覆盖。每步max_new_tokens首轮4；有新增定稿则下步2，无新增则加1且封顶4；finish_no_reset显式传max_new_tokens=4（沿用example的first_max_new_tokens），不得用None触发wrapper的4096默认。step_decode_budget_exhausted表示保留state等待下一音频块，不是session失败；会话总量上限、30s超时和持续无进展另外记录。上游driver调用standard streaming，本轮将该调度与no_reset/W16O8组合，须本机gate验证，不能叫官方已测整套recipe。

no_reset第二返回值已是全session累计last_fixed_text，外层只作前缀差分，滚窗裁的是内部chunk_text；再次把第二值按窗口拼接会重复文本。无词时标路径固定09章S0_UNTIMED_VAD_SNAPSHOT，不直接调用旧StreamCommitter。

EOF保持固定pin行为：finish_no_reset的buffer为空时只返回已定稿前缀，U1隐藏尾不自动解锁；有短尾时按pin再次计算。整块/短尾、空尾、重复finish均先stub。未定稿尾标withheld_tail，语义按missing/partial保留全分母，不将provisional晋升final，也不在看分后补一次推理解锁。将来改EOF另行预登记。

## 源码与工件锁

| 对象 | 固定值／动作 |
| --- | --- |
| R2T2 源码 | `72b81be7070095410b75ed2e11998e347f655218`，默认分支为 master，不用浮动 master 计分 |
| llama.cpp | `ad6c66839af3c5646fba8c6c2e2087a1e4e38948`；包装器引用非公开 mtmd helper，禁止自行升级 |
| R2T2 权重 | `netease-youdao/Confucius4-R2T2-GGUF` 的 Q4_K_M 与 Q8 mmproj；执行者先记录实际文件名、HF revision、SHA256、字节数 |
| 基础控制 | `Qwen3-ASR-1.7B` 的同后端同量化工件；记录其转换来源、命令及哈希，不把未量化 PyTorch 作为公平控制 |
| 许可 | 代码 Apache-2.0；权重 NetEase 自定义模型许可，先保存许可原文与适用结论 |

研究尚未固定 HF 权重 revision；未知 revision／哈希填 `null` 并标 `blocked_data`，不得填猜测值或 latest。正式运行前补齐统一 manifest，每次加载前核对；未锁定不得计分。

目标实现位置为 `p0/p3/adapters/r2t2.py`，统一接口、命令和输出 schema 服从 [09 章](09-runtime-integration.md)，不另造平行 signature。实验目录额外保留 `source-audit.md`、`build.log`、`otool.txt`、`parity.json`、`r2t2-state.jsonl`、工件锁及本地最小补丁；这些是实施阶段产物，本包不伪装已经存在。

## Task checklist：最多两个工程日，再做明确决策

两个工程日限定纯原生 source/import/load/parity 工程投入，不含排队／下载；同时记录总墙钟，不能靠排除等待掩盖总成本。

- [ ] **source gate**：逐项定位 `native_ext.cpp::generate_once`、`llama_native_backend.py`、`r2t2_asr.py` 和 CMake。确认完整音频编码→投影→解码链，不把 decoder-only demo 算加载成功。记录 Python、arm64、macOS、Xcode/clang、CMake 与 pybind11 版本。
- [ ] **Darwin build/import gate**：在隔离实验环境重建 arm64 pybind 扩展、llama/ggml/mtmd。当前 pin 有 Metal 源码；可评估 `GGML_CUDA=OFF`、`GGML_METAL=ON`，但这不是已验证的一键命令。检查 Mach-O 架构、实际链接库和 `otool -L`；构建目录绝对 BUILD_RPATH 可先用于本地 smoke，分发再处理 `@loader_path/@rpath`、dylib/metallib。Python 扩展本身的 `.so` 后缀并不等于 Linux ELF。
- [ ] **vLLM leak gate**：`pyproject.toml` 强依赖 `qwen-asr[vllm]`，且 streaming/finish 在 `max_new_tokens != None` 时导入 `vllm.SamplingParams`。只允许小型 DTO／backend 参数解耦并留补丁与测试；正式step与finish均显式传上述有界整数，DTO解耦后验证停止条件，不使用None分支规避导入。不得在 Mac 通过安装整套 CUDA/vLLM 偷渡依赖。
- [ ] **load/parity gate**：G0前冻结reference backend、同量化权重/frontend、输入、语言、驱动/状态策略、比较层级与容差，再做静音/短日语/低声起始/数字/否定/专名探针。同工件跨平台回归、adapter对直接native调用的接线一致性、非量化诊断、最终中文质量四类分开。只在同口径数值/前缀约定实际违背时判blocked_runtime；Q4+Q8对vLLM/hybrid/FP16不逐token相等不能单独归为Mac部署错误。缺可比reference为blocked_data/parity未证，可保留build/load诊断但不进正式质量。所有诊断留在原16工程小时与父路线运行预算内，不展开新量化矩阵。
- [ ] **U1 contract gate**：逐次记录 upstream 第一与第二返回值、隐藏尾 token 数、实际输入 SHA256／样本区间、state/epoch。核对第二返回值仍符合该pin的全session累计前缀，再差分为一次delta；禁止直接上屏 `state.text`。EOF、空返回、异常、取消、重置不得重复发旧 token。
- [ ] 在两个工程日到期时填写继续／停止决定。只有 source/import/load/parity 与 W16O8 session 累计 prefix 映射均成立才申请后续短流；若 full llama no_reset 不支持或两天内无法保有映射，标 `blocked_runtime`。不自动切每 VAD reset、无限累积或扩大矩阵。

## 两种流状态不能混写

当前 GGUF `generate_once` 先 `llama_memory_clear`，再重编码传入音频；embedding 路径也清 decoder KV。这是**当前实现**，不是 LSP 算法必然要求。论文缓存设想不能当已实现性能。

标准 `streaming_transcribe` 累积当前 state 的音频，官方 example 对单个 WAV 初始化一次 state；它不是“必然累计整个人生会话”。该标准 API 仅用于 **G0 短样一致性诊断**，不是正式流式配置的后备策略。正式使用 `streaming_transcribe_no_reset`：16 s 阈值移除最早 8 s，并调整 chunk_text；其首次文本裁切硬编码依赖首5120样本（320ms）、后续2560（160ms）的输入调度，不能用全程160ms替代。首轮就须检查 16 s 前后和连续多次裁切的丢词、重复及时间归属。

按 [09 章](09-runtime-integration.md)，外部 session 的累计 committed prefix／source_id 水位不得随内部滚窗清零；维护窗口偏移与已提交 token 映射，VAD 仅用于 S0 提交策略，不强制重置声学状态。EOF／取消／seek 是显式生命周期事件，不能伪装成每段 VAD 重置。若需变更状态策略，属于未获预算的新配置，必须另行审批；唯一条件槽仅限registry的S1或RNNT80ms，不可自行挪用。不得悄悄退回 G0、无限累计或按 VAD reset。

`stream_llama_hybrid` 不是“full llama 换 device=mps”：其 encoder 依赖 qwen_asr 的 vLLM-native 类、distributed/config、Gloo 初始化和 CUDA/BF16 默认值，且 decode 侧仍加载 mmproj。首轮不做 hybrid。若 full llama 的安静起始遗漏可复现，保留证据，不自动移植 encoder。

## 短流、失败用例与验收

通过上面 gate 后，只做预注册短流与基础控制成对运行；先 ASR 质量，再现有 Hy，最后决定 E2E 席位。记录 `visible/consumed` 样本水位、输入块哈希、首轮额外 lookahead、compute、队列最老年龄、日文 commit、MT 等待和中文 display 时刻。所有累计编码量、n_ctx/n_batch/n_ubatch、线程、FlashAttention、KV/图/scratch 分配进入日志；默认 32768 context、8192 batch、32 线程不能拿来推断低内存。

必须包含：200→160 ms 重分块余数、静音不乱发、低音量句首、长否定后置、U1 token 反转、EOF 不足一块、取消重建、超 token 上限，以及必测 16/8 s 滚窗和跨窗累计 prefix。日文 append-only 只是源侧验收；Hy 仅消费提交原文，中文提交门独立保证已显示条目不回写。对外 revision 次数必须为 0；延迟评分含漏词／漏译，不只算匹配成功 token。

质量、中文可用性和长流阈值使用统一评分章，不在本章另降门槛。实时回放必须无持续积压；资源按 ASR+MT 实测。超低驻留目标单列 `failed_resource`，不能以机器有 32 GiB 判通过。若必须深改 encoder 图、移植私有 kernel、实现新缓存算法或 hybrid MPS，立即停在 `blocked_runtime` 并另行审批；不吃掉其余候选预算。

结果严格区分：未跑为 `run.status=not_run`；缺人审为 `needs_human`；工件／来源锁或参考工件缺失为 `blocked_data`；编译、导入、算子、加载、实际部署／转换 parity 或 W16O8 映射阻塞为 `blocked_runtime`；有效运行且可靠参考下质量门失败才是 `failed_quality`。`unscored` 仅可作 `score_status`，不作全局 `run.status`。资源／延迟失败另附维度，不能改写成模型语言能力失败。

## 原始来源与核查锚点

- [R2T2 固定提交](https://github.com/netease-youdao/Confucius4-R2T2/commit/72b81be7070095410b75ed2e11998e347f655218)；同提交下 `r2t2/r2t2_asr.py` 的 `streaming_transcribe`、`finish_streaming_transcribe`、`streaming_transcribe_no_reset`。
- [native_ext.cpp](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/native_ext.cpp)：`generate_once`、`prefill_only_with_embedding`、`PYBIND11_MODULE`；[构建说明](https://github.com/netease-youdao/Confucius4-R2T2/blob/72b81be7070095410b75ed2e11998e347f655218/r2t2_llama/README.md)。
- [固定 llama Metal](https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/ggml/src/ggml-metal/CMakeLists.txt)、[mtmd helper](https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/tools/mtmd/mtmd-helper-common.h)。
- [模型卡](https://huggingface.co/netease-youdao/Confucius4-R2T2)、[GGUF 工件](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF)、[论文 v1](https://arxiv.org/html/2611.06043v1)。[原始只读复核](../research/r2t2-mac-feasibility.md)与[扩展结论](../research/expanded-review.md)未含本机编译／实测。
