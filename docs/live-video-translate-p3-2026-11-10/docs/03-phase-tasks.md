# 03 AI 分阶段实施计划

**Goal：** 在不放宽M1 Pro、本地当前音频、最终中文不回写和低占用目标的情况下，用有限预算验证R2T2与123M日语原生缓存路线是否比当前基线有净收益；允许得出否定或运行阻塞结论。

**Architecture：** 既有P2证据与生产默认保持不变；独立 `p0/p3/` 适配器与实验backend，沿用200ms PCM／网关合同，原生ASR稳定提交经S0或单独登记S1进入现有Hy，统一因果和DOM证据后决定继续或停止。

**Tech stack：** 项目现有Python3.12、sherpa-onnx／MLX Hy、WebSocket/Chrome扩展；R2T2为固定llama.cpp+pybind的隔离arm64工程；日语RNNT为精确NeMo／cache ONNX ORT路径。此包辅助脚本是Python3.10+标准库，不是模型runtime要求。

**Spec：** [00合同](00-contract-and-baseline.md)、[02预算](02-experiment-design.md)、[09接口](09-runtime-integration.md)、[08门槛](08-gates-and-stop-rules.md)和config JSON。冲突时停止；不能自行挑更宽规则。

**Global constraints：** 先授权再实施；独立worktree；不改历史hash／main默认；串行模型；两主线+一个备选+全局一个条件配置；最多两个E2E；不下载私有模型、不自动push、不把待听审变人工已审。

**Review focus：** 原生缓存是否真实、模型/后端是否同一身份、R2T2第二返回值与滚窗账本、因果采样水位、中文不可回写、实际worker取消、全分母与匹配P95、P2正式R与P3内存探索的边界。

## T00 接收任务与建立隔离（不推理）

- [ ] 阅读README、00、08和EXECUTOR；确认用户当前授权是“实施本包”还是仅继续讨论。只有前者才做以下仓库变更。
- [ ] 在实际仓库运行只读 `git status --short`、`git rev-parse HEAD`、`git log -5 --oneline`；记录未提交改动，绝不reset/checkout覆盖用户文件。
- [ ] 对比77e24c3基点；若相关代码/评分变动，先输出repo-diff，不强拉旧版本到用户工作目录。新worktree固定置于/Users/luo/.codex/worktrees/<task>/，路径和分支由执行器确认不存在后创建；不在本文硬编码删除或覆盖命令。
- [ ] 新建实验run和独立环境计划，不动engine现有锁。收集硬件/OS/电源/Chrome/可用磁盘；检查许可与素材权限。
- [ ] 本包尚未在目标worktree时复制到docs/live-video-translate-p3-2026-11-10/，已在原位则核验后复用；记录保留生产资产的基点checkout及其engine/.venv绝对路径，并将BASE_ENGINE_PYTHON设为该已存在Python可执行文件。新worktree不会包含ignored的模型、profile或虚拟环境，不应先重新安装全部依赖。原始音频、模型或私有profiles继续忽略。

输出：`run-manifest.json`、`repo-diff.md`、`authorization.md`、`environment.json`。退出：权限不足为blocked_permission；dirty overlap不能规避则询问用户。此阶段不给模型质量结论。

## T01 复用证据与重建本轮对照（G0）

创建：`p0/p3/freeze.py`、`p0/p3/tests/test_freeze.py`。只读复用：`p0/p2/freeze.py`、`types.py`、`events.py`、`runner.py`、`realtime.py`、`scoring.py`。不修改历史收据。

