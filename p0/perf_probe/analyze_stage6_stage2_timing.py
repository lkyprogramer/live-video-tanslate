"""Stage 6 Stage 2: Full-episode EP01 timestamp calibration, JP SRT alignment, and drift analysis."""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_asr"))

from metrics_utils import edit_distance, percentile  # noqa: E402

EVIDENCE_DIR = REPO_ROOT / "p0/evidence/stage6-ep01-20261126"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage6-ep01-2026-11-26"
JP_SRT_PATH = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.srt")
RATE = 16000.0
MAIN_END_S = 2690.0  # 00:44:50 main episode boundary

_SRT_TS_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_SFX_PAREN_RE = re.compile(r"^\([^)]+\)$|^⟨[^⟩]+⟩$")
_SPEAKER_RE = re.compile(r"\([^)]{1,20}\)\s*|>>\s*|→|[⟨⟩]")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def parse_jp_srt(path: Path) -> tuple[list[dict], list[dict]]:
    """Return (non_music_cues, pure_music_cues) in 0..MAIN_END_S."""
    raw = path.read_bytes().decode("utf-8-sig")
    non_music: list[dict] = []
    music: list[dict] = []
    for idx, block in enumerate(re.split(r"\r?\n\r?\n+", raw.strip()), 1):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        m = _SRT_TS_RE.search(lines[1])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        st = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        en = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        if st > MAIN_END_S:
            continue
        raw_text = " ".join(lines[2:]).strip()
        is_music = raw_text in ("♪～", "♪") or raw_text.startswith("♪～")
        clean_text = _SPEAKER_RE.sub("", raw_text).strip()
        is_sfx_only = bool(_SFX_PAREN_RE.match(raw_text)) and not is_music
        cue = {
            "cue_idx": idx,
            "start_s": round(st, 3),
            "end_s": round(en, 3),
            "raw_text": raw_text,
            "clean_text": clean_text,
            "is_sfx_only": is_sfx_only,
        }
        if is_music:
            music.append(cue)
        else:
            non_music.append(cue)
    return non_music, music


def build_wall_to_media(anchors: list[dict]):
    pts = sorted(
        [(float(a["t_ms"]), float(a["media_time"])) for a in anchors if a.get("phase") == "run_a" and not a.get("paused")],
        key=lambda x: x[0],
    )
    assert len(pts) >= 2, "Need >=2 Run A clock anchors"

    def convert(t_ms: float) -> float:
        if t_ms <= pts[0][0]:
            dt = (t_ms - pts[0][0]) / 1000.0
            return pts[0][1] + dt
        if t_ms >= pts[-1][0]:
            dt = (t_ms - pts[-1][0]) / 1000.0
            return pts[-1][1] + dt
        lo, hi = 0, len(pts) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if pts[mid][0] <= t_ms:
                lo = mid
            else:
                hi = mid
        t0, m0 = pts[lo]
        t1, m1 = pts[hi]
        frac = (t_ms - t0) / max(1e-6, t1 - t0)
        return m0 + frac * (m1 - m0)

    offsets = [t - m * 1000.0 for t, m in pts]
    drift_ms = max(offsets) - min(offsets)
    return convert, round(drift_ms, 2)


