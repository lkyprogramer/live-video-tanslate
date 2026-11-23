"""Measure the current Silero/Reazon capability before opening A1."""
from collections import Counter
from hashlib import sha256
from math import ceil
import argparse
import json
from pathlib import Path
from time import monotonic_ns

import numpy as np

from p0.p2.events import EventLog
from p0.p2.freeze import dump
from p0.p2.runner import pcm_packets
from subtitle_service.config import load_settings
from subtitle_service.local_pipeline import RATE, ReazonRecognizer, vad_segments


PUNCTUATION = frozenset("。？！?!")


def decode(recognizer, audio, start, end):
    assert 0 <= start < end <= len(audio)
    stream = recognizer.model.create_stream()
    stream.accept_waveform(RATE, audio[start:end])
    started = monotonic_ns()
    recognizer.model.decode_stream(stream)
    elapsed_ns = monotonic_ns() - started
    result = stream.result
    anchors = [start + round(t * RATE) for t in result.timestamps]
    assert len(result.tokens) == len(anchors)
    assert anchors == sorted(anchors)
    assert all(start <= anchor <= end for anchor in anchors), (start, end, anchors[-1:])
    return dict(start_sample=start, end_sample=end, decode_ns=elapsed_ns,
                text_ja=result.text.strip(), tokens=list(result.tokens), anchors=anchors)


def probe(run, output):
    output.mkdir()
    profile = load_settings(run / "locks/profiles.toml").profiles["reazon-hy"]
    locks = json.loads((run / "locks/models.json").read_text())
    paths = {row["id"]: Path(row["local_path"]) for row in locks["models"]}
    recognizer = ReazonRecognizer(paths["reazon-b0"], paths["silero-v4-b0"], profile.asr)
    assets = json.loads((run / "asset-manifest.json").read_text())["assets"]
    log = EventLog(output / "events.jsonl", output.name, experiment_id="A0")
    release_counts, tick_counts = Counter(), Counter()
    lengths = {4: [], 8: [], 12: []}
    overlaps, trailing_residuals = [], []
    try:
        for asset in assets:
            clip = asset["id"]
            raw = Path(asset["path"]).read_bytes()
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
            vad = recognizer.new_vad()
            shift = recognizer.vad_config.silero_vad.window_size
            active_start = None
            release_end = 0
            next_tick = 0
            last = None
            for accepted, frame in [*pcm_packets(raw), (len(audio), None)]:
                t = monotonic_ns()
                releases = list(vad_segments(vad, frame))
                vad_ns = monotonic_ns() - t
                tail = accepted // shift * shift
                activity = vad.current_segment if vad.is_speech_detected() else None
                if activity is not None:
                    if activity.start != active_start:
                        next_tick = accepted
                        last = None
                    active_start = activity.start
                    observed_end = active_start + len(activity.samples)
                    assert np.array_equal(activity.samples, audio[active_start:observed_end])
                else:
                    observed_end = active_start = None
                log.record("probe_vad", session_id=clip, accepted_samples=accepted,
                           active_start=active_start, observed_end=observed_end, vad_ns=vad_ns, eof=frame is None)
                for start, samples in releases:
                    end = start + len(samples)
                    assert 0 <= start < end <= accepted
                    assert np.array_equal(samples, audio[start:end])
                    if frame is None:
                        reason = "eof"
                        if accepted != end:
                            trailing_residuals.append(dict(clip=clip, samples=accepted-end))
                    else:
                        # v2.0.1 Silero v4: end = processed tail - effective silence duration.
                        trim = tail - end
                        reason = {9600: "natural_pause", 1600: "length_managed_pause"}.get(trim, "unresolved")
                    release_counts[reason] += 1
                    if start < release_end:
                        overlaps.append(dict(clip=clip, overlap_samples=release_end-start))
                    log.record("observed_release", session_id=clip, accepted_samples=accepted,
                               start_sample=start, end_sample=end, processed_tail=tail, reason=reason)
                    release_end = end
                if activity is None or accepted < next_tick:
                    continue
                left = max(0, active_start - 12800, accepted - 12 * RATE)
                if left > active_start:
                    tick_counts["window_cannot_contain_active_start"] += 1
                    continue
                next_tick += 12800
                snapshot = decode(recognizer, audio, left, accepted)
                tick_counts["snapshots"] += 1
                tick_counts["has_sentence_punctuation"] += bool(PUNCTUATION.intersection(snapshot["text_ja"]))
                if last is not None:
                    # Exploratory full-window prefix with the same origin. A1 must also handle moving origins.
                    stable = 0
                    for a, b, ta, tb in zip(last["tokens"], snapshot["tokens"], last["anchors"], snapshot["anchors"]):
                        if a != b or abs(ta-tb) > 3200 or max(ta, tb) >= last["end_sample"] - 6400:
                            break
                        stable += 1
                    tick_counts["has_stable_prefix"] += bool(stable)
                    tick_counts["has_stable_punctuation"] += bool(
                        PUNCTUATION.intersection("".join(snapshot["tokens"][:stable])))
                    snapshot["stable_prefix_tokens"] = stable
                log.record("probe_window", session_id=clip, vad_active_start=active_start,
                           pcm_sha256=sha256(raw[left*2:accepted*2]).hexdigest(), **snapshot)
                last = snapshot
        for asset in (item for item in assets if item["samples"] >= 12*RATE and item["id"] != "silence"):
            audio = np.fromfile(asset["path"], dtype="<i2").astype(np.float32) / 32768
            for offset in (0, len(audio)//4, len(audio)-12*RATE):
                for seconds, values in lengths.items():
                    if offset + seconds*RATE > len(audio):
                        continue
                    snapshot = decode(recognizer, audio, offset, offset+seconds*RATE)
                    values.append(snapshot["decode_ns"]/1e6)
                    log.record("timed_window", session_id=asset["id"], length_s=seconds, **snapshot)
    finally:
        log.close()
    timing = {}
    for length, values in lengths.items():
        values.sort()
        timing[str(length)] = dict(count=len(values), p50_ms=values[ceil(.5*len(values))-1],
                                  p95_ms=values[ceil(.95*len(values))-1], max_ms=values[-1])
    dump(output / "summary.json", dict(release_counts=release_counts, tick_counts=tick_counts,
         window_ms=timing, overlapping_vad_releases=overlaps, eof_unclassified_residuals=trailing_residuals,
         release_method="sherpa-onnx v2.0.1 compiled behavior checked against released ranges and input samples",
         timestamp_scope="token anchors, no word-end claim",
         vad_scheduler="separate VAD lane; preserve current packet granularity and native config"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    probe(args.run, args.output)
