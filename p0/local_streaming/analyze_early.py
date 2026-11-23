"""Analyze early stable_candidate Japanese appearance in A1a vs B0."""
import json
import sys
from pathlib import Path

from p0.p2.freeze import dump, rows


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

    def analyze_seed(seed, sim_dir):
        ev_by_clip = {}
        for ev in rows(sim_dir / "events.jsonl"):
            ev_by_clip.setdefault(ev["session_id"], []).append(ev)

        usable = set(verdict[f"m0-{seed}"]["usable"])
        unit_results = []
        exact_early_500 = []
        prefix70_early_500 = []

        for u in units:
            clip, grp = u["clip"], u["group"]
            uid = f"{clip}/{grp}"
            seg_ids = unit_map[uid]
            evs = ev_by_clip.get(clip, [])
            sc_events = [e for e in evs if e["event"] == "stable_candidate" and e.get("text_ja")]
            barrier_asr = {
                (e["start_sample"], e["end_sample"]): e["schedule_ns"] / 1e6
                for e in evs
                if e["event"] == "asr_completed" and e.get("kind") != "rolling"
            }

            seg_details = []
            for sid in seg_ids:
                b0_seg = b0_reazon[(clip, sid)]
                b0_t = b0_seg_timing.get((seed, uid, sid), {})
                b0_asr_ms = b0_t.get("asr_end")
                b0_vad_ms = b0_t.get("vad_released")
                a1a_asr_ms = barrier_asr.get((b0_seg["start_sample"], b0_seg["end_sample"]))

                target_ja = b0_seg["text_ja"]
                matching_sc = [
                    e for e in sc_events
                    if e["start_sample"] == b0_seg["start_sample"]
                    and e["end_sample"] <= b0_seg["end_sample"] + 6400
                ]
                first_exact_ms = None
                first_prefix70_ms = None
                first_prefix70_text = None
                for e in matching_sc:
                    t_ms = e["schedule_ns"] / 1e6
                    txt = e["text_ja"]
                    if first_exact_ms is None and txt == target_ja:
                        first_exact_ms = t_ms
                    if first_prefix70_ms is None and target_ja:
                        if len(txt) >= max(4, int(0.7 * len(target_ja))) and (
                            target_ja.startswith(txt) or txt in target_ja
                        ):
                            first_prefix70_ms = t_ms
                            first_prefix70_text = txt

                exact_adv_b0 = (b0_asr_ms - first_exact_ms) if (b0_asr_ms is not None and first_exact_ms is not None) else None
                prefix70_adv_b0 = (b0_asr_ms - first_prefix70_ms) if (b0_asr_ms is not None and first_prefix70_ms is not None) else None
                seg_details.append(dict(
                    segment_id=sid,
                    target_ja=target_ja,
                    b0_vad_ms=b0_vad_ms,
                    b0_asr_ms=b0_asr_ms,
                    a1a_asr_ms=a1a_asr_ms,
                    first_exact_ms=first_exact_ms,
                    exact_advance_vs_b0_ms=exact_adv_b0,
                    first_prefix70_ms=first_prefix70_ms,
                    first_prefix70_text=first_prefix70_text,
                    prefix70_advance_vs_b0_ms=prefix70_adv_b0,
                ))

            # Unit-level completion: all required segments must reach exact/prefix70; unit time = max over segments
            if seg_details and all(s["first_exact_ms"] is not None for s in seg_details) and all(s["b0_asr_ms"] is not None for s in seg_details):
                u_exact_ms = max(s["first_exact_ms"] for s in seg_details)
                u_b0_ms = max(s["b0_asr_ms"] for s in seg_details)
                u_exact_adv = u_b0_ms - u_exact_ms
            else:
                u_exact_ms = u_exact_adv = None

            if seg_details and all(s["first_prefix70_ms"] is not None for s in seg_details) and all(s["b0_asr_ms"] is not None for s in seg_details):
                u_p70_ms = max(s["first_prefix70_ms"] for s in seg_details)
                u_b0_ms = max(s["b0_asr_ms"] for s in seg_details)
                u_p70_adv = u_b0_ms - u_p70_ms
            else:
                u_p70_ms = u_p70_adv = None

            if u_exact_adv is not None and u_exact_adv >= 500.0:
                exact_early_500.append(uid)
            if u_p70_adv is not None and u_p70_adv >= 500.0:
                prefix70_early_500.append(uid)

            unit_results.append(dict(
                unit_id=uid,
                b0_correct=uid in usable,
                unit_exact_advance_ms=u_exact_adv,
                unit_prefix70_advance_ms=u_p70_adv,
                segments=seg_details,
            ))

        return dict(
            seed=seed,
            exact_early_500_count=len(exact_early_500),
            exact_early_500_units=exact_early_500,
            prefix70_early_500_count=len(prefix70_early_500),
            prefix70_early_500_units=prefix70_early_500,
            units=unit_results,
        )

    r11 = analyze_seed(11, sim11)
    r29 = analyze_seed(29, sim29)
    both_exact = sorted(set(r11["exact_early_500_units"]) & set(r29["exact_early_500_units"]))
    both_p70 = sorted(set(r11["prefix70_early_500_units"]) & set(r29["prefix70_early_500_units"]))

    dump(out, dict(
        exit_condition_3_threshold=3,
        both_seeds_exact_early_500_count=len(both_exact),
        both_seeds_exact_early_500_units=both_exact,
        both_seeds_prefix70_early_500_count=len(both_p70),
        both_seeds_prefix70_early_500_units=both_p70,
        rounds={"11": r11, "29": r29},
    ))
    print(json.dumps({
        "both_exact_500": len(both_exact),
        "both_exact_units": both_exact,
        "both_prefix70_500": len(both_p70),
        "both_prefix70_units": both_p70,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
