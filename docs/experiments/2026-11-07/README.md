# 2026-11-07 exploration evidence summary

[Result report](../../exploration-results-2026-11-07.md) records the E0–E5 experiments and why E6/E7 were not entered.

- `summary.json`: paired semantic counts, uncertainty ranges, stage decisions and model revisions.
- `resource-comparison.csv` / `.json`: all 19 resource runs, including 17 successful executions and two repaired preflight failures. CPU100% is one core; GPU and GPU shared memory are host-wide counters, not isolated model allocation.
- `validation.json`: the local experiment evidence validation result, before committing this batch.
- `commit-validation.json`: source submission checks. The isolated Chrome157 run verified caption diagnostics and pause/seek recovery, but its fullscreen request was denied with `TypeError: not granted`; the same debug launch without the extension reproduced that denial. Fullscreen and later lifecycle checks are not claimed to have passed.

Semantic reviews are AI text reviews. Audio listening is deferred. These offline numbers do not establish first-correct-caption or sentence-end latency at 1x. Ranges retain borderline reference judgements. Supplemental source-text diagnostics are not blind tests.

Raw audio, video, transcripts, model weights, tokens, absolute local paths and per-event traces stay in the ignored local evidence directory. Reproduction requires the licensed source material described in the result report.
