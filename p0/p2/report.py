"""Build B0's evidence report without inventing semantic or human timing labels."""
from collections import defaultdict
import json
from pathlib import Path

from .events import validate_causality
from .freeze import ROOT, digest, dump, frozen_fingerprint, rows
from .scoring import evidence_helper, readable_cues


def replay_analysis(run, repeat):
    folder = run / f"replay-{repeat}"
    data = json.loads((folder / "run.json").read_text())
    seed = data["seed"]
    expected = {(r["clip"], r["segment_id"]): r for r in rows(run / f"inputs/mt-seed{seed}.jsonl")}
    asr_expected = {(r["clip"], r["segment_id"]): r for r in rows(run / "inputs/reazon.jsonl")}
    by_session = defaultdict(list)
    for row in rows(folder / "model.jsonl"):
        by_session[row["session_id"]].append(row)
    units = rows(run / "inputs/units.jsonl")
    usable = set(json.loads((run / "inputs/verdict.json").read_text())["outcomes"][f"m0-{seed}"]["usable"])
    mapping = json.loads((run / "inputs/unit-map.json").read_text())
    parity_asr, parity_mt, stage_ms, unit_rows, firsts = [], [], defaultdict(list), [], {}
    packet_matches = []
    for case in data["cases"]:
        clip, sid = case["clip"], case["session_id"]
        events = by_session[sid]
        segments = defaultdict(dict)
        for e in events:
            if "segment_id" in e:
                segments[e["segment_id"]][e["stage"]] = e
        jobs = defaultdict(dict)
        for e in events:
            if "job_id" in e:
                jobs[e["job_id"]][e["stage"]] = e
        for job in jobs.values():
            if all(k in job for k in ("asr_enqueue", "asr_job_start", "asr_job_end")):
                stage_ms["asr_executor_wait"].append((job["asr_job_start"]["t"]-job["asr_enqueue"]["t"])*1000)
                stage_ms["vad_asr_job_total"].append((job["asr_job_end"]["t"]-job["asr_job_start"]["t"])*1000)
        # Exact bytes observed at the ASGI boundary, independently of client-side claims.
        sent = [r for r in rows(folder / "events.jsonl") if r["session_id"] == sid and r["event"] == "pcm_accepted"]
        received = [r for r in events if r["stage"] == "wire_pcm_received"]
        accepted = [r for r in events if r["stage"] == "pcm_accepted"]
        consumed = [r for r in events if r["stage"] == "pcm_consumed"]
        exact_packets = len(sent) == len(received) == len(accepted) == len(consumed) and all(
            a["pcm_sha256"] == b["pcm_sha256"] and a["accepted_samples"] == b["end_sample"]
            for a, b in zip(sent, received))
        if accepted and consumed:
            exact_packets &= accepted[-1]["end_sample"] == consumed[-1]["end_sample"] == case["accepted_samples"]
        packet_matches.append(dict(clip=clip, packets=len(sent), exact_pcm_and_counters=exact_packets))
        for segment_id, segment in segments.items():
            key = (clip, segment_id)
            if "asr_end" not in segment:
                continue
            old = asr_expected.get(key)
            actual = segment["asr_end"]
            parity_asr.append(old is not None and all(actual[k] == old[k] for k in ("start_sample", "end_sample", "text_ja")))
            if key in expected:
                parity_mt.append("mt_end" in segment and segment["mt_end"]["text_zh"] == expected[key]["text_zh"])
            if all(k in segment for k in ("asr_start", "asr_end")):
                stage_ms["asr_compute"].append((segment["asr_end"]["t"]-segment["asr_start"]["t"])*1000)
                stage_ms["vad_after_audio_end"].append((segment["asr_start"]["t"]-case["anchor_ns"]/1e9-actual["end_sample"]/16000)*1000)
            if all(k in segment for k in ("mt_enqueued", "mt_start", "mt_end")):
                stage_ms["mt_queue"].append((segment["mt_start"]["t"]-segment["mt_enqueued"]["t"])*1000)
                stage_ms["mt_compute"].append((segment["mt_end"]["t"]-segment["mt_start"]["t"])*1000)
        wire = [r for r in rows(folder / f"{clip}.jsonl") if r["event"]["type"] == "caption"]
        emitted = {s["ws_sent"]["seq"]: s for s in segments.values() if "ws_sent" in s}
        if {r["event"]["seq"] for r in wire} != set(emitted):
            raise ValueError("model/WS join incomplete")
        first_correct = None
        for unit in (u for u in units if u["clip"] == clip):
            uid = f"{clip}/{unit['group']}"
            required = mapping["required_segments"][uid]
            selected = [segments.get(i, {}) for i in required]
            can_match = uid in usable and bool(required) and all("ws_sent" in s and "mt_end" in s for s in selected)
            exact = all((clip, i) in expected and "mt_end" in segment and
                        segment["mt_end"]["text_zh"] == expected[clip, i]["text_zh"]
                        for i, segment in zip(required, selected))
            if can_match and exact:
                seqs = {s["ws_sent"]["seq"] for s in selected}
                arrival = max(r["t_ns"] for r in wire if r["event"]["seq"] in seqs)
                ms = (arrival-case["anchor_ns"])/1e6
                first_correct = ms if first_correct is None else min(first_correct, ms)
            else:
                ms = None
            raw_end = unit["end_sample"]/16
            sensitive_end = 126040 if uid == "wmf-04/153-155" else raw_end
            status = ("correct" if ms is not None else "filtered" if uid in usable and
                      any("gateway_filtered" in s for s in selected) else "incorrect" if selected and
                      any("mt_end" in s for s in selected) else "missing")
            unit_rows.append(dict(unit_id=uid, clip=clip, end_ms=raw_end, sensitivity_end_ms=sensitive_end,
                stable_correct_ms=ms, timing_reference="ai_reviewed", status=status,
                required_segments=required, exact_historical_text=exact,
                judgment_origin="frozen historical AI labels, reused only for exact text; not human verified"))
        firsts[clip] = dict(first_any_zh_s=case["first_any_zh_s"],
                           first_correct_frozen_unit_s=first_correct/1000 if first_correct is not None else None,
                           dom_stability="not_measured_in_PCM_WS_replay")
    timing_input = dict(schema_version=1, evidence_kind="measured", unit_ids=[r["unit_id"] for r in unit_rows],
        manifest_sha256=digest(run / "reference-manifest.json"), scope_label=f"B0 WS seed{seed}, AI references", rows=unit_rows)
    timing = evidence_helper().summarize_timing(timing_input)
    sensitivity = dict(timing_input, rows=[dict(r, end_ms=r["sensitivity_end_ms"]) for r in unit_rows])
    result = dict(seed=seed, completed=data["completed"], causal_errors=data["causal_errors"],
                  asr_exact_matches=sum(parity_asr), asr_expected=88,
                  mt_exact_matches=sum(parity_mt), mt_expected=62, pcm=packet_matches,
                  firsts=firsts, unit_rows=unit_rows, historical_end_timing=timing,
                  sensitivity_timing=evidence_helper().summarize_timing(sensitivity),
                  stages_ms={k: dict(count=len(v), p50=evidence_helper().quantile(v,.5),
                                     p95=evidence_helper().quantile(v,.95), maximum=max(v)) for k,v in stage_ms.items()},
                  reference_status="ai_reviewed", release_eligible=False)
    dump(folder / "analysis.json", result)
    return result


