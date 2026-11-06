import json
from pathlib import Path

from subtitle_service.backend import Caption, Warning
from subtitle_service.whisper import Snapshot

def line(start, end, text, speaker=0):
    return {"start": f"0:00:{start:05.2f}", "end": f"0:00:{end:05.2f}",
            "translation": text, "speaker": speaker}


def test_append_prune_repeat_and_rewrite():
    state = Snapshot()
    assert state.update([line(0, 1, "我这边有照片。")], 16000) == [Caption(0, 16000, "我这边有照片。")]
    assert state.update([line(0, 2, "我这边有照片。 我的名字")], 32000) == [Caption(0, 32000, "我的名字")]
    assert state.update([line(1, 2, "我的名字")], 32000) == []
    assert state.update([line(1, 2, "翻译被改写")], 32000) == [Warning("adapter_unaligned")]
    assert state.update([line(2, 3, "我这边有照片。")], 48000) == [Caption(32000, 48000, "我这边有照片。")]
    assert state.update([line(2, 3, "我这边有照片。"), line(3, 4, "还有姓名")], 64000) == [
        Caption(48000, 64000, "还有姓名"),
    ]
    assert state.update([line(1, 2, "旧的内容")], 64000) == [Warning("adapter_unaligned")]


def test_real_snapshots_keep_first_photo_and_ignore_repeated_frames():
    state = Snapshot()
    # Keep the translation fields and accepted sample clock from the CSJ P0 replay.
    path = Path(__file__).with_name("fixtures") / "whisper-csj-task.jsonl"
    captions = []
    for raw in path.read_text().splitlines():
        record = json.loads(raw)
        lines, accepted = record["lines"], record["accepted_samples"]
        events = state.update(lines, accepted)
        captions.extend(item for item in events if isinstance(item, Caption))
        assert not any(isinstance(item, Caption) for item in state.update(lines, accepted))
    assert sum("照片" in item.text_zh for item in captions) == 1
    assert [item.end_sample for item in captions] == sorted(item.end_sample for item in captions)
