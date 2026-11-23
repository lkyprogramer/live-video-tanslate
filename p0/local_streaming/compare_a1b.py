"""Compare A1b offline causal simulation against B0-new on all 34 semantic units."""
import json
import sys
from pathlib import Path

from p0.p2.freeze import dump, rows


def overlap(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0))


def main():
    a0 = Path(sys.argv[1])
    sim11 = Path(sys.argv[2])
    sim29 = Path(sys.argv[3])
    out = Path(sys.argv[4])

    units = rows(a0 / "inputs/units.jsonl")
    unit_map = json.loads((a0 / "inputs/unit-map.json").read_text())["required_segments"]
    verdict = json.loads((a0 / "inputs/verdict.json").read_text())["outcomes"]
    b0_reazon = {(r["clip"], r["segment_id"]): r for r in rows(a0 / "baseline/reazon.jsonl")}
    b0_timing_rows = json.loads((a0 / "classification/timing-by-unit.json").read_text())["records"]
    b0_seg_timing = {}
    for rec in b0_timing_rows:
        for seg in rec["segments"]:
            b0_seg_timing[(rec["seed"], rec["unit_id"], seg["segment_id"])] = seg

    def compare_seed(seed, sim_dir):
        b0_mt = {(r["clip"], r["segment_id"]): r["text_zh"] for r in rows(a0 / f"baseline/mt-seed{seed}.jsonl")}
        events = rows(sim_dir / "events.jsonl")
        by_clip_sources = {}
        by_clip_captions = {}
        by_clip_sc = {}
        for ev in events:
            clip = ev["session_id"]
            if ev["event"] == "source_commit":
                by_clip_sources.setdefault(clip, []).append(ev)
            elif ev["event"] == "caption_emit":
                by_clip_captions.setdefault(clip, []).append(ev)
            elif ev["event"] == "stable_candidate" and ev.get("text_ja"):
                by_clip_sc.setdefault(clip, []).append(ev)

        usable = set(verdict[f"m0-{seed}"]["usable"])
        unchanged = []
        changed = []

        for u in units:
            clip, grp = u["clip"], u["group"]
            uid = f"{clip}/{grp}"
            ustart, uend = u["start_sample"], u["end_sample"]
            req_sids = unit_map[uid]

            b0_ja = " ".join(b0_reazon[(clip, s)]["text_ja"] for s in req_sids)
            b0_zh = " ".join(b0_mt.get((clip, s), "") for s in req_sids).strip()
            b0_mt_end_ms = max(
                (b0_seg_timing[(seed, uid, s)]["mt_end"] for s in req_sids if (seed, uid, s) in b0_seg_timing),
                default=None,
            )

            a1b_srcs = [s for s in by_clip_sources.get(clip, []) if overlap(ustart, uend, s["start_sample"], s["end_sample"]) > 0]
            a1b_caps = [c for c in by_clip_captions.get(clip, []) if overlap(ustart, uend, c["start_sample"], c["end_sample"]) > 0]
            a1b_ja = " ".join(s["text_ja"] for s in a1b_srcs)
            a1b_zh = " ".join(c["text_zh"] for c in a1b_caps).strip()
            a1b_mt_end_ms = max((c["schedule_ns"] / 1e6 for c in a1b_caps), default=None)
            first_sc_ms = min(
                (e["schedule_ns"] / 1e6 for e in by_clip_sc.get(clip, []) if overlap(ustart, uend, e["start_sample"], e["end_sample"]) > 0),
                default=None,
            )

            entry = dict(
                unit_id=uid,
                clip=clip,
                group=grp,
                unit_start_sample=ustart,
                unit_end_sample=uend,
                source_ja=u["source_ja"],
                reference_zh=u["reference_zh"],
                b0_usable=uid in usable,
                b0_ja=b0_ja,
                b0_zh=b0_zh,
                b0_mt_end_ms=b0_mt_end_ms,
                a1b_sources=[
                    dict(segment_id=s["segment_id"], start_sample=s["start_sample"], end_sample=s["end_sample"],
                         boundary_reason=s["boundary_reason"], text_ja=s["text_ja"])
                    for s in a1b_srcs
                ],
                a1b_captions=[
                    dict(seq=c["seq"], start_sample=c["start_sample"], end_sample=c["end_sample"],
                         text_zh=c["text_zh"], schedule_ms=c["schedule_ns"] / 1e6)
                    for c in a1b_caps
                ],
                a1b_ja=a1b_ja,
                a1b_zh=a1b_zh,
                a1b_mt_end_ms=a1b_mt_end_ms,
                earliest_stable_candidate_ms=first_sc_ms,
                mt_end_delta_ms=(a1b_mt_end_ms - b0_mt_end_ms) if (a1b_mt_end_ms is not None and b0_mt_end_ms is not None) else None,
            )
            if a1b_ja == b0_ja and a1b_zh == b0_zh:
                unchanged.append(entry)
            else:
                changed.append(entry)

        return dict(
            seed=seed,
            total_units=len(units),
            unchanged_count=len(unchanged),
            changed_count=len(changed),
            unchanged_unit_ids=[e["unit_id"] for e in unchanged],
            changed_units=changed,
        )

    r11 = compare_seed(11, sim11)
    r29 = compare_seed(29, sim29)
    dump(out, dict(rounds={"11": r11, "29": r29}))
    print(json.dumps({
        "seed11": {"unchanged": r11["unchanged_count"], "changed": r11["changed_count"]},
        "seed29": {"unchanged": r29["unchanged_count"], "changed": r29["changed_count"]},
    }))


if __name__ == "__main__":
    main()
