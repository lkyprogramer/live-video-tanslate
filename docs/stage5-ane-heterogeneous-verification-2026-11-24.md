# Stage 5 物理异构并行架构（ANE ASR ‖ Metal GPU MT）全量实测验收报告（2026-11-24）

## 1. 总结论

Stage 5 的核心目标是在 Apple Silicon M1 Pro（32 GiB）上打破 Stage 0–4 的「单 Metal GPU 串行排队」上限，探索 **ANE 专核 ASR ‖ Metal GPU 专核 MT** 的真正物理异构并行。基于严格的独立基准与 1× 实时 WebSocket 端到端实测，最终决策如下：

1. **Stage 5A-1 + 5A-2（GO — 正式合入 `m1-pro-nemo` 默认配置）**：将 `reazonspeech-nemo-619M-v2-Q8_0` 的 FastConformer Encoder（`w8a16`）静态分桶下沉至 **Apple Neural Engine (ANE)**，搭配 **Accelerate AMX `greedy` RNNT 解码**，与 **Metal GPU `Qwen3-4B-Instruct-2507-4bit`** 形成物理异构真并行：
   - **D1 日剧 1× WS 实测**：`final_e2e` P50 首次跌破 `500ms`（`564ms → 491ms`，`-12.9%`），`first_delta_e2e` P50 `489ms → 440ms`（`-10.0%`），ASR P50 `123.5ms → 73.5ms`（`-40.5%`）；
   - **跨语料全线提速**：CSJ-reproduction ASR P50 `109.3ms → 66.4ms`、`first_delta_e2e` P50 `610ms → 515ms`、`final_e2e` P50 `745ms → 689ms`；WMF-04 ASR P50 `97.5ms → 64.0ms`；
   - **CPU 占用大幅下降**：服务端均值 CPU `23.9% → 13.4%`（`-43.9%`），峰值 CPU `157.0% → 48.2%`（`-69.3%`），为 Chrome 浏览器播放与桌面多任务留出更大余量；
   - **100 句 / 525 次真并发压测**：ANE 后台连续解码 `525` 次（ASR P50 `54.27ms`）期间，GPU MT 仅膨胀 `+7.76%`（`298.15ms → 321.29ms`）；
   - **识别精度与稳定性**：91-slice 离线门禁中 `w8a16 greedy` D0 CER = `0.1585` 优于 `gpu_beam`（`0.1610`）与 `ane_beam`（`0.1623`），静音返回 `""`（0 幻觉）。
2. **Stage 5B-1 & 5B-2（NO-GO — 保持 MLX `Qwen3-4B` 不变）**：实测 `kapitsa` 项目的 `Qwen3.5-4B`（`KFlash` 与 `plain`）在 91 条生产日语短句上未触发 `>= 80ms` 或 `>= 25%` 的切换门禁（`kapitsa_kflash` 总耗时 P50 `399.87ms` / P95 `988.33ms`，慢于 MLX `Qwen3-4B` 的 `358.14ms` / `744.11ms`）。
3. **Stage 5C（Deferred / Not Needed）**：`Ruby-ASR-1.7B` 仍属 GPU 自回归架构，会重新引入 Metal GPU 争用；当前 ANE NeMo-v2 已达 `64–73ms` 且 D0 CER `0.1585`，不再需要引入。
4. **Stage 5D（未运行清单）**：已完成 ~5.5 分钟/组 1× 实时 WebSocket 三组对照与 525 次连续 ANE 并发压测；需 `sudo powermetrics` 与 Chrome 1080p/4K 实际观影的 30–60 分钟热稳定性长跑已给出完整命令与门禁，**当前标为未运行**。

---

## 2. Stage 5A-1：CoreML ANE 静态桶原型与精度修复（GO）

- **复现脚本**：[verify_stage5a_ane.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/perf_probe/verify_stage5a_ane.py)
- **原始证据**：[stage5a_ane_probe.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/stage5a-2026-11-24/stage5a_ane_probe.json)

