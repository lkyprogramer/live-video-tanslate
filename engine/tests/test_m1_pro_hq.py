"""Unit tests for the m1-pro-hq MLX GPU profile and single-MLX-thread affinity."""

import asyncio
import hashlib
from pathlib import Path
import threading

import numpy as np
import pytest

from subtitle_service import local_pipeline as lp
from subtitle_service.config import load_settings


ROOT = Path(__file__).resolve().parents[2]
REV_A = "a" * 40
REV_B = "b" * 40


def _write_hq_config(tmp_path: Path, *, enabled: bool = True) -> Path:
    cfg = tmp_path / "profiles.toml"
    cfg.write_text(
        f"""
schema_version = 1
default_profile_id = "reazon-hy"
cache_root = "{tmp_path / 'cache'}"

[server]
port = 18765
allowed_origin = "chrome-extension://{'a' * 32}"

[profiles.reazon-hy]
label = "ReazonSpeech"
type = "local_pipeline"
enabled = true

[profiles.reazon-hy.asr]
type = "reazon_sherpa"
model_revision = "{REV_A}"
threads = 2
vad_pause_ms = 600

[profiles.reazon-hy.mt]
type = "hy_mt2_mlx"
model_revision = "{REV_B}"

[profiles.m1-pro-hq]
label = "Qwen3 · M1 Pro 高精度"
type = "local_pipeline"
enabled = {str(enabled).lower()}

[profiles.m1-pro-hq.asr]
type = "qwen3_asr_mlx"
model_revision = "{REV_A}"
threads = 2
vad_pause_ms = 600

[profiles.m1-pro-hq.mt]
type = "qwen3_mt_mlx"
model_revision = "{REV_B}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return cfg


def test_config_accepts_m1_pro_hq_and_example_parses(tmp_path):
    ex_copy = tmp_path / "profiles.example.toml"
    ex_copy.write_text(
        (ROOT / "engine/config/profiles.example.toml").read_text().replace(
            "chrome-extension://<extension-id>", "chrome-extension://" + "a" * 32,
        ),
        encoding="utf-8",
    )
    example = load_settings(ex_copy)
    assert "m1-pro-hq" in example.profiles
    assert example.profiles["m1-pro-hq"].enabled is False
    assert example.default_profile_id == "reazon-hy"

    settings = load_settings(_write_hq_config(tmp_path))
    hq = settings.profiles["m1-pro-hq"]
    assert hq.enabled is True
    assert hq.asr.type == "qwen3_asr_mlx"
    assert hq.mt.type == "qwen3_mt_mlx"


def test_local_backend_detects_qwen3_paths_and_missing(tmp_path):
    settings = load_settings(_write_hq_config(tmp_path))
    cache = Path(settings.cache_root)
    backend = lp.LocalBackend(cache, settings.profiles["m1-pro-hq"])
    assert backend.uses_mlx_asr is True
    assert len(backend.missing) == 7

    vad = cache / "reazonspeech-assets/silero_vad.onnx"
    vad.parent.mkdir(parents=True, exist_ok=True)
    vad.write_bytes(b"vad")
    for p in (
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/tokenizer_config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/tokenizer.json",
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"{}")

    ready_backend = lp.LocalBackend(cache, settings.profiles["m1-pro-hq"])
    assert ready_backend.missing == []


def test_mlx_asr_and_mt_share_single_persistent_thread(tmp_path, monkeypatch):
    settings = load_settings(_write_hq_config(tmp_path))
    cache = Path(settings.cache_root)
    vad = cache / "reazonspeech-assets/silero_vad.onnx"
    vad.parent.mkdir(parents=True, exist_ok=True)
    vad.write_bytes(b"vad")
    monkeypatch.setattr(lp, "VAD_SHA", hashlib.sha256(b"vad").hexdigest())

    for p in (
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/tokenizer_config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/tokenizer.json",
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"{}")

    mlx_threads = []
    vad_threads = []

    class FakeAsr:
        def __init__(self, snapshot, vad_path, cfg):
            mlx_threads.append(("asr_init", threading.get_ident()))

        def new_vad(self):
            vad_threads.append(("new_vad", threading.get_ident()))
            return object()

        def decode(self, samples):
            mlx_threads.append(("asr_decode", threading.get_ident()))
            return "こんにちは"

    class FakeMt:
        def __init__(self, snapshot):
            mlx_threads.append(("mt_init", threading.get_ident()))

        def new_session(self):
            return lp._NoopSession()

        @staticmethod
        def translate(session, text_ja):
            mlx_threads.append(("mt_translate", threading.get_ident()))
            return "你好。"

    def fake_vad_segments(vad_obj, frame):
        vad_threads.append(("vad_segments", threading.get_ident()))
        if frame is None:
            return
        yield 0, np.zeros(1600, dtype=np.float32)

    monkeypatch.setattr(lp, "Qwen3AsrRecognizer", FakeAsr)
    monkeypatch.setattr(lp, "Qwen3Translator", FakeMt)
    monkeypatch.setattr(lp, "vad_segments", fake_vad_segments)

    async def scenario():
        backend = lp.LocalBackend(cache, settings.profiles["m1-pro-hq"])
        emitted = []

        async def emit(item):
            emitted.append(item)

        session = await backend.open(emit)
        await session.accept_pcm(b"\x00\x00" * 1600)
        await session.finish()
        await session.aclose()
        await backend.aclose()
        return emitted

    emitted = asyncio.run(scenario())
    assert len(emitted) == 1
    assert emitted[0].text_zh == "你好。"
    mlx_tids = {tid for _, tid in mlx_threads}
    vad_tids = {tid for _, tid in vad_threads}
    assert [op for op, _ in mlx_threads] == ["asr_init", "mt_init", "asr_decode", "mt_translate"]
    assert len(mlx_tids) == 1
    assert len(vad_tids) == 1
    assert mlx_tids != vad_tids


def test_collapse_repetition_and_kana_repair():
    assert lp._collapse_repetition("いうのを、" + "このように、" * 20) == "いうのを、このように、"
    assert lp._collapse_repetition("今日はいい天気ですね。") == "今日はいい天気ですね。"

    class FakeTokenizer:
        def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
            return messages[-1]["content"]

    calls = []
    tr = lp.Qwen3Translator.__new__(lp.Qwen3Translator)
    tr.model = object()
    tr.tokenizer = FakeTokenizer()
    tr.sampler = object()
    tr.logits_processors = []

    def fake_gen_clean(model, tokenizer, prompt, **kwargs):
        calls.append(prompt)
        return "先简要回顾副语言信息。"

    tr._generate = fake_gen_clean
    assert tr.translate(lp._NoopSession(), "パラ言語情報") == "先简要回顾副语言信息。"
    assert len(calls) == 1

    calls.clear()
    responses = iter(["先に是什么", "首先是什么"])
    tr._generate = lambda model, tokenizer, prompt, **kwargs: (calls.append(prompt) or next(responses))
    assert tr.translate(lp._NoopSession(), "何であるか") == "首先是什么"
    assert len(calls) == 2


def test_smart_segmenter_and_dedup():
    class FakeSegment:
        def __init__(self, start, samples):
            self.start = start
            self.samples = samples

    class FakeVad:
        def __init__(self):
            self._speaking = True
            self._queue = []
            self._total = 0

        def accept_waveform(self, arr):
            self._total += len(arr)

        def is_speech_detected(self):
            return self._speaking

        def empty(self):
            return not self._queue

        @property
        def front(self):
            return self._queue[0]

        def pop(self):
            self._queue.pop(0)

        def flush(self):
            self._speaking = False
            self._queue.append(FakeSegment(0, np.ones(self._total, dtype=np.float32) * 0.2))

    vad = FakeVad()
    seg = lp._SmartSegmenter(vad, max_segment_s=4.5, overlap_ms=350)
    emitted = []
    # Build 6.0s of 200ms frames with an energy dip around 3.8s-4.0s
    rng = np.random.default_rng(7)
    for i in range(30):
        amp = 0.01 if i in (18, 19) else 0.25
        samples = (rng.standard_normal(3200).astype(np.float32) * amp).clip(-1.0, 1.0)
        frame = (samples * 32767.0).astype("<i2").tobytes()
        emitted.extend(seg.push(frame))
    emitted.extend(seg.push(None))

    assert len(emitted) >= 2
    assert emitted[0].dedup_before_sample == 0
    assert emitted[0].cut_reason == "energy_valley"
    assert emitted[1].dedup_before_sample > 0
    prev_end = 0
    for chunk in emitted:
        end = chunk.start_sample + len(chunk.samples)
        assert chunk.start_sample < end
        assert end > prev_end
        prev_end = end

    assert lp._dedup_boundary_ja("そういえば保育士さんって", "保育士さんって大変だよね。") == "大変だよね。"
    assert lp._dedup_boundary_ja("こんにちは", "今日はいい天気。") == "今日はいい天気。"


def test_smart_segmenter_audio_conservation_and_bounds():
    class DelayedVad:
        def __init__(self, speech_start: int, speech_end: int, confirm_after: int):
            self.speech_start = speech_start
            self.speech_end = speech_end
            self.confirm_after = confirm_after
            self._total = 0
            self._queue = []
            self._speaking = False

        def accept_waveform(self, arr):
            self._total += len(arr)
            if self._total >= self.confirm_after and self._total <= self.speech_end:
                self._speaking = True
            if self._total >= self.speech_end and self._speaking:
                self._speaking = False
                self._queue.append(type("Seg", (), {
                    "start": self.speech_start,
                    "samples": np.ones(self.speech_end - self.speech_start, dtype=np.float32) * 0.2,
                })())

        def is_speech_detected(self):
            return self._speaking

        def empty(self):
            return not self._queue

        @property
        def front(self):
            return self._queue[0]

        def pop(self):
            self._queue.pop(0)

        def flush(self):
            if self._speaking:
                self._speaking = False
                self._queue.append(type("Seg", (), {
                    "start": self.speech_start,
                    "samples": np.ones(self._total - self.speech_start, dtype=np.float32) * 0.2,
                })())

    def _check_conservation(speech_end: int, max_s: float):
        vad = DelayedVad(speech_start=0, speech_end=speech_end, confirm_after=3200)
        seg = lp._SmartSegmenter(vad, max_segment_s=max_s, overlap_ms=350)
        chunks = []
        for off in range(0, speech_end, 3200):
            n = min(3200, speech_end - off)
            frame = (np.ones(n, dtype=np.float32) * 0.2 * 32767.0).astype("<i2").tobytes()
            chunks.extend(seg.push(frame))
        chunks.extend(seg.push(None))

        covered = np.zeros(speech_end, dtype=bool)
        for c in chunks:
            assert 0 <= c.start_sample
            end = c.start_sample + len(c.samples)
            assert end <= speech_end
            covered[c.start_sample:end] = True
        assert covered.all(), f"Missing samples: {np.where(~covered)[0][:10]}"
        return chunks

    # 1. Delayed onset (first 3200 samples before VAD confirms) must be conserved in [0, 96000)
    c1 = _check_conservation(96000, 4.5)
    assert c1[0].start_sample == 0

    # 2. Short tail (<250ms past burst_cut) must be emitted rather than dropped
    c2 = _check_conservation(60480, 3.6)
    assert len(c2) >= 2 and c2[-1].cut_reason == "vad_tail"

    # 3. max_segment_s = 1.0s (<2.0s) must never overflow array bounds
    c3 = _check_conservation(48000, 1.0)
    assert len(c3) >= 2


def test_speech_cpp_token_dedup_and_error_check():
    # Preserves legitimate semantic repetition ("はいはい" followed by "はい、次に行きます")
    toks = [
        (0.40, 0.50, "は"),
        (0.50, 0.60, "い"),
        (0.65, 0.80, "、次"),
        (0.80, 1.00, "に行きます"),
    ]
    # Suppose overlap starts at sample 8000, dedup_before_sample is 8800 (+0.05s), pad_s = 0.35s
    # Midpoint of "は" (0.45s - 0.35s = 0.10s -> 8000 + 1600 = 9600 >= 8800), so "はい、次に行きます" is kept!
    kept = lp._filter_tokens_by_sample(toks, start_sample=8000, dedup_before_sample=8800, pad_s=0.35)
    assert kept == "はい、次に行きます"
    # If dedup_before_sample is 12800 (+0.30s), tokens before 0.65s are trimmed
    trimmed = lp._filter_tokens_by_sample(toks, start_sample=8000, dedup_before_sample=12800, pad_s=0.35)
    assert trimmed == "次に行きます"

    # Non-zero negative C API status must raise BackendError instead of returning ""
    rec = lp.SpeechCppNeMoRecognizer.__new__(lp.SpeechCppNeMoRecognizer)

    class FakeLib:
        def speech_status_name(self, st):
            return b"device"

        def speech_last_error(self):
            return b"mock failure"

    rec._lib = FakeLib()
    rec._SPEECH_OK = 0
    rec._SPEECH_CANCELLED = 1
    assert rec._check_status("ok", 0) == 0
    assert rec._check_status("cancel", 1) == 1
    with pytest.raises(lp.BackendError):
        rec._check_status("transcribe", -5)


def test_qwen3_kana_mask_and_cancellable_repair():
    import mlx.core as mx

    class FakeTok:
        def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
            return messages[-1]["content"]

        def encode(self, text, add_special_tokens=False):
            return [1, 2, 3]

        def decode(self, ids, skip_special_tokens=False):
            return "不会执行"

    tr = lp.Qwen3Translator.__new__(lp.Qwen3Translator)
    tr._mx = mx
    tr.tokenizer = FakeTok()
    tr.model = object()
    tr.sampler = object()
    tr._kana_token_ids = [2, 4]
    tr._kana_bias = None
    tr._eos_ids = {99}
    tr._prompt_cache = None

    proc = tr._make_kana_mask_processor()
    tr.logits_processors = [proc]
    logits = mx.zeros((1, 6), dtype=mx.float32)
    masked = np.array(proc([], logits))[0]
    assert masked[2] < -1e6 and masked[4] < -1e6
    assert masked[0] == 0.0 and masked[1] == 0.0 and masked[3] == 0.0

    step_calls = 0

    def fake_gen_step(*args, **kwargs):
        nonlocal step_calls
        step_calls += 1
        cb = kwargs.get("prompt_progress_callback")
        if cb:
            cb(1, 2)
        yield mx.array(10), None

    tr._generate_step = fake_gen_step
    ev = threading.Event()
    ev.set()
    assert tr._run_prompt([{"role": "user", "content": "hi"}], cancel_event=ev) == ""
    assert step_calls == 0

    ev2 = threading.Event()
    def fake_gen_step_mid(*args, **kwargs):
        cb = kwargs.get("prompt_progress_callback")
        ev2.set()
        if cb:
            cb(1, 2)
        yield mx.array(10), None
    tr._generate_step = fake_gen_step_mid
    assert tr._run_prompt([{"role": "user", "content": "hi"}], cancel_event=ev2) == ""


def test_qwen3_rolling_kv_and_streaming_delta_order(tmp_path, monkeypatch):
    import mlx.core as mx

    class FakeCacheSlot:
        def __init__(self, offset):
            self.offset = offset

    class FakeTok:
        def encode(self, text, add_special_tokens=False):
            return [10] * max(1, len(text))

        def decode(self, ids):
            return "你好世界"

    tr = lp.Qwen3Translator.__new__(lp.Qwen3Translator)
    tr._mx = mx
    tr.tokenizer = FakeTok()
    tr.model = object()
    tr.sampler = object()
    tr.logits_processors = []
    tr._eos_ids = {99}
    tr._prefix_len = 90
    tr._suffix_tail = "<TAIL>"
    tr._next_turn_prefix = "<NEXT>"
    tr._context_turns = 2
    tr._prompt_cache = [FakeCacheSlot(90)]
    tr._trim_prompt_cache = lambda cache, n: setattr(cache[0], "offset", cache[0].offset - n)

    seen_prompts = []
    def fake_step(suffix_ids, *args, **kwargs):
        seen_prompts.append(len(suffix_ids))
        tr._prompt_cache[0].offset += len(suffix_ids) + 4
        for tid in (1, 2, 3, 4, 99):
            yield mx.array(tid), None

    tr._generate_step = fake_step
    sess = tr.new_session()
    deltas = []
    out1 = tr.translate(sess, "A", start_sample=0, end_sample=16000, on_delta=deltas.append)
    assert out1 == "你好世界" and len(deltas) >= 1
    assert sess.turns == 1 and tr._prompt_cache[0].offset > 90

    # Turn 2 keeps KV from turn 1 and prepends _next_turn_prefix
    out2 = tr.translate(sess, "B", start_sample=16000, end_sample=32000)
    assert out2 == "你好世界" and sess.turns == 2
    assert seen_prompts[1] > seen_prompts[0]

    # Turn 3 hits max_turns=2 -> sliding-window replay retains 1 prior turn ("B") instead of hard reset!
    out3 = tr.translate(sess, "C", start_sample=32000, end_sample=48000)
    assert out3 == "你好世界" and sess.turns == 2
    assert seen_prompts[2] > seen_prompts[0]
    assert [x[0] for x in sess.history] == ["B", "C"]

    # >12.0s audio gap clears history and resets cache back to prefix_len
    _ = tr.translate(sess, "D", start_sample=48000 + 13 * lp.RATE, end_sample=48000 + 14 * lp.RATE)
    assert sess.turns == 1 and seen_prompts[3] == seen_prompts[0]
    assert [x[0] for x in sess.history] == ["D"]

    # session.close() resets cache strictly to prefix_len and clears history
    sess.close()
    assert tr._prompt_cache[0].offset == 90 and len(sess.history) == 0

    # Cancel and exception reset cache strictly to prefix_len
    ev = threading.Event()
    ev.set()
    assert tr.translate(sess, "E", cancel_event=ev) == ""
    assert tr._prompt_cache[0].offset == 90 and sess.turns == 0

    def boom_step(*args, **kwargs):
        tr._prompt_cache[0].offset += 10
        raise RuntimeError("boom")
    tr._generate_step = boom_step
    with pytest.raises(RuntimeError):
        tr.translate(sess, "F")
    assert tr._prompt_cache[0].offset == 90

    # LocalSession streaming delta strictly ordered before Caption
    settings = load_settings(_write_hq_config(tmp_path))
    cache = Path(settings.cache_root)
    vad = cache / "reazonspeech-assets/silero_vad.onnx"
    vad.parent.mkdir(parents=True, exist_ok=True)
    vad.write_bytes(b"vad")
    monkeypatch.setattr(lp, "VAD_SHA", hashlib.sha256(b"vad").hexdigest())
    for p in (
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/tokenizer_config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/tokenizer.json",
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"{}")

    class FakeAsr:
        def __init__(self, *a): pass
        def new_vad(self): return object()
        def decode(self, samples, *, cancel_event=None): return "こんにちは"

    class FakeMt:
        def __init__(self, *a): pass
        def new_session(self): return lp._NoopSession()
        @staticmethod
        def translate(session, text_ja, *, cancel_event=None, start_sample=None, end_sample=None, on_delta=None):
            if on_delta:
                on_delta("你好")
            return "你好。"

    monkeypatch.setattr(lp, "Qwen3AsrRecognizer", FakeAsr)
    monkeypatch.setattr(lp, "Qwen3Translator", FakeMt)
    called = 0
    def one_seg(vad_obj, frame):
        nonlocal called
        called += 1
        if frame is not None and called == 1:
            yield 0, np.zeros(1600, dtype=np.float32)
    monkeypatch.setattr(lp, "vad_segments", one_seg)

    async def run_delta_order():
        backend = lp.LocalBackend(cache, settings.profiles["m1-pro-hq"])
        emitted = []
        session = await backend.open(lambda item: emitted.append(item) or asyncio.sleep(0))
        await session.accept_pcm(b"\x00\x00" * 1600)
        await session.finish()
        await session.aclose()
        await backend.aclose()
        return emitted

    emitted = asyncio.run(run_delta_order())
    assert len(emitted) == 2
    assert isinstance(emitted[0], lp.CaptionDelta) and emitted[0].text_zh == "你好"
    assert isinstance(emitted[1], lp.Caption) and emitted[1].text_zh == "你好。"


def test_speech_cpp_gpu_staggered_priority_scheduler(tmp_path, monkeypatch):
    cfg = tmp_path / "profiles.toml"
    cfg.write_text(
        f"""
schema_version = 1
default_profile_id = "m1-pro-nemo"
cache_root = "{tmp_path / 'cache'}"

[server]
port = 18765
allowed_origin = "chrome-extension://{'a' * 32}"

[profiles.m1-pro-nemo]
label = "NeMo-v2 GPU Staggered"
type = "local_pipeline"
enabled = true

[profiles.m1-pro-nemo.asr]
type = "speech_cpp_nemo"
model_file = "reazonspeech-nemo-619M-v2-Q8_0.gguf"
threads = 6
decoding = "beam"
device = "gpu"

[profiles.m1-pro-nemo.mt]
type = "qwen3_mt_mlx"
model_revision = "{REV_B}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    cache = Path(settings.cache_root)
    vad = cache / "reazonspeech-assets/silero_vad.onnx"
    vad.parent.mkdir(parents=True, exist_ok=True)
    vad.write_bytes(b"vad")
    monkeypatch.setattr(lp, "VAD_SHA", hashlib.sha256(b"vad").hexdigest())
    for p in (
        cache / "speech-cpp/libspeech.dylib",
        cache / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/tokenizer.json",
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"{}")

    active = 0
    max_concurrent = 0
    events = []

    class FakeNeMoGpu:
        def __init__(self, *a):
            pass

        def new_vad(self):
            return object()

        def decode(self, samples, *, cancel_event=None, start_sample=0, dedup_before_sample=0):
            nonlocal active, max_concurrent
            active += 1
            max_concurrent = max(max_concurrent, active)
            events.append(("asr", threading.get_ident()))
            active -= 1
            return "こんにちは"

    class FakeMt:
        def __init__(self, *a, **kw):
            pass

        def new_session(self):
            return lp._NoopSession()

        @staticmethod
        def translate(session, text_ja, *, cancel_event=None, start_sample=None, end_sample=None, on_delta=None):
            nonlocal active, max_concurrent
            active += 1
            max_concurrent = max(max_concurrent, active)
            events.append(("mt", threading.get_ident()))
            active -= 1
            return "你好。"

    monkeypatch.setattr(lp, "SpeechCppNeMoRecognizer", FakeNeMoGpu)
    monkeypatch.setattr(lp, "Qwen3Translator", FakeMt)
    calls = 0
    def two_segs(vad_obj, frame):
        nonlocal calls
        calls += 1
        if frame is not None and calls <= 2:
            yield (calls - 1) * 1600, np.zeros(1600, dtype=np.float32)
    monkeypatch.setattr(lp, "vad_segments", two_segs)

    async def scenario():
        backend = lp.LocalBackend(cache, settings.profiles["m1-pro-nemo"])
        assert backend.uses_gpu_speech_cpp is True
        assert backend.uses_gpu_scheduler is True
        emitted = []
        session = await backend.open(lambda item: emitted.append(item) or asyncio.sleep(0))
        await session.accept_pcm(b"\x00\x00" * 1600)
        await session.accept_pcm(b"\x00\x00" * 1600)
        await session.finish()
        await session.aclose()
        await backend.aclose()
        return emitted

    emitted = asyncio.run(scenario())
    assert len(emitted) == 2
    assert max_concurrent == 1
    assert [op for op, _ in events] == ["asr", "mt", "asr", "mt"]
    # Isolated threads: asr_pool != mt_pool
    assert events[0][1] != events[1][1]


def test_stage4_draft_preview_clause_gate_kv_peek_and_preemption(tmp_path, monkeypatch):
    import mlx.core as mx

    # 1. _SmartSegmenter.active_preview_snapshot non-destructive audio conservation
    class SpeakVad:
        def __init__(self, speech_end: int):
            self._total = 0
            self._end = speech_end
            self._speaking = False
            self._q = []

        def accept_waveform(self, arr):
            self._total += len(arr)
            if self._total < self._end:
                self._speaking = True
            elif self._speaking:
                self._speaking = False
                self._q.append(type("Seg", (), {
                    "start": 0,
                    "samples": np.ones(self._end, dtype=np.float32) * 0.2,
                })())

        def is_speech_detected(self):
            return self._speaking

        def empty(self):
            return not self._q

        @property
        def front(self):
            return self._q[0]

        def pop(self):
            self._q.pop(0)

        def flush(self):
            if self._speaking:
                self._speaking = False
                self._q.append(type("Seg", (), {
                    "start": 0,
                    "samples": np.ones(self._total, dtype=np.float32) * 0.2,
                })())

    vad = SpeakVad(48000)
    seg = lp._SmartSegmenter(vad, max_segment_s=4.5, overlap_ms=350)
    snaps, finals = [], []
    for off in range(0, 48000, 1024):
        n = min(1024, 48000 - off)
        frame = (np.ones(n, dtype=np.float32) * 0.2 * 32767.0).astype("<i2").tobytes()
        rel, snap = seg.push_and_preview(frame, draft_interval_s=1.2)
        finals.extend(rel)
        if snap is not None:
            snaps.append(snap)
    rel, _ = seg.push_and_preview(None, draft_interval_s=1.2)
    finals.extend(rel)
    assert len(snaps) >= 1 and snaps[0].start_sample == 0 and snaps[0].end_sample >= int(1.6 * lp.RATE)
    assert len(finals) == 1 and finals[0].start_sample == 0 and len(finals[0].samples) == 48000

    # 2. _should_emit_draft_ja SOV clause gate
    assert lp._should_emit_draft_ja("", "これ") is False
    assert lp._should_emit_draft_ja("", "保育士さんって親は") is False
    assert lp._should_emit_draft_ja("", "今日は天気が") is False
    assert lp._should_emit_draft_ja("", "私、こんにちはって") is True
    assert lp._should_emit_draft_ja("私、こんにちはって", "明日も晴れると思うんだよ") is False
    assert lp._should_emit_draft_ja("私、こんにちはって", "私、こんにちはって言") is False
    assert lp._should_emit_draft_ja("私、こんにちはって", "私、こんにちはって言ってたんだよ。") is True

    # 3. Qwen3Translator.translate_draft non-mutating KV cache invariant
    class FakeSlot:
        def __init__(self, off):
            self.offset = off

    class FakeTok:
        def encode(self, text, add_special_tokens=False):
            return [10] * max(1, len(text))

        def decode(self, ids):
            return "草稿"

    tr = lp.Qwen3Translator.__new__(lp.Qwen3Translator)
    tr._mx = mx
    tr.tokenizer = FakeTok()
    tr.model = object()
    tr.sampler = object()
    tr.logits_processors = []
    tr._eos_ids = {99}
    tr._prefix_len = 90
    tr._suffix_tail = "<TAIL>"
    tr._next_turn_prefix = "<NEXT>"
    tr._prompt_cache = [FakeSlot(120)]
    tr._trim_prompt_cache = lambda cache, n: setattr(cache[0], "offset", cache[0].offset - n)

    def ok_step(suffix_ids, *a, **kw):
        tr._prompt_cache[0].offset += len(suffix_ids) + 2
        yield mx.array(1), None
        yield mx.array(99), None

    tr._generate_step = ok_step
    sess = lp._Qwen3Session(tr, max_turns=2)
    sess.turns = 1
    assert tr.translate_draft(sess, "こんにちはって言ってた") == "草稿"
    assert tr._prompt_cache[0].offset == 120 and sess.turns == 1

    ev = threading.Event()
    ev.set()
    assert tr.translate_draft(sess, "こんにちは", cancel_event=ev) == ""
    assert tr._prompt_cache[0].offset == 120 and sess.turns == 1

    def err_step(*a, **kw):
        tr._prompt_cache[0].offset += 7
        raise RuntimeError("boom")
    tr._generate_step = err_step
    with pytest.raises(RuntimeError):
        tr.translate_draft(sess, "こんにちは")
    assert tr._prompt_cache[0].offset == 120 and sess.turns == 1


def test_mlx_priority_scheduler_mt_over_asr_and_cancel(tmp_path, monkeypatch):
    settings = load_settings(_write_hq_config(tmp_path))
    cache = Path(settings.cache_root)
    vad = cache / "reazonspeech-assets/silero_vad.onnx"
    vad.parent.mkdir(parents=True, exist_ok=True)
    vad.write_bytes(b"vad")
    monkeypatch.setattr(lp, "VAD_SHA", hashlib.sha256(b"vad").hexdigest())

    for p in (
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-ASR-1.7B-8bit/snapshots/{REV_A}/tokenizer_config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/config.json",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/model.safetensors",
        cache / f"hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/{REV_B}/tokenizer.json",
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"{}")

    order = []

    class FakeAsr:
        def __init__(self, *a):
            pass

        def new_vad(self):
            return object()

        def decode(self, samples, *, cancel_event=None):
            order.append("asr")
            return "こんにちは"

    class FakeMt:
        def __init__(self, *a):
            pass

        def new_session(self):
            return lp._NoopSession()

        @staticmethod
        def translate(session, text_ja, *, cancel_event=None):
            order.append("mt")
            return "你好。"

    call_idx = 0

    def fake_vad_segments(vad_obj, frame):
        nonlocal call_idx
        call_idx += 1
        if frame is not None and call_idx <= 2:
            yield (call_idx - 1) * 1600, np.zeros(1600, dtype=np.float32)

    monkeypatch.setattr(lp, "Qwen3AsrRecognizer", FakeAsr)
    monkeypatch.setattr(lp, "Qwen3Translator", FakeMt)
    monkeypatch.setattr(lp, "vad_segments", fake_vad_segments)

    async def scenario():
        backend = lp.LocalBackend(cache, settings.profiles["m1-pro-hq"])
        emitted = []
        session = await backend.open(lambda item: emitted.append(item) or asyncio.sleep(0))
        await session.accept_pcm(b"\x00\x00" * 1600)
        await session.accept_pcm(b"\x00\x00" * 1600)
        await session.finish()
        assert session.cancelled.is_set() is False
        await session.aclose()
        assert session.cancelled.is_set() is True
        await backend.aclose()
        return emitted

    emitted = asyncio.run(scenario())
    assert len(emitted) == 2
    assert order == ["asr", "mt", "asr", "mt"]


def test_speech_cpp_nemo_config_validation(tmp_path):
    cfg = tmp_path / "profiles.toml"
    cfg.write_text(
        f"""
schema_version = 1
default_profile_id = "reazon-hy"
cache_root = "{tmp_path / 'cache'}"

[server]
port = 18765
allowed_origin = "chrome-extension://{'a' * 32}"

[profiles.reazon-hy]
label = "ReazonSpeech"
type = "local_pipeline"
enabled = true

[profiles.reazon-hy.asr]
type = "reazon_sherpa"
model_revision = "{REV_A}"

[profiles.reazon-hy.mt]
type = "hy_mt2_mlx"
model_revision = "{REV_B}"

[profiles.m1-pro-nemo]
label = "NeMo-v2"
type = "local_pipeline"
enabled = true

[profiles.m1-pro-nemo.asr]
type = "speech_cpp_nemo"
model_file = "reazonspeech-nemo-619M-v2-Q8_0.gguf"
threads = 8
decoding = "beam"
max_segment_s = 4.5
overlap_ms = 350

[profiles.m1-pro-nemo.mt]
type = "qwen3_mt_mlx"
model_revision = "{REV_B}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    nemo = settings.profiles["m1-pro-nemo"]
    assert nemo.asr.type == "speech_cpp_nemo"
    assert nemo.asr.threads == 8
    backend = lp.LocalBackend(Path(settings.cache_root), nemo)
    assert backend.uses_speech_cpp_asr is True
    assert len(backend.missing) == 6


def test_ane_config_fallback_and_parallel_session(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    cfg = tmp_path / "profiles.toml"
    cfg.write_text(
        f"""
schema_version = 1
default_profile_id = "reazon-hy"
cache_root = "{tmp_path / 'cache'}"

[server]
port = 18765
allowed_origin = "chrome-extension://{'a' * 32}"

[profiles.reazon-hy]
label = "ReazonSpeech"
type = "local_pipeline"
enabled = true

[profiles.reazon-hy.asr]
type = "reazon_sherpa"
model_revision = "{REV_A}"

[profiles.reazon-hy.mt]
type = "hy_mt2_mlx"
model_revision = "{REV_B}"

[profiles.m1-pro-nemo]
label = "NeMo-ANE"
type = "local_pipeline"
enabled = true

[profiles.m1-pro-nemo.asr]
type = "speech_cpp_nemo"
model_file = "reazonspeech-nemo-619M-v2-Q8_0.gguf"
threads = 6
decoding = "greedy"
device = "ane"
max_segment_s = 4.5
overlap_ms = 350
draft_interval_s = 1.2

[profiles.m1-pro-nemo.mt]
type = "qwen3_mt_mlx"
model_revision = "{REV_B}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    nemo = settings.profiles["m1-pro-nemo"]
    assert nemo.asr.device == "ane"
    backend = lp.LocalBackend(Path(settings.cache_root), nemo)
    assert backend.uses_ane_asr is True
    assert backend.uses_gpu_scheduler is False

    # Test SpeechCppNeMoRecognizer.decode ANE -> libspeech fallback on TOO_LONG / RETIRED
    rec = lp.SpeechCppNeMoRecognizer.__new__(lp.SpeechCppNeMoRecognizer)
    rec._model = object()
    rec._pad_samples = 5600
    rec._pad_s = 0.35
    rec._decoding = b"greedy"
    rec._device = "ane"
    rec._fallback_device = "gpu"

    class FakeAne:
        retired = False
        def transcribe(self, *args, **kwargs):
            raise lp._AneFallback(2)

    rec._ane = FakeAne()
    fallback_called = []

    class FakeCppLib:
        def speech_request_new(self, _m, req_ptr):
            return 0
        def speech_request_free(self, _r):
            pass
        def speech_request_set_audio(self, _r, _p, _n, _sr):
            return 0
        def speech_request_set_string(self, _r, _k, _v):
            return 0
        def speech_request_set_bool(self, _r, _k, _v):
            return 0
        def speech_transcribe(self, _r):
            fallback_called.append(True)
            return 0
        def speech_request_result(self, _r):
            return 1
        def speech_result_untimed(self, _r):
            return b"1"
        def speech_result_text(self, _r):
            return b"\xe3\x83\x86\xe3\x82\xb9\xe3\x83\x88"

    rec._lib = FakeCppLib()
    rec._check_status = lambda _op, st: st
    out = rec.decode(np.zeros(1600, dtype=np.float32))
    assert fallback_called == [True] and out == "テスト"

    # Test parallel ANE + GPU LocalSession Draft + Final emission & preemption
    class DummyBackend:
        profile = nemo
        uses_mlx_asr = False
        uses_speech_cpp_asr = True
        uses_gpu_speech_cpp = False
        uses_ane_asr = True
        uses_gpu_scheduler = False
        vad_pool = ThreadPoolExecutor(max_workers=1)
        asr_pool = ThreadPoolExecutor(max_workers=1)
        mt_pool = ThreadPoolExecutor(max_workers=1)

        class recognizer:
            @staticmethod
            def decode(samples, *, cancel_event=None, start_sample=0, dedup_before_sample=0, decoding_override=None, pad_ms_override=None):
                return "これはテストですよね" if decoding_override == "greedy" else "本番テストです。"

        class translator:
            @staticmethod
            def translate(_sess, text_ja, **_kwargs):
                return f"正式:{text_ja}"

            @staticmethod
            def translate_draft(_sess, text_ja, **_kwargs):
                return f"草稿:{text_ja}"

    async def scenario():
        emitted = []
        sess = lp.LocalSession(
            DummyBackend(),
            object(),
            type("Sess", (), {"close": lambda self: None})(),
            lambda ev: emitted.append(ev) or asyncio.sleep(0),
            None,
        )
        snap = lp._DraftSnapshot(0, 8000, np.zeros(8000, dtype=np.float32), 0)
        final_chunk = lp._EmittedChunk(0, np.zeros(16000, dtype=np.float32), 0, "vad_pause")
        sess.segmenter.push_and_preview = lambda frame, draft_interval_s=0.0: (
            ([final_chunk], None) if frame == b"FINAL" else ([], snap)
        )
        await sess.accept_pcm(b"DRAFT")
        for _ in range(50):
            if any(isinstance(e, lp.CaptionDelta) for e in emitted):
                break
            await asyncio.sleep(0.01)
        await sess.accept_pcm(b"FINAL")
        await sess.finish()
        await sess.aclose()
        DummyBackend.vad_pool.shutdown()
        DummyBackend.asr_pool.shutdown()
        DummyBackend.mt_pool.shutdown()
        return emitted

    evts = asyncio.run(scenario())
    assert any(isinstance(e, lp.CaptionDelta) and "草稿:" in e.text_zh for e in evts)
    assert any(isinstance(e, lp.Caption) and e.text_zh == "正式:本番テストです。" for e in evts)


def test_ane_init_failure_switches_session_to_gpu_serial_scheduler(tmp_path, monkeypatch):
    cfg = tmp_path / "profiles.toml"
    cfg.write_text(
        f"""
schema_version = 1
default_profile_id = "reazon-hy"
cache_root = "{tmp_path / 'cache'}"

[server]
port = 18765
allowed_origin = "chrome-extension://{'a' * 32}"

[profiles.reazon-hy]
label = "ReazonSpeech"
type = "local_pipeline"
enabled = true

[profiles.reazon-hy.asr]
type = "reazon_sherpa"
model_revision = "{REV_A}"

[profiles.reazon-hy.mt]
type = "hy_mt2_mlx"
model_revision = "{REV_B}"

[profiles.m1-pro-nemo]
label = "NeMo-ANE"
type = "local_pipeline"
enabled = true

[profiles.m1-pro-nemo.asr]
type = "speech_cpp_nemo"
model_file = "reazonspeech-nemo-619M-v2-Q8_0.gguf"
threads = 6
decoding = "greedy"
device = "ane"
max_segment_s = 4.5
overlap_ms = 350

[profiles.m1-pro-nemo.mt]
type = "qwen3_mt_mlx"
model_revision = "{REV_B}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    cache = Path(settings.cache_root)
    (cache / "speech-cpp").mkdir(parents=True, exist_ok=True)
    dylib_path = cache / "speech-cpp/libspeech.dylib"
    model_path = cache / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
    vad_path = cache / "reazonspeech-assets/silero_vad.onnx"
    vad_path.parent.mkdir(parents=True, exist_ok=True)
    dylib_path.write_bytes(b"")
    model_path.write_bytes(b"")
    vad_path.write_bytes(b"vad")

    monkeypatch.setattr(lp.NeMoAneBridge, "is_ready", classmethod(lambda cls, _dir: True))

    def boom_ane(*_a, **_kw):
        raise RuntimeError("ane boom")

    monkeypatch.setattr(lp, "NeMoAneBridge", type("BoomBridge", (), {
        "is_ready": classmethod(lambda cls, _dir: True),
        "__init__": boom_ane,
    }))

    class _StubFn:
        def __init__(self, ret=0):
            self.ret = ret

        def __call__(self, *_a):
            return self.ret

    class FakeCppLib:
        def __init__(self, _path):
            pass

        def __getattr__(self, name):
            if name == "speech_request_result":
                fn = _StubFn(1)
            elif name == "speech_result_untimed":
                fn = _StubFn(b"1")
            elif name in ("speech_result_text", "speech_last_error"):
                fn = _StubFn(b"")
            elif name == "speech_status_name":
                fn = _StubFn(b"ok")
            else:
                fn = _StubFn(0)
            setattr(self, name, fn)
            return fn

    monkeypatch.setattr(lp.ctypes, "CDLL", FakeCppLib)
    monkeypatch.setattr(lp, "silero_config", lambda *_a: object())

    rec = lp.SpeechCppNeMoRecognizer(dylib_path, model_path, vad_path, settings.profiles["m1-pro-nemo"].asr)
    assert rec.ane_active is False
    assert rec._ane_init_fallback_reason == "ane_init_failed"
    assert rec.last_decode_meta["backend"] == "speech_cpp_gpu"
    assert rec.last_decode_meta["fallback_reason"] == "ane_init_failed"

    backend = lp.LocalBackend(cache, settings.profiles["m1-pro-nemo"])
    backend.recognizer = rec
    assert backend.ane_runtime_active is False

    async def make_sess():
        sess = lp.LocalSession(backend, object(), lp._NoopSession(), lambda _ev: asyncio.sleep(0), None)
        assert sess._use_gpu_serial is True
        assert sess._use_ane_parallel is False
        await sess.aclose()
        await backend.aclose()

    asyncio.run(make_sess())


def test_ane_mid_session_gpu_fallback_serializes_with_mt_and_records_meta(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time

    cfg = tmp_path / "profiles.toml"
    cfg.write_text(
        f"""
schema_version = 1
default_profile_id = "reazon-hy"
cache_root = "{tmp_path / 'cache'}"

[server]
port = 18765
allowed_origin = "chrome-extension://{'a' * 32}"

[profiles.reazon-hy]
label = "ReazonSpeech"
type = "local_pipeline"
enabled = true

[profiles.reazon-hy.asr]
type = "reazon_sherpa"
model_revision = "{REV_A}"

[profiles.reazon-hy.mt]
type = "hy_mt2_mlx"
model_revision = "{REV_B}"

[profiles.m1-pro-nemo]
label = "NeMo-ANE"
type = "local_pipeline"
enabled = true

[profiles.m1-pro-nemo.asr]
type = "speech_cpp_nemo"
model_file = "reazonspeech-nemo-619M-v2-Q8_0.gguf"
threads = 6
decoding = "greedy"
device = "ane"
draft_interval_s = 0.0
max_segment_s = 4.5
overlap_ms = 350

[profiles.m1-pro-nemo.mt]
type = "qwen3_mt_mlx"
model_revision = "{REV_B}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    nemo = load_settings(cfg).profiles["m1-pro-nemo"]

    active_gpu = 0
    max_concurrent_gpu = 0
    gpu_mu = threading.Lock()

    class FallbackCppLib:
        def speech_request_new(self, _m, _r): return 0
        def speech_request_free(self, _r): pass
        def speech_request_set_audio(self, *_a): return 0
        def speech_request_set_string(self, *_a): return 0
        def speech_request_set_bool(self, *_a): return 0
        def speech_request_set_progress(self, *_a): return 0
        def speech_transcribe(self, _r):
            nonlocal active_gpu, max_concurrent_gpu
            with gpu_mu:
                active_gpu += 1
                max_concurrent_gpu = max(max_concurrent_gpu, active_gpu)
            time.sleep(0.03)
            with gpu_mu:
                active_gpu -= 1
            return 0
        def speech_request_result(self, _r): return 1
        def speech_result_untimed(self, _r): return b"1"
        def speech_result_text(self, _r): return b"\xe3\x83\x86\xe3\x82\xb9\xe3\x83\x88"

    rec = lp.SpeechCppNeMoRecognizer.__new__(lp.SpeechCppNeMoRecognizer)
    rec._model = object()
    rec._pad_samples = 0
    rec._pad_s = 0.0
    rec._decoding = b"greedy"
    rec._device = "ane"
    rec._fallback_device = "gpu"
    rec._ane_init_fallback_reason = None
    rec.last_decode_meta = {}
    rec._lib = FallbackCppLib()
    rec._check_status = lambda _op, st: st
    rec._ane = type("TooLongAne", (), {
        "retired": False,
        "transcribe": lambda *_a, **_kw: (_ for _ in ()).throw(lp._AneFallback(2)),
    })()

    class DummyBackend:
        profile = nemo
        uses_mlx_asr = False
        uses_speech_cpp_asr = True
        uses_gpu_speech_cpp = False
        uses_ane_asr = True
        uses_gpu_scheduler = False
        ane_runtime_active = True
        _gpu_fallback_lock = threading.Lock()
        recognizer = rec
        vad_pool = ThreadPoolExecutor(max_workers=1)
        asr_pool = ThreadPoolExecutor(max_workers=1)
        mt_pool = ThreadPoolExecutor(max_workers=1)

        class translator:
            @staticmethod
            def translate(_sess, text_ja, **_kwargs):
                nonlocal active_gpu, max_concurrent_gpu
                with gpu_mu:
                    active_gpu += 1
                    max_concurrent_gpu = max(max_concurrent_gpu, active_gpu)
                time.sleep(0.04)
                with gpu_mu:
                    active_gpu -= 1
                return f"译:{text_ja}"

    traces = []

    async def scenario():
        emitted = []
        sess = lp.LocalSession(
            DummyBackend(),
            object(),
            lp._NoopSession(),
            lambda ev: emitted.append(ev) or asyncio.sleep(0),
            lambda stage, **kw: traces.append((stage, kw)),
        )
        chunks = [
            lp._EmittedChunk(0, np.zeros(1600, dtype=np.float32), 0, "vad"),
            lp._EmittedChunk(1600, np.zeros(1600, dtype=np.float32), 0, "vad"),
        ]
        sess.segmenter.push = lambda frame: [chunks.pop(0)] if (frame is not None and chunks) else []
        await sess.accept_pcm(b"1")
        await sess.accept_pcm(b"2")
        await sess.finish()
        await sess.aclose()
        DummyBackend.vad_pool.shutdown()
        DummyBackend.asr_pool.shutdown()
        DummyBackend.mt_pool.shutdown()
        return emitted

    emitted = asyncio.run(scenario())
    assert len(emitted) == 2
    assert max_concurrent_gpu == 1
    asr_ends = [kw for st, kw in traces if st == "asr_end"]
    assert len(asr_ends) == 2
    assert all(x["asr_backend"] == "speech_cpp_gpu" and x["fallback_reason"] == "ane_too_long" for x in asr_ends)


def test_local_session_aclose_waits_for_mt_before_resetting_kv_cache():
    from concurrent.futures import ThreadPoolExecutor

    order = []
    threads = {}
    mt_entered = threading.Event()
    release_mt = threading.Event()

    class DummyBackend:
        profile = type("Prof", (), {
            "asr": type("Asr", (), {"max_segment_s": 0.0, "overlap_ms": 0, "draft_interval_s": 0.0})(),
        })()
        uses_mlx_asr = False
        uses_speech_cpp_asr = False
        uses_gpu_speech_cpp = False
        uses_ane_asr = False
        uses_gpu_scheduler = False
        vad_pool = ThreadPoolExecutor(max_workers=1)
        asr_pool = ThreadPoolExecutor(max_workers=1)
        mt_pool = ThreadPoolExecutor(max_workers=1)

        class translator:
            @staticmethod
            def translate(_sess, _text_ja, **_kwargs):
                order.append("mt_started")
                threads["mt"] = threading.get_ident()
                mt_entered.set()
                release_mt.wait(timeout=2.0)
                order.append("mt_finished")
                return "你好"

    class TrackingSession:
        def close(self):
            order.append("translation_closed")
            threads["close"] = threading.get_ident()

    async def scenario():
        sess = lp.LocalSession(DummyBackend(), object(), TrackingSession(), lambda _ev: asyncio.sleep(0), None)
        sess._enqueue_segment(lp.RecognizedSegment(1, 0, 1600, "こんにちは"))
        for _ in range(100):
            if mt_entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert mt_entered.is_set()
        close_task = asyncio.create_task(sess.aclose())
        await asyncio.sleep(0.05)
        assert "translation_closed" not in order
        release_mt.set()
        await close_task
        DummyBackend.vad_pool.shutdown()
        DummyBackend.asr_pool.shutdown()
        DummyBackend.mt_pool.shutdown()

    asyncio.run(scenario())
    assert order == ["mt_started", "mt_finished", "translation_closed"]
    assert threads["mt"] == threads["close"]


def test_native_nemo_ane_idle_no_keepalive_and_timings():
    import time

    ane_dir = ROOT / ".model-cache/nemo-ane"
    gguf_path = ROOT / ".model-cache/speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
    if not lp.NeMoAneBridge.is_ready(ane_dir) or not gguf_path.is_file():
        pytest.skip("local ANE assets not built")

    bridge = lp.NeMoAneBridge(ane_dir, gguf_path)
    try:
        c0 = bridge.eval_count()
        assert c0 >= 1
        time.sleep(2.5)
        assert bridge.eval_count() == c0

        audio = np.zeros(lp.RATE, dtype=np.float32)
        _ = bridge.transcribe(audio, pad_samples=5600, use_beam=False)
        assert bridge.eval_count() == c0 + 1
        timings = bridge.last_timings
        assert isinstance(timings, dict)
        for key in ("frontend_ms", "predict_ms", "output_copy_ms", "decode_ms", "total_ms"):
            assert timings[key] > 0.0
        assert timings["bucket_mel"] == 256
    finally:
        bridge.close()


def test_p0_hybrid_decoding_config_and_native_ane_hybrid_mode():
    from subtitle_service.config import AsrConfig, MtConfig

    asr_cfg = AsrConfig(
        type="speech_cpp_nemo",
        model_file="reazonspeech-nemo-619M-v2-Q8_0.gguf",
        decoding="hybrid",
        device="ane",
        vad_pause_ms=180,
        max_segment_s=2.8,
        pad_ms=500,
    )
    mt_cfg = MtConfig(
        type="qwen3_mt_mlx",
        model_revision=REV_B,
        context_turns=8,
    )
    assert asr_cfg.decoding == "hybrid" and mt_cfg.context_turns == 8

    # speech.cpp fallback maps hybrid -> beam while reporting decoding='hybrid'
    rec = lp.SpeechCppNeMoRecognizer.__new__(lp.SpeechCppNeMoRecognizer)
    rec._model = object()
    rec._pad_samples = 8000
    rec._pad_s = 0.5
    rec._decoding = b"hybrid"
    rec._device = "ane"
    rec._fallback_device = "gpu"
    rec._ane = None
    rec._ane_init_fallback_reason = "ane_unavailable"
    seen_decoding = []

    class FakeCppLib:
        def speech_request_new(self, _m, _ptr): return 0
        def speech_request_free(self, _r): pass
        def speech_request_set_audio(self, _r, _p, _n, _sr): return 0
        def speech_request_set_string(self, _r, key, val):
            if key == rec._OPT_DECODING:
                seen_decoding.append(val)
            return 0
        def speech_request_set_bool(self, _r, _k, _v): return 0
        def speech_transcribe(self, _r): return 0
        def speech_request_result(self, _r): return 1
        def speech_result_untimed(self, _r): return b"1"
        def speech_result_text(self, _r): return b"\xe3\x81\xaf\xe3\x81\x84\xe3\x80\x82"

    rec._lib = FakeCppLib()
    rec._check_status = lambda _op, st: st
    out = rec.decode(np.zeros(lp.RATE, dtype=np.float32))
    assert out == "はい。"
    assert seen_decoding == [b"beam"]
    assert rec.last_decode_meta["decoding"] == "hybrid"

    ane_dir = ROOT / ".model-cache/nemo-ane"
    gguf_path = ROOT / ".model-cache/speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
    if lp.NeMoAneBridge.is_ready(ane_dir) and gguf_path.is_file():
        bridge = lp.NeMoAneBridge(ane_dir, gguf_path)
        try:
            c0 = bridge.eval_count()
            _ = bridge.transcribe(np.zeros(2 * lp.RATE, dtype=np.float32), pad_samples=8000, use_beam=2)
            assert bridge.eval_count() == c0 + 1
        finally:
            bridge.close()


def test_p0_draft_regression_guard_recovers_collapsed_final():
    pcm = np.zeros(lp.RATE * 3, dtype=np.float32)

    # 1. Sufficient final passes through unchanged without invoking beam retry
    item_ok = lp._PendingAudio(
        1, 0, lp.RATE * 3, pcm, best_draft_ja="来週末どうするの？", best_draft_end_sample=int(lp.RATE * 2.0)
    )
    called = []
    txt, act = lp._guard_final_against_draft(
        "来週末どうするの？どうしよう。",
        item_ok,
        retry_beam_fn=lambda: called.append(True) or "unused",
    )
    assert act == "none" and txt == "来週末どうするの？どうしよう。" and not called

    # 2. Collapsed final recovered by beam retry
    item_collapsed = lp._PendingAudio(
        2, 0, lp.RATE * 4, pcm, best_draft_ja="麻美、来週末どうすんの。", best_draft_end_sample=int(lp.RATE * 2.5)
    )
    txt, act = lp._guard_final_against_draft(
        "どう",
        item_collapsed,
        retry_beam_fn=lambda: "麻美、来週末どうすんの？どうしようかな。",
    )
    assert act == "beam_recovered" and txt == "麻美、来週末どうすんの？どうしようかな。"

    # 3. Two-speaker loud suffix merges preserved draft prefix + tail final when beam also collapsed
    item_split_speakers = lp._PendingAudio(
        3, 0, lp.RATE * 4, pcm, best_draft_ja="麻美、そろそろ寝なさい。", best_draft_end_sample=int(lp.RATE * 2.4)
    )
    txt, act = lp._guard_final_against_draft(
        "はい。",
        item_split_speakers,
        retry_beam_fn=lambda: "はい。",
    )
    assert act == "merged_draft_tail" and txt == "麻美、そろそろ寝なさい。 はい。"

    # 4. Empty final falls back to draft cleanly
    txt, act = lp._guard_final_against_draft("", item_split_speakers, retry_beam_fn=lambda: "")
    assert act == "fallback_draft" and txt == "麻美、そろそろ寝なさい。"


def test_p1_pure_punctuation_asr_skipped_before_mt():
    from concurrent.futures import ThreadPoolExecutor

    class DummyBackend:
        profile = type("Prof", (), {
            "asr": type("Asr", (), {"max_segment_s": 0.0, "overlap_ms": 0, "draft_interval_s": 0.0})(),
        })()
        uses_mlx_asr = False
        uses_speech_cpp_asr = True
        uses_gpu_speech_cpp = False
        uses_ane_asr = True
        uses_gpu_scheduler = False
        ane_runtime_active = True
        _gpu_fallback_lock = threading.Lock()
        vad_pool = ThreadPoolExecutor(max_workers=1)
        asr_pool = ThreadPoolExecutor(max_workers=1)
        mt_pool = ThreadPoolExecutor(max_workers=1)
        asr_outputs = iter(["。", "！？", "こんにちは"])
        mt_calls = []

        class recognizer:
            last_decode_meta = {"backend": "ane", "fallback_reason": None, "timings": None}

            @classmethod
            def decode(cls, *_args, **_kwargs):
                return next(DummyBackend.asr_outputs)

        class translator:
            @staticmethod
            def translate(_sess, text_ja, **_kwargs):
                DummyBackend.mt_calls.append(text_ja)
                return f"ZH:{text_ja}"

    traces = []
    emitted = []

    async def scenario():
        sess = lp.LocalSession(
            DummyBackend(),
            object(),
            type("Sess", (), {"close": lambda self: None})(),
            lambda ev: emitted.append(ev) or asyncio.sleep(0),
            lambda stage, **kw: traces.append({"stage": stage, **kw}),
        )
        chunks = [
            lp._EmittedChunk(i * 16000, np.zeros(16000, dtype=np.float32), 0, "vad_natural")
            for i in range(3)
        ]
        idx = 0

        def fake_push(frame):
            nonlocal idx
            if frame is not None and idx < len(chunks):
                ch = chunks[idx]
                idx += 1
                return [ch]
            return []

        sess.segmenter.push = fake_push
        for _ in range(3):
            await sess.accept_pcm(b"\x00\x00" * 1600)
        await sess.finish()
        await sess.aclose()
        DummyBackend.vad_pool.shutdown()
        DummyBackend.asr_pool.shutdown()
        DummyBackend.mt_pool.shutdown()

    asyncio.run(scenario())
    assert DummyBackend.mt_calls == ["こんにちは"]
    assert [e.text_zh for e in emitted] == ["ZH:こんにちは"]
    asr_ends = [t for t in traces if t["stage"] == "asr_end"]
    assert len(asr_ends) == 3
    assert asr_ends[0].get("mt_skipped") == "punct_only"
    assert asr_ends[1].get("mt_skipped") == "punct_only"
    assert "mt_skipped" not in asr_ends[2]

