"""Stage 0 + Stage 1 hardware verification: NeMo ±pad parity, KV invariance + kana mask, and live 1x E2E."""

from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import statistics
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/perf_probe"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_mt"))

import numpy as np  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

from dataset import CACHE_ROOT, D1_PCM, QWEN3_MT_DIR, SILENCE_PCM, VAD_PATH, load_frozen_slices, percentile, score_asr_rows  # noqa: E402
from subtitle_service.app import AUDIO, FRAME_BYTES, KANA, chinese  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import AsrConfig, load_settings, load_token  # noqa: E402
from subtitle_service.local_pipeline import Qwen3Translator, SpeechCppNeMoRecognizer  # noqa: E402
from subtitle_service.replay import BYTES_PER_SECOND, replay  # noqa: E402
from verify_m1_pro_hq_live import ResourceSampler, analyze_latencies, find_server_pid, launched_server  # noqa: E402

OUT_DIR = REPO_ROOT / "p0/evidence/stage0-1-20261123"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage0-1-2026-11-23"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"


def verify_asr_and_mt() -> tuple[dict, dict]:
    _, _, all_slices, d0_units, d1_ref_text, silence_audio = load_frozen_slices()

    asr_cfg = AsrConfig(
        type="speech_cpp_nemo",
        model_file="reazonspeech-nemo-619M-v2-Q8_0.gguf",
        threads=6,
        decoding="beam",
        vad_pause_ms=600,
        max_segment_s=4.5,
        overlap_ms=350,
        pad_ms=350,
    )
    rec = SpeechCppNeMoRecognizer(
        CACHE_ROOT / "speech-cpp/libspeech.dylib",
        CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
        VAD_PATH,
        asr_cfg,
    )
    silence_out = rec.decode(silence_audio)
    rows, asr_ms = [], []
    for s in all_slices:
        t0 = time.perf_counter()
        ja = rec.decode(s["audio"])
        asr_ms.append((time.perf_counter() - t0) * 1000.0)
        rows.append(dict(s, text_ja=ja))
    rec.close()
    scores = score_asr_rows(rows, d0_units, d1_ref_text)
    asr_summary = {
        "pad_ms": 350,
        "threads": 6,
        "p50_ms": round(statistics.median(asr_ms), 2),
        "p95_ms": round(percentile([x / 1000.0 for x in asr_ms], 0.95) * 1000.0, 2),
        "silence_out": silence_out,
        "d0_overall_cer": scores["d0_cer"]["overall_cer"],
        "d0_by_clip": {k: v["cer"] for k, v in scores["d0_cer"]["by_clip"].items()},
        "d1_cer": scores["d1_cer"]["cer"],
    }

    tr = Qwen3Translator(QWEN3_MT_DIR)
    sess = tr.new_session()
    ja_inputs = [r["text_ja"] for r in rows]

    # Pass 1 & Pass 2 (A/B/A) + cache offset invariance
    pass1, pass2, mt_ms = [], [], []
    offsets_ok = True
    for ja in ja_inputs:
        t0 = time.perf_counter()
        zh = tr.translate(sess, ja)
        mt_ms.append((time.perf_counter() - t0) * 1000.0)
        pass1.append(zh)
        if tr._prompt_cache[0].offset != tr._prefix_len:
            offsets_ok = False
    for ja in ja_inputs:
        zh = tr.translate(sess, ja)
        pass2.append(zh)
        if tr._prompt_cache[0].offset != tr._prefix_len:
            offsets_ok = False

    # Shuffled order invariance
    idx_order = list(range(len(ja_inputs)))
    random.Random(42).shuffle(idx_order)
    shuffled_match = 0
    for idx in idx_order[:20]:
        zh = tr.translate(sess, ja_inputs[idx])
        if zh == pass1[idx]:
            shuffled_match += 1
        if tr._prompt_cache[0].offset != tr._prefix_len:
            offsets_ok = False

    # Cancel mid-decode KV invariance
    ev = threading.Event()
    timer = threading.Timer(0.05, ev.set)
    timer.start()
    _ = tr.translate(sess, ja_inputs[-1], cancel_event=ev)
    timer.cancel()
    cancel_offset_ok = tr._prompt_cache[0].offset == tr._prefix_len

    # Simulated exception KV invariance
    orig_step = tr._generate_step
    def _boom(*args, **kwargs):
        raise RuntimeError("injected")
    tr._generate_step = _boom
    exc_caught = False
    try:
        tr.translate(sess, ja_inputs[0])
    except RuntimeError:
        exc_caught = True
    finally:
        tr._generate_step = orig_step
    exc_offset_ok = exc_caught and (tr._prompt_cache[0].offset == tr._prefix_len)
    # Verify post-exception translation still matches pass1[0]
    post_exc_match = tr.translate(sess, ja_inputs[0]) == pass1[0]

    kana_leaks = sum(1 for zh in pass1 if zh and KANA.search(zh))
    gateway_ok = sum(1 for zh in pass1 if zh and chinese(zh) is not None)
    tricky = {
        "hou_de_arimasu": tr.translate(sess, "ほうであります。"),
        "d1_seg1": pass1[88],
        "d1_seg2": pass1[89],
        "d1_seg3": pass1[90],
    }

    mt_summary = {
        "kana_token_count": len(tr._kana_token_ids),
        "p50_ms": round(statistics.median(mt_ms), 2),
        "p95_ms": round(percentile([x / 1000.0 for x in mt_ms], 0.95) * 1000.0, 2),
        "pass1_pass2_exact": sum(1 for a, b in zip(pass1, pass2) if a == b),
        "total_slices": len(ja_inputs),
        "shuffled_20_exact": shuffled_match,
        "offsets_invariant_all_passes": offsets_ok,
        "cancel_offset_invariant": cancel_offset_ok,
        "exception_offset_invariant": exc_offset_ok,
        "post_exception_exact": post_exc_match,
        "kana_leaks": kana_leaks,
        "gateway_pass": gateway_ok,
        "tricky_samples": tricky,
    }
    return asr_summary, mt_summary


