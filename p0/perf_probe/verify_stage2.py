"""Stage 2 hardware verification: rolling KV context, 64ms/350ms live E2E + caption_delta, and kernel hotspot probes."""

from __future__ import annotations

import asyncio
import ctypes
import json
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

from dataset import CACHE_ROOT, D1_PCM, QWEN3_MT_DIR, VAD_PATH, load_frozen_slices, percentile  # noqa: E402
from subtitle_service.app import AUDIO  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import AsrConfig, load_settings, load_token  # noqa: E402
from subtitle_service.local_pipeline import Qwen3Translator, SpeechCppNeMoRecognizer  # noqa: E402
from subtitle_service.replay import BYTES_PER_SECOND, replay  # noqa: E402
from verify_m1_pro_hq_live import ResourceSampler, find_server_pid, launched_server  # noqa: E402

OUT_DIR = REPO_ROOT / "p0/evidence/stage2-20261123"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage2-2026-11-23"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"
FRAME_64MS_BYTES = 2048


def analyze_stage2_latencies(trace_path: Path, model_jsonl: Path, session_id: str) -> dict:
    events = [json.loads(ln) for ln in trace_path.read_text().splitlines() if ln.strip()]
    ws_captions = [e for e in events if e.get("event", {}).get("type") == "caption"]
    ws_deltas = [e for e in events if e.get("event", {}).get("type") == "caption_delta"]
    m_rows = [
        json.loads(ln) for ln in model_jsonl.read_text().splitlines()
        if ln.strip() and json.loads(ln).get("session_id") == session_id
    ]
    ready_t = next((m["t"] for m in m_rows if m.get("stage") == "ready"), None)
    asr_durs, mt_durs, final_e2e, first_delta_e2e = [], [], [], []
    asr_t0, mt_t0 = {}, {}
    first_delta_by_seg: dict[int, float] = {}
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
        elif st == "ws_delta_sent" and sid is not None and sid not in first_delta_by_seg:
            first_delta_by_seg[sid] = m["t"]
            if ready_t is not None:
                first_delta_e2e.append(round((m["t"] - ready_t) - (m["end_sample"] / 16000.0), 3))
        elif st == "ws_sent" and ready_t is not None:
            final_e2e.append(round((m["t"] - ready_t) - (m["end_sample"] / 16000.0), 3))

    return {
        "captions": len(ws_captions),
        "deltas": len(ws_deltas),
        "asr_p50_s": percentile(asr_durs, 0.50),
        "asr_p95_s": percentile(asr_durs, 0.95),
        "mt_p50_s": percentile(mt_durs, 0.50),
        "mt_p95_s": percentile(mt_durs, 0.95),
        "first_delta_e2e_p50_s": percentile(first_delta_e2e, 0.50),
        "first_delta_e2e_p95_s": percentile(first_delta_e2e, 0.95),
        "final_e2e_p50_s": percentile(final_e2e, 0.50),
        "final_e2e_p95_s": percentile(final_e2e, 0.95),
        "texts": [e["event"]["text_zh"] for e in ws_captions],
    }


