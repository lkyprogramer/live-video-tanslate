"""Unit tests for A2 draft/final scheduler and v2 online invariants."""
from .a2_probe import A2Session, DRAFT_MIN_INTERVAL_NS, play_a2, validate_v2_events
from .clock import Schedule
from .rolling import DRAIN_NS, RATE, Tokens, VadUpdate

NS = 1_000_000_000


class SyntheticA2:
    def __init__(self, audio, update, *, asr_fn=None, mt_fn=None):
        self.audio = audio
        self.update = update
        self.asr_fn = asr_fn
        self.mt_fn = mt_fn
        self.vad_end = 0
        self.mt_calls = []

    def vad(self, payload):
        accepted, frame = payload
        if frame is not None:
            self.vad_end = accepted
        return self.update(accepted, frame is None)

    def asr(self, window):
        if self.asr_fn is not None:
            return self.asr_fn(window)
        return Tokens("テストです", ("テスト", "です"), (window.start, min(window.end, window.start + 8000)))

    def mt(self, job):
        self.mt_calls.append((job["kind"], job["text_ja"], job["call_seed"]))
        if self.mt_fn is not None:
            return self.mt_fn(job)
        raw = "测试" + job["text_ja"]
        return raw, raw


def run(audio, update, *, asr_fn=None, mt_fn=None, timing=lambda *_: 1_000_000, configure=None):
    trace = []
    workers = SyntheticA2(audio, update, asr_fn=asr_fn, mt_fn=mt_fn)
    stream = play_a2(
        audio, workers,
        lambda event, **data: trace.append(dict(event=event, session_id="s1", epoch=0, t_ns=len(trace), **data)),
        elapsed_override=timing, configure=configure, tick_ns=400_000_000, roll_left=0,
    )
    return stream, trace, workers


def test_draft_coalescing_interval_and_unchanged_skip():
    def vad(accepted, eof):
        if eof:
            return A2Vad(None, ((0, accepted),))
        return VadUpdate(0, ())

    def asr_fn(window):
        if window.end < 2 * RATE:
            cands = [("最初の", 0), ("文", 8000)]
        else:
            cands = [("最初の", 0), ("文章", 8000), ("です", 20000)]
        pairs = [(t, a) for t, a in cands if window.start <= a <= window.end]
        return Tokens("".join(t for t, _ in pairs), tuple(t for t, _ in pairs), tuple(a for _, a in pairs))

    audio = b"\x01\x00" * (4 * RATE)
    stream, trace, workers = run(
        audio,
        lambda a, e: VadUpdate(None, ((0, a),)) if e else VadUpdate(0, ()),
        asr_fn=asr_fn,
    )
    assert stream.status == "finished"
    assert validate_v2_events(trace) == []
    drafts = [c for c in stream.captions if c["state"] == "draft"]
    finals = [c for c in stream.captions if c["state"] == "final"]
    assert len(finals) == 1
    assert len(drafts) >= 1
    # Min 800ms interval between draft submissions
    draft_starts = [e["schedule_ns"] for e in trace if e["event"] == "mt_start" and e["kind"] == "draft"]
    for a, b in zip(draft_starts, draft_starts[1:]):
        assert b - a >= DRAFT_MIN_INTERVAL_NS


def test_final_priority_and_stale_inflight_draft_discarded():
    # ASR emits stable prefix at 1.2s, draft MT starts and takes 1.5s;
    # meanwhile VAD releases final at 1.6s -> draft must be discarded as stale, then final runs.
    def vad(accepted, eof):
        if accepted == int(1.6 * RATE):
            return VadUpdate(None, ((0, accepted),))
        return VadUpdate(0 if accepted < int(1.6 * RATE) else None, ())

    def asr_fn(window):
        cands = [("安定", 0), ("テスト", 4000), ("文", 8000)]
        pairs = [(t, a) for t, a in cands if window.start <= a <= window.end]
        return Tokens("".join(t for t, _ in pairs), tuple(t for t, _ in pairs), tuple(a for _, a in pairs))

    def timing(lane, payload):
        if lane == "mt" and payload["kind"] == "draft":
            return 1_500_000_000
        if lane == "mt" and payload["kind"] == "final":
            return 100_000_000
        return 10_000_000

    audio = b"\x02\x00" * (2 * RATE)
    stream, trace, _ = run(audio, vad, asr_fn=asr_fn, timing=timing)
    assert stream.status == "finished"
    assert validate_v2_events(trace) == []
    assert any(e["event"] == "draft_discarded_stale" for e in trace)
    assert [c["state"] for c in stream.captions] == ["final"]


