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
from subtitle_service.backend import Caption
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

    async def open(self, emit):
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
