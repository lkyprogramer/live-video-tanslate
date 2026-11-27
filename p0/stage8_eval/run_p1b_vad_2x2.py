"""P1-B Runner: Clean apples-to-apples 2x2 diagnostic ({VAD, Oracle} x {pad 350ms, pad 500ms}) on EP01 0-600s."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import time
from typing import Any

import numpy as np
import sherpa_onnx

from p0.stage8_eval.contracts import sha256_file
from p0.stage8_eval.model_adapters import NeMoAneASRAdapter
from p0.stage8_eval.score import nearest_rank_quantile, score_asr
from subtitle_service.audio_span_contract import RATE
from subtitle_service.local_pipeline import _SmartSegmenter, _has_meaningful_ja, silero_config

MAIN_ROOT = Path("/Users/luo/Documents/github/jp-live-video-tanslate")
CACHE_ROOT = MAIN_ROOT / ".model-cache"
MKV_PATH = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.mkv")
ANE_DIR = CACHE_ROOT / "nemo-ane"
GGUF_PATH = CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
VAD_PATH = CACHE_ROOT / "reazonspeech-assets/silero_vad.onnx"
WINDOW_END_S = 600.0
FRAME_64MS_BYTES = 2048  # 1024 samples * 2 bytes


def group_contiguous_scenes(cues: list[dict[str, Any]], *, gap_s: float = 2.0) -> list[dict[str, Any]]:
    """Group chronologically ordered cues separated by >= gap_s silence into scenes."""
    ordered = sorted(cues, key=lambda c: (float(c["start_s"]), float(c["end_s"])))
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for cue in ordered:
        if cur and (float(cue["start_s"]) - float(cur[-1]["end_s"])) >= gap_s:
            groups.append(cur)
            cur = []
        cur.append(cue)
    if cur:
        groups.append(cur)

    scenes: list[dict[str, Any]] = []
    for idx, grp in enumerate(groups, start=1):
        st_s = float(grp[0]["start_s"])
        en_s = float(grp[-1]["end_s"])
        ref_ja = "".join(str(c.get("clean_text") or c.get("reference_ja") or "") for c in grp)
        scenes.append({
            "scene_id": f"sc-{idx:03d}",
            "start_s": st_s,
            "end_s": en_s,
            "start_sample": int(round(st_s * RATE)),
            "end_sample": int(round(en_s * RATE)),
            "cue_count": len(grp),
            "cues": grp,
            "reference_ja": ref_ja,
        })
    return scenes


def assign_segments_to_scenes(
    segments: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    *,
    margin_s: float = 0.5,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Assign each segment uniquely by midpoint (+- margin_s) to at most one scene."""
    by_scene: dict[str, list[dict[str, Any]]] = {str(sc["scene_id"]): [] for sc in scenes}
    orphans: list[dict[str, Any]] = []
    margin_samples = int(round(margin_s * RATE))
    ordered_scenes = sorted(scenes, key=lambda s: int(s["start_sample"]))

    for seg in segments:
        st = int(seg["start_sample"])
        en = int(seg["end_sample"])
        mid = (st + en) * 0.5
        cands: list[tuple[int, float, str]] = []
        for sc in ordered_scenes:
            sc_st = int(sc["start_sample"])
            sc_en = int(sc["end_sample"])
            if (sc_st - margin_samples) <= mid <= (sc_en + margin_samples):
                ov = max(0, min(en, sc_en) - max(st, sc_st))
                sc_mid = (sc_st + sc_en) * 0.5
                cands.append((ov, -abs(mid - sc_mid), str(sc["scene_id"])))
        if not cands:
            orphans.append(seg)
            continue
        cands.sort(key=lambda t: (t[0], t[1]), reverse=True)
        by_scene[cands[0][2]].append(seg)
    return by_scene, orphans


