import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import time
import socket
from threading import Thread

import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from fastapi.testclient import TestClient
import jsonschema
import pytest
from starlette.websockets import WebSocketDisconnect

from subtitle_service import app
from subtitle_service.backend import Caption, CaptionDelta
from subtitle_service.config import load_settings, load_token

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "chrome-extension://" + "a" * 32
HEADERS = {"origin": ORIGIN, "host": "127.0.0.1:18765"}
TOKEN = "fixture-token"
FIXTURE = json.loads((ROOT / "protocol/v1.fixture.json").read_text())
SCHEMA = json.loads((ROOT / "protocol/v1.schema.json").read_text())


class Fake:
    def __init__(self, *, warmup=0, block=0, tail="我这边有照片。"):
        self.warmup_delay = warmup
        self.block = block
        self.tail = tail
        self.closed = 0
        self.opened = 0

    async def warmup(self):
        await asyncio.sleep(self.warmup_delay)

    async def available(self):
        return True

    async def open(self, emit, *, record=None):
        self.opened += 1
        return FakeSession(self, emit)

    async def aclose(self):
        pass


class FakeSession:
    def __init__(self, engine, emit):
        self.engine = engine
        self.emit = emit
        self.samples = 0

    async def accept_pcm(self, frame):
        if self.engine.block:
            await asyncio.sleep(self.engine.block)
        self.samples += len(frame) // 2
        await self.emit(Caption(0, self.samples, "ショー将解释越位规则。"))

    async def finish(self):
        if self.samples:
            await self.emit(Caption(0, self.samples, self.engine.tail))

    async def aclose(self):
        self.engine.closed += 1


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "profiles.toml"
    path.write_text((ROOT / "engine/config/profiles.example.toml").read_text().replace(
        "chrome-extension://<extension-id>", ORIGIN,
    ))
    return load_settings(path)


@contextmanager
def client(config, backend=None, *, ready=True):
    engine = backend or Fake()
    with TestClient(app.create_app(config, TOKEN, {"reazon-hy": engine}),
                    base_url="http://127.0.0.1:18765") as c:
        if ready:
            deadline = time.monotonic() + 2
            while c.get("/v1/health").status_code != 200:
                assert time.monotonic() < deadline
                time.sleep(.005)
        yield c, engine


def websocket(c, headers=HEADERS):
    return c.websocket_connect("/v1/captions", headers=headers)


def start(ws, **updates):
    ws.send_json(FIXTURE["start"] | updates)
    response = ws.receive_json()
    jsonschema.validate(response, SCHEMA)
    return response


def assert_error(ws, kind, close_code):
    response = ws.receive_json()
    assert response["type"] == "error" and response["code"] == kind
    with pytest.raises(WebSocketDisconnect) as caught:
        ws.receive_json()
    assert caught.value.code == close_code


def test_contract_and_drain(config):
    for event in FIXTURE.values():
        jsonschema.validate(event, SCHEMA)
    with client(config) as (c, backend):
        assert c.get("/v1/profiles").status_code == 401
        headers = HEADERS | {"authorization": f"Bearer {TOKEN}"}
        assert c.get("/v1/profiles", headers=headers).json() == {
            "default_profile_id": "reazon-hy",
            "profiles": [{"id": "reazon-hy", "label": "ReazonSpeech · 本地简中", "available": True}],
        }
        assert c.options("/v1/profiles", headers=HEADERS | {
            "access-control-request-method": "GET", "access-control-request-headers": "authorization",
        }).headers["access-control-allow-origin"] == ORIGIN
        with websocket(c) as ws:
            ready = start(ws)
            sid = ready["session_id"]
            ws.send_bytes(bytes(6400))
            ws.send_bytes(bytes(6))
            ws.send_json(FIXTURE["finish"])
            for _ in range(2):
                assert ws.receive_json() == FIXTURE["warning"] | {"session_id": sid}
            assert ws.receive_json() == FIXTURE["caption"] | {"session_id": sid, "end_sample": 3203}
            assert ws.receive_json() == FIXTURE["finished"] | {"session_id": sid, "accepted_samples": 3203}
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_json()
            assert caught.value.code == 1000
        assert backend.closed == 1


@pytest.mark.parametrize("override,code,close", [
    ({"token": "wrong"}, "unauthorized", 4401),
    ({"profile_id": "absent"}, "unknown_profile", 4400),
    ({"profile_id": "whisper-small"}, "profile_unavailable", 1011),
    ({"version": True}, "unsupported_version", 4400),
    ({"audio": {"format": "s16le", "sample_rate": 16000.0, "channels": 1}}, "invalid_audio", 4400),
])
def test_start_rejected(config, override, code, close):
    with client(config) as (c, backend), websocket(c) as ws:
        ws.send_json(FIXTURE["start"] | override)
        assert_error(ws, code, close)
        assert backend.opened == 0


