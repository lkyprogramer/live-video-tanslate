"""Freeze v1 VAD slices and run isolated Japanese ASR comparisons on them."""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import time
import unicodedata

import numpy as np
import sherpa_onnx

from monitor import mark
from review import P0, percentile, references, seconds
from subtitle_service.app import FRAME_BYTES
from subtitle_service.local_pipeline import MODEL_FILES, RATE, VAD_SHA, silero_config, vad_segments


def read_rows(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source]


def write_rows(path, rows):
    with path.open("x", encoding="utf-8", buffering=1) as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")


def japanese_srt(path, offset):
    rows = {}
    for block in re.split(r"\n\s*\n", path.read_text().strip()):
        label, timing, *parts = block.splitlines()
        start, end = map(seconds, timing.split(" --> "))
        rows[int(label)] = (start - offset, end - offset, "".join(parts).strip())
    return rows


def csj_transcript(path):
    rows = {}
    current = None
    for line in path.read_text().splitlines():
        head = re.match(r"^(\d+)\s+([\d.]+)-([\d.]+)\s+[LR]:", line)
        if head:
            current = int(head[1])
            rows[current] = [float(head[2]), float(head[3]), ""]
        elif current is not None and "&" in line and not line.startswith("%"):
            word = line.partition("&")[0].strip()
            word = re.sub(r"\((?:F|D)\s+[^()]*\)", "", word)
            word = re.sub(r"\((?:[A-Z?]+|笑)\s+", "", word).replace(")", "")
            word = re.sub(r"<[^>]+>", "", word)
            rows[current][2] += word
    return rows


def prepare(run, cache):
    frozen = json.loads((run / "frozen.json").read_text())
    suite = {c["id"]: c for c in json.loads((P0 / "fixtures/suite.json").read_text())}
    transcribed = {}
    for clip in frozen["scored_clips"]:
        source = P0 / "fixtures/source"
        transcribed[clip] = (csj_transcript(source / f"{clip}.txt") if clip.startswith("csj-")
                             else japanese_srt(source / "wmf-ja.srt", suite[clip]["source_offset"]))
    units = []
    for clip, group, start, end, zh in references(suite):
        if clip not in transcribed:
            continue
        if clip.startswith("csj-"):
            first, _, last = group.partition("-")
            records = [transcribed[clip].get(n) for n in range(int(first), int(last or first) + 1)]
        else:
            # The Japanese and Chinese subtitle files split and number their cues independently.
            # The Chinese 153–155 ends before the Japanese clause describing the committee.
            source_end = 126.04 if group == "153-155" else end
            records = [r for r in transcribed[clip].values()
                       if min(source_end, r[1]) - max(start, r[0]) > .02]
            if group == "168":
                records = [(a, b, ja.removesuffix("起草の").rstrip()) for a, b, ja in records]
            elif group == "169":
                records = [(a, b, "起草の" + ja) for a, b, ja in records]
        joiner = "。" if clip.startswith("csj-") else " "
        units.append({"clip": clip, "group": group, "start_sample": round(start * RATE),
                      "end_sample": round(end * RATE), "reference_zh": zh,
                      "source_ja": joiner.join(r[2] for r in records if r and r[2]),
                      "reference_status": "source_transcript" if records and all(records) else "incomplete_source_transcript"})
    write_rows(run / "units.jsonl", units)

    vad_path = cache / "reazonspeech-assets/silero_vad.onnx"
    assert sha256(vad_path.read_bytes()).hexdigest() == VAD_SHA
    config = silero_config(vad_path, frozen["vad_pause_ms"])
    cuts = []
    for clip in frozen["inputs"]:
        path = P0 / "fixtures" / clip["file"]
        audio = path.read_bytes()
        if sha256(audio).hexdigest() != clip["sha256"]:
            raise ValueError(f"PCM checksum mismatch for {clip['id']}")
        vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)
        frames = [audio[i:i + FRAME_BYTES] for i in range(0, len(audio), FRAME_BYTES)]
        accepted = 0
        segment_id = 0
        for frame in [*frames, None]:
            accepted += len(frame) // 2 if frame is not None else 0
            for start, samples in vad_segments(vad, frame):
                segment_id += 1
                end = start + len(samples)
                segment = np.frombuffer(audio[2 * start:2 * end], dtype="<i2").astype(np.float32) / 32768
                if not np.array_equal(segment, samples):
                    raise ValueError(f"VAD bytes do not match source: {clip['id']} {start}-{end}")
                # A timestamped transcript is safe as a slice reference only when it has no cut cues.
                overlapping = [r for r in transcribed.get(clip["id"], {}).values()
                               if r[0] * RATE < end and r[1] * RATE > start and r[2]]
                boundary_uncertain = any(r[0] * RATE < start or r[1] * RATE > end for r in overlapping)
                cuts.append({"clip": clip["id"], "segment_id": segment_id, "start_sample": start,
                             "end_sample": end, "released_at_sample": accepted,
                             "pcm_path": str(path), "flush": frame is None,
                             "source_ja": "。".join(r[2] for r in overlapping) if not boundary_uncertain else "",
                             "reference_status": "boundary_uncertain" if boundary_uncertain else
                             "source_transcript" if overlapping else "no_source_reference"})
    write_rows(run / "slices.jsonl", cuts)
    print(f"Frozen {len(cuts)} speech cuts and {len(units)} scored units")


