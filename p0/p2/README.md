# P2 experiments

Run commands from the repository root. Production profiles and the extension are unchanged.

The current exploration is archived. Unused model snapshots, isolated environments and downloaded runtimes were removed on2026-11-09; reazon-hy and all raw evidence remain. See the [cleanup record](../../docs/local-runtime-cleanup-2026-11-09.md). Commands below document historical reproduction and require restoring their locked downloads first. Do not resume experiments or allocate remaining slots without a new user request.

## B1 boundary

`cli.py freeze --phase B1` adopts the accepted B0 raw output receipts, checks the nine PCM hashes, and freezes source, input, model, runtime and hypotheses before inference. B0 source snapshots remain immutable; their checkout hashes are not rewritten to today's implementation.

The four configurations are PAD-900 (88 original cuts), PUNCT-JA (62 original texts), and the official PLaMo 0.8B/2B MLX H0 models (62 original texts). Seeds remain 11/29. `B0-CURRENT` must first reproduce the two original 62-row outputs exactly. No causal-stream, realtime, Chrome, holdout or production-selection functionality is added by B1.

The selected model paths and downloaded repository metadata must be prepared under the new run's `sources/` before freezing; the measured run's `sources/model-paths.json`, `locks/models.json` and `sources/plamo-runtime-lock.txt` record the exact local preparation. Fixed revisions are also in the reviewed plan's model lock. PLaMo uses a separate environment:

```sh
uv venv --python engine/.venv/bin/python .model-cache/p2-b1-venv
uv pip install --python .model-cache/p2-b1-venv/bin/python 'mlx==0.32.3' 'mlx-lm==0.32.0' 'transformers==5.19.0' 'numba==0.68.0'
```

Download all selected weights and dependencies before resource measurement. Do not modify the engine lock or run models concurrently. Use a new attempt directory after failure; never remove failed rows or rewrite a frozen manifest to permit resume.

```sh
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B1_RUN" freeze --phase B1
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B1_RUN" preflight --experiment PAD-900
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 engine/.venv/bin/python -m p0.p2.resources --requests 140 --scope 'B1 offline current MT;124 main+16 short requests' "$B1_RUN/comparisons/current-mt" -- engine/.venv/bin/python -m p0.p2.cli --run-dir "$B1_RUN" probe --experiment B0-CURRENT --mode fixed-text
```

Then run PAD-900 fixed-cuts, PAD-900 fixed-text, PUNCT-JA fixed-text and the two MT-PLAMO*-H0 fixed-text commands serially through the same resource wrapper. The CLI dispatches PLaMo into its isolated interpreter. Use `--audio-seconds 476.058` for the PAD ASR source normalization (report added decode seconds separately); fixed-text denominators are 140 for control/PAD, 124 for punctuation, and 129 for each PLaMo (124 main +5 synthetic smoke). Prefer the separate `mt-seed11/29` intervals for main-request resource comparison. Whole-run includes loading and auxiliary controls.

`score --experiment ID` writes anonymous review material when `reviews/adjudication.json` is absent. Review all 34 units per completed candidate and seed, retain raw/filter/finish data, then provide explicit correct/partial/incorrect/missing/uncertain judgments and new-critical-error flags in that file. Re-run score to calculate gates. This is AI text review with provisional references; it does not replace listening. Incomplete candidates retain explicit failed/NOT_RUN states and cannot qualify.

Q requires net +3/34 in both rounds without new critical errors or same-round protected losses. R at this stage is only an offline quality prerequisite; B1 offline measurements cannot prove the later full-chain hot-memory 20% gate or realtime subtitle latency. Greedy duplicate rounds are repeatability evidence, not independent samples.

Resource raw JSONL, per-row outputs, model weights and private references stay ignored. Publish the compact phase report and resource CSV under `docs/experiments/`; never publish access tokens or private PCM. `test/` remains sealed.

## B2 boundary

`freeze --phase B2` adds only `ASR-SENSEVOICE-I8` and `ASR-WHISPER-JA760`. Prepare their selected local paths in `sources/model-paths.json`, the official SenseVoice release receipt and pinned author CT2 files first; the measured run contains exact wheel/installed-binary checks. Both execute in the unchanged engine environment. Release content uses archive SHA256, not the runtime Git commit as a model revision.

