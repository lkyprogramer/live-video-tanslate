"""Experimental A1b sentence-level rolling scheduler and causal simulator."""
import argparse
from collections import Counter, deque
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
    RIGHT,
    TICK_NS,
    VAD_PRE_ROLL,
    Tokens,
    Window,
    stable_pieces,
)

SOFT_LIMIT = 6 * RATE
HARD_LIMIT = 10 * RATE
MIN_FORCED_SPAN = 2 * RATE


@dataclass(frozen=True)
class A1bVadUpdate:
    active_start: int | None
    releases: tuple[tuple[int, int, str], ...]


@dataclass(frozen=True)
class A1bBarrier:
    start: int
    end: int
    released_at: int
    reason: str
    index: int
    covered_through: int
    precomputed_tokens: Tokens | None = None


class A1bWorkers:
    def __init__(self, recognizer, translator, seed, baseline, clip):
        self.recognizer = recognizer
        self.vad_detector = recognizer.new_vad()
        self.shift = recognizer.vad_config.silero_vad.window_size
        self.translator = translator
        self.session = translator.new_session()
        self.seed = seed
        self.seed_rows = baseline
        self.clip = clip

    def vad(self, payload):
        accepted, frame = payload
        tail = accepted // self.shift * self.shift
        releases = []
        for start, samples in vad_segments(self.vad_detector, frame):
            end = start + len(samples)
            if frame is None:
                reason = "eof"
            else:
                trim = tail - end
                reason = {9600: "natural_pause", 1600: "length_managed_pause"}.get(trim, "unresolved")
                assert reason != "unresolved", f"unresolved VAD trim={trim} at accepted={accepted}"
            releases.append((start, end, reason))
        active = self.vad_detector.current_segment.start if self.vad_detector.is_speech_detected() else None
        assert all(0 <= start < stop <= accepted for start, stop, _ in releases)
        assert active is None or 0 <= active < accepted
        return A1bVadUpdate(active, tuple(releases))

    def asr(self, window):
        audio = np.frombuffer(window.pcm, dtype="<i2").astype(np.float32) / 32768
        result = decode(self.recognizer, audio, 0, len(audio))
        tokens = tuple(result["tokens"])
        return Tokens(result["text_ja"], tokens, tuple(window.start + a for a in result["anchors"]))

    def mt(self, source):
        import mlx.core as mx
        key = dict(
            clip=self.clip,
            start_sample=source["start_sample"],
            end_sample=source["vad_end_sample"],
            text_ja=source["text_ja"],
        )
        mx.random.seed(self.seed + source_seed(key, self.seed_rows))
        raw = self.translator.translate(self.session, source["text_ja"])
        return raw, chinese(raw)


