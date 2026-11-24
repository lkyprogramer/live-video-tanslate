"""Stage 3 probes: CPU 6T vs Staggered MTL0 GPU, NeMo Q8/Q5_K/Q4_K sweep, and MT 1.8B/2B/4B comparison."""

from __future__ import annotations

import asyncio
import ctypes
import gc
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/perf_probe"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_mt"))

import mlx.core as mx  # noqa: E402
import numpy as np  # noqa: E402
from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402

from dataset import CACHE_ROOT, D1_PCM, QWEN3_MT_DIR, VAD_PATH, load_frozen_slices, percentile, score_asr_rows  # noqa: E402
from subtitle_service.app import KANA, chinese  # noqa: E402
from subtitle_service.cli import token_path  # noqa: E402
from subtitle_service.config import AsrConfig, load_settings, load_token  # noqa: E402
from subtitle_service.local_pipeline import Qwen3Translator, SpeechCppNeMoRecognizer  # noqa: E402
from subtitle_service.replay import replay  # noqa: E402
from verify_m1_pro_hq_live import ResourceSampler, find_server_pid, launched_server  # noqa: E402
from verify_stage2 import FRAME_64MS_BYTES, active_seek_64ms, analyze_stage2_latencies  # noqa: E402

TMP_CACHE = Path("/tmp/stage3-probe-cache")
OUT_DIR = REPO_ROOT / "p0/evidence/stage3-20261124"
ARCHIVE_DIR = REPO_ROOT / "docs/experiments/stage3-2026-11-24"
CFG_PATH = REPO_ROOT / "engine/config/profiles.toml"
SUITE_PATH = REPO_ROOT / "p0/fixtures/suite.json"


