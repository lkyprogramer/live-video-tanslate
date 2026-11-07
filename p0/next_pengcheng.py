"""Run a Japanese-conditioned PengChengStarling stream through the v1 benchmark."""

import argparse
import asyncio
from dataclasses import dataclass
from itertools import count
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import sherpa_onnx
import uvicorn

from review import P0
from subtitle_service import replay
from subtitle_service.app import FRAME_BYTES, create_app
from subtitle_service.backend import BackendError
from subtitle_service.cli import token_path
from subtitle_service.config import load_settings, load_token
from subtitle_service.local_pipeline import HyTranslator, LocalBackend, RATE, RecognizedSegment


@dataclass
class StreamState:
    stream: sherpa_onnx.OnlineStream
    accepted: int = 0
    last_end: int = 0
    previous: str = ""
    stable: str = ""


class PengChengRecognizer:
    def __init__(self, snapshot, decoder, threads, pause_ms):
        self.model = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(snapshot / "tokens.txt"),
            encoder=str(next(snapshot.glob("encoder-*.int8.onnx"))),
            decoder=str(decoder),
            joiner=str(next(snapshot.glob("joiner-*.int8.onnx"))),
            num_threads=threads,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            rule2_min_trailing_silence=pause_ms / 1000,
        )
        audio = (P0 / "fixtures/first-photo.pcm").read_bytes()[:3 * RATE * 2]
        warm = self.new_vad()
        ids = count(1)
        for offset in range(0, len(audio), FRAME_BYTES):
            self.segments(warm, audio[offset:offset + FRAME_BYTES], ids, None)
        self.segments(warm, None, ids, None)

    def new_vad(self):
        return StreamState(self.model.create_stream())

    def segments(self, state, frame, segment_ids, record):
        stream = state.stream
        flush = frame is None
        if flush:
            # Keep native feature lookahead at EOF without advancing the PCM clock.
            stream.accept_waveform(RATE, np.zeros(RATE // 2, dtype=np.float32))
            stream.input_finished()
        else:
            samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
            state.accepted += len(samples)
            stream.accept_waveform(RATE, samples)

        out = []

        def detail(row):
            timestamps = row["timestamps"]
            origin = row["start_time"]
            start = round((origin + timestamps[0]) * RATE)
            # Each decoded frame spans 40 ms in the transducer result clock.
            end = min(state.accepted, round((origin + timestamps[-1] + .04) * RATE))
            return {"native_segment": row["segment"], "text_ja": row["text"].strip(),
                    "start_sample": start, "end_sample": end, "accepted_samples": state.accepted}

        def finalize(row):
            if not row["text"].strip() or not row["timestamps"]:
                return
            data = detail(row)
            start, end = data["start_sample"], data["end_sample"]
            if not (0 <= start < end and end >= state.last_end):
                return
            segment = RecognizedSegment(next(segment_ids), start, end, data["text_ja"])
            state.last_end = end
            if record:
                record("pengcheng_final", segment_id=segment.segment_id, flush=flush, **data)
            out.append(segment)

        while self.model.is_ready(stream):
            t0 = time.perf_counter()
            self.model.decode_stream(stream)
            endpoint = not flush and self.model.is_endpoint(stream)
            if record:
                record("pengcheng_chunk", decode_s=time.perf_counter() - t0, accepted_samples=state.accepted,
                       flush=flush)
            if record or endpoint:
                row = json.loads(self.model.get_result_as_json_string(stream))
                text = row["text"].strip()
                if record and text and row["timestamps"]:
                    if text != state.previous:
                        record("pengcheng_partial", flush=flush, **detail(row))
                    elif text != state.stable and not endpoint and not flush and (
                        text.endswith(("。", "！", "？", "!", "?")) or row["num_trailing_blanks"] >= 6
                    ):
                        record("pengcheng_stable_candidate", **detail(row))
                        state.stable = text
                state.previous = text
                if endpoint:
                    finalize(row)
                    self.model.reset(stream)
                    state.previous = state.stable = ""
        if flush:
            finalize(json.loads(self.model.get_result_as_json_string(stream)))
        return out


class PengChengBackend(LocalBackend):
    def __init__(self, cache, profile, snapshot, decoder):
        super().__init__(cache, profile)
        self.snapshot = snapshot
        self.decoder = decoder
        self.missing = [file for file in self.missing if file.parent == self.mt_path]
        if not snapshot.is_dir() or not decoder.is_file():
            self.missing.append(decoder)

    async def warmup(self):
        if self.missing:
            raise BackendError("profile_unavailable", "not_ready")
        async with self.lock:
            if self.translator is not None:
                return
            loop = asyncio.get_running_loop()
            if self.recognizer is None:
                self.recognizer = await loop.run_in_executor(
                    self.asr_pool, PengChengRecognizer, self.snapshot, self.decoder,
                    self.profile.asr.threads, self.profile.asr.vad_pause_ms)
            self.translator = await loop.run_in_executor(self.mt_pool, HyTranslator, self.mt_path)


def serve(path):
    config = load_settings(path)
    token = load_token(token_path(), create=True)
    profile = config.profiles[config.default_profile_id]
    backend = PengChengBackend(Path(config.cache_root), profile, Path(os.environ["P0_PENGCHENG_SNAPSHOT"]),
                              Path(os.environ["P0_PENGCHENG_DECODER"]))
    app = create_app(config, token, {config.default_profile_id: backend})
    uvicorn.run(app, host="127.0.0.1", port=config.server.port, workers=1, proxy_headers=False,
                access_log=False, ws="websockets-sansio", ws_max_size=FRAME_BYTES * 2)


def smoke(snapshot, decoder):
    recognizer = PengChengRecognizer(snapshot, decoder, 2, 600)
    for clip in ("csj-task", "csj-task-tail", "first-photo", "silence"):
        state = recognizer.new_vad()
        ids = count(1)
        audio = (P0 / "fixtures" / f"{clip.removesuffix('-tail')}.pcm").read_bytes()
        if clip.endswith("-tail"):
            audio = audio[:11 * RATE * 2]
        out = []
        events = []

        def record(stage, **fields):
            events.append({"stage": stage, **fields})

        for offset in range(0, len(audio), FRAME_BYTES):
            out.extend(recognizer.segments(state, audio[offset:offset + FRAME_BYTES], ids, record))
        out.extend(recognizer.segments(state, None, ids, record))
        print(json.dumps({"clip": clip, "samples": state.accepted, "segments": [vars(row) for row in out],
                          "events": events}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--serve"]:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--serve", action="store_true")
        parser.add_argument("--config", type=Path, required=True)
        serve(parser.parse_args().config)
    elif sys.argv[1:2] == ["--smoke"]:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--smoke", nargs=2, type=Path, required=True, metavar=("SNAPSHOT", "DECODER"))
        smoke(*parser.parse_args().smoke)
    else:
        replay.main(server_command=[sys.executable, str(Path(__file__).resolve()), "--serve"])
