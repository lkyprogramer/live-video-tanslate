"""A2 Same-Hy-MT2 Draft+Final Offline Causal Probe and v2 Event Checker."""
import argparse
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from hashlib import sha256
import json
from math import inf
from pathlib import Path
import shutil

import numpy as np

from p0.monitor import mark
from p0.p2.b3_protocol import StreamCommitter, source_seed
from p0.p2.events import EventLog, validate_causality
from p0.p2.freeze import digest, dump, frozen_fingerprint, rows
from p0.p2.runner import pcm_packets
from subtitle_service.app import chinese
from subtitle_service.config import load_settings
from subtitle_service.local_pipeline import HyTranslator, ReazonRecognizer, vad_segments

from .a0 import decode
from .clock import Schedule
from .rolling import (
    DRAIN_NS,
    LEFT,
    MAX_PENDING,
    MAX_WINDOW,
    PACKET,
    RATE,
    TICK_NS,
    VAD_PRE_ROLL,
    Barrier,
    Tokens,
    VadUpdate,
    Window,
    stable_pieces,
)

DRAFT_MIN_INTERVAL_NS = 800_000_000
DRAFT_MIN_CHARS = 4


def draft_line_seed(base_seed, clip, start_sample):
    h = int(sha256(f"{clip}:{start_sample}".encode()).hexdigest()[:7], 16)
    return base_seed + 200000 + h


@dataclass
class LineState:
    line_id: int
    start_sample: int
    revision: int = 0
    terminal: bool = False
    committed: bool = False
    last_emitted_draft: tuple | None = None  # (text_ja, call_seed, text_zh, start_sample, end_sample)
    last_submitted_draft_ja: str | None = None
    last_draft_submit_ns: int = -DRAFT_MIN_INTERVAL_NS


class A2Workers:
    def __init__(self, recognizer, translator, seed, baseline, clip):
        self.recognizer = recognizer
        self.vad_detector = recognizer.new_vad()
        self.translator = translator
        self.session = translator.new_session()
        self.seed = seed
        self.seed_rows = baseline
        self.clip = clip

    def vad(self, payload):
        accepted, frame = payload
        releases = tuple((start, start + len(samples))
                         for start, samples in vad_segments(self.vad_detector, frame))
        active = self.vad_detector.current_segment.start if self.vad_detector.is_speech_detected() else None
        assert all(0 <= start < stop <= accepted for start, stop in releases)
        assert active is None or 0 <= active < accepted
        return VadUpdate(active, releases)

    def asr(self, window):
        audio = np.frombuffer(window.pcm, dtype="<i2").astype(np.float32) / 32768
        result = decode(self.recognizer, audio, 0, len(audio))
        tokens = tuple(result["tokens"])
        return Tokens(result["text_ja"], tokens, tuple(window.start + a for a in result["anchors"]))

    def mt(self, job):
        import mlx.core as mx
        mx.random.seed(job["call_seed"])
        raw = self.translator.translate(self.session, job["text_ja"])
        return raw, chinese(raw)


