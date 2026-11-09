"""Paced ASR/MT component controls while an isolated Chrome plays the same clip."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

from .events import EventLog
from .freeze import rows
from .runner import feed_1x


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("mode", choices=("asr", "mt"))
    args = parser.parse_args()
    run, output = args.run, args.output
    from p0.monitor import mark
    from subtitle_service.config import load_settings
    from subtitle_service.local_pipeline import HyTranslator, ReazonRecognizer, vad_segments
    lock = json.loads((run / "locks/models.json").read_text())
    paths = {m["id"]: Path(m["local_path"]) for m in lock["models"]}
    mark(args.mode + "-warmup", "start")
    if args.mode == "asr":
        import numpy as np
        profile = load_settings(run / "locks/profiles.toml").profiles["reazon-hy"]
        model = ReazonRecognizer(paths["reazon-b0"], paths["silero-v4-b0"], profile.asr)
    else:
        import mlx.core as mx
        model = HyTranslator(paths["hymt-b0"])
        session = model.new_session()
        model.translate(session, "こんにちは")
    mark(args.mode + "-warmup", "end")
    print(json.dumps(dict(type="ready", monotonic_ns=time.monotonic_ns())), flush=True)
    if sys.stdin.readline().strip() != "go":
        raise ValueError("control not released by the browser runner")
    log = EventLog(output / "component-events.jsonl", run.name, repeat_id=args.mode)
    sid = "csj-task-" + args.mode

    async def execute():
        if args.mode == "asr":
            detector = model.new_vad()
            accepted, segments = 0, []
            async def accept(frame):
                nonlocal accepted
                accepted += len(frame)//2
                for start, samples in vad_segments(detector, frame):
                    t = time.monotonic_ns()
                    ja = model.decode(np.asarray(samples, np.float32))
                    segments.append(dict(start_sample=start, end_sample=min(start+len(samples), accepted), text_ja=ja))
                    log.record("asr_end", session_id=sid, elapsed_ns=time.monotonic_ns()-t, **segments[-1])
            result = await feed_1x((run.parent.parent / "fixtures/csj-task.pcm").read_bytes(), accept, log.record, sid)
            for start, samples in vad_segments(detector, None):
                ja = model.decode(np.asarray(samples, np.float32))
                segments.append(dict(start_sample=start, end_sample=min(start+len(samples), accepted), text_ja=ja))
            return result | dict(segments=segments)
        references = [r for r in rows(run / "baseline/reazon.jsonl") if r["clip"] == "csj-task"]
        anchor = time.monotonic_ns()
        translated = []
        for row in references:
            due = anchor + round(row["released_at_sample"]/16000*1e9)
            await asyncio.sleep(max(0, (due-time.monotonic_ns())/1e9))
            mx.random.seed(11+row["mt_seed_index"])
            t = time.monotonic_ns()
            zh = model.translate(session, row["text_ja"])
            item = dict(source_id=row["source_id"], text_zh=zh, elapsed_ns=time.monotonic_ns()-t,
                        mlx_active_bytes=mx.get_active_memory(), mlx_peak_bytes=mx.get_peak_memory())
            translated.append(item)
            log.record("mt_end", session_id=sid, **item)
        session.close()
        return dict(anchor_ns=anchor, translations=translated)

    try:
        result = asyncio.run(execute())
        (output / "component.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    finally:
        log.close()


if __name__ == "__main__":
    main()
