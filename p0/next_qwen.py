"""Measure Qwen3-ASR on frozen PCM slices in a separate environment."""

import argparse
from contextlib import nullcontext
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import time

from monitor import mark
from review import percentile


def mt_worker(snapshot):
    from subtitle_service.local_pipeline import HyTranslator

    hy = HyTranslator(snapshot)
    session = hy.new_session()
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        source = json.loads(line)
        start = time.perf_counter()
        zh = hy.translate(session, source) if source else ""
        print(json.dumps({"text_zh": zh, "mt_s": time.perf_counter() - start}), flush=True)


def decode(args):
    import mlx.core as mx
    from mlx_audio.stt import load
    import numpy as np

    manifest = json.loads((args.run / "frozen.json").read_text())
    snapshot = args.cache / "hub/models--mlx-community--Qwen3-ASR-0.6B-8bit/snapshots" / manifest["qwen_revision"]
    with (args.run / "slices.jsonl").open() as source:
        rows = [json.loads(line) for line in source]
    model = load(snapshot)
    assert model.sample_rate == manifest["sample_rate"] == 16000

    def recognize(audio):
        return model.generate(audio, language="Japanese", temperature=0.0, max_tokens=256).text.strip()

    smoke = recognize(np.zeros(16000, dtype=np.float32))
    print(json.dumps({"raw_silence_text": smoke, "mlx": version("mlx"), "mlx_audio": version("mlx-audio"),
                      "gpu_model_active_mib": round(mx.get_active_memory() / 2**20, 2)}, ensure_ascii=False))
    if args.mt_python:
        mt_snapshot = args.cache / "hub/models--mlx-community--Hy-MT2-1.8B-4bit/snapshots" / manifest["hy_revision"]
        command = [str(args.mt_python), str(Path(__file__).resolve()), "mt-worker", str(mt_snapshot)]
        mt_context = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    else:
        mt_context = nullcontext()

    pcm = {}
    times = []
    translated_times = []
    with mt_context as mt, args.output.open("x", encoding="utf-8", buffering=1) as output:
        try:
            if mt and json.loads(mt.stdout.readline()) != {"ready": True}:
                raise RuntimeError("MT worker did not start")
            pending = None
            mark("qwen", "start")
            started = time.perf_counter()
            for row in rows:
                path = row["pcm_path"]
                if path not in pcm:
                    pcm[path] = np.frombuffer(Path(path).read_bytes(), dtype="<i2")
                audio = pcm[path][row["start_sample"]:row["end_sample"]].astype(np.float32) / 32768
                started_asr = time.perf_counter()
                text = recognize(audio)
                elapsed = time.perf_counter() - started_asr
                times.append(elapsed)
                current = row | {"text_ja": text, "decode_s": elapsed}
                if not mt:
                    output.write(json.dumps(current, ensure_ascii=False) + "\n")
                    continue
                if pending is not None:
                    result = json.loads(mt.stdout.readline())
                    translated_times.append(result["mt_s"])
                    output.write(json.dumps(pending | result, ensure_ascii=False) + "\n")
                mt.stdin.write(json.dumps(text) + "\n")
                pending = current
            if pending is not None:
                result = json.loads(mt.stdout.readline())
                translated_times.append(result["mt_s"])
                output.write(json.dumps(pending | result, ensure_ascii=False) + "\n")
            wall = time.perf_counter() - started
            mark("qwen", "end")
        finally:
            if mt:
                mt.stdin.close()
                try:
                    code = mt.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    mt.kill()
                    mt.wait()
                    raise
                if code != 0:
                    raise RuntimeError("MT worker failed")

    audio_seconds = sum(row["end_sample"] - row["start_sample"] for row in rows) / model.sample_rate
    print(json.dumps({"slices": len(times), "asr_p50_s": percentile(times, .50), "asr_p95_s": percentile(times, .95),
                      "asr_rtf": sum(times) / audio_seconds, "mt_p95_s": percentile(translated_times, .95),
                      "wall_s": round(wall, 3), "gpu_peak_mib": round(mx.get_peak_memory() / 2**20, 2)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("mt-worker")
    worker.add_argument("snapshot", type=Path)
    asr = commands.add_parser("decode")
    asr.add_argument("run", type=Path)
    asr.add_argument("output", type=Path)
    asr.add_argument("--cache", type=Path, default=Path(__file__).resolve().parent.parent / ".model-cache")
    asr.add_argument("--mt-python", type=Path)
    args = parser.parse_args()
    if args.command == "mt-worker":
        mt_worker(args.snapshot)
    else:
        decode(args)
