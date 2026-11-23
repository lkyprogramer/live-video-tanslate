"""Prepare frozen D0 and D1 benchmark inputs for Tier 2 ASR + Smart Turn v3 evaluation."""

import json
import re
from pathlib import Path

from subtitle_service.config import load_settings
from subtitle_service.local_pipeline import RATE, ReazonRecognizer, vad_segments

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "p0/evidence/tier2-asr-20261123"
D0_DIR = REPO_ROOT / "p0/evidence/n0-next"
D1_PCM = REPO_ROOT / "p0/evidence/d1-dialogue/first45s-control.pcm"
D1_SRT = Path("/Users/luo/Movies/重启人生/Brush.Up.Life.EP01.1080p.HULU.WEB-DL.AAC2.0.H.264-MagicStar.srt")


def parse_srt_cues(srt_path: Path, cue_ids: set[int]) -> list[dict]:
    raw = srt_path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\r?\n+", raw.strip())
    cues = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3 or not lines[0].isdigit():
            continue
        cid = int(lines[0])
        if cid not in cue_ids:
            continue
        text = " ".join(lines[2:])
        cues.append({"cue_id": cid, "timecode": lines[1], "text_ja": text})
    return cues


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. D0: copy/reference frozen 62 slices + 34 units
    slices = [json.loads(ln) for ln in (D0_DIR / "slices.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    units = [json.loads(ln) for ln in (D0_DIR / "final-references/units.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    for row in slices:
        # Ensure pcm_path points to main repo p0/fixtures
        p = Path(row["pcm_path"])
        if not p.exists():
            row["pcm_path"] = str(REPO_ROOT / "p0/fixtures" / p.name)
    (OUT_DIR / "d0_slices.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in slices) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "d0_units.jsonl").write_text(
        "\n".join(json.dumps(u, ensure_ascii=False) for u in units) + "\n", encoding="utf-8"
    )

    # 2. D1: parse SRT cues 75..93 and run Silero v4 + Reazon k2-v2 baseline
    cues = parse_srt_cues(D1_SRT, set(range(75, 94)))
    (OUT_DIR / "d1_srt_cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    import numpy as np
    import time

    raw_d1 = D1_PCM.read_bytes()
    audio_d1 = np.frombuffer(raw_d1, dtype="<i2").astype(np.float32) / 32768.0

    # Load default profile + model paths from preserved D1 locks
    locks_path = REPO_ROOT / "p0/evidence/d1-dialogue/models.json"
    locks = json.loads(locks_path.read_text())
    paths = {row["id"]: Path(row["local_path"]) for row in locks["models"]}
    profile = load_settings(REPO_ROOT / "p0/evidence/d1-dialogue/profiles.toml").profiles["reazon-hy"]

    recognizer = ReazonRecognizer(paths["reazon-b0"], paths["silero-v4-b0"], profile.asr)
    vad = recognizer.new_vad()

    def decode_reazon(samples):
        stream = recognizer.model.create_stream()
        stream.accept_waveform(RATE, samples)
        recognizer.model.decode_stream(stream)
        return stream.result.text.strip()

    # Feed 200ms packets (3200 samples = 6400 bytes) identically to streaming runtime
    pkt_bytes = 6400
    d1_slices = []
    reazon_d1_rows = []
    seg_id = 0
    for off in range(0, len(raw_d1), pkt_bytes):
        chunk = raw_d1[off : off + pkt_bytes]
        for start, samples in vad_segments(vad, chunk):
            seg_id += 1
            end = start + len(samples)
            d1_slices.append(
                {
                    "clip": "d1-ep01-45s",
                    "segment_id": seg_id,
                    "pcm_path": str(D1_PCM),
                    "start_sample": int(start),
                    "end_sample": int(end),
                }
            )
            t0 = time.perf_counter()
            text_ja = decode_reazon(samples)
            dt = time.perf_counter() - t0
            reazon_d1_rows.append(
                {
                    "clip": "d1-ep01-45s",
                    "segment_id": seg_id,
                    "pcm_path": str(D1_PCM),
                    "start_sample": int(start),
                    "end_sample": int(end),
                    "text_ja": text_ja,
                    "decode_s": round(dt, 4),
                }
            )
    for start, samples in vad_segments(vad, None):
        seg_id += 1
        end = start + len(samples)
        d1_slices.append(
            {
                "clip": "d1-ep01-45s",
                "segment_id": seg_id,
                "pcm_path": str(D1_PCM),
                "start_sample": int(start),
                "end_sample": int(end),
            }
        )
        t0 = time.perf_counter()
        text_ja = decode_reazon(samples)
        dt = time.perf_counter() - t0
        reazon_d1_rows.append(
            {
                "clip": "d1-ep01-45s",
                "segment_id": seg_id,
                "pcm_path": str(D1_PCM),
                "start_sample": int(start),
                "end_sample": int(end),
                "text_ja": text_ja,
                "decode_s": round(dt, 4),
            }
        )

    (OUT_DIR / "d1_silero_slices.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in d1_slices) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "reazon_d1.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in reazon_d1_rows) + "\n", encoding="utf-8"
    )
    print(f"Prepared D0 slices={len(slices)} units={len(units)}; D1 cues={len(cues)} silero_slices={len(d1_slices)}")


if __name__ == "__main__":
    main()
