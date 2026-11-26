"""Model registry enforcement and Stage 8 MT / ASR experiment adapters."""

from __future__ import annotations

import ctypes
import gc
import importlib
import json
from pathlib import Path
import re
import time
from typing import Any, Protocol

import numpy as np

from p0.stage8_eval.contracts import sha256_file
from subtitle_service.app import chinese as gateway_chinese
from subtitle_service.audio_span_contract import (
    RATE,
    nemo_frame_to_sample,
    owned_tokens,
    right_collar_delay_ms,
    validate_span,
)
from subtitle_service.config import AsrConfig
from subtitle_service.local_pipeline import (
    NeMoAneBridge,
    Qwen3AsrRecognizer,
    Qwen3Translator,
    SpeechCppNeMoRecognizer,
    _NemoAneTimings,
    _NemoAneToken,
    _collapse_repetition,
)
from subtitle_service.mt_cache_contract import canonical_messages, canonical_prompt_ids
from subtitle_service.quality_types import ASRHypothesis, AudioSpan, TokenSpan, TranslationResult


FORBIDDEN_REVISIONS = frozenset({"", "main", "master", "HEAD"})
_KANA_RE = re.compile(r"[\u3041-\u309f\u30a1-\u30ff\u31f0-\u31ff\uff66-\uff9f]")


def _resolve_local_path(raw: str | Path) -> Path:
    p = Path(str(raw))
    if p.exists() or p.is_absolute():
        return p
    for base in (
        Path(__file__).resolve().parents[2],
        Path("/Users/luo/Documents/github/jp-live-video-tanslate"),
    ):
        cand = base / p
        if cand.exists():
            return cand
    return p


def resolve_model(model_key: str, registry: dict[str, Any]) -> dict[str, Any]:
    """Validate approved local model entry without implicit network access."""
    models = registry.get("models") or {}
    entry = models.get(model_key)
    if not isinstance(entry, dict):
        raise RuntimeError(f"BLOCKED: model_key {model_key!r} missing from approved registry")
    if entry.get("trust_remote_code") is not False:
        raise RuntimeError(f"BLOCKED: trust_remote_code must be false for {model_key!r}")
    if entry.get("license_verified") is not True:
        raise RuntimeError(f"BLOCKED: license not verified for {model_key!r}")
    rev = str(entry.get("revision", ""))
    if rev in FORBIDDEN_REVISIONS:
        raise RuntimeError(f"BLOCKED: unpinned revision {rev!r} for {model_key!r}")
    artifacts = entry.get("artifacts") or []
    if not artifacts:
        raise RuntimeError(f"BLOCKED: no artifacts listed for {model_key!r}")
    resolved_artifacts: list[dict[str, Any]] = []
    for art in artifacts:
        p = _resolve_local_path(str(art.get("path", "")))
        if not p.exists():
            raise RuntimeError(f"BLOCKED: local artifact missing for {model_key!r}: {p}")
        expected_sha = str(art.get("sha256", "") or "")
        if expected_sha and p.is_file():
            actual = sha256_file(p)
            if actual != expected_sha:
                raise RuntimeError(
                    f"BLOCKED: artifact hash mismatch for {model_key!r}: {p} ({actual} != {expected_sha})"
                )
        resolved_artifacts.append({**art, "path": str(p)})
    resolved = dict(entry)
    resolved["artifacts"] = resolved_artifacts
    for dir_key in ("snapshot_dir", "processor_dir"):
        if resolved.get(dir_key):
            resolved[dir_key] = str(_resolve_local_path(str(resolved[dir_key])))
    return resolved


class MTAdapter(Protocol):
    def translate_turn(
        self,
        text_ja: str,
        *,
        history: list[tuple[str, str]],
        start_sample: int | None = None,
        end_sample: int | None = None,
        max_tokens: int | None = None,
    ) -> TranslationResult: ...


class ASRAdapter(Protocol):
    def transcribe(
        self,
        pcm_f32: np.ndarray,
        span: AudioSpan,
        options: dict[str, Any] | None = None,
    ) -> ASRHypothesis: ...


