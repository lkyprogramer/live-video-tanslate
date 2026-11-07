"""Benchmark Moonshine's native Japanese stream through the existing v1 gateway."""

import argparse
import asyncio
from itertools import count
import json
import os
from pathlib import Path
import sys

from moonshine_voice import ModelArch, Transcriber
from moonshine_voice.transcriber import Error, LineCompleted, LineStarted, LineTextChanged
import numpy as np
import uvicorn

from review import P0
from subtitle_service import replay
from subtitle_service.app import FRAME_BYTES, create_app
from subtitle_service.backend import BackendError
from subtitle_service.cli import token_path
from subtitle_service.config import load_settings, load_token
from subtitle_service.local_pipeline import HyTranslator, LocalBackend, LocalSession, RATE, RecognizedSegment


class MoonshineStream:
    def __init__(self, model):
        self.stream = model.create_stream(update_interval=.5)
        self.events = []
        self.accepted_samples = 0
        self.completed = set()
        self.stream.add_listener(self.events.append)
        self.stream.start()

    def close(self):
        self.stream.close()


class MoonshineRecognizer:
    def __init__(self, model_path):
        options = {}
        if limit := os.environ.get("P0_MOONSHINE_MAX_TOKENS_PER_SECOND"):
            options["max_tokens_per_second"] = float(limit)
        self.model = Transcriber(model_path, ModelArch.SMALL_STREAMING, options=options)
        audio = (P0 / "fixtures/first-photo.pcm").read_bytes()[:RATE * 2 * 3]
        stream = self.new_vad()
        ids = count(1)
        try:
            for offset in range(0, len(audio), FRAME_BYTES):
                self.segments(stream, audio[offset:offset + FRAME_BYTES], ids, None)
            self.segments(stream, None, ids, None)
        finally:
            stream.close()

    def new_vad(self):
        return MoonshineStream(self.model)

    def segments(self, state, frame, segment_ids, record):
        state.events.clear()
        if frame is None:
            state.stream.stop()
        else:
            audio = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
            state.accepted_samples += len(audio)
            state.stream.add_audio(audio.tolist(), RATE)
        segments = []
        for event in state.events:
            if isinstance(event, Error):
                raise event.error
            if not isinstance(event, (LineStarted, LineTextChanged, LineCompleted)):
                continue
            line = event.line
            raw_start = round(line.start_time * RATE)
            raw_end = round((line.start_time + line.duration) * RATE)
            text = line.text.strip()
            detail = {"line_id": line.line_id, "start_sample": raw_start, "end_sample": raw_end,
                      "accepted_samples": state.accepted_samples, "text_ja": text, "flush": frame is None}
            if not isinstance(event, LineCompleted):
                if record:
                    record("moonshine_started" if isinstance(event, LineStarted) else "moonshine_partial", **detail)
                continue
            if line.line_id in state.completed:
                raise RuntimeError("Moonshine emitted a duplicate final line")
            state.completed.add(line.line_id)
            start, end = max(0, raw_start), min(state.accepted_samples, raw_end)
            segment_id = next(segment_ids)
            if record:
                record("moonshine_final", segment_id=segment_id, **detail)
            if text and start < end:
                segments.append(RecognizedSegment(segment_id, start, end, text))
        return segments


class MoonshineSession(LocalSession):
    async def aclose(self):
        if self.closed:
            return
        try:
            await super().aclose()
        finally:
            await asyncio.get_running_loop().run_in_executor(self.backend.asr_pool, self.vad.close)


class MoonshineBackend(LocalBackend):
    def __init__(self, cache, profile, model_path):
        super().__init__(cache, profile)
        self.model_path = model_path
        self.missing = [file for file in self.missing if file.parent == self.mt_path]
        if not model_path.is_dir():
            self.missing.append(model_path)

    async def warmup(self):
        if self.missing:
            raise BackendError("profile_unavailable", "not_ready")
        async with self.lock:
            if self.translator is not None:
                return
            loop = asyncio.get_running_loop()
            if self.recognizer is None:
                self.recognizer = await loop.run_in_executor(self.asr_pool, MoonshineRecognizer, self.model_path)
            self.translator = await loop.run_in_executor(self.mt_pool, HyTranslator, self.mt_path)

    async def open(self, emit, *, record=None):
        await self.warmup()
        stream = await asyncio.get_running_loop().run_in_executor(self.asr_pool, self.recognizer.new_vad)
        return MoonshineSession(self, stream, self.translator.new_session(), emit, record)

    async def aclose(self):
        await super().aclose()
        if self.recognizer:
            self.recognizer.model.close()


def serve(path):
    config = load_settings(path)
    token = load_token(token_path(), create=True)
    profile = config.profiles[config.default_profile_id]
    backend = MoonshineBackend(Path(config.cache_root), profile, Path(os.environ["P0_MOONSHINE_MODEL"]))
    app = create_app(config, token, {config.default_profile_id: backend})
    uvicorn.run(app, host="127.0.0.1", port=config.server.port, workers=1, proxy_headers=False,
                access_log=False, ws="websockets-sansio", ws_max_size=FRAME_BYTES * 2)


def smoke(model_path, clips):
    recognizer = MoonshineRecognizer(model_path)
    try:
        for clip in clips:
            stream = recognizer.new_vad()
            ids = count(1)
            out = []
            events = []

            def record(stage, **detail):
                events.append({"stage": stage, **detail})

            try:
                pcm = (P0 / "fixtures" / f"{clip}.pcm").read_bytes()
                for offset in range(0, len(pcm), FRAME_BYTES):
                    out.extend(recognizer.segments(stream, pcm[offset:offset + FRAME_BYTES], ids, record))
                out.extend(recognizer.segments(stream, None, ids, record))
                print(json.dumps({"clip": clip, "samples": len(pcm) // 2, "segments": [vars(row) for row in out],
                                  "events": events}, ensure_ascii=False))
            finally:
                stream.close()
    finally:
        recognizer.model.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--serve"]:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--serve", action="store_true")
        parser.add_argument("--config", type=Path, required=True)
        serve(parser.parse_args().config)
    elif sys.argv[1:2] == ["--smoke"]:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--smoke", type=Path, required=True)
        suite = json.loads((P0 / "fixtures/suite.json").read_text())
        parser.add_argument("--clips", nargs="+", choices=[clip["id"] for clip in suite],
                            default=["csj-task", "first-photo", "silence"])
        args = parser.parse_args()
        smoke(args.smoke, args.clips)
    else:
        replay.main(server_command=[sys.executable, str(Path(__file__).resolve()), "--serve"])