def main() -> None:
    model_rows = load_jsonl(EVIDENCE_DIR / "model.jsonl")
    cdp_rows = load_jsonl(EVIDENCE_DIR / "cdp_diagnostics.jsonl")
    dom_rows = load_jsonl(EVIDENCE_DIR / "dom_events.jsonl")
    anchors = load_jsonl(EVIDENCE_DIR / "clock_anchors.jsonl")
    proc_rows = load_jsonl(EVIDENCE_DIR / "proc_samples.jsonl")
    run_summary = json.loads((EVIDENCE_DIR / "stage1_chrome_capture_summary.json").read_text(encoding="utf-8"))

    non_music_jp, music_jp = parse_jp_srt(JP_SRT_PATH)
    wall_to_media, clock_drift_ms = build_wall_to_media(anchors)

    # Identify Run A session ID (the first session in model.jsonl)
    run_a_sid = next(r["session_id"] for r in model_rows if r.get("session_id"))
    m_run_a = [r for r in model_rows if r.get("session_id") == run_a_sid]
    cdp_run_a = [r for r in cdp_rows if r.get("session_id") == run_a_sid]

    first_pcm = next(r for r in cdp_run_a if r.get("stage") == "first_pcm_sent")
    audio_origin_t_ms = float(first_pcm.get("estimated_first_input_t") or first_pcm["t"])
    audio_origin_media_s = wall_to_media(audio_origin_t_ms)

    def sample_to_media_s(sample_idx: int) -> float:
        return audio_origin_media_s + (sample_idx / RATE)

    # Calibrate server monotonic clock -> Chrome wall_ms using paired ws_sent / ws_received
    srv_ws_sent = {r["seq"]: r for r in m_run_a if r.get("stage") == "ws_sent" and "seq" in r}
    cdp_ws_cap = {
        r["seq"]: r
        for r in cdp_run_a
        if r.get("stage") == "ws_received" and r.get("type") == "caption" and "seq" in r
    }
    mon_to_wall_offsets = [
        cdp_ws_cap[sq]["t"] - srv_ws_sent[sq]["t"] * 1000.0
        for sq in sorted(set(srv_ws_sent) & set(cdp_ws_cap))
    ]
    mon_offset_ms = statistics.median(mon_to_wall_offsets)

    def srv_t_to_wall_ms(t_mon_s: float) -> float:
        return t_mon_s * 1000.0 + mon_offset_ms

    # Collect DOM visible / removed and Shadow DOM paint events for Run A
    cdp_dom_vis = {r["seq"]: r for r in cdp_run_a if r.get("stage") == "dom_visible"}
    cdp_dom_rem = {r["seq"]: r for r in cdp_run_a if r.get("stage") == "dom_removed"}
    dom_run_a = [r for r in dom_rows if r.get("phase") == "run_a" and not r.get("is_status") and r.get("text_zh")]

    # Group server stage events by segment_id
    by_seg: dict[int, dict] = {}
    gateway_filtered: list[dict] = []
    for r in m_run_a:
        st = r.get("stage")
        sid_seg = r.get("segment_id")
        if st == "gateway_filtered":
            gateway_filtered.append(r)
        if sid_seg is None:
            continue
        rec = by_seg.setdefault(sid_seg, {"deltas": []})
        if st in ("vad_released", "asr_start", "asr_end", "mt_enqueued", "mt_start", "mt_end", "ws_sent"):
            rec[st] = r
        elif st == "ws_delta_sent":
            rec["deltas"].append(r)

    # Index cdp ws_received caption_delta by seq (in content.ts: delta seq == current seq + 1)
    cdp_ws_deltas_by_seq: dict[int, list[dict]] = {}
    for r in cdp_run_a:
        if r.get("stage") == "ws_received" and r.get("type") == "caption_delta":
            cdp_ws_deltas_by_seq.setdefault(r["seq"], []).append(r)

    # Map dom_run_a events to nearest preceding cdp ws_received by (text_zh, is_streaming, t_mut >= ws_received.t - 5)
    dom_ptr = 0
    dom_by_ws_key: dict[tuple[str, int, int], dict] = {}
    ws_stream_events = sorted(
        [
            r for r in cdp_run_a
            if r.get("stage") == "ws_received" and r.get("type") in ("caption", "caption_delta")
        ],
        key=lambda x: x["t"],
    )
    for w in ws_stream_events:
        want_stream = w["type"] == "caption_delta"
        w_t = w["t"]
        w_txt = w["text_zh"].strip()
        for idx in range(max(0, dom_ptr - 2), min(len(dom_run_a), dom_ptr + 8)):
            ev = dom_run_a[idx]
            if ev["is_streaming"] == want_stream and ev["text_zh"].strip() == w_txt and ev["t_mut_ms"] >= w_t - 20.0:
                key = (w["type"], w["seq"], int(round(w_t * 10)))
                dom_by_ws_key[key] = ev
                dom_ptr = idx + 1
                break

    # Build aligned cue/caption rows for Run A
    aligned_rows: list[dict] = []
    hop_vad_pause_ms: list[float] = []
    hop_asr_q_ms: list[float] = []
    hop_ane_asr_ms: list[float] = []
    hop_mt_q_ms: list[float] = []
    hop_gpu_mt_ms: list[float] = []
    hop_ws_ipc_ms: list[float] = []
    hop_dom_paint_ms: list[float] = []
    ane_sub_timings: list[dict] = []
    asr_backends: dict[str, int] = {}
    fallback_count = 0

    for seg_id, seg in sorted(by_seg.items()):
        ae = seg.get("asr_end")
        if ae:
            b = ae.get("asr_backend", "unknown")
            asr_backends[b] = asr_backends.get(b, 0) + 1
            if ae.get("fallback_reason"):
                fallback_count += 1
            if ae.get("ane_timings"):
                ane_sub_timings.append(ae["ane_timings"])

        ws = seg.get("ws_sent")
        if not ws:
            continue
        seq = ws["seq"]
        seg_start_s = sample_to_media_s(ws["start_sample"])
        seg_end_s = sample_to_media_s(ws["end_sample"])
        if seg_start_s > MAIN_END_S:
            continue

        # Server hops
        vad_rel = seg.get("vad_released")
        asr_st = seg.get("asr_start")
        mt_enq = seg.get("mt_enqueued")
        mt_st = seg.get("mt_start")
        mt_en = seg.get("mt_end")
        cdp_cap = cdp_ws_cap.get(seq)
        dom_vis = cdp_dom_vis.get(seq)
        dom_rem = cdp_dom_rem.get(seq)
        paint_ev = dom_by_ws_key.get(("caption", seq, int(round(cdp_cap["t"] * 10)))) if cdp_cap else None

        if vad_rel:
            vad_pause = (srv_t_to_wall_ms(vad_rel["t"]) - (audio_origin_t_ms + (vad_rel["end_sample"] / RATE) * 1000.0))
            hop_vad_pause_ms.append(vad_pause)
        if vad_rel and asr_st:
            hop_asr_q_ms.append((asr_st["t"] - vad_rel["t"]) * 1000.0)
        if asr_st and ae:
            hop_ane_asr_ms.append((ae["t"] - asr_st["t"]) * 1000.0)
        if mt_enq and mt_st:
            hop_mt_q_ms.append((mt_st["t"] - mt_enq["t"]) * 1000.0)
        if mt_st and mt_en:
            hop_gpu_mt_ms.append((mt_en["t"] - mt_st["t"]) * 1000.0)
        if cdp_cap:
            ws_lat = cdp_cap["t"] - srv_t_to_wall_ms(ws["t"])
            hop_ws_ipc_ms.append(max(0.0, ws_lat))
        if cdp_cap and paint_ev:
            hop_dom_paint_ms.append(max(0.0, paint_ev["t_paint_ms"] - cdp_cap["t"]))
        elif cdp_cap and dom_vis:
            hop_dom_paint_ms.append(max(0.0, dom_vis["t"] - cdp_cap["t"]))

        # Final visible time on media clock
        if paint_ev:
            final_vis_media_s = float(paint_ev["media_time_paint"])
        elif dom_vis:
            final_vis_media_s = float(dom_vis["media_time"])
        elif cdp_cap:
            final_vis_media_s = wall_to_media(cdp_cap["t"])
        else:
            final_vis_media_s = wall_to_media(srv_t_to_wall_ms(ws["t"]))

        # First visible time (earliest draft paint or final visible)
        seq_ws_deltas = cdp_ws_deltas_by_seq.get(seq, [])
        draft_texts: list[str] = []
        first_delta_media_s: float | None = None
        for d in seq_ws_deltas:
            draft_texts.append(d["text_zh"].strip())
            dp = dom_by_ws_key.get(("caption_delta", seq, int(round(d["t"] * 10))))
            cand_s = float(dp["media_time_paint"]) if dp else wall_to_media(d["t"])
            if first_delta_media_s is None or cand_s < first_delta_media_s:
                first_delta_media_s = cand_s

        # Fallback: content.ts dom_visible fires when span first becomes visible (often on first delta!)
        if first_delta_media_s is None and seq_ws_deltas and dom_vis:
            first_delta_media_s = float(dom_vis["media_time"])

        first_vis_media_s = first_delta_media_s if first_delta_media_s is not None else final_vis_media_s

        # Dwell duration
        dwell_s: float | None = None
        if dom_vis and dom_rem:
            dwell_s = round((dom_rem["t"] - dom_vis["t"]) / 1000.0, 3)

        # Match against non-music JP SRT cues (strict positive overlap; fallback to <=0.35s proximity only if disjoint)
        ov_cues = [
            c for c in non_music_jp
            if min(seg_end_s, c["end_s"]) - max(seg_start_s, c["start_s"]) > 0.0
        ]
        if not ov_cues:
            ov_cues = [
                c for c in non_music_jp
                if min(seg_end_s + 0.35, c["end_s"]) - max(seg_start_s - 0.35, c["start_s"]) > 0.0
            ]
        # Prefer actual speech cues over pure parenthetical SFX
        dialogue_cues = [c for c in ov_cues if not c["is_sfx_only"]] or ov_cues
        first_cue_start = dialogue_cues[0]["start_s"] if dialogue_cues else None
        last_cue_end = dialogue_cues[-1]["end_s"] if dialogue_cues else None
        next_cue = None
        if dialogue_cues:
            last_idx = dialogue_cues[-1]["cue_idx"]
            for c in non_music_jp:
                if not c["is_sfx_only"] and c["cue_idx"] > last_idx:
                    next_cue = c
                    break

        final_text = cdp_cap["text_zh"].strip() if cdp_cap else ""
        first_draft_edit_ratio = None
        if draft_texts and final_text:
            first_draft_edit_ratio = round(
                edit_distance(draft_texts[0], final_text) / max(1, len(final_text)), 4
            )

        aligned_rows.append({
            "seq": seq,
            "segment_id": ws["segment_id"],
            "seg_start_s": round(seg_start_s, 3),
            "seg_end_s": round(seg_end_s, 3),
            "cut_reason": vad_rel.get("cut_reason") if vad_rel else None,
            "first_vis_media_s": round(first_vis_media_s, 3),
            "final_vis_media_s": round(final_vis_media_s, 3),
            "seg_start_to_first_vis_s": round(first_vis_media_s - seg_start_s, 3),
            "seg_end_to_final_vis_s": round(final_vis_media_s - seg_end_s, 3),
            "onset_to_first_vis_s": round(first_vis_media_s - first_cue_start, 3) if first_cue_start is not None else None,
            "cue_end_to_final_vis_s": round(final_vis_media_s - last_cue_end, 3) if last_cue_end is not None else None,
            "final_after_cue_end": (final_vis_media_s > last_cue_end) if last_cue_end is not None else None,
            "final_after_next_cue_start": (final_vis_media_s > next_cue["start_s"]) if next_cue is not None else None,
            "delta_count": len(seq_ws_deltas),
            "first_draft_to_final_edit_ratio": first_draft_edit_ratio,
            "dwell_s": dwell_s,
            "text_ja": ae.get("text_ja", "") if ae else "",
            "text_zh": final_text,
            "matched_cue_ids": [c["cue_idx"] for c in dialogue_cues],
        })

    # Compute summary percentiles
    seg_start_to_first = [r["seg_start_to_first_vis_s"] for r in aligned_rows]
    seg_end_to_final = [r["seg_end_to_final_vis_s"] for r in aligned_rows]
    onset_to_first = [r["onset_to_first_vis_s"] for r in aligned_rows if r["onset_to_first_vis_s"] is not None]
    cue_end_to_final = [r["cue_end_to_final_vis_s"] for r in aligned_rows if r["cue_end_to_final_vis_s"] is not None]
    matched_rows = [r for r in aligned_rows if r["matched_cue_ids"]]
    late_cue_end = sum(1 for r in matched_rows if r["final_after_cue_end"])
    late_next_cue = sum(1 for r in matched_rows if r["final_after_next_cue_start"])

    dwells = [r["dwell_s"] for r in aligned_rows if r["dwell_s"] is not None]
    short_flash_count = sum(1 for d in dwells if d < 1.2)
    draft_counts = [r["delta_count"] for r in aligned_rows]
    draft_edit_ratios = [r["first_draft_to_final_edit_ratio"] for r in aligned_rows if r["first_draft_to_final_edit_ratio"] is not None]
    heavy_rewrite_count = sum(1 for x in draft_edit_ratios if x >= 0.7)

    # Cut reasons distribution
    cut_reasons: dict[str, int] = {}
    for r in m_run_a:
        if r.get("stage") == "vad_released":
            cr = r.get("cut_reason", "unknown")
            cut_reasons[cr] = cut_reasons.get(cr, 0) + 1

    # Segment-level 15-min drift (0-15m, 15-30m, 30-45m)
    drift = {}
    run_a_procs = [p for p in proc_rows if p.get("phase") == "run_a"]
    run_a_anchors_active = [a for a in anchors if a.get("phase") == "run_a" and not a.get("paused")]
    prev_dropped = 0
    prev_total = 0
    for name, w0, w1 in [("0-15m", 0.0, 900.0), ("15-30m", 900.0, 1800.0), ("30-45m", 1800.0, 2750.0)]:
        sub_rows = [r for r in aligned_rows if w0 <= r["seg_start_s"] < w1]
        sub_onset = [r["onset_to_first_vis_s"] for r in sub_rows if r["onset_to_first_vis_s"] is not None]
        sub_cue_end = [r["cue_end_to_final_vis_s"] for r in sub_rows if r["cue_end_to_final_vis_s"] is not None]
        sub_seg_end = [r["seg_end_to_final_vis_s"] for r in sub_rows]

        # Per-segment server timings inside window
        sub_asr = [
            (by_seg[r["segment_id"]]["asr_end"]["t"] - by_seg[r["segment_id"]]["asr_start"]["t"]) * 1000.0
            for r in sub_rows if "asr_end" in by_seg[r["segment_id"]] and "asr_start" in by_seg[r["segment_id"]]
        ]
        sub_mt = [
            (by_seg[r["segment_id"]]["mt_end"]["t"] - by_seg[r["segment_id"]]["mt_start"]["t"]) * 1000.0
            for r in sub_rows if "mt_end" in by_seg[r["segment_id"]] and "mt_start" in by_seg[r["segment_id"]]
        ]
        sub_filt = [g for g in gateway_filtered if w0 <= sample_to_media_s(g["start_sample"]) < w1]
        sub_procs = [p for p in run_a_procs if w0 <= wall_to_media(p["t_ms"]) < w1]
        sub_anc = [a for a in run_a_anchors_active if w0 <= a["media_time"] < w1]
        win_dropped = (sub_anc[-1]["dropped_frames"] - prev_dropped) if sub_anc else 0
        win_total = (sub_anc[-1]["total_frames"] - prev_total) if sub_anc else 0
        if sub_anc:
            prev_dropped = sub_anc[-1]["dropped_frames"]
            prev_total = sub_anc[-1]["total_frames"]

        drift[name] = {
            "captions": len(sub_rows),
            "gateway_filtered": len(sub_filt),
            "seg_end_to_final_p50_s": percentile(sub_seg_end, 0.50),
            "seg_end_to_final_p95_s": percentile(sub_seg_end, 0.95),
            "onset_to_first_p50_s": percentile(sub_onset, 0.50),
            "onset_to_first_p95_s": percentile(sub_onset, 0.95),
            "cue_end_to_final_p50_s": percentile(sub_cue_end, 0.50),
            "cue_end_to_final_p95_s": percentile(sub_cue_end, 0.95),
            "asr_p50_ms": round(percentile(sub_asr, 0.50), 2),
            "mt_p50_ms": round(percentile(sub_mt, 0.50), 2),
            "srv_mean_cpu_pct": round(statistics.mean([p["srv_cpu_pct"] for p in sub_procs]), 1) if sub_procs else 0.0,
            "srv_peak_rss_mib": max([p["srv_rss_mib"] for p in sub_procs], default=0.0),
            "chrome_mean_cpu_pct": round(statistics.mean([p["chrome_cpu_pct"] for p in sub_procs]), 1) if sub_procs else 0.0,
            "chrome_peak_rss_mib": max([p["chrome_rss_mib"] for p in sub_procs], default=0.0),
            "dropped_frames": win_dropped,
            "dropped_frame_pct": round(win_dropped / max(1, win_total) * 100.0, 2),
        }

    # BGM false triggers: Run A captions strictly inside pure ♪～ spans (with 0.5s guard inside music)
    bgm_triggers: list[dict] = []
    for mc in music_jp:
        if mc["end_s"] - mc["start_s"] < 1.5:
            continue
        hits = [
            r for r in aligned_rows
            if r["seg_start_s"] >= mc["start_s"] + 0.5 and r["seg_end_s"] <= mc["end_s"] - 0.5
        ]
        if hits:
            bgm_triggers.append({
                "music_cue": mc,
                "triggered_captions": [{"seq": h["seq"], "seg_start_s": h["seg_start_s"], "seg_end_s": h["seg_end_s"], "text_ja": h["text_ja"], "text_zh": h["text_zh"]} for h in hits],
            })

    # Save full aligned captions into gitignored evidence for Stage 3 reuse
    (EVIDENCE_DIR / "run_a_aligned_captions.json").write_text(
        json.dumps({"run_a_session_id": run_a_sid, "audio_origin_media_s": audio_origin_media_s, "captions": aligned_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out = {
        "run_a_session_id": run_a_sid,
        "clock_calibration": {
            "audio_origin_media_s": round(audio_origin_media_s, 4),
            "clock_anchors_count": len(run_a_anchors_active),
            "clock_drift_across_45m_ms": clock_drift_ms,
        },
        "counts": {
            "vad_released": len(by_seg),
            "ws_sent_captions": len(aligned_rows),
            "gateway_filtered": len(gateway_filtered),
            "cut_reasons": cut_reasons,
            "asr_backends": asr_backends,
            "fallback_count": fallback_count,
            "matched_to_jp_srt_captions": len(matched_rows),
            "unmatched_captions": len(aligned_rows) - len(matched_rows),
        },
        "latencies": {
            "seg_start_to_first_visible_s": {
                "n": len(seg_start_to_first),
                "p50": percentile(seg_start_to_first, 0.50),
                "p90": percentile(seg_start_to_first, 0.90),
                "p95": percentile(seg_start_to_first, 0.95),
                "p99": percentile(seg_start_to_first, 0.99),
            },
            "onset_to_first_visible_s": {
                "n": len(onset_to_first),
                "p50": percentile(onset_to_first, 0.50),
                "p90": percentile(onset_to_first, 0.90),
                "p95": percentile(onset_to_first, 0.95),
                "p99": percentile(onset_to_first, 0.99),
            },
            "cue_end_to_final_visible_s": {
                "n": len(cue_end_to_final),
                "p50": percentile(cue_end_to_final, 0.50),
                "p90": percentile(cue_end_to_final, 0.90),
                "p95": percentile(cue_end_to_final, 0.95),
                "p99": percentile(cue_end_to_final, 0.99),
            },
            "seg_end_to_final_visible_s": {
                "n": len(seg_end_to_final),
                "p50": percentile(seg_end_to_final, 0.50),
                "p90": percentile(seg_end_to_final, 0.90),
                "p95": percentile(seg_end_to_final, 0.95),
                "p99": percentile(seg_end_to_final, 0.99),
            },
        },
        "hops_ms": {
            "vad_end_to_release_delay_ms": {"p50": round(percentile(hop_vad_pause_ms, 0.50), 2), "p95": round(percentile(hop_vad_pause_ms, 0.95), 2)},
            "asr_queue_ms": {"p50": round(percentile(hop_asr_q_ms, 0.50), 2), "p95": round(percentile(hop_asr_q_ms, 0.95), 2)},
            "ane_asr_total_ms": {"p50": round(percentile(hop_ane_asr_ms, 0.50), 2), "p95": round(percentile(hop_ane_asr_ms, 0.95), 2)},
            "ane_native_sub_ms": {
                k: {"p50": round(percentile([t[k] for t in ane_sub_timings], 0.50), 2), "p95": round(percentile([t[k] for t in ane_sub_timings], 0.95), 2)}
                for k in ("frontend_ms", "predict_ms", "output_copy_ms", "decode_ms")
            },
            "mt_queue_ms": {"p50": round(percentile(hop_mt_q_ms, 0.50), 2), "p95": round(percentile(hop_mt_q_ms, 0.95), 2)},
            "gpu_mt_ms": {"p50": round(percentile(hop_gpu_mt_ms, 0.50), 2), "p95": round(percentile(hop_gpu_mt_ms, 0.95), 2)},
            "ws_ipc_ms": {"p50": round(percentile(hop_ws_ipc_ms, 0.50), 2), "p95": round(percentile(hop_ws_ipc_ms, 0.95), 2)},
            "dom_raf_paint_ms": {"p50": round(percentile(hop_dom_paint_ms, 0.50), 2), "p95": round(percentile(hop_dom_paint_ms, 0.95), 2)},
        },
        "sync_and_reading": {
            "final_after_cue_end_pct": round(late_cue_end / max(1, len(matched_rows)) * 100.0, 2),
            "final_after_next_cue_start_pct": round(late_next_cue / max(1, len(matched_rows)) * 100.0, 2),
            "with_draft_before_final_pct": round(sum(1 for x in draft_counts if x > 0) / max(1, len(draft_counts)) * 100.0, 2),
            "deltas_per_caption_mean": round(statistics.mean(draft_counts), 2) if draft_counts else 0.0,
            "first_draft_to_final_edit_ratio_p50": percentile(draft_edit_ratios, 0.50),
            "heavy_draft_rewrite_ge_0_7_pct": round(heavy_rewrite_count / max(1, len(draft_edit_ratios)) * 100.0, 2),
            "dwell_s_p50": percentile(dwells, 0.50),
            "dwell_s_p10": percentile(dwells, 0.10),
            "dwell_under_1_2s_pct": round(short_flash_count / max(1, len(dwells)) * 100.0, 2),
        },
        "drift_15m": drift,
        "bgm_false_triggers": {
            "music_spans_checked": len([m for m in music_jp if m["end_s"] - m["start_s"] >= 1.5]),
            "triggered_span_count": len(bgm_triggers),
            "details": bgm_triggers,
        },
        "run_b_soak": run_summary["run_b"],
    }

    out_path = ARCHIVE_DIR / "stage2_timing_alignment.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "counts": out["counts"],
        "latencies": out["latencies"],
        "hops_ms": out["hops_ms"],
        "sync_and_reading": out["sync_and_reading"],
        "drift_15m": out["drift_15m"],
        "bgm_triggered": out["bgm_false_triggers"]["triggered_span_count"],
        "clock_drift_ms": clock_drift_ms,
    }, ensure_ascii=False, indent=2))
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
