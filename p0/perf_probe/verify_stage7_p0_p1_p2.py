"""Stage 7 P0+P1+P2 Verification Benchmark on EP01:
Part A: Full 45m EP01 Offline ASR (p180, m2.8, pad500, hybrid) vs Stage 6 baseline.
Part B: Full 654-cue EP01 Arm B Oracle-ASR MT (4-turn sliding KV + new SYSTEM_PROMPT).
Part C: 5m (0-300s) 1x Real-Time WebSocket Replay via real subtitle_service.cli (m1-pro-nemo).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import sherpa_onnx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/perf_probe"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_asr"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_mt"))

from analyze_stage6_stage3_accuracy import (  # noqa: E402
    JP_SRT_PATH,
    MAIN_END_S,
    ZH_SRT_PATH,
    chrf_score,
    norm_zh,
    parse_gold_zh_cues,
    parse_jp_cues,
)
from dataset import CACHE_ROOT, QWEN3_MT_DIR, percentile  # noqa: E402
from metrics_utils import compute_cer, edit_distance  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import AsrConfig, load_settings, load_token  # noqa: E402
from subtitle_service.local_pipeline import (  # noqa: E402
    RATE,
    Qwen3Translator,
    SpeechCppNeMoRecognizer,
    _SmartSegmenter,
    _has_meaningful_ja,
    silero_config,
)
from subtitle_service.replay import replay  # noqa: E402
from verify_m1_pro_hq_live import ResourceSampler, find_server_pid, launched_server  # noqa: E402
from verify_stage2 import FRAME_64MS_BYTES  # noqa: E402

MKV_PATH = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.mkv")
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
STAGE6_EVIDENCE = REPO_ROOT / "p0/evidence/stage6-ep01-20261126"
STAGE6_ATTR = REPO_ROOT / "docs/experiments/stage6-ep01-2026-11-26/stage3_accuracy_attribution.json"
OUT_DIR = REPO_ROOT / "p0/evidence/stage7-ep01-20261126"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage7-p0-p1-p2-2026-11-26"
VAD_PATH = CACHE_ROOT / "reazonspeech-assets/silero_vad.onnx"
DYLIB_PATH = CACHE_ROOT / "speech-cpp/libspeech.dylib"
GGUF_PATH = CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
_KANA_RE = re.compile(r"[\u3041-\u309f\u30a1-\u30ff\u31f0-\u31ff\uff66-\uff9f]")


def run_part_a_full_asr(
    raw_pcm: bytes,
    jp_dialogue: list[dict],
    asr_cfg: AsrConfig,
) -> tuple[dict, list[dict]]:
    t0 = time.perf_counter()
    rec = SpeechCppNeMoRecognizer(DYLIB_PATH, GGUF_PATH, VAD_PATH, asr_cfg)
    try:
        vad = rec.new_vad()
        seg = _SmartSegmenter(vad, max_segment_s=asr_cfg.max_segment_s, overlap_ms=asr_cfg.overlap_ms)
        chunks = []
        for off in range(0, len(raw_pcm), FRAME_64MS_BYTES):
            chunks.extend(seg.push(raw_pcm[off : off + FRAME_64MS_BYTES]))
        chunks.extend(seg.push(None))

        kept_segs: list[dict] = []
        lat_ms: list[float] = []
        decode_ms: list[float] = []
        predict_ms: list[float] = []
        punct_only_skipped = 0
        fallback_count = 0
        backends: Counter[str] = Counter()

        for idx, ch in enumerate(chunks, 1):
            ts = time.perf_counter()
            txt = rec.decode(
                ch.samples,
                start_sample=ch.start_sample,
                dedup_before_sample=ch.dedup_before_sample,
            )
            lat_ms.append((time.perf_counter() - ts) * 1000.0)
            meta = rec.last_decode_meta or {}
            backends[meta.get("backend", "unknown")] += 1
            if meta.get("fallback_reason"):
                fallback_count += 1
            tm = meta.get("timings") or {}
            if tm:
                decode_ms.append(float(tm.get("decode_ms", 0.0)))
                predict_ms.append(float(tm.get("predict_ms", 0.0)))
            if txt and not _has_meaningful_ja(txt):
                punct_only_skipped += 1
                continue
            if txt:
                kept_segs.append({
                    "seg_idx": idx,
                    "seg_start_s": round(ch.start_sample / RATE, 3),
                    "seg_end_s": round((ch.start_sample + len(ch.samples)) / RATE, 3),
                    "cut_reason": ch.cut_reason,
                    "text_ja": txt,
                })
    finally:
        rec.close()

    full_hyp = "".join(s["text_ja"] for s in kept_segs)
    full_ref = "".join(c["clean_text"] for c in jp_dialogue)
    overall_cer = compute_cer(full_hyp, full_ref)

    by_15m = {}
    for label, w0, w1 in [("0-15m", 0.0, 900.0), ("15-30m", 900.0, 1800.0), ("30-45m", 1800.0, MAIN_END_S + 1)]:
        hp = "".join(s["text_ja"] for s in kept_segs if w0 <= s["seg_start_s"] < w1)
        rp = "".join(c["clean_text"] for c in jp_dialogue if w0 <= c["start_s"] < w1)
        by_15m[label] = compute_cer(hp, rp)

    missed = []
    missed_short = 0
    for c in jp_dialogue:
        hit = any(
            min(c["end_s"] + 0.3, s["seg_end_s"]) - max(c["start_s"] - 0.3, s["seg_start_s"]) > 0.0
            for s in kept_segs
        )
        if not hit:
            missed.append(c)
            if len(re.sub(r"[\s、。？！!?…～・]+", "", c["clean_text"])) <= 4:
                missed_short += 1

    stage6_attr = json.loads(STAGE6_ATTR.read_text(encoding="utf-8"))
    s6_asr = stage6_attr["quantitative_full_episode"]["asr_accuracy"]

    report = {
        "wall_s": round(time.perf_counter() - t0, 1),
        "config": {
            "decoding": asr_cfg.decoding,
            "vad_pause_ms": asr_cfg.vad_pause_ms,
            "max_segment_s": asr_cfg.max_segment_s,
            "overlap_ms": asr_cfg.overlap_ms,
            "pad_ms": asr_cfg.pad_ms,
        },
        "total_vad_chunks": len(chunks),
        "kept_speech_segments": len(kept_segs),
        "punct_only_skipped": punct_only_skipped,
        "fallback_count": fallback_count,
        "backends": dict(backends),
        "asr_latency_ms": {
            "p50": round(percentile(lat_ms, 0.50), 2),
            "p95": round(percentile(lat_ms, 0.95), 2),
            "predict_p50": round(percentile(predict_ms, 0.50), 2),
            "decode_p50": round(percentile(decode_ms, 0.50), 2),
            "decode_p95": round(percentile(decode_ms, 0.95), 2),
        },
        "overall": overall_cer,
        "by_15m": by_15m,
        "missed_dialogue_cues": len(missed),
        "missed_dialogue_pct": round(len(missed) / max(1, len(jp_dialogue)) * 100.0, 2),
        "missed_substantive_pct": round((len(missed) - missed_short) / max(1, len(jp_dialogue)) * 100.0, 2),
        "vs_stage6": {
            "stage6_cer": s6_asr["overall"]["cer"],
            "cer_delta": round(overall_cer["cer"] - s6_asr["overall"]["cer"], 4),
            "stage6_hyp_len": s6_asr["overall"]["hyp_len"],
            "stage7_hyp_len": overall_cer["hyp_len"],
            "recovered_chars": overall_cer["hyp_len"] - s6_asr["overall"]["hyp_len"],
        },
    }
    return report, kept_segs


def verify_sliding_template_exact(tr: Qwen3Translator) -> bool:
    msgs_prefix = [
        {"role": "system", "content": tr.SYSTEM_PROMPT},
        {"role": "user", "content": "请翻译以下日语台词为简体中文：\n<<<SPLIT_HERE>>>"},
    ]
    full_tpl = tr.tokenizer.apply_chat_template(msgs_prefix, add_generation_prompt=True, tokenize=False)
    prefix_str, suffix_tail = full_tpl.split("<<<SPLIT_HERE>>>", 1)
    assert suffix_tail == tr._suffix_tail
    h = [("おはよう", "早安"), ("元気？", "还好吗？")]
    cur = "大丈夫。"
    reconstructed = prefix_str + "".join(
        f"{ja}{tr._suffix_tail}{zh}{tr._next_turn_prefix}" for ja, zh in h
    ) + f"{cur}{tr._suffix_tail}"
    canonical = tr.tokenizer.apply_chat_template(
        [
            {"role": "system", "content": tr.SYSTEM_PROMPT},
            {"role": "user", "content": "请翻译以下日语台词为简体中文：\nおはよう"},
            {"role": "assistant", "content": "早安"},
            {"role": "user", "content": "请翻译以下日语台词为简体中文：\n元気？"},
            {"role": "assistant", "content": "还好吗？"},
            {"role": "user", "content": "请翻译以下日语台词为简体中文：\n大丈夫。"},
        ],
        add_generation_prompt=True,
        tokenize=False,
    )
    assert reconstructed == canonical, "Sliding-window reconstructed prompt differs from canonical template"
    return True


def run_part_b_arm_b_mt(jp_dialogue: list[dict], gold_zh: list[dict], context_turns: int) -> dict:
    t0 = time.perf_counter()
    tr = Qwen3Translator(QWEN3_MT_DIR, context_turns=context_turns)
    tpl_ok = verify_sliding_template_exact(tr)
    sess = tr.new_session()
    rows = []
    lat_ms = []
    kana_leaks = 0
    for cue in jp_dialogue:
        ts = time.perf_counter()
        zh = tr.translate(
            sess,
            cue["clean_text"],
            start_sample=int(round(cue["start_s"] * RATE)),
            end_sample=int(round(cue["end_s"] * RATE)),
        )
        lat_ms.append((time.perf_counter() - ts) * 1000.0)
        if _KANA_RE.search(zh):
            kana_leaks += 1
        rows.append({
            "cue_idx": cue["cue_idx"],
            "start_s": cue["start_s"],
            "end_s": cue["end_s"],
            "jp": cue["clean_text"],
            "oracle_zh": zh,
        })
    sess.close()

    chrf_blocks = []
    chrf_15m = {"0-15m": [], "15-30m": [], "30-45m": []}
    for b0 in range(0, int(MAIN_END_S), 30):
        b1 = b0 + 30
        b_txt = "".join(r["oracle_zh"] for r in rows if b0 <= (r["start_s"] + r["end_s"]) * 0.5 < b1)
        g_txt = "".join(g["clean_text"] for g in gold_zh if b0 <= (g["start_s"] + g["end_s"]) * 0.5 < b1)
        if not norm_zh(g_txt):
            continue
        sc = chrf_score(b_txt, g_txt)
        chrf_blocks.append(sc)
        bucket = "0-15m" if b0 < 900 else ("15-30m" if b0 < 1800 else "30-45m")
        chrf_15m[bucket].append(sc)

    mean = lambda xs: round(sum(xs) / max(1, len(xs)), 4)
    full_chrf = chrf_score("".join(r["oracle_zh"] for r in rows), "".join(g["clean_text"] for g in gold_zh))

    stage6_attr = json.loads(STAGE6_ATTR.read_text(encoding="utf-8"))
    s6_mt = stage6_attr["quantitative_full_episode"]["mt_accuracy"]
    s6_arm_b_path = STAGE6_EVIDENCE / "arm_b_oracle_asr.json"
    s6_by_cue = (
        {r["cue_idx"]: r["oracle_zh"] for r in json.loads(s6_arm_b_path.read_text(encoding="utf-8"))["rows"]}
        if s6_arm_b_path.is_file()
        else {}
    )
    hard_labels = [x for x in stage6_attr["per_cue_labels"] if x.get("primary_tag") in ("E3", "E4")]
    new_by_cue = {r["cue_idx"]: r for r in rows}
    gold_by_cue = {}
    for c in rows:
        ov_g = [g["clean_text"] for g in gold_zh if min(c["end_s"] + 0.2, g["end_s"]) - max(c["start_s"] - 0.2, g["start_s"]) > 0.0]
        gold_by_cue[c["cue_idx"]] = " / ".join(ov_g)

    hard_comparisons = []
    for x in hard_labels:
        cid = x["cue_idx"]
        nr = new_by_cue.get(cid, {})
        hard_comparisons.append({
            "cue_idx": cid,
            "error_class": x.get("primary_tag"),
            "start_s": nr.get("start_s"),
            "jp": nr.get("jp", ""),
            "stage6_oracle_zh": s6_by_cue.get(cid, ""),
            "stage7_oracle_zh": nr.get("oracle_zh", ""),
            "gold_zh": gold_by_cue.get(cid, ""),
        })

    (OUT_DIR / "arm_b_oracle_asr_stage7.json").write_text(
        json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "cues": len(rows),
        "wall_s": round(time.perf_counter() - t0, 1),
        "sliding_template_exact": tpl_ok,
        "context_turns": context_turns,
        "mt_p50_ms": round(percentile(lat_ms, 0.50), 1),
        "mt_p95_ms": round(percentile(lat_ms, 0.95), 1),
        "kana_leaks": kana_leaks,
        "chrf_30s_blocks_mean": mean(chrf_blocks),
        "chrf_full_concat": full_chrf,
        "chrf_by_15m": {k: mean(v) for k, v in chrf_15m.items()},
        "vs_stage6": {
            "stage6_chrf_30s": s6_mt["chrf_30s_blocks_mean"]["arm_b_oracle_vs_gold"],
            "chrf_30s_delta": round(mean(chrf_blocks) - s6_mt["chrf_30s_blocks_mean"]["arm_b_oracle_vs_gold"], 4),
            "stage6_chrf_full": s6_mt["chrf_full_concat"]["arm_b_oracle_vs_gold"],
            "chrf_full_delta": round(full_chrf - s6_mt["chrf_full_concat"]["arm_b_oracle_vs_gold"], 4),
        },
        "e3_e4_hard_18_comparison": hard_comparisons,
    }


def analyze_live_300s(trace_path: Path, model_jsonl: Path, session_id: str, jp_300: list[dict], gold_300: list[dict]) -> dict:
    ws_events = [json.loads(ln) for ln in trace_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    m_rows = [
        json.loads(ln) for ln in model_jsonl.read_text(encoding="utf-8").splitlines()
        if ln.strip() and json.loads(ln).get("session_id") == session_id
    ]
    first_pcm = next(r for r in m_rows if r.get("stage") == "pcm_accepted")
    audio_origin_s = first_pcm["t"] - (first_pcm["end_sample"] / RATE)

    captions_by_seq = {}
    deltas_by_seq: dict[int, list[str]] = {}
    warnings: Counter[str] = Counter()
    for ev in ws_events:
        e = ev.get("event", {})
        tp = e.get("type")
        if tp == "caption":
            captions_by_seq[e["seq"]] = e
        elif tp == "caption_delta":
            deltas_by_seq.setdefault(e["seq"], []).append(e["text_zh"])
        elif tp == "warning":
            warnings[e.get("code", "unknown")] += 1

    by_seg: dict[int, dict] = {}
    gateway_filtered: Counter[str] = Counter()
    draft_events: list[dict] = []
    for r in m_rows:
        st = r.get("stage")
        if st == "gateway_filtered":
            gateway_filtered[r.get("code", "unknown")] += 1
        elif st == "draft_sent":
            draft_events.append(r)
        sid = r.get("segment_id")
        if sid is None:
            continue
        seg = by_seg.setdefault(sid, {"deltas": []})
        if st in ("vad_released", "asr_start", "asr_end", "mt_enqueued", "mt_start", "mt_end", "ws_sent"):
            seg[st] = r
        elif st == "ws_delta_sent":
            seg["deltas"].append(r)

    ws_delta_t_by_seq: dict[int, float] = {}
    for r in m_rows:
        if r.get("stage") == "ws_delta_sent" and "seq" in r and r["seq"] not in ws_delta_t_by_seq:
            ws_delta_t_by_seq[r["seq"]] = r["t"]

    vad_pause_ms, ane_asr_ms, gpu_mt_ms = [], [], []
    seg_start_to_first_s, seg_end_to_final_s = [], []
    edit_ratios = []
    guard_counts: Counter[str] = Counter()
    punct_skipped = 0
    fallback_count = 0
    kept_asr_texts = []
    kept_zh_rows = []
    kana_leaks = 0

    for sid, seg in sorted(by_seg.items()):
        vr = seg.get("vad_released")
        ast = seg.get("asr_start")
        aen = seg.get("asr_end")
        mst = seg.get("mt_start")
        men = seg.get("mt_end")
        ws = seg.get("ws_sent")
        if vr:
            vad_pause_ms.append((vr["t"] - (audio_origin_s + vr["end_sample"] / RATE)) * 1000.0)
        if ast and aen:
            ane_asr_ms.append((aen["t"] - ast["t"]) * 1000.0)
        if aen:
            if aen.get("fallback_reason"):
                fallback_count += 1
            if aen.get("draft_guard"):
                guard_counts[aen["draft_guard"]] += 1
            if aen.get("mt_skipped") == "punct_only":
                punct_skipped += 1
            elif aen.get("text_ja"):
                kept_asr_texts.append(aen["text_ja"])
        if mst and men:
            gpu_mt_ms.append((men["t"] - mst["t"]) * 1000.0)
        if ws:
            seq = ws["seq"]
            final_t = ws["t"]
            first_t = ws_delta_t_by_seq.get(seq, final_t)
            seg_start_to_first_s.append((first_t - (audio_origin_s + ws["start_sample"] / RATE)))
            seg_end_to_final_s.append((final_t - (audio_origin_s + ws["end_sample"] / RATE)))
            cap = captions_by_seq.get(seq)
            if cap:
                txt_zh = cap["text_zh"]
                if _KANA_RE.search(txt_zh):
                    kana_leaks += 1
                kept_zh_rows.append({
                    "seq": seq,
                    "start_s": ws["start_sample"] / RATE,
                    "end_s": ws["end_sample"] / RATE,
                    "text_zh": txt_zh,
                })
                ds = deltas_by_seq.get(seq, [])
                if ds:
                    edit_ratios.append(edit_distance(ds[0], txt_zh) / max(1, len(txt_zh)))

    hyp_ja = "".join(kept_asr_texts)
    ref_ja = "".join(c["clean_text"] for c in jp_300)
    cer_300 = compute_cer(hyp_ja, ref_ja)
    chrf_blocks = []
    for b0 in range(0, 300, 30):
        b1 = b0 + 30
        h_txt = "".join(r["text_zh"] for r in kept_zh_rows if b0 <= (r["start_s"] + r["end_s"]) * 0.5 < b1)
        g_txt = "".join(g["clean_text"] for g in gold_300 if b0 <= (g["start_s"] + g["end_s"]) * 0.5 < b1)
        if norm_zh(g_txt):
            chrf_blocks.append(chrf_score(h_txt, g_txt))

    heavy_pct = round(sum(1 for r in edit_ratios if r >= 0.7) / max(1, len(edit_ratios)) * 100.0, 2)

    # Compare against Stage 6 Run A first 300s if raw evidence is present
    s6_300_cmp = None
    s6_caps_path = STAGE6_EVIDENCE / "run_a_aligned_captions.json"
    if s6_caps_path.is_file():
        s6_caps = [
            c for c in json.loads(s6_caps_path.read_text(encoding="utf-8"))["captions"]
            if c["seg_start_s"] < 300.0
        ]
        s6_hyp_ja = "".join(c["text_ja"] for c in s6_caps)
        s6_cer = compute_cer(s6_hyp_ja, ref_ja)
        s6_blocks = []
        for b0 in range(0, 300, 30):
            b1 = b0 + 30
            st = "".join(c["text_zh"] for c in s6_caps if b0 <= (c["seg_start_s"] + c["seg_end_s"]) * 0.5 < b1)
            gt = "".join(g["clean_text"] for g in gold_300 if b0 <= (g["start_s"] + g["end_s"]) * 0.5 < b1)
            if norm_zh(gt):
                s6_blocks.append(chrf_score(st, gt))
        s6_300_cmp = {
            "stage6_captions_300s": len(s6_caps),
            "stage6_cer_300s": s6_cer["cer"],
            "stage6_chrf_30s_blocks_mean_300s": round(sum(s6_blocks) / max(1, len(s6_blocks)), 4),
        }

    return {
        "captions": len(kept_zh_rows),
        "draft_sent_count": len(draft_events),
        "ws_deltas_count": sum(len(v) for v in deltas_by_seq.values()),
        "warnings": dict(warnings),
        "gateway_filtered": dict(gateway_filtered),
        "punct_only_skipped": punct_skipped,
        "fallback_count": fallback_count,
        "draft_guard_counts": dict(guard_counts),
        "kana_leaks": kana_leaks,
        "hops_ms": {
            "vad_end_to_release_p50": round(percentile(vad_pause_ms, 0.50), 2),
            "ane_asr_p50": round(percentile(ane_asr_ms, 0.50), 2),
            "ane_asr_p95": round(percentile(ane_asr_ms, 0.95), 2),
            "gpu_mt_p50": round(percentile(gpu_mt_ms, 0.50), 2),
            "gpu_mt_p95": round(percentile(gpu_mt_ms, 0.95), 2),
        },
        "e2e_s": {
            "seg_start_to_first_ws_delta_p50": round(percentile(seg_start_to_first_s, 0.50), 3),
            "seg_start_to_first_ws_delta_p95": round(percentile(seg_start_to_first_s, 0.95), 3),
            "seg_end_to_final_ws_p50": round(percentile(seg_end_to_final_s, 0.50), 3),
            "seg_end_to_final_ws_p95": round(percentile(seg_end_to_final_s, 0.95), 3),
        },
        "draft_stability": {
            "paired_captions": len(edit_ratios),
            "first_delta_to_final_edit_ratio_p50": round(percentile(edit_ratios, 0.50), 4),
            "heavy_rewrite_ge_0_7_pct": heavy_pct,
        },
        "accuracy_0_300s": {
            "asr_cer": cer_300,
            "chrf_30s_blocks_mean": round(sum(chrf_blocks) / max(1, len(chrf_blocks)), 4),
        },
        "stage6_0_300s_reference": s6_300_cmp,
    }


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    settings = load_settings(CFG_PATH)
    prof = settings.profiles["m1-pro-nemo"]
    jp_dialogue, jp_sfx, jp_music = parse_jp_cues(JP_SRT_PATH)
    gold_zh, _ = parse_gold_zh_cues(ZH_SRT_PATH)

    print("Extracting EP01 0-2690s mono 16kHz PCM once in memory...")
    raw_full = subprocess.check_output([
        "ffmpeg", "-v", "error", "-t", str(int(MAIN_END_S)), "-i", str(MKV_PATH),
        "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1",
    ])
    raw_300 = raw_full[: 300 * RATE * 2]

    print("Part A: Running full 45m EP01 offline ANE hybrid ASR benchmark...")
    part_a, _ = run_part_a_full_asr(raw_full, jp_dialogue, prof.asr)
    print("Part A done:", json.dumps(part_a, ensure_ascii=False))

    print("Part B: Running full 654-cue EP01 Arm B Oracle-ASR MT benchmark...")
    part_b = run_part_b_arm_b_mt(jp_dialogue, gold_zh, prof.mt.context_turns)
    print("Part B done: chrf_30s=", part_b["chrf_30s_blocks_mean"], "full=", part_b["chrf_full_concat"], "kana_leaks=", part_b["kana_leaks"])

    print("Part C: Running 5m (0-300s) 1x live WebSocket replay against subtitle_service.cli...")
    live_dir = OUT_DIR / "live_300s"
    live_dir.mkdir(parents=True, exist_ok=True)
    cfg_live = live_dir / "profiles.toml"
    cfg_live.write_text(
        CFG_PATH.read_text(encoding="utf-8").replace('cache_root = "../../.model-cache"', f'cache_root = "{CACHE_ROOT}"'),
        encoding="utf-8",
    )
    live_settings = load_settings(cfg_live)
    token = load_token(token_path(), create=False)
    trace_path = live_dir / "ep01_0_300s.jsonl"
    with launched_server(cfg_live, live_settings, live_dir):
        pid = find_server_pid(live_settings.server.port)
        assert pid is not None
        with ResourceSampler(pid) as sampler:
            with trace_path.open("w", buffering=1, encoding="utf-8") as trace:
                replay_res = asyncio.run(
                    replay(live_settings, "m1-pro-nemo", token, "ep01-0-300s", raw_300, trace, frame_bytes=FRAME_64MS_BYTES)
                )
        peak_rss_mib = round(max(sampler.rss_mib), 1) if sampler.rss_mib else 0.0
        peak_cpu_pct = round(max(sampler.cpu_pct), 1) if sampler.cpu_pct else 0.0

    sid = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])["event"]["session_id"]
    jp_300 = [c for c in jp_dialogue if c["start_s"] < 300.0]
    gold_300 = [g for g in gold_zh if g["start_s"] < 300.0]
    part_c = analyze_live_300s(trace_path, live_dir / "model.jsonl", sid, jp_300, gold_300)
    part_c["replay"] = replay_res
    part_c["resources"] = {"srv_peak_rss_mib": peak_rss_mib, "srv_peak_cpu_pct": peak_cpu_pct}

    out = {
        "part_a_full_45m_asr": part_a,
        "part_b_full_654_arm_b_mt": part_b,
        "part_c_live_5m_ws_replay": part_c,
    }
    (OUT_DIR / "stage7_verification.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ARCHIVE_DIR / "stage7_verification.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("STAGE7_VERIFICATION_DONE")


if __name__ == "__main__":
    main()