class Qwen3MlxMTAdapter:
    """Wrapper over Qwen3Translator exposing controlled M1/M2 ablation knobs and device sync timing."""

    def __init__(
        self,
        snapshot_dir: Path,
        *,
        model_key: str = "qwen4b-q4",
        system_prompt: str | None = None,
        context_turns: int = 4,
        repetition_penalty: float = 1.08,
        enable_kana_mask: bool = True,
        use_kv_cache: bool = True,
        max_tokens: int = 256,
    ) -> None:
        from mlx_lm.models.cache import make_prompt_cache
        from mlx_lm.sample_utils import make_logits_processors

        self.model_key = model_key
        self.snapshot_dir = Path(snapshot_dir)
        self.context_turns = context_turns
        self.repetition_penalty = float(repetition_penalty)
        self.enable_kana_mask = bool(enable_kana_mask)
        self.use_kv_cache = bool(use_kv_cache)
        self.default_max_tokens = int(max_tokens)

        self.translator = Qwen3Translator(self.snapshot_dir, context_turns=context_turns)
        tr = self.translator
        self._mx = tr._mx
        self._make_prompt_cache = make_prompt_cache

        if system_prompt is not None and system_prompt != tr.SYSTEM_PROMPT:
            tr.SYSTEM_PROMPT = system_prompt
            sentinel = "<<<SPLIT_HERE>>>"
            full_template = tr.tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": tr.SYSTEM_PROMPT},
                    {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{sentinel}"},
                ],
                add_generation_prompt=True,
                tokenize=False,
            )
            prefix_str, tr._suffix_tail = full_template.split(sentinel, 1)
            tr._prefix_ids = tr.tokenizer.encode(prefix_str, add_special_tokens=False)
            tr._prefix_len = len(tr._prefix_ids)
            tr._prompt_cache = make_prompt_cache(tr.model)
            tr._cache_tokens = list(tr._prefix_ids)
            for _ in tr._generate_step(
                self._mx.array(tr._prefix_ids, dtype=self._mx.int32),
                tr.model,
                max_tokens=0,
                prompt_cache=tr._prompt_cache,
            ):
                pass
            self._mx.synchronize()

        processors = []
        if self.enable_kana_mask:
            processors.append(tr._make_kana_mask_processor())
        if abs(self.repetition_penalty - 1.0) > 1e-6:
            processors.extend(
                make_logits_processors(
                    repetition_penalty=self.repetition_penalty,
                    repetition_context_size=32,
                )
            )
        tr.logits_processors = processors

        self._mx.reset_peak_memory()
        # Synchronized warmup forward
        _ = self.translate_turn("こんにちは", history=[])
        self.translator._reset_cache()

    def _run_fresh_canonical(self, canonical_ids: list[int], max_tokens: int) -> tuple[str, str, int]:
        tr = self.translator
        mx = self._mx
        fresh_cache = self._make_prompt_cache(tr.model)
        tokens: list[int] = []
        stop_reason = "length_limit"
        for tok, _ in tr._generate_step(
            mx.array(canonical_ids, dtype=mx.int32),
            tr.model,
            max_tokens=max_tokens,
            sampler=tr.sampler,
            logits_processors=tr.logits_processors,
            prompt_cache=fresh_cache,
            prefill_step_size=512,
        ):
            tid = int(tok)
            if tid in tr._eos_ids:
                stop_reason = "eos"
                break
            tokens.append(tid)
        out = tr._clean_output(tr.tokenizer.decode(tokens))
        return out, stop_reason, len(tokens)

    def translate_turn(
        self,
        text_ja: str,
        *,
        history: list[tuple[str, str]],
        start_sample: int | None = None,
        end_sample: int | None = None,
        max_tokens: int | None = None,
    ) -> TranslationResult:
        tr = self.translator
        mx = self._mx
        mtok = int(max_tokens or self.default_max_tokens)
        used_hist = list(history[-self.context_turns :]) if self.context_turns > 0 else []
        canonical_ids = canonical_prompt_ids(
            tr.tokenizer,
            canonical_messages(tr.SYSTEM_PROMPT, used_hist, text_ja),
        )

        mx.synchronize()
        t0 = time.perf_counter()
        if self.use_kv_cache:
            out = tr._run_cached_prompt(
                "",
                keep_kv=True,
                canonical_ids=canonical_ids,
                max_tokens=mtok,
            )
            meta = dict(getattr(tr, "last_translate_meta", {}) or {})
            stop_reason = str(meta.get("stop_reason", "error"))
            reused = int(meta.get("reused_tokens", 0))
            fed = int(meta.get("fed_tokens", len(canonical_ids)))
            gen_toks = int(meta.get("gen_tokens", 0))
        else:
            out, stop_reason, gen_toks = self._run_fresh_canonical(canonical_ids, mtok)
            reused = 0
            fed = len(canonical_ids)
        mx.synchronize()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        kana_leak = bool(_KANA_RE.search(out)) if out else False
        gw_valid = gateway_chinese(out) is not None if out else False
        valid = (stop_reason == "eos") and gw_valid and not kana_leak
        metrics = {
            "latency_ms": round(elapsed_ms, 2),
            "prompt_tokens": len(canonical_ids),
            "reused_tokens": reused,
            "fed_tokens": fed,
            "gen_tokens": gen_toks,
            "kana_leak": kana_leak,
            "gateway_valid": gw_valid,
            "peak_mlx_mib": round(mx.get_peak_memory() / (1024 * 1024), 2),
        }
        return TranslationResult(text_zh=out, stop_reason=stop_reason, metrics=metrics, valid=valid)

    def close(self) -> None:
        mx = self._mx
        if self.translator is not None:
            self.translator._prompt_cache = None
            self.translator.model = None
            self.translator.tokenizer = None
            self.translator = None
        gc.collect()
        mx.clear_cache()


