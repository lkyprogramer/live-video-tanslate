"""Evaluate A2 offline draft+final probe against B0-new and revision burden targets."""
import json
import math
import sys
from pathlib import Path

from p0.p2.freeze import dump, rows


def non_punct_count(text):
    punct = set("。？！?!，、,.：:；;「」『』（）()…— ")
    return sum(1 for ch in text if ch not in punct)


def required_readable_ms(text):
    return max(600.0, non_punct_count(text) / 12.0 * 1000.0)


def char_edit_ratio(a, b):
    if not a and not b:
        return 0.0
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur
    return dp[m] / max(len(a), len(b), 1)


def p95(values):
    if not values:
        return 0
    s = sorted(values)
    return s[math.ceil(0.95 * len(s)) - 1]


def main():
    a0 = Path(sys.argv[1])
    p11 = Path(sys.argv[2])
    p29 = Path(sys.argv[3])
    out = Path(sys.argv[4])

    units = rows(a0 / "inputs/units.jsonl")
    unit_map = json.loads((a0 / "inputs/unit-map.json").read_text())["required_segments"]
    b0_timing_rows = json.loads((a0 / "classification/timing-by-unit.json").read_text())["records"]
    b0_seg_timing = {}
    for rec in b0_timing_rows:
        for seg in rec["segments"]:
            b0_seg_timing[(rec["seed"], rec["unit_id"], seg["segment_id"])] = seg

    def eval_seed(seed, pdir):
        summary = json.loads((pdir / "summary.json").read_text())
        events = rows(pdir / "events.jsonl")
        emits_by_clip = {}
        clip_end_ms = {}
        for ev in events:
            clip = ev["session_id"]
            if ev["event"] == "caption_emit":
                emits_by_clip.setdefault(clip, []).append(ev)
            elif ev["event"] == "finished":
                clip_end_ms[clip] = ev["schedule_ns"] / 1e6

        # Compute line-level revision burden across all clips
        revisions_per_line = []
        edit_ratios = []
        lines_with_draft = 0
        for clip, emits in emits_by_clip.items():
            # Visible duration of each event until next caption_emit on same clip or clip_end_ms
            for i, ev in enumerate(emits):
                next_t = emits[i + 1]["schedule_ns"] / 1e6 if i + 1 < len(emits) else clip_end_ms.get(clip, ev["schedule_ns"] / 1e6)
                ev["_vis_ms"] = max(0.0, next_t - ev["schedule_ns"] / 1e6)

            by_line = {}
            for ev in emits:
                by_line.setdefault(ev["line_id"], []).append(ev)
            for lid, levents in by_line.items():
                has_draft = any(e["state"] == "draft" for e in levents)
                if has_draft:
                    lines_with_draft += 1
                # Count visible text changes (draft->final with identical text is NOT a text revision)
                texts = [e.get("text_zh", "") for e in levents if e["state"] != "withdrawn"]
                vis_changes = 0
                for a, b in zip(texts, texts[1:]):
                    if a != b:
                        vis_changes += 1
                        edit_ratios.append(char_edit_ratio(a, b))
                revisions_per_line.append(vis_changes)

        unit_timelines = []
        for u in units:
            clip, grp = u["clip"], u["group"]
            uid = f"{clip}/{grp}"
            req_sids = unit_map[uid]
            b0_mt_end_ms = max(
                (b0_seg_timing[(seed, uid, s)]["mt_end"] for s in req_sids if (seed, uid, s) in b0_seg_timing),
                default=None,
            )
            emits = [
                e for e in emits_by_clip.get(clip, [])
                if e.get("segment_id") in req_sids or (
                    e["state"] == "draft"
                    and max(0, min(e["end_sample"], u["end_sample"]) - max(e["start_sample"], u["start_sample"])) > 0
                )
            ]
            # Filter drafts to lines whose final segment_id is in req_sids (if available)
            matched_lines = {e["line_id"] for e in emits if e["state"] == "final" and e.get("segment_id") in req_sids}
            timeline = []
            for e in emits_by_clip.get(clip, []):
                if e["line_id"] in matched_lines:
                    txt = e.get("text_zh", "")
                    req_ms = required_readable_ms(txt) if txt else 0.0
                    timeline.append(dict(
                        line_id=e["line_id"],
                        revision=e["revision"],
                        state=e["state"],
                        schedule_ms=round(e["schedule_ns"] / 1e6, 1),
                        visible_duration_ms=round(e["_vis_ms"], 1),
                        required_readable_ms=round(req_ms, 1),
                        readable=(e["_vis_ms"] >= req_ms) if txt else False,
                        text_ja=e.get("text_ja", ""),
                        text_zh=txt,
                    ))
            has_d = any(t["state"] == "draft" for t in timeline)
            unit_timelines.append(dict(
                unit_id=uid,
                reference_zh=u["reference_zh"],
                source_ja=u["source_ja"],
                b0_mt_end_ms=b0_mt_end_ms,
                has_draft=has_d,
                timeline=timeline,
            ))

        return dict(
            seed=seed,
            exact_final_parity=summary["exact_final_parity"],
            v2_errors=summary["v2_errors"],
            mt_calls=summary["mt_calls"],
            mt_promoted=summary["mt_promoted"],
            draft_discarded_stale=summary["draft_discarded_stale"],
            draft_invalid=summary["draft_invalid"],
            withdrawn=summary["withdrawn"],
            lines_total=len(revisions_per_line),
            lines_with_draft=lines_with_draft,
            visible_text_revisions_mean=round(sum(revisions_per_line) / max(len(revisions_per_line), 1), 3),
            visible_text_revisions_p95=p95(revisions_per_line),
            visible_text_revisions_max=max(revisions_per_line, default=0),
            char_edit_ratio_mean=round(sum(edit_ratios) / max(len(edit_ratios), 1), 3),
            units_with_draft=[u for u in unit_timelines if u["has_draft"]],
            all_units=unit_timelines,
        )

    r11 = eval_seed(11, p11)
    r29 = eval_seed(29, p29)
    dump(out, dict(rounds={"11": r11, "29": r29}))
    for s, r in (("11", r11), ("29", r29)):
        print(json.dumps({
            "seed": s,
            "exact_final_parity": r["exact_final_parity"],
            "v2_errors": r["v2_errors"],
            "mt_calls": r["mt_calls"],
            "lines_with_draft": r["lines_with_draft"],
            "revisions_p95": r["visible_text_revisions_p95"],
            "units_with_draft": len(r["units_with_draft"]),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
