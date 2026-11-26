"""T4 & T5 ASR runner: frozen semantic-unit spans, NeMo A0 ablations, and G3 ASR challenger screening."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

from p0.stage8_eval.contracts import load_manifest, sha256_file
from p0.stage8_eval.score import nearest_rank_quantile, score_asr
from subtitle_service.audio_span_contract import RATE, right_collar_delay_ms
from subtitle_service.quality_types import AudioSpan


def build_ep01_dev60_units(
    stratified_120_path: Path,
    *,
    out_path: Path | None = None,
    per_category: int = 12,
) -> list[dict[str, Any]]:
    """Build deterministic frozen 60-unit EP01 dev cohort (12 per category, preserving short <=4 cues)."""
    raw_rows = json.loads(stratified_120_path.read_text(encoding="utf-8"))
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(raw_rows, key=lambda r: int(r["cue_idx"])):
        by_cat.setdefault(str(row["category"]), []).append(row)

    units: list[dict[str, Any]] = []
    for cat in sorted(by_cat):
        bucket = by_cat[cat]
        shorts = [r for r in bucket if len(r["clean_jp"].strip()) <= 5]
        others = [r for r in bucket if len(r["clean_jp"].strip()) > 5]
        chosen = (shorts[:2] + others)[:per_category]
        if len(chosen) < per_category:
            chosen = bucket[:per_category]
        for r in chosen:
            st_s = float(r["start_s"])
            en_s = float(r["end_s"])
            units.append({
                "unit_id": f"ep01-c{int(r['cue_idx']):04d}",
                "cue_idx": int(r["cue_idx"]),
                "category": cat,
                "start_s": st_s,
                "end_s": en_s,
                "start_sample": int(round(st_s * RATE)),
                "end_sample": int(round(en_s * RATE)),
                "reference_ja": str(r["clean_jp"]),
                "gold_zh": str(r.get("gold_zh", "")),
                "eligible": True,
            })
    units.sort(key=lambda u: u["start_sample"])
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            "".join(json.dumps(u, ensure_ascii=False) + "\n" for u in units),
            encoding="utf-8",
        )
    return units


def run_asr_units(
    adapter,
    pcm_f32: np.ndarray,
    units: list[dict[str, Any]],
    *,
    left_context_s: float = 0.0,
    right_collar_ms: int = 0,
    options: dict[str, Any] | None = None,
    session_id: str = "dev60",
) -> dict[str, Any]:
    """Run causal same-span ASR inference serially on frozen units without future leakage."""
    total_samples = int(len(pcm_f32))
    left_samples = int(round(max(0.0, left_context_s) * RATE))
    right_samples = int(round(max(0, right_collar_ms) * RATE / 1000.0))

    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    predict_ms: list[float] = []
    decode_ms: list[float] = []
    buckets: dict[int, int] = {}
    empty_count = 0
    collar_delays: list[float] = []
    peak_mlx = 0.0

    for unit in units:
        t_start = int(unit["start_sample"])
        t_end = min(total_samples, int(unit["end_sample"]))
        in_start = max(0, t_start - left_samples)
        in_end = min(total_samples, t_end + right_samples)
        span = AudioSpan(
            session_id=session_id,
            epoch=0,
            target_start=t_start,
            target_end=t_end,
            input_start=in_start,
            input_end=in_end,
            captured_end=in_end,
            sample_rate=RATE,
        )
        collar_delays.append(right_collar_delay_ms(span))
        hyp = adapter.transcribe(pcm_f32[in_start:in_end], span, options=options)
        txt = hyp.text_ja.strip()
        if not txt:
            empty_count += 1
        tot_ms = float(hyp.timing.get("total_ms", 0.0))
        latencies.append(tot_ms)
        if "predict_ms" in hyp.timing:
            predict_ms.append(float(hyp.timing["predict_ms"]))
        if "decode_ms" in hyp.timing:
            decode_ms.append(float(hyp.timing["decode_ms"]))
        if "bucket_mel" in hyp.timing:
            bm = int(hyp.timing["bucket_mel"])
            buckets[bm] = buckets.get(bm, 0) + 1
        peak_mlx = max(peak_mlx, float(hyp.timing.get("peak_mlx_mib", 0.0)))

        cer_v2_row = score_asr(unit["reference_ja"], txt, "cer_v2")
        rows.append({
            "unit_id": unit["unit_id"],
            "cue_idx": unit.get("cue_idx"),
            "category": unit.get("category"),
            "target_start": t_start,
            "target_end": t_end,
            "input_start": in_start,
            "input_end": in_end,
            "reference_ja": unit["reference_ja"],
            "hyp_ja": txt,
            "cer_v2": cer_v2_row["cer"],
            "S": cer_v2_row["S"],
            "D": cer_v2_row["D"],
            "I": cer_v2_row["I"],
            "N": cer_v2_row["N"],
            "timing": hyp.timing,
            "flags": hyp.flags,
        })

    ref_all = "".join(str(u["reference_ja"]) for u in units)
    hyp_all = "".join(r["hyp_ja"] for r in rows)
    cer_v2_full = score_asr(ref_all, hyp_all, "cer_v2")
    legacy_full = score_asr(ref_all, hyp_all, "legacy_cer")
    summary = {
        "units": len(rows),
        "empty_count": empty_count,
        "left_context_s": left_context_s,
        "right_collar_ms": right_collar_ms,
        "extra_collar_delay_ms": round(sum(collar_delays) / max(1, len(collar_delays)), 2),
        "latency_p50_ms": nearest_rank_quantile(latencies, 0.50),
        "latency_p95_ms": nearest_rank_quantile(latencies, 0.95),
        "predict_p50_ms": nearest_rank_quantile(predict_ms, 0.50) if predict_ms else None,
        "decode_p50_ms": nearest_rank_quantile(decode_ms, 0.50) if decode_ms else None,
        "buckets": buckets,
        "peak_mlx_mib": round(peak_mlx, 2) if peak_mlx > 0 else None,
        "cer_v2": cer_v2_full,
        "legacy_cer": legacy_full,
    }
    return {"summary": summary, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 8 ASR same-span runner")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    if args.arm != (manifest.get("arm") or {}).get("arm_id"):
        sys.stderr.write(f"arm mismatch: --arm={args.arm} != manifest arm_id\n")
        return 2
    manifest_out = (manifest.get("execution") or {}).get("output_dir")
    if manifest_out and Path(args.out).resolve() != Path(manifest_out).resolve():
        sys.stderr.write(f"out mismatch: --out={args.out} != manifest output_dir={manifest_out}\n")
        return 2

    sys.stderr.write(f"bench_asr CLI validated for arm={args.arm}; use batch runner for multi-arm sweeps.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
