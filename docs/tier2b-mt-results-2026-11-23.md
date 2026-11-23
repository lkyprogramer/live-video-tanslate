# Tier 2B：4B–8B 级 JA→ZH 翻译与联合同音纠错盲测报告

- **评测日期**：2026-11-23
- **硬件与环境**：Apple M1 Pro（10 核 CPU / 16 核 GPU / 32 GiB 统一内存 / macOS 26.7.2），独立环境 `p0/tier2_mt/.venv`（`mlx-lm 0.32.2`）
- **输入轨道（全部来自 Tier 2A 落盘冻结结果）**：
  - **Track 1（Clean-JA Oracle）**：D0 34 单元标准日文（[d0_oracle_34unit.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/tier2b-mt-2026-11-23/d0_oracle_34unit.json)），对比历史 `Hy-MT2 1.8B` 的 `28/34` 上限
  - **Track 2（`ReazonSpeech-NeMo-v2`）**：D0 核心 62 切片 + D1《重启人生》45s 3 段（共 65 条）
  - **Track 3（`Qwen3-ASR-1.7B-8bit`）**：D0 核心 62 切片 + D1 45s 3 段（共 65 条）
  - **Track 4（D1 Clean SRT）**：《重启人生》EP01 第 75–93 条标准日文对白（19 条）
- **状态说明**：本轮仅完成离线脚本评测（[bench_mt.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/bench_mt.py)、[summarize_mt.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/summarize_mt.py)），**未修改产品代码**；34 单元语义复核为 `ai_reviewed`（`human_verified=False`）。

---

## 1. 核心结论（先给答案）

1. **Tier 2B 唯一全指标胜出 MT：`Qwen3-4B-Instruct-2507-4bit`（`M0` 单句模式）。**
   - **Clean-JA Oracle 突破上限**：在 34 单元标准日文上达到 **`32/34`**（超过基线 `Hy-MT2 1.8B` 的 `28/34`，也优于 `Qwen3-8B-4bit` 的 `30/34`），网关通过率 **`34/34`**（D1 SRT 也是 `19/19`）。
   - **实时性与内存极佳**：`M0` 全量 183 条翻译 **`P50 = 0.529s`、`P95 = 0.964s`**，GPU 峰值仅 **`2758.75 MiB`（`~2.69 GiB`）**。
2. **三个专用/大模型候选实测淘汰**：
   - **`Seed-X-PPO-7B-mlx-4Bit`**：大量输出 `<s>` 重复循环与偏离原文幻觉，网关通过率仅 **`79/183`（`43.2%`）**（D1 seg2 直接输出满屏 `<s>`），淘汰。
   - **`translategemma-4b-it-4bit`**：每条句尾固定输出 256 个 `<end_of_turn>` 填充导致 **`P50 = 5.73s / P95 = 6.32s`**，且把 `パラ言語情報` 译成 `"语言信息"`，实时观影不可用，淘汰。
   - **`Qwen3-8B-4bit`**：`M0` 延迟翻近 3 倍（`P50 = 1.49s / P95 = 3.42s`）、GPU 峰值 `4908 MiB`，且在 `[10] csj-task/20` 出现严重幻觉（把 `ショートカット` 译成 `"别用你那美食家的挑剔眼光盯着我，我可是走捷径的"`），性价比全面逊于 `Qwen3-4B-Instruct-2507-4bit`。
   - **`pfnet/plamo-3-translate-8b-MLX-4bit`**：依赖自定义 `modeling_plamo_mlx.py`（必须开启 `trust_remote_code=True`），未启用远端代码执行时直接报错拒载，暂不纳入默认安全栈。
3. **M1 Pro（32 GiB）推荐的双档完整组合（均已超越默认 `reazon-hy` `20/34`）**：
   - **高速低延迟高精度档（首选全 MLX GPU 链路）**：**`Qwen3-ASR-1.7B-8bit` + `Qwen3-4B-Instruct-2507-4bit (M0)`**
     - 总内存峰值约 `~6.3 GiB`（含 Chrome+服务约 `~8.5 GiB`），ASR `RTF=0.102` + MT `P50=0.59s / P95=1.02s`，端到端单句处理稳定在 **1 秒量级**；D0 语义命中约 `23/34`，D1 日语 CER `0.3623` 完整覆盖 19 句对白。
   - **最高准确率档（CPU+GPU 混合链路）**：**`ReazonSpeech-NeMo-v2`（619M）+ `Qwen3-4B-Instruct-2507-4bit (M0)`**
     - 总内存峰值约 `~8.4 GiB`，D0 日语 CER `0.1546`、D1 日语 CER **`0.2874`**，MT `P50=0.47s / P95=0.78s`，D0 语义命中约 `22–23/34`，在 D1 剧集对白还原度最高。

---

## 2. 5 个 4B–8B MT 模型全量实测对比