The same-run control now runs Reazon88 fixed-cuts with exact text parity, then M0 two62 fixed-text with exact parity, before any candidate. Each candidate runs88 fixed-cuts, then the same M0 two62, retaining16 short-control translations separately. Failed ASR prevents MT/scoring even if aggregate files survived. Shared B1 primitives have explicit phase/runtime/data inputs; B0/B1 historical source snapshots and receipts are not rewritten to pass a new checkout.

```sh
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B2_RUN" freeze --phase B2
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B2_RUN" probe --experiment B0-CURRENT --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B2_RUN" probe --experiment B0-CURRENT --mode fixed-text
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B2_RUN" probe --experiment ASR-SENSEVOICE-I8 --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B2_RUN" probe --experiment ASR-SENSEVOICE-I8 --mode fixed-text
```

Use the same two probe commands for `ASR-WHISPER-JA760`. For measured runs wrap all six commands with `p0.p2.resources`, `--audio-seconds 476.058` for source-audio normalization and `--requests 140` for MT workload normalization; put resource options before the output directory. Report main `asr-88` / `mt-seed*` phases separately from load, five fixed smoke controls and short probes. The raw driver records aggregate preparation+ASR+MT time per configuration and passes its remaining3h budget to the resource wrapper. `b2_postprocess` reads saved evidence with its own analysis hash; it does not rerun models.

SenseVoice tags come from the entire frozen vocabulary; unknown controls fail closed. Dedicated Whisper tokenizer files are mandatory; native generation records precede no-speech filtering, the lazy generator is exhausted, and length-cap results remain unusable. See [B2 results](../../docs/p2-b2-results-2026-11-09.md) for fixed settings, source-inferred EOS limits, provisional judgments and unrun later phases.

## B3 boundary

B3 adds `NATIVE-VOX-960` and Q-only `AST-G4-OFFICIAL`; it does not register production backends. `b3.py` freezes/adopts prior receipts and dispatches isolated workers. `b3_protocol.py` owns the frozen Chinese parser and causal source-commit rules; `b3_worker.py` owns the actual native audio and multimodal calls. Both workers use MLX0.32.4/Transformers5.22.1 in separate environments, with pinned `mlx-audio`/`mlx-vlm` commits and model revisions from the plan. Engine dependencies remain unchanged.

Prepare only the two selected snapshots, their `sources/{voxtral,gemma4}/repository-files.json`, `sources/model-paths.json`, and `sources/official-prompt.json` before freezing. The actual runs preserve complete source snapshots, model-file hashes, processor/template files and runtime versions. Finish downloads/installation before starting serial resource measurements.

```sh
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" freeze --phase B3
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" probe --experiment B0-CURRENT --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" probe --experiment B0-CURRENT --mode fixed-text
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" preflight --experiment AST-G4-OFFICIAL
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B3_RUN" probe --experiment AST-G4-OFFICIAL --mode fixed-cuts
```

Vox consumes nine continuous PCM timelines in200ms packets. A session persists across VAD endpoints and resets only between videos. Accepted/ingested/decoder progress differ; source intervals are decoder estimates, not word timestamps. UTF-8 deltas retain joint intervals, flush endpoints wait for native termination, and cumulative generated steps are limited to8192 per video. The observer holds no strong session/model reference after completion. No1x pacing or online MT queue is measured here; committed sources are translated separately with unchanged Hy-MT2 M0 seeds11/29.

The default MLX free cache hit the12GiB guard in the first native G0. Its sole deployment diagnosis is `b3_vox_cache.py`, using a frozen `locks/vox-allocator-policy.json` with512MiB free cache. Use the explicit launcher for this measured configuration:

```sh
engine/.venv/bin/python -m p0.p2.b3_vox_cache --run-dir "$VOX_RUN" --capability
engine/.venv/bin/python -m p0.p2.b3_vox_cache --run-dir "$VOX_RUN"
engine/.venv/bin/python -m p0.p2.cli --run-dir "$VOX_RUN" probe --experiment NATIVE-VOX-960 --mode fixed-text
```