class A2Session:
    """Rolling ASR + same-Hy-MT2 draft/final scheduler enforcing v2 online invariants."""

    def __init__(self, loop, workers, record, *, on_close, clip="clip", base_seed=11,
                 seed_rows=(), tick_ns=TICK_NS, roll_left=0):
        self.loop, self.workers, self.record = loop, workers, record
        self.on_close = on_close
        self.clip = clip
        self.base_seed = base_seed
        self.seed_rows = seed_rows
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
        self.mt_finals = deque()
        self.pending_draft = None
        self.inflight = set()
        self.inflight_mt_job = None
        self.asr_window = None
        self.asr_barrier = None
        self.finish_ns = None
        self.flushed = False
        self.status = None
        self.closed = False
        self.releases = 0
        self.caption_seq = 0
        self.next_line_id = 0
        self.open_line = None
        self.non_terminal_line_id = None
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
        self.pending_draft = None
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
        self.mt_finals.clear()
        self.pending_draft = None
        self.committer.pieces.clear()
        self.pump()

    def submit(self, lane, payload, complete):
        assert lane not in self.inflight
        self.inflight.add(lane)

        def done(result):
            self.inflight.remove(lane)
            if self.status is None:
                complete(result)
            else:
                self.record("discarded_completion", lane=lane, status=self.status)
            self.pump()

        self.loop.work(getattr(self.workers, lane), payload, done, lane)

    def _alloc_line(self, start_sample):
        self.next_line_id += 1
        line = LineState(line_id=self.next_line_id, start_sample=start_sample)
        return line

    def observed(self, end, frame, update):
        self.vad_through = end
        self.record("vad_completed", vad_through_sample=end, active_start_sample=update.active_start,
                    releases=update.releases, eof=frame is None)
        for start, stop in update.releases:
            assert self.source_end <= start < stop <= end
            self.releases += 1
            reason = "eof" if frame is None else "vad"
            covered = self.active_covered if start == self.active else start
            line = self.open_line if (self.open_line and self.open_line.start_sample == start and not self.open_line.committed) else None
            if line is not None:
                line.committed = True
            self.barriers.append((Barrier(start, stop, end, reason, self.releases, covered), line))
            if self.open_line is line:
                self.open_line = None
            self.pending_draft = None
        if update.active_start != self.active:
            self.active = update.active_start
            self.active_covered = self.active
            self.last_rolling = None
            self.last_seen_end = 0
            self.next_tick_ns = inf
            self.roll_due = False
            self.pending_draft = None
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
        return Window(start, end, bytes(self.pcm[2 * (start - self.floor):2 * (end - self.floor)]))

    def start_asr(self, item):
        active = self.active
        barrier, barrier_line = item if item else (None, None)
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
                self.complete_barrier(barrier, barrier_line, window, tokens)
            elif active == self.active:
                self.active_covered = max(self.active_covered, end)
                if self.last_rolling and self.finish_ns is None:
                    stable, stable_anchors, near_crop = stable_pieces(
                        self.last_rolling, (window, tokens), active,
                        prev_tail_tokens=self.prev_tail_tokens,
                    )
                    text_ja = "".join(stable).replace("▁", " ").strip()
                    self.record("stable_candidate", start_sample=active, end_sample=end,
                                text_ja=text_ja, tokens=stable, anchors=stable_anchors,
                                near_crop_boundary=near_crop)
                    if (
                        len(text_ja) >= DRAFT_MIN_CHARS
                        and not self.mt_finals
                        and not self.barriers
                        and (self.non_terminal_line_id is None or (self.open_line and self.open_line.line_id == self.non_terminal_line_id))
                    ):
                        if self.open_line is None:
                            self.open_line = self._alloc_line(active)
                        if text_ja != self.open_line.last_submitted_draft_ja:
                            draft_end = min(end, stable_anchors[-1]) if stable_anchors and stable_anchors[-1] > active else end
                            self.pending_draft = dict(
                                kind="draft",
                                line=self.open_line,
                                start_sample=active,
                                end_sample=max(active + 1, draft_end),
                                text_ja=text_ja,
                                call_seed=draft_line_seed(self.base_seed, self.clip, active),
                            )
                self.last_rolling = window, tokens
            elif active is not None:
                self.barriers = deque(
                    (replace(b, covered_through=max(b.covered_through, end)), ln)
                    if b.start == active else (b, ln)
                    for b, ln in self.barriers
                )

        self.submit("asr", window, completed)

    def complete_barrier(self, barrier, barrier_line, window, tokens):
        assert self.source_end <= barrier.start < barrier.end <= window.end <= self.decoded_through
        self.source_end = window.end
        self.committer.cuts.append(dict(start_sample=barrier.start, end_sample=window.end,
                                        released_at_sample=barrier.released_at, segment_id=barrier.index,
                                        flush=barrier.reason == "eof"))
        commits = self.committer.advance(accepted=self.accepted, frontier=window.end,
                                         pieces=[(barrier.start, window.end, tokens.text)],
                                         eof=barrier.reason == "eof")
        assert len(commits) == 1 and commits[0]["endpoint_ids"] == [barrier.index]
        assert commits[0]["text_ja"] == tokens.text
        self.committer.pieces[-1] = (barrier.start, window.end, "")
        self.prev_tail_tokens = tokens.pieces[-3:]
        eof_tail = (barrier.released_at - barrier.end) if barrier.reason == "eof" else 0
        if barrier.reason == "eof":
            assert 0 <= eof_tail < 512

        if barrier_line is None:
            barrier_line = self._alloc_line(barrier.start)
        barrier_line.committed = True
        self.pending_draft = None

        source = dict(start_sample=barrier.start, end_sample=window.end, vad_end_sample=barrier.end,
                      segment_id=barrier.index, line_id=barrier_line.line_id, text_ja=tokens.text,
                      boundary_reason=barrier.reason, eof_unclassified_tail_samples=eof_tail)
        self.sources.append(source)
        self.record("source_commit", **source)
        if not tokens.text:
            if barrier_line.last_emitted_draft is not None:
                self._emit_withdrawn(barrier_line, barrier.start, window.end, reason="empty_source")
            return
        if len(self.mt_finals) == 4 and "mt" in self.inflight:
            self.terminate("overloaded")
            return
        key = dict(clip=self.clip, start_sample=barrier.start, end_sample=barrier.end, text_ja=tokens.text)
        final_seed = self.base_seed + source_seed(key, self.seed_rows)
        self.mt_finals.append(dict(
            kind="final",
            line=barrier_line,
            source=source,
            start_sample=barrier.start,
            end_sample=window.end,
            text_ja=tokens.text,
            call_seed=final_seed,
        ))

    def _emit_caption(self, line, state, start_sample, end_sample, text_zh, raw_zh, source_meta=None):
        line.revision += 1
        self.caption_seq += 1
        if state == "draft":
            self.non_terminal_line_id = line.line_id
        else:
            line.terminal = True
            if self.non_terminal_line_id == line.line_id:
                self.non_terminal_line_id = None
        caption = dict(
            seq=self.caption_seq,
            line_id=line.line_id,
            revision=line.revision,
            state=state,
            start_sample=start_sample,
            end_sample=end_sample,
            text_zh=text_zh,
            raw_zh=raw_zh,
            visible_ns=self.loop.now,
        )
        if source_meta:
            caption.update(segment_id=source_meta["segment_id"], text_ja=source_meta["text_ja"],
                           vad_end_sample=source_meta["vad_end_sample"],
                           boundary_reason=source_meta["boundary_reason"])
        self.captions.append(caption)
        self.record("caption_emit", **caption)

    def _emit_withdrawn(self, line, start_sample, end_sample, *, reason):
        line.revision += 1
        line.terminal = True
        if self.non_terminal_line_id == line.line_id:
            self.non_terminal_line_id = None
        self.caption_seq += 1
        ev = dict(
            seq=self.caption_seq,
            line_id=line.line_id,
            revision=line.revision,
            state="withdrawn",
            start_sample=start_sample,
            end_sample=end_sample,
            reason=reason,
            visible_ns=self.loop.now,
        )
        self.captions.append(ev)
        self.record("caption_emit", **ev)

    def start_mt_final(self):
        job = self.mt_finals.popleft()
        line = job["line"]
        # Draft-to-final promotion if exact same source, settings, and seed.
        if line.last_emitted_draft is not None:
            d_ja, d_seed, d_zh, _ds, _de = line.last_emitted_draft
            if d_ja == job["text_ja"] and d_seed == job["call_seed"]:
                self.record("mt_promoted", line_id=line.line_id, segment_id=job["source"]["segment_id"])
                self._emit_caption(line, "final", job["start_sample"], job["end_sample"],
                                   d_zh, d_zh, source_meta=job["source"])
                return

        self.inflight_mt_job = job
        self.record("mt_start", kind="final", call_seed=job["call_seed"], **job["source"])

        def completed(result):
            self.inflight_mt_job = None
            raw, filtered = result
            self.record("mt_completed", kind="final", raw_zh=raw, text_zh=filtered, **job["source"])
            if filtered is None:
                if line.last_emitted_draft is not None:
                    self._emit_withdrawn(line, job["start_sample"], job["end_sample"], reason="translation_invalid")
                return
            self._emit_caption(line, "final", job["start_sample"], job["end_sample"],
                               filtered, raw, source_meta=job["source"])

        self.submit("mt", job, completed)

    def start_mt_draft(self):
        job = self.pending_draft
        self.pending_draft = None
        line = job["line"]
        line.last_submitted_draft_ja = job["text_ja"]
        line.last_draft_submit_ns = self.loop.now
        self.inflight_mt_job = job
        self.record("mt_start", kind="draft", line_id=line.line_id, start_sample=job["start_sample"],
                    end_sample=job["end_sample"], text_ja=job["text_ja"], call_seed=job["call_seed"])

        def completed(result):
            self.inflight_mt_job = None
            raw, filtered = result
            stale = line.committed or line.terminal or (self.open_line is not line)
            if stale:
                self.record("draft_discarded_stale", line_id=line.line_id, start_sample=job["start_sample"],
                            end_sample=job["end_sample"], text_ja=job["text_ja"], raw_zh=raw, text_zh=filtered)
                return
            self.record("mt_completed", kind="draft", line_id=line.line_id, start_sample=job["start_sample"],
                        end_sample=job["end_sample"], text_ja=job["text_ja"], raw_zh=raw, text_zh=filtered)
            if filtered is None:
                self.record("draft_invalid", line_id=line.line_id, raw_zh=raw)
                return
            line.last_emitted_draft = (job["text_ja"], job["call_seed"], filtered, job["start_sample"], job["end_sample"])
            self._emit_caption(line, "draft", job["start_sample"], job["end_sample"],
                               filtered, raw, source_meta=dict(segment_id=None, text_ja=job["text_ja"],
                                                               vad_end_sample=job["end_sample"],
                                                               boundary_reason="draft"))

        self.submit("mt", job, completed)

    def pending_samples(self):
        intervals = [(self.vad_through, self.accepted)]
        if self.active is not None:
            intervals.append((self.active_covered, self.vad_through))
        barriers = [b for b, _ in self.barriers]
        if self.asr_barrier is not None:
            barriers.append(self.asr_barrier)
        intervals.extend((min(item.covered_through, item.end), item.end) for item in barriers)
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
        retain = [max(0, self.vad_through - VAD_PRE_ROLL)]
        retain.extend(b.start for b, _ in self.barriers)
        if self.active is not None:
            retain.append(max(0, self.active - self.roll_left))
        floor = min(retain)
        assert floor >= self.floor
        del self.pcm[:2 * (floor - self.floor)]
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
        if self.status is None and "mt" not in self.inflight:
            if self.mt_finals:
                self.start_mt_final()
            elif (
                self.pending_draft is not None
                and self.finish_ns is None
                and not self.barriers
                and (self.non_terminal_line_id is None or self.non_terminal_line_id == self.pending_draft["line"].line_id)
                and self.loop.now - self.pending_draft["line"].last_draft_submit_ns >= DRAFT_MIN_INTERVAL_NS
            ):
                self.start_mt_draft()
        if self.status is None and self.flushed and not self.inflight and not self.barriers and not self.mt_finals:
            self.terminate("finished")