def verify_rolling_kv_and_shapes() -> tuple[dict, dict]:
    _, _, all_slices, _, _, _ = load_frozen_slices()
    asr_cfg = AsrConfig(
        type="speech_cpp_nemo",
        model_file="reazonspeech-nemo-619M-v2-Q8_0.gguf",
        threads=6,
        decoding="beam",
        pad_ms=350,
    )
    rec = SpeechCppNeMoRecognizer(
        CACHE_ROOT / "speech-cpp/libspeech.dylib",
        CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
        VAD_PATH,
        asr_cfg,
    )
    ja_slices = []
    for s in all_slices:
        ja_slices.append((s["clip"], s["start_sample"], s["end_sample"], rec.decode(s["audio"])))
    rec.close()

    tr = Qwen3Translator(QWEN3_MT_DIR, context_turns=2)
    sess_0 = lp_noop = tr.new_session()
    sess_0.max_turns = 0

    no_ctx_ms, no_ctx_texts = [], []
    for _, _, _, ja in ja_slices:
        t0 = time.perf_counter()
        no_ctx_texts.append(tr.translate(sess_0, ja))
        no_ctx_ms.append((time.perf_counter() - t0) * 1000.0)

    sess_2 = tr.new_session()
    ctx_ms, ctx_texts = [], []
    suffix_lens = []
    offsets_ok = True
    for clip, st, en, ja in ja_slices:
        # Track approximate suffix length fed in turn
        turn_prefix = tr._next_turn_prefix if sess_2.turns > 0 else ""
        suffix_lens.append(len(tr.tokenizer.encode(turn_prefix + ja + tr._suffix_tail, add_special_tokens=False)))
        t0 = time.perf_counter()
        zh = tr.translate(sess_2, ja, start_sample=st, end_sample=en)
        ctx_ms.append((time.perf_counter() - t0) * 1000.0)
        ctx_texts.append(zh)
        if sess_2.turns == 0 and tr._prompt_cache[0].offset != tr._prefix_len:
            offsets_ok = False
    sess_2.close()
    close_ok = tr._prompt_cache[0].offset == tr._prefix_len

    # Verify cancel resets KV
    ev = threading.Event()
    t_can = threading.Timer(0.05, ev.set)
    t_can.start()
    _ = tr.translate(sess_2, ja_slices[-1][3], cancel_event=ev)
    t_can.cancel()
    cancel_ok = tr._prompt_cache[0].offset == tr._prefix_len

    in_3_16 = sum(1 for x in suffix_lens if 3 <= x <= 16)
    in_17_32 = sum(1 for x in suffix_lens if 17 <= x <= 32)
    gt_32 = sum(1 for x in suffix_lens if x > 32)

    kv_summary = {
        "no_ctx_p50_ms": round(statistics.median(no_ctx_ms), 2),
        "rolling_2turn_p50_ms": round(statistics.median(ctx_ms), 2),
        "offsets_invariant": offsets_ok,
        "close_reset_invariant": close_ok,
        "cancel_reset_invariant": cancel_ok,
        "d1_no_ctx": no_ctx_texts[88:91],
        "d1_rolling_2turn": ctx_texts[88:91],
        "hou_no_ctx": tr.translate(sess_0, "ほうであります。"),
        "hou_after_prev": (
            tr.translate(sess_2, "そういう情報であります", start_sample=0, end_sample=32000),
            tr.translate(sess_2, "ほうであります。", start_sample=32000, end_sample=40000),
        ),
    }
    sess_2.close()
    shape_hist = {
        "total": len(suffix_lens),
        "in_3_16": in_3_16,
        "in_17_32": in_17_32,
        "gt_32": gt_32,
        "pct_in_3_16": round(100.0 * in_3_16 / max(1, len(suffix_lens)), 1),
    }
    return kv_summary, shape_hist


