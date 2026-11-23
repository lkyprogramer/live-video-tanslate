import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import ctypes
from dataclasses import dataclass, replace
import hashlib
import inspect
from itertools import count
from pathlib import Path
import re
import threading

import numpy as np

from .backend import BackendError, Caption

RATE = 16000
_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
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


def _call_with_cancel(fn, *args, cancel_event: threading.Event | None):
    if cancel_event is not None and "cancel_event" in inspect.signature(fn).parameters:
        return fn(*args, cancel_event=cancel_event)
    return fn(*args)


@dataclass(frozen=True)
class RecognizedSegment:
    segment_id: int
    start_sample: int
    end_sample: int
    text_ja: str


@dataclass(frozen=True)
class _PendingAudio:
    segment_id: int
    start_sample: int
    end_sample: int
    samples: np.ndarray
    has_overlap: bool = False


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
    """Silero VAD wrapper with optional energy-valley forced splitting and left overlap."""

    def __init__(self, vad, *, max_segment_s: float = 0.0, overlap_ms: int = 350):
        self.vad = vad
        self.max_segment_samples = int(round(max_segment_s * RATE)) if max_segment_s > 0 else 0
        self.overlap_samples = int(round(overlap_ms * RATE / 1000.0))
        self.total_samples = 0
        self.buf: list[np.ndarray] = []
        self.buf_start_sample: int | None = None
        self.buf_len = 0
        self.burst_cut_sample = 0

    def _find_energy_valley_cut(self, arr: np.ndarray) -> int:
        n = len(arr)
        search_start = max(int(2.0 * RATE), n - int(1.2 * RATE))
        search_end = max(search_start + 1, n - int(0.15 * RATE))
        win = int(0.16 * RATE)
        step = int(0.02 * RATE)
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
        return best_pos

    def push(self, frame: bytes | None) -> list[tuple[int, np.ndarray, bool]]:
        if self.max_segment_samples <= 0:
            return [(start, np.asarray(samples, dtype=np.float32), False)
                    for start, samples in vad_segments(self.vad, frame)]

        out: list[tuple[int, np.ndarray, bool]] = []
        if frame is None:
            samples_f32 = np.empty(0, dtype=np.float32)
            self.vad.flush()
        else:
            samples_f32 = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
            self.vad.accept_waveform(samples_f32)

        while not self.vad.empty():
            segment = self.vad.front
            seg_start = int(segment.start)
            seg_samples = np.asarray(segment.samples, dtype=np.float32)
            self.vad.pop()
            seg_end = seg_start + len(seg_samples)
            if self.burst_cut_sample > seg_start:
                if seg_end - self.burst_cut_sample >= int(0.25 * RATE):
                    tail_start = max(seg_start, self.burst_cut_sample - self.overlap_samples)
                    out.append((tail_start, seg_samples[tail_start - seg_start :], True))
            elif seg_end > seg_start:
                out.append((seg_start, seg_samples, False))
            self.buf.clear()
            self.buf_start_sample = None
            self.buf_len = 0
            self.burst_cut_sample = 0

        if frame is not None:
            if self.vad.is_speech_detected():
                if self.buf_start_sample is None:
                    self.buf_start_sample = self.total_samples
                self.buf.append(samples_f32)
                self.buf_len += len(samples_f32)
                while self.buf_len >= self.max_segment_samples:
                    arr = np.concatenate(self.buf)
                    cut_idx = self._find_energy_valley_cut(arr)
                    global_cut = self.buf_start_sample + cut_idx
                    if cut_idx <= 0 or global_cut <= self.burst_cut_sample:
                        break
                    out.append((self.buf_start_sample, arr[:cut_idx], self.burst_cut_sample > 0))
                    next_off = max(0, cut_idx - self.overlap_samples)
                    rem = arr[next_off:]
                    self.buf = [rem]
                    self.buf_len = len(rem)
                    self.buf_start_sample += next_off
                    self.burst_cut_sample = global_cut
            else:
                self.buf.clear()
                self.buf_start_sample = None
                self.buf_len = 0
                self.burst_cut_sample = 0
            self.total_samples += len(samples_f32)
        else:
            self.buf.clear()
            self.buf_start_sample = None
            self.buf_len = 0
            self.burst_cut_sample = 0

        return out


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


