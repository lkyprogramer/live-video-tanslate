"""Run Tier 2 MLX ASR blind benchmark on D0 (88 slices + silence) and D1 (45s Brush Up Life)."""

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[2] / ".model-cache"))

import mlx.core as mx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics_utils import compute_cer, percentile, rss_mib, score_d0_cer

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "p0/evidence/tier2-asr-20261123"
SILENCE_PCM = REPO_ROOT / "p0/fixtures/silence.pcm"


def load_pcm_float32(path: Path) -> np.ndarray:
    return np.frombuffer(path.read_bytes(), dtype="<i2").astype(np.float32) / 32768.0


def run_qwen3(repo_id: str, slices: list[dict], silence_audio: np.ndarray, out_path: Path) -> dict:
    from mlx_audio.stt import load

    mx.reset_peak_memory()
    t_load = time.perf_counter()
    model = load(repo_id)
    load_s = round(time.perf_counter() - t_load, 2)

    # Warmup on 0.5s silence
    _ = model.generate(np.zeros(8000, dtype=np.float32), language="Japanese", temperature=0.0, max_tokens=64)
    silence_text = model.generate(silence_audio, language="Japanese", temperature=0.0, max_tokens=128).text.strip()

    pcm_cache: dict[str, np.ndarray] = {}
    rows = []
    decode_times = []
    total_audio_s = 0.0
    for row in slices:
        p = row["pcm_path"]
        if p not in pcm_cache:
            pcm_cache[p] = load_pcm_float32(Path(p))
        audio = pcm_cache[p][row["start_sample"] : row["end_sample"]]
        total_audio_s += len(audio) / 16000.0
        t0 = time.perf_counter()
        res = model.generate(audio, language="Japanese", temperature=0.0, max_tokens=256)
        dt = time.perf_counter() - t0
        decode_times.append(dt)
        rows.append(row | {"text_ja": res.text.strip(), "decode_s": round(dt, 4)})

    out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    summary = {
        "repo_id": repo_id,
        "load_s": load_s,
        "slices": len(rows),
        "audio_s": round(total_audio_s, 2),
        "decode_p50_s": percentile(decode_times, 0.50),
        "decode_p95_s": percentile(decode_times, 0.95),
        "rtf": round(sum(decode_times) / max(1e-6, total_audio_s), 4),
        "silence_text": silence_text,
        "gpu_peak_mib": round(mx.get_peak_memory() / (1024 * 1024), 2),
        "rss_mib": rss_mib(),
    }
    del model
    gc.collect()
    mx.clear_cache()
    return summary


def run_mlx_whisper(repo_id: str, slices: list[dict], silence_audio: np.ndarray, out_path: Path) -> dict:
    import mlx_whisper

    mx.reset_peak_memory()
    t_load = time.perf_counter()
    # Trigger download & warmup
    _ = mlx_whisper.transcribe(
        np.zeros(8000, dtype=np.float32),
        path_or_hf_repo=repo_id,
        language="ja",
        temperature=0.0,
        condition_on_previous_text=False,
    )
    load_s = round(time.perf_counter() - t_load, 2)

    silence_text = mlx_whisper.transcribe(
        silence_audio,
        path_or_hf_repo=repo_id,
        language="ja",
        temperature=0.0,
        condition_on_previous_text=False,
    )["text"].strip()

    pcm_cache: dict[str, np.ndarray] = {}
    rows = []
    decode_times = []
    total_audio_s = 0.0
    for row in slices:
        p = row["pcm_path"]
        if p not in pcm_cache:
            pcm_cache[p] = load_pcm_float32(Path(p))
        audio = pcm_cache[p][row["start_sample"] : row["end_sample"]]
        total_audio_s += len(audio) / 16000.0
        t0 = time.perf_counter()
        res = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=repo_id,
            language="ja",
            temperature=0.0,
            condition_on_previous_text=False,
        )
        dt = time.perf_counter() - t0
        decode_times.append(dt)
        rows.append(row | {"text_ja": res["text"].strip(), "decode_s": round(dt, 4)})

    out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    summary = {
        "repo_id": repo_id,
        "load_s": load_s,
        "slices": len(rows),
        "audio_s": round(total_audio_s, 2),
        "decode_p50_s": percentile(decode_times, 0.50),
        "decode_p95_s": percentile(decode_times, 0.95),
        "rtf": round(sum(decode_times) / max(1e-6, total_audio_s), 4),
        "silence_text": silence_text,
        "gpu_peak_mib": round(mx.get_peak_memory() / (1024 * 1024), 2),
        "rss_mib": rss_mib(),
    }
    gc.collect()
    mx.clear_cache()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        nargs="+",
        default=["qwen3-asr-1.7b-8bit", "whisper-large-v3-turbo", "anime-whisper-mlx"],
    )
    args = parser.parse_args()

    d0_slices = [json.loads(ln) for ln in (OUT_DIR / "d0_slices.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    d0_units = [json.loads(ln) for ln in (OUT_DIR / "d0_units.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    d1_slices = [json.loads(ln) for ln in (OUT_DIR / "d1_silero_slices.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    d1_cues = json.loads((OUT_DIR / "d1_srt_cues.json").read_text(encoding="utf-8"))
    d1_ref_text = "".join(c["text_ja"] for c in d1_cues)
    silence_audio = load_pcm_float32(SILENCE_PCM)

    specs = {
        "qwen3-asr-1.7b-8bit": ("qwen3", "mlx-community/Qwen3-ASR-1.7B-8bit"),
        "whisper-large-v3-turbo": ("whisper", "mlx-community/whisper-large-v3-turbo"),
        "anime-whisper-mlx": ("whisper", "imeipopo18/anime-whisper-mlx"),
    }

    combined_slices = d0_slices + d1_slices
    results = {}

    for name in args.models:
        kind, repo_id = specs[name]
        all_out = OUT_DIR / f"{name}_all.jsonl"
        print(f"=== Running {name} ({repo_id}) on {len(combined_slices)} slices ===", flush=True)
        runner = run_qwen3 if kind == "qwen3" else run_mlx_whisper
        summary = runner(repo_id, combined_slices, silence_audio, all_out)

        all_rows = [json.loads(ln) for ln in all_out.read_text(encoding="utf-8").splitlines() if ln.strip()]
        d0_rows = [r for r in all_rows if r["clip"] != "d1-ep01-45s"]
        d1_rows = [r for r in all_rows if r["clip"] == "d1-ep01-45s"]
        (OUT_DIR / f"{name}_d0.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in d0_rows) + "\n", encoding="utf-8"
        )
        (OUT_DIR / f"{name}_d1.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in d1_rows) + "\n", encoding="utf-8"
        )

        d0_cer = score_d0_cer(d0_rows, d0_units)
        d1_hyp = "".join(r["text_ja"] for r in d1_rows)
        d1_cer = compute_cer(d1_hyp, d1_ref_text, strip_speakers=True)
        summary["d0_cer"] = d0_cer
        summary["d1_cer"] = d1_cer
        results[name] = summary
        print(json.dumps({name: {"d0_overall_cer": d0_cer["overall_cer"], "d1_cer": d1_cer["cer"], "rtf": summary["rtf"], "gpu_peak_mib": summary["gpu_peak_mib"], "silence": summary["silence_text"]}}, ensure_ascii=False), flush=True)

    summary_path = OUT_DIR / "asr_mlx_summary.json"
    existing = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    existing.update(results)
    summary_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
