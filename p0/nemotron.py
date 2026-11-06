"""Emit live Nemotron ASR events for the P0 JSONL translation runner."""

import json
from pathlib import Path
import re
import sys
import time

import mlx.core as mx
from mlx_audio.stt import load
import numpy as np


MODEL = "nemotron-3.5-asr-streaming-0.6b"
REVISION = "e550040c0478027ed679b2b6b0d055502c103663"
MODEL_PATH = (Path(__file__).resolve().parents[1] / ".model-cache" /
              f"hub/models--mlx-community--{MODEL}/snapshots/{REVISION}")
RATE = 16000
FRAME = 3200
QUIET_SECONDS = 1.2
SENTENCE = re.compile(r"^(.+?[。！？!?])", re.DOTALL)


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def replay(path):
    prepared = time.monotonic()
    audio = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768
    model = load(MODEL_PATH)
    # One native chunk is seven 80 ms encoder frames, including the current frame.
    model.default_att_context_size = [56, 6]
    warmup = model.create_streaming_session(language="ja-JP")
    warmup.feed(np.zeros(RATE, dtype=np.float32))
    warmup.close()
    while not warmup.done:
        warmup.step(max_decode_tokens=128)
    del warmup
    session = model.create_streaming_session(language="ja-JP")
    if session.input_sample_rate != RATE or session.att_context_size != [56, 6]:
        raise RuntimeError("Unexpected Nemotron audio or streaming configuration")
    anchor = time.monotonic()
    emit(type="start", prepare_s=anchor - prepared, lookahead=[56, 6], language="ja-JP")

    pending = ""
    segment_start = 0.0
    last_text = 0.0

    def consume(deltas, audio_end, *, eof=False):
        nonlocal pending, segment_start, last_text
        if deltas:
            pending += "".join(deltas)
            last_text = audio_end
            emit(type="asr", t=time.monotonic() - anchor, start=segment_start,
                 end=audio_end, final=False, ja=pending)
        while match := SENTENCE.match(pending.lstrip()):
            emit(type="asr", t=time.monotonic() - anchor, start=segment_start,
                 end=last_text, final=True, ja=match[1])
            pending = pending.lstrip()[match.end():]
            segment_start = last_text
        if pending.strip() and (eof or audio_end - last_text >= QUIET_SECONDS):
            emit(type="asr", t=time.monotonic() - anchor, start=segment_start,
                 end=last_text, final=True, ja=pending.strip())
            pending = ""
            segment_start = last_text

    max_late = 0.0
    for offset in range(0, len(audio), FRAME):
        chunk = audio[offset:offset + FRAME]
        end = (offset + len(chunk)) / RATE
        time.sleep(max(0, anchor + end - time.monotonic()))
        max_late = max(max_late, time.monotonic() - anchor - end)
        session.feed(chunk)
        consume(session.step(max_decode_tokens=128), end)
    input_end = time.monotonic() - anchor
    session.close()
    while not session.done:
        consume(session.step(max_decode_tokens=128), len(audio) / RATE)
    consume([], len(audio) / RATE, eof=True)
    emit(type="eof", t=time.monotonic() - anchor, input_end=input_end,
         input_bytes=len(audio) * 2, max_feed_late_ms=max_late * 1000,
         mlx_peak_bytes=mx.get_peak_memory())


if __name__ == "__main__":
    replay(Path(sys.argv[1]))
