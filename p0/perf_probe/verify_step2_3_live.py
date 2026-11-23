"""Live 1x realtime end-to-end verification comparing m1-pro-hq (v2) vs m1-pro-nemo."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from websockets.asyncio.client import connect

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_mt"))

from subtitle_service.app import AUDIO, FRAME_BYTES  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import load_settings, load_token  # noqa: E402
from subtitle_service.replay import BYTES_PER_SECOND, replay  # noqa: E402
from verify_m1_pro_hq_live import (  # noqa: E402
    ResourceSampler,
    analyze_latencies,
    find_server_pid,
    launched_server,
    percentile,
)

OUT_DIR = REPO_ROOT / "p0/evidence/step2-3-live-20261123"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/step2-3-e2e-2026-11-23"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"
D1_PCM = REPO_ROOT / "p0/evidence/d1-dialogue/first45s-control.pcm"


async def aggressive_seek_cancel_reopen(config, profile_id: str, token: str, audio_csj: bytes, out_dir: Path) -> dict:
    """Stream 3.5s at 1x, abruptly drop WS mid-inference, wait 50ms, immediately reopen & replay."""
    ws_url = f"ws://127.0.0.1:{config.server.port}/v1/captions"
    async with connect(ws_url, origin=config.server.allowed_origin, proxy=None) as ws:
        await ws.send(json.dumps({"type": "start", "version": 1, "profile_id": profile_id, "audio": AUDIO, "token": token}))
        ready = json.loads(await asyncio.wait_for(ws.recv(), 25))
        assert ready["type"] == "ready"
        anchor = time.monotonic()
        limit = min(len(audio_csj), int(5.0 * BYTES_PER_SECOND))
        for off in range(0, limit, FRAME_BYTES):
            end = min(off + FRAME_BYTES, limit)
            await asyncio.sleep(max(0.0, anchor + end / BYTES_PER_SECOND - time.monotonic()))
            await ws.send(audio_csj[off:end])

    # Only 50ms gap (much tighter than previous 200ms!)
    await asyncio.sleep(0.05)
    t_reopen0 = time.monotonic()
    with (out_dir / f"{profile_id}_reopen.jsonl").open("w", buffering=1) as trace:
        reopen_res = await replay(config, profile_id, token, f"reopen-{profile_id}", audio_csj, trace)
    return {
        "gap_ms": 50,
        "reopen_total_s": round(time.monotonic() - t_reopen0, 3),
        "reopen": reopen_res,
    }


def run_profile_suite(config, profile_id: str, token: str, suite: dict, d1_audio: bytes) -> dict:
    d0_ids = ["csj-reproduction", "csj-task", "wmf-04", "silence"]
    d0_results = []
    for cid in d0_ids:
        audio = (SUITE_PATH.parent / suite[cid]["file"]).read_bytes()
        trace_path = OUT_DIR / f"{profile_id}_{cid}.jsonl"
        with trace_path.open("w", buffering=1) as trace:
            res = asyncio.run(replay(config, profile_id, token, cid, audio, trace))
        sid = json.loads(trace_path.read_text().splitlines()[0])["event"]["session_id"]
        lat = analyze_latencies(trace_path, OUT_DIR / "model.jsonl", sid)
        d0_results.append(res | lat)
        print(json.dumps({"profile": profile_id, "d0": cid, "captions": lat["captions"], "warnings": res["warnings"], "p50_s": lat["e2e_from_seg_end_p50_s"], "p95_s": lat["e2e_from_seg_end_p95_s"]}, ensure_ascii=False), flush=True)

    d1_trace = OUT_DIR / f"{profile_id}_d1.jsonl"
    with d1_trace.open("w", buffering=1) as trace:
        d1_res = asyncio.run(replay(config, profile_id, token, "d1-ep01-45s", d1_audio, trace))
    d1_sid = json.loads(d1_trace.read_text().splitlines()[0])["event"]["session_id"]
    d1_lat = analyze_latencies(d1_trace, OUT_DIR / "model.jsonl", d1_sid)
    d1_full = d1_res | d1_lat
    print(json.dumps({"profile": profile_id, "d1": {"captions": d1_lat["captions"], "warnings": d1_res["warnings"], "p50_s": d1_lat["e2e_from_seg_end_p50_s"], "p95_s": d1_lat["e2e_from_seg_end_p95_s"], "drain_s": round(d1_res["drain_seconds"], 3)}}, ensure_ascii=False), flush=True)

    csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
    lifecycle = asyncio.run(aggressive_seek_cancel_reopen(config, profile_id, token, csj_audio, OUT_DIR))
    print(json.dumps({"profile": profile_id, "lifecycle": lifecycle}, ensure_ascii=False), flush=True)

    return {"d0": d0_results, "d1": d1_full, "lifecycle": lifecycle}


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    config = load_settings(CFG_PATH)
    suite = {x["id"]: x for x in json.loads(SUITE_PATH.read_text())}
    d1_audio = D1_PCM.read_bytes()

    with launched_server(CFG_PATH, config, OUT_DIR):
        pid = find_server_pid(config.server.port)
        assert pid is not None
        token = load_token(token_path(), create=False)

        with ResourceSampler(pid) as sampler:
            hq_res = run_profile_suite(config, "m1-pro-hq", token, suite, d1_audio)
            nemo_res = run_profile_suite(config, "m1-pro-nemo", token, suite, d1_audio)

            csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
            with (OUT_DIR / "base_csj.jsonl").open("w", buffering=1) as trace:
                base_res = asyncio.run(replay(config, "reazon-hy", token, "csj-reproduction", csj_audio, trace))

        summary = {
            "m1_pro_hq": hq_res,
            "m1_pro_nemo": nemo_res,
            "reazon_hy_non_regression": base_res,
            "resources": {
                "samples": len(sampler.rss_mib),
                "peak_rss_mib": max(sampler.rss_mib) if sampler.rss_mib else 0.0,
                "mean_cpu_pct": round(sum(sampler.cpu_pct) / max(1, len(sampler.cpu_pct)), 1),
                "peak_cpu_pct": max(sampler.cpu_pct) if sampler.cpu_pct else 0.0,
            },
        }
        (ARCHIVE_DIR / "live_comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("STEP2_3_LIVE_DONE", flush=True)


if __name__ == "__main__":
    main()
