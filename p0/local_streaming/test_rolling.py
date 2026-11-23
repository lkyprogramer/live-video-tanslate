from hashlib import sha256

from .rolling import DRAIN_NS, Tokens, VadUpdate, Window, stable_pieces
from .run_a1 import play


NS = 1_000_000_000
RATE = 16000


class Synthetic:
    def __init__(self, audio, update):
        self.audio, self.update = audio, update
        self.vad_end = 0

    def vad(self, payload):
        accepted, frame = payload
        if frame is not None:
            assert frame == self.audio[2*self.vad_end:2*accepted]
            self.vad_end = accepted
        return self.update(accepted, frame is None)

    def asr(self, window):
        assert window.pcm == self.audio[2*window.start:2*window.end]
        assert window.end > window.start
        text = sha256(window.pcm).hexdigest()[:8]
        return Tokens(text, (text,), (window.start,))

    def mt(self, source):
        assert source["start_sample"] < source["end_sample"] <= self.vad_end
        text = "字幕" + source["text_ja"]
        return text, text


def run(audio, update, timing=lambda *_: 1_000_000, *, configure=None):
    trace = []
    result = play(audio, Synthetic(audio, update), lambda event, **data: trace.append(dict(event=event, **data)),
                  elapsed_override=timing, configure=configure)
    return result, trace


def full_active(accepted, eof):
    return VadUpdate(None, ((0, accepted - 10),)) if eof else VadUpdate(0, ())


def test_fixed_time_differing_future_uses_immutable_snapshots_and_actual_completion():
    def vad(accepted, eof):
        if accepted == RATE:
            return VadUpdate(None, ((0, RATE-3200),))
        if accepted < RATE:
            return VadUpdate(0, ())
        if eof:
            return VadUpdate(None, ((RATE, accepted),))
        return VadUpdate(RATE, ())

    traces = []
    audio_prefix = b"\x01\x00" * (2*RATE)
    for value in (2, 3):
        audio = audio_prefix + bytes([value, 0]) * RATE
        results, events = run(audio, vad, lambda lane, _: 850_000_000 if lane == "asr" else 1_000_000)
        assert results.status == "finished"
        before_fork = [e for e in events if e["schedule_ns"] < 2_200_000_000]
        assert any(e["event"] == "caption_emit" for e in before_fork)
        assert any(e["event"] == "tick" and e["asr_busy"] for e in before_fork)
        for event in (e for e in events if e["event"] == "asr_start"):
            assert event["end_sample"] <= event["consumed_samples"]
            assert sha256(audio[2*event["start_sample"]:2*event["end_sample"]]).hexdigest() == event["pcm_sha256"]
        traces.append(before_fork)
    assert traces[0] == traces[1]


def test_short_pcm_tail_precedes_finish_and_inflight_asr_drains_all_samples():
    audio = b"\x05\x00" * (RATE + 1371)
    result, trace = run(audio, full_active, lambda lane, _: 1_100_000_000 if lane == "asr" else 210_000_000)
    events = [row["event"] for row in trace]
    finish = next(row for row in trace if row["event"] == "finish_received")
    assert finish["asr_inflight"] and finish["vad_pending"] >= 1
    assert result.status == "finished"
    assert result.sources[0]["end_sample"] == len(audio)//2 - 10
    assert result.sources[0]["vad_end_sample"] == len(audio)//2 - 10
    assert result.sources[0]["eof_unclassified_tail_samples"] == 10
    assert max(i for i, label in enumerate(events) if label == "pcm_accepted") < events.index("finish_received")
    assert events.index("asr_completed") < events.index("source_commit") < events.index("caption_emit")
    assert trace[-1]["schedule_ns"]-finish["schedule_ns"] < DRAIN_NS


def test_drain_deadline_discards_late_completion():
    def timing(lane, item):
        return DRAIN_NS + NS if lane == "asr" else 1_000_000
    result, events = run(b"\x05\x00"*RATE, full_active, timing)
    assert result.status == "drain_timeout"
    assert [e["event"] for e in events if e["event"] in ("error", "discarded_completion", "closed")] == [
        "error", "discarded_completion", "closed"]
    assert result.closed
    assert result.sources == result.captions == []