def probe_speech_cpp_thread_scaling() -> list[dict]:
    _, _, all_slices, _, _, _ = load_frozen_slices()
    probe_slices = [all_slices[i]["audio"] for i in range(0, 90, 6)]
    lib = ctypes.CDLL(str(CACHE_ROOT / "speech-cpp/libspeech.dylib"))
    lib.speech_log_set.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.speech_load_params_new.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.speech_load_params_free.argtypes = [ctypes.c_void_p]
    lib.speech_load_params_set_device.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.speech_load_params_set_threads.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.speech_load_params_set_warmup.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.speech_model_load.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.speech_model_free.argtypes = [ctypes.c_void_p]
    lib.speech_request_new.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.speech_request_free.argtypes = [ctypes.c_void_p]
    lib.speech_request_set_audio.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_int]
    lib.speech_request_set_string.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
    lib.speech_transcribe.argtypes = [ctypes.c_void_p]
    lib.speech_log_set(None, None)

    pad = np.zeros(int(0.35 * 16000), dtype=np.float32)
    rows = []
    for th in (1, 2, 4, 6, 8):
        lp = ctypes.c_void_p()
        lib.speech_load_params_new(ctypes.byref(lp))
        lib.speech_load_params_set_device(lp, b"cpu")
        lib.speech_load_params_set_threads(lp, th)
        lib.speech_load_params_set_warmup(lp, 1)
        m = ctypes.c_void_p()
        lib.speech_model_load(str(CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf").encode("utf-8"), lp, ctypes.byref(m))
        lib.speech_load_params_free(lp)
        entry = {"threads": th}
        for dec in (b"greedy", b"beam"):
            ts = []
            for a0 in probe_slices:
                a = np.ascontiguousarray(np.concatenate([pad, a0, pad]), dtype=np.float32)
                req = ctypes.c_void_p()
                lib.speech_request_new(m, ctypes.byref(req))
                lib.speech_request_set_audio(req, a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), a.size, 16000)
                lib.speech_request_set_string(req, 10, dec)
                t0 = time.perf_counter()
                lib.speech_transcribe(req)
                ts.append((time.perf_counter() - t0) * 1000.0)
                lib.speech_request_free(req)
            entry[f"{dec.decode()}_p50_ms"] = round(statistics.median(ts), 1)
        entry["beam_overhead_ms"] = round(entry["beam_p50_ms"] - entry["greedy_p50_ms"], 1)
        rows.append(entry)
        lib.speech_model_free(m)
    return rows


async def active_seek_64ms(config, profile_id: str, token: str, audio_csj: bytes, out_dir: Path) -> dict:
    ws_url = f"ws://127.0.0.1:{config.server.port}/v1/captions"
    async with connect(ws_url, origin=config.server.allowed_origin, proxy=None) as ws:
        await ws.send(json.dumps({"type": "start", "version": 1, "profile_id": profile_id, "audio": AUDIO, "token": token}))
        ready = json.loads(await asyncio.wait_for(ws.recv(), 25))
        assert ready["type"] == "ready"
        anchor = time.monotonic()
        limit = min(len(audio_csj), int(5.0 * BYTES_PER_SECOND))
        for off in range(0, limit, FRAME_64MS_BYTES):
            end = min(off + FRAME_64MS_BYTES, limit)
            await asyncio.sleep(max(0.0, anchor + end / BYTES_PER_SECOND - time.monotonic()))
            await ws.send(audio_csj[off:end])
    t_cancel = time.monotonic()
    await asyncio.sleep(0.05)
    with (out_dir / "reopen.jsonl").open("w", buffering=1) as trace:
        reopen = await replay(config, profile_id, token, "reopen-stage2", audio_csj, trace, frame_bytes=FRAME_64MS_BYTES)
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
                    res = asyncio.run(replay(config, "m1-pro-nemo", token, cid, audio, trace, frame_bytes=FRAME_64MS_BYTES))
                sid = json.loads(tp.read_text().splitlines()[0])["event"]["session_id"]
                results[cid] = res | analyze_stage2_latencies(tp, OUT_DIR / "model.jsonl", sid)

            d1_tp = OUT_DIR / "d1.jsonl"
            with d1_tp.open("w", buffering=1) as trace:
                d1_res = asyncio.run(replay(config, "m1-pro-nemo", token, "d1-ep01-45s", d1_audio, trace, frame_bytes=FRAME_64MS_BYTES))
            d1_sid = json.loads(d1_tp.read_text().splitlines()[0])["event"]["session_id"]
            results["d1-ep01-45s"] = d1_res | analyze_stage2_latencies(d1_tp, OUT_DIR / "model.jsonl", d1_sid)

            csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
            seek_res = asyncio.run(active_seek_64ms(config, "m1-pro-nemo", token, csj_audio, OUT_DIR))

            with (OUT_DIR / "reazon_hy.jsonl").open("w", buffering=1) as trace:
                hy_res = asyncio.run(replay(config, "reazon-hy", token, "csj-reproduction", csj_audio, trace, frame_bytes=FRAME_64MS_BYTES))

        return {
            "results": results,
            "seek_50ms": seek_res,
            "reazon_hy_non_regression": hy_res,
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

    kv_summary, shape_hist = verify_rolling_kv_and_shapes()
    print("STAGE2_KV_AND_SHAPES:", json.dumps({"kv": kv_summary, "shapes": shape_hist}, ensure_ascii=False), flush=True)
    thread_scaling = probe_speech_cpp_thread_scaling()
    print("STAGE2_THREAD_SCALING:", json.dumps(thread_scaling, ensure_ascii=False), flush=True)
    live_summary = verify_live_ws()
    out = {
        "rolling_kv": kv_summary,
        "mt_suffix_shapes": shape_hist,
        "speech_cpp_thread_scaling": thread_scaling,
        "live_ws_64ms": live_summary,
    }
    (ARCHIVE_DIR / "stage2_verification.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("STAGE2_DONE", flush=True)


if __name__ == "__main__":
    main()
