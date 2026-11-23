"""Step 1B: speech.cpp (ReazonSpeech-NeMo-v2 619M GGUF) M1 Pro benchmark + concurrency contention probe."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import (  # noqa: E402
    QWEN3_MT_DIR,
    RATE,
    REPO_ROOT,
    load_frozen_slices,
    percentile,
    rss_mib,
    score_asr_rows,
)
from profile_and_bench_mlx import CachedQwen3Translator  # noqa: E402
from subtitle_service.local_pipeline import Qwen3Translator  # noqa: E402

PROBE_DIR = Path("/tmp/speech-cpp-probe")
MODELS_DIR = PROBE_DIR / "models"
WAVS_DIR = PROBE_DIR / "wavs"
RUNNER_BIN = PROBE_DIR / "bench_nemo_batch"
OUT_DIR = REPO_ROOT / "docs/experiments/perf-probe-2026-11-23"


def write_wav_s16le(path: Path, audio_f32: np.ndarray, sr: int = RATE) -> None:
    pcm16 = np.clip(audio_f32 * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())


def prepare_wav_manifest(slices: list[dict], silence_audio: np.ndarray) -> tuple[Path, Path]:
    WAVS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = PROBE_DIR / "manifest.txt"
    sil_wav = WAVS_DIR / "silence_10s.wav"
    write_wav_s16le(sil_wav, silence_audio)

    lines = [str(sil_wav)]
    for i, s in enumerate(slices):
        wp = WAVS_DIR / f"slice_{i:03d}.wav"
        if not wp.exists():
            write_wav_s16le(wp, s["audio"])
        lines.append(str(wp))
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest, sil_wav


def compile_runner() -> None:
    if RUNNER_BIN.exists():
        return
    src = PROBE_DIR / "bench_nemo_batch.cpp"
    inc = PROBE_DIR / "speech.cpp/include"
    build = PROBE_DIR / "speech.cpp/build"
    # Find libspeech.dylib inside build
    libs = list(build.glob("libspeech*.dylib"))
    assert libs, f"libspeech.dylib not found in {build}"
    cmd = [
        "clang++",
        "-O3",
        "-std=c++17",
        f"-I{inc}",
        str(src),
        str(libs[0]),
        f"-Wl,-rpath,{build}",
        "-o",
        str(RUNNER_BIN),
    ]
    subprocess.run(cmd, check=True)


def run_config(
    name: str,
    model_path: Path,
    device: str,
    threads: int,
    decoding: str,
    decode_on_cpu: str,
    manifest: Path,
    slices: list[dict],
    d0_units: list[dict],
    d1_ref_text: str,
) -> dict:
    out_jsonl = PROBE_DIR / f"{name}.jsonl"
    cmd = [
        str(RUNNER_BIN),
        str(model_path),
        device,
        str(threads),
        decoding,
        decode_on_cpu,
        str(manifest),
        str(out_jsonl),
    ]
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    wall_s = round(time.perf_counter() - t0, 3)

    raw_rows = [json.loads(ln) for ln in out_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
    silence_row = raw_rows[0]
    slice_rows = raw_rows[1:]
    assert len(slice_rows) == len(slices)

    scored_rows = []
    decode_times = []
    for s, rr in zip(slices, slice_rows):
        decode_times.append(rr["decode_s"])
        scored_rows.append(
            {
                "clip": s["clip"],
                "segment_id": s["segment_id"],
                "start_sample": s["start_sample"],
                "end_sample": s["end_sample"],
                "duration_s": s["duration_s"],
                "text_ja": rr["text_ja"],
                "decode_s": rr["decode_s"],
            }
        )

    cer = score_asr_rows(scored_rows, d0_units, d1_ref_text)
    d1_texts = [r["text_ja"] for r in scored_rows if r["clip"] == "d1-ep01-45s"]
    total_audio = sum(s["duration_s"] for s in slices)
    return {
        "name": name,
        "model_file": model_path.name,
        "requested_device": device,
        "actual_device": silence_row["device"],
        "actual_threads": silence_row["threads"],
        "decoding": decoding,
        "decode_on_cpu": decode_on_cpu,
        "load_s": round(silence_row["load_s"], 3),
        "wall_s": wall_s,
        "total_decode_s": round(sum(decode_times), 4),
        "p50_s": percentile(decode_times, 0.50),
        "p95_s": percentile(decode_times, 0.95),
        "rtf": round(sum(decode_times) / max(1e-6, total_audio), 4),
        "rss_mib": round(max(r["rss_mib"] for r in raw_rows), 2),
        "silence_text": silence_row["text_ja"],
        "d0_overall_cer": cer["d0_cer"]["overall_cer"],
        "d0_by_clip": {k: v["cer"] for k, v in cer["d0_cer"]["by_clip"].items()},
        "d1_cer": cer["d1_cer"]["cer"],
        "d1_texts": d1_texts,
    }


def run_concurrency_test(
    label: str,
    model_path: Path,
    device: str,
    threads: int,
    decoding: str,
    decode_on_cpu: str,
    manifest_20s: Path,
    mt_inputs: list[str],
    cached_mt: CachedQwen3Translator,
    mt_solo_p50_s: float,
    asr_solo_p50_s: float,
) -> dict:
    """Run speech.cpp subprocess concurrently with resident MLX Qwen3-4B MT for ~15-20s."""
    out_jsonl = PROBE_DIR / f"conc_{label}.jsonl"
    if out_jsonl.exists():
        out_jsonl.unlink()

    cmd = [
        str(RUNNER_BIN),
        str(model_path),
        device,
        str(threads),
        decoding,
        decode_on_cpu,
        str(manifest_20s),
        str(out_jsonl),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Wait briefly for model load to complete (~0.8-1.5s) before pumping MT
    while proc.poll() is None and (not out_jsonl.exists() or out_jsonl.stat().st_size == 0):
        time.sleep(0.05)

    mt_times = []
    idx = 0
    while proc.poll() is None:
        ja = mt_inputs[idx % len(mt_inputs)]
        idx += 1
        _, info = cached_mt.translate(ja)
        mt_times.append(info["total_ms"] / 1000.0)
        if len(mt_times) >= 40:
            break

    proc.wait(timeout=60)
    assert proc.returncode == 0, proc.stderr.read().decode()

    asr_rows = [json.loads(ln) for ln in out_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
    asr_times = [r["decode_s"] for r in asr_rows[1:]]
    asr_p50 = percentile(asr_times, 0.50)
    mt_p50 = percentile(mt_times, 0.50)
    return {
        "mode": label,
        "asr_iters": len(asr_times),
        "mt_iters": len(mt_times),
        "asr_solo_p50_s": asr_solo_p50_s,
        "asr_conc_p50_s": asr_p50,
        "asr_conc_p95_s": percentile(asr_times, 0.95),
        "asr_inflation_pct": round((asr_p50 / max(1e-6, asr_solo_p50_s) - 1.0) * 100, 2),
        "mt_solo_p50_s": mt_solo_p50_s,
        "mt_conc_p50_s": mt_p50,
        "mt_conc_p95_s": percentile(mt_times, 0.95),
        "mt_inflation_pct": round((mt_p50 / max(1e-6, mt_solo_p50_s) - 1.0) * 100, 2),
        "asr_rss_mib": round(max(r["rss_mib"] for r in asr_rows), 2),
        "mt_process_rss_mib": rss_mib(),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    compile_runner()
    _, _, slices, d0_units, d1_ref_text, silence_audio = load_frozen_slices()
    manifest, _ = prepare_wav_manifest(slices, silence_audio)

    q8_path = MODELS_DIR / "reazonspeech-nemo-619M-v2-Q8_0.gguf"
    f16_path = MODELS_DIR / "reazonspeech-nemo-619M-v2-F16.gguf"

    configs = [
        ("q8_gpu_greedy", q8_path, "gpu", 8, "greedy", "true"),
        ("q8_gpu_beam", q8_path, "gpu", 8, "beam", "true"),
        ("q8_cpu_t4_greedy", q8_path, "cpu", 4, "greedy", "default"),
        ("q8_cpu_t8_greedy", q8_path, "cpu", 8, "greedy", "default"),
        ("q8_cpu_t8_beam", q8_path, "cpu", 8, "beam", "default"),
        ("f16_gpu_greedy", f16_path, "gpu", 8, "greedy", "false"),
        ("f16_gpu_beam", f16_path, "gpu", 8, "beam", "false"),
    ]

    results = []
    for cfg in configs:
        print(f"=== Running speech.cpp {cfg[0]} ===", flush=True)
        res = run_config(*cfg, manifest, slices, d0_units, d1_ref_text)
        results.append(res)
        print(
            json.dumps(
                {
                    res["name"]: {
                        "device": res["actual_device"],
                        "p50_s": res["p50_s"],
                        "p95_s": res["p95_s"],
                        "rtf": res["rtf"],
                        "d0_cer": res["d0_overall_cer"],
                        "d1_cer": res["d1_cer"],
                        "rss_mib": res["rss_mib"],
                        "silence": res["silence_text"],
                    }
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    (OUT_DIR / "speech_cpp_nemo_summary.json").write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Concurrency Contention Probe: best CPU-only vs best Metal GPU against MLX Qwen3-4B MT (M1)
    mt_base = Qwen3Translator(QWEN3_MT_DIR)
    cached_mt = CachedQwen3Translator(mt_base.model, mt_base.tokenizer)
    sample_ja = [
        "そういえば保育士さんって大変だよね。",
        "一回キレられたのよなれなれしいっつって。",
        "この三分法は藤崎先生によって提案されたものです。",
        "パラ言語情報は意図的にコントロールすることができる。",
        "投稿者あるいは貢献者。編集者が気をつけるべき点は何ですか。",
    ]
    # Solo MT P50
    solo_mt = []
    for _ in range(3):
        for ja in sample_ja:
            _, info = cached_mt.translate(ja)
            solo_mt.append(info["total_ms"] / 1000.0)
    mt_solo_p50 = percentile(solo_mt, 0.50)

    # Build a ~25-slice manifest for ~15-20s concurrent run (use 2-6s slices)
    mid_slices = [i for i, s in enumerate(slices) if 2.0 <= s["duration_s"] <= 7.0][:25]
    conc_manifest = PROBE_DIR / "manifest_conc.txt"
    conc_lines = [str(WAVS_DIR / "silence_10s.wav")] + [str(WAVS_DIR / f"slice_{i:03d}.wav") for i in mid_slices]
    conc_manifest.write_text("\n".join(conc_lines) + "\n", encoding="utf-8")

    best_cpu = min([r for r in results if r["requested_device"] == "cpu"], key=lambda x: x["rtf"])
    best_gpu = min([r for r in results if r["requested_device"] == "gpu"], key=lambda x: x["rtf"])

    conc_cpu = run_concurrency_test(
        f"cpu_{best_cpu['name']}",
        MODELS_DIR / best_cpu["model_file"],
        "cpu",
        best_cpu["actual_threads"],
        best_cpu["decoding"],
        best_cpu["decode_on_cpu"],
        conc_manifest,
        sample_ja,
        cached_mt,
        mt_solo_p50,
        best_cpu["p50_s"],
    )
    conc_gpu = run_concurrency_test(
        f"gpu_{best_gpu['name']}",
        MODELS_DIR / best_gpu["model_file"],
        "gpu",
        8,
        best_gpu["decoding"],
        best_gpu["decode_on_cpu"],
        conc_manifest,
        sample_ja,
        cached_mt,
        mt_solo_p50,
        best_gpu["p50_s"],
    )

    (OUT_DIR / "concurrency_contention.json").write_text(
        json.dumps({"heterogeneous_cpu_nemo_plus_gpu_mt": conc_cpu, "dual_metal_gpu_nemo_plus_gpu_mt": conc_gpu}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("DONE CONCURRENCY:", json.dumps({"cpu": conc_cpu, "gpu": conc_gpu}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