class SpeechCppNeMoRecognizer:
    """ctypes binding for speech.cpp FastConformer-RNNT running on CPU."""

    _PROGRESS_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_double, ctypes.c_void_p)
    _OPT_DECODING = 10

    def __init__(self, dylib_path: Path, model_path: Path, vad_path: Path, config):
        import sherpa_onnx

        self.sherpa = sherpa_onnx
        self.vad_config = silero_config(vad_path, config.vad_pause_ms)
        self._decoding = config.decoding.encode("utf-8")

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
        lib.speech_request_set_progress.argtypes = [ctypes.c_void_p, self._PROGRESS_CB, ctypes.c_void_p]
        lib.speech_request_set_progress.restype = ctypes.c_int
        lib.speech_transcribe.argtypes = [ctypes.c_void_p]
        lib.speech_transcribe.restype = ctypes.c_int
        lib.speech_request_result.argtypes = [ctypes.c_void_p]
        lib.speech_request_result.restype = ctypes.c_void_p
        lib.speech_result_text.argtypes = [ctypes.c_void_p]
        lib.speech_result_text.restype = ctypes.c_char_p
        lib.speech_last_error.argtypes = []
        lib.speech_last_error.restype = ctypes.c_char_p

        lib.speech_log_set(None, None)
        lparams = ctypes.c_void_p()
        if lib.speech_load_params_new(ctypes.byref(lparams)) != 0:
            raise RuntimeError("speech_load_params_new failed")
        try:
            lib.speech_load_params_set_device(lparams, b"cpu")
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

    def new_vad(self):
        return self.sherpa.VoiceActivityDetector(self.vad_config, buffer_size_in_seconds=120)

    def decode(self, samples, *, cancel_event: threading.Event | None = None):
        if self._model is None or (cancel_event is not None and cancel_event.is_set()):
            return ""
        audio = np.ascontiguousarray(samples, dtype=np.float32)
        req = ctypes.c_void_p()
        if self._lib.speech_request_new(self._model, ctypes.byref(req)) != 0:
            return ""
        try:
            ptr = audio.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            self._lib.speech_request_set_audio(req, ptr, audio.size, RATE)
            self._lib.speech_request_set_string(req, self._OPT_DECODING, self._decoding)
            cb = None
            if cancel_event is not None:
                cb = self._PROGRESS_CB(lambda _done, _ud: 1 if cancel_event.is_set() else 0)
                self._lib.speech_request_set_progress(req, cb, None)
            status = self._lib.speech_transcribe(req)
            if status != 0:
                return ""
            res = self._lib.speech_request_result(req)
            if not res:
                return ""
            raw = self._lib.speech_result_text(res)
            text = raw.decode("utf-8", "replace").strip() if raw else ""
            if text in ("ピッ!", "ピッ", "ピッ。"):
                return ""
            return _collapse_repetition(text)
        finally:
            self._lib.speech_request_free(req)

    def close(self):
        if getattr(self, "_model", None):
            self._lib.speech_model_free(self._model)
            self._model = None


class _NoopSession:
    def close(self):
        pass


