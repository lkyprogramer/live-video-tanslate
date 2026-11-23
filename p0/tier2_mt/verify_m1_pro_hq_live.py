"""Live 1x realtime end-to-end verification for m1-pro-hq on subtitle-server."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from websockets.asyncio.client import connect

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))

from contextlib import contextmanager
import socket
import urllib.error
import urllib.request

from subtitle_service.app import AUDIO, FRAME_BYTES
from subtitle_service.cli import token_path
from subtitle_service.config import load_settings, load_token
from subtitle_service.replay import BYTES_PER_SECOND, replay


@contextmanager
def launched_server(config_path: Path, config, output: Path):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", config.server.port)) == 0:
            raise RuntimeError("service port already in use")
    env = os.environ.copy()
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
                    with opener.open(f"http://127.0.0.1:{config.server.port}/v1/health", timeout=5) as response:
                        if json.load(response)["status"] == "ready":
                            break
                except urllib.error.HTTPError as exc:
                    try:
                        if json.load(exc).get("status") == "unavailable":
                            raise RuntimeError("default model warmup failed") from None
                    except (json.JSONDecodeError, TimeoutError):
                        pass
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(0.25)
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

OUT_DIR = REPO_ROOT / "p0/evidence/m1-pro-hq-live-20261123"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"
D1_PCM = REPO_ROOT / "p0/evidence/d1-dialogue/first45s-control.pcm"


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))], 3)


def find_server_pid(port: int) -> int | None:
    out = subprocess.check_output(["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"], text=True).strip()
    return int(out.splitlines()[0]) if out else None


class ResourceSampler:
    def __init__(self, pid: int):
        self.pid = pid
        self.stop = threading.Event()
        self.rss_mib: list[float] = []
        self.cpu_pct: list[float] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            try:
                out = subprocess.check_output(["ps", "-p", str(self.pid), "-o", "rss=,%cpu="], text=True).strip()
                if out:
                    parts = out.split()
                    self.rss_mib.append(round(int(parts[0]) / 1024.0, 1))
                    self.cpu_pct.append(float(parts[1]))
            except Exception:
                pass
            self.stop.wait(0.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=2)


async def cancel_busy_then_reopen(config, token: str, audio_csj: bytes, out_dir: Path) -> dict:
    """Stream 8s of csj-reproduction, abruptly close WS while busy, then immediately replay full clip."""
    ws_url = f"ws://127.0.0.1:{config.server.port}/v1/captions"
    async with connect(ws_url, origin=config.server.allowed_origin, proxy=None) as ws:
        await ws.send(json.dumps({"type": "start", "version": 1, "profile_id": "m1-pro-hq", "audio": AUDIO, "token": token}))
        ready = json.loads(await asyncio.wait_for(ws.recv(), 25))
        assert ready["type"] == "ready"
        anchor = time.monotonic()
        # Send 8s at 1x speed
        limit = min(len(audio_csj), 8 * BYTES_PER_SECOND)
        for off in range(0, limit, FRAME_BYTES):
            end = min(off + FRAME_BYTES, limit)
            await asyncio.sleep(max(0.0, anchor + end / BYTES_PER_SECOND - time.monotonic()))
            await ws.send(audio_csj[off:end])
        # Abruptly close socket without finish!

    await asyncio.sleep(0.2)
    with (out_dir / "reopen_csj-reproduction.jsonl").open("w", buffering=1) as trace:
        reopen_res = await replay(config, "m1-pro-hq", token, "reopen-csj", audio_csj, trace)
    return {"abrupt_cancel_sent_s": 8.0, "reopen": reopen_res}


def analyze_latencies(trace_path: Path, model_jsonl: Path, session_id: str) -> dict:
    events = [json.loads(l) for l in trace_path.read_text().splitlines() if l.strip()]
    ws_sent = [e for e in events if e.get("event", {}).get("type") == "caption"]
    m_rows = [json.loads(l) for l in model_jsonl.read_text().splitlines() if l.strip() and json.loads(l).get("session_id") == session_id]

    asr_durs, mt_durs, e2e_s = [], [], []
    asr_t0, mt_t0 = {}, {}
    for m in m_rows:
        st, sid = m.get("stage"), m.get("segment_id")
        if st == "asr_start":
            asr_t0[sid] = m["t"]
        elif st == "asr_end" and sid in asr_t0:
            asr_durs.append(m["t"] - asr_t0[sid])
        elif st == "mt_start":
            mt_t0[sid] = m["t"]
        elif st == "mt_end" and sid in mt_t0:
            mt_durs.append(m["t"] - mt_t0[sid])

    # End-to-end latency from audio end_sample media time to ws caption arrival (relative to session ready anchor)
    ready_t = next((m["t"] for m in m_rows if m.get("stage") == "ready"), None)
    ws_sent_m = [m for m in m_rows if m.get("stage") == "ws_sent"]
    if ready_t is not None and ws_sent_m:
        for m in ws_sent_m:
            e2e_s.append(round((m["t"] - ready_t) - (m["end_sample"] / 16000.0), 3))

    return {
        "captions": len(ws_sent),
        "asr_p50_s": percentile(asr_durs, 0.50),
        "asr_p95_s": percentile(asr_durs, 0.95),
        "mt_p50_s": percentile(mt_durs, 0.50),
        "mt_p95_s": percentile(mt_durs, 0.95),
        "e2e_from_seg_end_p50_s": percentile(e2e_s, 0.50),
        "e2e_from_seg_end_p95_s": percentile(e2e_s, 0.95),
        "texts": [e["event"]["text_zh"] for e in ws_sent],
    }


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    config = load_settings(CFG_PATH)
    suite = {x["id"]: x for x in json.loads(SUITE_PATH.read_text())}
    d0_ids = ["csj-reproduction", "csj-task", "wmf-04", "silence"]

    with launched_server(CFG_PATH, config, OUT_DIR):
        pid = find_server_pid(config.server.port)
        assert pid is not None
        token = load_token(token_path(), create=False)

        with ResourceSampler(pid) as sampler:
            # 1. D0 3 clips + silence on m1-pro-hq
            d0_results = []
            for cid in d0_ids:
                audio = (SUITE_PATH.parent / suite[cid]["file"]).read_bytes()
                with (OUT_DIR / f"hq_{cid}.jsonl").open("w", buffering=1) as trace:
                    res = asyncio.run(replay(config, "m1-pro-hq", token, cid, audio, trace))
                sid = json.loads((OUT_DIR / f"hq_{cid}.jsonl").read_text().splitlines()[0])["event"]["session_id"]
                lat = analyze_latencies(OUT_DIR / f"hq_{cid}.jsonl", OUT_DIR / "model.jsonl", sid)
                d0_results.append(res | lat)
                print(json.dumps({"d0": cid, **{k: v for k, v in (res | lat).items() if k != "texts"}}, ensure_ascii=False), flush=True)

            # 2. D1 45s Brush Up Life on m1-pro-hq
            d1_audio = D1_PCM.read_bytes()
            with (OUT_DIR / "hq_d1.jsonl").open("w", buffering=1) as trace:
                d1_res = asyncio.run(replay(config, "m1-pro-hq", token, "d1-ep01-45s", d1_audio, trace))
            d1_sid = json.loads((OUT_DIR / "hq_d1.jsonl").read_text().splitlines()[0])["event"]["session_id"]
            d1_lat = analyze_latencies(OUT_DIR / "hq_d1.jsonl", OUT_DIR / "model.jsonl", d1_sid)
            d1_full = d1_res | d1_lat
            print(json.dumps({"d1": {k: v for k, v in d1_full.items() if k != "texts"}}, ensure_ascii=False), flush=True)

            # 3. Cancel-busy-reopen lifecycle on m1-pro-hq
            csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
            lifecycle = asyncio.run(cancel_busy_then_reopen(config, token, csj_audio, OUT_DIR))
            print(json.dumps({"lifecycle": lifecycle}, ensure_ascii=False), flush=True)

            # 4. Default reazon-hy non-regression check on csj-reproduction
            with (OUT_DIR / "base_csj.jsonl").open("w", buffering=1) as trace:
                base_res = asyncio.run(replay(config, "reazon-hy", token, "csj-reproduction", csj_audio, trace))
            print(json.dumps({"reazon_hy_non_regression": base_res}, ensure_ascii=False), flush=True)

        summary = {
            "d0": d0_results,
            "d1": d1_full,
            "lifecycle": lifecycle,
            "reazon_hy_non_regression": base_res,
            "resources": {
                "samples": len(sampler.rss_mib),
                "peak_rss_mib": max(sampler.rss_mib) if sampler.rss_mib else 0.0,
                "mean_cpu_pct": round(sum(sampler.cpu_pct) / max(1, len(sampler.cpu_pct)), 1),
                "peak_cpu_pct": max(sampler.cpu_pct) if sampler.cpu_pct else 0.0,
            },
        }
        (OUT_DIR / "live_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("LIVE_SUMMARY_WRITTEN", flush=True)


if __name__ == "__main__":
    main()