def detect_unprotected_overlaps(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect consecutive emitted chunks that re-cover earlier audio without dedup protection."""
    overlaps: list[dict[str, Any]] = []
    max_end = -1
    max_end_reason = ""
    for idx, ch in enumerate(chunks):
        st = int(ch["start_sample"])
        en = int(ch["end_sample"])
        dedup = int(ch.get("dedup_before_sample", 0))
        if max_end > 0 and st < max_end and dedup < max_end:
            uncovered_end = min(en, max_end)
            uncovered_start = max(st, dedup)
            if uncovered_end > uncovered_start:
                overlaps.append({
                    "chunk_idx": idx,
                    "start_sample": st,
                    "end_sample": en,
                    "dedup_before_sample": dedup,
                    "prev_end_sample": max_end,
                    "overlap_ms": round((uncovered_end - uncovered_start) * 1000.0 / RATE, 1),
                    "cut_reason": str(ch.get("cut_reason", "")),
                    "prev_cut_reason": max_end_reason,
                })
        if en > max_end:
            max_end = en
            max_end_reason = str(ch.get("cut_reason", ""))
    return overlaps


def _transcribe_items_no_collar(
    adapter: NeMoAneASRAdapter,
    items: list[dict[str, Any]],
    *,
    pad_ms: int,
) -> list[dict[str, Any]]:
    pad_samples = int(round(pad_ms * RATE / 1000.0))
    out: list[dict[str, Any]] = []
    for it in items:
        pcm_slice = np.ascontiguousarray(it["samples_f32"], dtype=np.float32)
        t0 = time.perf_counter()
        raw_txt = adapter.bridge.transcribe(
            pcm_slice,
            pad_samples=pad_samples,
            use_beam=2,
            start_sample=int(it["start_sample"]),
            dedup_before_sample=int(it.get("dedup_before_sample", 0)),
        )
        wall_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        txt = raw_txt.strip() if raw_txt and _has_meaningful_ja(raw_txt) else ""
        timings = dict(adapter.bridge.last_timings or {})
        out.append({
            **{k: v for k, v in it.items() if k != "samples_f32"},
            "text_ja": txt,
            "wall_ms": wall_ms,
            "native_total_ms": round(float(timings.get("total_ms", wall_ms)), 2),
            "bucket_mel": int(timings.get("bucket_mel", 0)),
            "seg_len_s": round(len(pcm_slice) / RATE, 3),
        })
    return out


def _score_scenes(
    scenes: list[dict[str, Any]],
    segs_by_scene: dict[str, list[dict[str, Any]]],
    orphans: list[dict[str, Any]],
    all_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    scene_rows: list[dict[str, Any]] = []
    empty_scene_count = 0
    for sc in scenes:
        sid = sc["scene_id"]
        segs = segs_by_scene.get(sid, [])
        hyp_ja = "".join(s["text_ja"] for s in segs).strip()
        if not hyp_ja:
            empty_scene_count += 1
        sc_res = score_asr(sc["reference_ja"], hyp_ja, "cer_v2")
        cut_reasons = Counter(str(s.get("cut_reason", "oracle")) for s in segs)
        scene_rows.append({
            "scene_id": sid,
            "start_s": sc["start_s"],
            "end_s": sc["end_s"],
            "cue_count": sc["cue_count"],
            "segment_count": len(segs),
            "cut_reasons": dict(cut_reasons),
            "reference_ja": sc["reference_ja"],
            "hyp_ja": hyp_ja,
            "cer_v2": sc_res["cer"],
            "S": sc_res["S"],
            "D": sc_res["D"],
            "I": sc_res["I"],
            "N": sc_res["N"],
        })

    sum_s = sum(r["S"] for r in scene_rows)
    sum_d = sum(r["D"] for r in scene_rows)
    sum_i = sum(r["I"] for r in scene_rows)
    sum_n = sum(r["N"] for r in scene_rows)
    cer_per_scene = {
        "S": sum_s,
        "D": sum_d,
        "I": sum_i,
        "N": sum_n,
        "cer": round((sum_s + sum_d + sum_i) / max(1, sum_n), 4),
    }
    ref_all = "".join(r["reference_ja"] for r in scene_rows)
    hyp_all = "".join(r["hyp_ja"] for r in scene_rows)
    cer_concat = score_asr(ref_all, hyp_all, "cer_v2")
    lats = [float(s["wall_ms"]) for s in all_segments]
    seg_lens = [float(s.get("seg_len_s", 0.0)) for s in all_segments]
    bucket_mel_counts = dict(sorted(Counter(int(s.get("bucket_mel", 0)) for s in all_segments).items()))
    orphan_chars = sum(len(s["text_ja"]) for s in orphans)
    return {
        "summary": {
            "scenes": len(scene_rows),
            "segments": len(all_segments),
            "empty_scene_count": empty_scene_count,
            "orphan_chunk_count": len(orphans),
            "orphan_chars": orphan_chars,
            "latency_p50_ms": nearest_rank_quantile(lats, 0.50),
            "latency_p95_ms": nearest_rank_quantile(lats, 0.95),
            "seg_len_p50_s": nearest_rank_quantile(seg_lens, 0.50) if seg_lens else None,
            "seg_len_p95_s": nearest_rank_quantile(seg_lens, 0.95) if seg_lens else None,
            "seg_len_max_s": round(max(seg_lens), 3) if seg_lens else None,
            "bucket_mel_counts": bucket_mel_counts,
            "cer_v2_per_scene": cer_per_scene,
            "cer_v2_concat": cer_concat,
        },
        "scene_rows": scene_rows,
        "orphans": orphans,
    }


def _collect_vad_items(raw_bytes: bytes, *, max_segment_s: float, overlap_ms: int = 350, pause_ms: int = 180) -> list[dict[str, Any]]:
    cfg = silero_config(VAD_PATH, pause_ms)
    cfg.silero_vad.max_speech_duration = max(4.0, float(max_segment_s) + 1.0)
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)
    seg = _SmartSegmenter(vad, max_segment_s=max_segment_s, overlap_ms=overlap_ms)
    vad_chunks_raw = []
    for off in range(0, len(raw_bytes), FRAME_64MS_BYTES):
        vad_chunks_raw.extend(seg.push(raw_bytes[off : off + FRAME_64MS_BYTES]))
    vad_chunks_raw.extend(seg.push(None))
    return [
        {
            "start_sample": int(ch.start_sample),
            "end_sample": int(ch.start_sample) + len(ch.samples),
            "dedup_before_sample": int(ch.dedup_before_sample),
            "cut_reason": str(ch.cut_reason),
            "samples_f32": ch.samples,
        }
        for ch in vad_chunks_raw
    ]


def _run_2x2(out_dir: Path, raw_bytes: bytes, pcm_f32: np.ndarray, pcm_sha: str, cues_600: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> int:
    vad_items = _collect_vad_items(raw_bytes, max_segment_s=2.8, overlap_ms=350, pause_ms=180)
    cut_reason_counts = dict(Counter(it["cut_reason"] for it in vad_items))
    unprotected_overlaps = detect_unprotected_overlaps(vad_items)

    oracle_items = [
        {
            "cue_idx": int(c["cue_idx"]),
            "start_sample": int(round(float(c["start_s"]) * RATE)),
            "end_sample": min(len(pcm_f32), int(round(float(c["end_s"]) * RATE))),
            "dedup_before_sample": 0,
            "cut_reason": "oracle_cue",
            "samples_f32": pcm_f32[int(round(float(c["start_s"]) * RATE)) : min(len(pcm_f32), int(round(float(c["end_s"]) * RATE)))],
        }
        for c in cues_600
    ]

    cue_to_scene: dict[int, str] = {}
    for sc in scenes:
        for c in sc["cues"]:
            cue_to_scene[int(c["cue_idx"])] = sc["scene_id"]

    adapter = NeMoAneASRAdapter(ANE_DIR, GGUF_PATH, decoding="hybrid", pad_ms=500)
    try:
        vad_350_segs = _transcribe_items_no_collar(adapter, vad_items, pad_ms=350)
        vad_500_segs = _transcribe_items_no_collar(adapter, vad_items, pad_ms=500)
        ora_350_segs = _transcribe_items_no_collar(adapter, oracle_items, pad_ms=350)
        ora_500_segs = _transcribe_items_no_collar(adapter, oracle_items, pad_ms=500)
    finally:
        adapter.close()

    vad_350_by_sc, vad_350_orphans = assign_segments_to_scenes(vad_350_segs, scenes)
    vad_500_by_sc, vad_500_orphans = assign_segments_to_scenes(vad_500_segs, scenes)

    def _group_oracle(segs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {sc["scene_id"]: [] for sc in scenes}
        for s in segs:
            grouped[cue_to_scene[int(s["cue_idx"])]].append(s)
        return grouped

    arms = {
        "VAD_pad350": _score_scenes(scenes, vad_350_by_sc, vad_350_orphans, vad_350_segs),
        "VAD_pad500": _score_scenes(scenes, vad_500_by_sc, vad_500_orphans, vad_500_segs),
        "Oracle_pad350": _score_scenes(scenes, _group_oracle(ora_350_segs), [], ora_350_segs),
        "Oracle_pad500": _score_scenes(scenes, _group_oracle(ora_500_segs), [], ora_500_segs),
    }

    vad500_rows = {r["scene_id"]: r for r in arms["VAD_pad500"]["scene_rows"]}
    ora500_rows = {r["scene_id"]: r for r in arms["Oracle_pad500"]["scene_rows"]}
    strat = {
        "has_forced_cut": {"S": 0, "D": 0, "I": 0, "N": 0, "ora_err": 0, "scenes": 0},
        "natural_only": {"S": 0, "D": 0, "I": 0, "N": 0, "ora_err": 0, "scenes": 0},
    }
    scene_diffs = []
    for sid, vr in vad500_rows.items():
        orr = ora500_rows[sid]
        cuts = vr["cut_reasons"]
        bucket = "has_forced_cut" if (cuts.get("energy_valley", 0) > 0 or cuts.get("vad_tail", 0) > 0) else "natural_only"
        strat[bucket]["scenes"] += 1
        strat[bucket]["S"] += vr["S"]
        strat[bucket]["D"] += vr["D"]
        strat[bucket]["I"] += vr["I"]
        strat[bucket]["N"] += vr["N"]
        strat[bucket]["ora_err"] += orr["S"] + orr["D"] + orr["I"]
        v_err = vr["S"] + vr["D"] + vr["I"]
        o_err = orr["S"] + orr["D"] + orr["I"]
        scene_diffs.append({
            "scene_id": sid,
            "start_s": vr["start_s"],
            "end_s": vr["end_s"],
            "cut_reasons": cuts,
            "edit_delta_vad_minus_oracle": v_err - o_err,
            "vad_cer": vr["cer_v2"],
            "oracle_cer": orr["cer_v2"],
            "vad_sdi": [vr["S"], vr["D"], vr["I"], vr["N"]],
            "oracle_sdi": [orr["S"], orr["D"], orr["I"], orr["N"]],
            "reference_ja": vr["reference_ja"],
            "vad_hyp_ja": vr["hyp_ja"],
            "oracle_hyp_ja": orr["hyp_ja"],
        })
    scene_diffs.sort(key=lambda d: d["edit_delta_vad_minus_oracle"], reverse=True)

    strat_summary = {
        k: {**v, "vad_cer": round((v["S"] + v["D"] + v["I"]) / max(1, v["N"]), 4), "oracle_cer": round(v["ora_err"] / max(1, v["N"]), 4)}
        for k, v in strat.items()
    }

    summary_doc = {
        "schema_version": "stage8-p1b-vad-2x2-v1",
        "window_s": [0.0, WINDOW_END_S],
        "pcm_sha256": pcm_sha,
        "cues": len(cues_600),
        "scenes": len(scenes),
        "cut_reason_counts": cut_reason_counts,
        "unprotected_overlap_count": len(unprotected_overlaps),
        "unprotected_overlaps": unprotected_overlaps,
        "arms": {k: v["summary"] for k, v in arms.items()},
        "stratified_vad500_vs_oracle500": strat_summary,
        "top_worst_scenes": scene_diffs[:5],
        "all_scene_diffs": scene_diffs,
    }
    (out_dir / "p1b_vad_2x2_summary.json").write_text(
        json.dumps(summary_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("=== P1-B 2x2 Summary (EP01 0-600s, 168 cues / 14 scenes) ===")
    for arm_name in ("VAD_pad350", "VAD_pad500", "Oracle_pad350", "Oracle_pad500"):
        s = arms[arm_name]["summary"]
        print(
            f"{arm_name:14s} | per_scene={s['cer_v2_per_scene']['cer']:.4f} "
            f"(S={s['cer_v2_per_scene']['S']} D={s['cer_v2_per_scene']['D']} I={s['cer_v2_per_scene']['I']} N={s['cer_v2_per_scene']['N']}) "
            f"| concat={s['cer_v2_concat']['cer']:.4f} | empty_sc={s['empty_scene_count']} "
            f"| orphans={s['orphan_chunk_count']} ({s['orphan_chars']}ch) "
            f"| p50={s['latency_p50_ms']}ms p95={s['latency_p95_ms']}ms"
        )
    print("cut_reason_counts:", cut_reason_counts)
    print("unprotected_overlap_count:", len(unprotected_overlaps))
    return 0


def _run_max_seg_sweep(out_dir: Path, raw_bytes: bytes, pcm_sha: str, cues_600: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> int:
    arm_results: dict[str, Any] = {}
    adapter = NeMoAneASRAdapter(ANE_DIR, GGUF_PATH, decoding="hybrid", pad_ms=500)
    try:
        for max_seg_s in (2.8, 3.6, 4.0, 4.5):
            key = f"max_seg_{max_seg_s:.1f}s"
            vad_items = _collect_vad_items(raw_bytes, max_segment_s=max_seg_s, overlap_ms=350, pause_ms=180)
            cut_counts = dict(Counter(it["cut_reason"] for it in vad_items))
            overlaps = detect_unprotected_overlaps(vad_items)
            segs = _transcribe_items_no_collar(adapter, vad_items, pad_ms=500)
            by_sc, orphans = assign_segments_to_scenes(segs, scenes)
            scored = _score_scenes(scenes, by_sc, orphans, segs)
            arm_results[key] = {
                "max_segment_s": max_seg_s,
                "cut_reason_counts": cut_counts,
                "unprotected_overlap_count": len(overlaps),
                **scored["summary"],
            }
    finally:
        adapter.close()

    doc = {
        "schema_version": "stage8-p1b-max-seg-sweep-v1",
        "window_s": [0.0, WINDOW_END_S],
        "pcm_sha256": pcm_sha,
        "cues": len(cues_600),
        "scenes": len(scenes),
        "fixed_params": {"pad_ms": 500, "overlap_ms": 350, "vad_pause_ms": 180, "decoding": "hybrid"},
        "arms": arm_results,
    }
    (out_dir / "p1b_max_seg_sweep_summary.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("=== P1-B max_segment_s Sweep (EP01 0-600s, 168 cues / 14 scenes, pad=500ms) ===")
    for k, s in arm_results.items():
        c = s["cer_v2_per_scene"]
        print(
            f"{k:14s} | cer={c['cer']:.4f} (S={c['S']} D={c['D']} I={c['I']} N={c['N']}) "
            f"| cuts={s['cut_reason_counts']} | overlaps={s['unprotected_overlap_count']} "
            f"| orphans={s['orphan_chunk_count']}({s['orphan_chars']}ch) "
            f"| len_p50={s['seg_len_p50_s']}s len_p95={s['seg_len_p95_s']}s max={s['seg_len_max_s']}s "
            f"| buckets={s['bucket_mel_counts']} | lat_p50={s['latency_p50_ms']}ms lat_p95={s['latency_p95_ms']}ms"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1-B VAD boundary & max_segment_s diagnostics")
    parser.add_argument("--mode", choices=["2x2", "max_seg_sweep"], default="2x2")
    args = parser.parse_args(argv)

    import sys

    sys.path.insert(0, str(MAIN_ROOT / "p0/perf_probe"))
    from analyze_stage6_stage3_accuracy import JP_SRT_PATH, parse_jp_cues

    out_dir = MAIN_ROOT / "p0/evidence/stage8-batch3-20261127/p1b_vad_2x2"
    out_dir.mkdir(parents=True, exist_ok=True)
    pcm_path = out_dir / "ep01_600s_16k.pcm"

    subprocess.check_call([
        "ffmpeg", "-v", "error", "-y", "-t", str(int(WINDOW_END_S)),
        "-i", str(MKV_PATH), "-ac", "1", "-ar", str(RATE), "-f", "s16le", str(pcm_path),
    ])

    try:
        pcm_sha = sha256_file(pcm_path)
        raw_bytes = pcm_path.read_bytes()
        pcm_f32 = np.frombuffer(raw_bytes, dtype="<i2").astype(np.float32) / 32768.0
        jp_dialogue, _, _ = parse_jp_cues(JP_SRT_PATH)
        cues_600 = [c for c in jp_dialogue if float(c["end_s"]) <= WINDOW_END_S]
        scenes = group_contiguous_scenes(cues_600, gap_s=2.0)
        if args.mode == "max_seg_sweep":
            return _run_max_seg_sweep(out_dir, raw_bytes, pcm_sha, cues_600, scenes)
        return _run_2x2(out_dir, raw_bytes, pcm_f32, pcm_sha, cues_600, scenes)
    finally:
        pcm_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