- [ ] 先写测试：缺文件/hash、null必需revision、不同experiment参数、输入hash漂移、旧run覆盖、路径越界、假human_verified均失败；相同输入纯读恢复才成功。
- [ ] 实现P3自己的run和锁转换；旧P2锁仍单独留在 `history/`，新的选中材料锁为 `locks/materials.json`。
- [ ] G0只先做静态check。`p0.p2 ... preflight`可能推理，不能拿它作静态check。
- [ ] 核对D0的9/88/62/34和15 pending-listen、历史17/121是否有本地合法资产；缺少时blocked_data，不重新拼凑另一集却沿用分数。
- [ ] 在新候选输出前冻结D1至少20个不与D0重复的开发unit；仅使用已授权材料，保留暂定参考和不确定项，不打开test/。缺D1只阻断D1及其泛化结论，D0可继续有限研究，并在T06/T09明确该缺口。
- [ ] 已获得实施授权并完成锁后，在同轮环境复现B0固定切片/MT；ASR/MT原文逐条一致才继承对应判定。需要重评分时双seed均保留。
- [ ] 原生causal模式用本轮实际VAD生成的端点与B0；相同34语义分母。若基线模式或边界改变，使用新同轮B0，不能机械套20/18。

现有B0命令形态如下，**须在仓库根、旧B0所需资产与profile已就绪、独立新run已选定后**执行；变量必须由执行AI明确赋为安全路径，不能复制未设置的变量调用：

```bash
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" freeze --phase B0
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" probe --experiment B0 --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" probe --experiment B0 --mode fixed-text
engine/.venv/bin/python -m p0.p2.cli --run-dir "$P3_B0_RUN" score --experiment B0
```

这些旧B0命令在仍保有engine/.venv、私密profile和PCM的基点checkout运行，P3_B0_RUN使用其p0/evidence/下全新目录；P3 worktree只读引用该新控制run的绝对路径和hash。候选环境另建且不写生产环境；不因worktree缺ignored资产把清理后的缺文件写成模型失败。这里ID是B0；B1+阶段的控制才叫B0-CURRENT，不能混用。若当前checkout无法原样调用，保存准确错误并在P3实现显式包装；不得改旧receipt使其“通过”。G0总预算1工程日；模型资产下载与人工等待另记，不自动重开计时。

## T02a 最小离线接口与安全探针底座

创建：`p0/p3/{types.py,cli.py,native_runner.py,commit_bridge.py,resources.py}`、`p0/p3/adapters/base.py`及对应最小测试。此时native_runner仅做离线因果包驱动和受控stub，不做完整在线backend/server。T01+T02a共同计入G0的8工程小时；到期报告阻塞，不能先无限搭框架再发现模型无法加载。

- [ ] RED：运行新增无模型测试，确认失败来自未实现行为，不是缺真实权重。
- [ ] 实现09类型、三个音频水位、源前缀账本和S0的最小stub测试；S1代码晚于父候选质量前提，不先扩功能。
- [ ] CLI只先交付check/freeze和能力探针分派；未实现的在线子命令返回not_implemented，不先把help当功能。
- [ ] CLI严格分静态check与capability；phase/ID白名单、预算与required锁先检查，不允许导入模型产生隐式副作用。
- [ ] 复用现有资源采集，为短capability补12GiB/持续压力/30秒native调用守卫；保持单owner、真实返回前不并发启动第二实例。
- [ ] GREEN：最小接口和P2合同回归、独立review通过后，才准备T03/T04环境。完整WS/在线MT/DOM集成留给T02b，未胜出不投入。

未来实现后验证命令（目前仓库没有P3，不能立即运行）：

```bash
"$BASE_ENGINE_PYTHON" -m pytest -q p0/p3/tests
"$BASE_ENGINE_PYTHON" -m pytest -q p0/p2/tests
"$BASE_ENGINE_PYTHON" -m pytest -q engine/tests/test_gateway.py
"$BASE_ENGINE_PYTHON" -m p0.p3.cli --help
```

上述gateway测试在T02b产品合同对接后再跑；T02a运行其实际已实现的测试，不以“尚未实现”测试被跳过来宣称全接口完成。若pytest并非当前test环境已有依赖，使用该仓库既定test命令并记录，禁止为一条示例无条件升级engine。`--help`验证只是接口，不是能力成功。

## T03 R2T2 有界可行性

创建：`p0/p3/adapters/r2t2.py`、`p0/p3/tests/test_r2t2_adapter.py`；详细配方04章。

