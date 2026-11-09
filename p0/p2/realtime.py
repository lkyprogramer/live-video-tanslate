"""Real 1x PCM replay through the existing gateway with experimental seed control."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from websockets.asyncio.client import connect

from .events import EventLog, validate_causality
from .freeze import ROOT, dump, rows
from .runner import pcm_packets


def replay_baseline(run, repeat):
    from subtitle_service.app import AUDIO
    from subtitle_service.config import load_settings, load_token
    from subtitle_service.replay import launched
    from p0.monitor import mark
    seed = {1: 11, 2: 29}[repeat]
    output = run / f"replay-{repeat}"
    output.mkdir()
    text = (run / "locks/profiles.toml").read_text().replace('../../.model-cache', str(ROOT / '.model-cache'))
    # Use a dedicated port for PCM replay. Chrome's extension has a fixed localhost port.
    text = text.replace("port = 18765", "port = 18769")
    config_path = output / "profiles.toml"
    config_path.write_text(text)
    config = load_settings(config_path)
    assets = json.loads((run / "asset-manifest.json").read_text())["assets"]
    os.environ.update(P2_B0_RUN=str(run), P2_B0_SEED=str(seed), SUBTITLE_TOKEN_FILE=str(output / "private.token"))
    normalized = EventLog(output / "events.jsonl", run.name, repeat_id=f"seed{seed}")
    cases = []

    async def one(asset, token):
        audio = Path(asset["path"]).read_bytes()
        trace = []
        async with connect(f"ws://127.0.0.1:{config.server.port}/v1/captions",
                           origin=config.server.allowed_origin, proxy=None) as ws:
            await ws.send(json.dumps(dict(type="start", version=1, profile_id="reazon-hy", audio=AUDIO, token=token)))
            ready = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if ready.get("type") != "ready":
                raise ValueError("baseline gateway did not become ready: " + str(ready.get("code")))
            sid = ready["session_id"]
            anchor = time.monotonic_ns()
            late = []
            trace.append(dict(event=ready, t_ns=anchor))
            mark(f"replay-seed{seed}/{asset['id']}", "start")

            async def send():
                for end, frame in pcm_packets(audio):
                    due = anchor + round(end / 16000 * 1e9)
                    await asyncio.sleep(max(0, (due - time.monotonic_ns()) / 1e9))
                    await ws.send(frame)
                    now = time.monotonic_ns()
                    late.append((now - due) / 1e6)
                    normalized.record("pcm_accepted", session_id=sid, clip_id=asset["id"],
                                      accepted_samples=end, packet_samples=len(frame)//2,
                                      pcm_sha256=__import__("hashlib").sha256(frame).hexdigest(),
                                      observation="client_sent; server acceptance separately joined")
                await ws.send('{"type":"finish"}')

            task = asyncio.create_task(send())
            captions, warnings, finished = [], [], None
            try:
                async with asyncio.timeout(len(audio) / 32000 + 12):
                    async for raw in ws:
                        event = json.loads(raw)
                        now = time.monotonic_ns()
                        trace.append(dict(event=event, t_ns=now))
                        if event.get("session_id") != sid:
                            raise ValueError("cross-session gateway output")
                        if event["type"] == "caption":
                            captions.append(event | dict(t_ns=now))
                            normalized.record("caption_emit", session_id=sid, clip_id=asset["id"],
                                              source_id=f"{sid}/caption/{event['seq']}",
                                              **{k: event[k] for k in ("seq", "start_sample", "end_sample", "text_zh")})
                        elif event["type"] == "warning":
                            warnings.append(event["code"])
                        elif event["type"] == "finished":
                            finished = event
                            normalized.record("finished", session_id=sid, clip_id=asset["id"])
                            break
                        elif event["type"] == "error":
                            raise ValueError("gateway error: " + event["code"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                mark(f"replay-seed{seed}/{asset['id']}", "end")
            with (output / f"{asset['id']}.jsonl").open("x") as target:
                for event in trace:
                    target.write(json.dumps(event, ensure_ascii=False) + "\n")
            if not finished or finished["accepted_samples"] != asset["samples"]:
                raise ValueError("PCM sample loss or missing EOF")
            return dict(clip=asset["id"], session_id=sid, anchor_ns=anchor, accepted_samples=asset["samples"],
                        audio_s=asset["samples"]/16000, captions=len(captions), warnings=warnings,
                        first_any_zh_s=(captions[0]["t_ns"]-anchor)/1e9 if captions else None,
                        max_send_late_ms=max(late), finished=True)

    try:
        with launched(config_path, config, output, True,
                      server_command=[sys.executable, "-m", "p0.p2.server"]):
            token = load_token(output / "private.token", create=False)
            for asset in assets:
                result = asyncio.run(one(asset, token))
                cases.append(result)
                print(json.dumps({"clip": result["clip"], "finished": result["finished"], "captions": result["captions"]}), flush=True)
    finally:
        normalized.close()
    causal = validate_causality(rows(output / "events.jsonl"))
    result = dict(seed=seed, repeat=repeat, cases=cases, mode="realtime-1x", causal_errors=causal,
                  completed=all(c["finished"] for c in cases), scope="gateway WS; not Chrome DOM")
    dump(output / "run.json", result)
    if causal:
        raise ValueError("replay event causality failed: " + str(causal[:2]))
    return dict(completed=True, clips=len(cases), seed=seed, output=str(output))
