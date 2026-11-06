import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import time

from .backend import BackendError, Caption

RATE = 16000
VAD_SHA = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
MODEL_FILES = (
    "encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt",
)


@dataclass(frozen=True)
class RecognizedSegment:
    start_sample: int
    end_sample: int
    text_ja: str


class ReazonRecognizer:
    def __init__(self, snapshot, vad_path, config):
        import numpy as np
        import sherpa_onnx

        self.np = np
        self.sherpa = sherpa_onnx
        self.model = sherpa_onnx.OfflineRecognizer.from_transducer(
            **{key: str(snapshot / filename) for key, filename in zip(
                ("encoder", "decoder", "joiner", "tokens"), MODEL_FILES, strict=True)},
            num_threads=config.threads, sample_rate=RATE, provider="cpu",
        )
        self.vad_config = sherpa_onnx.VadModelConfig()
        self.vad_config.sample_rate = RATE
        silero = self.vad_config.silero_vad
        silero.model = str(vad_path)
        silero.threshold = .5
        silero.min_speech_duration = .15
        silero.min_silence_duration = config.vad_pause_ms / 1000
        silero.max_speech_duration = 4
        self.decode(np.zeros(RATE, dtype=np.float32))
        self.new_vad()

    def new_vad(self):
        return self.sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=120)

    def decode(self, samples):
        stream = self.model.create_stream()
        stream.accept_waveform(RATE, samples)
        self.model.decode_stream(stream)
        return stream.result.text.strip()

    def segments(self, vad, frame):
        if frame is None:
            vad.flush()
        else:
            vad.accept_waveform(self.np.frombuffer(frame, dtype="<i2").astype(self.np.float32) / 32768)
        out = []
        while not vad.empty():
            segment = vad.front
            start, samples = segment.start, segment.samples
            vad.pop()
            if text := self.decode(samples):
                out.append(RecognizedSegment(start, start + len(samples), text))
        return out


class HyTranslator:
    def __init__(self, snapshot):
        from whisperlivekit.translation_mlx_llm_mt import MlxLlmTranslation, MTX_MODEL_CONFIGS

        key = f"subtitle-hy-{snapshot.name}"
        MTX_MODEL_CONFIGS[key] = replace(MTX_MODEL_CONFIGS["hy-mt2-1.8b-4bit"],
                                        repo=str(snapshot), revision=None)
        self.engine = MlxLlmTranslation(model_id=key, source_language="ja", target_language="zh-cn")

    def new_session(self):
        return self.engine.new_session()

    @staticmethod
    def translate(session, text_ja):
        return session._translate_text(text_ja)


class LocalBackend:
    def __init__(self, cache: Path, profile):
        hub = cache / "hub"
        self.profile = profile
        self.asr_path = hub / "models--reazon-research--reazonspeech-k2-v2/snapshots" / profile.asr.model_revision
        self.mt_path = hub / "models--mlx-community--Hy-MT2-1.8B-4bit/snapshots" / profile.mt.model_revision
        self.vad_path = cache / "reazonspeech-assets/silero_vad.onnx"
        self.missing = [f for f in (
            *(self.asr_path / f for f in MODEL_FILES), self.vad_path,
            self.mt_path / "config.json", self.mt_path / "model.safetensors",
            self.mt_path / "tokenizer.json",
        ) if not f.is_file()]
        self.asr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="subtitle-asr")
        self.mt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="subtitle-mt")
        self.lock = asyncio.Lock()
        self.recognizer = None
        self.translator = None

    async def available(self):
        return not self.missing

    async def warmup(self):
        if self.missing:
            raise BackendError("profile_unavailable", "not_ready")
        async with self.lock:
            if self.translator is not None:
                return
            if hashlib.sha256(self.vad_path.read_bytes()).hexdigest() != VAD_SHA:
                raise ValueError("Silero VAD checksum mismatch")
            loop = asyncio.get_running_loop()
            if self.recognizer is None:
                self.recognizer = await loop.run_in_executor(
                    self.asr_pool, ReazonRecognizer, self.asr_path, self.vad_path, self.profile.asr)
            self.translator = await loop.run_in_executor(self.mt_pool, HyTranslator, self.mt_path)

    async def open(self, emit):
        await self.warmup()
        vad = await asyncio.get_running_loop().run_in_executor(self.asr_pool, self.recognizer.new_vad)
        return LocalSession(self, vad, self.translator.new_session(), emit)

    async def aclose(self):
        await asyncio.to_thread(self.asr_pool.shutdown)
        await asyncio.to_thread(self.mt_pool.shutdown)


class LocalSession:
    def __init__(self, backend, vad, translation, emit):
        self.backend = backend
        self.vad = vad
        self.translation = translation
        self.emit = emit
        self.asr_work = self.mt_work = None
        self.closed = False
        self.pending = asyncio.Queue(maxsize=4)
        self.trace_path = os.environ.get("SUBTITLE_BENCH_TRACE")
        self.mt_task = asyncio.create_task(self.run_translation())

    def trace(self, stage, segment, **extra):
        if self.trace_path:
            with open(self.trace_path, "a", encoding="utf-8") as log:
                log.write(json.dumps({"t": time.monotonic(), "stage": stage,
                                      **vars(segment), **extra}, ensure_ascii=False) + "\n")

    async def run_translation(self):
        try:
            loop = asyncio.get_running_loop()
            while (segment := await self.pending.get()) is not None:
                self.mt_work = loop.run_in_executor(
                    self.backend.mt_pool, self.backend.translator.translate, self.translation, segment.text_ja)
                translated = await asyncio.shield(self.mt_work)
                self.trace("translated", segment, text_zh=translated)
                await self.emit(Caption(segment.start_sample, segment.end_sample, translated))
        except Exception:
            await self.emit(BackendError())

    async def accept_pcm(self, frame):
        self.asr_work = asyncio.get_running_loop().run_in_executor(
            self.backend.asr_pool, self.backend.recognizer.segments, self.vad, frame)
        for segment in await asyncio.shield(self.asr_work):
            self.trace("recognized", segment)
            try:
                self.pending.put_nowait(segment)
            except asyncio.QueueFull:
                raise BackendError("overloaded") from None

    async def finish(self):
        await self.accept_pcm(None)
        await self.pending.put(None)
        await asyncio.shield(self.mt_task)

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        self.translation.close()
        self.mt_task.cancel()
        await asyncio.gather(self.mt_task, return_exceptions=True)
        for work in (self.asr_work, self.mt_work):
            if work is not None:
                with suppress(Exception):
                    await asyncio.shield(work)