def probe_3a_live_and_staggered() -> dict:
    suite = {x["id"]: x for x in json.loads(SUITE_PATH.read_text())}
    d1_audio = D1_PCM.read_bytes()
    base_toml = CFG_PATH.read_text(encoding="utf-8").replace(
        'cache_root = "../../.model-cache"',
        f'cache_root = "{CACHE_ROOT}"',
    )

    live_runs = {}
    for label, dev in [("cpu_6t", "cpu"), ("mtl0_staggered", "gpu")]:
        run_dir = OUT_DIR / label
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = run_dir / "profiles.toml"
        toml_text = base_toml
        if dev == "gpu":
            toml_text = toml_text.replace(
                'decoding = "beam"',
                'decoding = "beam"\ndevice = "gpu"',
                1,
            )
        cfg_file.write_text(toml_text, encoding="utf-8")
        config = load_settings(cfg_file)
        with launched_server(cfg_file, config, run_dir):
            pid = find_server_pid(config.server.port)
            assert pid is not None
            token = load_token(token_path(), create=False)
            res_map = {}
            with ResourceSampler(pid) as sampler:
                for cid in ["csj-reproduction", "csj-task", "wmf-04", "silence"]:
                    audio = (SUITE_PATH.parent / suite[cid]["file"]).read_bytes()
                    tp = run_dir / f"{cid}.jsonl"
                    with tp.open("w", buffering=1) as trace:
                        r = asyncio.run(replay(config, "m1-pro-nemo", token, cid, audio, trace, frame_bytes=FRAME_64MS_BYTES))
                    sid = json.loads(tp.read_text().splitlines()[0])["event"]["session_id"]
                    res_map[cid] = r | analyze_stage2_latencies(tp, run_dir / "model.jsonl", sid)

                d1_tp = run_dir / "d1.jsonl"
                with d1_tp.open("w", buffering=1) as trace:
                    d1_r = asyncio.run(replay(config, "m1-pro-nemo", token, "d1-ep01-45s", d1_audio, trace, frame_bytes=FRAME_64MS_BYTES))
                d1_sid = json.loads(d1_tp.read_text().splitlines()[0])["event"]["session_id"]
                res_map["d1-ep01-45s"] = d1_r | analyze_stage2_latencies(d1_tp, run_dir / "model.jsonl", d1_sid)

                csj_audio = (SUITE_PATH.parent / suite["csj-reproduction"]["file"]).read_bytes()
                seek_r = asyncio.run(active_seek_64ms(config, "m1-pro-nemo", token, csj_audio, run_dir))

            live_runs[label] = {
                "results": res_map,
                "seek_50ms": seek_r,
                "resources": {
                    "peak_rss_mib": max(sampler.rss_mib) if sampler.rss_mib else 0.0,
                    "mean_cpu_pct": round(sum(sampler.cpu_pct) / max(1, len(sampler.cpu_pct)), 1),
                    "peak_cpu_pct": max(sampler.cpu_pct) if sampler.cpu_pct else 0.0,
                },
            }

    # 100-utterance staggered interleaved MT + MTL0 ASR contention check
    _, _, all_slices, _, _, _ = load_frozen_slices()
    rec = SpeechCppNeMoRecognizer(
        CACHE_ROOT / "speech-cpp/libspeech.dylib",
        CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
        VAD_PATH,
        AsrConfig(type="speech_cpp_nemo", model_file="reazonspeech-nemo-619M-v2-Q8_0.gguf", threads=6, decoding="beam", device="gpu", pad_ms=350),
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

    stag_mt_ms, stag_asr_ms = [], []
    for i in range(100):
        sl = all_slices[i % len(all_slices)]
        t_a0 = time.perf_counter()
        ja = rec.decode(sl["audio"])
        stag_asr_ms.append((time.perf_counter() - t_a0) * 1000.0)
        mx.synchronize()
        t_m0 = time.perf_counter()
        _ = tr.translate(sess, ja or ja_pool[0])
        mx.synchronize()
        stag_mt_ms.append((time.perf_counter() - t_m0) * 1000.0)

    rec.close()
    del tr
    gc.collect()
    mx.clear_cache()

    solo_p50 = round(statistics.median(solo_mt_ms), 2)
    stag_p50 = round(statistics.median(stag_mt_ms), 2)
    return {
        "live_runs": live_runs,
        "staggered_100_stress": {
            "solo_mt_p50_ms": solo_p50,
            "staggered_mt_p50_ms": stag_p50,
            "mt_inflation_pct": round((stag_p50 - solo_p50) / max(0.01, solo_p50) * 100.0, 2),
            "staggered_asr_p50_ms": round(statistics.median(stag_asr_ms), 2),
        },
    }


def probe_3b_nemo_quant_sweep() -> list[dict]:
    _, _, all_slices, d0_units, d1_ref_text, silence_audio = load_frozen_slices()
    models = {
        "Q8_0": CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
    }
    for q in ("Q5_K", "Q4_K"):
        fname = f"reazonspeech-nemo-619M-v2-{q}.gguf"
        p = Path(hf_hub_download("sakasegawa/reazonspeech-nemo-v2-GGUF", fname, local_dir=str(TMP_CACHE / "nemo-gguf")))
        models[q] = p

    out = []
    for qname, mpath in models.items():
        size_mb = round(mpath.stat().st_size / 1e6, 1)
        for dev in ("cpu", "gpu"):
            cfg = AsrConfig(
                type="speech_cpp_nemo",
                model_file=mpath.name,
                threads=6,
                decoding="beam",
                device=dev,
                pad_ms=350,
            )
            rec = SpeechCppNeMoRecognizer(CACHE_ROOT / "speech-cpp/libspeech.dylib", mpath, VAD_PATH, cfg)
            rows, ts = [], []
            for s in all_slices:
                t0 = time.perf_counter()
                ja = rec.decode(s["audio"])
                ts.append((time.perf_counter() - t0) * 1000.0)
                rows.append(dict(s, text_ja=ja))
            sil = rec.decode(silence_audio)
            rec.close()
            sc = score_asr_rows(rows, d0_units, d1_ref_text)
            entry = {
                "quant": qname,
                "size_mb": size_mb,
                "device": dev,
                "p50_ms": round(statistics.median(ts), 1),
                "p95_ms": round(percentile([x / 1000.0 for x in ts], 0.95) * 1000.0, 1),
                "d0_cer": sc["d0_cer"]["overall_cer"],
                "d1_cer": sc["d1_cer"]["cer"],
                "silence_out": sil,
            }
            out.append(entry)
            print("PROBE_3B:", json.dumps(entry, ensure_ascii=False), flush=True)
    return out


def probe_3c_mt_candidates() -> list[dict]:
    _, _, all_slices, _, _, _ = load_frozen_slices()
    rec = SpeechCppNeMoRecognizer(
        CACHE_ROOT / "speech-cpp/libspeech.dylib",
        CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
        VAD_PATH,
        AsrConfig(type="speech_cpp_nemo", model_file="reazonspeech-nemo-619M-v2-Q8_0.gguf", threads=6, decoding="beam", device="cpu", pad_ms=350),
    )
    ja_inputs = [rec.decode(s["audio"]) for s in all_slices]
    rec.close()

    candidates = [
        ("Qwen3-4B-Instruct-2507-4bit", QWEN3_MT_DIR),
        ("HY-MT1.5-1.8B-4bit", Path(snapshot_download("mlx-community/HY-MT1.5-1.8B-4bit", cache_dir=str(TMP_CACHE / "hf")))),
        ("Qwen3.5-2B-4bit", Path(snapshot_download("mlx-community/Qwen3.5-2B-4bit", cache_dir=str(TMP_CACHE / "hf")))),
    ]

    sample_indices = {
        "csj_r_0": 0,
        "csj_task_remi": 13,
        "wmf_0": 32,
        "d1_seg1": 88,
        "d1_seg2": 89,
        "d1_seg3": 90,
    }

    results = []
    for name, path in candidates:
        try:
            mx.reset_peak_memory()
            tr = Qwen3Translator(path, context_turns=0)
            sess = tr.new_session()
            times_ms, outs = [], []
            for ja in ja_inputs:
                mx.synchronize()
                t0 = time.perf_counter()
                zh = tr.translate(sess, ja)
                mx.synchronize()
                times_ms.append((time.perf_counter() - t0) * 1000.0)
                outs.append(zh)
            kana_leaks = sum(1 for z in outs if z and KANA.search(z))
            gw_ok = sum(1 for z in outs if z and chinese(z) is not None)
            peak_gpu = round(mx.get_peak_memory() / (1024 * 1024), 1)
            entry = {
                "model": name,
                "status": "ok",
                "p50_ms": round(statistics.median(times_ms), 1),
                "p95_ms": round(percentile([x / 1000.0 for x in times_ms], 0.95) * 1000.0, 1),
                "total_s": round(sum(times_ms) / 1000.0, 2),
                "kana_leaks": kana_leaks,
                "gateway_pass": gw_ok,
                "peak_gpu_mib": peak_gpu,
                "samples": {k: outs[idx] for k, idx in sample_indices.items() if idx < len(outs)},
            }
            del tr
            gc.collect()
            mx.clear_cache()
        except Exception as exc:
            entry = {"model": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
        results.append(entry)
        print("PROBE_3C:", json.dumps(entry, ensure_ascii=False), flush=True)
    return results


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    TMP_CACHE.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    p3a = probe_3a_live_and_staggered()
    print("PROBE_3A_STRESS:", json.dumps(p3a["staggered_100_stress"], ensure_ascii=False), flush=True)
    p3b = probe_3b_nemo_quant_sweep()
    p3c = probe_3c_mt_candidates()

    out = {"probe_3a": p3a, "probe_3b": p3b, "probe_3c": p3c}
    (ARCHIVE_DIR / "stage3_verification.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("STAGE3_DONE", flush=True)


if __name__ == "__main__":
    main()
