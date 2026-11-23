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

