"""Inspect and score short P0 runs with fixed cue references and reviewed event IDs."""

import argparse
import json
from pathlib import Path
import re

from review import CSJ_REFERENCES, emissions, percentile


def events(run, clip):
    path = run / f"{clip}.zh.jsonl"
    if path.exists():
        return [json.loads(line) for line in path.read_text().splitlines()]
    return emissions(run / f"{clip}.jsonl")


def score(clip, refs, verdicts, output):
    if set(verdicts) != {cue[0] for cue in refs}:
        raise ValueError(f"Review must cover every cue in {clip}")
    rows = []
    for group, start, end, text in refs:
        event_id = verdicts[group]
        row = dict(group=group, start_s=start, end_s=end, reference=text, event_id=event_id)
        if event_id is not None:
            event = output[event_id - 1]
            if event["id"] != event_id or not start - 0.5 <= event["t"] <= end + 15:
                raise ValueError(f"{clip} cue {group}: invalid event {event}")
            row["received_s"] = round(event["t"], 6)
            row["delay_s"] = round(max(0, event["t"] - end), 6)
        rows.append(row)
    delays = [row["delay_s"] for row in rows if "delay_s" in row]
    return {
        "first_chinese_s": next((round(row["t"], 3) for row in output
                                 if re.search(r"[\u3400-\u9fff]", row["zh"])
                                 and not re.search(r"[\u3040-\u30ff]", row["zh"])), None),
        "first_reviewed_chinese_s": min((row["received_s"] for row in rows if "received_s" in row), default=None),
        "references": len(rows),
        "usable_in_5s": sum(delay <= 5 for delay in delays),
        "usable_in_15s": len(delays),
        "missing_in_15s": len(rows) - len(delays),
        "p95_s": percentile(delays, .95),
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--events", nargs=2, metavar=("RUN", "CLIP"))
    parser.add_argument("--review", type=Path)
    args = parser.parse_args()
    if args.events:
        run, clip = args.events
        for row in events(args.root / run, clip):
            print(f'{row["id"]:>3} {row["t"]:>7.3f} [{row["start"]:.2f}–{row["end"]:.2f}] {row["zh"]}')
        return
    if not args.review:
        parser.error("Use --events or --review")
    review = json.loads(args.review.read_text())
    refs = {"csj-task": CSJ_REFERENCES["csj-task"], **review["other_references"]}
    result = {
        run: {clip: score(clip, refs[clip], judgment, events(args.root / run, clip))
              for clip, judgment in clips.items()}
        for run, clips in review["verdicts"].items()
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