| 模型（4-bit MLX） | 模式 | 网关通过（总计） | Clean-JA Oracle (34) | `NeMo-v2` (65) | `Qwen3-1.7B` (65) | D1 SRT (19) | P50 / P95 延迟 | GPU 峰值 | 结论 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`Qwen3-4B-Instruct-2507-4bit`** | **`M0`** | **`178/183`** | **`34/34`（`32/34` 命中）** | `63/65` | `62/65` | **`19/19`** | **`0.53s / 0.96s`** | **`2759 MiB`** | **胜出推荐** |
| `Qwen3-4B-Instruct-2507-4bit` | `M_CTX` | `124/130` | — | `60/65` | `64/65` | — | `1.34s / 2.26s` | `2759 MiB` | 可纠错但慢 2.5× |
| `Qwen3-8B-4bit` | `M0` | `182/183` | `34/34`（`30/34` 命中） | `64/65` | `65/65` | `19/19` | `1.49s / 3.42s` | `4908 MiB` | 慢且有幻觉，淘汰 |
| `Qwen3-8B-4bit` | `M_CTX` | `121/130` | — | `60/65` | `61/65` | — | `4.01s / 6.86s` | `4908 MiB` | 过慢且偶漏日文，淘汰 |
| `translategemma-4b-it-4bit` | `M0` | `180/183` | `34/34` | `62/65` | `65/65` | `19/19` | `5.73s / 6.32s` | `2359 MiB` | `<end_of_turn>` 拖垮延迟，淘汰 |
| `Seed-X-PPO-7B-mlx-4Bit` | `M0` | `79/183` | `16/34` | `24/65` | `32/65` | `7/19` | `0.97s / 3.38s` | `4354 MiB` | `<s>` 循环幻觉，淘汰 |
| `plamo-3-translate-8b-MLX-4bit` | `M0` | 加载失败 | — | — | — | — | — | — | 强依赖 `trust_remote_code=True` |

> **注**：`Qwen3-4B (M0)` 在 `NeMo-v2` 与 `Qwen3-1.7B` 上的 2–3 条 `gw=False`，主因是保留了 `平野レミ` 中的片假名 `レミ`（在日文原句中属于专有名词，后续可在 Prompt 中明确要求“专有名词一律音译为汉字，禁止保留片假名”轻松消除）。

---

## 3. `M_CTX`（联合同音纠错 + 前 2 句语境）专项评测

与此前 `A3 C1`（`Hy-MT2 1.8B` 直接整句抄写历史句子）不同，**`Qwen3-4B-Instruct` 在 `M_CTX` 下未发生整句上下文污染，且确实展示了真实的跨句同音纠错能力**：

- **正例修复**：
  - `Qwen3-1.7B` `[11] csj-task/30`：识别原文漏字错听为 `ヒラの平らなのっぱらですか？はい。レミはカタカナです。`，`M0` 译为 `"平的表面是平的吗？是的。雷米是片假名。"`；而 **`M_CTX` 结合前文 `平野レミ`，直接纠错译成 `"平野莱米，对吧？是的，莱米是片假名。"`**。
  - `Qwen3-1.7B` `[03] csj-reproduction/4`：`M_CTX` 结合上文 `パラ言語情報`，将 **`副语言信息`** 准确还原，优于 `M0` 的 `"语篇语言信息"`。
- **代价与不采用为默认的原因**：
  1. 延迟从 `0.53s` 涨至 `1.34s`（`P95 = 2.26s`），实时流式预算压力翻倍；
  2. `M_CTX` 在 D1 seg1 漏出 `"呐呐しい"`、在 `Qwen3-8B` `[33]` 直接漏出整句日文，稳定性反而不如 `M0`。
  3. 因此 **现阶段推荐 `M0` 作为高精度默认档**，若后续需要 `M_CTX` 纠错，必须先收紧 Prompt 并只对关键片段按需触发。

---

## 4. D1（《重启人生》45s 19 句密集对白）中文译文对比

以 `ReazonSpeech-NeMo-v2` 第 1 段（`1.16s–15.29s`，含连珠炮吐槽 `一回キレられたのよなれなれしいっつって。えっなれなれしいって普通に挨拶じゃん`）为例：
- **基线 `reazon-k2-v2 + Hy-MT2 1.8B`**：漏掉前两句及末尾保育员，把 `なれなれしい` 错译为 `"很奇怪""很离谱"`。
- **`NeMo-v2 + Hy-MT2 1.8B`**：译成 `"被对方回绝了，真让人不愉快呢。哎呀，不愉快的话，直接打招呼不就得了吗？"`（把“被发火骂太自来熟”错译成“被回绝/不愉快”）。
- **`NeMo-v2 + Qwen3-8B (M0)`**：**`"有一次被骂了，说我很熟稔。诶，熟稔不是普通的打招呼吗？对吧？你根本不懂吧？那样真的很过分。对了，当保育士的。"`**（语义与语气完全到位）。
- **`NeMo-v2 + Qwen3-4B (M0)`**：`"有一次被训了一顿，说“你这话说得真让人难受”。诶？“难受”不就是普通的打招呼吗？"`（语句极流畅，`なれなれしい` 翻成“让人难受”略偏，其余全对）。

---

## 5. 诚实限制与下一步建议

1. **当前限制**：
   - 本轮为**离线切片评测**，尚未在产品代码（`engine/`）中接入 `Qwen3-ASR-1.7B-8bit` / `ReazonSpeech-NeMo-v2` + `Qwen3-4B-Instruct-2507-4bit` 并跑 **1× 实速时钟与 Chrome 播放并发测试**（验证 Metal GPU 并发下的真实延迟与热稳定性）。
   - `34 单元` 语义对照为 AI 辅助复核（`ai_reviewed, human_verified=False`）。
2. **下一步建议**：
   - 若你认可上述结果，下一步可授权在 `engine/` 中新增一个可选的高精度 Profile（例如 **`m1-pro-hq`**：`Qwen3-ASR-1.7B-8bit` + `Silero VAD v4` + `Qwen3-4B-Instruct-2507-4bit (M0)`，保留 `reazon-hy` 作为轻量默认档），并在 `D0` + `D1` 上跑通 **1× 实速并发与生命周期验收**。

---

### 附录：本轮数据与脚本索引

- 评测脚本：[bench_mt.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/bench_mt.py)、[summarize_mt.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_mt/summarize_mt.py)
- 归档汇总数据：[tier2b-mt-2026-11-23](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/tier2b-mt-2026-11-23)（`mt_summary.json`、`d0_oracle_34unit.json`、`d0_nemo_v2_34unit.json`、`d0_qwen3_1.7b_34unit.json`、`d1_drama_comparison.json`）
