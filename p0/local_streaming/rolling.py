"""Experimental final-only rolling scheduler. Audio positions are real PCM samples."""
from collections import deque
from dataclasses import dataclass, replace
from hashlib import sha256
from math import inf

from p0.p2.b3_protocol import StreamCommitter

RATE = 16000
PACKET = 3200
TICK_NS = 800_000_000
LEFT = 12800
RIGHT = 6400
DRIFT = 3200
MAX_WINDOW = 12 * RATE
MAX_PENDING = 4 * RATE
DRAIN_NS = 8_000_000_000
VAD_PRE_ROLL = LEFT + RIGHT


@dataclass(frozen=True)
class VadUpdate:
    active_start: int | None
    releases: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Window:
    start: int
    end: int
    pcm: bytes


@dataclass(frozen=True)
class Tokens:
    text: str
    pieces: tuple[str, ...]
    anchors: tuple[int, ...]


@dataclass(frozen=True)
class Barrier:
    start: int
    end: int
    released_at: int
    reason: str
    index: int
    covered_through: int


def stable_pieces(previous, current, source_start, *, prev_tail_tokens=()):
    """Compare corresponding time regions, counting repeated lexical tokens separately."""
    old_window, old_tokens = previous
    new_window, new_tokens = current
    if new_window.end <= old_window.end:
        return (), (), False
    # Without a matching window origin, require an unambiguous correspondence.
    common_start = max(old_window.start, new_window.start)
    old = [(tok, anchor) for tok, anchor in zip(old_tokens.pieces, old_tokens.anchors, strict=True)
           if anchor >= common_start]
    new = [(tok, anchor) for tok, anchor in zip(new_tokens.pieces, new_tokens.anchors, strict=True)
           if anchor >= common_start]
    if old_window.start != new_window.start and old and new:
        choices = [index for index, (token, anchor) in enumerate(new)
                   if token == old[0][0] and abs(anchor - old[0][1]) <= DRIFT]
        reverse = [index for index, (token, anchor) in enumerate(old)
                   if token == new[0][0] and abs(anchor - new[0][1]) <= DRIFT]
        if choices != [0] or reverse != [0]:
            return (), (), False
    stable = []
    stable_anchors = []
    near_crop_boundary = False
    dropped_boundary_dup = False
    for (old_token, old_anchor), (token, anchor) in zip(old, new):
        if (old_token != token or abs(old_anchor - anchor) > DRIFT
                or max(old_anchor, anchor) >= old_window.end - RIGHT):
            break
        if max(old_anchor, anchor) < source_start:
            continue
        if common_start < source_start and abs(min(old_anchor, anchor) - source_start) <= DRIFT:
            near_crop_boundary = True
            if not stable and not dropped_boundary_dup and prev_tail_tokens and prev_tail_tokens[-1] == token:
                dropped_boundary_dup = True
                continue
        stable.append(token)
        stable_anchors.append(anchor)
    return tuple(stable), tuple(stable_anchors), near_crop_boundary