### 2.1 ANE 数值稳定性与排布关键修复
1. **FP16 溢出防护**：ANE 仅以 FP16 执行激活计算，`FastConformer` 的 `LayerNorm` 平方和及注意力 `Q * K^T` 易上溢；导出前对 `LayerNorm` 输入实施 `1/16` 缩放并在 Attention 对 `Q` 预乘 `1/sqrt(128)`。
2. **ANE 原生 `[1, D, 1, T]` 排布**：最初在 `permute/transpose + reshape(1, T, D) -> Linear` 路径上出现 `15.5024` 的最大激活误差（CoreML ANE 编译器在非连续 reshape/transpose 上精度退化）；将其重写为 ANE 原生 4D 张量 `[1, D, 1, T] -> F.conv2d(1x1)` 后，最大误差骤降至 `0.0713`（降低 **217×**），前 5 条 sanity 切片 `PyTorch` vs `speech.cpp` 基准 **CER = `0.0000`**。
3. **单局部注意力窗静态分桶**：`NeMo-v2` GGUF 元数据 `attention_context = 128`、`subsampling_factor = 8`。生产切片（`4.5s + 2×0.35s = 5.20s`）对应 `<= 520` mel 帧（`<= 65` 编码帧），完全落入单注意力窗口（`<= 128`），无需复杂流式 KV 分块，静态分桶即可覆盖并自动对超长切片（`> 1024` mel 帧）优雅回退至 `speech.cpp`。

### 2.2 计算单元对比（`M512` 桶）与全量 91-slice 门禁

| 测试项 | `fp16` `CPU_AND_NE` | `w8a16` `CPU_ONLY` | `w8a16` `CPU_AND_GPU` | `w8a16` `CPU_AND_NE` | `w8a16` `ALL` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `M512` 耗时 P50 / P95 (ms) | `24.53` / `24.87` | `68.46` / `77.47` | `60.05` / `67.89` | **`19.78` / `19.91`** | `19.72` / `19.80` |
| `slice[2]` CER | `0.0` | `0.0` | `0.0` | **`0.0`** | `0.0` |

- **三桶热态 ANE 耗时（`w8a16 CPU_AND_NE`）**：`S256` P50 `16.74ms` / P95 `17.18ms`；`M512` P50 `19.97ms` / P95 `20.18ms`；`L1024` P50 `29.11ms` / P95 `29.26ms`（三桶总 wired 增量 `+703.3 MB`）。
- **Keepalive 实测**：2 秒静默后 `cold_wake` 中位数 `32.53ms` vs 1 秒 `S256` ping `keepalive` 中位数 `34.91ms`，证明 M1 Pro ANE 在 2 秒间隔内无显著休眠惩罚，无需后台常驻心跳。
- **91-slice 门禁（87 切片走 ANE + 4 超长切片回退）**：
  - `w8a16` ANE Encoder-only P50 `29.79ms` / P95 `39.50ms`；
  - `w8a16 greedy`：`total P50 = 54.36ms`、`D0 CER = 0.1592`、`D1 CER = 0.2874`、静音 `""`；
  - `w8a16 beam`：`total P50 = 260.43ms`、`D0 CER = 0.1623`、`D1 CER = 0.2904`、静音 `"ピッ!"`；
  - `fp16 greedy`：`total P50 = 65.08ms`、`D0 CER = 0.1577`、`D1 CER = 0.2874`、静音 `""`。

---

## 3. Stage 5B-1 & 5B-2：`kapitsa` `Qwen3.5-4B` / `KFlash` 评估（NO-GO）

- **复现脚本**：[verify_stage5b_kapitsa_mt.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/perf_probe/verify_stage5b_kapitsa_mt.py)
- **原始证据**：[stage5b_kapitsa_mt_probe.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/stage5b-2026-11-24/stage5b_kapitsa_mt_probe.json)