class Qwen3Translator:
    SYSTEM_PROMPT = (
        "你是专业日译中视频字幕翻译。只输出自然流畅的简体中文译文，不要解释，不要输出思考过程。"
        "严禁保留任何日文平假名或片假名：人名与专有名词一律音译为中文汉字，外来片假名借词必须译成对应中文或还原英文单词。"
    )
    REPAIR_PROMPT = (
        "请将以下日语台词准确翻译为自然流畅的纯简体中文（严禁包含任何平假名或片假名；"
        "专有名词音译为中文汉字，外来词译为中文或英文；若原文为残缺句尾也请意译为自然中文短语）：\n"
    )

    def __init__(self, snapshot):
        import mlx.core as mx
        from mlx_lm import generate, load
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache
        from mlx_lm.sample_utils import make_logits_processors, make_sampler

        self._mx = mx
        self._generate = generate
        self._generate_step = __import__("mlx_lm.generate", fromlist=["generate_step"]).generate_step
        self._trim_prompt_cache = trim_prompt_cache
        self.model, self.tokenizer = load(str(snapshot))
        self.sampler = make_sampler(temp=0.0)
        self.logits_processors = make_logits_processors(
            repetition_penalty=1.08, repetition_context_size=32,
        )
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

    def new_session(self):
        return _NoopSession()

    @staticmethod
    def _clean_output(raw: str) -> str:
        raw = raw.strip()
        if "</think>" in raw:
            raw = raw.split("</think>", 1)[-1].strip()
        if len(raw) >= 2 and ((raw[0] == '"' and raw[-1] == '"') or (raw[0] == "“" and raw[-1] == "”")):
            raw = raw[1:-1].strip()
        return raw

    def _run_prompt(self, messages):
        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False,
        )
        raw = self._generate(
            self.model, self.tokenizer, prompt=prompt,
            max_tokens=256, sampler=self.sampler,
            logits_processors=self.logits_processors, verbose=False,
        )
        return self._clean_output(raw)

    def _run_cached_prompt(self, suffix_str: str, cancel_event: threading.Event | None = None) -> str:
        if cancel_event is not None and cancel_event.is_set():
            return ""
        mx = self._mx
        suffix_ids = mx.array(self.tokenizer.encode(suffix_str, add_special_tokens=False), dtype=mx.int32)
        cur_off = self._prompt_cache[0].offset
        if cur_off > self._prefix_len:
            self._trim_prompt_cache(self._prompt_cache, cur_off - self._prefix_len)

        def _check_cancel(_processed, _total):
            if cancel_event is not None and cancel_event.is_set():
                raise _Cancelled()

        tokens: list[int] = []
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
                    break
                tokens.append(tid)
        except _Cancelled:
            return ""
        finally:
            used = self._prompt_cache[0].offset - self._prefix_len
            if used > 0:
                self._trim_prompt_cache(self._prompt_cache, used)

        return self._clean_output(self.tokenizer.decode(tokens))

    def translate(self, session, text_ja, *, cancel_event: threading.Event | None = None):
        if cancel_event is not None and cancel_event.is_set():
            return ""
        if getattr(self, "_prompt_cache", None) is not None:
            raw = self._run_cached_prompt(text_ja + self._suffix_tail, cancel_event=cancel_event)
        else:
            raw = self._run_prompt([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{text_ja}"},
            ])
        if cancel_event is not None and cancel_event.is_set():
            return ""
        if raw and _KANA.search(raw):
            repaired = self._run_prompt([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": f"{self.REPAIR_PROMPT}原文：{text_ja}\n上次含假名草稿：{raw}"},
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
        self.uses_speech_cpp_asr = profile.asr.type == "speech_cpp_nemo"
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
            mt_cls = Qwen3Translator if self.profile.mt.type == "qwen3_mt_mlx" else HyTranslator
            self.translator = await loop.run_in_executor(self.mt_pool, mt_cls, self.mt_path)

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

        if self.backend.uses_mlx_asr:
            self._asr_queue: deque[_PendingAudio] = deque()
            self._mt_queue: deque[RecognizedSegment] = deque()
            self._queued_audio_s = 0.0
            self._input_done = False
            self._work_ready = asyncio.Event()
            self.mt_task = asyncio.create_task(self._run_mlx_scheduler())
            self._cpp_asr_task = None
        elif self.backend.uses_speech_cpp_asr:
            self._cpp_asr_queue: asyncio.Queue[_PendingAudio | None] = asyncio.Queue(maxsize=8)
            self._cpp_asr_task = asyncio.create_task(self._run_cpp_asr())
            self.mt_task = asyncio.create_task(self.run_translation())
        else:
            self._cpp_asr_task = None
            self.mt_task = asyncio.create_task(self.run_translation())

    def translate(self, segment):
        if self.record:
            self.record("mt_start", **vars(segment))
        text = _call_with_cancel(
            self.backend.translator.translate,
            self.translation,
            segment.text_ja,
            cancel_event=self.cancelled,
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
                self.mt_work = loop.run_in_executor(self.backend.mt_pool, self.translate, segment)
                translated = await asyncio.shield(self.mt_work)
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
                    lambda s=item.samples: _call_with_cancel(
                        self.backend.recognizer.decode, s, cancel_event=self.cancelled
                    ),
                )
                text = await asyncio.shield(self.asr_work)
                if self.cancelled.is_set():
                    break
                if item.has_overlap and self._last_ja and text:
                    text = _dedup_boundary_ja(self._last_ja, text)
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

    async def _run_mlx_scheduler(self):
        """Single-MLX-thread priority scheduler: MT > ASR."""
        try:
            loop = asyncio.get_running_loop()
            while not self.cancelled.is_set():
                if self._mt_queue:
                    segment = self._mt_queue.popleft()
                    self.mt_work = loop.run_in_executor(self.backend.mt_pool, self.translate, segment)
                    translated = await asyncio.shield(self.mt_work)
                    if not self.cancelled.is_set() and translated:
                        await self.emit(Caption(segment.start_sample, segment.end_sample, translated, segment.segment_id))
                    continue
                if self._asr_queue:
                    item = self._asr_queue.popleft()
                    self._queued_audio_s = max(0.0, self._queued_audio_s - len(item.samples) / RATE)
                    span = {"segment_id": item.segment_id, "start_sample": item.start_sample, "end_sample": item.end_sample}
                    if self.record:
                        self.record("asr_start", **span)
                    self.asr_work = loop.run_in_executor(
                        self.backend.mt_pool,
                        lambda s=item.samples: _call_with_cancel(
                            self.backend.recognizer.decode, s, cancel_event=self.cancelled
                        ),
                    )
                    text = await asyncio.shield(self.asr_work)
                    if self.cancelled.is_set():
                        break
                    if item.has_overlap and self._last_ja and text:
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
        released = await loop.run_in_executor(vad_pool, self.segmenter.push, frame)
        for start, samples, has_overlap in released:
            segment_id = next(self.segment_ids)
            end = start + len(samples)
            if self.record:
                self.record("vad_released", segment_id=segment_id, start_sample=start, end_sample=end)
            item = _PendingAudio(segment_id, start, end, samples, has_overlap)
            if self.backend.uses_mlx_asr:
                dur = len(samples) / RATE
                if self._queued_audio_s + dur > self.MAX_QUEUED_AUDIO_S:
                    raise BackendError("overloaded")
                self._asr_queue.append(item)
                self._queued_audio_s += dur
                self._work_ready.set()
            else:
                try:
                    self._cpp_asr_queue.put_nowait(item)
                except asyncio.QueueFull:
                    raise BackendError("overloaded") from None

    async def finish(self):
        await self.accept_pcm(None)
        if self.backend.uses_mlx_asr:
            self._input_done = True
            self._work_ready.set()
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
        if self.backend.uses_mlx_asr:
            self._work_ready.set()
        self.translation.close()
        for task in (self._cpp_asr_task, self.mt_task):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        for work in (self.asr_work, self.mt_work):
            if work is not None:
                with suppress(Exception):
                    await asyncio.shield(work)