def decode(run, cache, candidate, threads, swift_binary, swift_weights, smoke):
    frozen = json.loads((run / "frozen.json").read_text())
    source = run / "slices.jsonl"
    rows = read_rows(source)
    if smoke:
        rows = [next(r for r in rows if r["clip"] == name) for name in ("csj-task", "csj-reproduction")]
        rows.append({"clip": "silence", "segment_id": 1, "start_sample": 0, "end_sample": RATE,
                     "pcm_path": str(P0 / "fixtures/silence.pcm")})
        source = run / f"{candidate}-smoke-input.jsonl"
        write_rows(source, rows)
    output = run / f"{candidate}{'-smoke' if smoke else ''}.jsonl"
    if candidate == "parakeet":
        subprocess.run([str(swift_binary), str(swift_weights), str(source), str(output)], check=True)
    else:
        if candidate == "haas":
            repo, rev = "oboroge0--Haas-ja-v1.0-onnx-quantized", frozen["haas_revision"]
            files = "encoder.quantized.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"
            kwargs = {"model_type": "nemo_transducer", "feature_dim": 128}
        else:
            repo, rev = "reazon-research--reazonspeech-k2-v2", frozen["reazon_revision"]
            files = MODEL_FILES
            kwargs = {}
            if candidate == "reazon-beam":
                kwargs = {"decoding_method": "modified_beam_search", "max_active_paths": 4}
            elif candidate == "reazon-fp32":
                files = tuple(name.replace(".int8.onnx", ".onnx") for name in MODEL_FILES)
        snapshot = cache / "hub" / f"models--{repo}" / "snapshots" / rev
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            **{key: str(snapshot / name) for key, name in zip(("encoder", "decoder", "joiner", "tokens"), files)},
            sample_rate=RATE, provider="cpu", num_threads=threads, **kwargs,
        )

        def transcribe(samples):
            stream = recognizer.create_stream()
            stream.accept_waveform(RATE, samples)
            recognizer.decode_stream(stream)
            return stream.result.text.strip()

        transcribe(np.zeros(RATE, dtype=np.float32))
        mark(candidate, "start")
        pcm = {}
        with output.open("x", encoding="utf-8", buffering=1) as target:
            for row in rows:
                path = row["pcm_path"]
                if path not in pcm:
                    pcm[path] = np.frombuffer(Path(path).read_bytes(), dtype="<i2")
                segment = pcm[path][row["start_sample"]:row["end_sample"]].astype(np.float32) / 32768
                start = time.perf_counter()
                text = transcribe(segment)
                record = row | {"text_ja": text, "decode_s": time.perf_counter() - start}
                target.write(json.dumps(record, ensure_ascii=False) + "\n")
        mark(candidate, "end")
    results = read_rows(output)
    assert len(results) == len(rows)
    print(json.dumps({"asr": candidate, "slices": len(results), "nonempty": sum(bool(r["text_ja"]) for r in results),
                      "p50_decode_s": percentile([r["decode_s"] for r in results], .50),
                      "p95_decode_s": percentile([r["decode_s"] for r in results], .95),
                      "rtf": sum(r["decode_s"] for r in results) * RATE /
                      sum(r["end_sample"] - r["start_sample"] for r in results)}, ensure_ascii=False))
    if smoke:
        for row in results:
            print(row["clip"], repr(row["text_ja"]))


def normalized(value):
    return "".join(ch.lower() for ch in unicodedata.normalize("NFKC", value) if ch.isalnum())


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for index, x in enumerate(a, 1):
        row = [index]
        for j, y in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = row
    return previous[-1]


def compare(run, candidates):
    slices = read_rows(run / "slices.jsonl")
    for name in candidates:
        outputs = read_rows(run / f"{name}.jsonl")
        scores = defaultdict(lambda: {"slices": 0, "raw_characters": 0, "raw_errors": 0,
                                      "normalized_characters": 0, "normalized_errors": 0})
        for ref, got in zip(slices, outputs, strict=True):
            assert all(ref[k] == got[k] for k in ("clip", "segment_id", "start_sample", "end_sample"))
            if not ref["source_ja"]:
                continue
            score = scores[ref["clip"]]
            score["slices"] += 1
            for prefix, convert in (("raw", lambda value: value), ("normalized", normalized)):
                a, b = convert(ref["source_ja"]), convert(got["text_ja"])
                score[f"{prefix}_characters"] += len(a)
                score[f"{prefix}_errors"] += edit_distance(a, b)
        print(json.dumps({"asr": name, "timestamped_slice_cer_counts": scores,
                          "missing_reference_slices": sum(not r["source_ja"] for r in slices)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cache", type=Path, default=P0.parent / ".model-cache")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    asr = commands.add_parser("decode")
    asr.add_argument("candidate", choices=("reazon", "reazon-beam", "reazon-fp32", "haas", "parakeet"))
    asr.add_argument("--threads", type=int, default=2)
    asr.add_argument("--smoke", action="store_true")
    asr.add_argument("--swift-binary", type=Path)
    asr.add_argument("--swift-weights", type=Path)
    scoring = commands.add_parser("compare")
    scoring.add_argument("candidates", nargs="+")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.run, args.cache)
    elif args.command == "decode":
        if args.candidate == "parakeet" and (not args.swift_binary or not args.swift_weights):
            parser.error("parakeet needs --swift-binary and --swift-weights")
        decode(args.run, args.cache, args.candidate, args.threads, args.swift_binary, args.swift_weights, args.smoke)
    else:
        compare(args.run, args.candidates)


if __name__ == "__main__":
    main()