class NeMoAneASRAdapter:
    """Adapter for CoreML ANE FastConformer + Accelerate RNN-T with Sample-Axis Token Ownership."""

    def __init__(
        self,
        ane_dir: Path,
        gguf_path: Path,
        *,
        decoding: str = "hybrid",
        pad_ms: int = 500,
        model_key: str = "nemo-baseline",
    ) -> None:
        self.model_key = model_key
        self.decoding = decoding
        self.pad_ms = int(pad_ms)
        self.pad_samples = int(round(self.pad_ms * RATE / 1000.0))
        self.bridge = NeMoAneBridge(Path(ane_dir), Path(gguf_path))
        self._use_beam = 0 if decoding == "greedy" else (2 if decoding == "hybrid" else 1)
        # Warmup once on 1s silence
        _ = self.bridge.transcribe(
            np.zeros(RATE, dtype=np.float32),
            pad_samples=self.pad_samples,
            use_beam=self._use_beam,
        )

    def transcribe(
        self,
        pcm_f32: np.ndarray,
        span: AudioSpan,
        options: dict[str, Any] | None = None,
    ) -> ASRHypothesis:
        validate_span(span)
        opts = options or {}
        decoding = str(opts.get("decoding", self.decoding))
        pad_ms = int(opts.get("pad_ms", self.pad_ms))
        pad_samples = int(round(pad_ms * RATE / 1000.0))
        use_beam = 0 if decoding == "greedy" else (2 if decoding == "hybrid" else 1)
        committed_until = int(opts.get("committed_until", span.target_start))

        audio = np.ascontiguousarray(pcm_f32, dtype=np.float32)
        out_buf = ctypes.create_string_buffer(8192)
        max_toks = 1024
        toks = (_NemoAneToken * max_toks)()
        n_toks = ctypes.c_int32(0)

        t0 = time.perf_counter()
        rc = self.bridge._lib.nemo_ane_transcribe(
            self.bridge._handle,
            audio.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            int(audio.size),
            int(pad_samples),
            int(use_beam),
            self.bridge._CANCEL_CB(),
            None,
            out_buf,
            8192,
            toks,
            max_toks,
            ctypes.byref(n_toks),
            None,
            0,
        )
        wall_ms = (time.perf_counter() - t0) * 1000.0

        flags: list[str] = []
        if rc != 0:
            flags.append(f"ane_error_{rc}")
            return ASRHypothesis(
                span=span,
                text_ja="",
                model_key=self.model_key,
                variant=f"{decoding}-pad{pad_ms}",
                tokens=[],
                timing={"total_ms": round(wall_ms, 2)},
                flags=flags,
            )

        timing = {"total_ms": round(wall_ms, 2)}
        if self.bridge._has_timings:
            t_struct = _NemoAneTimings()
            if self.bridge._lib.nemo_ane_last_timings(self.bridge._handle, ctypes.byref(t_struct)) == 0:
                timing.update({
                    "frontend_ms": round(float(t_struct.frontend_ms), 2),
                    "predict_ms": round(float(t_struct.predict_ms), 2),
                    "output_copy_ms": round(float(t_struct.output_copy_ms), 2),
                    "decode_ms": round(float(t_struct.decode_ms), 2),
                    "native_total_ms": round(float(t_struct.total_ms), 2),
                    "bucket_mel": int(t_struct.bucket_mel),
                    "mel_frames": int(t_struct.mel_frames),
                    "enc_frames": int(t_struct.enc_frames),
                })

        cnt = min(int(n_toks.value), max_toks)
        token_spans: list[TokenSpan] = []
        for i in range(cnt):
            tid = int(toks[i].token_id)
            fr = int(toks[i].frame)
            s_sample = max(0, nemo_frame_to_sample(span.input_start, fr, pad_samples))
            tok_txt = ""
            if tid >= 0:
                arr1 = (ctypes.c_int32 * 1)(tid)
                db1 = ctypes.create_string_buffer(256)
                if self.bridge._lib.nemo_ane_detokenize(self.bridge._handle, arr1, 1, db1, 256) == 0:
                    tok_txt = db1.value.decode("utf-8", "replace")
            token_spans.append(TokenSpan(tid, tok_txt, s_sample, s_sample + 1280, "emission_approx"))

        needs_filter = (
            span.input_start < span.target_start
            or span.input_end > span.target_end
            or committed_until > span.target_start
        )
        if needs_filter:
            kept_spans = owned_tokens(token_spans, span, committed_until=committed_until)
            kept_ids = [int(t.token_id) for t in kept_spans if t.token_id is not None]
            if not kept_ids:
                text = ""
            else:
                arr = (ctypes.c_int32 * len(kept_ids))(*kept_ids)
                detok_buf = ctypes.create_string_buffer(8192)
                if self.bridge._lib.nemo_ane_detokenize(self.bridge._handle, arr, len(kept_ids), detok_buf, 8192) == 0:
                    text = detok_buf.value.decode("utf-8", "replace").strip()
                else:
                    text = out_buf.value.decode("utf-8", "replace").strip()
            final_tokens = kept_spans
        else:
            text = out_buf.value.decode("utf-8", "replace").strip()
            final_tokens = token_spans

        if text in ("ピッ!", "ピッ", "ピッ。"):
            text = ""
            flags.append("beep_filtered")
        else:
            text = _collapse_repetition(text)
        return ASRHypothesis(
            span=span,
            text_ja=text,
            model_key=self.model_key,
            variant=f"{decoding}-pad{pad_ms}",
            tokens=final_tokens,
            timing=timing,
            flags=flags,
        )

    def close(self) -> None:
        if getattr(self, "bridge", None) is not None:
            self.bridge.close()
            self.bridge = None


