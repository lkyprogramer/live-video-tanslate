"""Run one pinned WLK model against the fixed suite using native /asr WS."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import websockets
from websockets.exceptions import ConnectionClosed
from monitor import mark


ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 18765
BYTES_PER_SECOND = 32000
FRAME_BYTES = 6400
SUITE = ROOT / "p0/fixtures/suite.json"
MODEL_REVISIONS = {
    "whisper-small-mlx": "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
    "whisper-large-v3-turbo": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
    "Hy-MT2-1.8B-4bit": "e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912",
}


def wait_ready(server, port):
    started = time.monotonic()
    local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() - started < 180:
        if server.poll() is not None:
            raise RuntimeError(f"Engine exited during startup ({server.returncode})")
        try:
            with local_http.open(f"http://{HOST}:{port}/health", timeout=1) as response:
                health = json.load(response)
            if health.get("ready") and health.get("backend") == "mlx-whisper":
                return time.monotonic() - started
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.2)
    raise TimeoutError("Engine readiness exceeded 180 seconds")


async def replay(audio, token, trace_file, port):
    loop = asyncio.get_running_loop()
    end_of_audio = len(audio) / BYTES_PER_SECOND
    async with websockets.connect(
        f"ws://{HOST}:{port}/asr?mode=full",
        additional_headers={"Authorization": f"Bearer {token}"},
        proxy=None,
    ) as ws:
        config = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if config.get("type") != "config" or not config.get("useAudioWorklet") or config.get("mode") != "full":
            raise RuntimeError(f"Unexpected engine config: {config}")

        delays = []
        anchor = loop.time()
        result = {"updates": 0, "eof": False, "error": None}

        async def send():
            for offset in range(0, len(audio), FRAME_BYTES):
                end = min(offset + FRAME_BYTES, len(audio))
                due = anchor + end / BYTES_PER_SECOND
                await asyncio.sleep(max(0, due - loop.time()))
                await ws.send(audio[offset:end])
                delays.append(loop.time() - due)
            await ws.send(b"")

        async def receive():
            async for raw in ws:
                received = loop.time() - anchor
                event = json.loads(raw)
                trace_file.write(json.dumps({"t": round(received, 6), "event": event}, ensure_ascii=False) + "\n")
                result["updates"] += 1
                if event.get("type") == "ready_to_stop":
                    result["eof"] = True
                    result["drain_seconds"] = max(0, received - end_of_audio)
                if event.get("type") == "error" or event.get("status") == "error":
                    result["error"] = event.get("error", "Engine error")

        try:
            async with asyncio.timeout(end_of_audio + 30):
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(send())
                    tasks.create_task(receive())
        except* (ConnectionClosed, TimeoutError) as errors:
            result["error"] = result["error"] or str(errors.exceptions[0])

        result["max_send_late_ms"] = round(max(delays, default=0) * 1000, 2)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=("small", "large-v3-turbo"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--only", nargs="+", metavar="CLIP")
    parser.add_argument("--variant", choices=("mixed", "full-mlx", "localagreement"), default="mixed")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    suite = json.loads(SUITE.read_text())
    if args.only:
        suite = [item for item in suite if item["id"] in args.only]
        if len(suite) != len(set(args.only)):
            parser.error("Unknown clip in --only")

    cache = ROOT / ".model-cache"
    decoder_cache = cache / "whisper-pt"
    if args.variant == "mixed" and not (decoder_cache / f"{args.model}.pt").is_file():
        raise RuntimeError(f"Missing local Whisper PyTorch decoder checkpoint for {args.model}")
    for repo, revision in MODEL_REVISIONS.items():
        main_ref = cache / f"hub/models--mlx-community--{repo}/refs/main"
        if main_ref.read_text().strip() != revision:
            raise RuntimeError(f"Unexpected cached model revision for {repo}")

    with socket.socket() as check_port:
        if check_port.connect_ex((HOST, args.port)) == 0:
            raise RuntimeError(f"Port {args.port} is already in use")

    args.output.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    env = os.environ | {
        "WLK_API_TOKEN": token,
        "HF_HOME": str(cache),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "PYTHONUNBUFFERED": "1",
    }
    launcher = [str(Path(sys.executable).parent / "whisperlivekit-server")]
    if args.variant == "full-mlx":
        launcher = [sys.executable, str(Path(__file__).resolve()), "--serve-full-mlx"]
    policy = "localagreement" if args.variant == "localagreement" else "simulstreaming"
    command = [
        *launcher,
        "--host", HOST, "--port", str(args.port), "--pcm-input",
        "--warmup-file", str(ROOT / "../whisper.cpp/samples/jfk.wav"),
        "--model_cache_dir", str(decoder_cache),
        "--backend", "mlx-whisper", "--backend-policy", policy, "--model", args.model,
        "--language", "ja", "--target-language", "zh-cn",
        "--translation-backend", "mlx-llm-mt", "--mlx-llm-mt-model", "hy-mt2-1.8b-4bit",
        "--retention-seconds", "30", "--max-concurrent-sessions", "1",
        "--max-buffered-audio", "4", "--backpressure-timeout", "1", "--log-level", args.log_level,
    ]
    summary = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "variant": args.variant,
        "model_revisions": MODEL_REVISIONS,
        "engine_command": command,
        "cases": [],
    }
    try:
        with (args.output / "server.log").open("w") as server_log:
            server = subprocess.Popen(command, env=env, stdout=server_log, stderr=subprocess.STDOUT)
            try:
                summary["ready_seconds"] = round(wait_ready(server, args.port), 3)
                print(f"{args.model} ready in {summary['ready_seconds']}s", flush=True)
                for clip in suite:
                    if server.poll() is not None:
                        raise RuntimeError(f"Engine died ({server.returncode})")
                    audio = (SUITE.parent / clip["file"]).read_bytes()
                    if hashlib.sha256(audio).hexdigest() != clip["sha256"]:
                        raise ValueError(f"PCM digest mismatch for {clip['id']}")
                    print(f"{args.model} {clip['id']} {clip['duration']:.1f}s", flush=True)
                    with (args.output / f"{clip['id']}.jsonl").open("w", buffering=1) as trace:
                        mark(clip["id"], "start")
                        result = asyncio.run(replay(audio, token, trace, args.port))
                        mark(clip["id"], "end")
                    summary["cases"].append(clip | result)
                    print(result, flush=True)
                    time.sleep(0.3)
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
    finally:
        (args.output / "run.json").write_text(json.dumps(summary, indent=2) + "\n")
    if not all(item["eof"] and not item["error"] for item in summary["cases"]):
        sys.exit(1)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--serve-full-mlx"]:
        from whisperlivekit.basic_server import main as serve
        from whisperlivekit.simul_whisper.backend import SimulStreamingASR

        # WLK 0.2.26 reads this setting at initialization, but its CLI does
        # not expose it. Apply the setting only to this benchmark child.
        SimulStreamingASR.use_full_mlx = True
        del sys.argv[1]
        serve()
    else:
        main()