def validate_v2_events(events):
    """Validate v2 online invariants and final-projection causality."""
    errors = []
    by_session = {}
    for ev in events:
        by_session.setdefault((ev["session_id"], ev.get("epoch", 0)), []).append(ev)

    final_proj_events = []
    for (sid, epoch), trace in by_session.items():
        seq = 0
        max_line = 0
        open_line = None
        line_revs = {}
        terminal_lines = set()
        accepted = decoded = 0
        final_seq = 0
        for idx, ev in enumerate(trace):
            kind = ev["event"]
            if kind == "pcm_accepted":
                accepted = ev["accepted_samples"]
            elif kind == "asr_completed":
                decoded = max(decoded, ev["end_sample"])
            elif kind == "caption_emit":
                seq += 1
                if ev["seq"] != seq:
                    errors.append(f"{sid} idx={idx}: seq {ev['seq']} != {seq}")
                lid = ev["line_id"]
                rev = ev["revision"]
                state = ev["state"]
                if lid < max_line:
                    errors.append(f"{sid} idx={idx}: line_id regression {lid} < {max_line}")
                max_line = max(max_line, lid)
                if open_line is not None and lid != open_line:
                    errors.append(f"{sid} idx={idx}: new line {lid} while line {open_line} non-terminal")
                if lid in terminal_lines:
                    errors.append(f"{sid} idx={idx}: update after terminal on line {lid}")
                prev_rev = line_revs.get(lid, 0)
                if rev != prev_rev + 1:
                    errors.append(f"{sid} idx={idx}: revision {rev} != {prev_rev + 1} on line {lid}")
                line_revs[lid] = rev
                s, e = ev["start_sample"], ev["end_sample"]
                if not (0 <= s < e <= decoded <= accepted):
                    errors.append(f"{sid} idx={idx}: range [{s}, {e}] exceeds decoded={decoded}/accepted={accepted}")
                if state in ("draft", "final"):
                    if not ev.get("text_zh"):
                        errors.append(f"{sid} idx={idx}: empty text_zh on {state}")
                elif state == "withdrawn":
                    if "text_zh" in ev and ev["text_zh"] is not None:
                        errors.append(f"{sid} idx={idx}: withdrawn carries text_zh")
                else:
                    errors.append(f"{sid} idx={idx}: invalid state {state}")
                if state == "draft":
                    open_line = lid
                else:
                    terminal_lines.add(lid)
                    if open_line == lid:
                        open_line = None
                if state == "final":
                    final_seq += 1
                    final_proj_events.append(ev | dict(seq=final_seq))
                    continue
            if kind != "caption_emit":
                final_proj_events.append(ev)

    causal_errs = validate_causality(final_proj_events)
    errors.extend(f"final_proj: {e}" for e in causal_errs)
    return errors


