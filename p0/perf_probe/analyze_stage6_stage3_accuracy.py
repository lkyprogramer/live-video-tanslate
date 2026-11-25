"""Stage 6 Stage 3: Full-episode EP01 ASR/MT accuracy, Oracle-ASR Arm B comparison, and 120-cue audit export."""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/perf_probe"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_asr"))

from dataset import CACHE_ROOT, QWEN3_MT_DIR  # noqa: E402
from metrics_utils import compute_cer  # noqa: E402
from subtitle_service.local_pipeline import RATE, Qwen3Translator  # noqa: E402

EVIDENCE_DIR = REPO_ROOT / "p0/evidence/stage6-ep01-20261126"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage6-ep01-2026-11-26"
JP_SRT_PATH = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.srt")
ZH_SRT_PATH = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.chi.srt")
MAIN_END_S = 2690.0  # 00:44:50

_SRT_TS_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_SFX_PAREN_RE = re.compile(r"^\([^)]+\)$|^⟨[^⟩]+⟩$")
_SPEAKER_RE = re.compile(r"\([^)]{1,20}\)\s*|>>\s*|→|[⟨⟩]")
_ZH_PAREN_FULL_RE = re.compile(r"^\([^)]+\)$")
_ZH_PAREN_INLINE_RE = re.compile(r"\([^)]+\)")
_ZH_PUNCT_RE = re.compile(r"[\s\u3000，。！？、：；“”\"'‘’（）()\-—…·《》「」『』.,!?;:]+")
_KANA_RE = re.compile(r"[\u3041-\u309f\u30a1-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
_PROPER_NOUN_RE = re.compile(r"麻美|久美子|遥|夏希|美穂|玲奈|洋子|保育園|市役所|三田|寛|徳")


def parse_jp_cues(path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    raw = path.read_bytes().decode("utf-8-sig")
    dialogue, sfx_only, music = [], [], []
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
        row = {
            "cue_idx": idx,
            "start_s": round(st, 3),
            "end_s": round(en, 3),
            "raw_text": raw_text,
            "clean_text": clean_text,
            "has_narration": "⟨" in raw_text,
            "has_overlap": ">>" in raw_text,
        }
        if is_music:
            music.append(row)
        elif _SFX_PAREN_RE.match(raw_text) or not clean_text:
            sfx_only.append(row)
        else:
            dialogue.append(row)
    return dialogue, sfx_only, music


def parse_gold_zh_cues(path: Path) -> tuple[list[dict], int]:
    raw = path.read_bytes().decode("utf-8-sig")
    out = []
    stripped_screen_notes = 0
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
        if raw_text == "©NIPPON TV":
            continue
        if _ZH_PAREN_FULL_RE.match(raw_text):
            stripped_screen_notes += 1
            continue
        cleaned = _ZH_PAREN_INLINE_RE.sub("", raw_text).strip()
        if cleaned != raw_text:
            stripped_screen_notes += 1
        cleaned = re.sub(r"^-\s*|\s+-\s*", " ", cleaned).strip()
        if not cleaned:
            continue
        out.append({
            "cue_idx": idx,
            "start_s": round(st, 3),
            "end_s": round(en, 3),
            "raw_text": raw_text,
            "clean_text": cleaned,
        })
    return out, stripped_screen_notes


def norm_zh(text: str) -> str:
    return _ZH_PUNCT_RE.sub("", text).strip()


def chrf_score(hyp: str, ref: str, max_n: int = 4, beta: float = 2.0) -> float:
    h = norm_zh(hyp)
    r = norm_zh(ref)
    if not h or not r:
        return 0.0
    total_f = 0.0
    used_n = 0
    for n in range(1, max_n + 1):
        if len(h) < n or len(r) < n:
            continue
        hc = Counter(h[i : i + n] for i in range(len(h) - n + 1))
        rc = Counter(r[i : i + n] for i in range(len(r) - n + 1))
        overlap = sum((hc & rc).values())
        if overlap == 0:
            total_f += 0.0
        else:
            prec = overlap / max(1, sum(hc.values()))
            rec = overlap / max(1, sum(rc.values()))
            b2 = beta * beta
            total_f += (1 + b2) * prec * rec / (b2 * prec + rec)
        used_n += 1
    return round(total_f / max(1, used_n), 4)


def run_arm_b_oracle(jp_dialogue: list[dict]) -> tuple[list[dict], dict]:
    cache_path = EVIDENCE_DIR / "arm_b_oracle_asr.json"
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if len(cached.get("rows", [])) == len(jp_dialogue):
            return cached["rows"], cached["stats"]

    t0 = time.perf_counter()
    tr = Qwen3Translator(QWEN3_MT_DIR, context_turns=2)
    sess = tr.new_session()
    rows = []
    lat_ms = []
    kana_leaks = 0
    for cue in jp_dialogue:
        st_s = time.perf_counter()
        zh = tr.translate(
            sess,
            cue["clean_text"],
            start_sample=int(round(cue["start_s"] * RATE)),
            end_sample=int(round(cue["end_s"] * RATE)),
        )
        lat_ms.append((time.perf_counter() - st_s) * 1000.0)
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
    lat_sorted = sorted(lat_ms)
    stats = {
        "cues": len(rows),
        "wall_s": round(time.perf_counter() - t0, 1),
        "mt_p50_ms": round(lat_sorted[len(lat_sorted) // 2], 1),
        "mt_p95_ms": round(lat_sorted[int(0.95 * (len(lat_sorted) - 1))], 1),
        "kana_leaks": kana_leaks,
    }
    cache_path.write_text(json.dumps({"stats": stats, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows, stats


def main() -> None:
    jp_dialogue, jp_sfx, jp_music = parse_jp_cues(JP_SRT_PATH)
    gold_zh, stripped_notes = parse_gold_zh_cues(ZH_SRT_PATH)
    aligned_data = json.loads((EVIDENCE_DIR / "run_a_aligned_captions.json").read_text(encoding="utf-8"))
    run_a_caps = aligned_data["captions"]
    run_a_sid = aligned_data["run_a_session_id"]

    # Collect per-segment drafts from model.jsonl for inspection
    m_rows = [json.loads(ln) for ln in (EVIDENCE_DIR / "model.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    drafts_by_seg: dict[int, list[dict]] = {}
    asr_all_run_a: list[dict] = []
    for r in m_rows:
        if r.get("session_id") != run_a_sid:
            continue
        if r.get("stage") == "draft_sent":
            # draft_sent doesn't carry segment_id; group chronologically by start/end sample overlap later
            drafts_by_seg.setdefault(0, []).append({
                "start_s": round(r["start_sample"] / RATE, 2),
                "end_s": round(r["end_sample"] / RATE, 2),
                "text_ja": r.get("text_ja", ""),
                "text_zh": r.get("text_zh", ""),
            })
        elif r.get("stage") == "asr_end":
            asr_all_run_a.append(r)

    print(f"Running Arm B (Oracle-ASR) over {len(jp_dialogue)} dialogue JP cues...")
    arm_b_rows, arm_b_stats = run_arm_b_oracle(jp_dialogue)
    arm_b_by_cue = {r["cue_idx"]: r["oracle_zh"] for r in arm_b_rows}

    # 1. Full-episode ASR CER + missed cue rate
    # Note: compare dialogue cues against all Run A captions (and all asr_end outputs)
    full_hyp_ja = "".join(c["text_ja"] for c in run_a_caps)
    full_ref_ja = "".join(c["clean_text"] for c in jp_dialogue)
    asr_overall = compute_cer(full_hyp_ja, full_ref_ja)

    asr_by_15m = {}
    for name, w0, w1 in [("0-15m", 0.0, 900.0), ("15-30m", 900.0, 1800.0), ("30-45m", 1800.0, MAIN_END_S + 1)]:
        h_part = "".join(c["text_ja"] for c in run_a_caps if w0 <= c["seg_start_s"] < w1)
        r_part = "".join(c["clean_text"] for c in jp_dialogue if w0 <= c["start_s"] < w1)
        asr_by_15m[name] = compute_cer(h_part, r_part)

    # Missed cue detection: spoken JP cues with no overlapping Run A caption
    missed_cues = []
    missed_short_interjection = 0
    for c in jp_dialogue:
        has_cap = any(min(c["end_s"] + 0.3, r["seg_end_s"]) - max(c["start_s"] - 0.3, r["seg_start_s"]) > 0.0 for r in run_a_caps)
        if not has_cap:
            missed_cues.append(c)
            if len(re.sub(r"[\s、。？！!?…～・]+", "", c["clean_text"])) <= 4:
                missed_short_interjection += 1

    # 2. 30-second block chrF (Arm A vs Gold, Arm B vs Gold)
    chrf_a_blocks, chrf_b_blocks = [], []
    chrf_15m = {k: {"a": [], "b": []} for k in ("0-15m", "15-30m", "30-45m")}
    for t0 in range(0, int(MAIN_END_S), 30):
        t1 = t0 + 30
        a_txt = "".join(c["text_zh"] for c in run_a_caps if t0 <= (c["seg_start_s"] + c["seg_end_s"]) * 0.5 < t1)
        b_txt = "".join(r["oracle_zh"] for r in arm_b_rows if t0 <= (r["start_s"] + r["end_s"]) * 0.5 < t1)
        g_txt = "".join(g["clean_text"] for g in gold_zh if t0 <= (g["start_s"] + g["end_s"]) * 0.5 < t1)
        if not norm_zh(g_txt):
            continue
        sa = chrf_score(a_txt, g_txt)
        sb = chrf_score(b_txt, g_txt)
        chrf_a_blocks.append(sa)
        chrf_b_blocks.append(sb)
        bucket = "0-15m" if t0 < 900 else ("15-30m" if t0 < 1800 else "30-45m")
        chrf_15m[bucket]["a"].append(sa)
        chrf_15m[bucket]["b"].append(sb)

    mean = lambda xs: round(sum(xs) / max(1, len(xs)), 4)
    full_chrf_a = chrf_score("".join(c["text_zh"] for c in run_a_caps), "".join(g["clean_text"] for g in gold_zh))
    full_chrf_b = chrf_score("".join(r["oracle_zh"] for r in arm_b_rows), "".join(g["clean_text"] for g in gold_zh))

    # 3. Export stratified 120-cue audit set across 5 categories (~24 per category)
    all_drafts = drafts_by_seg.get(0, [])

    def attach_context(cue: dict, category: str) -> dict:
        ov_a = [
            c for c in run_a_caps
            if min(cue["end_s"] + 0.2, c["seg_end_s"]) - max(cue["start_s"] - 0.2, c["seg_start_s"]) > 0.0
        ]
        ov_g = [
            g for g in gold_zh
            if min(cue["end_s"] + 0.2, g["end_s"]) - max(cue["start_s"] - 0.2, g["start_s"]) > 0.0
        ]
        ov_drafts = [
            d for d in all_drafts
            if min(cue["end_s"] + 0.2, d["end_s"]) - max(cue["start_s"] - 0.2, d["start_s"]) > 0.0
        ]
        return {
            "category": category,
            "cue_idx": cue["cue_idx"],
            "start_s": cue["start_s"],
            "end_s": cue["end_s"],
            "raw_jp": cue["raw_text"],
            "clean_jp": cue["clean_text"],
            "arm_b_oracle_zh": arm_b_by_cue.get(cue["cue_idx"], ""),
            "gold_zh": " / ".join(g["clean_text"] for g in ov_g),
            "arm_a_items": [
                {
                    "seq": c["seq"],
                    "segment_id": c["segment_id"],
                    "seg_start_s": c["seg_start_s"],
                    "seg_end_s": c["seg_end_s"],
                    "cut_reason": c["cut_reason"],
                    "asr_ja": c["text_ja"],
                    "live_zh": c["text_zh"],
                }
                for c in ov_a
            ],
            "drafts": ov_drafts[:6],
        }

    used_cids: set[int] = set()
    audit_rows: list[dict] = []

    def take_cues(cat: str, candidates: list[dict], limit: int = 24):
        taken = 0
        for c in candidates:
            if c["cue_idx"] in used_cids:
                continue
            used_cids.add(c["cue_idx"])
            audit_rows.append(attach_context(c, cat))
            taken += 1
            if taken >= limit:
                break

    take_cues("family_banter", [c for c in jp_dialogue if (13 <= c["start_s"] <= 60) or (2190 <= c["start_s"] <= 2300)], 24)
    take_cues("workplace_banter", [c for c in jp_dialogue if (274 <= c["start_s"] <= 345) or (395 <= c["start_s"] <= 460)], 24)
    take_cues("multi_speaker_karaoke", [c for c in jp_dialogue if (700 <= c["start_s"] <= 830) or (1150 <= c["start_s"] <= 1250)], 24)
    take_cues("monologue_bgm", [c for c in jp_dialogue if c["has_narration"] or (1315 <= c["start_s"] <= 1410) or (2645 <= c["start_s"] <= 2690)], 24)
    take_cues("proper_nouns", [c for c in jp_dialogue if _PROPER_NOUN_RE.search(c["raw_text"])], 24)

    (EVIDENCE_DIR / "stratified_120_audit.json").write_text(
        json.dumps(audit_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "cues": {
            "jp_total_0_44_50": len(jp_dialogue) + len(jp_sfx) + len(jp_music),
            "jp_dialogue": len(jp_dialogue),
            "jp_sfx_only": len(jp_sfx),
            "jp_pure_music": len(jp_music),
            "gold_zh_dialogue_cues": len(gold_zh),
            "gold_zh_stripped_screen_notes": stripped_notes,
            "run_a_ws_captions": len(run_a_caps),
        },
        "asr_accuracy": {
            "overall": asr_overall,
            "by_15m": asr_by_15m,
            "missed_dialogue_cues": len(missed_cues),
            "missed_dialogue_pct": round(len(missed_cues) / max(1, len(jp_dialogue)) * 100.0, 2),
            "missed_short_interjection_count": missed_short_interjection,
            "missed_substantive_pct": round((len(missed_cues) - missed_short_interjection) / max(1, len(jp_dialogue)) * 100.0, 2),
        },
        "mt_accuracy": {
            "arm_b_stats": arm_b_stats,
            "chrf_30s_blocks_mean": {
                "arm_a_vs_gold": mean(chrf_a_blocks),
                "arm_b_oracle_vs_gold": mean(chrf_b_blocks),
                "asr_vad_gap_points": round(mean(chrf_b_blocks) - mean(chrf_a_blocks), 4),
            },
            "chrf_full_concat": {
                "arm_a_vs_gold": full_chrf_a,
                "arm_b_oracle_vs_gold": full_chrf_b,
            },
            "chrf_by_15m": {
                k: {
                    "arm_a_vs_gold": mean(v["a"]),
                    "arm_b_oracle_vs_gold": mean(v["b"]),
                }
                for k, v in chrf_15m.items()
            },
        },
        "stratified_audit_exported": {
            "count": len(audit_rows),
            "by_category": dict(Counter(r["category"] for r in audit_rows)),
        },
    }
    (EVIDENCE_DIR / "stage3_quantitative_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
