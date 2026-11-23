# Tier 2 ASR 盲测与 Smart Turn v3 评估结果报告

- **评测日期**：2026-11-23
- **硬件与环境**：Apple M1 Pro（10 核 CPU / 16 核 GPU / 32 GiB 统一内存 / macOS 26.7.2）
- **评测范围**：
  - **D0 基准**：沿用冻结的 [slices.jsonl](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/evidence/n0-next/slices.jsonl)（9 段 PCM 共 88 切片，其中 62 切片覆盖 [units.jsonl](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/evidence/n0-next/final-references/units.jsonl) 的 34 个评分单元）+ 10 秒静音 [silence.pcm](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/fixtures/silence.pcm)
  - **D1 密集影视对白**：《重启人生》第 1 集 `04:35.676–05:20.676`（45 秒 / 19 条日文 SRT 对白 / 3 个说话人，[material.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/evidence/d1-dialogue/material.json)）
  - **下游统一 MT**：`Hy-MT2 1.8B 4-bit`（固定 `seed=11`，沿用 `subtitle_service.app.chinese()` 网关过滤）
  - **状态说明**：本轮仅完成离线脚本评测（代码位于 `p0/tier2_asr/`，原始产物落盘于 `p0/evidence/tier2-asr-20261123/`），**未修改产品代码**；34 单元中文语义复核为 `ai_reviewed`（`human_verified=False`）。

---

## 1. 核心结论（先给答案）

1. **新 ASR 冠军：`ReazonSpeech-NeMo-v2`（619M FastConformer-RNNT）全面领先；`Qwen3-ASR-1.7B-8bit` 为最高速 MLX 备选。**
   - **`ReazonSpeech-NeMo-v2`** 在不改切句的前提下，将 **D0 总体日语 CER 从基线 `k2-v2` 的 `0.2154` 降至 `0.1546`**（降幅 `-28.2%`），在最贴近真实观影的 **D1《重启人生》45 秒 19 句密集对白上，更是将 CER 从 `0.7575`（`k2-v2` 漏掉约 70% 台词）骤降至 `0.2874`**；即便仅跑 PyTorch CPU，实测 `RTF = 0.4718`（约 `2.1×` 实时倍速），RSS 峰值 `5734 MiB`（在 32 GiB M1 Pro 上可轻松容纳）。
   - **`Qwen3-ASR-1.7B-8bit`**（via `mlx-audio`）跑在 M1 Pro GPU 上速度极快（`RTF = 0.1018`，约 **`9.8×` 实时**，`P50 = 0.435s`、`P95 = 0.866s`），GPU 峰值 `3581 MiB`；D0 关键片段显著优于 `k2-v2`（`csj-reproduction` `0.0818` vs `0.0909`、`wmf-04` `0.1829` vs `0.2209`），D1 达到 `0.3623`，修复了 `三分法`、`貢献者・編集者` 等核心词汇。
2. **两个 Whisper 候选淘汰（不再投入）**：
   - `mlx-whisper` Metal 下的 `whisper-large-v3-turbo` 虽然速度很快（`RTF = 0.1713`），但 10s 静音稳定幻觉 `"ご視聴ありがとうございました"`，D0 `csj-task` / `wmf-04` 幻觉拉高总 CER 至 `0.3800`。
   - `imeipopo18/anime-whisper-mlx` 在 D1 漏识严重（`0.5060`）、触发网关拦截（漏出平假名 `"こんにちわ"`）并出现乱译幻觉。
3. **`Smart Turn v3.1` 不建议接入日语连续视频字幕流水线**：
   - S1 断点判别 `ROC-AUC = 0.6034`，`acc@0.5 = 56.31%`（`FP = 36/69`）；S2 混合切分将段长拉长至 `P95 = 9.4–19.0s` 后，反而使 `ReazonSpeech-NeMo-v2` D0 CER 从 `0.1546` 恶化到 `0.1931`、`Qwen3-ASR-1.7B-8bit` 从 `0.2208` 恶化到 `0.4331`。
