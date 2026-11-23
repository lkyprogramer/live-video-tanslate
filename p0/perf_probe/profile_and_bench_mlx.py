"""Exact stage-level profiling and MLX low-level optimization microbenchmark (Step 1A).

Measures:
  Part 1: Baseline stage breakdown for Qwen3-ASR-1.7B-8bit & Qwen3-4B-Instruct-2507-4bit
  Part 2: ASR optimization ablations A0 -> A1 -> A2 -> A3 -> A4 (with token/text equivalence)
  Part 3: MT optimization ablations M0 -> M1 (KV cache) -> M2 (per-token cancel) -> M3 (speculative)
  Part 4: Combined interleaved ASR+MT run (A_best + M_best vs A0 + M0)
"""

from __future__ import annotations

import argparse
import copy
import gc
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import mlx.core as mx
import mlx.nn as nn
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import (  # noqa: E402
    QWEN3_ASR_DIR,
    QWEN3_MT_DIR,
    RATE,
    REPO_ROOT,
    load_frozen_slices,
    percentile,
    rss_mib,
    score_asr_rows,
)
from subtitle_service.local_pipeline import Qwen3Translator, _collapse_repetition  # noqa: E402

OUT_DIR = REPO_ROOT / "docs/experiments/perf-probe-2026-11-23"


def _sync_time() -> float:
    mx.synchronize()
    return time.perf_counter()


def _bucket_name(dur_s: float) -> str:
    if dur_s < 2.0:
        return "<2s"
    if dur_s < 4.0:
        return "2-4s"
    if dur_s < 8.0:
        return "4-8s"
    return ">8s"


def _max_tokens_for(audio: np.ndarray) -> int:
    return min(256, max(48, int(len(audio) / RATE * 18)))


def _gpu_stats() -> dict:
    return {
        "gpu_peak_mib": round(mx.get_peak_memory() / (1024 * 1024), 2),
        "gpu_active_mib": round(mx.get_active_memory() / (1024 * 1024), 2),
        "gpu_cache_mib": round(mx.get_cache_memory() / (1024 * 1024), 2),
        "rss_mib": rss_mib(),
    }


def _summarize_asr(name: str, rows: list[dict], d0_units: list[dict], d1_ref: str, base_texts: list[str] | None = None) -> dict:
    times = [r["decode_s"] for r in rows]
    total_audio = sum(r["duration_s"] for r in rows)
    cer = score_asr_rows(rows, d0_units, d1_ref)
    exact_matches = (
        sum(1 for r, b in zip(rows, base_texts) if r["text_ja"] == b)
        if base_texts is not None
        else len(rows)
    )
    return {
        "variant": name,
        "slices": len(rows),
        "audio_s": round(total_audio, 2),
        "total_s": round(sum(times), 4),
        "p50_s": percentile(times, 0.50),
        "p95_s": percentile(times, 0.95),
        "mean_s": round(sum(times) / len(times), 4),
        "rtf": round(sum(times) / max(1e-6, total_audio), 4),
        "exact_match_vs_a0": exact_matches,
        "d0_overall_cer": cer["d0_cer"]["overall_cer"],
        "d0_by_clip": {k: v["cer"] for k, v in cer["d0_cer"]["by_clip"].items()},
        "d1_cer": cer["d1_cer"]["cer"],
        **_gpu_stats(),
    }


# ---------------------------------------------------------------------------
# Pure MLX GPU Log-Mel matching transformers.WhisperFeatureExtractor
# ---------------------------------------------------------------------------

