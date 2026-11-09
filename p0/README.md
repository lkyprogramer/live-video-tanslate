# Local subtitle experiment probes

The current exploration is archived. Unused model/runtime downloads were removed on2026-11-09; the active reazon-hy runtime, source probes and raw evidence remain. Historical commands require restoring their recorded dependencies first. See the [cleanup and smoke record](../docs/local-runtime-cleanup-2026-11-09.md); do not restart model exploration without a new request.

These programs implement the experiments in [the exploration plan](../docs/exploration-plan.md). They do not enable experimental models in the production service. Latest decisions and public resource aggregates are in [the result report](../docs/exploration-results-2026-11-07.md).

Run Python commands from the repository root. Use the engine environment with `PYTHONPATH=p0:engine/src` unless the experiment specifies an isolated environment. Model revisions, inference settings, prompts, seeds, input hashes and runtime versions belong in each run's manifest before inference starts.

## Inputs and evidence

The original audio preparation and source descriptions are in [p0.md](../docs/p0.md). Public source material has its own terms. Keep downloaded sources, prepared PCM, weights, credentials and per-event traces in the ignored local directories.

Many follow-up probes consume an existing frozen run containing `frozen.json`, `slices.jsonl`, `reazon.jsonl` and `units.jsonl`. A fresh clone does not include these raw local records. Restore or recreate the licensed inputs and their manifests before executing a probe; an empty output directory is not a substitute for the required baseline.

Create a new persistent directory under `p0/evidence/` for each experiment. Existing output files normally reject overwrite. Keep `test/` sealed until the plan's blind-test gate. Source-text translation diagnostics and actual-ASR-input results have different purposes and must remain separately labeled.

## Probe catalog

| Entry | Purpose |
| --- | --- |
| `run.py` | Original 1x WhisperLiveKit replay; mixed, full-MLX and LocalAgreement variants |
| `next_asr.py` | Freeze VAD cuts and compare Reazon, beam/FP32, Haas and Parakeet on identical PCM |
| `next_mt.py` | Hy-MT2 prompt, greedy, context and glossary comparisons, with fixed paired seeds |
| `next_boundaries.py`, `next_kotoba.py`, `next_qwen.py` | Boundary diagnostics and isolated alternative ASR runs |
| `next_moonshine.py`, `next_pengcheng.py`, `next_whisperkit.py` | Experimental adapters using the v1 service/replay interface |
| `pengcheng_decoder.py` | Prepare the matching streaming decoder graph |
| `apple-translation.swift` | Installed Japanese to simplified-Chinese Apple Translation probe |
| `parakeet/`, `whisperkit/` | Swift probes with pinned package revisions |
| `next_trace.py` | Correlate model, gateway, WS, player and DOM events |
| `exploration.py` | E0 freeze, E1 VAD/tail checks, E2 raw-token audit, E3 VAD models and E4 hotwords |
| `explore_new_asr.py`, `explore_index_mt.py` | E5 pinned ASR and local llama.cpp translation screening |
| `exploration_resources.py` | Recover every marked resource interval, including repeated phase names |
| `exploration_score.py` | Recompute this run's written AI semantic verdicts |
| `exploration_validate.py` | Validate saved evidence and frozen source hashes, reporting later checkout drift separately |
| `next_vad_packet.py` | Supplemental v4/v6.2 comparison with original 200ms packet calls and native EOF |
| `p2/cli.py` | P2 B0 reproduction/replay/acceptance and B1/B2/B3 bounded offline screening; candidate realtime/holdout remain unsupported |
| `p2/export_resources.py` | Recompute B0 resource views with CPU peaks and request-based offline MT normalization, retaining original samples |
| `p2/b1.py`, `p2/adapters/` | Four B1 adapters with paired current-control parity, frozen inputs/runtimes and explicit failure states; see [execution notes](p2/README.md) |
| `p2/b1_scoring.py`, `p2/b1_postprocess.py` | Explicit reviewed34-unit gates and separately versioned offline integrity/resource export; [B1 results](../docs/p2-b1-results-2026-11-08.md) |
| `p2/b2.py`, `p2/b2_postprocess.py` | Two CPU ASR configurations, exact same-round Reazon/M0 controls, shared MT/scoring and serial resource export; [B2 results](../docs/p2-b2-results-2026-11-09.md) |
| `p2/b3.py`, `p2/b3_worker.py`, `p2/b3_protocol.py` | Isolated native Vox PCM sessions and complete Gemma audio AST, frozen Chinese protocol and all34-unit review; [B3 results](../docs/p2-b3-results-2026-11-09.md) |
| `p2/b3_vox_cache.py`, `p2/b3_export.py` | Explicit bounded-cache diagnosis; immutable failed attempts, observer lifecycle correction and component-resource export |
| `p2/b4*.py` | Single registered Gemma Chinese-only protocol ablation, strict paired controls and task-failed G0 exit with resource evidence; [B4 result](../docs/p2-b4-gemma-zh-results-2026-11-09.md) |

Use each program's `--help` for its actual arguments. Qwen/Granite use an isolated MLX environment. The VAD probe additionally needs its recorded Kaldi dependencies. Swift probes require compatible macOS/Xcode and their pinned model/tokenizer assets. Build a Swift package using `xcodebuildmcp swift-package build --package-path <absolute-package-path> --target-name <target> --configuration release`.

`exploration_score.py` is a replay of explicit text-review decisions for the recorded outputs. It is not an automatic semantic judge. New model outputs require new per-unit review before those decisions can be reused. AI text review does not turn unverified audio or source timestamps into human gold.

## Resource and integrity checks

Wrap one active model combination at a time:

```sh
PYTHONPATH=p0:engine/src engine/.venv/bin/python p0/monitor.py p0/evidence/NEW/resources -- engine/.venv/bin/python p0/exploration.py p0/evidence/NEW E1
```

This example requires E0's inputs and run manifest to exist. E1's VAD probe, ASR decoding and MT comparison are separate operations; use `--step decode --name packet`, `--name window`, `--name window-tail`, then `--step translate --name window` as specified by the plan. For the E3 official maximum use `E3 --name firered-default`, followed by decoding/translation with `--variant official-default --name firered`.

CPU100% is one core. Process-tree physical footprint and host GPU shared memory may overlap and are reported separately. Host GPU counters include unrelated applications. Resource reports retain the idle baseline, sampling interval, command, phase boundaries and raw samples. Offline decoding/translation time does not establish 1x first-correct-caption or sentence-end latency.

To revalidate the recorded 2026-11-07 run without rewriting its executed-source snapshot or previous report:

```sh
PYTHONPATH=p0:engine/src engine/.venv/bin/python p0/exploration_validate.py p0/evidence/exploration-20261107-0312 --output p0/evidence/revalidation-NEW.json
```

Choose a fresh report path. Later source drift is reported rather than silently rewriting the historical source hashes. The validator is specific to the recorded suite and stage decisions; another run requires its own input and adjudication checks.