4. **“好 ASR + 1.8B 小 MT” 的瓶颈实证**：
   - 即使 `NeMo-v2` 与 `Qwen3-ASR-1.7B` 在日语层大幅修复了 D1 漏句与 D0 专词，下游 `Hy-MT2 1.8B` 仍会频繁把正确或近音日语译偏（如把 `キレられる` 误听/误译为 `可以消失` / `会被决定`、把 `二千四年` 译成 `204年`、在 `large-v3-turbo` 上甚至漏出泰语字符 `ก็`）。下一步应直接把 Tier 2A 胜出 ASR（`ReazonSpeech-NeMo-v2` / `Qwen3-ASR-1.7B`）送入 **Tier 2B（4B–8B MT + 同音纠错）盲测**。

---

## 2. D0 基准（88 切片 / 34 单元 + 10s 静音）全量实测对比

> **关于 `csj-task` CER 的口径说明**：[units.jsonl](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/evidence/n0-next/final-references/units.jsonl) 在 `csj-task` 中只标注了说话人 A 的部分台词（共 `134` 字），但音频中实际存在第二位说话人 B。基线 `k2-v2` 漏听了大部分说话人 B（输出 `110` 字），而 `Qwen3-ASR-1.7B`（`193` 字）与 `NeMo-v2`（`129` 字）识别出了更多对话内容，导致 `csj-task` 的原始整段拼接 CER 偏高。因此**应结合 `csj-reproduction`、`wmf-04`、34 单元语义复核及 D1 全句 CER 综合判断**。

| 模型 | 后端 / 精度 | D0 总 CER | `csj-reproduction` | `csj-task` | `wmf-04` | P50 / P95 解码 | RTF | 10s 静音输出 | 内存（GPU 峰值 / RSS） | 网关通过（34 单元） |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- | :---: |
| **`reazon-k2-v2`（基线）** | sherpa-onnx INT8 (CPU) | `0.2154` | `0.0909` | `0.3806` | `0.2209` | — | `<0.1` | `""` | `~1.85 GiB` 整机 | `34/34`（历史语义 `20/34`） |
| **`ReazonSpeech-NeMo-v2`** | PyTorch FP32 (CPU) | **`0.1546`** | **`0.0364`** | `0.4478` | **`0.1406`** | `2.07s / 3.06s` | `0.4718` | `"ピッ!"` | RSS `5734 MiB` | `34/34` |
| **`Qwen3-ASR-1.7B-8bit`** | mlx-audio 8-bit (GPU) | `0.2208` | `0.0818` | `0.7164` | `0.1829` | **`0.435s / 0.866s`** | **`0.1018`** | `"はい。"` | GPU `3581 MiB` / RSS `2535 MiB` | `33/34`（1 空） |
| **`anime-whisper-mlx`** | mlx-whisper (GPU) | `0.2615` | `0.1136` | `0.7090` | `0.2326` | `0.714s / 0.907s` | `0.1607` | `"…"` | GPU `2982 MiB` / RSS `2170 MiB` | `33/34` |
| **`whisper-large-v3-turbo`** | mlx-whisper (GPU) | `0.3800` | `0.0273` | `0.7015` | `0.4165` | `0.748s / 1.014s` | `0.1713` | `"ご視聴ありがとうございました"` | GPU `2362 MiB` / RSS `2170 MiB` | `34/34` |

### 34 单元端到端语义对比关键样本（统一使用 `Hy-MT2 1.8B`，`ai_reviewed`）

- **正样本修复（相对基线 `reazon-k2-v2` 显著改善）**：
  - `csj-reproduction/2-3`：基线漏识 `同時にパラ言語情報`；`NeMo-v2` 与 `Qwen3-1.7B` 均完整识别出 `同時にパラ言語情報、そして非言語情報が伝わっております`。
  - `csj-reproduction/4`：基线误识为 `三文法`（“这种三文法”）；`Qwen3-1.7B` 准确识别 **`三分法`**（`"这一三分法是由藤崎先生提出的"`）。
  - `wmf-04/148-152`：基线误识为 `寄稿者あるいは後見者`（“作者或监护人”）；`Qwen3-1.7B` 准确识别 **`寄稿者あるいは貢献者。編集者が`**（`"即投稿者或贡献者。编辑们各自需要注意什么"`）。
  - `wmf-04/173`：基线截断为 `な会を開いて`；`NeMo-v2` 与 `Qwen3-1.7B` 均完整识别 **`今日のような会を開いて`**（`"举办像今天这样的会议"`）。