class RollingSession:
    """Dispatches from observed VAD updates and actual worker completions."""

    def __init__(self, loop, workers, record, *, on_close, tick_ns=TICK_NS, roll_left=LEFT):
        self.loop, self.workers, self.record = loop, workers, record
        self.on_close = on_close
        self.tick_ns = tick_ns
        self.roll_left = roll_left
        self.accepted = self.vad_through = self.decoded_through = 0
        self.floor = 0
        self.pcm = bytearray()
        self.active = None
        self.active_covered = 0
        self.last_rolling = None
        self.last_seen_end = 0
        self.roll_due = False
        self.next_tick_ns = inf
        self.vad_pending = deque()
        self.barriers = deque()
        self.mt_pending = deque()
        self.inflight = set()
        self.asr_window = None
        self.asr_barrier = None
        self.finish_ns = None
        self.flushed = False
        self.status = None
        self.closed = False
        self.releases = 0
        self.caption_seq = 0
        self.source_end = 0
        self.prev_tail_tokens = ()
        self.sources = []
        self.captions = []
        self.committer = StreamCommitter([])

    def accept(self, end, frame):
        if self.status or self.finish_ns is not None:
            return
        start = self.accepted
        assert 0 < len(frame) <= 2 * PACKET and len(frame) == 2 * (end - start)
        self.accepted = end
        self.record("pcm_accepted", accepted_samples=end, pcm_sha256=sha256(frame).hexdigest())
        self.pcm.extend(frame)
        self.record("pcm_consumed", consumed_samples=end)
        self.vad_pending.append((end, frame))
        self.pump()

    def finish(self):
        if self.status or self.finish_ns is not None:
            return
        self.finish_ns = self.loop.now
        self.record("finish_received", accepted_samples=self.accepted,
                    vad_pending=len(self.vad_pending), asr_inflight="asr" in self.inflight,
                    mt_inflight="mt" in self.inflight)
        self.loop.at(self.loop.now + DRAIN_NS, self.drain_timeout)
        self.vad_pending.append((self.accepted, None))
        self.roll_due = False
        self.pump()

    def drain_timeout(self):
        if self.status is None:
            self.terminate("drain_timeout")

    def cancel(self):
        if self.status is None:
            self.terminate("cancel")

    def terminate(self, status):
        self.status = status
        self.record("cancel" if status == "cancel" else ("finished" if status == "finished" else "error"),
                    status=status, accepted_samples=self.accepted,
                    decoded_through_sample=self.decoded_through)
        self.roll_due = False
        self.vad_pending.clear()
        self.barriers.clear()
        self.mt_pending.clear()
        self.committer.pieces.clear()
        self.pump()

    def submit(self, lane, audio_or_source, complete):
        assert lane not in self.inflight
        self.inflight.add(lane)

        def done(result):
            self.inflight.remove(lane)
            if self.status is None:
                complete(result)
            else:
                self.record("discarded_completion", lane=lane, status=self.status)
            self.pump()

        self.loop.work(getattr(self.workers, lane), audio_or_source, done, lane)

    def observed(self, end, frame, update):
        self.vad_through = end
        self.record("vad_completed", vad_through_sample=end, active_start_sample=update.active_start,
                    releases=update.releases, eof=frame is None)
        for start, stop in update.releases:
            assert self.source_end <= start < stop <= end
            self.releases += 1
            reason = "eof" if frame is None else "vad"
            # EOF short frames can leave up to one native VAD block outside flush.
            # Include that real tail in the ASR input while retaining the control's VAD boundary.
            covered = self.active_covered if start == self.active else start
            self.barriers.append(Barrier(start, stop, end, reason, self.releases, covered))
        if update.active_start != self.active:
            self.active = update.active_start
            self.active_covered = self.active
            self.last_rolling = None
            self.last_seen_end = 0
            self.next_tick_ns = inf
            self.roll_due = False
            if self.active is not None and self.finish_ns is None:
                self.next_tick_ns = self.loop.now
                self.tick(self.active, self.next_tick_ns)
        if frame is None:
            self.flushed = True

    def tick(self, start, scheduled):
        if self.status or self.finish_ns is not None or self.active != start or self.next_tick_ns != scheduled:
            return
        self.record("tick", requested_end_sample=self.accepted, coalesced=self.roll_due, asr_busy="asr" in self.inflight)
        self.roll_due = True
        self.next_tick_ns = scheduled + self.tick_ns
        self.loop.at(self.next_tick_ns, lambda: self.tick(start, scheduled + self.tick_ns))
        self.pump()

    def fetch(self, start, end):
        assert self.floor <= start < end <= self.accepted
        return Window(start, end, bytes(self.pcm[2*(start-self.floor):2*(end-self.floor)]))

    def start_asr(self, barrier):
        active = self.active
        if barrier:
            start = barrier.start
            end = barrier.end
        else:
            start = max(0, active - self.roll_left)
            end = self.accepted
            self.last_seen_end = end
            if end <= active:
                return
        if end - start > MAX_WINDOW:
            self.terminate("adapter_unaligned")
            return
        window = self.fetch(start, end)
        self.asr_window = window
        self.asr_barrier = barrier
        self.record("asr_start", start_sample=start, end_sample=end, consumed_samples=self.accepted,
                    pcm_sha256=sha256(window.pcm).hexdigest(), kind=barrier.reason if barrier else "rolling")

        def completed(tokens):
            self.asr_window = None
            self.asr_barrier = None
            self.decoded_through = max(end, self.decoded_through)
            assert self.decoded_through <= self.accepted
            assert len(tokens.pieces) == len(tokens.anchors)
            assert list(tokens.anchors) == sorted(tokens.anchors)
            assert all(start <= anchor <= end for anchor in tokens.anchors)
            self.record("asr_completed", kind=barrier.reason if barrier else "rolling",
                        start_sample=start, end_sample=end, decoded_through_sample=self.decoded_through,
                        text_ja=tokens.text, tokens=tokens.pieces, anchors=tokens.anchors)
            if barrier:
                self.complete_barrier(barrier, window, tokens)
            elif active == self.active:
                self.active_covered = max(self.active_covered, end)
                if self.last_rolling and self.finish_ns is None:
                    stable, stable_anchors, near_crop = stable_pieces(
                        self.last_rolling, (window, tokens), active,
                        prev_tail_tokens=self.prev_tail_tokens,
                    )
                    self.record("stable_candidate", start_sample=active, end_sample=end,
                                text_ja="".join(stable).replace("▁", " ").strip(),
                                tokens=stable, anchors=stable_anchors,
                                near_crop_boundary=near_crop)
                self.last_rolling = window, tokens
            elif active is not None:
                self.barriers = deque(replace(item, covered_through=max(item.covered_through, end))
                                      if item.start == active else item for item in self.barriers)

        self.submit("asr", window, completed)

    def complete_barrier(self, barrier, window, tokens):
        assert self.source_end <= barrier.start < barrier.end <= window.end <= self.decoded_through
        self.source_end = window.end
        # The final decode uses the exact VAD origin without left context. Only that new
        # segment is fed to the old committer. Rolling hypotheses remain diagnostics.
        self.committer.cuts.append(dict(start_sample=barrier.start, end_sample=window.end,
                                        released_at_sample=barrier.released_at, segment_id=barrier.index,
                                        flush=barrier.reason == "eof"))
        # StreamCommitter is used for its piece cursor and causal release gate. Verified
        # A1a VAD ranges provide the line identity; its gap-including start is not used.
        commits = self.committer.advance(accepted=self.accepted, frontier=window.end,
                    pieces=[(barrier.start, window.end, tokens.text)], eof=barrier.reason == "eof")
        assert len(commits) == 1 and commits[0]["endpoint_ids"] == [barrier.index]
        assert commits[0]["text_ja"] == tokens.text
        # Internal piece storage has a cumulative cursor. Keep full text in the log,
        # then release committed payloads without resetting the cursor or endpoints.
        self.committer.pieces[-1] = (barrier.start, window.end, "")
        self.prev_tail_tokens = tokens.pieces[-3:]
        eof_tail = (barrier.released_at - barrier.end) if barrier.reason == "eof" else 0
        if barrier.reason == "eof":
            assert 0 <= eof_tail < 512
        source = dict(start_sample=barrier.start, end_sample=window.end, vad_end_sample=barrier.end,
                      segment_id=barrier.index, text_ja=tokens.text, boundary_reason=barrier.reason,
                      eof_unclassified_tail_samples=eof_tail)
        self.sources.append(source)
        self.record("source_commit", **source)
        if not tokens.text:
            return
        if len(self.mt_pending) == 4 and "mt" in self.inflight:
            self.terminate("overloaded")
            return
        self.mt_pending.append(source)

    def start_mt(self):
        source = self.mt_pending.popleft()
        self.record("mt_start", **source)

        def completed(result):
            raw, filtered = result
            self.record("mt_completed", **source, raw_zh=raw, text_zh=filtered)
            if filtered is None:
                return
            self.caption_seq += 1
            caption = source | dict(seq=self.caption_seq, text_zh=filtered, raw_zh=raw, visible_ns=self.loop.now)
            self.captions.append(caption)
            self.record("caption_emit", **caption)

        self.submit("mt", source, completed)

    def pending_samples(self):
        intervals = [(self.vad_through, self.accepted)]
        if self.active is not None:
            intervals.append((self.active_covered, self.vad_through))
        barriers = list(self.barriers)
        if self.asr_barrier is not None:
            barriers.append(self.asr_barrier)
        intervals.extend((min(item.covered_through, item.end), item.end)
                         for item in barriers)
        # Deduplicate pending ranges and exclude already covered left context.
        total = last_end = 0
        for start, end in sorted(intervals):
            total += max(0, end - max(start, last_end))
            last_end = max(last_end, end)
        return total

    def pump(self):
        if self.status is not None:
            if not self.inflight and not self.closed:
                self.closed = True
                self.pcm.clear()
                self.last_rolling = None
                self.asr_window = self.asr_barrier = None
                self.record("closed", status=self.status)
                self.on_close(self)
            return
        pending = self.pending_samples()
        if pending > MAX_PENDING:
            self.record("pending_audio_overflow", pending_samples=pending)
            self.terminate("overloaded")
            return
        # Keep active/barrier origins and pre-roll preceding the oldest unknown VAD frame.
        retain = [max(0, self.vad_through - VAD_PRE_ROLL)]
        retain.extend(barrier.start for barrier in self.barriers)
        if self.active is not None:
            retain.append(max(0, self.active - self.roll_left))
        floor = min(retain)
        assert floor >= self.floor
        del self.pcm[:2*(floor - self.floor)]
        self.floor = floor
        if "vad" not in self.inflight and self.vad_pending:
            end, frame = self.vad_pending.popleft()
            self.submit("vad", (end, frame), lambda result: self.observed(end, frame, result))
        if "asr" not in self.inflight:
            if self.barriers:
                self.start_asr(self.barriers.popleft())
            elif self.roll_due and self.active is not None and self.accepted > self.last_seen_end:
                self.roll_due = False
                self.start_asr(None)
        if self.status is None and "mt" not in self.inflight and self.mt_pending:
            self.start_mt()
        if self.status is None and self.flushed and not self.inflight and not self.barriers and not self.mt_pending:
            self.terminate("finished")
