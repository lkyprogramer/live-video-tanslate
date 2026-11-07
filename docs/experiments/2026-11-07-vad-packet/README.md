# Silero v6.2 packet-call supplement

[Result report](../../vad-packet-results-2026-11-07.md) records the paired comparison with v4.

`summary.json` contains the frozen settings, lower/upper semantic counts, paired new critical errors and component workloads. The ranges represent borderline grading sensitivity, not statistical confidence intervals. Historical denominator34 and seed11/29 are retained. The common low-confidence unit158-160 also has a symmetric exclusion sensitivity.

`resource-comparison.csv` and `.json` contain six successful offline resource runs. VAD and ASR process all nine PCM files; MT uses the same three scored clips as the original34-unit experiment. Different output segment counts imply different MT workloads. CPU100% is one core; GPU and GPU shared-memory counters are host-wide and can overlap process footprint.

`validation.json` confirms input/source hashes, exact baseline reproduction, identical MT settings, native EOF accounting and the failed entry gate. Raw audio, full transcripts and per-event traces remain local. First-caption and sentence-end latency at1x were not measured because the candidate did not qualify.