def test_origin_and_host(config):
    with client(config) as (c, backend):
        for name, value in (("origin", "https://example.org"), ("host", "example.org")):
            with pytest.raises(WebSocketDisconnect):
                with websocket(c, HEADERS | {name: value}):
                    pass
            assert c.get("/v1/health", headers=HEADERS | {name: value}).status_code == 403
        assert backend.opened == 0


def test_not_ready(config):
    with client(config, Fake(warmup=.2), ready=False) as (c, _):
        assert c.get("/v1/health").status_code == 503
        with websocket(c) as ws:
            ws.send_json(FIXTURE["start"])
            assert_error(ws, "profile_unavailable", 1011)


@pytest.mark.parametrize("frames", [(b"",), (b"x",), (b"x"*6402,), (b"xx", b"xx")])
def test_bad_audio(config, frames):
    with client(config) as (c, _), websocket(c) as ws:
        start(ws)
        for frame in frames:
            ws.send_bytes(frame)
        response = ws.receive_json()
        if response["type"] == "warning":
            response = ws.receive_json()
        assert response["type"] == "error" and response["code"] == "invalid_audio"


def test_overload_and_drain_timeout(config, monkeypatch):
    monkeypatch.setattr(app, "QUEUE_TIMEOUT", .03)
    monkeypatch.setattr(app, "DRAIN_TIMEOUT", .03)
    with client(config, Fake(block=5)) as (c, backend):
        with websocket(c) as ws:
            start(ws)
            for _ in range(23):
                ws.send_bytes(bytes(6400))
            assert_error(ws, "overloaded", 1013)
        with websocket(c) as ws:
            start(ws)
            ws.send_bytes(bytes(6400))
            ws.send_json(FIXTURE["finish"])
            assert_error(ws, "drain_timeout", 1011)
        assert backend.closed == 2


def test_cancel_releases_and_isolates_sessions(config):
    backend = Fake(block=5)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        config.server.port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app.create_app(config, TOKEN, {"reazon-hy": backend}),
                                              log_level="error", ws="websockets-sansio"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(.01)

        async def exercise():
            uri = f"ws://127.0.0.1:{config.server.port}/v1/captions"
            async with asyncio.timeout(4):
                async with connect(uri, origin=ORIGIN, proxy=None) as old:
                    await old.send(json.dumps(FIXTURE["start"]))
                    previous = json.loads(await old.recv())["session_id"]
                    await old.send(bytes(6400))
                    async with connect(uri, origin=ORIGIN, proxy=None) as second:
                        await second.send(json.dumps(FIXTURE["start"]))
                        assert json.loads(await second.recv())["code"] == "busy"
                        with pytest.raises(ConnectionClosed):
                            await second.recv()
                while backend.closed != 1:
                    await asyncio.sleep(.01)
                backend.block = 0
                async with connect(uri, origin=ORIGIN, proxy=None) as ws:
                    await ws.send(json.dumps(FIXTURE["start"]))
                    sid = json.loads(await ws.recv())["session_id"]
                    assert sid != previous
                    await ws.send(bytes(6400))
                    await ws.send(json.dumps(FIXTURE["finish"]))
                    assert json.loads(await ws.recv())["type"] == "warning"
                    assert json.loads(await ws.recv()) == FIXTURE["caption"] | {"session_id": sid}
                    assert json.loads(await ws.recv())["accepted_samples"] == 3200
        try:
            asyncio.run(exercise())
        finally:
            server.should_exit = True
            thread.join(4)
            assert not thread.is_alive()


def test_private_token(tmp_path):
    path = tmp_path / "token"
    value = load_token(path, create=True)
    assert load_token(path, create=True) == value
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_token(path, create=False)


