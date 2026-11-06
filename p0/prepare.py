"""Build the fixed 16 kHz, mono PCM suite from the sources listed in p0.md."""

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent / "fixtures"
SOURCE = ROOT / "source"
SAMPLE_RATE = 16000
VIDEO_CUTS = (0, 276, 528.6, 721, 978, 1155, 1389.7)
CSJ_SAMPLES = ("aps", "reproduction", "sps", "interview_aps", "task")


def main():
    clips = []
    for name in CSJ_SAMPLES:
        clip_id = f"csj-{name}"
        clips.append((clip_id, "speech-corpus", SOURCE / f"{clip_id}.mp3", 0, None))
    clips.append(("offsiderule", "video", SOURCE / "offsiderule.webm", 0, None))
    for number, (start, end) in enumerate(zip(VIDEO_CUTS, VIDEO_CUTS[1:]), 1):
        clips.append((f"wmf-{number:02}", "video", SOURCE / "wmf-mcdc.webm", start, end - start))
    clips.extend([
        ("first-photo", "short-first", SOURCE / "csj-task.mp3", 3.84, 7),
        ("first-strangers", "short-first", SOURCE / "csj-task.mp3", 11.75, 7),
        ("first-europe", "short-first", SOURCE / "wmf-mcdc.webm", 323.8, 8),
        ("first-slides", "short-first", SOURCE / "wmf-mcdc.webm", 697.019, 8),
    ])

    suite = []
    for clip_id, kind, source, start, duration in clips:
        output = ROOT / f"{clip_id}.pcm"
        command = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(source)]
        if duration is not None:
            command += ["-t", str(duration)]
        subprocess.run(
            command + ["-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", str(output)],
            check=True,
        )
        suite.append(record(clip_id, kind, output, start))

    silence = ROOT / "silence.pcm"
    silence.write_bytes(bytes(SAMPLE_RATE * 2 * 10))
    suite.append(record("silence", "negative", silence, 0))
    (ROOT / "suite.json").write_text(json.dumps(suite, indent=2) + "\n")
    print(f"{len(suite)} clips, {sum(item['duration'] for item in suite):.2f} seconds")


def record(clip_id, kind, output, start):
    audio = output.read_bytes()
    if not audio or len(audio) % 2:
        raise ValueError(f"Invalid PCM byte count for {clip_id}")
    return {
        "id": clip_id,
        "kind": kind,
        "file": output.name,
        "source_offset": start,
        "duration": len(audio) / (SAMPLE_RATE * 2),
        "sha256": hashlib.sha256(audio).hexdigest(),
    }


if __name__ == "__main__":
    main()
