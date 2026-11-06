"""Replay P0 PCM through Silero VAD and the ReazonSpeech k2-v2 offline transducer."""

import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import sherpa_onnx


CACHE = Path(__file__).resolve().parents[1] / ".model-cache"
REVISION = "291488c8151be24d7da4bf7af26e533fad96e407"
MODEL = CACHE / "hub/models--reazon-research--reazonspeech-k2-v2/snapshots" / REVISION
RATE = 16000
FRAME = 3200
THREADS = 2


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def replay(path):
    prepared = time.monotonic()
    audio = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(MODEL / "encoder-epoch-99-avg-1.int8.onnx"),
        decoder=str(MODEL / "decoder-epoch-99-avg-1.onnx"),
        joiner=str(MODEL / "joiner-epoch-99-avg-1.int8.onnx"),
        tokens=str(MODEL / "tokens.txt"),
        num_threads=THREADS, provider="cpu", sample_rate=RATE,
    )
    warmup = recognizer.create_stream()
    warmup.accept_waveform(RATE, np.zeros(RATE, dtype=np.float32))
    recognizer.decode_stream(warmup)

    config = sherpa_onnx.VadModelConfig()
    config.sample_rate = RATE
    config.silero_vad.model = str(CACHE / "reazonspeech-assets/silero_vad.onnx")
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_speech_duration = 0.15
    silence = float(os.environ.get("P0_REAZON_SILENCE_S", "0.35"))
    config.silero_vad.min_silence_duration = silence
    config.silero_vad.max_speech_duration = 4.0
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)

    anchor = time.monotonic()
    emit(type="start", prepare_s=anchor - prepared, threads=THREADS,
         provider="cpu", silero_min_silence_s=silence, silero_soft_limit_s=4.0)

    def drain():
        while not vad.empty():
            segment = vad.front
            start = segment.start / RATE
            samples = segment.samples
            end = start + len(samples) / RATE
            vad.pop()
            submitted = time.monotonic()
            stream = recognizer.create_stream()
            stream.accept_waveform(RATE, samples)
            recognizer.decode_stream(stream)
            finished = time.monotonic()
            emit(type="asr", t=finished - anchor, start=start, end=end, final=True,
                 ja=stream.result.text, vad_t=submitted - anchor, decode_s=finished - submitted)

    max_late = 0.0
    for offset in range(0, len(audio), FRAME):
        chunk = audio[offset:offset + FRAME]
        end = (offset + len(chunk)) / RATE
        time.sleep(max(0, anchor + end - time.monotonic()))
        max_late = max(max_late, time.monotonic() - anchor - end)
        vad.accept_waveform(chunk)
        drain()
    input_end = time.monotonic() - anchor
    vad.flush()
    drain()
    emit(type="eof", t=time.monotonic() - anchor, input_end=input_end,
         input_bytes=len(audio) * 2, max_feed_late_ms=max_late * 1000)


if __name__ == "__main__":
    replay(Path(sys.argv[1]))
