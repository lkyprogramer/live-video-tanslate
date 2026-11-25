"""Stage 5A-2 live A/B verification: MTL0 GPU vs ANE (beam & greedy) + concurrent ANE/GPU stress."""

from __future__ import annotations

import asyncio
import ctypes
import gc
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

import mlx.core as mx  # noqa: E402

from dataset import CACHE_ROOT, D1_PCM, QWEN3_MT_DIR, VAD_PATH, load_frozen_slices, percentile  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import AsrConfig, load_settings, load_token  # noqa: E402
from subtitle_service.local_pipeline import Qwen3Translator, SpeechCppNeMoRecognizer  # noqa: E402
from subtitle_service.replay import replay  # noqa: E402
from verify_m1_pro_hq_live import ResourceSampler, find_server_pid, launched_server  # noqa: E402
from verify_stage2 import FRAME_64MS_BYTES, active_seek_64ms, analyze_stage2_latencies  # noqa: E402
from verify_stage4 import analyze_stage4_extra as analyze_draft_onset  # noqa: E402

OUT_DIR = REPO_ROOT / "p0/evidence/stage5a2-20261124"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage5a2-2026-11-24"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"


def _wired_mb() -> float:
    lib = ctypes.CDLL(str(CACHE_ROOT / "nemo-ane/libnemo_ane.dylib"))
    lib.nemo_ane_wired_mb.argtypes = []
    lib.nemo_ane_wired_mb.restype = ctypes.c_double
    return float(lib.nemo_ane_wired_mb())


def _patch_profile(base_toml: str, *, device: str, decoding: str) -> str:
    lines = []
    in_nemo_asr = False
    replaced_dev = False
    replaced_dec = False
    for ln in base_toml.splitlines():
        if ln.strip() == "[profiles.m1-pro-nemo.asr]":
            in_nemo_asr = True
        elif ln.strip().startswith("[") and in_nemo_asr:
            if not replaced_dev:
                lines.append(f'device = "{device}"')
            in_nemo_asr = False
        if in_nemo_asr and ln.strip().startswith("device ="):
            lines.append(f'device = "{device}"')
            replaced_dev = True
            continue
        if in_nemo_asr and ln.strip().startswith("decoding ="):
            lines.append(f'decoding = "{decoding}"')
            replaced_dec = True
            continue
        lines.append(ln)
    if in_nemo_asr and not replaced_dev:
        lines.append(f'device = "{device}"')
    assert replaced_dec
    return "\n".join(lines) + "\n"


def run_live_arm(label: str, base_toml: str, suite: dict, d1_audio: bytes, *, device: str, decoding: str) -> dict:
    run_dir = OUT_DIR / label
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = run_dir / "profiles.toml"
    cfg_file.write_text(_patch_profile(base_toml, device=device, decoding=decoding), encoding="utf-8")
    config = load_settings(cfg_file)

    w0 = _wired_mb()
    with launched_server(cfg_file, config, run_dir):
        pid = find_server_pid(config.server.port)
        assert pid is not None
        token = load_token(token_path(), create=False)
        res_map = {}
        with ResourceSampler(pid) as sampler:
            for cid in ("csj-reproduction", "csj-task", "wmf-04", "silence"):
                audio = (SUITE_PATH.parent / suite[cid]["file"]).read_bytes()
                tp = run_dir / f"{cid}.jsonl"
                with tp.open("w", buffering=1) as trace:
                    r = asyncio.run(replay(config, "m1-pro-nemo", token, cid, audio, trace, frame_bytes=FRAME_64MS_BYTES))
                sid = json.loads(tp.read_text().splitlines()[0])["event"]["session_id"]
                lat = analyze_stage2_latencies(tp, run_dir / "model.jsonl", sid)
                onset = analyze_draft_onset(tp, run_dir / "model.jsonl", sid) if cid != "silence" else {}
                res_map[cid] = r | lat | onset

            d1_tp = run_dir / "d1.jsonl"
            with d1_tp.open("w", buffering=1) as trace:
                d1_r = asyncio.run(replay(config, "m1-pro-nemo", token, "d1-ep01-45s", d1_audio, trace, frame_bytes=FRAME_64MS_BYTES))
            d1_sid = json.loads(d1_tp.read_text().splitlines()[0])["event"]["session_id"]
            res_map["d1-ep01-45s"] = d1_r | analyze_stage2_latencies(d1_tp, run_dir / "model.jsonl", d1_sid) | analyze_draft_onset(d1_tp, run_dir / "model.jsonl", d1_sid)

            csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
            seek_r = asyncio.run(active_seek_64ms(config, "m1-pro-nemo", token, csj_audio, run_dir))

        w1 = _wired_mb()
        return {
            "results": res_map,
            "seek_50ms": seek_r,
            "resources": {
                "peak_rss_mib": max(sampler.rss_mib) if sampler.rss_mib else 0.0,
                "mean_cpu_pct": round(sum(sampler.cpu_pct) / max(1, len(sampler.cpu_pct)), 1),
                "peak_cpu_pct": max(sampler.cpu_pct) if sampler.cpu_pct else 0.0,
                "wired_delta_mb": round(w1 - w0, 1),
            },
        }


