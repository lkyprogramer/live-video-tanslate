"""Minimal B0 execution. Future adapters are not imported or selected implicitly."""
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import time

from .events import EventLog
from .freeze import ROOT, dump, rows


def pcm_packets(audio):
    if not audio or len(audio) % 2:
        raise ValueError("PCM must contain complete s16le samples")
    for offset in range(0, len(audio), 6400):
        frame = audio[offset:offset + 6400]
        yield (offset + len(frame)) // 2, frame


async def feed_1x(audio, accept, record, session_id, *, epoch=0):
    anchor = time.monotonic_ns()
    late_ms = []
    for end, frame in pcm_packets(audio):
        due = anchor + round(end / 16000 * 1e9)
        await asyncio.sleep(max(0, (due - time.monotonic_ns()) / 1e9))
        late_ms.append((time.monotonic_ns() - due) / 1e6)
        record("pcm_accepted", session_id=session_id, epoch=epoch, accepted_samples=end,
               pcm_sha256=sha256(frame).hexdigest(), packet_samples=len(frame) // 2)
        await accept(frame)
        record("pcm_consumed", session_id=session_id, epoch=epoch, consumed_samples=end)
    return dict(anchor_ns=anchor, max_send_late_ms=max(late_ms), samples=len(audio) // 2)


def run_probe(experiment_id, mode, manifest):
    if experiment_id != "B0":
        raise NotImplementedError("B0 phase implements only the current baseline")
    run = Path(manifest["run_dir"])
    if mode == "fixed-cuts":
        return asr_probe(run)
    if mode == "fixed-text":
        return mt_probe(run)
    raise NotImplementedError("Native causal-stream adapters belong to later phases")


def asr_probe(run):
    import numpy as np
    from p0.monitor import mark
    from subtitle_service.config import load_settings
    from subtitle_service.local_pipeline import ReazonRecognizer, vad_segments
    settings = load_settings(run / "locks/profiles.toml")
    # The copied profile contains a repository-relative cache path; resolve from the actual root.
    profile = settings.profiles["reazon-hy"]
    lock = json.loads((run / "locks/models.json").read_text())
    paths = {m["id"]: Path(m["local_path"]) for m in lock["models"]}
    mark("asr-load", "start")
    model = ReazonRecognizer(paths["reazon-b0"], paths["silero-v4-b0"], profile.asr)
    mark("asr-load", "end")
    assets = json.loads((run / "asset-manifest.json").read_text())["assets"]
    reference = rows(run / "inputs/reazon.jsonl")
    expected = {(r["clip"], r["segment_id"]): r for r in reference}
    log = EventLog(run / "baseline/asr-events.jsonl", run.name)
    output = []
    try:
        for asset in assets:
            clip = asset["id"]
            mark("asr/" + clip, "start")
            detector, segment_id, accepted = model.new_vad(), 0, 0
            for frame in [*(frame for _, frame in pcm_packets(Path(asset["path"]).read_bytes())), None]:
                if frame is not None:
                    accepted += len(frame) // 2
                    log.record("pcm_accepted", session_id=clip, clip_id=clip, accepted_samples=accepted,
                               pcm_sha256=sha256(frame).hexdigest())
                for start, samples in vad_segments(detector, frame):
                    segment_id += 1
                    end = min(start + len(samples), accepted)
                    span = dict(start_sample=start, end_sample=end, segment_id=segment_id,
                                source_id=f"{clip}/{segment_id}", clip_id=clip)
                    log.record("vad_release", session_id=clip, **span)
                    log.record("asr_start", session_id=clip, **span)
                    t = time.monotonic_ns()
                    ja = model.decode(np.asarray(samples[:end-start], np.float32))
                    elapsed = (time.monotonic_ns() - t) / 1e9
                    log.record("asr_end", session_id=clip, text_ja=ja, **span)
                    previous = expected.get((clip, segment_id), {})
                    output.append(dict(clip=clip, segment_id=segment_id, start_sample=start, end_sample=end,
                        released_at_sample=accepted, flush=frame is None, pcm_path=asset["path"],
                        text_ja=ja, decode_s=elapsed, mt_seed_index=reference.index(previous) if previous else None,
                        source_id=span["source_id"]))
                if frame is not None:
                    log.record("pcm_consumed", session_id=clip, clip_id=clip, consumed_samples=accepted)
            log.record("finished", session_id=clip, clip_id=clip)
            mark("asr/" + clip, "end")
        with (run / "baseline/reazon.jsonl").open("x") as target:
            for row in output:
                target.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        log.close()
    fields = ("clip", "segment_id", "start_sample", "end_sample", "released_at_sample", "flush", "text_ja")
    matches = len(output) == len(reference) and all(tuple(a[k] for k in fields) == tuple(b[k] for k in fields)
                                                     for a, b in zip(output, reference))
    result = dict(rows=len(output), expected=88, exact_parity=matches, mode="fixed-cuts", timing="offline_only")
    dump(run / "baseline/asr-parity.json", result)
    if not matches:
        raise ValueError("ASR/VAD baseline drift; retain this failed attempt")
    return result


def mt_probe(run):
    import mlx.core as mx
    from p0.monitor import mark
    from subtitle_service.app import chinese
    from subtitle_service.local_pipeline import HyTranslator
    lock = json.loads((run / "locks/models.json").read_text())
    snapshot = next(Path(m["local_path"]) for m in lock["models"] if m["id"] == "hymt-b0")
    mark("mt-load", "start")
    model = HyTranslator(snapshot)
    session = model.new_session()
    dump(run / "baseline/mt-settings.json", dict(prompt=session._prompt, generation=asdict(session._config)))
    mark("mt-load", "end")
    asr = rows(run / "baseline/reazon.jsonl")
    scored = {u["clip"] for u in rows(run / "inputs/units.jsonl")}
    results = {}
    for seed in (11, 29):
        log = EventLog(run / f"baseline/mt-events-seed{seed}.jsonl", run.name, repeat_id=f"seed{seed}")
        translated = []
        mark(f"mt-seed{seed}", "start")
        try:
            for row in asr:
                if row["clip"] not in scored:
                    continue
                sid = row["clip"]
                span = {k: row[k] for k in ("source_id", "segment_id", "start_sample", "end_sample")}
                log.record("mt_start", session_id=sid, **span)
                mx.random.seed(seed + row["mt_seed_index"])
                started = time.monotonic_ns()
                zh = model.translate(session, row["text_ja"]) if row["text_ja"] else ""
                elapsed = (time.monotonic_ns() - started) / 1e9
                log.record("mt_end", session_id=sid, raw_zh=zh, filtered_zh=chinese(zh),
                           generation_s=elapsed, mlx_active_bytes=mx.get_active_memory(),
                           mlx_peak_bytes=mx.get_peak_memory(), **span)
                translated.append(row | dict(input_ja=row["text_ja"], text_zh=zh, mt_s=elapsed))
            with (run / f"baseline/mt-seed{seed}.jsonl").open("x") as target:
                for row in translated:
                    target.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            mark(f"mt-seed{seed}", "end")
            log.close()
        expected = rows(run / f"inputs/mt-seed{seed}.jsonl")
        fields = ("clip", "segment_id", "input_ja", "text_zh")
        exact = len(translated) == len(expected) and all(tuple(a[k] for k in fields) == tuple(b[k] for k in fields)
                                                        for a, b in zip(translated, expected))
        results[str(seed)] = dict(rows=len(translated), expected=62, exact_parity=exact)
    session.close()
    dump(run / "baseline/mt-parity.json", results)
    if not all(x["exact_parity"] for x in results.values()):
        raise ValueError("MT baseline drift; scores cannot be inherited")
    return results