- [ ] 锁source、llama子依赖、decoder/mmproj以及基础Qwen机制控制；先看许可、已知依赖障碍和内存默认参数。
- [ ] 小型Darwin构建/导入解耦；不做hybrid迁移。2工程日内必须交代source/import/load/parity四门。
- [ ] 测试已累计last_fixed_text到一次delta的差分、Japanese语言、首320/后160与2..4步预算、整块/短尾EOF、内部reset和正式W16O8的16/8边界；S0_UNTIMED_VAD_SNAPSHOT按09章，不虚构词时标。
- [ ] 当缺参考输出时不强称数值等价；记录可验证部分和缺口。无法建立运行正确性则不转正式质量。
- [ ] 通过后固定R2T2 160/U1/S0，跑causal-stream质量，再将其真实原文送Hy两seed；基础Qwen同条件只做机制诊断。

输出：build/parity/来源证据、逐步重算与KV日志、两seed语义包。Q失败停止，或仅在符合02章条件时申请唯一S1槽；资源超限按02章停止。不能为了证明R2T2优势选择一个比B0弱的基础Qwen分数作产品对照。

## T04 123M 日语cache-aware RNNT

创建：`p0/p3/adapters/japanese_cache.py`、`p0/p3/tests/test_japanese_cache.py`；配方05章。

- [ ] M1-6、480ms、RNNT先锁唯一主配置；确认这是论文编号不是Apple型号。
- [ ] 验证NeMo参考及原始PCM因果frontend（跨包预加重、STFT支持域、feature-ready水位与同前缀不同未来反例），再保缓存运行/导出；填exact tensor contract。CPU/MPS/ONNX只能选择实际可行路径，不自动并行三栈。
- [ ] 连续至少两块证明cache读写；对照清零反事实；RNNT predictor/joint/blank/停止行为均检查。仅CTC成功不能晋级。
- [ ] 2工程日后给出blocked或可继续，停止深度框架开发。
- [ ] 通过后causal-stream + 同Hy两seed；记录低驻留探索R_memory和Q分别怎样判定，不把小文件当低内存证据。
- [ ] 只有主配置通过相应前提、并且唯一条件槽空闲时，预登记80ms；与S1二选一。

输出：tensor契约、转换命令与hash、前端/logit/token parity、缓存轨迹、真实质量与资源。CC-BY-NC的商业许可问题不能因测试好而忽略。

## T05 次候选只选一个

创建：`p0/p3/adapters/secondary.py`、`p0/p3/tests/test_secondary.py`；配方06章。

- [ ] 在任何该候选输出前将registry中secondary选择固定；默认Omni300M v2 INT8，另两项未选为not_run。
- [ ] 若正确假名→Hy语义门不通过，不加载Kizuna；其失败不是声学模型失败。若选Fun-ASR-MLT先读取既有证据、确认是否已足够，不重跑已失败配置。
- [ ] 主体只做4小时工程+2小时质量预算。来源不等价、缺日语、权重private或精度回落均停止。
- [ ] 通过同质量门才允许争取最多两个E2E之一；不会因为小模型就跳过关键语义错误。

## T06 盲评与正式实时入口

创建：`p0/p3/scoring.py`、`p0/p3/tests/test_scoring.py`及`p0/p3/decisions.py`的离线判定分支；T02b再补在线判定。复用旧单位/判定格式时显式记录版本。

- [ ] 隐藏模型名和输出顺序；冻结分母。逐项对照ASR、raw/filtered中文，判定restored/lost/new-critical/protected-loss。
- [ ] 15 pending-listen维持provisional；缺人工时间或语义不能用AI签字补齐。只允许标“暂定研究晋级”，不出默认替换结论。
- [ ] 按08章读Q、正式R_latency、探索R_memory；任何路线不得事后改主指标。所有失败项和未完成项保留。
- [ ] 达研究入口的候选最多选两个；先单列D1与同轮B0结果，缺D1则保留数据阻塞并限制为D0研究，不宣称泛化。按预登记目标与风险选择，不按最漂亮单项。R_memory此时只可标quality_entry_eligible，整链内存尚未测就不能标路线pass。若0个通过，直接T09结案。

