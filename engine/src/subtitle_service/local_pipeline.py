import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, replace
import hashlib
from itertools import count
from pathlib import Path
import re

from .backend import BackendError, Caption

RATE = 16000
_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_REPEAT = re.compile(r"(.{2,16}?)\1{3,}")


def _collapse_repetition(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _REPEAT.sub(r"\1", text)
    return text.strip()
VAD_SHA = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
MODEL_FILES = (
    "encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt",
)


@dataclass(frozen=True)
class RecognizedSegment:
    segment_id: int
    start_sample: int
    end_sample: int
    text_ja: str


def silero_config(vad_path, pause_ms):
    import sherpa_onnx

    config = sherpa_onnx.VadModelConfig()
    config.sample_rate = RATE
    config.silero_vad.model = str(vad_path)
    config.silero_vad.threshold = .5
    config.silero_vad.min_speech_duration = .15
    config.silero_vad.min_silence_duration = pause_ms / 1000
    config.silero_vad.max_speech_duration = 4
    return config


def vad_segments(vad, frame):
    import numpy as np

    if frame is None:
        vad.flush()
    else:
        vad.accept_waveform(np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768)
    while not vad.empty():
        segment = vad.front
        start, samples = segment.start, segment.samples
        vad.pop()
        yield start, samples


class ReazonRecognizer:
    def __init__(self, snapshot, vad_path, config):
        import numpy as np
        import sherpa_onnx

        self.sherpa = sherpa_onnx
        self.model = sherpa_onnx.OfflineRecognizer.from_transducer(
            **{key: str(snapshot / filename) for key, filename in zip(
                ("encoder", "decoder", "joiner", "tokens"), MODEL_FILES, strict=True)},
            num_threads=config.threads, sample_rate=RATE, provider="cpu",
        )
        self.vad_config = silero_config(vad_path, config.vad_pause_ms)
        self.decode(np.zeros(RATE, dtype=np.float32))
        self.new_vad()

    def new_vad(self):
        return self.sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=120)

    def decode(self, samples):
        stream = self.model.create_stream()
        stream.accept_waveform(RATE, samples)
        self.model.decode_stream(stream)
        return stream.result.text.strip()

    def segments(self, vad, frame, segment_ids, record):
        out = []
        for start, samples in vad_segments(vad, frame):
            segment_id = next(segment_ids)
            end = start + len(samples)
            if record:
                span = {"segment_id": segment_id, "start_sample": start, "end_sample": end}
                record("vad_released", **span)
                record("asr_start", **span)
            text = self.decode(samples)
            if record:
                record("asr_end", text_ja=text, **span)
            if text:
                out.append(RecognizedSegment(segment_id, start, end, text))
        return out


class Qwen3AsrRecognizer:
    def __init__(self, snapshot, vad_path, config):
        import numpy as np
        import sherpa_onnx
        from mlx_audio.stt import load

        self.sherpa = sherpa_onnx
        self.vad_config = silero_config(vad_path, config.vad_pause_ms)
        self.model = load(str(snapshot))
        self.decode(np.zeros(RATE // 2, dtype=np.float32))

    def new_vad(self):
        return self.sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=120)

    def decode(self, samples):
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32)
        max_tokens = min(256, max(48, int(len(audio) / RATE * 18)))
        raw = self.model.generate(
            audio,
            language="Japanese",
            temperature=0.0,
            max_tokens=max_tokens,
            repetition_penalty=1.15,
            repetition_context_size=64,
        ).text.strip()
        return _collapse_repetition(raw)


class _NoopSession:
    def close(self):
        pass