class A1bSession:
    """Sentence-level commit scheduler built on A1a rolling ASR."""

    def __init__(self, loop, workers, record, *, on_close):
        self.loop, self.workers, self.record = loop, workers, record
        self.on_close = on_close
        self.accepted = self.vad_through = self.decoded_through = 0
        self.floor = 0
        self.pcm = bytearray()
        self.unit_start = None
        self.unit_covered = 0
        self.soft_limit_logged = False
        self.last_rolling = None
        self.last_stable = None
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

    def _enqueue_barrier(self, start, end, released_at, reason, precomputed=None):
        self.releases += 1
        covered = max(start, min(self.unit_covered, end))
        self.barriers.append(A1bBarrier(start, end, released_at, reason, self.releases, covered, precomputed))

    def _reset_unit(self, new_start=None, new_covered=None):
        self.unit_start = new_start
        self.unit_covered = new_start if new_covered is None and new_start is not None else (new_covered or 0)
        self.soft_limit_logged = False
        self.last_rolling = None
        self.last_stable = None
        self.last_seen_end = 0
        self.next_tick_ns = inf
        self.roll_due = False
        if self.unit_start is not None and self.finish_ns is None:
            self.next_tick_ns = self.loop.now
            self.tick(self.unit_start, self.next_tick_ns)

    def _find_forced_limit_cut(self):
        if not self.last_stable or not self.last_rolling:
            return None
        pieces, anchors, _near_crop = self.last_stable
        last_win, last_toks = self.last_rolling
        if not pieces or not anchors:
            return None
        last_stable_anchor = anchors[-1]
        next_anchor = None
        for anc in last_toks.anchors:
            if anc > last_stable_anchor:
                next_anchor = anc
                break
        if next_anchor is None:
            return None
        if not (self.unit_start < next_anchor <= last_win.end - RIGHT):
            return None
        if next_anchor - self.unit_start < MIN_FORCED_SPAN:
            return None
        if next_anchor <= self.source_end or next_anchor > self.decoded_through:
            return None
        text_ja = "".join(pieces).replace("▁", " ").strip()
        if not text_ja:
            return None
        return next_anchor, Tokens(text_ja, pieces, anchors)

    def _handle_hard_limit(self, end, *, keep_open_after_stop=True, vad_stop=None):
        cut = self._find_forced_limit_cut()
        if cut is not None:
            next_anchor, precomputed = cut
            self._enqueue_barrier(self.unit_start, next_anchor, end, "forced_limit", precomputed=precomputed)
            self._reset_unit(next_anchor, max(self.unit_covered, next_anchor))
            if end - self.unit_start >= SOFT_LIMIT:
                self.soft_limit_logged = True
                self.record("soft_limit_reached", unit_start=self.unit_start, end_sample=end)
            return
        hard_end = vad_stop if vad_stop is not None else min(end, self.unit_start + HARD_LIMIT)
        if hard_end <= self.unit_start:
            self.terminate("adapter_unaligned")
            return
        self._enqueue_barrier(self.unit_start, hard_end, end, "forced_unstable")
        if keep_open_after_stop or hard_end < end:
            self._reset_unit(hard_end, max(self.unit_covered, hard_end))
        else:
            self._reset_unit(None, 0)

    def observed(self, end, frame, update):
        self.vad_through = end
        self.record("vad_completed", vad_through_sample=end, active_start_sample=update.active_start,
                    releases=update.releases, eof=frame is None)
        for start, stop, reason in update.releases:
            if self.unit_start is None:
                committed_or_queued = max(
                    self.source_end,
                    self.barriers[-1].end if self.barriers else 0,
                    self.asr_barrier.end if self.asr_barrier else 0,
                )
                if stop <= committed_or_queued:
                    continue
                self.unit_start = max(start, committed_or_queued)
                self.unit_covered = self.unit_start
            if stop <= self.unit_start:
                continue
            assert 0 <= start < stop <= end
            if reason == "length_managed_pause":
                if stop - self.unit_start < HARD_LIMIT:
                    self.record("vad_length_managed_continued", unit_start=self.unit_start,
                                vad_start=start, vad_stop=stop, duration_samples=stop - self.unit_start)
                else:
                    self._handle_hard_limit(end, keep_open_after_stop=(update.active_start is not None), vad_stop=stop)
            elif reason == "natural_pause":
                self._enqueue_barrier(self.unit_start, stop, end, "natural_pause")
                self._reset_unit(None, 0)
            elif reason == "eof":
                self._enqueue_barrier(self.unit_start, stop, end, "eof")
                self._reset_unit(None, 0)
        if update.active_start is not None and self.unit_start is None:
            self._reset_unit(update.active_start, update.active_start)
        if self.unit_start is not None and frame is not None:
            elapsed = end - self.unit_start
            if not self.soft_limit_logged and elapsed >= SOFT_LIMIT:
                self.soft_limit_logged = True
                self.record("soft_limit_reached", unit_start=self.unit_start, end_sample=end)
            if end - self.unit_start >= HARD_LIMIT:
                self._handle_hard_limit(end, keep_open_after_stop=True)
        if frame is None:
            if self.unit_start is not None:
                # Open unit across a brief dip before EOF without a flush release.
                self._enqueue_barrier(self.unit_start, end, end, "eof")
                self._reset_unit(None, 0)
            self.flushed = True

    def tick(self, start, scheduled):
        if self.status or self.finish_ns is not None or self.unit_start != start or self.next_tick_ns != scheduled:
            return
        self.record("tick", requested_end_sample=self.accepted, coalesced=self.roll_due, asr_busy="asr" in self.inflight)
        self.roll_due = True
        self.next_tick_ns = scheduled + TICK_NS
        self.loop.at(self.next_tick_ns, lambda: self.tick(start, scheduled + TICK_NS))
        self.pump()

    def fetch(self, start, end):
        assert self.floor <= start < end <= self.accepted
        return Window(start, end, bytes(self.pcm[2 * (start - self.floor):2 * (end - self.floor)]))

    def start_asr(self, barrier):
        unit_start = self.unit_start
        if barrier:
            if barrier.precomputed_tokens is not None:
                start, end = barrier.start, barrier.end
                window = self.fetch(start, end)
                self.complete_barrier(barrier, window, barrier.precomputed_tokens)
                return
            start = barrier.start
            end = barrier.end
        else:
            start = max(0, unit_start - LEFT)
            end = self.accepted
            self.last_seen_end = end
            if end <= unit_start:
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
            elif unit_start == self.unit_start:
                self.unit_covered = max(self.unit_covered, end)
                if self.last_rolling and self.finish_ns is None:
                    stable, stable_anchors, near_crop = stable_pieces(
                        self.last_rolling, (window, tokens), unit_start,
                        prev_tail_tokens=self.prev_tail_tokens,
                    )
                    self.last_stable = (stable, stable_anchors, near_crop) if stable else None
                    self.record("stable_candidate", start_sample=unit_start, end_sample=end,
                                text_ja="".join(stable).replace("▁", " ").strip(),
                                tokens=stable, anchors=stable_anchors, near_crop_boundary=near_crop)
                self.last_rolling = window, tokens
            elif unit_start is not None:
                self.barriers = deque(replace(item, covered_through=max(item.covered_through, end))
                                      if item.start == unit_start else item for item in self.barriers)

        self.submit("asr", window, completed)

    def complete_barrier(self, barrier, window, tokens):
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
        if self.unit_start is not None:
            intervals.append((self.unit_covered, self.vad_through))
        barriers = list(self.barriers)
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
                self.last_stable = None
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
        retain.extend(barrier.start for barrier in self.barriers)
        if self.unit_start is not None:
            retain.append(max(0, self.unit_start - LEFT))
        floor = min(retain)
        assert floor >= self.floor
        del self.pcm[:2 * (floor - self.floor)]
        self.floor = floor
        if "vad" not in self.inflight and self.vad_pending:
            end, frame = self.vad_pending.popleft()
            self.submit("vad", (end, frame), lambda result: self.observed(end, frame, result))
        while "asr" not in self.inflight and self.barriers and self.barriers[0].precomputed_tokens is not None:
            self.start_asr(self.barriers.popleft())
            if self.status is not None:
                return
        if "asr" not in self.inflight:
            if self.barriers:
                self.start_asr(self.barriers.popleft())
            elif self.roll_due and self.unit_start is not None and self.accepted > self.last_seen_end:
                self.roll_due = False
                self.start_asr(None)
        if self.status is None and "mt" not in self.inflight and self.mt_pending:
            self.start_mt()
        if self.status is None and self.flushed and not self.inflight and not self.barriers and not self.mt_pending:
            self.terminate("finished")


