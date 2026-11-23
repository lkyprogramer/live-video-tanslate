"""Run ReazonSpeech-NeMo-v2 (619M) blind benchmark on D0 (88 slices + silence) and D1 (45s)."""

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[2] / ".model-cache"))

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics_utils import compute_cer, percentile, rss_mib, score_d0_cer

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "p0/evidence/tier2-asr-20261123"
SILENCE_PCM = REPO_ROOT / "p0/fixtures/silence.pcm"


def load_pcm_float32(path: Path) -> np.ndarray:
    return np.frombuffer(path.read_bytes(), dtype="<i2").astype(np.float32) / 32768.0


def main() -> None:
    from reazonspeech.nemo.asr import audio_from_numpy, load_model, transcribe, TranscribeConfig

    d0_slices = [json.loads(ln) for ln in (OUT_DIR / "d0_slices.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    d0_units = [json.loads(ln) for ln in (OUT_DIR / "d0_units.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    d1_slices = [json.loads(ln) for ln in (OUT_DIR / "d1_silero_slices.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    d1_cues = json.loads((OUT_DIR / "d1_srt_cues.json").read_text(encoding="utf-8"))
    d1_ref_text = "".join(c["text_ja"] for c in d1_cues)
    silence_audio = load_pcm_float32(SILENCE_PCM)

    device = "cpu"
    t_load = time.perf_counter()
    model = load_model(device=device)
    load_s = round(time.perf_counter() - t_load, 2)
    cfg = TranscribeConfig(verbose=False)

    # Warmup + silence check
    _ = transcribe(model, audio_from_numpy(np.zeros(8000, dtype=np.float32), 16000), cfg)
    silence_text = transcribe(model, audio_from_numpy(silence_audio, 16000), cfg).text.strip()

    combined = d0_slices + d1_slices
    pcm_cache: dict[str, np.ndarray] = {}
    rows = []
    decode_times = []
    total_audio_s = 0.0
    for row in combined:
        p = row["pcm_path"]
        if p not in pcm_cache:
            pcm_cache[p] = load_pcm_float32(Path(p))
        audio = pcm_cache[p][row["start_sample"] : row["end_sample"]]
        total_audio_s += len(audio) / 16000.0
        t0 = time.perf_counter()
        res = transcribe(model, audio_from_numpy(audio, 16000), cfg)
        dt = time.perf_counter() - t0
        decode_times.append(dt)
        rows.append(row | {"text_ja": res.text.strip(), "decode_s": round(dt, 4)})

    d0_rows = [r for r in rows if r["clip"] != "d1-ep01-45s"]
    d1_rows = [r for r in rows if r["clip"] == "d1-ep01-45s"]
    (OUT_DIR / "reazonspeech-nemo-v2_d0.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in d0_rows) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "reazonspeech-nemo-v2_d1.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in d1_rows) + "\n", encoding="utf-8"
    )

    d0_cer = score_d0_cer(d0_rows, d0_units)
    d1_cer = compute_cer("".join(r["text_ja"] for r in d1_rows), d1_ref_text, strip_speakers=True)
    summary = {
        "repo_id": "reazon-research/reazonspeech-nemo-v2",
        "device": device,
        "load_s": load_s,
        "slices": len(rows),
        "audio_s": round(total_audio_s, 2),
        "decode_p50_s": percentile(decode_times, 0.50),
        "decode_p95_s": percentile(decode_times, 0.95),
        "rtf": round(sum(decode_times) / max(1e-6, total_audio_s), 4),
        "silence_text": silence_text,
        "rss_mib": rss_mib(),
        "d0_cer": d0_cer,
        "d1_cer": d1_cer,
    }
    (OUT_DIR / "asr_nemo_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"reazonspeech-nemo-v2": {"d0_overall_cer": d0_cer["overall_cer"], "d1_cer": d1_cer["cer"], "rtf": summary["rtf"], "rss_mib": summary["rss_mib"], "silence": silence_text}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