class Qwen3AsrMlxAdapter:
    """Adapter for existing repo Qwen3AsrRecognizer (whole-target span, tokens=None)."""

    def __init__(self, snapshot_dir: Path, vad_path: Path, *, model_key: str = "qwen3-asr-1.7b-8bit") -> None:
        self.model_key = model_key
        cfg = AsrConfig(type="qwen3_asr_mlx", model_revision="a" * 40, threads=2, vad_pause_ms=350)
        self.recognizer = Qwen3AsrRecognizer(Path(snapshot_dir), Path(vad_path), cfg)
        self._mx = self.recognizer._mx
        self._mx.reset_peak_memory()

    def transcribe(
        self,
        pcm_f32: np.ndarray,
        span: AudioSpan,
        options: dict[str, Any] | None = None,
    ) -> ASRHypothesis:
        validate_span(span)
        if span.input_start != span.target_start or span.input_end != span.target_end:
            raise ValueError(
                "Qwen3AsrMlxAdapter has no word timestamps; only exact whole-target span is allowed"
            )
        mx = self._mx
        mx.synchronize()
        t0 = time.perf_counter()
        text = self.recognizer.decode(np.ascontiguousarray(pcm_f32, dtype=np.float32))
        mx.synchronize()
        wall_ms = (time.perf_counter() - t0) * 1000.0
        return ASRHypothesis(
            span=span,
            text_ja=text,
            model_key=self.model_key,
            variant="target_only_greedy",
            tokens=None,
            timing={
                "total_ms": round(wall_ms, 2),
                "peak_mlx_mib": round(mx.get_peak_memory() / (1024 * 1024), 2),
            },
            flags=[],
        )

    def close(self) -> None:
        mx = self._mx
        self.recognizer.model = None
        self.recognizer = None
        gc.collect()
        mx.clear_cache()