The generic Vox CLI remains the default-cache recipe; it does not silently enable the measured diagnosis. Freeze a fresh attempt after source/policy changes. The later observer-lifecycle resource confirmation explicitly adopts historical control/G0 receipts and verifies all nine token sequences plus the entire88-source file before reusing MT judgments. Adopted outputs are never labeled newly inferred.

Gemma processes all88 original audio cuts in each seed, with fresh KV per request and a persistent model. The official Japanese-transcription/newline/`Chinese: ` protocol is frozen before inference. Missing/duplicate/empty fields, kana in Chinese, markup and length termination are rejected. The complete audio tower/projector is instrumented; actual placeholders must equal projection frames. One observed128-sample padding mismatch was repaired by passing640 to the real feature-extractor call, preserving original PCM and valid mask while adding invalid right padding. Published BF16/packed projection weights are retained; float32 activations are recorded separately.

Wrap commands with `p0.p2.resources`; resource options precede the output path. Gemma quality uses176 requests/750.776s total sliced audio; Vox ASR uses476.058s continuous audio; native MT uses176 committed-source rows, including24 empty-source no-op rows. Failed/interrupted groups retain planned denominators but are not used for per-completed-request comparisons. Component peaks must not be added into a full-chain figure.

`python -m p0.p2.b3_postprocess --run-dir RUN packet` creates all34-unit review material. Save independent/root judgments and reconcile them into `reviews/adjudication.json`; `score` computes route-specific gates. New native spans are linked after inference, without feeding reference boundaries or text to the committer. `b3_export.py` validates archived snapshots/receipts and exports all attempts, including failures and resource corrections. See [B3 results](../../docs/p2-b3-results-2026-11-09.md). Candidate realtime/Chrome/accept/holdout remain unimplemented and gated; no qualified B3 candidate means no automatic B4/B5 run.

## B4 AST-G4-ZH boundary

`AST-G4-ZH` occupies the single `B4-1` formal slot. The predeclared protocol-cost exception permits this test after official-format completion; it does not make the parent6/8 quality-qualified. `b4.py` freezes the unchanged model/runtime, original88 cuts, seeds and freshly rendered prompt hashes. Slot reservation is idempotent, persists in the global registry, and is not refunded on a task failure. The fixed prompt/parser and budgets are recorded in the [preregistration](../../docs/p2-b4-gemma-zh-protocol-2026-11-09.md).

```sh
.model-cache/p2-b3-gemma-env/bin/python -m p0.p2.b4_worker --run-dir "$B4_RUN" --render-locks
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B4_RUN" freeze --phase B4
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B4_RUN" probe --experiment B0-CURRENT --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B4_RUN" probe --experiment B0-CURRENT --mode fixed-text
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B4_RUN" probe --experiment AST-G4-OFFICIAL --mode fixed-cuts
engine/.venv/bin/python -m p0.p2.cli --run-dir "$B4_RUN" preflight --experiment AST-G4-ZH
```

Prepare the same Gemma snapshot/metadata and original official prompt under `sources/` first. Render locks use the fixed MLX Gemma4Processor, which is the actual inference processor, without loading model weights. Full official176 output/token/feature parity with B3 is mandatory. The measured raw `sources/drive.py` wraps all commands serially in the existing250ms CPU/GPU/footprint/RSS/pressure monitor and enforces setup/quality/whole budgets.

Only a completed speech/structure G0 permits the candidate `probe --mode fixed-cuts`. Nonspeech valid captions additionally set safety_fail and block engineering; an English/meta acknowledgement is not automatically a speech hallucination. A task-failed G0 stops the full176 run. For that observed outcome use the post-inference exit exporter, not the full-quality scoring path:

```sh
engine/.venv/bin/python -m p0.p2.b4_outcome --run-dir "$B4_RUN" --out docs/experiments/p2-b4-gemma-zh-2026-11-09
```

`b4_analysis` contains the conditional full88x2 packet/score/cost path; it was not executed in this failed G0. The actual outcome is `failed_task`, not0/34. Official first-Chinese detection ignores Japanese Han before the line-start Chinese marker. Internal readable-Han times are not correct/stable/DOM latency; invalid final outputs never count as usable subtitles. See [B4 result](../../docs/p2-b4-gemma-zh-results-2026-11-09.md).