- **残留同音错与 1.8B MT 翻车样本**：
  - `wmf-04/161-165`：`Qwen3-1.7B` 将 `そこをきちんと明文化します` 误识为 `速攻金と名分がします`，导致 `Hy-MT2` 译成 `"因此被称为“快速资金”"`（此处 `NeMo-v2` 稳定识别 `そこをきんと明文化します`）。
  - `wmf-04/136` / `140`：`NeMo-v2` 把 `憲章` 偶发误识为 `土憲章` / `減少`，`Hy-MT2` 直译成 `"纳入土宪法"` / `"减少的幅度很小"`。
  - `wmf-04/176-178`：`Qwen3-1.7B` 识别为 `二千四年`，`Hy-MT2 1.8B` 输出错误年份 `"204年"`。

---

## 3. D1（《重启人生》45 秒 / 19 句密集对白）实测对比

沿用同一批 `Silero v4` 切片（3 段：`1.16–15.29s`、`15.37–24.09s`、`24.17–44.99s`），对照官方日文 SRT 第 75–93 条（共 `334` 个规范化日文字符）：

| 模型 | D1 识别字数（参考 `334`） | D1 日语 CER | 网关通过（3 段） | 关键对白表现 |
| :--- | :---: | :---: | :---: | :--- |
| **`reazon-k2-v2`（基线）** | `103` | `0.7575` | `3/3` | 第 2 段仅留 1 句、第 3 段仅留 1 句，**漏识约 70% 台词** |
| **`ReazonSpeech-NeMo-v2`** | **`292`** | **`0.2874`** | `3/3` | 唯一完整抓出 `キレられたのよなれなれしいっつって` 与 `保育士さんって` 的模型 |
| **`whisper-large-v3-turbo`** | `290` | `0.3323` | `3/3` | 覆盖度高，但将 `保育士` 误听为 `ハイクッシュ`，且 `Hy-MT2` 译出泰语字符 `ก็` |
| **`Qwen3-ASR-1.7B-8bit`** | `280` | `0.3623` | `3/3` | 覆盖近全部 19 句，正确识别 `保育士さん`、`窓口の締めの挨拶`、`お疲れ様でした` |
| **`anime-whisper-mlx`** | `243` | `0.5060` | **`2/3`**（第 1 段漏平假名拦截） | 第 2 段出现 `"すけべ続け"` 幻觉 |

> **D1 典型对照（第 1 段 `1.16s–15.29s`，SRT 覆盖第 76–81 条）**：
> - **SRT 原文节选**：`私も前までは「こんにちは」って言ってたけどさ 一回キレられたんだよね なれなれしいっつって。普通に挨拶でしょ。でしょ？意味分かんなくない？それはひどいね。そういえば保育士さんって…`
> - **`reazon-k2-v2`**：漏掉前两句及末尾 `保育士さんって`，把 `なれなれしい` 误译为 `"很奇怪""很离谱"`。
> - **`ReazonSpeech-NeMo-v2`**：几乎逐字命中 `一回キレられたのよなれなれしいっつって。えっなれなれしいって普通に挨拶じゃん。でしょ?意味分かんなくない?それはひどいね。そういえば保育士さんって。`
> - **`Qwen3-ASR-1.7B-8bit`**：完整跟上语速与句数，仅将 `キレられた` 误听为 `切れられた`（`Hy-MT2` 误译为 `"被切断了"`）、`なれなれしい` 误听为 `慣れ慣れし`（`"已经习惯了"`）。

---

## 4. `Smart Turn v3.1` 语义端点检测评估（S1 & S2）

模型：[pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3)（`smart-turn-v3.1-cpu.onnx`，INT8 `8.68 MB`，8 秒音频窗 Whisper log-mel 输入）。

### 4.1 S1：D0 真实句末（34 正例）vs 句中切分（69 负例）判别能力

| 指标 | 实测值 | 说明 |
| :--- | :---: | :--- |
| **ROC-AUC** | **`0.6034`** | 正例均值 `0.7248`，负例均值 `0.5448`，区分度弱 |
| **最优准确率（threshold `0.6 / 0.7`）** | `57.28%` | `thr=0.5` 时 `TP=25, FN=9, TN=33, FP=36`（`Precision=40.98%`, `Recall=73.53%`） |
| **CPU 推理耗时** | `P50 = 21.18 ms` / `P95 = 40.88 ms` | 算力开销很低，但模型本身不适配日语独白/会议子句边界 |