def test_cancel_with_asr_tick_and_eof_pending_waits_for_actual_completion():
    pending = []
    def configure(clock, stream):
        def cancel():
            finish = stream.finish_ns is not None
            pending.append((finish, bool(stream.barriers), "asr" in stream.inflight, stream.closed))
            stream.cancel()
            stream.cancel()
            assert not stream.closed
        clock.at(2 * NS + 100_000_000, cancel)
    timing = lambda lane, _: 3*NS if lane == "asr" else 1_000_000
    stream, events = run(b"\x05\x00"*(2*RATE), full_active, timing, configure=configure)
    assert pending == [(True, True, True, False)]
    assert any(e.get("coalesced") for e in events if e["event"] == "tick")
    cancel_index = next(i for i, e in enumerate(events) if e["event"] == "cancel")
    assert [e["event"] for e in events[cancel_index:]] == ["cancel", "discarded_completion", "closed"]
    assert stream.closed and not stream.pcm and not stream.barriers


def test_long_silence_does_not_inflate_backlog_when_speech_starts():
    def vad(accepted, eof):
        assert accepted >= 0
        if eof:
            return VadUpdate(None, ((7*RATE, accepted),))
        return VadUpdate(7*RATE if accepted > 7*RATE else None, ())
    result, trace = run(b"\x01\x00"*(9*RATE), vad)
    assert result.status == "finished"
    assert all(e["schedule_ns"] > 7*NS for e in trace if e["event"] == "asr_start")
    assert result.captions and result.captions[0]["start_sample"] == 7*RATE


def test_unclassified_vad_frames_are_counted_during_worker_stall():
    result, trace = run(b"\x01\x00"*(6*RATE), full_active,
                        lambda lane, _: 5*NS if lane == "vad" else 1_000_000)
    assert result.status == "overloaded"
    assert result.accepted == 4*RATE + 3200
    assert any(e["event"] == "pending_audio_overflow" and e["pending_samples"] == result.accepted for e in trace)


def test_old_tail_and_new_speech_share_pending_budget():
    def vad(accepted, eof):
        if accepted == 3*RATE:
            return VadUpdate(None, ((0, 3*RATE),))
        return VadUpdate(3*RATE if accepted > 3*RATE else 0, ())
    result, trace = run(b"\x01\x00"*(6*RATE), vad,
                        lambda lane, _: 5*NS if lane == "asr" else 1_000_000)
    assert result.status == "overloaded"
    assert result.accepted == 4*RATE + 3200
    assert any(e.get("active_start_sample") == 3*RATE for e in trace)


def test_overlap_alignment_keeps_repetition_without_claiming_unstable_end():
    initial = Window(0, 24000, b"")
    longer = Window(0, 36800, b"")
    previous = initial, Tokens("はいはい後", ("はい", "はい", "後"), (4800, 10400, 20000))
    current = longer, Tokens("はいはい次", ("はい", "はい", "次"), (5000, 11200, 20200))
    pieces, anchors, near = stable_pieces(previous, current, 0)
    assert pieces == ("はい", "はい")
    assert anchors == (5000, 11200)
    assert not near

    # Moving window origin keeps aligned token after source_start.
    shifted = Window(10100, 36800, b""), Tokens("はい次", ("はい", "次"), (10500, 20200))
    pieces, anchors, near = stable_pieces(previous, shifted, 10100)
    assert pieces == ("はい",)
    assert anchors == (10500,)

    # Token near source_start (within DRIFT=3200) is NOT dropped unless it duplicates prev_tail_tokens.
    pieces, anchors, near = stable_pieces(previous, current, 10400)
    assert pieces == ("はい",)
    assert anchors == (11200,)
    assert near

    # Same boundary token IS deduped once when prev_tail_tokens ends with it, while genuine repetition survives.
    rep_prev = Window(0, 28800, b""), Tokens("えはいはい後", ("え", "はい", "はい", "後"), (4800, 10400, 15000, 24000))
    rep_cur = Window(0, 36800, b""), Tokens("えはいはい次", ("え", "はい", "はい", "次"), (4800, 11200, 15200, 24200))
    pieces, anchors, near = stable_pieces(rep_prev, rep_cur, 10400, prev_tail_tokens=("え", "はい"))
    assert pieces == ("はい",)
    assert anchors == (15200,)
    assert near


