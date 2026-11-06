"""Read WLK full snapshots, assist semantic review, and score the reviewed events."""

import argparse
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
import unicodedata


P0 = Path(__file__).resolve().parent
GROUPS = {
    "wmf-01": "2-4 5-6 9 10-11 12 13-14 16 17 18 19-20 21-24 25-26 27-28 29-31 32 33 34-37 38-39 40-43 44-46 47-48 49",
    "wmf-02": "51-53 54-55 56 57-59 60-62 63 64 65-67 68-69 71-73 75-77 78-80 81-83 84 85-87 88-90 91-94",
    "wmf-03": "95-99 100-101 102-103 104 105-106 107-110 111-114 115 117-118 120-121 122-123 124-126 127 128-130",
    "wmf-04": "131-132 133-135 136 137-138 139 140-141 142-143 144-145 146-147 148-152 153-155 156-157 158-160 161-165 166-167 168 169 170-172 173 174-175 176-178 179",
    "wmf-05": "180-182 184-186 187-188 189 190-192 193-195 196-197 198-201 203-206 207-209 211-213 214 215-216",
    "wmf-06": "217-219 220-222 223-225 227-229 230-231 232-233 235-237 239-241 242-245 246 247-248 249 250-251 252 253-254 255-256 257 258-260",
}
CSJ_REFERENCES = {
    "csj-aps": [
        ("2-6", 0.525, 6.126, "先简要回顾副语言信息"),
        ("7-10", 6.545, 20.801, "说话传递语言信息，也同时传达副语言和非语言信息"),
        ("11-15", 21.398, 34.695, "副语言信息能被说话者有意控制，但连续变化所以难以分类"),
    ],
    "csj-reproduction": [
        ("1", 0.296, 6.742, "先简要回顾副语言信息"),
        ("2-3", 7.365, 21.499, "讲话传递语言信息，同时也传递副语言和非语言信息"),
        ("4", 22.364, 30.403, "藤崎老师提出三分法，副语言信息是有意识控制的"),
        ("5-7", 30.916, 40.808, "说话者控制发出，和语言信息不同，会连续变化而难以归类"),
    ],
    "csj-task": [
        ("1", 0.314, 0.946, "我打开看看"),
        ("5", 3.971, 5.436, "我这边有照片"),
        ("6", 6.080, 11.018, "我这边有名字和一句话"),
        ("9", 11.951, 14.537, "有很多人我不认识"),
        ("12-15", 15.866, 19.721, "我从最上面开始读名字"),
        ("20", 22.308, 28.617, "是烹饪研究者，戴眼镜，短发"),
        ("30", 33.130, 34.861, "雷米的名字用片假名写"),
        ("34-35", 36.998, 40.562, "塞尔吉奥越后是谁，我不知道"),
    ],
}
SHORT_FIRST = {
    "first-photo": (3.971 - 3.84, 5.436 - 3.84, "我这边有照片"),
    "first-strangers": (11.951 - 11.75, 14.537 - 11.75, "有很多人我不认识"),
    "first-europe": (324.0 - 323.8, 326.96 - 323.8, "位于西欧"),
    "first-slides": (0, 699.86 - 697.019, "切换幻灯片"),
}


def seconds(value):
    hours, minutes, tail = value.split(":")
    return 3600 * int(hours) + 60 * int(minutes) + float(tail.replace(",", "."))


def references(suite):
    for clip, cues in CSJ_REFERENCES.items():
        for group, start, end, text in cues:
            assert 0 <= start < end <= suite[clip]["duration"] + 0.03, (clip, group, end)
            yield clip, group, start, end, text

    srt = (P0 / "fixtures/source/wmf-zh-hans.srt").read_text().strip()
    cues = {}
    for block in re.split(r"\n\s*\n", srt):
        index, bounds, *text = block.splitlines()
        start, end = map(seconds, bounds.split(" --> "))
        cues[int(index)] = (start, end, " ".join(text))

    for clip, groups in GROUPS.items():
        offset = suite[clip]["source_offset"]
        for group in groups.split():
            ids = list(map(int, group.split("-")))
            start, end = cues[ids[0]][0] - offset, cues[ids[-1]][1] - offset
            assert 0 <= start < end <= suite[clip]["duration"] + 0.03, (clip, group, start, end)
            text = " ".join(cues[i][2] for i in range(ids[0], ids[-1] + 1) if i in cues).strip()
            yield clip, group, start, end, text


def emissions(trace):
    previous = ""
    events = []
    for line in trace.read_text().splitlines():
        frame = json.loads(line)
        snapshot = frame.get("event", {})
        if snapshot.get("type") == "caption":
            events.append({
                "id": snapshot["seq"], "t": frame["t"],
                "start": snapshot["start_sample"] / 16000,
                "end": snapshot["end_sample"] / 16000,
                "zh": snapshot["text_zh"],
            })
            continue
        if "lines" not in snapshot:
            continue
        lines = []
        pos = 0
        for item in snapshot["lines"]:
            zh = " ".join((item.get("translation") or "").split())
            if item.get("speaker") == -2 or not zh:
                continue
            lines.append((pos, pos + len(zh), zh, item))
            pos += len(zh) + 1
        current = " ".join(line[2] for line in lines)
        for lo, hi in new_spans(previous, current):
            for start, end, _, item in lines:
                if end <= lo or start >= hi:
                    continue
                delta = current[max(start, lo):min(end, hi)].strip()
                if delta:
                    events.append({
                        "id": len(events) + 1,
                        "t": frame["t"],
                        "start": seconds(item["start"]),
                        "end": seconds(item["end"]),
                        "zh": delta,
                    })
        previous = current
    return events


