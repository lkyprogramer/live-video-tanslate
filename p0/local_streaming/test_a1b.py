"""Unit tests for A1b sentence-level commit scheduler."""
from hashlib import sha256

from .a1b import A1bSession, A1bVadUpdate, HARD_LIMIT, SOFT_LIMIT, play_a1b
from .clock import Schedule
from .rolling import DRAIN_NS, RATE, Tokens

NS = 1_000_000_000


class SyntheticA1b:
    def __init__(self, audio, update, *, asr_fn=None):
        self.audio = audio
        self.update = update
        self.asr_fn = asr_fn
        self.vad_end = 0

    def vad(self, payload):
        accepted, frame = payload
        if frame is not None:
            assert frame == self.audio[2 * self.vad_end:2 * accepted]
            self.vad_end = accepted
        return self.update(accepted, frame is None)

    def asr(self, window):
        assert window.pcm == self.audio[2 * window.start:2 * window.end]
        assert window.end > window.start
        if self.asr_fn is not None:
            return self.asr_fn(window)
        text = sha256(window.pcm).hexdigest()[:8]
        return Tokens(text, (text,), (window.start,))

    def mt(self, source):
        assert source["start_sample"] < source["end_sample"] <= self.vad_end
        text = "字幕" + source["text_ja"]
        return text, text


def run(audio, update, timing=lambda *_: 1_000_000, *, configure=None, asr_fn=None):
    trace = []
    result = play_a1b(
        audio,
        SyntheticA1b(audio, update, asr_fn=asr_fn),
        lambda event, **data: trace.append(dict(event=event, **data)),
        elapsed_override=timing,
        configure=configure,
    )
    return result, trace


def test_length_managed_pause_continues_and_commits_on_natural_pause():
    def vad(accepted, eof):
        if accepted == 4 * RATE:
            return A1bVadUpdate(None, ((0, 4 * RATE, "length_managed_pause"),))
        if accepted == 7 * RATE:
            return A1bVadUpdate(None, ((4 * RATE + 3200, 7 * RATE, "natural_pause"),))
        if accepted < 4 * RATE:
            return A1bVadUpdate(0, ())
        if 4 * RATE < accepted < 4 * RATE + 3200:
            return A1bVadUpdate(None, ())
        if accepted < 7 * RATE:
            return A1bVadUpdate(4 * RATE + 3200, ())
        return A1bVadUpdate(None, ())

    audio = b"\x03\x00" * (8 * RATE)
    result, trace = run(audio, vad)
    assert result.status == "finished"
    assert len(result.sources) == 1
    assert result.sources[0]["start_sample"] == 0
    assert result.sources[0]["end_sample"] == 7 * RATE
    assert result.sources[0]["boundary_reason"] == "natural_pause"
    assert any(e["event"] == "vad_length_managed_continued" for e in trace)
    assert any(e["event"] == "soft_limit_reached" for e in trace)


def test_six_second_soft_limit_does_not_force_cut():
    def vad(accepted, eof):
        if accepted == 8 * RATE:
            return A1bVadUpdate(None, ((0, 8 * RATE, "natural_pause"),))
        return A1bVadUpdate(0 if accepted < 8 * RATE else None, ())

    audio = b"\x04\x00" * (9 * RATE)
    result, trace = run(audio, vad)
    assert result.status == "finished"
    assert len(result.sources) == 1
    assert result.sources[0]["boundary_reason"] == "natural_pause"
    assert result.sources[0]["end_sample"] == 8 * RATE
    assert any(e["event"] == "soft_limit_reached" for e in trace)


def test_hard_limit_forced_limit_with_stable_alignment_and_cumulative_state():
    def vad(accepted, eof):
        if eof:
            return A1bVadUpdate(None, ((0 if accepted <= HARD_LIMIT else 5 * RATE, accepted, "eof"),))
        return A1bVadUpdate(0, ())

    def asr_fn(window):
        # Provide stable token before 5*RATE and an uncommitted token at 5*RATE <= window.end - RIGHT
        candidates = [("前半", 16000), (f"続き{window.end}", 5 * RATE)]
        if window.end > 5 * RATE + 3200:
            candidates.append(("後半", window.end - 3200))
        pairs = [(t, a) for t, a in candidates if window.start <= a <= window.end]
        return Tokens("".join(t for t, _ in pairs), tuple(t for t, _ in pairs), tuple(a for _, a in pairs))

    audio = b"\x05\x00" * (11 * RATE)
    result, trace = run(audio, vad, asr_fn=asr_fn)
    assert result.status == "finished"
    assert [s["boundary_reason"] for s in result.sources] == ["forced_limit", "eof"]
    assert result.sources[0]["start_sample"] == 0
    assert result.sources[0]["end_sample"] == 5 * RATE
    assert result.sources[1]["start_sample"] == 5 * RATE
    assert result.sources[1]["end_sample"] == 11 * RATE
    assert result.floor > 0
    assert [c["seq"] for c in result.captions] == [1, 2]


