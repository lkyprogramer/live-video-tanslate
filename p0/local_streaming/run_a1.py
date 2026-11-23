"""Run A1a over frozen PCM and write replayable event traces."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from hashlib import sha256
import json
from pathlib import Path
import shutil

import numpy as np

from p0.p2.b3_protocol import source_seed
from p0.p2.events import EventLog, validate_causality
from p0.p2.freeze import dump, rows, frozen_fingerprint, digest
from p0.p2.runner import pcm_packets
from p0.monitor import mark
from subtitle_service.app import chinese
from subtitle_service.config import load_settings
from subtitle_service.local_pipeline import HyTranslator, ReazonRecognizer, vad_segments

from .a0 import decode
from .clock import Schedule
from .rolling import LEFT, RATE, TICK_NS, RollingSession, Tokens, VadUpdate


class Workers:
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

    def mt(self, source):
        import mlx.core as mx
        key = source | dict(clip=self.clip, end_sample=source["vad_end_sample"])
        mx.random.seed(self.seed + source_seed(key, self.seed_rows))
        raw = self.translator.translate(self.session, source["text_ja"])
        return raw, chinese(raw)


def play(audio, workers, record, *, realtime=False, configure=None, elapsed_override=None,
         shared_lanes=None, tick_ns=TICK_NS, roll_left=LEFT):
    loop = Schedule(realtime=realtime, elapsed_override=elapsed_override, shared_lanes=shared_lanes)
    stream = RollingSession(loop, workers, lambda event, **fields: record(event, schedule_ns=loop.now, **fields),
                            on_close=lambda _: None, tick_ns=tick_ns, roll_left=roll_left)
    for end, packet in pcm_packets(audio):
        loop.at(end * 1_000_000_000 // RATE, partial(stream.accept, end, packet), priority=0)
    loop.at((len(audio)//2) * 1_000_000_000 // RATE, stream.finish, priority=1)
    if configure:
        configure(loop, stream)
    loop.run(lambda: stream.closed)
    return stream


def run_a1(run, out, seed, realtime, selected, *, roll_left_ms=800, tick_ms=800):
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
         roll_left_ms=roll_left_ms, tick_ms=tick_ms,
         cases=[item["id"] for item in assets], code_sha256={p.name:digest(p) for p in root.glob("*.py")},
         event_order=["pcm", "finish_or_worker_completion", "tick_or_drain_deadline"],
         vad_worker="separate lane", source="raw PCM; no reference text or mapped boundaries delivered to scheduler"))
    model_lock = json.loads((run / "locks/models.json").read_text())["models"]
    model_paths = {row["id"]: Path(row["local_path"]) for row in model_lock}
    profile = load_settings(run / "locks/profiles.toml").profiles["reazon-hy"]
    mt_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="a1-mt") if realtime else None
    mark("model-load", "start")
    recognizer = ReazonRecognizer(model_paths["reazon-b0"], model_paths["silero-v4-b0"], profile.asr)
    translator = (
        mt_executor.submit(HyTranslator, model_paths["hymt-b0"]).result()
        if mt_executor is not None
        else HyTranslator(model_paths["hymt-b0"])
    )
    mark("model-load", "end")
    original = rows(run / "baseline/reazon.jsonl")
    baseline_asr = {(row["clip"], row["segment_id"]): row for row in original}
    expected_mt = {(row["clip"], row["segment_id"]): row["text_zh"]
                   for row in rows(run / f"baseline/mt-seed{seed}.jsonl")}
    log = EventLog(out / "events.jsonl", out.name, experiment_id="A1a", repeat_id=f"seed{seed}")
    verdict = []
    roll_left = roll_left_ms * RATE // 1000
    tick_ns = tick_ms * 1_000_000
    shared_lanes = {"mt": mt_executor} if mt_executor is not None else None
    try:
        for asset in assets:
            clip = asset["id"]
            data = Path(asset["path"]).read_bytes()
            worker = Workers(recognizer, translator, seed, original, clip)
            mark("case/" + clip, "start")
            try:
                stream = play(data, worker, partial(log.record, session_id=clip),
                              realtime=realtime, shared_lanes=shared_lanes,
                              tick_ns=tick_ns, roll_left=roll_left)
            finally:
                worker.session.close()
                mark("case/" + clip, "end")
            actual_source = {(clip, row["segment_id"]): row for row in stream.sources}
            actual_captions = {(clip, row["segment_id"]): row for row in stream.captions}
            errors = []
            for key in sorted(set(actual_source) | {key for key in baseline_asr if key[0] == clip}):
                expect, found = baseline_asr.get(key), actual_source.get(key)
                if (expect is None or found is None
                        or (found["start_sample"], found["vad_end_sample"], found["text_ja"])
                        != (expect["start_sample"], expect["end_sample"], expect["text_ja"])):
                    errors.append(dict(kind="asr_difference", segment_id=key[1]))
                if key in expected_mt:
                    filtered = chinese(expected_mt[key])
                    item = actual_captions.get(key)
                    if filtered != (item and item["text_zh"]):
                        errors.append(dict(kind="caption_difference", segment_id=key[1]))
            verdict.append(dict(clip=clip, status=stream.status, samples=stream.accepted,
                    expected_samples=asset["samples"], sources=len(stream.sources), captions=len(stream.captions),
                    errors=errors))
            print(json.dumps(verdict[-1]), flush=True)
    finally:
        log.close()
        if mt_executor is not None:
            mt_executor.shutdown(wait=True)
    events = rows(out / "events.jsonl")
    causal = validate_causality(events)
    assets_by_id = {asset["id"]: Path(asset["path"]).read_bytes() for asset in assets}
    by_clip = defaultdict(list)
    for ev in events:
        by_clip[ev["session_id"]].append(ev)
    audit_errors = []
    drain_ms_by_clip = {}
    first_any_zh_ms_by_clip = {}
    asr_input_hashes_checked = 0
    for clip_id, trace in by_clip.items():
        acc = con = dec = 0
        win = None
        fin_sched = fin_wall = first_sched = first_wall = None
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
                if ev["end_sample"] > dec or not (0 <= ev.get("eof_unclassified_tail_samples", 0) < 512):
                    audit_errors.append(f"{clip_id}: bad source_commit")
            elif kind == "caption_emit":
                if ev["end_sample"] > dec:
                    audit_errors.append(f"{clip_id}: caption_emit exceeds decoded_through_sample")
                if first_sched is None:
                    first_sched = ev["schedule_ns"]
                    first_wall = ev["t_ns"]
            elif kind == "finished":
                if acc != len(pcm_bytes) // 2 or win is not None:
                    audit_errors.append(f"{clip_id}: incomplete finish")
                drain_ms_by_clip[clip_id] = dict(
                    schedule_ms=(ev["schedule_ns"] - fin_sched) / 1e6,
                    wall_ms=(ev["t_ns"] - fin_wall) / 1e6,
                )
        first_any_zh_ms_by_clip[clip_id] = (
            dict(schedule_ms=first_sched / 1e6, wall_ms=first_wall / 1e6) if first_sched is not None else None
        )
    by_kind = Counter((row["kind"] for row in events if row["event"] == "asr_start"))
    raw_changes = []
    for event in events:
        key = event["session_id"], event.get("segment_id")
        if event["event"] == "mt_completed" and key in expected_mt and expected_mt[key] != event["raw_zh"]:
            raw_changes.append(dict(clip=key[0], segment_id=key[1]))
    for row in verdict:
        row["drain_ms"] = drain_ms_by_clip.get(row["clip"])
        row["first_any_zh_ms"] = first_any_zh_ms_by_clip.get(row["clip"])
    passed = all(row["status"] == "finished" and row["samples"] == row["expected_samples"] and not row["errors"]
                 for row in verdict) and not causal and not raw_changes and not audit_errors
    dump(out / "summary.json", dict(engineering_pass=passed, cases=verdict,
         exact_frozen_translation=not raw_changes, raw_mt_differences=raw_changes,
         causality_errors=causal, audit_errors=audit_errors,
         asr_input_hashes_checked=asr_input_hashes_checked,
         max_drain_schedule_ms=max((d["schedule_ms"] for d in drain_ms_by_clip.values()), default=0.0),
         max_drain_wall_ms=max((d["wall_ms"] for d in drain_ms_by_clip.values()), default=0.0),
         decode_counts=by_kind, total_decoded_audio_seconds=sum(
             (row["end_sample"]-row["start_sample"])/RATE for row in events if row["event"] == "asr_start"),
         references="provisional; exact output parity reuses the frozen labels only"))
    return 0 if passed else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, choices=(11, 29), required=True)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--roll-left-ms", type=int, default=800)
    parser.add_argument("--tick-ms", type=int, default=800)
    parser.add_argument("--only", nargs="+", default=[])
    args = parser.parse_args()
    raise SystemExit(run_a1(args.baseline, args.output, args.seed, args.realtime, args.only,
                            roll_left_ms=args.roll_left_ms, tick_ms=args.tick_ms))