def clock_offsets(calibration):
    browser = min(calibration["browser"], key=lambda r:r["node_end_ns"]-r["node_start_ns"])
    backend = min(calibration["backend"], key=lambda r:r["node_end_ns"]-r["node_start_ns"])
    return dict(browser_ms_to_backend_ns=(browser["node_start_ns"]+browser["node_end_ns"])/2-browser["browser_ms"]*1e6
                +backend["backend_ns"]-(backend["node_start_ns"]+backend["node_end_ns"])/2,
                uncertainty_ns=((browser["node_end_ns"]-browser["node_start_ns"])+
                                (backend["node_end_ns"]-backend["node_start_ns"]))/2)


def browser_analysis(run):
    folder = run / "browser-full"
    result = json.loads((folder / "result.json").read_text())
    if not result["completed"]:
        return dict(completed=False, error=result.get("error"))
    raw = rows(folder / "browser.jsonl")
    captions = [r for r in raw if r["stage"] == "ws_received" and r.get("type") == "caption"]
    cues = readable_cues(captions, raw)
    before, after = clock_offsets(result["clock_before"]), clock_offsets(result["clock_after"])
    server = rows(folder / "model.jsonl")
    sent = {(e["session_id"],e["seq"]):e for e in server if e["stage"] == "ws_sent"}
    joined = set(sent) == {(e["session_id"],e["seq"]) for e in captions}
    mapped = []
    for cue in cues:
        emission = sent.get((cue["session_id"],cue["seq"]))
        if emission:
            observed = cue["first_readable_visible_ms"]
            mapped.append(cue | dict(source_segment=emission["segment_id"],
                readable_backend_ns=observed*1e6+before["browser_ms_to_backend_ns"] if observed is not None else None))
    review_path = run / "reviews/browser-verdict.json"
    review = json.loads(review_path.read_text()) if review_path.exists() else None
    frames = [r for r in raw if r["stage"] == "video_frame"]
    origin = result.get("first_pcm", {}).get("estimated_first_input_t")
    closest = min(frames, key=lambda f:abs(f["t"]-origin)) if origin is not None and frames else None
    observed = {(e["session_id"],e["seq"]) for e in raw if e["stage"] == "dom_visible"}
    removed = {(e["session_id"],e["seq"]) for e in raw if e["stage"] == "dom_removed"}
    observation_complete = observed <= set(sent) and observed <= removed and result["video_frames"] >= 500 and not any(
        e["hidden"] for e in result.get("visibility_events", [{"hidden": True}]))
    output = dict(completed=True, ws_identity_join=joined, dom_observation_complete=observation_complete,
        captions_never_observed=sorted(seq for sid,seq in set(sent)-observed), captions=len(captions),
        readable_captions=sum(c["readable"] for c in cues), cues=mapped,
        ws_to_visible_p95_ms=evidence_helper().quantile([
            c["first_readable_visible_ms"]-c["t"] for c in cues if c["readable"]], .95),
        clock_before=before, clock_after=after,
        clock_offset_drift_ms=(after["browser_ms_to_backend_ns"]-before["browser_ms_to_backend_ns"])/1e6,
        capture_origin_media_s=closest["media_time"] if closest else None,
        capture_origin_frame_resolution_s=1/15, semantic_review=review,
        visibility_scope="isolated unobscured foreground page; no general occlusion/fullscreen claim",
        formal_tail_claim_eligible=False, release_eligible=False)
    dump(folder / "analysis.json", output)
    return output