def new_spans(old, new):
    if old.endswith(new):
        return []
    if new.startswith(old):
        return [(len(old), len(new))]
    # Pruning can change the source line start on every snapshot. Its remaining
    # translation is still an ordered suffix and must only count once.
    prefix = new[:16]
    for match in re.finditer(re.escape(prefix), old):
        overlap = old[match.start():]
        if new.startswith(overlap):
            return [(len(overlap), len(new))]
    words = list(re.finditer(r"\S+", new))
    edits = SequenceMatcher(a=old.split(), b=[word.group() for word in words], autojunk=False)
    return [(words[start].start(), words[end - 1].end()) for op, _, _, start, end in edits.get_opcodes()
            if op in ("insert", "replace")]


def normalized(text):
    return "".join(c.lower() for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def candidates(refs, streams, clip, limit):
    stream = streams[clip]
    for _, group, start, end, ref in (item for item in refs if item[0] == clip):
        target = normalized(ref)
        window = [ev for ev in stream if start - 0.5 <= ev["t"] <= end + 15]

        def rank(ev):
            out = normalized(ev["zh"])
            match = sum(b.size for b in SequenceMatcher(a=target, b=out, autojunk=False).get_matching_blocks())
            return match / max(1, min(len(target), len(out))) - 0.018 * abs(ev["t"] - end)

        chosen = sorted(sorted(window, key=rank, reverse=True)[:limit], key=lambda ev: ev["id"])
        print(f"{group:>7} {start:6.2f}–{end:6.2f} {ref}")
        for event in chosen:
            print(f"   {event['id']:>3} {event['t']-end:+6.2f} {event['zh']}")


def percentile(values, fraction):
    values = sorted(values)
    return round(values[math.ceil(fraction * len(values)) - 1], 3) if values else None


def score(refs, streams, verdict, runs):
    delays = []
    first = {}
    first_refs = {}
    rows = []
    for clip, group, start, end, _ in refs:
        first_refs.setdefault(clip, (start, end))
        chosen = verdict[clip][group]
        if chosen is None:
            rows.append({"clip": clip, "group": group, "ref_end_s": round(end, 3), "event_id": None})
            continue
        event = streams[clip][chosen - 1]
        assert event["id"] == chosen and start - 0.5 <= event["t"] <= end + 15, (clip, group, event)
        delay = event["t"] - end
        delays.append(delay)
        first[clip] = min(first.get(clip, float("inf")), event["t"])
        rows.append({"clip": clip, "group": group, "ref_end_s": round(end, 3), "event_id": chosen,
                     "received_s": event["t"], "raw_delay_s": round(delay, 6)})
    counts = len(refs)
    first_from_ref = {clip: round(timestamp - first_refs[clip][0], 3) for clip, timestamp in first.items()}
    short_first = {}
    for clip, (start, _, _) in SHORT_FIRST.items():
        chosen = verdict[clip]["first"]
        short_first[clip] = round(streams[clip][chosen - 1]["t"] - start, 3) if chosen else None
    return {
        "model": runs.get("model", runs.get("profile")),
        "ref_count": counts,
        "usable_in_15s": len(delays),
        "usable_in_5s": sum(delay <= 5 for delay in delays),
        "missing_or_unusable_in_15s": counts - len(delays),
        "missing_late_or_unusable_in_5s": counts - sum(delay <= 5 for delay in delays),
        "p50_sentence_end_s": percentile([max(0, d) for d in delays], 0.5),
        "p95_sentence_end_s": percentile([max(0, d) for d in delays], 0.95),
        "early_count": sum(delay < 0 for delay in delays),
        "first_chinese_raw_s": {clip: next((e["t"] for e in events if re.search(r"[\u3400-\u9fff]", e["zh"]) and
                                            not re.search(r"[\u3040-\u30ff]", e["zh"])), None)
                                for clip, events in streams.items()},
        "first_reviewed_from_audio_start_s": first,
        "first_reviewed_from_first_ref_start_s": first_from_ref,
        "p90_first_reviewed_from_first_ref_start_s": percentile(first_from_ref.values(), 0.9),
        "short_first_from_reference_start_s": short_first,
        "p90_short_first_s": percentile(list(short_first.values()), 0.9) if all(v is not None for v in short_first.values()) else None,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--clip", choices=tuple(CSJ_REFERENCES) + tuple(GROUPS) + tuple(SHORT_FIRST))
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--events", action="store_true")
    parser.add_argument("--verdict", type=Path)
    args = parser.parse_args()

    suite = {item["id"]: item for item in json.loads((P0 / "fixtures/suite.json").read_text())}
    refs = list(references(suite))
    traces = args.run.glob("*.jsonl") if not args.clip else [args.run / f"{args.clip}.jsonl"]
    streams = {trace.stem: emissions(trace) for trace in sorted(traces)}
    if args.events:
        for clip, events in streams.items():
            for event in events:
                print(f"{clip} {event['id']:>3} {event['t']:6.2f} [{event['start']:.2f}–{event['end']:.2f}] {event['zh']}")
    elif args.clip:
        if args.clip in SHORT_FIRST:
            refs = [(args.clip, "first", *SHORT_FIRST[args.clip])]
        candidates(refs, streams, args.clip, args.candidates)
    elif args.verdict:
        runs = json.loads((args.run / "run.json").read_text())
        verdict = json.loads(args.verdict.read_text())
        print(json.dumps(score(refs, streams, verdict, runs), ensure_ascii=False, indent=2))
    else:
        parser.error("Use --clip, --events, or --verdict")


if __name__ == "__main__":
    main()
