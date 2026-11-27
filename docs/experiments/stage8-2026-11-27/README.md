# Stage 8 Evaluation, Stability Hardening & Candidate Screening Report (2026-11-27)

- **Baseline (`B-deployed`)**: `7ea1386` (Stage 7 production bundle: `ReazonSpeech NeMo-v2 619M ANE hybrid + Qwen3-4B-Instruct-2507-4bit`)
- **Stage 8 Candidate (`main`)**: `be8e3a8` + `T7` release gate tooling (`p0/stage8_eval/{aggregate.py,acceptance.py}`)
- **Contract / Plan**: [docs/live_video_translate_stage8_plan_20261126/08_acceptance_and_rollback.md](../../live_video_translate_stage8_plan_20261126/08_acceptance_and_rollback.md) & [thresholds.proposed.json](../../live_video_translate_stage8_plan_20261126/templates/thresholds.proposed.json)

---

## 1. Executive Decision Summary

| Workstream | Verdict | Key Evidence | Production Disposition |
| :--- | :--- | :--- | :--- |
| **T2 Qwen3 KV Token-Ledger Fix** | **PASS** | [check_mt_cache.py](../../../p0/stage8_eval/check_mt_cache.py) `15/15 PASS`, `all_ids_exact=true`, `top1_match=true`; fixes `+1/+2/+3/+4` EOS cache offset drift | **Merged** ([`6ea03ef`](../../../engine/src/subtitle_service/mt_cache_contract.py)) |
| **P0 Metrics, `ExperimentKey` & `length_limit` Final Guard** | **PASS** | `LocalSession.translate()` blocks `stop_reason != "eos"` (`length_limit`/`cancelled`/`error`) from emitting final `Caption`; WebSocket regression test passes | **Merged** ([`aa3df8c`](../../../engine/src/subtitle_service/local_pipeline.py)) |
| **P1-A Preview Anti-Retraction (`preview_guard.ts`)** | **PASS** | 120s paired Chrome: `dom_shrink_flickers` **`10 → 0`** (`-100%`), `streaming_erased_chars` **`74 → 32`** (`-56.8%`), `seg_end_to_ws P95` `-44.75 ms` | **Merged** ([`preview_guard.ts`](../../../extension/src/preview_guard.ts)) |
| **P1-B VAD 2×2 & `max_segment_s` Sweep** | **KEEP DEFAULT** | Stage 7 (`max_segment_s=2.8s, pad=500ms`) achieves `0.3192` vs `0.3301–0.3804` (`3.6–4.5s`); `0` unprotected overlaps | **No runtime change** ([`run_p1b_vad_2x2.py`](../../../p0/stage8_eval/run_p1b_vad_2x2.py)) |
| **G3 / P2 Model Challengers (`M2/A3/A4/Index-2B`)** | **REJECTED** | `Q8`, `Qwen3-ASR-1.7B`, `Whisper-v3-turbo`, and `Index-Translate-2B` all failed quality/cost Pareto gates | **Not promoted**; challenger caches cleaned |
| **T7 Paired 120s Chrome Acceptance** | **Stability `PASS` / G5 Upgrade `BLOCKED`** | `g0`, `g1`, `g3` PASS on 120s paired Chrome; full `default_quality` G5 gate stays `BLOCKED` (`HOLDOUT_MISSING`, 45m/10m & human review deferred) | Ready as **stability/UX release**; Stage 7 models retained |

---

## 2. Batch 1–3 Offline & Microbenchmark Summary

### 2.1 MT Ablations & Screening (`T3`, `T5 M2`, `P2 Index-2B`)