| 91 条生产日语短句对比 | `mlx_qwen3_4b`（现网基线） | `kapitsa_qwen35_4b_kflash` | `kapitsa_qwen35_4b_plain` |
| :--- | :---: | :---: | :---: |
| `first_delta` P50 / P95 (ms) | `194.78` / `332.69` | `158.78` / `309.72` | `148.79` / `278.64` |
| `total` P50 / P95 (ms) | **`358.14`** / **`744.11`** | `399.87` / `988.33` | `356.57` / `801.48` |
| Decode 吞吐 (tok/s) | — | `43.4`（P50 输出 `10.0` tok） | `50.3`（P50 输出 `10.0` tok） |
| 假名泄漏 / 非中文 | `0` / `1` | `0` / `1` | `0` / `1` |

**NO-GO 根因**：`kapitsa` 的 `FfnSplit` 主要加速 `T >= 512/1024` 的长 prefill，而本项目的增量翻译 prefill 仅 `18–55` tokens；`KFlash` 推测解码在 `10` tokens 极短中文输出下，因验证开销反而比 `plain` 更慢（`399.87ms` vs `356.57ms`），P95 更恶化至 `988.33ms`。且部分口语语义反而不如现网（例如「保育士」直译「保育士」而非「保育员」）。故未触发 `>= 80ms / >= 25%` 切换门禁，保留 MLX `Qwen3-4B-Instruct-2507-4bit`。

---

## 4. Stage 5A-2：原生 `libnemo_ane.dylib` 与 1× 实时 WebSocket 端到端实测（GO）