class MlxWhisperMel:
    """Exact GPU log-mel spectrogram matching Qwen3-ASR's WhisperFeatureExtractor."""

    def __init__(self, feature_extractor):
        self.n_fft = int(feature_extractor.n_fft)  # 400
        self.hop_length = int(feature_extractor.hop_length)  # 160
        self.pad = self.n_fft // 2  # 200
        # Periodic Hann window matching np.hanning(n_fft + 1)[:-1] / scipy hann(sym=False)
        n = np.arange(self.n_fft, dtype=np.float32)
        win = 0.5 * (1.0 - np.cos(2.0 * np.pi * n / self.n_fft))
        self.window = mx.array(win, dtype=mx.float32)
        # mel_filters: (n_fft // 2 + 1, n_mels) = (201, 128)
        self.mel_filters = mx.array(feature_extractor.mel_filters.astype(np.float32))
        mx.eval(self.window, self.mel_filters)

    def __call__(self, audio: np.ndarray) -> tuple[mx.array, mx.array, int]:
        x = mx.array(audio, dtype=mx.float32)
        n_samples = int(x.shape[0])
        # WhisperFeatureExtractor uses padding=True without max_length when padding single sample,
        # so reflect pad by n_fft // 2 on both sides (fallback to edge/zero if audio < 200 samples).
        if n_samples > self.pad:
            left = x[self.pad : 0 : -1]
            right = x[-2 : -2 - self.pad : -1]
            padded = mx.concatenate([left, x, right], axis=0)
        else:
            padded = mx.pad(x, (self.pad, self.pad))

        # Frame into (num_frames, n_fft) using strided view / slice gather
        num_frames = 1 + (int(padded.shape[0]) - self.n_fft) // self.hop_length
        # Vectorized index matrix: (num_frames, n_fft)
        idx = mx.arange(num_frames, dtype=mx.int32)[:, None] * self.hop_length + mx.arange(self.n_fft, dtype=mx.int32)[None, :]
        frames = padded[idx] * self.window[None, :]
        spec = mx.fft.rfft(frames, axis=-1)
        magnitudes = (spec.real * spec.real + spec.imag * spec.imag)[:-1, :]  # drop last frame like Whisper
        mel = magnitudes @ self.mel_filters  # (T, 128)
        log_spec = mx.log10(mx.maximum(mel, 1e-10))
        log_spec = mx.maximum(log_spec, mx.max(log_spec) - 8.0)
        log_spec = (log_spec + 4.0) / 4.0
        # Transpose to (1, 128, T)
        input_features = log_spec.T[None, :, :]
        t_len = int(input_features.shape[-1])
        mask = mx.ones((1, t_len), dtype=mx.int32)
        # _get_feat_extract_output_lengths(t_len)
        rem = t_len % 100
        feat_len = ((rem - 1) // 2) + 1
        out_len = ((((feat_len - 1) // 2) + 1 - 1) // 2) + 1 + (t_len // 100) * 13
        return input_features, mask, int(out_len)


# ---------------------------------------------------------------------------
# Fast Qwen3-ASR Runner supporting A1 / A2 / A3 / A4 + Stage Profiling
# ---------------------------------------------------------------------------

class FastQwen3AsrRunner:
    def __init__(self, model):
        self.model = model
        self.mel_gpu = MlxWhisperMel(model._feature_extractor)
        # Precompute fixed Japanese prompt prefix/suffix tokens & embeddings!
        # Prompt template:
        # <|im_start|>system\n<|im_end|>\n<|im_start|>user\n<|audio_start|>{pads}<|audio_end|><|im_end|>\n<|im_start|>assistant\nlanguage Japanese<asr_text>
        prefix_str = "<|im_start|>system\n<|im_end|>\n<|im_start|>user\n<|audio_start|>"
        suffix_str = "<|audio_end|><|im_end|>\n<|im_start|>assistant\nlanguage Japanese<asr_text>"
        self.prefix_ids = mx.array(model._tokenizer.encode(prefix_str, return_tensors="np")[0], dtype=mx.int32)
        self.suffix_ids = mx.array(model._tokenizer.encode(suffix_str, return_tensors="np")[0], dtype=mx.int32)
        self.prefix_emb = model.model.embed_tokens(self.prefix_ids[None, :])[0]  # (Lp, H)
        self.suffix_emb = model.model.embed_tokens(self.suffix_ids[None, :])[0]  # (Ls, H)
        self.audio_token_id = int(model.config.audio_token_id)
        self.eos_ids = model._eos_token_ids()
        mx.eval(self.prefix_ids, self.suffix_ids, self.prefix_emb, self.suffix_emb)

    def _make_logits_processors(self):
        from mlx_audio.lm.sample_utils import make_logits_processors
        return make_logits_processors(repetition_penalty=1.15, repetition_context_size=64)

    def _greedy_sampler(self):
        return lambda x: mx.argmax(x, axis=-1)

    def _encode_audio_clean(self, input_features: mx.array, mask: mx.array | None, use_clean_encoder: bool) -> mx.array:
        if not use_clean_encoder:
            return self.model.get_audio_features(input_features, mask)
        # Clean AudioEncoder without unused padded_mask_after_cnn & redundant syncs
        enc = self.model.audio_tower
        t_len = int(input_features.shape[-1])
        chunk_size = enc.n_window * 2  # 100
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
        attention_mask = enc._create_block_attention_mask(total_cnn_len, cu_seqlens, hidden_states.dtype)[None, None, :, :]
        hidden_states = hidden_states[None, :, :]
        for layer in enc.layers:
            hidden_states = layer(hidden_states, mask=attention_mask)
        hidden_states = enc.ln_post(hidden_states[0])
        hidden_states = nn.gelu(enc.proj1(hidden_states))
        return enc.proj2(hidden_states)

    def run_variant(self, audio: np.ndarray, *, variant: str, cancel_event: threading.Event | None = None) -> tuple[str, dict]:
        from mlx_audio.lm.generate import generate_step

        max_tokens = _max_tokens_for(audio)
        lps = self._make_logits_processors()
        sampler = self._greedy_sampler()

        t_start = _sync_time()
        # 1. Preprocess
        if variant == "A4":
            input_features, mask, num_audio_tokens = self.mel_gpu(audio)
        else:
            input_features, mask, num_audio_tokens = self.model._preprocess_audio(audio)
        t_prep = _sync_time()

        # 2. Encoder
        use_clean_enc = variant in ("A3", "A4")
        audio_features = self._encode_audio_clean(input_features, mask, use_clean_encoder=use_clean_enc)
        if variant == "A1":
            mx.eval(audio_features)
            del input_features, mask
            mx.clear_cache()
        t_enc = _sync_time()

        # 3. Embed merge
        if variant == "A1":
            input_ids = self.model._build_prompt(num_audio_tokens, "Japanese", None)
            inputs_embeds = self.model._build_inputs_embeds(input_ids, audio_features)
            mx.eval(inputs_embeds)
            del audio_features
            mx.clear_cache()
            input_embeddings = inputs_embeds[0]
            prompt = input_ids[0]
            del inputs_embeds
            mx.clear_cache()
        else:
            # O(1) zero-loop concatenation!
            af = audio_features.astype(self.prefix_emb.dtype)
            input_embeddings = mx.concatenate([self.prefix_emb, af, self.suffix_emb], axis=0)
            audio_pads = mx.full((af.shape[0],), self.audio_token_id, dtype=mx.int32)
            prompt = mx.concatenate([self.prefix_ids, audio_pads, self.suffix_ids], axis=0)
            if variant == "A2":
                mx.eval(input_embeddings)
                mx.clear_cache()
        t_embed = _sync_time()

        # 4. Prefill + decode via generate_step
        tokens: list[int] = []
        t_first = None
        cancelled = False
        for token, _ in generate_step(
            prompt=prompt,
            input_embeddings=input_embeddings,
            model=self.model,
            max_tokens=max_tokens,
            sampler=sampler,
            logits_processors=lps,
            prefill_step_size=2048,
        ):
            if t_first is None:
                t_first = _sync_time()
            tid = int(token)
            if tid in self.eos_ids:
                break
            tokens.append(tid)
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break

        t_end = _sync_time()
        text = self.model._tokenizer.decode(tokens, skip_special_tokens=True).strip()
        text = _collapse_repetition(text)
        timings = {
            "preprocess_ms": round((t_prep - t_start) * 1000, 3),
            "encoder_ms": round((t_enc - t_prep) * 1000, 3),
            "embed_ms": round((t_embed - t_enc) * 1000, 3),
            "ttft_ms": round(((t_first or t_end) - t_embed) * 1000, 3),
            "decode_rest_ms": round((t_end - (t_first or t_end)) * 1000, 3),
            "total_ms": round((t_end - t_start) * 1000, 3),
            "gen_tokens": len(tokens),
            "cancelled": cancelled,
        }
        return text, timings


def profile_baseline_asr_stages(model, slices: list[dict]) -> tuple[list[dict], list[dict]]:
    """Run exact instrumented clone of mlx-audio 0.6.7 _generate_single_chunk + stock model.generate."""
    from mlx_audio.lm.generate import generate_step
    from mlx_audio.lm.sample_utils import make_logits_processors

    # First run stock A0 model.generate for exact reference texts & wall time
    a0_rows = []
    mx.reset_peak_memory()
    for s in slices:
        audio = s["audio"]
        max_tok = _max_tokens_for(audio)
        t0 = _sync_time()
        raw = model.generate(
            audio,
            language="Japanese",
            temperature=0.0,
            max_tokens=max_tok,
            repetition_penalty=1.15,
            repetition_context_size=64,
        ).text.strip()
        t1 = _sync_time()
        text = _collapse_repetition(raw)
        a0_rows.append(
            {
                "clip": s["clip"],
                "segment_id": s["segment_id"],
                "start_sample": s["start_sample"],
                "end_sample": s["end_sample"],
                "duration_s": s["duration_s"],
                "text_ja": text,
                "decode_s": round(t1 - t0, 5),
            }
        )

    # Now run stage-instrumented clone of mlx-audio 0.6.7 exact operations
    stage_rows = []
    lps = make_logits_processors(repetition_penalty=1.15, repetition_context_size=64)
    sampler = lambda x: mx.argmax(x, axis=-1)
    eos_ids = model._eos_token_ids()

    for s in slices:
        audio = s["audio"]
        max_tok = _max_tokens_for(audio)

        t0 = _sync_time()
        input_features, feature_attention_mask, num_audio_tokens = model._preprocess_audio(audio)
        input_ids = model._build_prompt(num_audio_tokens, "Japanese", None)
        t1 = _sync_time()

        audio_features = model.get_audio_features(input_features, feature_attention_mask)
        mx.eval(audio_features)
        t2 = _sync_time()

        del input_features, feature_attention_mask
        mx.clear_cache()
        t3 = _sync_time()

        inputs_embeds = model._build_inputs_embeds(input_ids, audio_features)
        mx.eval(inputs_embeds)
        t4 = _sync_time()

        del audio_features
        mx.clear_cache()
        input_embeddings = inputs_embeds[0]
        prompt = input_ids[0]
        del inputs_embeds
        mx.clear_cache()
        t5 = _sync_time()

        tokens = []
        t_first = None
        t_dup = None
        for token, _ in generate_step(
            prompt=prompt,
            input_embeddings=input_embeddings,
            model=model,
            max_tokens=max_tok,
            sampler=sampler,
            logits_processors=lps,
            prefill_step_size=2048,
        ):
            if t_first is None:
                t_first = _sync_time()
                # Exact duplicate preprocess inside _generate_single_chunk
                _, _, n2 = model._preprocess_audio(audio)
                _ = model._build_prompt(n2, "Japanese", None)
                t_dup = _sync_time()
            tid = int(token)
            if tid in eos_ids:
                break
            tokens.append(tid)
        t_end = _sync_time()

        clear_ms = ((t3 - t2) + (t5 - t4)) * 1000
        dup_ms = ((t_dup - t_first) * 1000) if (t_first and t_dup) else 0.0
        ttft_ms = ((t_first - t5) * 1000) if t_first else ((t_end - t5) * 1000)
        dec_ms = ((t_end - (t_dup or t_end)) * 1000) if t_first else 0.0

        stage_rows.append(
            {
                "clip": s["clip"],
                "segment_id": s["segment_id"],
                "duration_s": s["duration_s"],
                "bucket": _bucket_name(s["duration_s"]),
                "gen_tokens": len(tokens),
                "preprocess_1_ms": round((t1 - t0) * 1000, 3),
                "audio_enc_ms": round((t2 - t1) * 1000, 3),
                "embed_merge_ms": round((t4 - t3) * 1000, 3),
                "clear_cache_ms": round(clear_ms, 3),
                "ttft_ms": round(ttft_ms, 3),
                "dup_preprocess_2_ms": round(dup_ms, 3),
                "decode_rest_ms": round(dec_ms, 3),
                "total_ms": round((t_end - t0) * 1000, 3),
            }
        )

    return a0_rows, stage_rows


def summarize_stage_table(rows: list[dict], keys: list[str]) -> dict:
    out = {}
    for k in keys:
        vals = [r[k] for r in rows]
        out[k] = {
            "p50": percentile(vals, 0.50),
            "p95": percentile(vals, 0.95),
            "mean": round(sum(vals) / max(1, len(vals)), 3),
            "sum": round(sum(vals), 2),
        }
    return out


# ---------------------------------------------------------------------------
# Fast Qwen3-MT Runner (M0 baseline vs M1 KV-Cache vs M2 cancel vs M3 draft)
# ---------------------------------------------------------------------------

class CachedQwen3Translator:
    """Qwen3-4B-Instruct-2507-4bit with persistent SYSTEM_PROMPT KV Cache."""

    def __init__(self, model, tokenizer):
        from mlx_lm.generate import generate_step
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache
        from mlx_lm.sample_utils import make_logits_processors, make_sampler

        self.model = model
        self.tokenizer = tokenizer
        self.generate_step = generate_step
        self.trim_prompt_cache = trim_prompt_cache
        self.sampler = make_sampler(temp=0.0)
        self.logits_processors = make_logits_processors(repetition_penalty=1.08, repetition_context_size=32)
        self.eos_ids = set(tokenizer.eos_token_ids)

        # Split at exact user-content prefix (ending with newline before Japanese text).
        sentinel = "<<<SPLIT_HERE>>>"
        full_template = tokenizer.apply_chat_template(
            [
                {"role": "system", "content": Qwen3Translator.SYSTEM_PROMPT},
                {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{sentinel}"},
            ],
            add_generation_prompt=True,
            tokenize=False,
        )
        self.prefix_str, self.suffix_tail = full_template.split(sentinel, 1)
        self.prefix_ids = tokenizer.encode(self.prefix_str, add_special_tokens=False)
        self.prefix_len = len(self.prefix_ids)

        # Prefill fixed prefix into KV cache once
        self.prompt_cache = make_prompt_cache(model)
        # Run generate_step with max_tokens=0 on prefix_ids to populate prompt_cache!
        for _ in self.generate_step(
            mx.array(self.prefix_ids, dtype=mx.int32),
            self.model,
            max_tokens=0,
            prompt_cache=self.prompt_cache,
        ):
            pass
        mx.synchronize()
        assert self.prompt_cache[0].offset == self.prefix_len

    def verify_token_split(self, texts_ja: list[str]) -> tuple[int, int]:
        """Verify prefix_ids + suffix_ids == full_ids across all utterances."""
        exact = 0
        for ja in texts_ja:
            full_str = self.tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": Qwen3Translator.SYSTEM_PROMPT},
                    {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{ja}"},
                ],
                add_generation_prompt=True,
                tokenize=False,
            )
            full_ids = self.tokenizer.encode(full_str, add_special_tokens=False)
            suffix_ids = self.tokenizer.encode(ja + self.suffix_tail, add_special_tokens=False)
            if self.prefix_ids + suffix_ids == full_ids:
                exact += 1
        return exact, len(texts_ja)

    def _run_cached_prompt(self, suffix_str: str, cancel_event: threading.Event | None = None) -> tuple[str, dict]:
        t0 = _sync_time()
        suffix_ids = mx.array(self.tokenizer.encode(suffix_str, add_special_tokens=False), dtype=mx.int32)
        t_tok = _sync_time()

        # Trim any previous utterance tail if needed
        cur_off = self.prompt_cache[0].offset
        if cur_off > self.prefix_len:
            self.trim_prompt_cache(self.prompt_cache, cur_off - self.prefix_len)

        tokens: list[int] = []
        t_first = None
        cancelled = False
        for tok, _ in self.generate_step(
            suffix_ids,
            self.model,
            max_tokens=256,
            sampler=self.sampler,
            logits_processors=self.logits_processors,
            prompt_cache=self.prompt_cache,
        ):
            if t_first is None:
                t_first = _sync_time()
            tid = int(tok)
            if tid in self.eos_ids:
                break
            tokens.append(tid)
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break

        t_end = _sync_time()
        # Restore cache back to prefix_len immediately
        used = self.prompt_cache[0].offset - self.prefix_len
        if used > 0:
            self.trim_prompt_cache(self.prompt_cache, used)

        raw = self.tokenizer.decode(tokens).strip()
        if "</think>" in raw:
            raw = raw.split("</think>", 1)[-1].strip()
        if len(raw) >= 2 and ((raw[0] == '"' and raw[-1] == '"') or (raw[0] == "“" and raw[-1] == "”")):
            raw = raw[1:-1].strip()

        return raw, {
            "tokenize_ms": round((t_tok - t0) * 1000, 3),
            "ttft_ms": round(((t_first or t_end) - t_tok) * 1000, 3),
            "decode_rest_ms": round((t_end - (t_first or t_end)) * 1000, 3),
            "total_ms": round((t_end - t0) * 1000, 3),
            "gen_tokens": len(tokens),
            "cancelled": cancelled,
        }

    def translate(self, text_ja: str, fallback_translator: Qwen3Translator | None = None, cancel_event: threading.Event | None = None) -> tuple[str, dict]:
        raw, info = self._run_cached_prompt(text_ja + self.suffix_tail, cancel_event=cancel_event)
        info["repaired"] = False
        if not info["cancelled"] and re.search(r"[\u3040-\u309f\u30a0-\u30ff]", raw) and fallback_translator is not None:
            repaired = fallback_translator._run_prompt(
                [{"role": "user", "content": Qwen3Translator.REPAIR_PROMPT + raw}]
            )
            if repaired:
                raw = repaired
                info["repaired"] = True
        return raw, info


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-speculative", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _, _, slices, d0_units, d1_ref_text, _ = load_frozen_slices()
    print(f"Loaded {len(slices)} frozen slices ({sum(s['duration_s'] for s in slices):.2f}s audio)", flush=True)

    # -----------------------------------------------------------------------
    # PART 1 & 2: ASR Profiling & Ablations (A0 -> A4)
    # -----------------------------------------------------------------------
    from mlx_audio.stt import load as load_asr

    mx.reset_peak_memory()
    t_load = time.perf_counter()
    asr_wrapper = load_asr(str(QWEN3_ASR_DIR))
    asr_model = getattr(asr_wrapper, "_model", asr_wrapper)
    asr_load_s = round(time.perf_counter() - t_load, 2)
    # Warmup
    _ = asr_model.generate(np.zeros(RATE // 2, dtype=np.float32), language="Japanese", temperature=0.0, max_tokens=32)
    mx.synchronize()

    print("=== Running A0 + Baseline Stage Breakdown ===", flush=True)
    a0_rows, stage_rows = profile_baseline_asr_stages(asr_model, slices)
    base_texts = [r["text_ja"] for r in a0_rows]
    a0_summary = _summarize_asr("A0_stock_mlx_audio", a0_rows, d0_units, d1_ref_text)

    asr_stage_keys = [
        "preprocess_1_ms",
        "audio_enc_ms",
        "embed_merge_ms",
        "clear_cache_ms",
        "ttft_ms",
        "dup_preprocess_2_ms",
        "decode_rest_ms",
        "total_ms",
    ]
    buckets = {}
    for b in ("<2s", "2-4s", "4-8s", ">8s"):
        sub = [r for r in stage_rows if r["bucket"] == b]
        if sub:
            buckets[b] = {"count": len(sub), **summarize_stage_table(sub, asr_stage_keys)}

    asr_stage_summary = {
        "model": "Qwen3-ASR-1.7B-8bit",
        "load_s": asr_load_s,
        "a0_stock_total_s": a0_summary["total_s"],
        "instrumented_total_s": round(sum(r["total_ms"] for r in stage_rows) / 1000, 4),
        "overall_ms": summarize_stage_table(stage_rows, asr_stage_keys),
        "by_duration_bucket_ms": buckets,
    }

    fast_asr = FastQwen3AsrRunner(asr_model)

    # Verify MlxWhisperMel numerical closeness vs WhisperFeatureExtractor
    mel_diffs = []
    for s in slices[:10]:
        f_ref, _, n_ref = asr_model._preprocess_audio(s["audio"])
        f_mlx, _, n_mlx = fast_asr.mel_gpu(s["audio"])
        mx.eval(f_ref, f_mlx)
        diff = float(mx.max(mx.abs(f_ref - f_mlx)).item())
        mel_diffs.append({"diff": diff, "n_ref": n_ref, "n_mlx": n_mlx})

    asr_ablations = [a0_summary]
    for variant, cache_limit in [("A1", None), ("A2", None), ("A3", 1024 * 1024 * 1024), ("A4", 1024 * 1024 * 1024)]:
        if cache_limit is not None:
            mx.set_cache_limit(cache_limit)
        mx.reset_peak_memory()
        # Warmup 1 short slice
        _ = fast_asr.run_variant(np.zeros(RATE // 2, dtype=np.float32), variant=variant)
        v_rows = []
        v_timings = []
        for s in slices:
            text, info = fast_asr.run_variant(s["audio"], variant=variant)
            v_rows.append(
                {
                    "clip": s["clip"],
                    "segment_id": s["segment_id"],
                    "start_sample": s["start_sample"],
                    "end_sample": s["end_sample"],
                    "duration_s": s["duration_s"],
                    "text_ja": text,
                    "decode_s": round(info["total_ms"] / 1000.0, 5),
                }
            )
            v_timings.append(info)
        summ = _summarize_asr(variant, v_rows, d0_units, d1_ref_text, base_texts=base_texts)
        summ["speedup_vs_a0_pct"] = round((1.0 - summ["total_s"] / a0_summary["total_s"]) * 100, 2)
        summ["stage_ms"] = summarize_stage_table(v_timings, ["preprocess_ms", "encoder_ms", "embed_ms", "ttft_ms", "decode_rest_ms", "total_ms"])
        asr_ablations.append(summ)
        print(json.dumps({variant: {"total_s": summ["total_s"], "p50_s": summ["p50_s"], "p95_s": summ["p95_s"], "speedup_pct": summ["speedup_vs_a0_pct"], "exact_match": summ["exact_match_vs_a0"], "d0_cer": summ["d0_overall_cer"], "d1_cer": summ["d1_cer"]}}), flush=True)

    # Cooperative per-token cancel latency test on ASR (D1 seg 3)
    d1_long = slices[-1]["audio"]
    asr_cancel_latencies = []
    for _ in range(5):
        ev = threading.Event()
        timer = threading.Timer(0.10, ev.set)
        t_set_holder = []

        def _trigger():
            t_set_holder.append(time.perf_counter())
            ev.set()

        timer = threading.Timer(0.10, _trigger)
        timer.start()
        _, info = fast_asr.run_variant(d1_long, variant="A3", cancel_event=ev)
        t_exit = time.perf_counter()
        timer.cancel()
        if t_set_holder and info["cancelled"]:
            asr_cancel_latencies.append(round((t_exit - t_set_holder[0]) * 1000, 2))

    # -----------------------------------------------------------------------
    # PART 3: MT Profiling & Ablations (M0 -> M1 -> M2 -> M3)
    # -----------------------------------------------------------------------
    from mlx_lm.generate import generate_step
    from mlx_lm.sample_utils import make_logits_processors, make_sampler

    print("=== Loading Qwen3-4B-Instruct-2507-4bit ===", flush=True)
    mt_translator = Qwen3Translator(QWEN3_MT_DIR)
    mt_model, mt_tok = mt_translator.model, mt_translator.tokenizer
    ja_inputs = [r["text_ja"] for r in a0_rows]

    # M0 Baseline + Stage Breakdown via generate_step
    mx.reset_peak_memory()
    m0_texts = []
    m0_times = []
    m0_stages = []
    repair_count = 0
    lps = make_logits_processors(repetition_penalty=1.08, repetition_context_size=32)
    sampler = make_sampler(temp=0.0)
    eos_ids = set(mt_tok.eos_token_ids)

    for ja in ja_inputs:
        t0 = _sync_time()
        prompt_str = mt_tok.apply_chat_template(
            [
                {"role": "system", "content": Qwen3Translator.SYSTEM_PROMPT},
                {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{ja}"},
            ],
            add_generation_prompt=True,
            tokenize=False,
        )
        prompt_ids = mx.array(mt_tok.encode(prompt_str, add_special_tokens=False), dtype=mx.int32)
        t_tok = _sync_time()
        toks = []
        t_first = None
        for tok, _ in generate_step(prompt_ids, mt_model, max_tokens=256, sampler=sampler, logits_processors=lps):
            if t_first is None:
                t_first = _sync_time()
            tid = int(tok)
            if tid in eos_ids:
                break
            toks.append(tid)
        t_dec = _sync_time()
        raw = mt_tok.decode(toks).strip()
        if "</think>" in raw:
            raw = raw.split("</think>", 1)[-1].strip()
        if len(raw) >= 2 and ((raw[0] == '"' and raw[-1] == '"') or (raw[0] == "“" and raw[-1] == "”")):
            raw = raw[1:-1].strip()
        repaired = False
        if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", raw):
            rep = mt_translator._run_prompt([{"role": "user", "content": Qwen3Translator.REPAIR_PROMPT + raw}])
            if rep:
                raw = rep
                repaired = True
                repair_count += 1
        t_end = _sync_time()
        m0_texts.append(raw)
        m0_times.append(t_end - t0)
        m0_stages.append(
            {
                "tokenize_ms": round((t_tok - t0) * 1000, 3),
                "ttft_ms": round(((t_first or t_dec) - t_tok) * 1000, 3),
                "decode_rest_ms": round((t_dec - (t_first or t_dec)) * 1000, 3),
                "repair_ms": round((t_end - t_dec) * 1000, 3) if repaired else 0.0,
                "total_ms": round((t_end - t0) * 1000, 3),
                "gen_tokens": len(toks),
            }
        )

    m0_summary = {
        "variant": "M0_uncached",
        "utterances": len(ja_inputs),
        "total_s": round(sum(m0_times), 4),
        "p50_s": percentile(m0_times, 0.50),
        "p95_s": percentile(m0_times, 0.95),
        "repair_count": repair_count,
        "stage_ms": summarize_stage_table(m0_stages, ["tokenize_ms", "ttft_ms", "decode_rest_ms", "total_ms"]),
        **_gpu_stats(),
    }

    # M1: Prompt KV Cache reuse (run TWO full passes to prove zero cache corruption!)
    cached_mt = CachedQwen3Translator(mt_model, mt_tok)
    split_exact, split_total = cached_mt.verify_token_split(ja_inputs)
    print(f"MT token split exact match: {split_exact}/{split_total}", flush=True)

    m1_passes = []
    for p_idx in (1, 2):
        mx.reset_peak_memory()
        p_texts = []
        p_times = []
        p_stages = []
        for ja in ja_inputs:
            zh, info = cached_mt.translate(ja, fallback_translator=mt_translator)
            p_texts.append(zh)
            p_times.append(info["total_ms"] / 1000.0)
            p_stages.append(info)
        exact_m0 = sum(1 for a, b in zip(p_texts, m0_texts) if a == b)
        m1_passes.append(
            {
                "pass": p_idx,
                "total_s": round(sum(p_times), 4),
                "p50_s": percentile(p_times, 0.50),
                "p95_s": percentile(p_times, 0.95),
                "speedup_vs_m0_pct": round((1.0 - sum(p_times) / sum(m0_times)) * 100, 2),
                "exact_match_vs_m0": exact_m0,
                "stage_ms": summarize_stage_table(p_stages, ["tokenize_ms", "ttft_ms", "decode_rest_ms", "total_ms"]),
                **_gpu_stats(),
            }
        )
        print(json.dumps({"M1_pass": p_idx, "total_s": m1_passes[-1]["total_s"], "p50_s": m1_passes[-1]["p50_s"], "speedup_pct": m1_passes[-1]["speedup_vs_m0_pct"], "exact_m0": exact_m0}), flush=True)

    # M2: Cooperative per-token cancel latency on MT (long D1 utterance)
    mt_cancel_latencies = []
    long_ja = ja_inputs[-1]
    for _ in range(5):
        ev = threading.Event()
        t_set_holder = []

        def _trigger_mt():
            t_set_holder.append(time.perf_counter())
            ev.set()

        timer = threading.Timer(0.10, _trigger_mt)
        timer.start()
        _, info = cached_mt.translate(long_ja, cancel_event=ev)
        t_exit = time.perf_counter()
        timer.cancel()
        if t_set_holder and info["cancelled"]:
            mt_cancel_latencies.append(round((t_exit - t_set_holder[0]) * 1000, 2))

    # M3: Speculative decoding probe (optional)
    m3_summary = {"skipped": True}
    if not args.skip_speculative:
        draft_cache = Path("/tmp/speech-cpp-probe/draft-cache")
        try:
            from huggingface_hub import snapshot_download
            from mlx_lm import load as load_lm
            from mlx_lm.generate import stream_generate

            draft_dir = snapshot_download("mlx-community/Qwen3-0.6B-4bit", cache_dir=str(draft_cache))
            draft_model, _ = load_lm(draft_dir)
            # Warmup
            for _ in stream_generate(
                mt_model, mt_tok, prompt=cached_mt.prefix_str + "こんにちは" + cached_mt.suffix_tail,
                max_tokens=16, draft_model=draft_model, sampler=sampler, logits_processors=lps,
            ):
                pass
            mx.reset_peak_memory()
            m3_times = []
            m3_texts = []
            from_draft_total = 0
            gen_total = 0
            for ja in ja_inputs:
                prompt_str = mt_tok.apply_chat_template(
                    [
                        {"role": "system", "content": Qwen3Translator.SYSTEM_PROMPT},
                        {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{ja}"},
                    ],
                    add_generation_prompt=True,
                    tokenize=False,
                )
                t0 = _sync_time()
                resp = None
                for resp in stream_generate(
                    mt_model, mt_tok, prompt=prompt_str, max_tokens=256,
                    draft_model=draft_model, sampler=sampler, logits_processors=lps,
                ):
                    if resp.from_draft:
                        from_draft_total += 1
                    gen_total += 1
                t1 = _sync_time()
                raw = resp.text.strip() if resp else ""
                if "</think>" in raw:
                    raw = raw.split("</think>", 1)[-1].strip()
                m3_texts.append(raw)
                m3_times.append(t1 - t0)
            m3_summary = {
                "skipped": False,
                "draft_model": "mlx-community/Qwen3-0.6B-4bit",
                "total_s": round(sum(m3_times), 4),
                "p50_s": percentile(m3_times, 0.50),
                "p95_s": percentile(m3_times, 0.95),
                "speedup_vs_m0_pct": round((1.0 - sum(m3_times) / sum(m0_times)) * 100, 2),
                "draft_accept_rate": round(from_draft_total / max(1, gen_total), 4),
                "exact_match_vs_m0": sum(1 for a, b in zip(m3_texts, m0_texts) if a == b),
                **_gpu_stats(),
            }
            del draft_model
            gc.collect()
            mx.clear_cache()
        except Exception as exc:
            m3_summary = {"skipped": False, "error": str(exc)}

    # -----------------------------------------------------------------------
    # PART 4: Combined Interleaved ASR + MT Run (A0+M0 vs A_best+M1)
    # -----------------------------------------------------------------------
    best_asr_variant = min(asr_ablations[1:], key=lambda x: x["total_s"] if x["exact_match_vs_a0"] >= 89 else 1e9)["variant"]
    print(f"=== Running Combined Interleaved ASR+MT: A0+M0 vs {best_asr_variant}+M1 ===", flush=True)

    mx.reset_peak_memory()
    t_comb_base = []
    for s in slices:
        t0 = _sync_time()
        ja = _collapse_repetition(
            asr_model.generate(
                s["audio"], language="Japanese", temperature=0.0,
                max_tokens=_max_tokens_for(s["audio"]), repetition_penalty=1.15, repetition_context_size=64,
            ).text.strip()
        )
        if ja:
            _ = mt_translator.translate(None, ja)
        t1 = _sync_time()
        t_comb_base.append(t1 - t0)
    comb_base_stats = {
        "total_s": round(sum(t_comb_base), 4),
        "p50_s": percentile(t_comb_base, 0.50),
        "p95_s": percentile(t_comb_base, 0.95),
        **_gpu_stats(),
    }

    mx.reset_peak_memory()
    t_comb_opt = []
    for s in slices:
        t0 = _sync_time()
        ja, _ = fast_asr.run_variant(s["audio"], variant=best_asr_variant)
        if ja:
            _, _ = cached_mt.translate(ja, fallback_translator=mt_translator)
        t1 = _sync_time()
        t_comb_opt.append(t1 - t0)
    comb_opt_stats = {
        "asr_variant": best_asr_variant,
        "mt_variant": "M1_kv_cache",
        "total_s": round(sum(t_comb_opt), 4),
        "p50_s": percentile(t_comb_opt, 0.50),
        "p95_s": percentile(t_comb_opt, 0.95),
        "speedup_pct": round((1.0 - sum(t_comb_opt) / sum(t_comb_base)) * 100, 2),
        **_gpu_stats(),
    }

    # Write JSON archives
    (OUT_DIR / "mlx_stage_profile.json").write_text(
        json.dumps(
            {
                "asr": asr_stage_summary,
                "mt": {
                    "model": "Qwen3-4B-Instruct-2507-4bit",
                    "m0_summary": m0_summary,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "mlx_asr_ablation.json").write_text(
        json.dumps(
            {
                "mel_diff_sample10": mel_diffs,
                "ablations": asr_ablations,
                "cancel_latency_ms": {
                    "samples": asr_cancel_latencies,
                    "p50": percentile(asr_cancel_latencies, 0.50),
                    "max": max(asr_cancel_latencies) if asr_cancel_latencies else None,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "mlx_mt_ablation.json").write_text(
        json.dumps(
            {
                "token_split_exact": f"{split_exact}/{split_total}",
                "m0_baseline": m0_summary,
                "m1_kv_cache_passes": m1_passes,
                "m2_cancel_latency_ms": {
                    "samples": mt_cancel_latencies,
                    "p50": percentile(mt_cancel_latencies, 0.50),
                    "max": max(mt_cancel_latencies) if mt_cancel_latencies else None,
                },
                "m3_speculative": m3_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "mlx_combined_summary.json").write_text(
        json.dumps({"baseline_a0_m0": comb_base_stats, "optimized": comb_opt_stats}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("DONE MLX BENCH:", json.dumps(comb_opt_stats, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