def test_64ms_frames_and_caption_delta_contract(config):
    class DeltaSession:
        def __init__(self, emit):
            self.emit = emit
            self.samples = 0

        async def accept_pcm(self, frame):
            self.samples += len(frame) // 2

        async def finish(self):
            await self.emit(CaptionDelta(0, self.samples, "これは仮名"))  # skipped silently
            await self.emit(CaptionDelta(0, self.samples, "先看"))
            await self.emit(Caption(0, self.samples, "先看这个。"))

        async def aclose(self):
            pass

    class DeltaBackend(Fake):
        async def open(self, emit, *, record=None):
            return DeltaSession(emit)

    with client(config, DeltaBackend()) as (c, _), websocket(c) as ws:
        sid = start(ws)["session_id"]
        ws.send_bytes(bytes(2048))
        ws.send_bytes(bytes(2048))
        ws.send_json(FIXTURE["finish"])
        d1 = ws.receive_json()
        jsonschema.validate(d1, SCHEMA)
        assert d1 == {
            "type": "caption_delta",
            "session_id": sid,
            "seq": 1,
            "start_sample": 0,
            "end_sample": 2048,
            "text_zh": "先看",
        }
        c1 = ws.receive_json()
        jsonschema.validate(c1, SCHEMA)
        assert c1["type"] == "caption" and c1["seq"] == 1 and c1["text_zh"] == "先看这个。"
        fin = ws.receive_json()
        assert fin["type"] == "finished" and fin["accepted_samples"] == 2048


def test_caption_delta_kind_provenance_schema(config):
    class ProvenanceSession:
        def __init__(self, emit):
            self.emit = emit
            self.samples = 0

        async def accept_pcm(self, frame):
            self.samples += len(frame) // 2

        async def finish(self):
            await self.emit(CaptionDelta(0, self.samples, "预览字幕", kind="preview"))
            await self.emit(CaptionDelta(0, self.samples, "流式终稿", segment_id=1, kind="final_token_stream"))
            await self.emit(Caption(0, self.samples, "流式终稿。", segment_id=1))

        async def aclose(self):
            pass

    class ProvenanceBackend(Fake):
        async def open(self, emit, *, record=None):
            return ProvenanceSession(emit)

    with client(config, ProvenanceBackend()) as (c, _), websocket(c) as ws:
        sid = start(ws)["session_id"]
        ws.send_bytes(bytes(2048))
        ws.send_json(FIXTURE["finish"])
        d_prev = ws.receive_json()
        jsonschema.validate(d_prev, SCHEMA)
        assert d_prev["delta_kind"] == "preview"
        d_stream = ws.receive_json()
        jsonschema.validate(d_stream, SCHEMA)
        assert d_stream["delta_kind"] == "final_token_stream"
        cap = ws.receive_json()
        jsonschema.validate(cap, SCHEMA)
        assert cap["text_zh"] == "流式终稿。"
        fin = ws.receive_json()
        assert fin["type"] == "finished"


