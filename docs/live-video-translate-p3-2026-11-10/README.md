# M1 Pro 日语实时中文字幕：P3 探索与验证执行包

研究截止：**2026-11-10（北京时间）**。项目基点：`live-video-tanslate @ 77e24c3e490a11721041a40ad4bd203f51f84039`。格式沿用上一轮：完整单文件方案＋分章Markdown＋JSON配置/Schema＋证据脚本/测试＋AI提示词。

本包将最新发现变成可执行的有界研究，不承诺有赢家。两条优先主线是 **R2T2稳定前缀后训练** 和 **123M日语cache-aware RNNT**。它们值得补测，但尚未证明在本机上运行正确、能超过Reazon+Hy，或能满足约2.5GiB低占用目标。

本目录经主代理及独立Astra xhigh结合本机现状复核修订，见[问题与修订记录](REVIEW-CHANGES.md)。本轮只修改本包规格与文档辅助工具，**未修改产品实现、未安装/下载新模型、未运行M1模型性能实验**。原包自带审查回执与本次本地验证分开，包内脚本验证不等于模型验证。

## 从哪里开始

1. 给执行AI：[prompts/EXECUTOR.md](prompts/EXECUTOR.md)。实施必须另有用户授权，接收ZIP本身不等于授权运行全部实验。
2. 一次读完：[P3-AI-EXECUTION-PLAN.md](P3-AI-EXECUTION-PLAN.md)。需要按任务推进则从[03分阶段任务](docs/03-phase-tasks.md)开始。
3. 冻结规则：[00合同/基线](docs/00-contract-and-baseline.md)、[08门槛](docs/08-gates-and-stop-rules.md)、[config说明](config/README.md)。
4. 接口实现：[09原生接口](docs/09-runtime-integration.md)；中断恢复：[10执行/恢复](docs/10-ai-execution-and-recovery.md)。
5. 独立审查：[prompts/REVIEWER.md](prompts/REVIEWER.md)；恢复提示：[prompts/RESUME.md](prompts/RESUME.md)。
6. 本包已验证内容及限制：[12交付验证](docs/12-delivery-validation.md)。

## 固定探索顺序

| 顺序 | 首配置 | 先回答的问题 | 时间/预算门 |
| --- | --- | --- | --- |
| G0 | 当前Reazon+Hy | 资产、分母、基线、工具和人工参考是否可复现 | 1工程日 |
| 1 | R2T2 full llama、160ms/U1、S0、W16O8 | Mac构建/数值及稳定前缀成立吗；真实中文是否改善 | source/import/load/parity最多2工程日 |
| 2 | 日语123M M1-6 RNNT、480ms、S0 | 前端、缓存和RNNT路径是否完整；质量/资源是否有利 | 运行/保缓存导出最多2工程日 |
| 3 | Omni300M v2 INT8为默认备选 | 独立小CTC家族的短句质量是否有收益 | 备选正式席位最多1个 |
| 条件 | S1提前稳定整句，或RNNT80ms | 是否改变已量到的等待/右文代价 | 全局仅1个追加正式配置 |

第三席位可在输出前改选Fun-ASR-MLT或经正确假名语义门通过后的Kizuna，不能三个都跑。上限为**3主候选配置＋1条件消融，共4个正式experiment_id，最多2个进入E2E**。不同实验组串行；同一组的播放、ASR、Hy和采样仍按生产调度联跑。

现有P2代码已经实现，不重新从零写。P3新增的候选在线MT、1×、生命周期和设备适配仍待实现；不能把旧CLI显示某参数理解为支持任意新模型。

## 保留的硬边界

- 仅M1 Pro本地已到达音频，无原字幕/OCR/未来预读/云或4090卸载。
- 固定200ms PCM传输；模型chunk、右文、VAD窗口和中文延迟分别度量。
- 对外中文不可回写；日文内部临时尾可以隐藏，但不能偷偷送MT。
- 当前基线20/18 of34、1×5秒19/17是历史参照；15项待听审不因新包而变人工已审。
- 默认低占用资格使用完整树热态physical footprint峰值2.5GiB，缺测为inconclusive。
- 新的`research_R_memory`只做探索，不替代旧正式R延迟指标，不直接授权默认替换。
- unknown revision/hash一律null+blocked；失败、未测、接口阻塞、质量失败分别报告。

## 现在可以运行的工具

在解压后的本目录运行，Python3.10+标准库即可。四个示例都是合成数据：

```bash
python3 -B -m unittest discover -s tests -v
python3 -B scripts/p3_evidence.py timing examples/synthetic-timing.json
python3 -B scripts/p3_evidence.py prefix-audit examples/synthetic-prefix-valid.json
python3 -B scripts/p3_evidence.py prefix-audit examples/synthetic-prefix-invalid.json
python3 -B scripts/p3_evidence.py check-lock examples/synthetic-lock-pending.json
python3 -B scripts/p3_evidence.py check-lock config/model-lock.json
```

预期退出码依次为 **0、0、0、1、2、2**。故意无效前缀和未锁定模型必须被拒绝；不要为了“全部绿灯”删掉这些反例。所有helper输出固定`release_eligible=false`。它们只做算数/部分结构校验，不替你执行模型、判断翻译对错或发布。

正式选中材料已填写后可使用 `check-lock <本次材料锁> --root <本次材料根>` 验真实文件bytes；该命令不下载、不验证许可证、不验证revision来源映射。完整前置检查见10章。

**`python -m p0.p3.cli`不是本包现成脚本。** 09章定义其未来接口；T02实现并通过测试后才能调用。不要对现有P2输入`--phase P3`。

## 文件布局

| 目录/文件 | 作用 |
| --- | --- |
| `docs/00–12` | 合同、研究结论、输入与预算、逐步任务、三条配方、评分、门槛、接口、恢复、来源、交付验证 |
| `config/` | 候选注册、模型材料模板、运行时/工件锁、统一评测合同 |
| `schemas/` | helper输入、run manifest与决策报告的JSON Schema |
| `templates/` | 假设、run、盲评、决策和批次报告模板，明确未执行 |
| `scripts/`、`tests/`、`examples/` | 实际可运行的离线辅助工具、测试及合成反例 |
| `prompts/` | 执行、独立审查、中断恢复提示词 |
| `research/` | 最新调研底稿、Exa查询/URL台账、源码审计及只读CLI证据 |
| `validation/` | 本包离线测试的真实输出，不是M1模型结果 |
| `MANIFEST.sha256` | 除其自身外所有交付文件的内容校验值 |

macOS可在解压根运行 `shasum -a 256 -c MANIFEST.sha256` 校验内容；Linux可用 `sha256sum -c MANIFEST.sha256`。ZIP不包含权重、音频、用户token、产品补丁或伪造的M1结果。

完整单文件方案由分章机械合并，分章/config/prompts是维护真源。维护后在仓库根用现有engine环境运行下列命令；整包检查另需现有jsonschema开发依赖，不安装或运行模型：

~~~sh
engine/.venv/bin/python -B docs/live-video-translate-p3-2026-11-10/scripts/validate_package.py --refresh
engine/.venv/bin/python -B docs/live-video-translate-p3-2026-11-10/scripts/validate_package.py --check
~~~

--refresh机械生成阅读副本、实际测试回执和清单；--check只读核对源/Schema/链接/清单。原包自带42项回执单列为历史，本次测试数及实际结果以validation/BUILD-VALIDATION.json和LOCAL-OFFLINE-TESTS.txt为准。JSON与脚本请保留目录结构读取。[相对P2的变化](CHANGES-FROM-P2.md)说明哪些旧任务已完成、哪些新探索优先级被调整。