def play_a1b(audio, workers, record, *, realtime=False, configure=None, elapsed_override=None):
    loop = Schedule(realtime=realtime, elapsed_override=elapsed_override)
    stream = A1bSession(loop, workers, lambda event, **fields: record(event, schedule_ns=loop.now, **fields),
                        on_close=lambda _: None)
    for end, packet in pcm_packets(audio):
        loop.at(end * 1_000_000_000 // RATE, partial(stream.accept, end, packet), priority=0)
    loop.at((len(audio) // 2) * 1_000_000_000 // RATE, stream.finish, priority=1)
    if configure:
        configure(loop, stream)
    loop.run(lambda: stream.closed)
    return stream


def run_a1b(run, out, seed, realtime, selected):
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
         mode="realtime_1x" if realtime else "virtual_time",
         cases=[item["id"] for item in assets], code_sha256={p.name: digest(p) for p in root.glob("*.py")},
         event_order=["pcm", "finish_or_worker_completion", "tick_or_drain_deadline"],
         vad_worker="separate lane", source="raw PCM; no reference text or mapped boundaries delivered to scheduler"))
    model_lock = json.loads((run / "locks/models.json").read_text())["models"]
    model_paths = {row["id"]: Path(row["local_path"]) for row in model_lock}
    profile = load_settings(run / "locks/profiles.toml").profiles["reazon-hy"]
    mark("model-load", "start")
    recognizer = ReazonRecognizer(model_paths["reazon-b0"], model_paths["silero-v4-b0"], profile.asr)
    translator = HyTranslator(model_paths["hymt-b0"])
    mark("model-load", "end")
    original = rows(run / "baseline/reazon.jsonl")
    log = EventLog(out / "events.jsonl", out.name, experiment_id="A1b", repeat_id=f"seed{seed}")
    verdict = []
    try:
        for asset in assets:
            clip = asset["id"]
            data = Path(asset["path"]).read_bytes()
            worker = A1bWorkers(recognizer, translator, seed, original, clip)
            mark("case/" + clip, "start")
            try:
                stream = play_a1b(data, worker, partial(log.record, session_id=clip), realtime=realtime)
            finally:
                worker.session.close()
                mark("case/" + clip, "end")
            verdict.append(dict(clip=clip, status=stream.status, samples=stream.accepted,
                                expected_samples=asset["samples"], sources=len(stream.sources),
                                captions=len(stream.captions)))
            print(json.dumps(verdict[-1]), flush=True)
    finally:
        log.close()

    events = rows(out / "events.jsonl")
    causal = validate_causality(events)
    assets_by_id = {asset["id"]: Path(asset["path"]).read_bytes() for asset in assets}
    by_clip = {}
    for ev in events:
        by_clip.setdefault(ev["session_id"], []).append(ev)
    audit_errors = []
    asr_input_hashes_checked = 0
    drain_ms_by_clip = {}
    boundary_counts = Counter()
    for clip_id, trace in by_clip.items():
        acc = con = dec = 0
        win = None
        fin_sched = fin_wall = None
        pcm_bytes = assets_by_id[clip_id]
        for ev in trace:
            kind = ev["event"]
            if kind == "finish_received":
                fin_sched = ev["schedule_ns"]
                fin_wall = ev["t_ns"]
            elif kind == "pcm_accepted":
                cur = ev["accepted_samples"]
                if cur <= acc or sha256(pcm_bytes[acc * 2:cur * 2]).hexdigest() != ev["pcm_sha256"]:
                    audit_errors.append(f"{clip_id}: bad pcm_accepted at {cur}")
                acc = cur
            elif kind == "pcm_consumed":
                con = ev["consumed_samples"]
                if con != acc:
                    audit_errors.append(f"{clip_id}: consumed {con} != accepted {acc}")
            elif kind == "asr_start":
                if win is not None:
                    audit_errors.append(f"{clip_id}: overlapping asr_start")
                s, e = win = ev["start_sample"], ev["end_sample"]
                if not (0 <= s < e <= con) or sha256(pcm_bytes[s * 2:e * 2]).hexdigest() != ev["pcm_sha256"]:
                    audit_errors.append(f"{clip_id}: bad asr_start [{s}, {e}]")
                asr_input_hashes_checked += 1
            elif kind == "asr_completed":
                if win != (ev["start_sample"], ev["end_sample"]):
                    audit_errors.append(f"{clip_id}: mismatched asr_completed")
                dec = max(dec, win[1])
                if not (ev["decoded_through_sample"] == dec <= con <= acc):
                    audit_errors.append(f"{clip_id}: bad decoded_through_sample")
                win = None
            elif kind == "source_commit":
                boundary_counts[ev["boundary_reason"]] += 1
                if ev["end_sample"] > dec or not (0 <= ev.get("eof_unclassified_tail_samples", 0) < 512):
                    audit_errors.append(f"{clip_id}: bad source_commit")
            elif kind == "caption_emit":
                if ev["end_sample"] > dec:
                    audit_errors.append(f"{clip_id}: caption_emit exceeds decoded_through_sample")
            elif kind == "finished":
                if acc != len(pcm_bytes) // 2 or win is not None:
                    audit_errors.append(f"{clip_id}: incomplete finish")
                drain_ms_by_clip[clip_id] = dict(
                    schedule_ms=(ev["schedule_ns"] - fin_sched) / 1e6,
                    wall_ms=(ev["t_ns"] - fin_wall) / 1e6,
                )
        if clip_id == "silence" and any(e["event"] == "caption_emit" for e in trace):
            audit_errors.append("silence produced captions")

    by_kind = Counter((row["kind"] for row in events if row["event"] == "asr_start"))
    passed = all(row["status"] == "finished" and row["samples"] == row["expected_samples"] for row in verdict) and not causal and not audit_errors
    b0_audio_s = sum((r["end_sample"] - r["start_sample"]) / RATE for r in original)
    decoded_s = sum((row["end_sample"] - row["start_sample"]) / RATE for row in events if row["event"] == "asr_start")
    dump(out / "summary.json", dict(
        engineering_pass=passed,
        cases=verdict,
        causality_errors=causal,
        audit_errors=audit_errors,
        asr_input_hashes_checked=asr_input_hashes_checked,
        boundary_counts=boundary_counts,
        decode_counts=by_kind,
        total_decoded_audio_seconds=decoded_s,
        decode_audio_ratio_to_b0=decoded_s / b0_audio_s,
        max_drain_schedule_ms=max((d["schedule_ms"] for d in drain_ms_by_clip.values()), default=0.0),
        max_drain_wall_ms=max((d["wall_ms"] for d in drain_ms_by_clip.values()), default=0.0),
    ))
    return 0 if passed else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, choices=(11, 29), required=True)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--only", nargs="+", default=[])
    args = parser.parse_args()
    raise SystemExit(run_a1b(args.baseline, args.output, args.seed, args.realtime, args.only))
