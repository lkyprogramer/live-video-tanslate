import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import ctypes
from dataclasses import dataclass, replace
import hashlib
import inspect
from itertools import count
import logging
from pathlib import Path
import re
import threading

import numpy as np

from .backend import BackendError, Caption, CaptionDelta

log = logging.getLogger(__name__)
RATE = 16000
_KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
_REPEAT = re.compile(r"(.{2,16}?)\1{3,}")
_PUNCT_STRIP = re.compile(r"[\s\u3000、。．，？！?!「」『』（）()]+")
VAD_SHA = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
MODEL_FILES = (
    "encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt",
)


class _Cancelled(Exception):
    pass


def _collapse_repetition(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _REPEAT.sub(r"\1", text)
    return text.strip()


def _dedup_boundary_ja(prev_ja: str, curr_ja: str) -> str:
    """Trim duplicated leading phrase in curr_ja that matches suffix of prev_ja."""
    if not prev_ja or not curr_ja:
        return curr_ja
    prev_norm = _PUNCT_STRIP.sub("", prev_ja)
    if len(prev_norm) < 2:
        return curr_ja
    # Map normalized indices of curr_ja back to raw string indices
    norm_chars: list[str] = []
    raw_end_for_norm_len: list[int] = [0]
    for idx, ch in enumerate(curr_ja):
        if not _PUNCT_STRIP.match(ch):
            norm_chars.append(ch)
            raw_end_for_norm_len.append(idx + 1)
    curr_norm = "".join(norm_chars)
    max_k = min(12, len(prev_norm), len(curr_norm))
    for k in range(max_k, 1, -1):
        if prev_norm.endswith(curr_norm[:k]):
            trimmed = curr_ja[raw_end_for_norm_len[k]:].lstrip("、。．，？！?! \u3000")
            return trimmed if trimmed else curr_ja
    return curr_ja


def _call_with_cancel(fn, *args, cancel_event: threading.Event | None, **kwargs):
    sig = inspect.signature(fn).parameters
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.values())
    extra = {k: v for k, v in kwargs.items() if has_var_kw or k in sig}
    if cancel_event is not None and (has_var_kw or "cancel_event" in sig):
        extra["cancel_event"] = cancel_event
    return fn(*args, **extra)


@dataclass(frozen=True)
class RecognizedSegment:
    segment_id: int
    start_sample: int
    end_sample: int
    text_ja: str


@dataclass(frozen=True)
class _EmittedChunk:
    start_sample: int
    samples: np.ndarray
    dedup_before_sample: int = 0
    cut_reason: str = "vad_natural"


@dataclass(frozen=True)
class _DraftSnapshot:
    start_sample: int
    end_sample: int
    samples: np.ndarray
    dedup_before_sample: int = 0


_DRAFT_CLAUSE_END = (
    "、", "。", "？", "！", "?", "!",
    "けど", "から", "ので", "って", "よね", "ですね",
    "ます", "です", "した", "ない", "て", "が", "は", "も", "ね", "よ",
)


def _should_emit_draft_ja(prev_ja: str, curr_ja: str) -> bool:
    if not curr_ja or curr_ja == prev_ja:
        return False
    curr_norm = _PUNCT_STRIP.sub("", curr_ja)
    if len(curr_norm) < 6:
        return False
    prev_norm = _PUNCT_STRIP.sub("", prev_ja) if prev_ja else ""
    if prev_norm and len(curr_norm) < len(prev_norm) + 3:
        return False
    return len(curr_norm) >= 12 or curr_ja.endswith(_DRAFT_CLAUSE_END)


@dataclass(frozen=True)
class _PendingAudio:
    segment_id: int
    start_sample: int
    end_sample: int
    samples: np.ndarray
    has_overlap: bool = False
    dedup_before_sample: int = 0


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
    if frame is None:
        vad.flush()
    else:
        vad.accept_waveform(np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768)
    while not vad.empty():
        segment = vad.front
        start, samples = segment.start, segment.samples
        vad.pop()
        yield start, samples


