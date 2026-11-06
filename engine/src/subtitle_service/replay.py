import argparse
import asyncio
from contextlib import contextmanager, suppress
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .app import AUDIO, FRAME_BYTES
from .cli import token_path
from .config import load_settings, load_token

BYTES_PER_SECOND = 32000


def mark(clip, phase):
    if path := os.environ.get("P0_RESOURCE_PHASES"):
        with open(path, "a") as log:
            log.write(json.dumps({"t": time.monotonic(), "clip": clip, "phase": phase}) + "\n")


@contextmanager
def launched(config_path, config, output, diagnostics):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", config.server.port)) == 0:
            raise RuntimeError("service port already in use")
    env = os.environ.copy()
    if diagnostics:
        path = output.resolve() / "model.jsonl"
        path.touch(mode=0o600, exist_ok=False)
        env["SUBTITLE_BENCH_TRACE"] = str(path)
    with (output / "server.log").open("w") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "subtitle_service.cli", "--config", str(config_path)],
            stdout=log, stderr=subprocess.STDOUT, env=env,
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and child.poll() is None:
                try:
                    with opener.open(f"http://127.0.0.1:{config.server.port}/v1/health", timeout=1) as response:
                        if json.load(response)["status"] == "ready":
                            break
                except urllib.error.HTTPError as exc:
                    if json.load(exc).get("status") == "unavailable":
                        raise RuntimeError("default model warmup failed") from None
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(.15)
            else:
                raise RuntimeError("service failed to become ready")
            yield
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


async def replay(config, profile, token, clip, audio, trace):
    result = {"id": clip, "eof": False, "captions": 0, "warnings": 0, "max_send_late_ms": 0}
    try:
        async with connect(f"ws://127.0.0.1:{config.server.port}/v1/captions",
                           origin=config.server.allowed_origin, proxy=None) as ws:
            await ws.send(json.dumps({"type": "start", "version": 1, "profile_id": profile,
                                      "audio": AUDIO, "token": token}))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if ready.get("type") != "ready" or ready.get("language") != "zh-Hans":
                return result | {"error": ready.get("code", "invalid_ready")}
            session_id = ready["session_id"]
            anchor = time.monotonic()
            mark(clip, "start")

            async def send():
                for offset in range(0, len(audio), FRAME_BYTES):
                    end = min(offset + FRAME_BYTES, len(audio))
                    due = anchor + end / BYTES_PER_SECOND
                    await asyncio.sleep(max(0, due - time.monotonic()))
                    await ws.send(audio[offset:end])
                    late = (time.monotonic() - due) * 1000
                    result["max_send_late_ms"] = max(result["max_send_late_ms"], round(late, 2))
                await ws.send('{"type":"finish"}')

            sender = asyncio.create_task(send())
            try:
                async with asyncio.timeout(len(audio) / BYTES_PER_SECOND + 12):
                    async for raw in ws:
                        event = json.loads(raw)
                        if event.get("session_id") != session_id:
                            raise RuntimeError("event belongs to a different session")
                        trace.write(json.dumps({"t": time.monotonic() - anchor, "event": event},
                                               ensure_ascii=False) + "\n")
                        if event["type"] == "caption":
                            result["captions"] += 1
                        elif event["type"] == "warning":
                            result["warnings"] += 1
                        elif event["type"] == "finished":
                            result["eof"] = event["accepted_samples"] * 2 == len(audio)
                            result["drain_seconds"] = max(0, time.monotonic() - anchor - len(audio) / BYTES_PER_SECOND)
                            break
                        elif event["type"] == "error":
                            result["error"] = event["code"]
                            break
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                mark(clip, "end")
    except (WebSocketException, OSError, TimeoutError) as exc:
        result["error"] = type(exc).__name__
    if not result["eof"]:
        result.setdefault("error", "missing_finished")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch-config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clips", nargs="+")
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    config = load_settings(args.launch_config)
    suite = json.loads(args.suite.read_text())
    if args.clips:
        suite = [x for x in suite if x["id"] in args.clips]
        if len(suite) != len(args.clips):
            parser.error("unknown or duplicate clip")
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    with launched(args.launch_config, config, args.output, args.diagnostics):
        token = load_token(token_path(), create=False)
        for clip in suite:
            audio = (args.suite.parent / clip["file"]).read_bytes()
            if hashlib.sha256(audio).hexdigest() != clip["sha256"]:
                raise ValueError(f"incorrect PCM digest for {clip['id']}")
            with (args.output / f"{clip['id']}.jsonl").open("w", buffering=1) as trace:
                result = asyncio.run(replay(config, args.profile, token, clip["id"], audio, trace))
            results.append(result)
            print(json.dumps(result), flush=True)
    (args.output / "run.json").write_text(json.dumps({"profile": args.profile, "cases": results}, indent=2) + "\n")
    if not all(x["eof"] for x in results):
        raise SystemExit(1)
