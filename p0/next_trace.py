"""Verify the N0 diagnostic joins and measure delay by stage."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from next_asr import RATE, read_rows
from review import percentile


def replay(run):
    output = run / "replay-v2"
    sessions = defaultdict(list)
    for row in read_rows(output / "model.jsonl"):
        sessions[row["session_id"]].append(row)
    expected = defaultdict(list)
    for row in read_rows(run / "slices.jsonl"):
        expected[row["clip"]].append(tuple(row[k] for k in ("segment_id", "start_sample", "end_sample")))
    stage_times = defaultdict(list)
    counts = Counter()
    clips = {}
    for case in json.loads((output / "run.json").read_text())["cases"]:
        clip = case["id"]
        rows = read_rows(output / f"{clip}.jsonl")
        ready, *_, finished = rows
        assert ready["event"]["type"] == "ready" and finished["event"]["type"] == "finished"
        trace = sessions[ready["event"]["session_id"]]
        events = defaultdict(list)
        for row in trace:
            events[row["stage"]].append(row)
        for stage in ("pcm_accepted", "pcm_consumed"):
            ends = [row["end_sample"] for row in events[stage]]
            assert ends == sorted(set(ends)) and ends[-1] == finished["event"]["accepted_samples"]
        cuts = events["vad_released"]
        assert expected[clip] == [tuple(row[k] for k in ("segment_id", "start_sample", "end_sample")) for row in cuts]
        by_segment = defaultdict(dict)
        for row in trace:
            if "segment_id" in row:
                by_segment[row["segment_id"]][row["stage"]] = row
        for segment in by_segment.values():
            sequence = ["vad_released", "asr_start", "asr_end"]
            if segment["asr_end"]["text_ja"]:
                sequence.extend(["mt_enqueued", "mt_start", "mt_end"])
                sequence.append("gateway_filtered" if "gateway_filtered" in segment else "ws_sent")
            times = [segment[k]["t"] for k in sequence]
            assert times == sorted(times)
            stage_times["vad_after_audio_end"].append(max(0, times[0] - events["ready"][0]["t"] -
                                                           segment["vad_released"]["end_sample"] / RATE))
            stage_times["asr"].append(times[2] - times[1])
            if len(times) > 3:
                stage_times["mt_queue"].append(times[4] - times[3])
                stage_times["mt"].append(times[5] - times[4])
        wire = [row for row in rows if row["event"]["type"] == "caption"]
        spans = ("seq", "start_sample", "end_sample")
        assert [tuple(row["event"][k] for k in spans) for row in wire] == [
            tuple(row[k] for k in spans) for row in events["ws_sent"]]
        counts.update(row["stage"] for row in trace)
        clips[clip] = {"captions": len(wire), "first_raw_s": round(wire[0]["t"], 3) if wire else None,
                       "warnings": [row["event"]["code"] for row in rows if row["event"]["type"] == "warning"]}
    return {"clips": clips, "trace_counts": counts,
            "stage_seconds": {k: {"p50": percentile(v, .5), "p95": percentile(v, .95)} for k, v in stage_times.items()}}


def browser(run):
    root = run / "browser"
    rows = read_rows(root / "browser.jsonl")
    frames = [row for row in rows if row["stage"] in ("video_frame", "video_ended")]
    ready = next(row for row in rows if row.get("type") == "ready")
    session = ready["session_id"]
    wire = {(row["session_id"], row["seq"]): row for row in rows if row.get("type") == "caption"}
    sent = {(row["session_id"], row["seq"]): row for row in read_rows(root / "model.jsonl")
            if row["stage"] == "ws_sent" and row["session_id"] == session}
    assert wire.keys() == sent.keys()
    first_pcm = next(row for row in rows if row["stage"] == "first_pcm_sent" and row["session_id"] == session)
    start = next(row["t"] for row in rows if row["stage"] == "start_action")
    first_frame = min(frames, key=lambda f: abs(f["t"] - first_pcm["estimated_first_input_t"]))
    cues = []
    for visible in (row for row in rows if row["stage"] == "dom_visible"):
        key = (visible["session_id"], visible["seq"])
        ws = wire[key]
        removed = next(row for row in rows if row["stage"] == "dom_removed" and row["t"] >= visible["t"] and
                       (row["session_id"], row["seq"]) == key)
        assert ws["t"] <= visible["t"] <= removed["t"]
        length = sum(ch.isalnum() for ch in ws["text_zh"])
        duration = (removed["t"] - visible["t"]) / 1000
        cues.append({"seq": key[1], "text_zh": ws["text_zh"], "visible_s_from_action": round((visible["t"] - start) / 1000, 3),
                     "ws_to_visible_ms": round(visible["t"] - ws["t"], 3), "visible_duration_s": round(duration, 3),
                     "long_enough": duration >= max(.6, length / 12)})
    return {"pcm_start_estimated_media_time_s": first_frame["media_time"],
            "ready_ms_from_action": round(ready["t"] - start, 1), "frames": len(frames),
            "captions": cues, "ws_to_visible_p95_ms": percentile([r["ws_to_visible_ms"] for r in cues], .95)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps({"replay": replay(args.run), "browser": browser(args.run)}, ensure_ascii=False, indent=2))