def test_invalid_final_withdraws_emitted_draft():
    def vad(accepted, eof):
        if eof:
            return VadUpdate(None, ((0, accepted),))
        return VadUpdate(0, ())

    def asr_fn(window):
        cands = [("安定", 0), ("テスト", 4000), ("文", 8000)]
        pairs = [(t, a) for t, a in cands if window.start <= a <= window.end]
        return Tokens("".join(t for t, _ in pairs), tuple(t for t, _ in pairs), tuple(a for _, a in pairs))

    def mt_fn(job):
        if job["kind"] == "draft":
            return "有效中文草稿", "有效中文草稿"
        return "invalid english only", None

    audio = b"\x03\x00" * (3 * RATE)
    stream, trace, _ = run(audio, vad, asr_fn=asr_fn, mt_fn=mt_fn)
    assert stream.status == "finished"
    assert validate_v2_events(trace) == []
    assert [c["state"] for c in stream.captions] == ["draft", "withdrawn"]
    assert "text_zh" not in stream.captions[-1]


def test_online_invariant_blocks_line2_draft_until_line1_terminal():
    # Line 1 releases at 1.6s with slow final MT (2.0s); Line 2 speaks from 1.8s..4.0s.
    # Line 2 must NOT emit any draft before Line 1 emits final!
    def vad(accepted, eof):
        if accepted == int(1.6 * RATE):
            return VadUpdate(None, ((0, accepted),))
        if accepted < int(1.6 * RATE):
            return VadUpdate(0, ())
        if accepted < int(1.8 * RATE):
            return VadUpdate(None, ())
        if eof:
            return VadUpdate(None, ((int(1.8 * RATE), accepted),))
        return VadUpdate(int(1.8 * RATE), ())

    def asr_fn(window):
        cands = [("安定", window.start), ("テスト", window.start + 4000), ("文", window.start + 6000)]
        pairs = [(t, a) for t, a in cands if window.start <= a <= window.end]
        return Tokens("".join(t for t, _ in pairs), tuple(t for t, _ in pairs), tuple(a for _, a in pairs))

    def timing(lane, payload):
        if lane == "mt" and payload["kind"] == "final" and payload["line"].line_id == 1:
            return 2_000_000_000
        return 10_000_000

    audio = b"\x04\x00" * (4 * RATE)
    stream, trace, _ = run(audio, vad, asr_fn=asr_fn, timing=timing)
    assert stream.status == "finished"
    assert validate_v2_events(trace) == []
    emits = [c for c in stream.captions]
    l1_final_idx = next(i for i, c in enumerate(emits) if c["line_id"] == 1 and c["state"] == "final")
    l2_indices = [i for i, c in enumerate(emits) if c["line_id"] == 2]
    assert all(i > l1_final_idx for i in l2_indices)


def test_identical_draft_promoted_to_final_and_cancel_with_inflight_draft():
    # Cancel with inflight draft
    audio = b"\x05\x00" * (3 * RATE)
    def asr_fn(window):
        cands = [("安定", 0), ("テスト", 4000)]
        pairs = [(t, a) for t, a in cands if window.start <= a <= window.end]
        return Tokens("".join(t for t, _ in pairs), tuple(t for t, _ in pairs), tuple(a for _, a in pairs))

    workers = SyntheticA2(audio, lambda a, e: VadUpdate(None, ((0, a),)) if e else VadUpdate(0, ()), asr_fn=asr_fn)
    loop2 = Schedule(elapsed_override=lambda lane, p: 3 * NS if (lane == "mt" and p["kind"] == "draft") else 1_000_000)
    trace2 = []
    s2 = A2Session(loop2, workers, lambda ev, **kw: trace2.append(dict(event=ev, session_id="s1", epoch=0, **kw)),
                   on_close=lambda _: None, tick_ns=400_000_000, roll_left=0)
    for end in range(3200, len(audio) // 2 + 1, 3200):
        loop2.at(end * NS // RATE, lambda e=end: s2.accept(e, audio[2 * (e - 3200):2 * e]), priority=0)
    loop2.at(int(1.8 * NS), lambda: (s2.cancel(), assert_false(s2.closed)))
    loop2.run(lambda: s2.closed)
    assert s2.status == "cancel" and s2.closed
    assert any(e["event"] == "discarded_completion" and e["lane"] == "mt" for e in trace2)


def assert_false(cond):
    assert not cond
