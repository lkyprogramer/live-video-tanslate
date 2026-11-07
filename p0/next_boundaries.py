"""Prepare two offline boundary probes without using reference timings."""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

from next_asr import read_rows, write_rows


def prepare(baseline, output):
    manifest = json.loads((baseline / "frozen.json").read_text())
    rate = manifest["sample_rate"]
    frame = manifest["frame_bytes"] // 2
    max_gap = round(rate * .160)
    max_span = 15 * rate
    padding = round(rate * .320)
    originals = defaultdict(list)
    for index, row in enumerate(read_rows(baseline / "slices.jsonl")):
        originals[row["clip"]].append(row | {"mt_seed_index": index, "from_ids": [row["segment_id"]]})

    rows = {"merge160": [], "pad320": []}
    for clip in manifest["inputs"]:
        cuts = originals[clip["id"]]
        if not cuts:
            continue
        audio = Path(cuts[0]["pcm_path"]).read_bytes()
        assert sha256(audio).hexdigest() == clip["sha256"]
        size = len(audio) // 2
        for cut in cuts:
            start = max(0, cut["start_sample"] - padding)
            end = min(size, cut["end_sample"] + padding)
            available = min(size, ((end + frame - 1) // frame) * frame)
            rows["pad320"].append(cut | {
                "start_sample": start, "end_sample": end, "source_ja": "",
                "reference_status": "boundary_probe", "released_at_sample": max(cut["released_at_sample"], available),
                "baseline_release_samples": [cut["released_at_sample"]],
            })

        i = 0
        while i < len(cuts):
            left = cuts[i]
            right = cuts[i + 1] if i + 1 < len(cuts) else None
            if right and 0 <= right["start_sample"] - left["end_sample"] <= max_gap and (
                    right["end_sample"] - left["start_sample"] <= max_span):
                rows["merge160"].append(left | {
                    "end_sample": right["end_sample"], "released_at_sample": right["released_at_sample"],
                    "flush": right["flush"], "source_ja": "", "reference_status": "boundary_probe",
                    "from_ids": left["from_ids"] + right["from_ids"],
                    "baseline_release_samples": [left["released_at_sample"], right["released_at_sample"]],
                })
                i += 2
            else:
                rows["merge160"].append(left | {"baseline_release_samples": [left["released_at_sample"]]})
                i += 1

    for name, cuts in rows.items():
        target = output / name
        target.mkdir(parents=True, exist_ok=False)
        (target / "frozen.json").write_text(json.dumps(manifest | {"boundary_probe": {
            "variant": name, "merge_max_gap_ms": 160, "merge_max_duration_s": 15, "padding_ms": 320,
        }}, ensure_ascii=False, indent=2) + "\n")
        write_rows(target / "slices.jsonl", cuts)
        print(f"{name}: {len(cuts)} cuts, {sum(len(row['from_ids']) == 2 for row in cuts)} merged pairs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    prepare(args.baseline, args.output)