def report(run):
    frozen_fingerprint(run)
    data = dict(run_id=run.name, baseline="reazon-hy", reference_status="ai_reviewed",
                holdout_accessed=False, human_listening="deferred", production_default_changed=False,
                replay={}, browser_controls={}, resources={})
    if (run / "baseline/score.json").exists():
        data["offline"] = json.loads((run / "baseline/score.json").read_text())
    for repeat in (1, 2):
        if (run / f"replay-{repeat}/run.json").exists():
            data["replay"][str(repeat)] = replay_analysis(run, repeat)
    for mode in ("play", "asr", "mt", "full"):
        path = run / f"browser-{mode}/result.json"
        if path.exists():
            data["browser_controls"][mode] = json.loads(path.read_text())
    if (run / "browser-full/result.json").exists():
        data["browser_analysis"] = browser_analysis(run)
    for path in sorted((run / "comparisons").glob("*/comparison.json")):
        data["resources"][path.parent.name] = json.loads(path.read_text())
        data["resources"][path.parent.name]["monitor_exit_code"] = json.loads(path.with_name("resources.json").read_text())["exit_code"]
    data["release_eligible"] = False
    data["gpu_competition"] = "not_established; Reazon uses CPU, host utilization is not a queue trace"
    dump(run / "summary.json", data)
    return dict(summary=str(run / "summary.json"), replay_runs=len(data["replay"]),
                browser_controls=len(data["browser_controls"]), resources=len(data["resources"]), release_eligible=False)


def acceptance(run):
    report(run)
    data = json.loads((run / "summary.json").read_text())
    checks = dict(offline_exact_parity=data.get("offline", {}).get("exact_output_reproduction", False),
        two_complete_1x_runs=len(data["replay"]) == 2 and all(
            r["completed"] and not r["causal_errors"] for r in data["replay"].values()),
        four_browser_controls=len(data["browser_controls"]) == 4 and all(
            v.get("completed") for v in data["browser_controls"].values()),
        ws_identity_join=data.get("browser_analysis", {}).get("ws_identity_join", False),
        dom_observation_complete=data.get("browser_analysis", {}).get("dom_observation_complete", False),
        readable_dom_signal=data.get("browser_analysis", {}).get("readable_captions", 0)>0,
        browser_ai_review=data.get("browser_analysis", {}).get("semantic_review", {}).get("reference_status") == "ai_reviewed"
            if data.get("browser_analysis", {}).get("semantic_review") else False,
        resource_comparisons=len(data["resources"]) >= 8 and all(
            r["monitor_exit_code"] == 0 for r in data["resources"].values()))
    state = json.loads((run / "state.json").read_text())
    checks["required_steps_passed"] = all(state["steps"].get(name, {}).get("status") == "passed"
        for name in ("fixed-cuts", "fixed-text", "score", "replay-1", "replay-2"))
    for value in data["replay"].values():
        checks[f"seed{value['seed']}_realtime_exact_parity"] = value["asr_exact_matches"] == 88 and value["mt_exact_matches"] == 62 and all(
            p["exact_pcm_and_counters"] for p in value["pcm"])
    outcome = dict(status="B0_ENGINEERING_PASS_PROVISIONAL_REFERENCES" if all(checks.values()) else "B0_INCOMPLETE",
                   checks=checks, ready_for_B1=all(checks.values()), human_verified=False,
                   formal_semantic_acceptance=False, release_eligible=False)
    dump(run / "acceptance.json", outcome)
    return outcome