def play_a2(audio, workers, record, *, clip="clip", base_seed=11, seed_rows=(),
            realtime=False, configure=None, elapsed_override=None, shared_lanes=None,
            tick_ns=TICK_NS, roll_left=0):
    loop = Schedule(realtime=realtime, elapsed_override=elapsed_override, shared_lanes=shared_lanes)
    stream = A2Session(loop, workers, lambda event, **fields: record(event, schedule_ns=loop.now, **fields),
                       on_close=lambda _: None, clip=clip, base_seed=base_seed,
                       seed_rows=seed_rows, tick_ns=tick_ns, roll_left=roll_left)
    for end, packet in pcm_packets(audio):
        loop.at(end * 1_000_000_000 // RATE, partial(stream.accept, end, packet), priority=0)
    loop.at((len(audio) // 2) * 1_000_000_000 // RATE, stream.finish, priority=1)
    if configure:
        configure(loop, stream)
    loop.run(lambda: stream.closed)
    return stream


def run_a2(run, out, seed, *, tick_ms=400, roll_left_ms=0, selected=()):
    manifest = frozen_fingerprint(run)
    root = Path(__file__).parent
    assets = json.loads((run / "asset-manifest.json").read_text())["assets"]
    if selected and set(selected) - {asset["id"] for asset in assets}:
        raise ValueError("Unknown case")
    assets = [asset for asset in assets if not selected or asset["id"] in selected]
    out.mkdir(parents=True, exist_ok=True)
    snap_dir = out / "source-snapshot"
    snap_dir.mkdir(exist_ok=True)
    for p in root.glob("*.py"):
        shutil.copy2(p, snap_dir / p.name)
    dump(out / "run-manifest.json", dict(b0_fingerprint=manifest["fingerprint"], seed=seed,
         mode="virtual_time", tick_ms=tick_ms, roll_left_ms=roll_left_ms,
         cases=[item["id"] for item in assets], code_sha256={p.name: digest(p) for p in root.glob("*.py")}))
    model_lock = json.loads((run / "locks/models.json").read_text())["models"]
    model_paths = {row["id"]: Path(row["local_path"]) for row in model_lock}
    profile = load_settings(run / "locks/profiles.toml").profiles["reazon-hy"]
    mark("model-load", "start")
    recognizer = ReazonRecognizer(model_paths["reazon-b0"], model_paths["silero-v4-b0"], profile.asr)
    translator = HyTranslator(model_paths["hymt-b0"])
    mark("model-load", "end")
    original = rows(run / "baseline/reazon.jsonl")
    expected_mt = {(row["clip"], row["segment_id"]): row["text_zh"]
                   for row in rows(run / f"baseline/mt-seed{seed}.jsonl")}
    log = EventLog(out / "events.jsonl", out.name, experiment_id="A2_probe", repeat_id=f"seed{seed}")
    tick_ns = tick_ms * 1_000_000
    roll_left = roll_left_ms * RATE // 1000
    verdict = []
    try:
        for asset in assets:
            clip = asset["id"]
            data = Path(asset["path"]).read_bytes()
            worker = A2Workers(recognizer, translator, seed, original, clip)
            mark("case/" + clip, "start")
            try:
                stream = play_a2(data, worker, partial(log.record, session_id=clip),
                                 clip=clip, base_seed=seed, seed_rows=original,
                                 tick_ns=tick_ns, roll_left=roll_left)
            finally:
                worker.session.close()
                mark("case/" + clip, "end")
            finals = [c for c in stream.captions if c["state"] == "final"]
            drafts = [c for c in stream.captions if c["state"] == "draft"]
            verdict.append(dict(clip=clip, status=stream.status, samples=stream.accepted,
                                expected_samples=asset["samples"], sources=len(stream.sources),
                                finals=len(finals), drafts=len(drafts)))
            print(json.dumps(verdict[-1]), flush=True)
    finally:
        log.close()

    events = rows(out / "events.jsonl")
    v2_errors = validate_v2_events(events)
    raw_mt_diffs = []
    for ev in events:
        if ev["event"] == "caption_emit" and ev["state"] == "final":
            key = (ev["session_id"], ev.get("segment_id"))
            if key in expected_mt and chinese(expected_mt[key]) != ev["text_zh"]:
                raw_mt_diffs.append(dict(clip=key[0], segment_id=key[1]))

    mt_counts = Counter(ev["kind"] for ev in events if ev["event"] == "mt_start")
    promoted = sum(1 for ev in events if ev["event"] == "mt_promoted")
    stale = sum(1 for ev in events if ev["event"] == "draft_discarded_stale")
    invalid_drafts = sum(1 for ev in events if ev["event"] == "draft_invalid")
    withdrawn = sum(1 for ev in events if ev["event"] == "caption_emit" and ev["state"] == "withdrawn")
    passed = (
        all(r["status"] == "finished" and r["samples"] == r["expected_samples"] for r in verdict)
        and not v2_errors
        and not raw_mt_diffs
    )
    dump(out / "summary.json", dict(
        engineering_pass=passed,
        exact_final_parity=not raw_mt_diffs,
        raw_mt_differences=raw_mt_diffs,
        v2_errors=v2_errors,
        cases=verdict,
        mt_calls=mt_counts,
        mt_promoted=promoted,
        draft_discarded_stale=stale,
        draft_invalid=invalid_drafts,
        withdrawn=withdrawn,
    ))
    return 0 if passed else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, choices=(11, 29), required=True)
    parser.add_argument("--tick-ms", type=int, default=400)
    parser.add_argument("--roll-left-ms", type=int, default=0)
    parser.add_argument("--only", nargs="+", default=[])
    args = parser.parse_args()
    raise SystemExit(run_a2(args.baseline, args.output, args.seed,
                            tick_ms=args.tick_ms, roll_left_ms=args.roll_left_ms, selected=args.only))