## T02b 仅为合格候选补在线整链

这是T06之后才执行的条件任务，不是T03之前的固定成本。整批共8工程小时，包括最多两个E2E配置的薄对接；到期仍需深改通用运行栈则blocked_runtime并请求新范围。

创建 `p0/p3/backend.py`、`server.py`，完成 `native_runner.py`、`resources.py`、`decisions.py` 在线分支及09章生命周期测试；复用既有gateway而非重做服务。

- [ ] RED：在线队列、单owner、取消封sink、在途结果、真实返回后释放lease、满队列断线、重复finish和epoch隔离。
- [ ] 实现真实WS接纳/消费、在线Hy、两条独立不可回写账本、8秒drain；client送出不能伪装accepted。
- [ ] 完成资源/队龄观测和持续压力守卫；先stub反例再真实native短取消，不在未证回收安全时长跑。
- [ ] 实现09中的replay/accept分支，验证help与已实现行为相符；旧P2/gateway回归以及P3完整测试通过。
- [ ] 独立review确认可进入T07；本阶段通过不等于目标机质量或资源过线。

实际执行顺序是T00 → T01/T02a → T03/T04/所选T05 → T06 → 合格者T02b → T07/T08 → T09。分支之间可以单独停止，不能因任务编号把昂贵在线工作提前。

## T07 1×、Chrome、工程生命周期

- [ ] 同一天相近环境做两轮配对，seed11先B0→候选，seed29候选→B0；同一轮中输入、视频渲染设置、负载、缓存预热均一致并记录。实验组串行，同组ASR+Hy按真实调度联跑。
- [ ] 先WS 1×验证实际accepted/consumed与MT，再Chrome真实tabCapture/DOM；二者分开报告，不能拿WS分数代替DOM。
- [ ] 做前后时钟校准和可读终点审核；missing/late保留全分母。重复真值/参考时标敏感性分析不得只报更好版本。
- [ ] 测暂停、seek、切源、关闭WS、busy、EOF短尾、过载、后台与全屏；权限拒绝就blocked_permission，不绕过。
- [ ] 资源ASR+MT完整树同驻留；记录各阶段、队龄、native重算/缓存、长句增长，不加各组件单跑峰值。
- [ ] 如某个延迟“改善”仅来自漏译更多或filter更多，failed_quality而非性能胜者。

运行命令使用T02b真正已实现且单测确认的P3 CLI；不能执行P2 candidate replay代替。每个run含完整原始日志和业务状态，不以shell exit0判断通过。

## T08 长稳与保留集（有门才做）

- [ ] 冻结最终唯一配置及代码hash，确认完整工程门、资源目标和许可；未过则不打开holdout。
- [ ] 独立holdout至少6来源/60项，含20短句；执行后不调参再报同一holdout。
- [ ] 17段121项历史回归与独立集分别验证，不能合并凑90%。现有34项不是完整产品验收集。
- [ ] 合格候选一次连续60分钟，不用多段重启拼成长稳；同session、相同模型生命周期，记录最后/最初热态10分钟增长。
- [ ] 所有硬门及人工检查过后，独立review仅能给release_candidate建议。用户明确选择前默认不动。

## T09 结案与恢复点

- [ ] 生成每候选决策、全局台账、收益—成本、待补证据和再开条件，未赢也算完成。
- [ ] 运行本包辅助工具只验证材料/算数，再由未来decisions和独立review检查业务门；两种报告分开。
- [ ] 存工件hash/补丁/许可/版本/命令/退出/时长；去敏后才考虑提交，提交和push仍需用户相应授权。
- [ ] 默认保留原路径，回收临时服务与已结束worker。不要擅删模型缓存/旧实验；可列出清理清单等待授权。

最终必须回答：①模型部署通了吗；②日文稳定源文本改善了吗；③同Hy的中文净收益多少；④实际1×/DOM何时出现；⑤完整资源与长稳代价；⑥哪里被硬件、实现、模型、等待语义或评分证据限制；⑦是否值得下一轮，以及触发新一轮的具体新证据。不能用“更多优化可期”替代这七项。