def test_cancel_busy_reopen_lifecycle():
    from .clock import Schedule
    from .rolling import RollingSession

    class LeaseManager:
        def __init__(self):
            self.active_session = None

        def open(self, loop, workers, trace):
            if self.active_session is not None:
                return None
            session = RollingSession(
                loop, workers,
                lambda event, **data: trace.append(dict(event=event, schedule_ns=loop.now, **data)),
                on_close=self._release,
            )
            self.active_session = session
            return session

        def _release(self, session):
            if self.active_session is session:
                self.active_session = None

    audio = b"\x05\x00" * (2 * RATE)
    loop = Schedule(elapsed_override=lambda lane, _: 3 * NS if lane == "asr" else 1_000_000)
    workers = Synthetic(audio, full_active)
    mgr = LeaseManager()
    trace1 = []
    s1 = mgr.open(loop, workers, trace1)
    assert s1 is not None

    reopen_attempts = []

    def step():
        # Feed 1s of PCM
        for i in range(0, RATE, 3200):
            s1.accept(i + 3200, audio[2 * i:2 * (i + 3200)])

    loop.at(0, step, priority=0)
    # At 0.5s, ASR is in-flight (3s duration); cancel s1 twice and try reopening immediately.
    def do_cancel():
        s1.cancel()
        s1.cancel()
        assert not s1.closed
        reopen_attempts.append(("during_cancel_inflight", mgr.open(loop, workers, []) is None))

    loop.at(500_000_000, do_cancel)
    # After 3.5s, worker has returned, s1.closed is True, and a new session can open & finish.
    trace2 = []
    s2_holder = []

    def try_reopen():
        assert s1.closed
        s2 = mgr.open(loop, workers, trace2)
        reopen_attempts.append(("after_close", s2 is not None))
        s2_holder.append(s2)
        workers2 = Synthetic(audio, full_active)
        s2.workers = workers2
        for i in range(0, len(audio) // 2, 3200):
            end = min(len(audio) // 2, i + 3200)
            s2.accept(end, audio[2 * i:2 * end])
        s2.finish()

    loop.at(3_500_000_000, try_reopen)
    loop.run(lambda: bool(s2_holder) and s2_holder[0].closed)
    assert reopen_attempts == [("during_cancel_inflight", True), ("after_close", True)]
    assert s2_holder[0].status == "finished"
    assert mgr.active_session is None
    assert not any(e["event"] == "caption_emit" for e in trace1)


def test_window_origin_shift_preserves_cumulative_committed_state():
    # Two consecutive utterances where the second shifts pcm floor well past 0.
    def vad(accepted, eof):
        if accepted == 3 * RATE:
            return VadUpdate(None, ((0, 3 * RATE),))
        if accepted < 3 * RATE:
            return VadUpdate(0, ())
        if accepted < 5 * RATE:
            return VadUpdate(None, ())
        if eof:
            return VadUpdate(None, ((5 * RATE, accepted),))
        return VadUpdate(5 * RATE, ())

    audio = b"\x02\x00" * (7 * RATE)
    result, trace = run(audio, vad)
    assert result.status == "finished"
    assert result.floor > 3 * RATE
    assert len(result.sources) == 2
    assert len(result.captions) == 2
    assert result.sources[0]["start_sample"] == 0 and result.sources[1]["start_sample"] == 5 * RATE
    assert result.committer.last_end == 7 * RATE
    assert [c["seq"] for c in result.captions] == [1, 2]