class Qwen3Translator:
    SYSTEM_PROMPT = (
        "你是专业日译中视频字幕翻译。只输出自然流畅的简体中文译文，不要解释，不要输出思考过程。"
        "严禁保留任何日文平假名或片假名：人名与专有名词一律音译为中文汉字，外来片假名借词必须译成对应中文或还原英文单词。"
    )
    REPAIR_PROMPT = (
        "将下面译文中的残留日文假名改为自然中文或对应英文单词，只输出修正后的纯中文句子，不要解释，严禁包含任何假名：\n"
    )

    def __init__(self, snapshot):
        from mlx_lm import generate, load
        from mlx_lm.sample_utils import make_logits_processors, make_sampler

        self._generate = generate
        self.model, self.tokenizer = load(str(snapshot))
        self.sampler = make_sampler(temp=0.0)
        self.logits_processors = make_logits_processors(
            repetition_penalty=1.08, repetition_context_size=32,
        )
        self.translate(_NoopSession(), "こんにちは")

    def new_session(self):
        return _NoopSession()

    def _run_prompt(self, messages):
        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False,
        )
        raw = self._generate(
            self.model, self.tokenizer, prompt=prompt,
            max_tokens=256, sampler=self.sampler,
            logits_processors=self.logits_processors, verbose=False,
        ).strip()
        if "</think>" in raw:
            raw = raw.split("</think>", 1)[-1].strip()
        if len(raw) >= 2 and ((raw[0] == '"' and raw[-1] == '"') or (raw[0] == "“" and raw[-1] == "”")):
            raw = raw[1:-1].strip()
        return raw

    def translate(self, session, text_ja):
        raw = self._run_prompt([
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{text_ja}"},
        ])
        if _KANA.search(raw):
            repaired = self._run_prompt([
                {"role": "user", "content": self.REPAIR_PROMPT + raw},
            ])
            if repaired:
                return repaired
        return raw


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
        self.uses_mlx_asr = profile.asr.type == "qwen3_asr_mlx"
        self.vad_path = cache / "reazonspeech-assets/silero_vad.onnx"

        if self.uses_mlx_asr:
            self.asr_path = hub / "models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots" / profile.asr.model_revision
            asr_files = (
                self.asr_path / "config.json",
                self.asr_path / "model.safetensors",
                self.asr_path / "tokenizer_config.json",
            )
        else:
            self.asr_path = hub / "models--reazon-research--reazonspeech-k2-v2/snapshots" / profile.asr.model_revision
            asr_files = tuple(self.asr_path / f for f in MODEL_FILES)

        if profile.mt.type == "qwen3_mt_mlx":
            self.mt_path = hub / "models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots" / profile.mt.model_revision
            mt_files = (
                self.mt_path / "config.json",
                self.mt_path / "model.safetensors",
                self.mt_path / "tokenizer.json",
            )
        else:
            self.mt_path = hub / "models--mlx-community--Hy-MT2-1.8B-4bit/snapshots" / profile.mt.model_revision
            mt_files = (
                self.mt_path / "config.json",
                self.mt_path / "model.safetensors",
                self.mt_path / "tokenizer.json",
            )

        self.missing = [f for f in (*asr_files, self.vad_path, *mt_files) if not f.is_file()]
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
                # Bind all MLX models (including MLX ASR) to mt_pool so Metal runs on one persistent thread.
                pool = self.mt_pool if self.uses_mlx_asr else self.asr_pool
                cls = Qwen3AsrRecognizer if self.uses_mlx_asr else ReazonRecognizer
                self.recognizer = await loop.run_in_executor(
                    pool, cls, self.asr_path, self.vad_path, self.profile.asr)
            mt_cls = Qwen3Translator if self.profile.mt.type == "qwen3_mt_mlx" else HyTranslator
            self.translator = await loop.run_in_executor(self.mt_pool, mt_cls, self.mt_path)

    async def open(self, emit, *, record=None):
        await self.warmup()
        vad = await asyncio.get_running_loop().run_in_executor(self.asr_pool, self.recognizer.new_vad)
        return LocalSession(self, vad, self.translator.new_session(), emit, record)

    async def aclose(self):
        await asyncio.to_thread(self.asr_pool.shutdown)
        await asyncio.to_thread(self.mt_pool.shutdown)


class LocalSession:
    def __init__(self, backend, vad, translation, emit, record):
        self.backend = backend
        self.vad = vad
        self.translation = translation
        self.emit = emit
        self.asr_work = self.mt_work = None
        self.closed = False
        self.pending = asyncio.Queue(maxsize=4)
        self.record = record
        self.segment_ids = count(1)
        self.mt_task = asyncio.create_task(self.run_translation())

    def translate(self, segment):
        if self.record:
            self.record("mt_start", **vars(segment))
        text = self.backend.translator.translate(self.translation, segment.text_ja)
        if self.record:
            self.record("mt_end", text_zh=text, **vars(segment))
        return text

    async def run_translation(self):
        try:
            loop = asyncio.get_running_loop()
            while (segment := await self.pending.get()) is not None:
                self.mt_work = loop.run_in_executor(self.backend.mt_pool, self.translate, segment)
                translated = await asyncio.shield(self.mt_work)
                await self.emit(Caption(segment.start_sample, segment.end_sample, translated, segment.segment_id))
        except Exception:
            await self.emit(BackendError())

    def _enqueue_segment(self, segment):
        try:
            self.pending.put_nowait(segment)
            if self.record:
                self.record("mt_enqueued", **vars(segment))
        except asyncio.QueueFull:
            raise BackendError("overloaded") from None

    async def accept_pcm(self, frame):
        loop = asyncio.get_running_loop()
        if not self.backend.uses_mlx_asr:
            self.asr_work = loop.run_in_executor(
                self.backend.asr_pool, self.backend.recognizer.segments, self.vad, frame,
                self.segment_ids, self.record)
            for segment in await asyncio.shield(self.asr_work):
                self._enqueue_segment(segment)
            return

        self.asr_work = loop.run_in_executor(
            self.backend.asr_pool, lambda: list(vad_segments(self.vad, frame)),
        )
        released = await asyncio.shield(self.asr_work)
        for start, samples in released:
            segment_id = next(self.segment_ids)
            end = start + len(samples)
            span = {"segment_id": segment_id, "start_sample": start, "end_sample": end}
            if self.record:
                self.record("vad_released", **span)
                self.record("asr_start", **span)
            self.asr_work = loop.run_in_executor(
                self.backend.mt_pool, self.backend.recognizer.decode, samples,
            )
            text = await asyncio.shield(self.asr_work)
            if self.record:
                self.record("asr_end", text_ja=text, **span)
            if text:
                self._enqueue_segment(RecognizedSegment(segment_id, start, end, text))

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