class _SmartSegmenter:
    """Silero VAD wrapper with absolute-sample ring buffer, zero-loss splitting, and left overlap."""

    def __init__(self, vad, *, max_segment_s: float = 0.0, overlap_ms: int = 350):
        self.vad = vad
        self.max_segment_samples = int(round(max_segment_s * RATE)) if max_segment_s > 0 else 0
        self.overlap_samples = int(round(overlap_ms * RATE / 1000.0))
        self.total_samples = 0
        self._pcm_chunks: deque[np.ndarray] = deque()
        self._buf_origin = 0
        self._speech_origin: int | None = None
        self._burst_cut_sample = 0
        self._last_emitted_end = 0
        self._last_draft_end = 0

    def _slice_pcm(self, start: int, end: int) -> np.ndarray:
        lo = max(start, self._buf_origin)
        hi = min(end, self.total_samples)
        if lo >= hi:
            return np.empty(0, dtype=np.float32)
        parts: list[np.ndarray] = []
        pos = self._buf_origin
        for chunk in self._pcm_chunks:
            chunk_end = pos + len(chunk)
            if chunk_end > lo and pos < hi:
                s = max(0, lo - pos)
                e = min(len(chunk), hi - pos)
                parts.append(chunk[s:e])
            pos = chunk_end
            if pos >= hi:
                break
        if not parts:
            return np.empty(0, dtype=np.float32)
        return parts[0].copy() if len(parts) == 1 else np.concatenate(parts)

    def _trim_ring(self, keep_from: int) -> None:
        while self._pcm_chunks and self._buf_origin + len(self._pcm_chunks[0]) <= keep_from:
            dropped = self._pcm_chunks.popleft()
            self._buf_origin += len(dropped)

    def _find_energy_valley_cut(self, arr: np.ndarray) -> int:
        n = len(arr)
        if n <= 1:
            return n
        search_end = max(1, n - min(int(0.08 * RATE), n // 4))
        min_keep = min(int(2.0 * RATE), max(1, search_end // 2))
        search_start = min(max(min_keep, n - int(0.65 * RATE)), max(0, search_end - 1))
        win = min(int(0.16 * RATE), max(1, search_end - search_start))
        step = max(1, min(int(0.02 * RATE), win))
        if search_end - search_start <= win:
            return search_end
        best_pos = search_end - win // 2
        best_energy = float("inf")
        for s in range(search_start, search_end - win + 1, step):
            chunk = arr[s : s + win]
            energy = float(np.mean(chunk * chunk))
            if energy < best_energy:
                best_energy = energy
                best_pos = s + win // 2
        return min(n, max(1, best_pos))

    def push(self, frame: bytes | None) -> list[_EmittedChunk]:
        if self.max_segment_samples <= 0:
            return [
                _EmittedChunk(int(start), np.asarray(samples, dtype=np.float32), 0, "vad_natural")
                for start, samples in vad_segments(self.vad, frame)
            ]

        out: list[_EmittedChunk] = []
        if frame is None:
            samples_f32 = np.empty(0, dtype=np.float32)
            self.vad.flush()
        else:
            samples_f32 = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
            self._pcm_chunks.append(samples_f32)
            self.total_samples += len(samples_f32)
            self.vad.accept_waveform(samples_f32)

        while not self.vad.empty():
            segment = self.vad.front
            seg_start = int(segment.start)
            seg_samples = np.asarray(segment.samples, dtype=np.float32)
            self.vad.pop()
            seg_end = seg_start + len(seg_samples)
            if self._burst_cut_sample > seg_start:
                if seg_end > self._burst_cut_sample:
                    tail_start = max(seg_start, self._burst_cut_sample - self.overlap_samples)
                    out.append(
                        _EmittedChunk(
                            tail_start,
                            seg_samples[tail_start - seg_start :],
                            dedup_before_sample=self._burst_cut_sample,
                            cut_reason="vad_tail",
                        )
                    )
            elif seg_end > seg_start:
                out.append(_EmittedChunk(seg_start, seg_samples, 0, "vad_natural"))
            self._last_emitted_end = max(self._last_emitted_end, seg_end)
            self._burst_cut_sample = 0
            self._speech_origin = None
            self._last_draft_end = 0

        if frame is not None:
            if self.vad.is_speech_detected():
                frame_start = self.total_samples - len(samples_f32)
                if self._speech_origin is None:
                    # Backtrack up to 500ms before onset confirmation frame so delayed VAD never clips speech start.
                    self._speech_origin = max(
                        self._buf_origin,
                        self._last_emitted_end,
                        frame_start - int(0.5 * RATE),
                    )
                active_start = self._burst_cut_sample if self._burst_cut_sample > 0 else self._speech_origin
                while self.total_samples - active_start >= self.max_segment_samples:
                    arr = self._slice_pcm(active_start, self.total_samples)
                    cut_idx = self._find_energy_valley_cut(arr)
                    global_cut = active_start + cut_idx
                    if cut_idx <= 0 or global_cut <= active_start:
                        break
                    has_overlap = self._burst_cut_sample > 0
                    emit_start = max(self._buf_origin, active_start - self.overlap_samples) if has_overlap else active_start
                    seg_pcm = self._slice_pcm(emit_start, global_cut)
                    dedup_before = self._burst_cut_sample if has_overlap else 0
                    out.append(_EmittedChunk(emit_start, seg_pcm, dedup_before, "energy_valley"))
                    self._burst_cut_sample = global_cut
                    self._last_emitted_end = max(self._last_emitted_end, global_cut)
                    self._last_draft_end = 0
                    active_start = global_cut
                    self._trim_ring(max(0, self._burst_cut_sample - self.overlap_samples))
            else:
                self._speech_origin = None
                self._burst_cut_sample = 0
                self._last_draft_end = 0
                self._trim_ring(max(self._last_emitted_end, self.total_samples - int(1.0 * RATE)))
        else:
            self._speech_origin = None
            self._burst_cut_sample = 0
            self._last_draft_end = 0
            self._pcm_chunks.clear()
            self._buf_origin = self.total_samples

        return out

    def active_preview_snapshot(self, *, min_speech_s: float = 1.5, min_step_s: float = 1.2) -> _DraftSnapshot | None:
        if self.max_segment_samples <= 0 or self._speech_origin is None or not self.vad.is_speech_detected():
            return None
        active_start = self._burst_cut_sample if self._burst_cut_sample > 0 else self._speech_origin
        if self.total_samples - active_start < int(min_speech_s * RATE):
            return None
        if self.total_samples - max(active_start, self._last_draft_end) < int(min_step_s * RATE):
            return None
        has_overlap = self._burst_cut_sample > 0
        emit_start = max(self._buf_origin, active_start - self.overlap_samples) if has_overlap else active_start
        seg_pcm = self._slice_pcm(emit_start, self.total_samples)
        if len(seg_pcm) == 0:
            return None
        dedup_before = self._burst_cut_sample if has_overlap else 0
        self._last_draft_end = self.total_samples
        return _DraftSnapshot(emit_start, self.total_samples, seg_pcm, dedup_before)

    def push_and_preview(self, frame: bytes | None, *, draft_interval_s: float = 0.0) -> tuple[list[_EmittedChunk], _DraftSnapshot | None]:
        released = self.push(frame)
        if released or frame is None or draft_interval_s <= 0:
            return released, None
        snap = self.active_preview_snapshot(
            min_speech_s=max(1.4, draft_interval_s),
            min_step_s=draft_interval_s,
        )
        return released, snap


class ReazonRecognizer:
    def __init__(self, snapshot, vad_path, config):
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
        import mlx.core as mx
        import sherpa_onnx
        from mlx_audio.lm.generate import generate_step
        from mlx_audio.lm.sample_utils import make_logits_processors
        from mlx_audio.stt import load

        self.sherpa = sherpa_onnx
        self.vad_config = silero_config(vad_path, config.vad_pause_ms)
        self._mx = mx
        self._generate_step = generate_step
        loaded = load(str(snapshot))
        self.model = getattr(loaded, "_model", loaded)
        mx.set_cache_limit(1024 * 1024 * 1024)

        prefix_str = "<|im_start|>system\n<|im_end|>\n<|im_start|>user\n<|audio_start|>"
        suffix_str = "<|audio_end|><|im_end|>\n<|im_start|>assistant\nlanguage Japanese<asr_text>"
        self._prefix_ids = mx.array(self.model._tokenizer.encode(prefix_str, return_tensors="np")[0], dtype=mx.int32)
        self._suffix_ids = mx.array(self.model._tokenizer.encode(suffix_str, return_tensors="np")[0], dtype=mx.int32)
        self._prefix_emb = self.model.model.embed_tokens(self._prefix_ids[None, :])[0]
        self._suffix_emb = self.model.model.embed_tokens(self._suffix_ids[None, :])[0]
        self._audio_token_id = int(self.model.config.audio_token_id)
        self._eos_ids = self.model._eos_token_ids()
        self._sampler = lambda logits: mx.argmax(logits, axis=-1)
        self._logits_processors = make_logits_processors(repetition_penalty=1.15, repetition_context_size=64)
        mx.eval(self._prefix_ids, self._suffix_ids, self._prefix_emb, self._suffix_emb)
        self.decode(np.zeros(RATE // 2, dtype=np.float32))

    def new_vad(self):
        return self.sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=120)

    def _encode_audio_clean(self, input_features):
        import mlx.nn as nn

        mx = self._mx
        enc = self.model.audio_tower
        t_len = int(input_features.shape[-1])
        chunk_size = enc.n_window * 2
        num_chunks = (t_len + chunk_size - 1) // chunk_size
        chunk_lengths = []
        chunks = []
        feat = input_features[0]
        pos = 0
        for j in range(num_chunks):
            clen = chunk_size if (j < num_chunks - 1 or t_len % chunk_size == 0) else (t_len % chunk_size)
            chunk_lengths.append(clen)
            c = feat[:, pos : pos + clen]
            if clen < chunk_size and num_chunks > 1:
                c = mx.pad(c, [(0, 0), (0, chunk_size - clen)])
            chunks.append(c)
            pos += clen
        padded_feature = mx.stack(chunks, axis=0)
        x = padded_feature[:, :, :, None]
        x = nn.gelu(enc.conv2d1(x))
        x = nn.gelu(enc.conv2d2(x))
        x = nn.gelu(enc.conv2d3(x))
        b, f, t, c = x.shape
        x = x.transpose(0, 2, 3, 1).reshape(b, t, c * f)
        x = enc.conv_out(x)
        x = x + enc.positional_embedding(x.shape[1])[None, :, :]
        if num_chunks == 1:
            hidden_states = x[0]
        else:
            last_len = chunk_lengths[-1]
            rem = last_len % 100
            fl = ((rem - 1) // 2) + 1
            last_cnn_len = ((((fl - 1) // 2) + 1 - 1) // 2) + 1 + (last_len // 100) * 13
            parts = [x[i] for i in range(num_chunks - 1)] + [x[-1, :last_cnn_len]]
            hidden_states = mx.concatenate(parts, axis=0)

        max_len_after_cnn = int(x.shape[1])
        window_aftercnn = max_len_after_cnn * (enc.n_window_infer // (enc.n_window * 2))
        total_cnn_len = int(hidden_states.shape[0])
        cu_seqlens = [0]
        cur = 0
        while cur < total_cnn_len:
            step = min(window_aftercnn, total_cnn_len - cur)
            cur += step
            cu_seqlens.append(cur)
        attention_mask = enc._create_block_attention_mask(
            total_cnn_len, cu_seqlens, hidden_states.dtype
        )[None, None, :, :]
        hidden_states = hidden_states[None, :, :]
        for layer in enc.layers:
            hidden_states = layer(hidden_states, mask=attention_mask)
        hidden_states = enc.ln_post(hidden_states[0])
        hidden_states = nn.gelu(enc.proj1(hidden_states))
        return enc.proj2(hidden_states)

    def decode(self, samples, *, cancel_event: threading.Event | None = None):
        if cancel_event is not None and cancel_event.is_set():
            return ""
        mx = self._mx
        audio = np.asarray(samples, dtype=np.float32)
        max_tokens = min(256, max(48, int(len(audio) / RATE * 18)))
        input_features, _, _ = self.model._preprocess_audio(audio)
        audio_features = self._encode_audio_clean(input_features).astype(self._prefix_emb.dtype)
        input_embeddings = mx.concatenate([self._prefix_emb, audio_features, self._suffix_emb], axis=0)
        audio_pads = mx.full((audio_features.shape[0],), self._audio_token_id, dtype=mx.int32)
        prompt = mx.concatenate([self._prefix_ids, audio_pads, self._suffix_ids], axis=0)

        def _check_cancel(_processed, _total):
            if cancel_event is not None and cancel_event.is_set():
                raise _Cancelled()

        tokens: list[int] = []
        try:
            for token, _ in self._generate_step(
                prompt=prompt,
                input_embeddings=input_embeddings,
                model=self.model,
                max_tokens=max_tokens,
                sampler=self._sampler,
                logits_processors=self._logits_processors,
                prefill_step_size=512,
                prompt_progress_callback=_check_cancel if cancel_event is not None else None,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return ""
                tid = int(token)
                if tid in self._eos_ids:
                    break
                tokens.append(tid)
        except _Cancelled:
            return ""
        raw = self.model._tokenizer.decode(tokens, skip_special_tokens=True).strip()
        return _collapse_repetition(raw)


def _filter_tokens_by_sample(
    tokens: list[tuple[float, float, str]],
    *,
    start_sample: int,
    dedup_before_sample: int,
    pad_s: float,
) -> str:
    """Keep tokens whose midpoint falls at or after dedup_before_sample on the absolute PCM axis."""
    if dedup_before_sample <= 0 or not tokens:
        return "".join(t[2] for t in tokens).strip()
    kept: list[str] = []
    trimmed_any = False
    for st, en, txt in tokens:
        abs_mid = start_sample + int(round(((st + en) * 0.5 - pad_s) * RATE))
        if abs_mid >= dedup_before_sample:
            kept.append(txt)
        else:
            trimmed_any = True
    joined = "".join(kept).strip()
    if trimmed_any:
        joined = joined.lstrip("、。．，？！?! \u3000")
    return joined


class _AneFallback(Exception):
    def __init__(self, code: int):
        super().__init__(f"ane_fallback:{code}")
        self.code = code


class _NemoAneToken(ctypes.Structure):
    _fields_ = [("token_id", ctypes.c_int32), ("frame", ctypes.c_int32)]


class NeMoAneBridge:
    """ctypes binding for libnemo_ane.dylib (CoreML ANE FastConformer + Accelerate RNNT)."""

    _CANCEL_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
    _ANE_BUCKETS = ("fc_S256_w8a16.mlmodelc", "fc_M560_w8a16.mlmodelc", "fc_L1024_w8a16.mlmodelc")

    @classmethod
    def is_ready(cls, ane_dir: Path) -> bool:
        return (ane_dir / "libnemo_ane.dylib").is_file() and all((ane_dir / b).is_dir() for b in cls._ANE_BUCKETS)

    def __init__(self, ane_dir: Path, gguf_path: Path):
        lib = ctypes.CDLL(str(ane_dir / "libnemo_ane.dylib"))
        self._lib = lib
        lib.nemo_ane_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        lib.nemo_ane_create.restype = ctypes.c_void_p
        lib.nemo_ane_destroy.argtypes = [ctypes.c_void_p]
        lib.nemo_ane_wired_mb.argtypes = []
        lib.nemo_ane_wired_mb.restype = ctypes.c_double
        lib.nemo_ane_retired.argtypes = [ctypes.c_void_p]
        lib.nemo_ane_retired.restype = ctypes.c_int
        lib.nemo_ane_transcribe.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            self._CANCEL_CB,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.POINTER(_NemoAneToken),
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int32),
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.nemo_ane_transcribe.restype = ctypes.c_int
        lib.nemo_ane_detokenize.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int32),
            ctypes.c_int32,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.nemo_ane_detokenize.restype = ctypes.c_int

        err = ctypes.create_string_buffer(512)
        handle = lib.nemo_ane_create(str(ane_dir).encode("utf-8"), str(gguf_path).encode("utf-8"), err, 512)
        if not handle:
            raise RuntimeError(f"nemo_ane_create failed: {err.value.decode('utf-8', 'replace')}")
        self._handle = handle
        self._retired_logged = False

    @property
    def retired(self) -> bool:
        return self._handle is None or bool(self._lib.nemo_ane_retired(self._handle))

    def wired_mb(self) -> float:
        return float(self._lib.nemo_ane_wired_mb())

    def transcribe(
        self,
        samples: np.ndarray,
        *,
        pad_samples: int,
        use_beam: bool,
        cancel_event: threading.Event | None = None,
        start_sample: int = 0,
        dedup_before_sample: int = 0,
    ) -> str:
        if self.retired:
            raise _AneFallback(-2)
        audio = np.ascontiguousarray(samples, dtype=np.float32)
        out_buf = ctypes.create_string_buffer(8192)
        max_toks = 1024
        toks = (_NemoAneToken * max_toks)()
        n_toks = ctypes.c_int32(0)
        cb = self._CANCEL_CB(lambda _ud: 1 if (cancel_event is not None and cancel_event.is_set()) else 0) if cancel_event is not None else self._CANCEL_CB()
        rc = self._lib.nemo_ane_transcribe(
            self._handle,
            audio.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            int(audio.size),
            int(pad_samples),
            1 if use_beam else 0,
            cb,
            None,
            out_buf,
            8192,
            toks,
            max_toks,
            ctypes.byref(n_toks),
            None,
            0,
        )
        if rc == 1 or (cancel_event is not None and cancel_event.is_set()):
            return ""
        if rc != 0:
            if rc == -2 and not self._retired_logged:
                self._retired_logged = True
                log.warning("nemo_ane retired; falling back to speech.cpp MTL0 GPU")
            raise _AneFallback(rc)
        text = ""
        cnt = min(int(n_toks.value), max_toks)
        if dedup_before_sample > 0 and cnt > 0:
            kept_ids = [
                toks[i].token_id
                for i in range(cnt)
                if (toks[i].frame * 1280 - pad_samples + start_sample) >= dedup_before_sample
            ]
            if not kept_ids:
                return ""
            arr = (ctypes.c_int32 * len(kept_ids))(*kept_ids)
            detok_buf = ctypes.create_string_buffer(8192)
            if self._lib.nemo_ane_detokenize(self._handle, arr, len(kept_ids), detok_buf, 8192) == 0:
                text = detok_buf.value.decode("utf-8", "replace").strip()
        if not text:
            text = out_buf.value.decode("utf-8", "replace").strip()
        if text in ("ピッ!", "ピッ", "ピッ。"):
            return ""
        return _collapse_repetition(text)

    def close(self):
        if getattr(self, "_handle", None):
            self._lib.nemo_ane_destroy(self._handle)
            self._handle = None


class SpeechCppNeMoRecognizer:
    """ctypes binding for speech.cpp FastConformer-RNNT running on CPU or staggered MTL0."""

    _PROGRESS_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_double, ctypes.c_void_p)
    _OPT_TIMESTAMPS = 8
    _OPT_DECODING = 10
    _OPT_DECODE_ON_CPU = 44
    _SPEECH_OK = 0
    _SPEECH_CANCELLED = 1

    def __init__(self, dylib_path: Path, model_path: Path, vad_path: Path, config):
        import sherpa_onnx

        self.sherpa = sherpa_onnx
        self.vad_config = silero_config(vad_path, config.vad_pause_ms)
        self._decoding = config.decoding.encode("utf-8")
        self._device = getattr(config, "device", "cpu")
        self._fallback_device = "gpu" if self._device == "ane" else self._device
        self._pad_samples = int(round(getattr(config, "pad_ms", 350) * RATE / 1000.0))
        self._pad_s = self._pad_samples / float(RATE)
        self._ane = None
        if self._device == "ane":
            ane_dir = dylib_path.parent.parent / "nemo-ane"
            if NeMoAneBridge.is_ready(ane_dir):
                self._ane = NeMoAneBridge(ane_dir, model_path)
            else:
                log.warning("nemo-ane assets missing at %s; using speech.cpp %s fallback", ane_dir, self._fallback_device)

        lib = ctypes.CDLL(str(dylib_path))
        self._lib = lib
        lib.speech_log_set.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.speech_load_params_new.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.speech_load_params_new.restype = ctypes.c_int
        lib.speech_load_params_free.argtypes = [ctypes.c_void_p]
        lib.speech_load_params_set_device.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.speech_load_params_set_device.restype = ctypes.c_int
        lib.speech_load_params_set_threads.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.speech_load_params_set_threads.restype = ctypes.c_int
        lib.speech_load_params_set_warmup.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.speech_load_params_set_warmup.restype = ctypes.c_int
        lib.speech_model_load.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        lib.speech_model_load.restype = ctypes.c_int
        lib.speech_model_free.argtypes = [ctypes.c_void_p]
        lib.speech_request_new.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        lib.speech_request_new.restype = ctypes.c_int
        lib.speech_request_free.argtypes = [ctypes.c_void_p]
        lib.speech_request_set_audio.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_int]
        lib.speech_request_set_audio.restype = ctypes.c_int
        lib.speech_request_set_string.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        lib.speech_request_set_string.restype = ctypes.c_int
        lib.speech_request_set_bool.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.speech_request_set_bool.restype = ctypes.c_int
        lib.speech_request_set_progress.argtypes = [ctypes.c_void_p, self._PROGRESS_CB, ctypes.c_void_p]
        lib.speech_request_set_progress.restype = ctypes.c_int
        lib.speech_transcribe.argtypes = [ctypes.c_void_p]
        lib.speech_transcribe.restype = ctypes.c_int
        lib.speech_request_result.argtypes = [ctypes.c_void_p]
        lib.speech_request_result.restype = ctypes.c_void_p
        lib.speech_result_text.argtypes = [ctypes.c_void_p]
        lib.speech_result_text.restype = ctypes.c_char_p
        lib.speech_result_token_count.argtypes = [ctypes.c_void_p]
        lib.speech_result_token_count.restype = ctypes.c_size_t
        lib.speech_result_token.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_char_p),
        ]
        lib.speech_result_token.restype = ctypes.c_int
        lib.speech_result_untimed.argtypes = [ctypes.c_void_p]
        lib.speech_result_untimed.restype = ctypes.c_char_p
        lib.speech_status_name.argtypes = [ctypes.c_int]
        lib.speech_status_name.restype = ctypes.c_char_p
        lib.speech_last_error.argtypes = []
        lib.speech_last_error.restype = ctypes.c_char_p

        lib.speech_log_set(None, None)
        lparams = ctypes.c_void_p()
        if lib.speech_load_params_new(ctypes.byref(lparams)) != 0:
            raise RuntimeError("speech_load_params_new failed")
        try:
            lib.speech_load_params_set_device(lparams, self._fallback_device.encode("utf-8"))
            lib.speech_load_params_set_threads(lparams, int(config.threads))
            lib.speech_load_params_set_warmup(lparams, 1)
            model = ctypes.c_void_p()
            if lib.speech_model_load(str(model_path).encode("utf-8"), lparams, ctypes.byref(model)) != 0:
                err = lib.speech_last_error()
                raise RuntimeError(f"speech_model_load failed: {err.decode('utf-8', 'replace') if err else 'unknown'}")
            self._model = model
        finally:
            lib.speech_load_params_free(lparams)

        self.decode(np.zeros(RATE // 2, dtype=np.float32))

    def _check_status(self, op: str, status: int) -> int:
        if status in (self._SPEECH_OK, self._SPEECH_CANCELLED):
            return status
        sname = self._lib.speech_status_name(status)
        err = self._lib.speech_last_error()
        log.warning(
            "speech_cpp_error op=%s status=%s(%d) err=%s",
            op,
            sname.decode("utf-8", "replace") if sname else "unknown",
            status,
            err.decode("utf-8", "replace") if err else "",
        )
        raise BackendError("backend_error")

    def new_vad(self):
        return self.sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=120)

    def decode(
        self,
        samples,
        *,
        cancel_event: threading.Event | None = None,
        start_sample: int = 0,
        dedup_before_sample: int = 0,
        decoding_override: str | None = None,
        pad_ms_override: int | None = None,
    ):
        if self._model is None or (cancel_event is not None and cancel_event.is_set()):
            return ""
        pad_samples = (
            int(round(pad_ms_override * RATE / 1000.0))
            if pad_ms_override is not None
            else self._pad_samples
        )
        pad_s = pad_samples / float(RATE)
        decoding_str = decoding_override or self._decoding.decode("utf-8")
        decoding_bytes = decoding_str.encode("utf-8")
        audio = np.ascontiguousarray(samples, dtype=np.float32)
        if getattr(self, "_ane", None) is not None and not self._ane.retired:
            try:
                return self._ane.transcribe(
                    audio,
                    pad_samples=pad_samples,
                    use_beam=(decoding_str != "greedy"),
                    cancel_event=cancel_event,
                    start_sample=start_sample,
                    dedup_before_sample=dedup_before_sample,
                )
            except _AneFallback:
                pass
        if pad_samples > 0:
            pad = np.zeros(pad_samples, dtype=np.float32)
            audio = np.ascontiguousarray(np.concatenate([pad, audio, pad]), dtype=np.float32)
        req = ctypes.c_void_p()
        self._check_status("speech_request_new", self._lib.speech_request_new(self._model, ctypes.byref(req)))
        try:
            ptr = audio.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            self._check_status("set_audio", self._lib.speech_request_set_audio(req, ptr, audio.size, RATE))
            self._check_status("set_decoding", self._lib.speech_request_set_string(req, self._OPT_DECODING, decoding_bytes))
            if self._fallback_device == "gpu":
                self._check_status("set_decode_on_cpu", self._lib.speech_request_set_bool(req, self._OPT_DECODE_ON_CPU, 1))
            if dedup_before_sample > 0:
                self._check_status("set_timestamps", self._lib.speech_request_set_bool(req, self._OPT_TIMESTAMPS, 1))
            cb = None
            if cancel_event is not None:
                cb = self._PROGRESS_CB(lambda _done, _ud: 1 if cancel_event.is_set() else 0)
                self._check_status("set_progress", self._lib.speech_request_set_progress(req, cb, None))
            status = self._check_status("transcribe", self._lib.speech_transcribe(req))
            if status == self._SPEECH_CANCELLED or (cancel_event is not None and cancel_event.is_set()):
                return ""
            res = self._lib.speech_request_result(req)
            if not res:
                raise BackendError("backend_error")
            text = ""
            if dedup_before_sample > 0 and not self._lib.speech_result_untimed(res):
                tok_count = int(self._lib.speech_result_token_count(res))
                if tok_count > 0:
                    toks: list[tuple[float, float, str]] = []
                    st = ctypes.c_double()
                    en = ctypes.c_double()
                    tp = ctypes.c_char_p()
                    for idx in range(tok_count):
                        if self._lib.speech_result_token(res, idx, ctypes.byref(st), ctypes.byref(en), ctypes.byref(tp)) == 0:
                            toks.append((float(st.value), float(en.value), tp.value.decode("utf-8", "replace") if tp.value else ""))
                    text = _filter_tokens_by_sample(
                        toks,
                        start_sample=start_sample,
                        dedup_before_sample=dedup_before_sample,
                        pad_s=pad_s,
                    )
            if not text:
                raw = self._lib.speech_result_text(res)
                text = raw.decode("utf-8", "replace").strip() if raw else ""
            if text in ("ピッ!", "ピッ", "ピッ。"):
                return ""
            return _collapse_repetition(text)
        finally:
            self._lib.speech_request_free(req)

    def close(self):
        if getattr(self, "_ane", None) is not None:
            self._ane.close()
            self._ane = None
        if getattr(self, "_model", None):
            self._lib.speech_model_free(self._model)
            self._model = None


class _NoopSession:
    def close(self):
        pass


class _Qwen3Session:
    def __init__(self, translator, max_turns: int):
        self._translator = translator
        self.max_turns = max_turns
        self.turns = 0
        self.last_end_sample = 0

    def close(self):
        self.turns = 0
        self.last_end_sample = 0
        self._translator._reset_cache()


class Qwen3Translator:
    SYSTEM_PROMPT = (
        "你是专业日译中视频字幕翻译。只输出自然流畅的简体中文译文，不要解释，不要输出思考过程。"
        "严禁保留任何日文平假名或片假名：人名与专有名词一律音译为中文汉字，外来片假名借词必须译成对应中文或还原英文单词。"
    )
    REPAIR_PROMPT = (
        "请将以下日语台词准确翻译为自然流畅的纯简体中文（严禁包含任何平假名或片假名；"
        "专有名词音译为中文汉字，外来词译为中文或英文；若原文为残缺句尾也请意译为自然中文短语）：\n"
    )

    def __init__(self, snapshot, *, context_turns: int = 2):
        import mlx.core as mx
        from mlx_lm import generate, load
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache
        from mlx_lm.sample_utils import make_logits_processors, make_sampler

        self._mx = mx
        self._generate = generate
        self._generate_step = __import__("mlx_lm.generate", fromlist=["generate_step"]).generate_step
        self._trim_prompt_cache = trim_prompt_cache
        self._context_turns = context_turns
        self.model, self.tokenizer = load(str(snapshot))
        self.sampler = make_sampler(temp=0.0)
        vocab_n = int(getattr(self.tokenizer, "vocab_size", 0) or len(self.tokenizer._tokenizer))
        self._kana_token_ids = [
            tid for tid in range(vocab_n)
            if _KANA.search(self.tokenizer.decode([tid], skip_special_tokens=False))
        ]
        self._kana_bias = None
        self.logits_processors = [
            self._make_kana_mask_processor(),
            *make_logits_processors(repetition_penalty=1.08, repetition_context_size=32),
        ]
        self._eos_ids = set(self.tokenizer.eos_token_ids)

        sentinel = "<<<SPLIT_HERE>>>"
        full_template = self.tokenizer.apply_chat_template(
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{sentinel}"},
            ],
            add_generation_prompt=True,
            tokenize=False,
        )
        prefix_str, self._suffix_tail = full_template.split(sentinel, 1)
        self._next_turn_prefix = "<|im_end|>\n<|im_start|>user\n请翻译以下日语台词为简体中文：\n"
        self._prefix_ids = self.tokenizer.encode(prefix_str, add_special_tokens=False)
        self._prefix_len = len(self._prefix_ids)
        self._prompt_cache = make_prompt_cache(self.model)
        for _ in self._generate_step(
            mx.array(self._prefix_ids, dtype=mx.int32),
            self.model,
            max_tokens=0,
            prompt_cache=self._prompt_cache,
        ):
            pass
        mx.synchronize()
        self.translate(_NoopSession(), "こんにちは")

    def _reset_cache(self):
        cache = getattr(self, "_prompt_cache", None)
        if cache:
            cur_off = cache[0].offset
            if cur_off > self._prefix_len:
                self._trim_prompt_cache(cache, cur_off - self._prefix_len)

    def new_session(self):
        self._reset_cache()
        return _Qwen3Session(self, getattr(self, "_context_turns", 0))

    @staticmethod
    def _clean_output(raw: str) -> str:
        raw = raw.strip()
        if "</think>" in raw:
            raw = raw.split("</think>", 1)[-1].strip()
        if len(raw) >= 2 and ((raw[0] == '"' and raw[-1] == '"') or (raw[0] == "“" and raw[-1] == "”")):
            raw = raw[1:-1].strip()
        return raw

    def _make_kana_mask_processor(self):
        mx = self._mx

        def _ban_kana(_tokens, logits):
            if not self._kana_token_ids:
                return logits
            vocab = int(logits.shape[-1])
            if self._kana_bias is None or int(self._kana_bias.shape[-1]) != vocab:
                bias = np.zeros((1, vocab), dtype=np.float32)
                valid = [tid for tid in self._kana_token_ids if 0 <= tid < vocab]
                if valid:
                    bias[0, valid] = -1e9
                self._kana_bias = mx.array(bias, dtype=logits.dtype)
            return logits + self._kana_bias

        return _ban_kana

    def _run_prompt(self, messages, *, cancel_event: threading.Event | None = None):
        if cancel_event is not None and cancel_event.is_set():
            return ""
        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False,
        )
        if getattr(self, "_generate_step", None) is None or getattr(self, "_eos_ids", None) is None:
            raw = self._generate(
                self.model, self.tokenizer, prompt=prompt,
                max_tokens=256, sampler=self.sampler,
                logits_processors=self.logits_processors, verbose=False,
            )
            return self._clean_output(raw)

        mx = self._mx
        prompt_ids = mx.array(self.tokenizer.encode(prompt, add_special_tokens=False), dtype=mx.int32)

        def _check_cancel(_processed, _total):
            if cancel_event is not None and cancel_event.is_set():
                raise _Cancelled()

        tokens: list[int] = []
        try:
            for tok, _ in self._generate_step(
                prompt_ids,
                self.model,
                max_tokens=256,
                sampler=self.sampler,
                logits_processors=self.logits_processors,
                prefill_step_size=512,
                prompt_progress_callback=_check_cancel if cancel_event is not None else None,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return ""
                tid = int(tok)
                if tid in self._eos_ids:
                    break
                tokens.append(tid)
        except _Cancelled:
            return ""
        return self._clean_output(self.tokenizer.decode(tokens))

    def _run_cached_prompt(
        self,
        suffix_str: str,
        cancel_event: threading.Event | None = None,
        *,
        keep_kv: bool = False,
        on_delta=None,
    ) -> str:
        if cancel_event is not None and cancel_event.is_set():
            if keep_kv:
                self._reset_cache()
            return ""
        mx = self._mx
        suffix_ids = mx.array(self.tokenizer.encode(suffix_str, add_special_tokens=False), dtype=mx.int32)
        if not keep_kv:
            self._reset_cache()

        def _check_cancel(_processed, _total):
            if cancel_event is not None and cancel_event.is_set():
                raise _Cancelled()

        tokens: list[int] = []
        last_delta = ""
        completed = False
        try:
            for tok, _ in self._generate_step(
                suffix_ids,
                self.model,
                max_tokens=256,
                sampler=self.sampler,
                logits_processors=self.logits_processors,
                prompt_cache=self._prompt_cache,
                prefill_step_size=512,
                prompt_progress_callback=_check_cancel if cancel_event is not None else None,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return ""
                tid = int(tok)
                if tid in self._eos_ids:
                    completed = True
                    break
                tokens.append(tid)
                if on_delta is not None and (len(tokens) % 4 == 0 or len(tokens) == 2):
                    partial = self._clean_output(self.tokenizer.decode(tokens))
                    if partial and len(partial) >= 2 and partial != last_delta and not _KANA.search(partial):
                        last_delta = partial
                        on_delta(partial)
            else:
                completed = True
        except _Cancelled:
            return ""
        finally:
            if not keep_kv or not completed:
                self._reset_cache()

        out = self._clean_output(self.tokenizer.decode(tokens))
        if keep_kv and (not out or _KANA.search(out)):
            self._reset_cache()
        return out

    def translate(
        self,
        session,
        text_ja,
        *,
        cancel_event: threading.Event | None = None,
        start_sample: int | None = None,
        end_sample: int | None = None,
        on_delta=None,
    ):
        if cancel_event is not None and cancel_event.is_set():
            return ""
        has_cache = getattr(self, "_prompt_cache", None) is not None
        is_rolling = has_cache and isinstance(session, _Qwen3Session) and session.max_turns > 0
        if is_rolling:
            cache_extra = self._prompt_cache[0].offset - self._prefix_len
            gap_exceeded = (
                start_sample is not None
                and session.last_end_sample > 0
                and (start_sample - session.last_end_sample) > int(5.0 * RATE)
            )
            if session.turns == 0 or session.turns >= session.max_turns or cache_extra > 180 or gap_exceeded:
                self._reset_cache()
                session.turns = 0
            turn_prefix = self._next_turn_prefix if session.turns > 0 else ""
            raw = self._run_cached_prompt(
                turn_prefix + text_ja + self._suffix_tail,
                cancel_event=cancel_event,
                keep_kv=True,
                on_delta=on_delta,
            )
            if raw and not (cancel_event is not None and cancel_event.is_set()) and not _KANA.search(raw):
                session.turns += 1
                if end_sample is not None:
                    session.last_end_sample = end_sample
            else:
                self._reset_cache()
                session.turns = 0
        elif has_cache:
            raw = self._run_cached_prompt(
                text_ja + self._suffix_tail,
                cancel_event=cancel_event,
                keep_kv=False,
                on_delta=on_delta,
            )
        else:
            raw = self._run_prompt([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{text_ja}"},
            ])
        if cancel_event is not None and cancel_event.is_set():
            return ""
        if raw and _KANA.search(raw):
            repaired = self._run_prompt(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": f"{self.REPAIR_PROMPT}原文：{text_ja}\n上次含假名草稿：{raw}"},
                ],
                cancel_event=cancel_event,
            )
            if repaired:
                return repaired
        return raw

    def translate_draft(self, session, text_ja: str, *, cancel_event: threading.Event | None = None) -> str:
        """Non-mutating KV cache peek for streaming draft previews."""
        if not text_ja or (cancel_event is not None and cancel_event.is_set()):
            return ""
        cache = getattr(self, "_prompt_cache", None)
        if cache is None:
            return ""
        mx = self._mx
        saved_offset = cache[0].offset
        turn_prefix = self._next_turn_prefix if saved_offset > self._prefix_len else ""
        suffix_ids = mx.array(
            self.tokenizer.encode(turn_prefix + text_ja + self._suffix_tail, add_special_tokens=False),
            dtype=mx.int32,
        )

        def _check_cancel(_processed, _total):
            if cancel_event is not None and cancel_event.is_set():
                raise _Cancelled()

        tokens: list[int] = []
        try:
            for tok, _ in self._generate_step(
                suffix_ids,
                self.model,
                max_tokens=96,
                sampler=self.sampler,
                logits_processors=self.logits_processors,
                prompt_cache=cache,
                prefill_step_size=512,
                prompt_progress_callback=_check_cancel if cancel_event is not None else None,
            ):
                if cancel_event is not None and cancel_event.is_set():
                    return ""
                tid = int(tok)
                if tid in self._eos_ids:
                    break
                tokens.append(tid)
        except _Cancelled:
            return ""
        finally:
            extra = cache[0].offset - saved_offset
            if extra > 0:
                self._trim_prompt_cache(cache, extra)

        out = self._clean_output(self.tokenizer.decode(tokens))
        return "" if not out or _KANA.search(out) else out


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
        self.uses_speech_cpp_asr = profile.asr.type == "speech_cpp_nemo"
        self.uses_gpu_speech_cpp = self.uses_speech_cpp_asr and getattr(profile.asr, "device", "cpu") == "gpu"
        self.uses_ane_asr = self.uses_speech_cpp_asr and getattr(profile.asr, "device", "cpu") == "ane"
        self.uses_gpu_scheduler = self.uses_mlx_asr or self.uses_gpu_speech_cpp
        self.vad_path = cache / "reazonspeech-assets/silero_vad.onnx"

        if self.uses_mlx_asr:
            self.asr_path = hub / "models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots" / profile.asr.model_revision
            asr_files = (
                self.asr_path / "config.json",
                self.asr_path / "model.safetensors",
                self.asr_path / "tokenizer_config.json",
            )
        elif self.uses_speech_cpp_asr:
            self.speech_dylib_path = cache / "speech-cpp/libspeech.dylib"
            self.asr_path = cache / "speech-cpp" / profile.asr.model_file
            asr_files = (self.speech_dylib_path, self.asr_path)
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
        self.vad_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="subtitle-vad")
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
                if self.uses_mlx_asr:
                    # Bind all MLX models (including MLX ASR) to mt_pool so Metal runs on one persistent thread.
                    self.recognizer = await loop.run_in_executor(
                        self.mt_pool, Qwen3AsrRecognizer, self.asr_path, self.vad_path, self.profile.asr)
                elif self.uses_speech_cpp_asr:
                    self.recognizer = await loop.run_in_executor(
                        self.asr_pool, SpeechCppNeMoRecognizer,
                        self.speech_dylib_path, self.asr_path, self.vad_path, self.profile.asr)
                else:
                    self.recognizer = await loop.run_in_executor(
                        self.asr_pool, ReazonRecognizer, self.asr_path, self.vad_path, self.profile.asr)
            if self.profile.mt.type == "qwen3_mt_mlx":
                ctx_turns = getattr(self.profile.mt, "context_turns", 2)
                self.translator = await loop.run_in_executor(
                    self.mt_pool,
                    lambda: _call_with_cancel(
                        Qwen3Translator, self.mt_path, cancel_event=None, context_turns=ctx_turns
                    ),
                )
            else:
                self.translator = await loop.run_in_executor(self.mt_pool, HyTranslator, self.mt_path)

    async def open(self, emit, *, record=None):
        await self.warmup()
        vad_pool = self.vad_pool if self.uses_speech_cpp_asr else self.asr_pool
        vad = await asyncio.get_running_loop().run_in_executor(vad_pool, self.recognizer.new_vad)
        return LocalSession(self, vad, self.translator.new_session(), emit, record)

    async def aclose(self):
        if self.recognizer is not None and hasattr(self.recognizer, "close"):
            pool = self.mt_pool if self.uses_mlx_asr else self.asr_pool
            await asyncio.get_running_loop().run_in_executor(pool, self.recognizer.close)
        await asyncio.to_thread(self.vad_pool.shutdown)
        await asyncio.to_thread(self.asr_pool.shutdown)
        await asyncio.to_thread(self.mt_pool.shutdown)


class LocalSession:
    MAX_QUEUED_AUDIO_S = 20.0

    def __init__(self, backend, vad, translation, emit, record):
        self.backend = backend
        self.vad = vad
        self.segmenter = _SmartSegmenter(
            vad,
            max_segment_s=getattr(backend.profile.asr, "max_segment_s", 0.0),
            overlap_ms=getattr(backend.profile.asr, "overlap_ms", 350),
        )
        self.translation = translation
        self.emit = emit
        self.asr_work = self.mt_work = None
        self.closed = False
        self.cancelled = threading.Event()
        self.pending = asyncio.Queue(maxsize=4)
        self.record = record
        self.segment_ids = count(1)
        self._last_ja = ""
        self._delta_tasks: set[asyncio.Task] = set()
        self._draft_interval_s = float(getattr(backend.profile.asr, "draft_interval_s", 0.0))
        self._pending_draft: _DraftSnapshot | None = None
        self._draft_cancel = threading.Event()
        self._last_draft_ja = ""

        if self.backend.uses_gpu_scheduler:
            self._asr_queue: deque[_PendingAudio] = deque()
            self._mt_queue: deque[RecognizedSegment] = deque()
            self._queued_audio_s = 0.0
            self._input_done = False
            self._work_ready = asyncio.Event()
            self.mt_task = asyncio.create_task(self._run_mlx_scheduler())
            self._cpp_asr_task = None
        elif self.backend.uses_ane_asr:
            self._asr_queue: deque[_PendingAudio] = deque()
            self._mt_queue: deque[RecognizedSegment] = deque()
            self._queued_audio_s = 0.0
            self._asr_input_done = False
            self._mt_input_done = False
            self._ane_asr_ready = asyncio.Event()
            self._ane_mt_ready = asyncio.Event()
            self._pending_draft_ja: tuple[_DraftSnapshot, str] | None = None
            self._cpp_asr_task = asyncio.create_task(self._run_ane_asr())
            self.mt_task = asyncio.create_task(self._run_ane_mt_scheduler())
        elif self.backend.uses_speech_cpp_asr:
            self._cpp_asr_queue: asyncio.Queue[_PendingAudio | None] = asyncio.Queue(maxsize=8)
            self._cpp_asr_task = asyncio.create_task(self._run_cpp_asr())
            self.mt_task = asyncio.create_task(self.run_translation())
        else:
            self._cpp_asr_task = None
            self.mt_task = asyncio.create_task(self.run_translation())

    def _schedule_delta(self, segment: RecognizedSegment, partial: str):
        if self.cancelled.is_set() or not partial:
            return
        task = asyncio.create_task(
            self.emit(CaptionDelta(segment.start_sample, segment.end_sample, partial, segment.segment_id))
        )
        self._delta_tasks.add(task)
        task.add_done_callback(self._delta_tasks.discard)

    async def _drain_deltas(self):
        await asyncio.sleep(0)
        if self._delta_tasks:
            await asyncio.gather(*list(self._delta_tasks), return_exceptions=True)

    def translate(self, segment, loop: asyncio.AbstractEventLoop | None = None):
        if self.record:
            self.record("mt_start", **vars(segment))
        on_delta = None
        if loop is not None:
            on_delta = lambda partial: None if self.cancelled.is_set() else loop.call_soon_threadsafe(
                self._schedule_delta, segment, partial
            )
        text = _call_with_cancel(
            self.backend.translator.translate,
            self.translation,
            segment.text_ja,
            cancel_event=self.cancelled,
            start_sample=segment.start_sample,
            end_sample=segment.end_sample,
            on_delta=on_delta,
        )
        if self.record:
            self.record("mt_end", text_zh=text, **vars(segment))
        return text

    async def run_translation(self):
        try:
            loop = asyncio.get_running_loop()
            while (segment := await self.pending.get()) is not None:
                if self.cancelled.is_set():
                    break
                self.mt_work = loop.run_in_executor(self.backend.mt_pool, self.translate, segment, loop)
                translated = await asyncio.shield(self.mt_work)
                await self._drain_deltas()
                if not self.cancelled.is_set() and translated:
                    await self.emit(Caption(segment.start_sample, segment.end_sample, translated, segment.segment_id))
        except Exception:
            if not self.cancelled.is_set():
                await self.emit(BackendError())

    async def _run_cpp_asr(self):
        try:
            loop = asyncio.get_running_loop()
            while (item := await self._cpp_asr_queue.get()) is not None:
                if self.cancelled.is_set():
                    break
                span = {"segment_id": item.segment_id, "start_sample": item.start_sample, "end_sample": item.end_sample}
                if self.record:
                    self.record("asr_start", **span)
                self.asr_work = loop.run_in_executor(
                    self.backend.asr_pool,
                    lambda s=item: _call_with_cancel(
                        self.backend.recognizer.decode,
                        s.samples,
                        cancel_event=self.cancelled,
                        start_sample=s.start_sample,
                        dedup_before_sample=s.dedup_before_sample,
                    ),
                )
                text = await asyncio.shield(self.asr_work)
                if self.cancelled.is_set():
                    break
                if text:
                    self._last_ja = text
                if self.record:
                    self.record("asr_end", text_ja=text, **span)
                if text:
                    self._enqueue_segment(RecognizedSegment(item.segment_id, item.start_sample, item.end_sample, text))
            await self.pending.put(None)
        except Exception as exc:
            if not self.cancelled.is_set():
                await self.emit(exc if isinstance(exc, BackendError) else BackendError())

    async def _run_ane_asr(self):
        """Dedicated ANE ASR worker: Final ASR > Draft ASR, feeding GPU MT queue in parallel."""
        try:
            loop = asyncio.get_running_loop()
            while not self.cancelled.is_set():
                if self._asr_queue:
                    item = self._asr_queue.popleft()
                    self._queued_audio_s = max(0.0, self._queued_audio_s - len(item.samples) / RATE)
                    span = {"segment_id": item.segment_id, "start_sample": item.start_sample, "end_sample": item.end_sample}
                    if self.record:
                        self.record("asr_start", **span)
                    self.asr_work = loop.run_in_executor(
                        self.backend.asr_pool,
                        lambda s=item: _call_with_cancel(
                            self.backend.recognizer.decode,
                            s.samples,
                            cancel_event=self.cancelled,
                            start_sample=s.start_sample,
                            dedup_before_sample=s.dedup_before_sample,
                        ),
                    )
                    text = await asyncio.shield(self.asr_work)
                    if self.cancelled.is_set():
                        break
                    if text:
                        self._last_ja = text
                    if self.record:
                        self.record("asr_end", text_ja=text, **span)
                    if text:
                        seg = RecognizedSegment(item.segment_id, item.start_sample, item.end_sample, text)
                        self._mt_queue.append(seg)
                        if self.record:
                            self.record("mt_enqueued", **vars(seg))
                        self._ane_mt_ready.set()
                    continue
                if self._pending_draft is not None:
                    snap = self._pending_draft
                    self._pending_draft = None
                    if self.cancelled.is_set():
                        break
                    self._draft_cancel.clear()
                    self.asr_work = loop.run_in_executor(
                        self.backend.asr_pool,
                        lambda s=snap: _call_with_cancel(
                            self.backend.recognizer.decode,
                            s.samples,
                            cancel_event=self._draft_cancel,
                            start_sample=s.start_sample,
                            dedup_before_sample=s.dedup_before_sample,
                            decoding_override="greedy",
                            pad_ms_override=150,
                        ),
                    )
                    draft_ja = await asyncio.shield(self.asr_work)
                    if (
                        not self.cancelled.is_set()
                        and not self._draft_cancel.is_set()
                        and not self._asr_queue
                        and not self._mt_queue
                        and draft_ja
                        and _should_emit_draft_ja(self._last_draft_ja, draft_ja)
                    ):
                        self._pending_draft_ja = (snap, draft_ja)
                        self._ane_mt_ready.set()
                    continue
                if self._asr_input_done:
                    break
                self._ane_asr_ready.clear()
                await self._ane_asr_ready.wait()
            self._mt_input_done = True
            self._ane_mt_ready.set()
        except Exception as exc:
            self._mt_input_done = True
            self._ane_mt_ready.set()
            if not self.cancelled.is_set():
                await self.emit(exc if isinstance(exc, BackendError) else BackendError())

    async def _run_ane_mt_scheduler(self):
        """Dedicated GPU MT worker running in parallel with ANE ASR: Final MT > Draft MT."""
        try:
            loop = asyncio.get_running_loop()
            while not self.cancelled.is_set():
                if self._mt_queue:
                    segment = self._mt_queue.popleft()
                    self.mt_work = loop.run_in_executor(self.backend.mt_pool, self.translate, segment, loop)
                    translated = await asyncio.shield(self.mt_work)
                    await self._drain_deltas()
                    if not self.cancelled.is_set() and translated:
                        await self.emit(Caption(segment.start_sample, segment.end_sample, translated, segment.segment_id))
                    continue
                if self._pending_draft_ja is not None:
                    snap, draft_ja = self._pending_draft_ja
                    self._pending_draft_ja = None
                    if self.cancelled.is_set() or self._draft_cancel.is_set() or self._asr_queue or self._mt_queue:
                        continue
                    self.mt_work = loop.run_in_executor(
                        self.backend.mt_pool,
                        lambda ja=draft_ja: _call_with_cancel(
                            self.backend.translator.translate_draft,
                            self.translation,
                            ja,
                            cancel_event=self._draft_cancel,
                        ),
                    )
                    draft_zh = await asyncio.shield(self.mt_work)
                    if (
                        not self.cancelled.is_set()
                        and not self._draft_cancel.is_set()
                        and not self._asr_queue
                        and not self._mt_queue
                        and draft_zh
                    ):
                        self._last_draft_ja = draft_ja
                        if self.record:
                            self.record(
                                "draft_sent",
                                start_sample=snap.start_sample,
                                end_sample=snap.end_sample,
                                text_ja=draft_ja,
                                text_zh=draft_zh,
                            )
                        await self.emit(CaptionDelta(snap.start_sample, snap.end_sample, draft_zh))
                    continue
                if self._mt_input_done:
                    break
                self._ane_mt_ready.clear()
                await self._ane_mt_ready.wait()
        except Exception:
            if not self.cancelled.is_set():
                await self.emit(BackendError())

    async def _run_mlx_scheduler(self):
        """Single-MLX-thread priority scheduler: MT > ASR."""
        try:
            loop = asyncio.get_running_loop()
            while not self.cancelled.is_set():
                if self._mt_queue:
                    segment = self._mt_queue.popleft()
                    self.mt_work = loop.run_in_executor(self.backend.mt_pool, self.translate, segment, loop)
                    translated = await asyncio.shield(self.mt_work)
                    await self._drain_deltas()
                    if not self.cancelled.is_set() and translated:
                        await self.emit(Caption(segment.start_sample, segment.end_sample, translated, segment.segment_id))
                    continue
                if self._asr_queue:
                    item = self._asr_queue.popleft()
                    self._queued_audio_s = max(0.0, self._queued_audio_s - len(item.samples) / RATE)
                    span = {"segment_id": item.segment_id, "start_sample": item.start_sample, "end_sample": item.end_sample}
                    if self.record:
                        self.record("asr_start", **span)
                    if self.backend.uses_speech_cpp_asr:
                        self.asr_work = loop.run_in_executor(
                            self.backend.asr_pool,
                            lambda s=item: _call_with_cancel(
                                self.backend.recognizer.decode,
                                s.samples,
                                cancel_event=self.cancelled,
                                start_sample=s.start_sample,
                                dedup_before_sample=s.dedup_before_sample,
                            ),
                        )
                    else:
                        self.asr_work = loop.run_in_executor(
                            self.backend.mt_pool,
                            lambda s=item.samples: _call_with_cancel(
                                self.backend.recognizer.decode, s, cancel_event=self.cancelled
                            ),
                        )
                    text = await asyncio.shield(self.asr_work)
                    if self.cancelled.is_set():
                        break
                    if not self.backend.uses_speech_cpp_asr and item.has_overlap and self._last_ja and text:
                        text = _dedup_boundary_ja(self._last_ja, text)
                    if text:
                        self._last_ja = text
                    if self.record:
                        self.record("asr_end", text_ja=text, **span)
                    if text:
                        seg = RecognizedSegment(item.segment_id, item.start_sample, item.end_sample, text)
                        self._mt_queue.append(seg)
                        if self.record:
                            self.record("mt_enqueued", **vars(seg))
                    continue
                if self._pending_draft is not None:
                    snap = self._pending_draft
                    self._pending_draft = None
                    if self.cancelled.is_set():
                        break
                    self._draft_cancel.clear()
                    self.asr_work = loop.run_in_executor(
                        self.backend.asr_pool,
                        lambda s=snap: _call_with_cancel(
                            self.backend.recognizer.decode,
                            s.samples,
                            cancel_event=self._draft_cancel,
                            start_sample=s.start_sample,
                            dedup_before_sample=s.dedup_before_sample,
                            decoding_override="greedy",
                            pad_ms_override=150,
                        ),
                    )
                    draft_ja = await asyncio.shield(self.asr_work)
                    if (
                        self.cancelled.is_set()
                        or self._draft_cancel.is_set()
                        or self._asr_queue
                        or self._mt_queue
                        or not draft_ja
                    ):
                        continue
                    if not _should_emit_draft_ja(self._last_draft_ja, draft_ja):
                        continue
                    self.mt_work = loop.run_in_executor(
                        self.backend.mt_pool,
                        lambda ja=draft_ja: _call_with_cancel(
                            self.backend.translator.translate_draft,
                            self.translation,
                            ja,
                            cancel_event=self._draft_cancel,
                        ),
                    )
                    draft_zh = await asyncio.shield(self.mt_work)
                    if (
                        not self.cancelled.is_set()
                        and not self._draft_cancel.is_set()
                        and not self._asr_queue
                        and not self._mt_queue
                        and draft_zh
                    ):
                        self._last_draft_ja = draft_ja
                        if self.record:
                            self.record(
                                "draft_sent",
                                start_sample=snap.start_sample,
                                end_sample=snap.end_sample,
                                text_ja=draft_ja,
                                text_zh=draft_zh,
                            )
                        await self.emit(CaptionDelta(snap.start_sample, snap.end_sample, draft_zh))
                    continue
                if self._input_done:
                    break
                self._work_ready.clear()
                await self._work_ready.wait()
        except Exception:
            if not self.cancelled.is_set():
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
        if not self.backend.uses_mlx_asr and not self.backend.uses_speech_cpp_asr:
            self.asr_work = loop.run_in_executor(
                self.backend.asr_pool, self.backend.recognizer.segments, self.vad, frame,
                self.segment_ids, self.record)
            for segment in await asyncio.shield(self.asr_work):
                self._enqueue_segment(segment)
            return

        vad_pool = self.backend.vad_pool if self.backend.uses_speech_cpp_asr else self.backend.asr_pool
        if (self.backend.uses_gpu_speech_cpp or self.backend.uses_ane_asr) and self._draft_interval_s > 0:
            released, snap = await loop.run_in_executor(
                vad_pool,
                lambda: self.segmenter.push_and_preview(frame, draft_interval_s=self._draft_interval_s),
            )
        else:
            released = await loop.run_in_executor(vad_pool, self.segmenter.push, frame)
            snap = None

        if self.backend.uses_gpu_scheduler or self.backend.uses_ane_asr:
            if released:
                self._draft_cancel.set()
                self._pending_draft = None
                if self.backend.uses_ane_asr:
                    self._pending_draft_ja = None
                self._last_draft_ja = ""
            elif snap is not None and not self._asr_queue and not self._mt_queue:
                self._pending_draft = snap
                (self._ane_asr_ready if self.backend.uses_ane_asr else self._work_ready).set()

        for chunk in released:
            segment_id = next(self.segment_ids)
            start = chunk.start_sample
            samples = chunk.samples
            end = start + len(samples)
            if self.record:
                self.record(
                    "vad_released",
                    segment_id=segment_id,
                    start_sample=start,
                    end_sample=end,
                    cut_reason=chunk.cut_reason,
                    dedup_before_sample=chunk.dedup_before_sample,
                )
            item = _PendingAudio(
                segment_id,
                start,
                end,
                samples,
                chunk.dedup_before_sample > 0,
                chunk.dedup_before_sample,
            )
            if self.backend.uses_gpu_scheduler or self.backend.uses_ane_asr:
                dur = len(samples) / RATE
                if self._queued_audio_s + dur > self.MAX_QUEUED_AUDIO_S:
                    raise BackendError("overloaded")
                self._asr_queue.append(item)
                self._queued_audio_s += dur
                (self._ane_asr_ready if self.backend.uses_ane_asr else self._work_ready).set()
            else:
                try:
                    self._cpp_asr_queue.put_nowait(item)
                except asyncio.QueueFull:
                    raise BackendError("overloaded") from None

    async def finish(self):
        self._draft_cancel.set()
        self._pending_draft = None
        await self.accept_pcm(None)
        if self.backend.uses_gpu_scheduler:
            self._input_done = True
            self._work_ready.set()
            await asyncio.shield(self.mt_task)
        elif self.backend.uses_ane_asr:
            self._pending_draft_ja = None
            self._asr_input_done = True
            self._ane_asr_ready.set()
            await asyncio.shield(self._cpp_asr_task)
            await asyncio.shield(self.mt_task)
        elif self.backend.uses_speech_cpp_asr:
            await self._cpp_asr_queue.put(None)
            await asyncio.shield(self._cpp_asr_task)
            await asyncio.shield(self.mt_task)
        else:
            await self.pending.put(None)
            await asyncio.shield(self.mt_task)

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        self.cancelled.set()
        self._draft_cancel.set()
        self._pending_draft = None
        if self.backend.uses_gpu_scheduler:
            self._work_ready.set()
        if self.backend.uses_ane_asr:
            self._pending_draft_ja = None
            self._ane_asr_ready.set()
            self._ane_mt_ready.set()
        self.translation.close()
        for task in (self._cpp_asr_task, self.mt_task):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        for work in (self.asr_work, self.mt_work):
            if work is not None:
                with suppress(Exception):
                    await asyncio.shield(work)
        for dt in list(self._delta_tasks):
            dt.cancel()
        if self._delta_tasks:
            await asyncio.gather(*list(self._delta_tasks), return_exceptions=True)