def run_concurrent_ane_gpu_stress() -> dict:
    _, _, all_slices, _, _, _ = load_frozen_slices()
    rec = SpeechCppNeMoRecognizer(
        CACHE_ROOT / "speech-cpp/libspeech.dylib",
        CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
        VAD_PATH,
        AsrConfig(
            type="speech_cpp_nemo",
            model_file="reazonspeech-nemo-619M-v2-Q8_0.gguf",
            threads=6,
            decoding="greedy",
            device="ane",
            pad_ms=350,
        ),
    )
    tr = Qwen3Translator(QWEN3_MT_DIR, context_turns=0)
    sess = tr.new_session()
    ja_pool = [rec.decode(s["audio"]) for s in all_slices[:25]]

    solo_mt_ms = []
    for i in range(100):
        ja = ja_pool[i % len(ja_pool)]
        mx.synchronize()
        t0 = time.perf_counter()
        _ = tr.translate(sess, ja)
        mx.synchronize()
        solo_mt_ms.append((time.perf_counter() - t0) * 1000.0)

    # True parallel ANE ASR in background thread while GPU MT runs 100 utterances
    stop_asr = threading.Event()
    asr_times: list[float] = []

    def asr_worker() -> None:
        idx = 0
        while not stop_asr.is_set():
            sl = all_slices[idx % len(all_slices)]
            idx += 1
            t0 = time.perf_counter()
            _ = rec.decode(sl["audio"])
            asr_times.append((time.perf_counter() - t0) * 1000.0)

    t_asr = threading.Thread(target=asr_worker, daemon=True)
    t_asr.start()
    conc_mt_ms = []
    for i in range(100):
        ja = ja_pool[i % len(ja_pool)]
        mx.synchronize()
        t0 = time.perf_counter()
        _ = tr.translate(sess, ja)
        mx.synchronize()
        conc_mt_ms.append((time.perf_counter() - t0) * 1000.0)
    stop_asr.set()
    t_asr.join(timeout=5.0)

    rec.close()
    del tr
    gc.collect()
    mx.clear_cache()

    solo_p50 = round(statistics.median(solo_mt_ms), 2)
    conc_p50 = round(statistics.median(conc_mt_ms), 2)
    return {
        "solo_mt_p50_ms": solo_p50,
        "concurrent_mt_p50_ms": conc_p50,
        "mt_inflation_pct": round((conc_p50 - solo_p50) / max(0.01, solo_p50) * 100.0, 2),
        "concurrent_asr_count": len(asr_times),
        "concurrent_asr_p50_ms": round(statistics.median(asr_times), 2) if asr_times else 0.0,
    }


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    suite = {x["id"]: x for x in json.loads(SUITE_PATH.read_text())}
    d1_audio = D1_PCM.read_bytes()
    base_toml = CFG_PATH.read_text(encoding="utf-8").replace(
        'cache_root = "../../.model-cache"',
        f'cache_root = "{CACHE_ROOT}"',
    )

    live = {}
    for label, dev, dec in [
        ("gpu_beam", "gpu", "beam"),
        ("ane_beam", "ane", "beam"),
        ("ane_greedy", "ane", "greedy"),
    ]:
        print(f"=== Running 1x WS Live Arm: {label} (device={dev}, decoding={dec}) ===")
        live[label] = run_live_arm(label, base_toml, suite, d1_audio, device=dev, decoding=dec)

    print("=== Running 100-utterance true concurrent ANE ASR + GPU MT stress ===")
    stress = run_concurrent_ane_gpu_stress()

    summary = {"live_runs": live, "concurrent_ane_gpu_stress": stress}
    out_file = ARCHIVE_DIR / "stage5a2_live_verification.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(OUT_DIR, ignore_errors=True)

    compact = {
        "concurrent_ane_gpu_stress": stress,
        "arms": {
            k: {
                "resources": v["resources"],
                "seek_50ms": v["seek_50ms"],
                "d1": {
                    "captions": v["results"]["d1-ep01-45s"]["captions"],
                    "warnings": v["results"]["d1-ep01-45s"]["warnings"],
                    "first_delta_p50": v["results"]["d1-ep01-45s"]["first_delta_e2e_p50_s"],
                    "first_delta_p95": v["results"]["d1-ep01-45s"]["first_delta_e2e_p95_s"],
                    "final_e2e_p50": v["results"]["d1-ep01-45s"]["final_e2e_p50_s"],
                    "final_e2e_p95": v["results"]["d1-ep01-45s"]["final_e2e_p95_s"],
                    "onset_to_first_delta_p50": v["results"]["d1-ep01-45s"].get("onset_to_first_delta_p50_s"),
                },
                "csj_repro": {
                    "captions": v["results"]["csj-reproduction"]["captions"],
                    "first_delta_p50": v["results"]["csj-reproduction"]["first_delta_e2e_p50_s"],
                    "final_e2e_p50": v["results"]["csj-reproduction"]["final_e2e_p50_s"],
                    "final_e2e_p95": v["results"]["csj-reproduction"]["final_e2e_p95_s"],
                    "onset_to_first_delta_p50": v["results"]["csj-reproduction"].get("onset_to_first_delta_p50_s"),
                },
            }
            for k, v in live.items()
        },
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    print(f"\nSaved {out_file}")


if __name__ == "__main__":
    main()