- **原生实现**：[nemo_ane.mm](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/native/nemo_ane.mm) · [setup_nemo_ane.sh](file:///Users/luo/Documents/github/jp-live-video-tanslate/scripts/setup_nemo_ane.sh)
- **引擎调度**：[config.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/config.py) · [local_pipeline.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/engine/src/subtitle_service/local_pipeline.py)
- **复现脚本**：[verify_stage5a2_live.py](file:///Users/luo/Documents/github/jp-live-video-tanslate/p0/perf_probe/verify_stage5a2_live.py)
- **原始证据**：[stage5a2_live_verification.json](file:///Users/luo/Documents/github/jp-live-video-tanslate/docs/experiments/stage5a2-2026-11-24/stage5a2_live_verification.json)

### 4.1 架构与优雅回退设计
- **三生产桶**：`fc_S256_w8a16.mlmodelc`（`<= 2.56s`）、`fc_M560_w8a16.mlmodelc`（`<= 5.60s`，精确覆盖 `4.5s + 2×0.35s = 5.20s` 生产切片）、`fc_L1024_w8a16.mlmodelc`（`<= 10.24s`）。
- **C++ 原生解码**：Objective-C++ `libnemo_ane.dylib`（`121K`，CoreML `MLModel` `CPUAndNeuralEngine` + Accelerate AMX `cblas_sgemv` RNNT 解码 + 协作式 `cancel_cb`）。
- **双路真并行调度**：当 `device = "ane"` 时，`LocalSession` 启动独立的 `_run_ane_asr` 与 `_run_ane_mt_scheduler` 双线程，ANE ASR 与 Metal GPU MT 不再通过 `_gpu_lock` 串行排队，仅保留 `Final MT > Draft MT` 的 GPU 优先级。
- **自动回退**：若 `.model-cache/nemo-ane/` 资产缺失或遇到 `> 1024` mel 帧超长切片，自动无感回退至 `speech.cpp`（`MTL0` GPU）。

### 4.2 1× 实时 WebSocket 三组 A/B 全量对比

| 指标 / 语料 | `gpu_beam`（Stage 4 基线） | `ane_beam` | **`ane_greedy`（新默认）** | `ane_greedy` vs `gpu_beam` |
| :--- | :---: | :---: | :---: | :---: |
| **D1 日剧 (45s)** `asr_p50` | `123.5ms` | `232.2ms` | **`73.5ms`** (`p95=85.1ms`) | **`-40.5%`** |
| **D1 日剧** `first_delta_e2e` P50 / P95 | `489ms` / `819ms` | `624ms` / `955ms` | **`440ms` / `808ms`** | **`-10.0%`** / `-1.3%` |
| **D1 日剧** `final_e2e` P50 / P95 | `564ms` / `928ms` | `731ms` / `1085ms` | **`491ms` / `916ms`** | **`-12.9%`** / `-1.3%` |
| **D1 日剧** `captions` / `warnings` | `13` / `0` | `13` / `0` | `13` / `0` | 持平 |
| **CSJ-repro** `asr_p50` | `109.3ms` | `169.6ms` | **`66.4ms`** (`p95=93.4ms`) | **`-39.2%`** |
| **CSJ-repro** `first_delta_e2e` P50 / P95 | `610ms` / `931ms` | `686ms` / `895ms` | **`515ms` / `732ms`** | **`-15.6%`** / **`-21.4%`** |
| **CSJ-repro** `final_e2e` P50 / P95 | `745ms` / `1028ms` | `809ms` / `1032ms` | **`689ms` / `871ms`** | **`-7.5%`** / **`-15.3%`** |
| **CSJ-task** `asr_p50` / `final_e2e` P50 | `92.9ms` / `627ms` | `140.4ms` / `651ms` | **`66.1ms` / `608ms`** | **`-28.8%`** / `-3.0%` |
| **WMF-04** `asr_p50` / `final_e2e` P50 | `97.5ms` / `642ms` | `177.8ms` / `713ms` | **`64.0ms` / `636ms`** | **`-34.4%`** / `-0.9%` |
| **Silence** `captions` / `warnings` | `0` / `0` | `0` / `0` | `0` / `0` | 零幻觉 |
| **50ms Seek 重连** `reopen_wall` / `drain` | `41.415s` / `0.2666s` | `41.482s` / `0.3461s` | `41.478s` / `0.3165s` | 稳定 |
| **CPU 均值 / 峰值 (%)** | `23.9%` / `157.0%` | `16.1%` / `71.4%` | **`13.4%` / `48.2%`** | **`-43.9%`** / **`-69.3%`** |
| **峰值 RSS (MiB) / Wired 增量 (MB)** | `1477.3` / `3151.3` | `1513.4` / `4322.2` | `1507.9` / `3366.6` | RSS `+30.6 MiB` |

> **注**：`ane_greedy` 在 `csj-reproduction`（及 `seek_50ms` 重连同一音频）上各有 `1` 条极短语气词（如 `"は。"`）触发网关 `translation_invalid` 过滤，与 `gpu_beam` 在 `wmf-04` 上触发 `1` 条属同一边界机制；`ane_greedy` 在 `wmf-04` 上为 `0` warnings。

### 4.3 100 句真并发 ANE ASR + GPU MT 压力测试
- **Solo GPU MT P50**：`298.15 ms`
- **Concurrent GPU MT P50**（后台 ANE 持续完成 `525` 次完整解码，ANE ASR P50 = `54.27 ms`）：`321.29 ms`（**仅 `+7.76%` 膨胀**）

---

## 5. Stage 5D：30–60 分钟功耗与 Chrome 实机压测（未运行）

本轮已完成的并发代理验证包括：~5.5 分钟/组 1× 实时 WebSocket 三组全量回放，以及 `525` 次连续 ANE ASR ‖ 100 句 GPU MT 真并发压测。以下需要 `sudo` 权限与实机视频渲染的 30–60 分钟热稳定性压测**尚未运行**，留作最终实机长跑清单：

```sh
# 终端 1：启动引擎（确保 engine/config/profiles.toml 中 m1-pro-nemo 已设为 device="ane", decoding="greedy"）
engine/.venv/bin/python -m subtitle_service.cli --config engine/config/profiles.toml

# 终端 2：采集 30–60 分钟功耗与温度（需 sudo）
sudo powermetrics --samplers gpu_power,ane_power,cpu_power,thermal -i 5000 -n 360 -o /tmp/stage5d_powermetrics.txt

# 终端 3：Chrome 1080p/4K 播放与字幕端到端长跑
TEST_PROFILE=m1-pro-nemo MEDIA_DIR=/path/to/videos node p0/tier2_mt/verify_chrome_1080p_4k.mjs
```

**验收门禁**：30–60 分钟连续播放无掉帧与 ANE 降频劣化，热状态保持 `Nominal`/`Moderate`，`RSS` 无持续泄漏上涨，`final_e2e` P50 维持 `< 0.6s`。
