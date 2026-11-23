"""Real-model drain and cancel+reopen lifecycle smoke probe for A1a."""
import json
import sys
from pathlib import Path
from time import monotonic_ns

from p0.p2.freeze import dump, rows
from p0.p2.runner import pcm_packets
from subtitle_service.config import load_settings
from subtitle_service.local_pipeline import HyTranslator, ReazonRecognizer

from .clock import Schedule
from .rolling import DRAIN_NS, RATE, RollingSession
from .run_a1 import Workers

WATCHDOG_NS = 30 * 1_000_000_000


def _watchdog():
    raise TimeoutError("probe_lifecycle watchdog exceeded 30s")


def main():
    a0 = Path(sys.argv[1])
    out = Path(sys.argv[2])

    model_lock = json.loads((a0 / "locks/models.json").read_text())["models"]
    model_paths = {row["id"]: Path(row["local_path"]) for row in model_lock}
    profile = load_settings(a0 / "locks/profiles.toml").profiles["reazon-hy"]
    recognizer = ReazonRecognizer(model_paths["reazon-b0"], model_paths["silero-v4-b0"], profile.asr)
    translator = HyTranslator(model_paths["hymt-b0"])
    original = rows(a0 / "baseline/reazon.jsonl")
    assets = {a["id"]: a for a in json.loads((a0 / "asset-manifest.json").read_text())["assets"]}

    clip_id = "first-photo"
    pcm = Path(assets[clip_id]["path"]).read_bytes()
    total_samples = len(pcm) // 2
    packets = list(pcm_packets(pcm))

    # 1. Worst-case drain probe: trigger finish() while ASR is in-flight and vad_pending is non-empty.
    w1 = Workers(recognizer, translator, 11, original, clip_id)
    loop1 = Schedule(realtime=True)
    trace1 = []
    fed_idx1 = 0
    burst_done = False

    def record1(ev, **kw):
        nonlocal burst_done, fed_idx1
        trace1.append(dict(event=ev, schedule_ns=loop1.now, t_ns=monotonic_ns(), **kw))
        if ev == "asr_start" and not burst_done:
            burst_done = True
            # Note: submit() adds "asr" to s1.inflight immediately after record("asr_start"),
            # so schedule burst + finish at priority=0 right now on the loop before worker completes.
            def do_burst():
                nonlocal fed_idx1
                limit = min(len(packets), fed_idx1 + 8)
                while fed_idx1 < limit:
                    end, frame = packets[fed_idx1]
                    fed_idx1 += 1
                    s1.accept(end, frame)
                s1.finish()

            loop1.at(loop1.now, do_burst, priority=0)

    s1 = RollingSession(loop1, w1, record1, on_close=lambda _: None)

    def feed1(idx):
        nonlocal fed_idx1
        if burst_done:
            return
        if idx >= len(packets):
            s1.finish()
            return
        end, frame = packets[idx]
        fed_idx1 = idx + 1
        s1.accept(end, frame)
        if not burst_done:
            loop1.at(end * 1_000_000_000 // RATE, lambda: feed1(idx + 1), priority=0)

    loop1.at(0, lambda: feed1(0), priority=0)
    loop1.at(total_samples * 1_000_000_000 // RATE, s1.finish, priority=1)
    loop1.at(WATCHDOG_NS, _watchdog)
    try:
        loop1.run(lambda: s1.closed)
    finally:
        w1.session.close()

    fin_ev = next(e for e in trace1 if e["event"] == "finish_received")
    closed_ev = next(e for e in trace1 if e["event"] == "closed")
    drain_wall_ms = (closed_ev["t_ns"] - fin_ev["t_ns"]) / 1e6
    expected_fed_samples = packets[fed_idx1 - 1][0]
    assert s1.status == "finished", f"Expected finished, got {s1.status}"
    assert fin_ev["asr_inflight"] is True, f"Expected asr_inflight=True, got {fin_ev}"
    assert fin_ev["vad_pending"] >= 1, f"Expected vad_pending>=1, got {fin_ev}"
    assert s1.accepted == expected_fed_samples
    assert drain_wall_ms < DRAIN_NS / 1e6

    # 2. Real-model cancel + busy lease + reopen probe
    w2 = Workers(recognizer, translator, 11, original, clip_id)
    loop2 = Schedule(realtime=True)
    trace2 = []
    active_lease = {"session": None}

    def release(sess):
        if active_lease["session"] is sess:
            active_lease["session"] = None

    cancelled = False
    busy_while_closing = None

    def record2(ev, **kw):
        nonlocal cancelled, busy_while_closing
        trace2.append(dict(event=ev, schedule_ns=loop2.now, t_ns=monotonic_ns(), **kw))
        if ev == "asr_start" and not cancelled:
            cancelled = True

            def do_cancel():
                nonlocal busy_while_closing
                s2.finish()
                s2.cancel()
                s2.cancel()
                assert not s2.closed
                busy_while_closing = active_lease["session"] is not None

            loop2.at(loop2.now, do_cancel, priority=0)

    s2 = RollingSession(loop2, w2, record2, on_close=release)
    active_lease["session"] = s2

    def feed2(idx):
        if cancelled or idx >= len(packets):
            return
        end, frame = packets[idx]
        s2.accept(end, frame)
        if not cancelled:
            loop2.at(end * 1_000_000_000 // RATE, lambda: feed2(idx + 1), priority=0)

    loop2.at(0, lambda: feed2(0), priority=0)
    loop2.at(WATCHDOG_NS, _watchdog)
    try:
        loop2.run(lambda: s2.closed)
    finally:
        w2.session.close()

    assert cancelled and busy_while_closing
    assert s2.status == "cancel" and s2.closed and active_lease["session"] is None
    assert len(s2.captions) == 0

    # Immediately reopen a new session on the same recognizer/translator
    w3 = Workers(recognizer, translator, 11, original, clip_id)
    loop3 = Schedule(realtime=True)
    trace3 = []
    s3 = RollingSession(
        loop3, w3,
        lambda ev, **kw: trace3.append(dict(event=ev, schedule_ns=loop3.now, t_ns=monotonic_ns(), **kw)),
        on_close=release,
    )
    active_lease["session"] = s3
    for end, frame in packets:
        loop3.at(end * 1_000_000_000 // RATE, lambda e=end, f=frame: s3.accept(e, f), priority=0)
    loop3.at(total_samples * 1_000_000_000 // RATE, s3.finish, priority=1)
    loop3.at(WATCHDOG_NS, _watchdog)
    try:
        loop3.run(lambda: s3.closed)
    finally:
        w3.session.close()

    assert s3.status == "finished" and s3.closed and active_lease["session"] is None
    assert len(s3.captions) == 2

    result = dict(
        drain_probe=dict(
            clip=clip_id,
            status=s1.status,
            accepted_samples=s1.accepted,
            finish_asr_inflight=fin_ev["asr_inflight"],
            finish_vad_pending=fin_ev["vad_pending"],
            drain_wall_ms=drain_wall_ms,
            within_8s_budget=drain_wall_ms < 8000.0,
            captions=len(s1.captions),
        ),
        cancel_reopen_probe=dict(
            clip=clip_id,
            cancel_status=s2.status,
            busy_while_closing=busy_while_closing,
            cancel_captions=len(s2.captions),
            reopen_status=s3.status,
            reopen_captions=len(s3.captions),
        ),
    )
    dump(out, result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