| Variant | Cohort | `valid` | `chrF2 (30s/case mean)` | `chrF2 concat` | Latency `P50 / P95` | Peak Mem (`MiB`) | Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `B-corrected-654` (`Qwen3-4B-4bit`, KV `ctx4`) | EP01 `654` cues | `653/654` | `13.1520` *(v2)* | `20.0879` | `931.42 / 1295.75 ms` | `2762.39` (MLX) | **Baseline** |
| `M1-C0` (`stage7`, `ctx4`, `rep1.08`, KV) | Stratified `120` | `120/120` | `9.7238` *(v1)* | `13.5010` | `817.38 / 1113.05 ms` | `2762.39` (MLX) | **M1 Winner** |
| `M1-C5` (`fresh`, no KV reuse) | Stratified `120` | `120/120` | — | `13.4862` | `1961.58 ms` (`P50`) | — | Validates KV speedup |
| `M2` (`Qwen3-4B-Instruct-2507-8bit`) | EP01 `654` cues | `652/654` | `13.5510` *(v2)* | `20.2033` | `1080.58 / 1535.56 ms` | `4522.72` (MLX) | **Rejected** (+64% mem, slower, marginal gain) |
| `Index-Translate-2B.Q4_K_M` (`0-ctx`, `clean_jp`) | `26` (`18` E3/E4 + `8` OK) | `24/26` (`2` kana) | `9.5324` | `11.0890` | `201.83 / 272.10 ms` | `2412.06` (RSS) | **Rejected** (`OK8` `16.61` vs `18.33`, kana leaks) |
| `Qwen3-4B-4bit` (`0-ctx`, `clean_jp`) | Same `26` | `26/26` | `9.4020` | `11.2299` | `952.64 / 1040.85 ms` | `2782.70` (MLX) | **Control** (`ctx4` outperforms both `0-ctx`) |
| `Index-Translate-2B.Q4_K_M` (`live_asr_ja`) | `26` ASR texts | `25/26` (`1` kana) | `11.0837` | `15.0378` | `225.19 / 428.37 ms` | `2850.11` (RSS) | **Rejected** |
| `Qwen3-4B-4bit` (`live_asr_ja`) | `26` ASR texts | `26/26` | `11.2385` | `15.0789` | `1012.25 / 1346.91 ms` | `2782.70` (MLX) | **Control** |

### 2.2 ASR Screening & Same-Scene Boundary Diagnostics (`T4`, `T5`, `P1-B`)

- **G3 Challengers (`T5`, frozen `60` units, `N=850`)**:
  - `A0_oracle_hybrid_pad500`: `cer_v2 concat=0.3082`, `per_unit=0.3365`, `P50=43.92 ms` (`P95=233.77 ms`).
  - `A3` (`Qwen3-ASR-1.7B-8bit`): `concat=0.3000`, `per_unit=0.3494`, `P50=335.81 ms` (7.6× slower); per-unit vs NeMo: `better=16`, `worse=22`, `tie=22` (`net_edit_delta=+11`) → **Rejected**.
  - `A4` (`whisper-large-v3-turbo`): `concat=0.3341`, `per_unit=0.3576`, `P50=713.53 ms` (16× slower); `better=18`, `worse=25`, `tie=17` (`net_edit_delta=+18`) → **Rejected**.
  - Consequently, `T6` dual-ASR arbiter architecture is **skipped** by contract.
- **P1-B Same-Scene 2×2 & `max_segment_s` Sweep (EP01 `0–600s`, `168` cues / `14` contiguous scenes, `N=2566`)**:
  - `VAD_pad350`: `0.3301` (`S=250 D=534 I=63`) vs `VAD_pad500`: **`0.3192`** (`S=251 D=499 I=69`, `P50=41.26 ms`).
  - `Oracle_pad350`: `0.3429` (`S=200 D=660 I=20`) vs `Oracle_pad500`: **`0.2907`** (`S=225 D=497 I=24`, `P50=45.45 ms`).
  - `unprotected_overlap_count = 0`; gap between `VAD_pad500` and `Oracle_pad500` is `2.85` pts (mainly `I +45` from adjacent unannotated speech + forced cuts).
  - Single-factor `max_segment_s` sweep (`pad=500ms`): `2.8s=0.3192 (P95=159.15ms)` < `3.6s=0.3578 (199.09ms)` < `4.0s=0.3663 (215.64ms)` < `4.5s=0.3804 (235.10ms)` — confirming `2.8s` + `pad=500ms` is locally optimal.

---

## 3. T7 Paired 120s Real Chrome Verification (`subtitles_off` vs `B-deployed` vs `Stage 8 main`)