def test_hard_limit_forced_unstable_when_no_reliable_alignment():
    def vad(accepted, eof):
        if eof:
            return A1bVadUpdate(None, ())
        return A1bVadUpdate(0, ())

    def asr_fn(window):
        # Always unstable: token text changes with window end
        t = f"w{window.end}"
        return Tokens(t, (t,), (window.start,))

    audio = b"\x06\x00" * (11 * RATE)
    result, trace = run(audio, vad, asr_fn=asr_fn)
    assert result.status == "finished"
    assert [s["boundary_reason"] for s in result.sources] == ["forced_unstable", "eof"]
    assert result.sources[0]["start_sample"] == 0
    assert result.sources[0]["end_sample"] == HARD_LIMIT
    assert result.sources[1]["start_sample"] == HARD_LIMIT
    assert result.sources[1]["end_sample"] == 11 * RATE


def test_fixed_time_differing_future_causal_counterexample():
    def vad(accepted, eof):
        if accepted == RATE:
            return A1bVadUpdate(None, ((0, RATE - 3200, "natural_pause"),))
        if accepted < RATE:
            return A1bVadUpdate(0, ())
        if eof:
            return A1bVadUpdate(None, ((RATE, accepted, "eof"),))
        return A1bVadUpdate(RATE, ())

    traces = []
    audio_prefix = b"\x01\x00" * (2 * RATE)
    for value in (2, 3):
        audio = audio_prefix + bytes([value, 0]) * RATE
        results, events = run(audio, vad, lambda lane, _: 850_000_000 if lane == "asr" else 1_000_000)
        assert results.status == "finished"
        before_fork = [e for e in events if e["schedule_ns"] < 2_200_000_000]
        assert any(e["event"] == "caption_emit" for e in before_fork)
        traces.append(before_fork)
    assert traces[0] == traces[1]


def test_a1b_overflow_drain_timeout_and_cancel_reopen():
    # Overflow
    res_ov, _ = run(b"\x01\x00" * (6 * RATE), lambda a, e: A1bVadUpdate(0, ()),
                    lambda lane, _: 5 * NS if lane == "vad" else 1_000_000)
    assert res_ov.status == "overloaded"

    # Drain timeout
    res_to, _ = run(b"\x05\x00" * RATE,
                    lambda a, e: A1bVadUpdate(None, ((0, a, "eof"),)) if e else A1bVadUpdate(0, ()),
                    lambda lane, _: DRAIN_NS + NS if lane == "asr" else 1_000_000)
    assert res_to.status == "drain_timeout"

    # Cancel + busy + reopen
    audio = b"\x05\x00" * (2 * RATE)
    loop = Schedule(elapsed_override=lambda lane, _: 3 * NS if lane == "asr" else 1_000_000)
    active = {"s": None}
    s1 = A1bSession(loop, SyntheticA1b(audio, lambda a, e: A1bVadUpdate(None, ((0, a, "eof"),)) if e else A1bVadUpdate(0, ())),
                    lambda *_, **__: None, on_close=lambda s: active.__setitem__("s", None))
    active["s"] = s1
    loop.at(0, lambda: [s1.accept(i + 3200, audio[2 * i:2 * (i + 3200)]) for i in range(0, RATE, 3200)], priority=0)
    loop.at(500_000_000, lambda: (s1.cancel(), assert_false(s1.closed)))
    s2_holder = []

    def reopen():
        assert active["s"] is None
        s2 = A1bSession(loop, SyntheticA1b(audio, lambda a, e: A1bVadUpdate(None, ((0, a, "eof"),)) if e else A1bVadUpdate(0, ())),
                        lambda *_, **__: None, on_close=lambda s: active.__setitem__("s", None))
        active["s"] = s2
        s2_holder.append(s2)
        for i in range(0, len(audio) // 2, 3200):
            s2.accept(min(len(audio) // 2, i + 3200), audio[2 * i:2 * min(len(audio) // 2, i + 3200)])
        s2.finish()

    loop.at(3_500_000_000, reopen)
    loop.run(lambda: bool(s2_holder) and s2_holder[0].closed)
    assert s2_holder[0].status == "finished"


def assert_false(cond):
    assert not cond