def test_length_limit_translation_not_emitted_over_websocket(config):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from subtitle_service import local_pipeline as lp

    class TruncatingTranslator:
        def __init__(self):
            self.calls = 0
            self.last_translate_meta = {}

        def translate(self, _sess, _text_ja, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                self.last_translate_meta = {"stop_reason": "length_limit", "valid": False, "gen_tokens": 256}
                return "截断的中文译文"
            self.last_translate_meta = {"stop_reason": "eos", "valid": True, "gen_tokens": 4}
            return "完整中文。"

    class StubBackend(Fake):
        def __init__(self):
            super().__init__()
            self.uses_ane_asr = False
            self.uses_gpu_scheduler = False
            self.uses_speech_cpp_asr = False
            self.profile = SimpleNamespace(asr=SimpleNamespace(max_segment_s=0.0, overlap_ms=350, draft_interval_s=0.0))
            self.translator = TruncatingTranslator()
            self.vad_pool = ThreadPoolExecutor(max_workers=1)
            self.asr_pool = ThreadPoolExecutor(max_workers=1)
            self.mt_pool = ThreadPoolExecutor(max_workers=1)

        async def open(self, emit, *, record=None):
            sess = lp.LocalSession(self, object(), lp._NoopSession(), emit, record)
            sess._enqueue_segment(lp.RecognizedSegment(1, 0, 1600, "テスト一"))
            sess._enqueue_segment(lp.RecognizedSegment(2, 1600, 3200, "テスト二"))

            async def _accept(_frame):
                pass

            async def _finish():
                await sess.pending.put(None)
                if sess.mt_task is not None:
                    await sess.mt_task

            sess.accept_pcm = _accept
            sess.finish = _finish
            return sess

        async def aclose(self):
            self.vad_pool.shutdown()
            self.asr_pool.shutdown()
            self.mt_pool.shutdown()

    backend = StubBackend()
    try:
        with client(config, backend) as (c, _), websocket(c) as ws:
            start(ws)
            ws.send_bytes(bytes(6400))
            ws.send_json(FIXTURE["finish"])
            cap = ws.receive_json()
            jsonschema.validate(cap, SCHEMA)
            assert cap["type"] == "caption"
            assert cap["text_zh"] == "完整中文。"
            fin = ws.receive_json()
            assert fin["type"] == "finished"
    finally:
        backend.vad_pool.shutdown()
        backend.asr_pool.shutdown()
        backend.mt_pool.shutdown()


def test_r1_delta_then_length_limit_emits_discard_and_advances_seq_for_next_cue(config):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from subtitle_service import local_pipeline as lp

    class DeltaAwareTranslator:
        def __init__(self):
            self.calls = 0
            self.last_translate_meta = {}

        def translate(self, _sess, _text_ja, **kwargs):
            self.calls += 1
            on_delta = kwargs.get("on_delta")
            if self.calls <= 2:
                if on_delta is not None:
                    on_delta(f"截断第{self.calls}句前两字")
                self.last_translate_meta = {"stop_reason": "length_limit", "valid": False, "gen_tokens": 256}
                return "截断的超长文本"
            if on_delta is not None:
                on_delta("下一句短前")
            self.last_translate_meta = {"stop_reason": "eos", "valid": True, "gen_tokens": 6}
            return "下一句完整中文。"

    class StubBackend(Fake):
        def __init__(self):
            super().__init__()
            self.uses_ane_asr = False
            self.uses_gpu_scheduler = False
            self.uses_speech_cpp_asr = False
            self.profile = SimpleNamespace(asr=SimpleNamespace(max_segment_s=0.0, overlap_ms=350, draft_interval_s=0.0))
            self.translator = DeltaAwareTranslator()
            self.vad_pool = ThreadPoolExecutor(max_workers=1)
            self.asr_pool = ThreadPoolExecutor(max_workers=1)
            self.mt_pool = ThreadPoolExecutor(max_workers=1)

        async def open(self, emit, *, record=None):
            sess = lp.LocalSession(self, object(), lp._NoopSession(), emit, record)
            enqueued = False

            async def _accept(_frame):
                nonlocal enqueued
                if not enqueued:
                    enqueued = True
                    sess._enqueue_segment(lp.RecognizedSegment(1, 0, 1600, "テスト一"))
                    sess._enqueue_segment(lp.RecognizedSegment(2, 1600, 3200, "テスト二"))
                    sess._enqueue_segment(lp.RecognizedSegment(3, 3200, 4800, "テスト三"))

            async def _finish():
                await sess.pending.put(None)
                if sess.mt_task is not None:
                    await sess.mt_task

            sess.accept_pcm = _accept
            sess.finish = _finish
            return sess

        async def aclose(self):
            self.vad_pool.shutdown()
            self.asr_pool.shutdown()
            self.mt_pool.shutdown()

    backend = StubBackend()
    try:
        with client(config, backend) as (c, _), websocket(c) as ws:
            start(ws)
            ws.send_bytes(bytes(6400))
            ws.send_bytes(bytes(6400))
            ws.send_json(FIXTURE["finish"])
            ev1 = ws.receive_json()
            ev2 = ws.receive_json()
            ev3 = ws.receive_json()
            ev4 = ws.receive_json()
            ev5 = ws.receive_json()
            ev6 = ws.receive_json()
            ev7 = ws.receive_json()
            for ev in (ev1, ev2, ev3, ev4, ev5, ev6, ev7):
                jsonschema.validate(ev, SCHEMA)
            assert ev1["type"] == "caption_delta" and ev1["seq"] == 1
            assert ev2 == {"type": "caption_discard", "session_id": ev1["session_id"], "seq": 1, "reason": "length_limit"}
            assert ev3["type"] == "caption_delta" and ev3["seq"] == 2
            assert ev4 == {"type": "caption_discard", "session_id": ev1["session_id"], "seq": 2, "reason": "length_limit"}
            assert ev5["type"] == "caption_delta" and ev5["seq"] == 3 and ev5["text_zh"] == "下一句短前"
            assert ev6["type"] == "caption" and ev6["seq"] == 3 and ev6["text_zh"] == "下一句完整中文。"
            assert ev7["type"] == "finished"
    finally:
        backend.vad_pool.shutdown()
        backend.asr_pool.shutdown()
        backend.mt_pool.shutdown()


def test_r1_gateway_filter_with_active_delta_emits_discard(config):
    class FilteredAfterDeltaSession:
        def __init__(self, emit):
            self.emit = emit
            self.samples = 0

        async def accept_pcm(self, frame):
            self.samples += len(frame) // 2

        async def finish(self):
            await self.emit(CaptionDelta(0, self.samples, "先显示预览", kind="preview"))
            await self.emit(Caption(0, self.samples, "カタカナ混入"))
            await self.emit(Caption(0, self.samples, "恢复正常中文。"))

        async def aclose(self):
            pass

    class B(Fake):
        async def open(self, emit, *, record=None):
            return FilteredAfterDeltaSession(emit)

    with client(config, B()) as (c, _), websocket(c) as ws:
        start(ws)
        ws.send_bytes(bytes(2048))
        ws.send_json(FIXTURE["finish"])
        d1 = ws.receive_json()
        w1 = ws.receive_json()
        disc = ws.receive_json()
        cap = ws.receive_json()
        fin = ws.receive_json()
        for ev in (d1, w1, disc, cap, fin):
            jsonschema.validate(ev, SCHEMA)
        assert d1["type"] == "caption_delta" and d1["seq"] == 1
        assert w1["type"] == "warning" and w1["code"] == "translation_invalid"
        assert disc["type"] == "caption_discard" and disc["seq"] == 1 and disc["reason"] == "translation_invalid"
        assert cap["type"] == "caption" and cap["seq"] == 2 and cap["text_zh"] == "恢复正常中文。"
        assert fin["type"] == "finished"


def test_r3_backpressure_drains_within_deadline_and_handles_finish_or_disconnect(config, monkeypatch):
    monkeypatch.setattr(app, "QUEUE_MAX_BYTES", FRAME_BYTES := 6400)
    monkeypatch.setattr(app, "QUEUE_TIMEOUT", 0.35)

    class PacedBackend(Fake):
        def __init__(self, delay_s):
            super().__init__(block=delay_s)
            self.received_frames = 0

        async def open(self, emit, *, record=None):
            sess = FakeSession(self, emit)
            orig = sess.accept_pcm

            async def _accept(frame):
                self.received_frames += 1
                await orig(frame)

            sess.accept_pcm = _accept
            return sess

    # Case 1: queue full + subsequent frames arrive AND finish arrives while draining within deadline -> succeeds cleanly
    b_ok = PacedBackend(0.06)
    with client(config, b_ok) as (c, _), websocket(c) as ws:
        start(ws)
        for _ in range(3):
            ws.send_bytes(bytes(FRAME_BYTES))
        ws.send_json(FIXTURE["finish"])
        captions = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "finished":
                assert msg["accepted_samples"] == 3 * 3200
                break
            captions.append(msg)
        assert b_ok.received_frames == 3

    # Case 2: queue never drains within deadline -> overloaded
    monkeypatch.setattr(app, "QUEUE_TIMEOUT", 0.05)
    b_slow = PacedBackend(0.5)
    with client(config, b_slow) as (c, _), websocket(c) as ws:
        start(ws)
        ws.send_bytes(bytes(FRAME_BYTES))
        ws.send_bytes(bytes(FRAME_BYTES))
        ws.send_bytes(bytes(FRAME_BYTES))
        assert_error(ws, "overloaded", 1013)

    # Case 3: client disconnects while backpressured -> closes immediately without waiting for QUEUE_TIMEOUT
    monkeypatch.setattr(app, "QUEUE_TIMEOUT", 2.0)
    b_hang = PacedBackend(1.5)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        orig_port = config.server.port
        config.server.port = port
        srv = uvicorn.Server(
            uvicorn.Config(
                app.create_app(config, TOKEN, {"reazon-hy": b_hang}),
                log_level="error",
                ws="websockets-sansio",
            )
        )
        t = Thread(target=srv.run, kwargs={"sockets": [listener]}, daemon=True)
        t.start()
        dl = time.monotonic() + 3.0
        while not srv.started:
            assert time.monotonic() < dl
            time.sleep(0.01)

        async def _disconnect_under_backpressure():
            uri = f"ws://127.0.0.1:{port}/v1/captions"
            async with connect(uri, origin=ORIGIN, proxy=None) as ws:
                await ws.send(json.dumps(FIXTURE["start"]))
                json.loads(await ws.recv())
                for _ in range(3):
                    await ws.send(bytes(FRAME_BYTES))
                await asyncio.sleep(0.05)
            dl_close = time.monotonic() + 1.0
            while b_hang.closed != 1 and time.monotonic() < dl_close:
                await asyncio.sleep(0.01)
            assert b_hang.closed == 1

        try:
            t0 = time.monotonic()
            asyncio.run(_disconnect_under_backpressure())
            assert (time.monotonic() - t0) < 1.2
        finally:
            config.server.port = orig_port
            srv.should_exit = True
            t.join(4)
            assert not t.is_alive()