Executed serially under Chrome `158.0.8127.88` on EP01 `0–120s` at `1×` playback using [`run_chrome.mjs`](../../../p0/stage8_eval/run_chrome.mjs), [`aggregate.py`](../../../p0/stage8_eval/aggregate.py), and [`acceptance.py`](../../../p0/stage8_eval/acceptance.py).

| Metric | `subtitles_off` | `B-deployed` (`7ea1386`) | `Stage 8 candidate` (`main`) | Delta (`Candidate vs Baseline`) |
| :--- | :--- | :--- | :--- | :--- |
| Final captions sent / rendered (`valid`) | `0` | `31 / 31` | `31 / 31` | Equal coverage (`0` length-limit drops) |
| `dom_events.streaming` | `0` | `76` | `51` | `-25` streaming churn (`24` short deltas held) |
| **`dom_events.shrink_flickers`** | `0` | **`10`** | **`0`** | **`-10` (-100% shrink-then-regrow eliminated)** |
| `dom_events.streaming_erased_chars` | `0` | `74` | `32` | **`-42` (-56.8%)** |
| `asr_p50_ms` | — | `54.77 ms` | `51.75 ms` | `-3.02 ms` |
| `mt_p50_ms / mt_p95_ms` | — | `302.38 / 592.96 ms` | `297.78 / 554.96 ms` | `-4.60 / -38.00 ms` |
| `seg_end_to_ws_p50 / p95` | — | `620.24 / 1032.27 ms` | `646.63 / 987.52 ms` | `+26.39 / -44.75 ms` |
| `srv_peak_rss_mib / cpu%` | `0 / 0` | `1482.1 MiB / 76.4%` | `1301.7 MiB / 53.7%` | `-180.4 MiB / -22.7 pp` |
| `chrome_peak_rss_mib` | `3661.3 MiB` | `2866.6 MiB` | `2700.7 MiB` | `-165.9 MiB` |
| `dropped_frames / total` (`drop_rate%`) | `263 / 3634` (`7.237%`) | `195 / 3630` (`5.372%`) | `247 / 3632` (`6.801%`) | `-0.436 pp` vs `subtitles_off` (`<= +1.0 pp` pass) |

### Gate Breakdown (`evaluate_gates` @ `default_quality`)

- `g0_identity_and_dom`: **PASS** (`G0_IDENTITY_AND_DOM_OK`)
- `g1_lifecycle`: **PASS** (`G1_LIFECYCLE_OK`)
- `g3_resources`: **PASS** (`G3_RESOURCES_OK`, `drop_rate_delta_vs_subtitles_off_pp = -0.436 <= 1.0`)
- **`stability_verdict`**: **`PASS`**
- `g2_availability_and_latency`: **BLOCKED** (`MISSING_UNIT_ALIGNED_DOM_AVAILABILITY_OR_LATENCY` — 120s paired run measured end-to-WS/DOM telemetry rather than aligned full-episode unit map)
- `g4_quality_and_review`: **BLOCKED** (`HOLDOUT_MISSING` — 15+ min holdout / human review / 45m+10m soak not run since Stage 7 models remain unchanged)
- **Overall `verdict` (`default_quality` model-upgrade gate)**: **`BLOCKED`**

---

## 4. Rollback & Release Recommendation

- **Recommended Release Framing**: Accept current `main` (`T2` KV ledger fix + `P0` `length_limit` final guard + `P1-A` preview anti-retraction + `P1-B` validated VAD settings + `T7` telemetry/gates) as a **stability & subtitle UX hardening release** keeping the proven Stage 7 model pair (`NeMo-v2 ANE hybrid` + `Qwen3-4B-4bit`).
- **Exact Rollback to `B-deployed` (`7ea1386`)**:
  1. Stop any running `subtitle_service.cli` process and wait for worker/Metal teardown.
  2. Switch or checkout `7ea1386` (keeping `.model-cache/hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit`, `.model-cache/nemo-ane`, and `.model-cache/speech-cpp` intact).
  3. Rebuild the browser extension from `extension/` (`npm run build`) and reload in Chrome.
  4. Run a short regression check (`pytest engine/tests p0/p2/tests -q`) to confirm clean behavior.