async def active_mid_inference_seek(config, profile_id: str, token: str, audio_csj: bytes, out_dir: Path) -> dict:
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
    t_cancel = time.monotonic()
    await asyncio.sleep(0.05)
    with (out_dir / "reopen.jsonl").open("w", buffering=1) as trace:
        reopen = await replay(config, profile_id, token, "reopen-stage1", audio_csj, trace)
    return {"gap_ms": 50, "reopen_wall_s": round(time.monotonic() - t_cancel, 3), "reopen": reopen}


def verify_live_ws() -> dict:
    config = load_settings(CFG_PATH)
    suite = {x["id"]: x for x in json.loads(SUITE_PATH.read_text())}
    d1_audio = D1_PCM.read_bytes()
    with launched_server(CFG_PATH, config, OUT_DIR):
        pid = find_server_pid(config.server.port)
        assert pid is not None
        token = load_token(token_path(), create=False)
        results = {}
        with ResourceSampler(pid) as sampler:
            for cid in ["csj-reproduction", "csj-task", "wmf-04", "silence"]:
                audio = (SUITE_PATH.parent / suite[cid]["file"]).read_bytes()
                tp = OUT_DIR / f"{cid}.jsonl"
                with tp.open("w", buffering=1) as trace:
                    res = asyncio.run(replay(config, "m1-pro-nemo", token, cid, audio, trace))
                sid = json.loads(tp.read_text().splitlines()[0])["event"]["session_id"]
                results[cid] = res | analyze_latencies(tp, OUT_DIR / "model.jsonl", sid)

            d1_tp = OUT_DIR / "d1.jsonl"
            with d1_tp.open("w", buffering=1) as trace:
                d1_res = asyncio.run(replay(config, "m1-pro-nemo", token, "d1-ep01-45s", d1_audio, trace))
            d1_sid = json.loads(d1_tp.read_text().splitlines()[0])["event"]["session_id"]
            results["d1-ep01-45s"] = d1_res | analyze_latencies(d1_tp, OUT_DIR / "model.jsonl", d1_sid)

            csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
            seek_res = asyncio.run(active_mid_inference_seek(config, "m1-pro-nemo", token, csj_audio, OUT_DIR))

        cut_reasons: dict[str, int] = {}
        for ln in (OUT_DIR / "model.jsonl").read_text().splitlines():
            if not ln.strip():
                continue
            ev = json.loads(ln)
            if ev.get("stage") == "vad_released":
                cr = ev.get("cut_reason", "unknown")
                cut_reasons[cr] = cut_reasons.get(cr, 0) + 1

        return {
            "results": results,
            "seek_50ms": seek_res,
            "cut_reasons": cut_reasons,
            "resources": {
                "peak_rss_mib": max(sampler.rss_mib) if sampler.rss_mib else 0.0,
                "mean_cpu_pct": round(sum(sampler.cpu_pct) / max(1, len(sampler.cpu_pct)), 1),
                "peak_cpu_pct": max(sampler.cpu_pct) if sampler.cpu_pct else 0.0,
            },
        }


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    asr_summary, mt_summary = verify_asr_and_mt()
    print("ASR_MT_VERIFIED:", json.dumps({"asr": asr_summary, "mt": mt_summary}, ensure_ascii=False), flush=True)
    live_summary = verify_live_ws()
    out = {"asr_parity": asr_summary, "mt_invariance_and_mask": mt_summary, "live_ws": live_summary}
    (ARCHIVE_DIR / "stage0_1_verification.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("STAGE0_1_DONE", flush=True)


if __name__ == "__main__":
    main()
