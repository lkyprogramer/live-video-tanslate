"""Run Kotoba in one warm whisper.cpp process on the frozen VAD slices."""

import argparse
from collections import defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import subprocess
import time
import wave

from monitor import mark
from next_asr import read_rows, write_rows
from review import P0, percentile


def wav(path, data, rate):
    with wave.open(str(path), "wb") as target:
        target.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        target.writeframes(data)


def prepare(baseline, run):
    run.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((baseline / "frozen.json").read_text())
    rate = manifest["sample_rate"]
    source_pcm = {}
    for clip in manifest["inputs"]:
        pcm = (P0 / "fixtures" / clip["file"]).read_bytes()
        assert sha256(pcm).hexdigest() == clip["sha256"]
        source_pcm[clip["id"]] = pcm
    cuts = defaultdict(list)
    audio_dir = run / "wav"
    audio_dir.mkdir()
    rows = read_rows(baseline / "slices.jsonl")
    for row in rows:
        path = audio_dir / f"{row['clip']}-{row['segment_id']:03d}.wav"
        pcm = source_pcm[row["clip"]]
        start, end = row["start_sample"], row["end_sample"]
        assert 0 <= start < end <= len(pcm) // 2
        wav(path, pcm[2 * start:2 * end], rate)
        row["wav"] = str(path.relative_to(run))
        cuts[row["clip"]].append(row)
    wav(audio_dir / "silence-1s.wav", source_pcm["silence"][:2 * rate], rate)
    wav(audio_dir / "silence-10s.wav", source_pcm["silence"], rate)
    write_rows(run / "slices.jsonl", rows)
    units = read_rows(baseline / "final-references/units.jsonl")
    write_rows(run / "units.jsonl", units)
    (run / "frozen.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    scores = json.loads((baseline / "offline-semantic.json").read_text())
    missed = {key for key, hit in scores["reazon-m0-11"]["units"].items() if not hit}
    review = []
    sections = []
    annotations = set()
    for unit in units:
        clip, group = unit["clip"], unit["group"]
        key = f"{clip}/{group}"
        if key not in missed:
            continue
        start, end = unit["start_sample"], unit["end_sample"]
        overlapping = [row for row in cuts[clip] if row["start_sample"] < end and row["end_sample"] > start]
        context = audio_dir / f"ref-{clip}-{group}.wav"
        wav(context, source_pcm[clip][2 * max(0, start - rate // 5):2 * (end + rate // 5)], rate)
        review.append(unit | {"original_wav": str(context.relative_to(run)), "slices": overlapping})
        parts = []
        for row in overlapping:
            name = f"{clip}-{row['segment_id']:03d}"
            if name in annotations:
                parts.append(f'<p><a href="#{name}">{name}</a> 已在上方列出。</p>')
                continue
            annotations.add(name)
            a, b = row["start_sample"] / rate, row["end_sample"] / rate
            parts.append(f'<div id="{name}"><label for="text-{name}">{name}，{a:.3f}–{b:.3f} s</label>'
                         f'<audio controls preload="none" src="{escape(row["wav"])}"></audio>'
                         f'<textarea id="text-{name}" data-slice="{name}" rows="2"></textarea></div>')
        slices = "".join(parts) or "<p>该时间范围没有任何 VAD 切片。</p>"
        sections.append(f'<section><h2>{key}</h2>{slices}<details><summary>核对原音频范围</summary>'
                        f'<audio controls preload="none" src="wav/{context.name}"></audio></details></section>')
    write_rows(run / "review-units.jsonl", review)
    (run / "listen.html").write_text("""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>B 路切片复听</title><style>body{font:17px system-ui;max-width:56rem;margin:2rem auto;padding:0 1rem}
section{margin:2rem 0}audio,textarea{display:block;width:100%;max-width:38rem;margin:.5rem 0}
</style><h1>B 路切片复听</h1><p>逐段听原 VAD 切片并输入实际听到的日文。保留被截断的词尾。
可用「[不清]」标注听不清的位置。展开的原音频只用于核对切片之外的语音。</p>
<button id="download">导出日文转写</button>
""" + "\n".join(sections) + """
<script>
document.querySelector('#download').onclick = () => {
  const rows = [...document.querySelectorAll('textarea[data-slice]')]
    .filter(field => field.value.trim())
    .map(field => JSON.stringify({slice: field.dataset.slice, text_ja: field.value.trim()}));
  const url = URL.createObjectURL(new Blob([rows.join('\u005cn') + '\u005cn'], {type: 'application/x-ndjson'}));
  const link = document.createElement('a');
  link.href = url; link.download = 'manual-transcripts.jsonl'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
</script></html>
""", encoding="utf-8")
    print(f"Prepared {len(rows)} VAD slices and {len(review)} review units ({len(annotations)} unique slices)")


def decode(run, cli, model, threads):
    rows = read_rows(run / "slices.jsonl")
    wave_paths = [rows[0]["wav"], *(row["wav"] for row in rows), "wav/silence-1s.wav", "wav/silence-10s.wav"]
    json_dir = run / "whisper-json"
    json_dir.mkdir(exist_ok=False)
    outputs = [json_dir / f"{i:03d}" for i in range(len(wave_paths))]
    command = [str(cli.resolve()), "-m", str(model.resolve()), "-l", "ja", "-t", str(threads),
               "-mc", "0", "-bs", "5", "-tp", "0", "-nf", "-oj"]
    for source, target in zip(wave_paths, outputs, strict=True):
        command.extend(["-f", str(run / source), "-of", str(target)])
    (run / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    started = None
    durations = []
    with (run / "whisper.log").open("x", encoding="utf-8") as log:
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) as proc:
            for line in proc.stderr:
                stamp = time.perf_counter()
                log.write(line)
                if re.match(r"main: processing '", line):
                    started = stamp
                elif re.match(r"output_json: saving output to '", line):
                    if started is None:
                        raise ValueError("Missing Whisper processing marker")
                    durations.append(stamp - started)
                    started = None
                    if len(durations) == 1:
                        mark("kotoba", "start")
                    elif len(durations) == len(rows) + 1:
                        mark("kotoba", "end")
            if proc.wait():
                raise subprocess.CalledProcessError(proc.returncode, command)
    if len(durations) != len(wave_paths):
        raise ValueError(f"Expected {len(wave_paths)} result markers, got {len(durations)}")
    texts = []
    for path in outputs:
        raw = json.loads(path.with_suffix(".json").read_text())
        if raw["result"]["language"] != "ja":
            raise ValueError(f"Unexpected output language in {path}")
        texts.append("".join(item["text"] for item in raw["transcription"]).strip())
    timed = durations[1:-2]
    write_rows(run / "kotoba.jsonl", (row | {"text_ja": text, "decode_s": elapsed}
                                     for row, text, elapsed in zip(rows, texts[1:-2], timed, strict=True)))
    rate = json.loads((run / "frozen.json").read_text())["sample_rate"]
    audio_seconds = sum(row["end_sample"] - row["start_sample"] for row in rows) / rate
    print(json.dumps({"slices": len(rows), "nonempty": sum(bool(value) for value in texts[1:-2]),
                      "p50_decode_s": percentile(timed, .50), "p95_decode_s": percentile(timed, .95),
                      "rtf": sum(timed) / audio_seconds,
                      "raw_silence_1s": texts[-2], "raw_silence_10s": texts[-1]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("baseline", type=Path)
    prep.add_argument("run", type=Path)
    asr = commands.add_parser("decode")
    asr.add_argument("run", type=Path)
    asr.add_argument("cli", type=Path)
    asr.add_argument("model", type=Path)
    asr.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.baseline, args.run)
    else:
        decode(args.run, args.cli, args.model, args.threads)
