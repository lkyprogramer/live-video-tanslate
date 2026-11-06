"""Replay P0 PCM through a local JSONL speech recognizer and the existing Hy-MT2."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import time

from review import percentile
from run import BYTES_PER_SECOND, MODEL_REVISIONS, ROOT, SUITE
from monitor import mark


def write_jsonl(file, item):
    file.write(json.dumps(item, ensure_ascii=False) + "\n")
    file.flush()


def translate(engine, anchor, queued, event, event_id):
    started = time.monotonic()
    zh = engine._translate_text(event["ja"])
    finished = time.monotonic()
    return {
        "id": event_id, "t": finished - anchor, "asr_t": event["t"],
        "start": event["start"], "end": event["end"], "ja": event["ja"], "zh": zh,
        "mt_queue_s": started - queued, "mt_s": finished - started,
    }


async def replay(asr_command, sentence_finals, clip, output, worker, engine):
    clip_id = clip["id"]
    pcm = SUITE.parent / clip["file"]
    audio = pcm.read_bytes()
    if hashlib.sha256(audio).hexdigest() != clip["sha256"]:
        raise ValueError(f"Fixture checksum mismatch: {pcm}")
    duration = len(audio) / BYTES_PER_SECOND
    translations = []
    first_partial = first_final = anchor = eof = None
    partial_lags = []
    loop = asyncio.get_running_loop()
    command = [*asr_command, str(pcm)]
    if sentence_finals:
        command.append("--sentence-finals")
    with (output / f"{clip_id}.asr.jsonl").open("w") as trace, (output / f"{clip_id}.stderr.log").open("w") as stderr:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=stderr,
        )
        try:
            async with asyncio.timeout(duration + 30):
                async for line in process.stdout:
                    received = time.monotonic()
                    event = json.loads(line)
                    write_jsonl(trace, event)
                    if event["type"] == "start":
                        anchor = received
                        mark(clip_id, "start")
                    elif event["type"] == "eof":
                        eof = event
                    elif event["type"] == "asr" and event["ja"].strip():
                        if event["final"]:
                            if first_final is None:
                                first_final = event["t"]
                            translations.append(loop.run_in_executor(
                                worker, translate, engine, anchor, received, event, len(translations) + 1,
                            ))
                        else:
                            if first_partial is None:
                                first_partial = event["t"]
                            if event["end"] is not None:
                                partial_lags.append(max(0, event["t"] - event["end"]))
                code = await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    if code or not eof or eof["input_bytes"] != len(audio):
        raise RuntimeError(f"{clip_id}: incomplete ASR replay (exit {code}, EOF {eof})")
    with (output / f"{clip_id}.zh.jsonl").open("w") as trace:
        rows = []
        for task in translations:
            row = await task
            write_jsonl(trace, row)
            rows.append(row)
    mark(clip_id, "end")

    chinese = [
        row for row in rows
        if re.search(r"[\u3400-\u9fff]", row["zh"]) and not re.search(r"[\u3040-\u30ff]", row["zh"])
    ]
    return {
        "clip": clip_id, "duration_s": duration, "eof": True,
        "input_bytes": eof["input_bytes"], "max_feed_late_ms": eof["max_feed_late_ms"],
        "first_partial_s": first_partial, "first_final_s": first_final,
        "first_chinese_s": chinese[0]["t"] if chinese else None,
        "final_segments": len(rows), "partial_range_end_to_result_p95_s": percentile(partial_lags, .95),
        "mt_p95_s": percentile([row["mt_s"] for row in rows], .95),
        "mt_queue_max_s": max((row["mt_queue_s"] for row in rows), default=0),
        "asr_drain_s": eof["t"] - eof["input_end"],
        "pipeline_drain_s": max(eof["t"], rows[-1]["t"] if rows else 0) - duration,
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("clips", nargs="+")
    parser.add_argument("--sentence-finals", action="store_true")
    parser.add_argument("--asr-python", type=Path, help="Run the recognizer script in this Python environment")
    args = parser.parse_args()
    suite = {item["id"]: item for item in json.loads(SUITE.read_text())}
    if set(args.clips) - suite.keys() or len(set(args.clips)) != len(args.clips):
        parser.error("Expected distinct P0 clip IDs")

    cache = ROOT / ".model-cache"
    mt_repo = "Hy-MT2-1.8B-4bit"
    revision = (cache / f"hub/models--mlx-community--{mt_repo}/refs/main").read_text().strip()
    if revision != MODEL_REVISIONS[mt_repo]:
        raise RuntimeError(f"Unexpected cached model revision for {mt_repo}: {revision}")
    os.environ.update(HF_HOME=str(cache), HF_HUB_OFFLINE="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1")
    from whisperlivekit.translation_mlx_llm_mt import MlxLlmTranslation

    args.output.mkdir(parents=True, exist_ok=False)
    asr_command = [str(args.binary.resolve())]
    if args.asr_python:
        asr_command.insert(0, str(args.asr_python.absolute()))
    with ThreadPoolExecutor(max_workers=1) as worker:
        engine = await asyncio.get_running_loop().run_in_executor(
            worker, lambda: MlxLlmTranslation(
                model_id="hy-mt2-1.8b-4bit", source_language="ja", target_language="zh-cn",
            ),
        )
        with (args.output / "runs.jsonl").open("w") as summary:
            for clip_id in args.clips:
                result = await replay(asr_command, args.sentence_finals, suite[clip_id], args.output, worker, engine)
                write_jsonl(summary, result)
                print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