### 4.2 S2：Silero（短停顿 `200ms`）+ `Smart Turn v3.1`（`hard_cap=8s`）混合切分端到端实测

| 片段 | 基线 Silero 段数 / 单元内切分 | 混合方案段数 / 单元内切分 | `Reazon k2-v2` CER（基线 → 混合） | `NeMo-v2` CER（基线 → 混合） | `Qwen3-1.7B` CER（基线 → 混合） |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`csj-reproduction`** | `6` / `2` | `7` / `3` | `0.0909` → `0.0955` | `0.0364` → `0.1000` | `0.0818` → `0.0955` |
| **`csj-task`** | `11` / `1` | `7` / `1` | `0.3806` → `0.5149` | `0.4478` → `0.4254` | `0.7164` → `0.6866` |
| **`wmf-04`** | `45` / `38` | `44` / `37` | `0.2209` → `0.1924` | `0.1406` → `0.1818` | `0.1829` → `0.4757` |
| **D0 总体 CER** | — | — | `0.2154` → **`0.2092`** | **`0.1546`** → `0.1931` | **`0.2208`** → `0.4331` |
| **D1（45s 对白）** | `3`（均长 `14.56s`） | `5`（均长 `8.55s`） | `0.7575` → `0.7126` | **`0.2874`** → `0.3293` | **`0.3623`** → `0.3832` |

- **结论**：`Smart Turn v3.1` 在日语连续演讲与日剧对白中误判率偏高（`FP=36/69`），且合并生成的超长段（`P95 = 9.4–19.0s`）显著拉低了 `Qwen3-1.7B` 与 `NeMo-v2` 的识别率。**不建议将 `Smart Turn v3.1` 接入当前流水线**；切句精度后续应交给上游更强词法/语境能力（或 P3 原生流式模型）。

---

## 5. 诚实限制与下一步建议（Tier 2B）

1. **当前限制**：
   - 本轮测试为**离线切片评测**，`ReazonSpeech-NeMo-v2` 目前跑在 PyTorch CPU 上（`P95 = 3.06s`，RSS `5.6 GiB`），尚未验证 ONNX/MPS 加速与与 Chrome 播放并发的 1× 实速延迟；而 `Qwen3-ASR-1.7B-8bit` 在 MLX GPU 上实测 `P95 = 0.866s`（`RTF = 0.1018`），工程接入路径最成熟。
   - 34 单元中文语义比对由 AI 对照参考译文完成（`ai_reviewed, human_verified=False`）。
2. **下一步建议（进入 Tier 2B：MT + 联合同音纠错盲测）**：
   - 保留本轮胜出的两路 ASR 识别文本（**`ReazonSpeech-NeMo-v2`** 与 **`Qwen3-ASR-1.7B-8bit`**）以及干净日文 Oracle 作为输入，直接在 D0（34 单元）与 D1（19 句）上盲测 **4B–8B 级翻译与联合纠错模型**（`Seed-X-7B`、`PLaMo-3-Translate 8B`、`TranslateGemma 4B/12B`、`Qwen3-4B/8B no-think`、`Sakura-7B`），重点验证它们能否：
     1. 修复 `キレられる / 切れられる`、`憲章 / 検証` 等日语同音错字；
     2. 在不发生 A3 C1 上下文污染的前提下，利用前 2 句语境消灭 `1.8B` 的误译与漏字符。

---

### 附录：本轮脚本与落盘证据索引

- 评测脚本目录：`p0/tier2_asr/`（[prepare_inputs.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_asr/prepare_inputs.py)、[bench_asr.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_asr/bench_asr.py)、[bench_nemo.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_asr/bench_nemo.py)、[run_mt_eval.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/tier2_asr/run_mt_eval.py)；已淘汰的 Smart Turn 探测脚本已随清理移除）
- 汇总数据产物：[tier2-asr-2026-11-23](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/tier2-asr-2026-11-23)（`asr_mlx_summary.json`、`asr_nemo_summary.json`、`reazon_baseline_cer.json`、`d0_34unit_comparison.json`、`smart_turn_s1.json`、`smart_turn_s2.json`、`s2_*_cer.json`）
