"""Shared frozen D0 + D1 + silence dataset loader for perf probes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = REPO_ROOT / ".model-cache"
HUB_ROOT = CACHE_ROOT / "hub"
VAD_PATH = CACHE_ROOT / "reazonspeech-assets/silero_vad.onnx"
SILENCE_PCM = REPO_ROOT / "p0/fixtures/silence.pcm"
D1_PCM = REPO_ROOT / "p0/evidence/d1-dialogue/first45s-control.pcm"
D1_SRT_CUES = REPO_ROOT / "docs/experiments/tier2-asr-2026-11-23/d1_srt_cues.json"
D0_SLICES = REPO_ROOT / "p0/evidence/n0-next/slices.jsonl"
D0_UNITS = REPO_ROOT / "p0/evidence/n0-next/final-references/units.jsonl"

QWEN3_ASR_REV = "a8379a2e2f9e313c9292cdf1af4055ab56d50d55"
QWEN3_MT_REV = "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
QWEN3_ASR_DIR = HUB_ROOT / "models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots" / QWEN3_ASR_REV
QWEN3_MT_DIR = HUB_ROOT / "models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots" / QWEN3_MT_REV

sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_asr"))

from metrics_utils import compute_cer, percentile, rss_mib, score_d0_cer  # noqa: E402
from subtitle_service.local_pipeline import RATE, silero_config, vad_segments  # noqa: E402


def load_pcm_float32(path: Path) -> np.ndarray:
    return np.frombuffer(path.read_bytes(), dtype="<i2").astype(np.float32) / 32768.0


def load_frozen_slices() -> tuple[list[dict], list[dict], list[dict], list[dict], str, np.ndarray]:
    """Return (d0_slices, d1_slices, all_slices, d0_units, d1_ref_text, silence_audio)."""
    d0_slices = [
        json.loads(ln)
        for ln in D0_SLICES.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    for row in d0_slices:
        p = Path(row["pcm_path"])
        if not p.exists():
            p = REPO_ROOT / "p0/fixtures" / p.name
        row["pcm_path"] = str(p)

    d0_units = [
        json.loads(ln)
        for ln in D0_UNITS.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]

    import sherpa_onnx

    vad = sherpa_onnx.VoiceActivityDetector(silero_config(VAD_PATH, 600), buffer_size_in_seconds=120)
    raw_d1 = D1_PCM.read_bytes()
    d1_slices: list[dict] = []
    seg_id = 0
    for off in range(0, len(raw_d1), 6400):
        chunk = raw_d1[off : off + 6400]
        for start, samples in vad_segments(vad, chunk):
            seg_id += 1
            d1_slices.append(
                {
                    "clip": "d1-ep01-45s",
                    "segment_id": seg_id,
                    "pcm_path": str(D1_PCM),
                    "start_sample": int(start),
                    "end_sample": int(start + len(samples)),
                }
            )
    for start, samples in vad_segments(vad, None):
        seg_id += 1
        d1_slices.append(
            {
                "clip": "d1-ep01-45s",
                "segment_id": seg_id,
                "pcm_path": str(D1_PCM),
                "start_sample": int(start),
                "end_sample": int(start + len(samples)),
            }
        )
    assert len(d1_slices) == 3, f"Expected 3 D1 slices, got {len(d1_slices)}"

    d1_cues = json.loads(D1_SRT_CUES.read_text(encoding="utf-8"))
    d1_ref_text = "".join(c["text_ja"] for c in d1_cues)
    silence_audio = load_pcm_float32(SILENCE_PCM)

    pcm_cache: dict[str, np.ndarray] = {}
    all_slices = []
    for idx, row in enumerate(d0_slices + d1_slices):
        p = row["pcm_path"]
        if p not in pcm_cache:
            pcm_cache[p] = load_pcm_float32(Path(p))
        audio = pcm_cache[p][row["start_sample"] : row["end_sample"]]
        item = dict(row)
        item["slice_idx"] = idx
        item["audio"] = audio
        item["duration_s"] = round(len(audio) / float(RATE), 4)
        all_slices.append(item)

    return d0_slices, d1_slices, all_slices, d0_units, d1_ref_text, silence_audio


def score_asr_rows(rows: list[dict], d0_units: list[dict], d1_ref_text: str) -> dict:
    d0_rows = [r for r in rows if r["clip"] != "d1-ep01-45s"]
    d1_rows = [r for r in rows if r["clip"] == "d1-ep01-45s"]
    d0_cer = score_d0_cer(d0_rows, d0_units)
    d1_cer = compute_cer("".join(r["text_ja"] for r in d1_rows), d1_ref_text, strip_speakers=True)
    return {"d0_cer": d0_cer, "d1_cer": d1_cer}


__all__ = [
    "CACHE_ROOT",
    "D1_PCM",
    "QWEN3_ASR_DIR",
    "QWEN3_MT_DIR",
    "RATE",
    "REPO_ROOT",
    "SILENCE_PCM",
    "VAD_PATH",
    "load_frozen_slices",
    "percentile",
    "rss_mib",
    "score_asr_rows",
]
