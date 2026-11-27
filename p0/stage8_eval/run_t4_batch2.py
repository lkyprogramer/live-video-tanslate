"""T4 Runner: Build frozen EP01 60-unit cohort and run NeMo-v2 A0 boundary/context/collar ablations."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import numpy as np
import sherpa_onnx

from p0.stage8_eval.bench_asr import build_ep01_dev60_units, run_asr_units
from p0.stage8_eval.contracts import sha256_file
from p0.stage8_eval.model_adapters import NeMoAneASRAdapter
from p0.stage8_eval.score import score_asr
from subtitle_service.audio_span_contract import RATE
from subtitle_service.local_pipeline import _SmartSegmenter, _has_meaningful_ja, silero_config

MAIN_ROOT = Path("/Users/luo/Documents/github/jp-live-video-tanslate")
CACHE_ROOT = MAIN_ROOT / ".model-cache"
MKV_PATH = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.mkv")
STRATIFIED_120_PATH = MAIN_ROOT / "p0/evidence/stage6-ep01-20261126/stratified_120_audit.json"
ANE_DIR = CACHE_ROOT / "nemo-ane"
GGUF_PATH = CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
VAD_PATH = CACHE_ROOT / "reazonspeech-assets/silero_vad.onnx"
MAIN_END_S = 2690.0
FRAME_64MS_BYTES = 2048  # 1024 samples * 2 bytes


def ensure_ep01_pcm(pcm_path: Path) -> str:
    if not pcm_path.is_file() or pcm_path.stat().st_size < int(MAIN_END_S * RATE * 2 * 0.99):
        pcm_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call([
            "ffmpeg", "-v", "error", "-y", "-t", str(int(MAIN_END_S)),
            "-i", str(MKV_PATH), "-ac", "1", "-ar", str(RATE), "-f", "s16le", str(pcm_path),
        ])
    return sha256_file(pcm_path)


def run_a0_vad_projection(
    adapter: NeMoAneASRAdapter,
    raw_pcm_bytes: bytes,
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run current Stage 7 VAD segmenter over EP01 and project segments onto frozen 60 units."""
    vad = sherpa_onnx.VoiceActivityDetector(silero_config(VAD_PATH, 180), buffer_size_in_seconds=120)
    seg = _SmartSegmenter(vad, max_segment_s=2.8, overlap_ms=350)
    chunks = []
    for off in range(0, len(raw_pcm_bytes), FRAME_64MS_BYTES):
        chunks.extend(seg.push(raw_pcm_bytes[off : off + FRAME_64MS_BYTES]))
    chunks.extend(seg.push(None))

    vad_segs: list[dict[str, Any]] = []
    for ch in chunks:
        en = ch.start_sample + len(ch.samples)
        txt = adapter.bridge.transcribe(
            ch.samples,
            pad_samples=adapter.pad_samples,
            use_beam=2,
            start_sample=ch.start_sample,
            dedup_before_sample=ch.dedup_before_sample,
        )
        if txt and not _has_meaningful_ja(txt):
            txt = ""
        if txt:
            vad_segs.append({
                "start_sample": ch.start_sample,
                "end_sample": en,
                "text_ja": txt,
                "timings": dict(adapter.bridge.last_timings or {}),
            })

    # Project VAD segments to each unit by midpoint or overlap (keeping all 60 units in denominator)
    rows = []
    empty_units = 0
    for u in units:
        us, ue = int(u["start_sample"]), int(u["end_sample"])
        matched = []
        for vs in vad_segs:
            mid = (vs["start_sample"] + vs["end_sample"]) * 0.5
            ov = min(ue, vs["end_sample"]) - max(us, vs["start_sample"])
            if (us <= mid < ue) or ov >= max(1600, int(0.45 * (ue - us))):
                matched.append(vs)
        txt = "".join(m["text_ja"] for m in matched).strip()
        if not txt:
            empty_units += 1
        sc = score_asr(u["reference_ja"], txt, "cer_v2")
        rows.append({
            "unit_id": u["unit_id"],
            "cue_idx": u["cue_idx"],
            "reference_ja": u["reference_ja"],
            "hyp_ja": txt,
            "cer_v2": sc["cer"],
            "S": sc["S"],
            "D": sc["D"],
            "I": sc["I"],
            "N": sc["N"],
        })

    ref_all = "".join(u["reference_ja"] for u in units)
    hyp_all = "".join(r["hyp_ja"] for r in rows)
    cer_v2_concat = score_asr(ref_all, hyp_all, "cer_v2")
    sum_s = sum(int(r["S"]) for r in rows)
    sum_d = sum(int(r["D"]) for r in rows)
    sum_i = sum(int(r["I"]) for r in rows)
    sum_n = sum(int(r["N"]) for r in rows)
    cer_v2_per_unit = {
        "S": sum_s,
        "D": sum_d,
        "I": sum_i,
        "N": sum_n,
        "cer": round((sum_s + sum_d + sum_i) / max(1, sum_n), 4),
    }
    return {
        "summary": {
            "units": len(rows),
            "vad_total_chunks": len(chunks),
            "vad_kept_segments": len(vad_segs),
            "empty_count": empty_units,
            "empty_unit_count": empty_units,
            "cer_v2": cer_v2_concat,
            "cer_v2_concat": cer_v2_concat,
            "cer_v2_per_unit": cer_v2_per_unit,
            "legacy_cer": score_asr(ref_all, hyp_all, "legacy_cer"),
        },
        "rows": rows,
    }