class WhisperMlxAdapter:
    """Adapter for MLX Whisper models (e.g., whisper-large-v3-turbo) locked to Japanese transcribe."""

    def __init__(
        self,
        snapshot_dir: Path,
        *,
        model_key: str = "whisper-large-v3-turbo",
        processor_dir: Path | None = None,
    ) -> None:
        import mlx.core as mx
        from mlx_audio.stt import load

        self.model_key = model_key
        self._mx = mx
        loaded = load(str(snapshot_dir))
        self.model = getattr(loaded, "_model", loaded)
        if getattr(self.model, "_processor", None) is None and processor_dir is not None:
            from transformers import WhisperProcessor

            self.model._processor = WhisperProcessor.from_pretrained(
                str(processor_dir), local_files_only=True
            )
        # Warmup on 1s silence with explicit language="ja", task="transcribe"
        _ = self.model.generate(
            np.zeros(RATE, dtype=np.float32),
            language="ja",
            task="transcribe",
            temperature=0.0,
            return_timestamps=False,
            verbose=False,
        )
        mx.synchronize()
        mx.reset_peak_memory()

    def transcribe(
        self,
        pcm_f32: np.ndarray,
        span: AudioSpan,
        options: dict[str, Any] | None = None,
    ) -> ASRHypothesis:
        validate_span(span)
        if span.input_start != span.target_start or span.input_end != span.target_end:
            raise ValueError(
                "WhisperMlxAdapter whole-target screening rejects cross-boundary trim without verified timestamps"
            )
        mx = self._mx
        mx.synchronize()
        t0 = time.perf_counter()
        res = self.model.generate(
            np.ascontiguousarray(pcm_f32, dtype=np.float32),
            language="ja",
            task="transcribe",
            temperature=0.0,
            return_timestamps=False,
            condition_on_previous_text=False,
            verbose=False,
        )
        mx.synchronize()
        wall_ms = (time.perf_counter() - t0) * 1000.0
        raw_txt = getattr(res, "text", None)
        if raw_txt is None:
            raw_txt = res.get("text", "") if isinstance(res, dict) else str(res)
        text = _collapse_repetition(str(raw_txt).strip())
        return ASRHypothesis(
            span=span,
            text_ja=text,
            model_key=self.model_key,
            variant="ja_transcribe_temp0",
            tokens=None,
            timing={
                "total_ms": round(wall_ms, 2),
                "peak_mlx_mib": round(mx.get_peak_memory() / (1024 * 1024), 2),
            },
            flags=[],
        )

    def close(self) -> None:
        mx = self._mx
        self.model = None
        gc.collect()
        mx.clear_cache()


def load_mt_adapter(model_key: str, manifest: dict[str, Any], registry: dict[str, Any] | None = None) -> MTAdapter:
    """Resolve and load an approved local MT adapter from registry + manifest."""
    if registry is not None:
        entry = resolve_model(model_key, registry)
        snap = Path(entry["artifacts"][0]["path"]).parent
        if Path(entry["artifacts"][0]["path"]).is_dir():
            snap = Path(entry["artifacts"][0]["path"])
    else:
        matched = [m for m in (manifest.get("models") or []) if m.get("key") == model_key]
        if not matched:
            raise RuntimeError(f"BLOCKED: model_key {model_key!r} missing from manifest")
        art0 = Path(matched[0]["artifacts"][0]["path"])
        snap = art0 if art0.is_dir() else art0.parent
    params = (manifest.get("arm") or {}).get("parameters") or {}
    return Qwen3MlxMTAdapter(
        snap,
        model_key=model_key,
        context_turns=int(params.get("context_turns", 4)),
        repetition_penalty=float(params.get("repetition_penalty", 1.08)),
        enable_kana_mask=bool(params.get("enable_kana_mask", True)),
        use_kv_cache=bool(params.get("use_kv_cache", True)),
        max_tokens=int(params.get("max_output_tokens", 256)),
    )