def main() -> int:
    ev_dir = MAIN_ROOT / "p0/evidence/stage8-batch2-20261126"
    out_dir = ev_dir / "t4_asr"
    out_dir.mkdir(parents=True, exist_ok=True)

    pcm_path = ev_dir / "ep01_16k.pcm"
    pcm_sha = ensure_ep01_pcm(pcm_path)
    raw_bytes = pcm_path.read_bytes()
    pcm_f32 = np.frombuffer(raw_bytes, dtype="<i2").astype(np.float32) / 32768.0

    units_path = ev_dir / "ep01_dev60_units.jsonl"
    units = build_ep01_dev60_units(STRATIFIED_120_PATH, out_path=units_path, per_category=12)
    units_sha = sha256_file(units_path)

    adapter = NeMoAneASRAdapter(ANE_DIR, GGUF_PATH, decoding="hybrid", pad_ms=500)
    results: dict[str, Any] = {}
    try:
        print("[T4] Running A0-vad projection on frozen 60 units...", flush=True)
        t0 = time.perf_counter()
        a0_vad = run_a0_vad_projection(adapter, raw_bytes, units)
        a0_vad["wall_s"] = round(time.perf_counter() - t0, 1)
        (out_dir / "a0_vad.json").write_text(json.dumps(a0_vad, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results["A0_vad"] = a0_vad["summary"]

        configs = [
            ("A0_oracle_hybrid_pad500", 0.0, 0, {"decoding": "hybrid", "pad_ms": 500}),
            ("A0_oracle_greedy_pad500", 0.0, 0, {"decoding": "greedy", "pad_ms": 500}),
            ("A0_oracle_beam_pad500", 0.0, 0, {"decoding": "beam", "pad_ms": 500}),
            ("A0_oracle_hybrid_pad350", 0.0, 0, {"decoding": "hybrid", "pad_ms": 350}),
            ("A0_oracle_left4s", 4.0, 0, {"decoding": "hybrid", "pad_ms": 500}),
            ("A0_oracle_left6s", 6.0, 0, {"decoding": "hybrid", "pad_ms": 500}),
            ("A0_oracle_collar160", 0.0, 160, {"decoding": "hybrid", "pad_ms": 500}),
            ("A0_oracle_collar320", 0.0, 320, {"decoding": "hybrid", "pad_ms": 500}),
        ]
        for arm_name, left_s, collar_ms, opts in configs:
            print(f"[T4] Running {arm_name}...", flush=True)
            res = run_asr_units(
                adapter,
                pcm_f32,
                units,
                left_context_s=left_s,
                right_collar_ms=collar_ms,
                options=opts,
            )
            (out_dir / f"{arm_name.lower()}.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            results[arm_name] = res["summary"]
            print(
                f"[T4] {arm_name} done:",
                json.dumps(
                    {
                        "cer_v2": res["summary"]["cer_v2"]["cer"],
                        "S/D/I": [res["summary"]["cer_v2"]["S"], res["summary"]["cer_v2"]["D"], res["summary"]["cer_v2"]["I"]],
                        "legacy": res["summary"]["legacy_cer"]["cer"],
                        "p50": res["summary"]["latency_p50_ms"],
                        "p95": res["summary"]["latency_p95_ms"],
                        "collar": res["summary"]["extra_collar_delay_ms"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        adapter.close()

    summary_doc = {
        "schema_version": "stage8-t4-asr-summary-v1",
        "pcm_path": str(pcm_path),
        "pcm_sha256": pcm_sha,
        "units_path": str(units_path),
        "units_sha256": units_sha,
        "arms": results,
    }
    (out_dir / "t4_asr_summary.json").write_text(
        json.dumps(summary_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("[T4] COMPLETE.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
