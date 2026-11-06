import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import json
import logging
import re
import unicodedata
from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from .backend import BackendError, Caption, SubtitleBackend, Warning
from .config import Settings

log = logging.getLogger(__name__)
FRAME_BYTES = 6400
QUEUE_FRAMES = 20
QUEUE_TIMEOUT = 1
DRAIN_TIMEOUT = 8
CLOSE_CODES = {
    "unauthorized": 4401,
    "invalid_message": 4400, "unsupported_version": 4400,
    "invalid_audio": 4400, "unknown_profile": 4400,
    "busy": 1013, "overloaded": 1013,
}
AUDIO = {"format": "s16le", "sample_rate": 16000, "channels": 1}
KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
HAN = re.compile(r"[\u3400-\u9fff\U00020000-\U0002fa1f]")


def control(text):
    if text is None or len(text) > 4096:
        raise BackendError("invalid_message")
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        raise BackendError("invalid_message") from None
    if not isinstance(obj, dict):
        raise BackendError("invalid_message")
    return obj


def matches(secret, supplied):
    return isinstance(supplied, str) and supplied.isascii() and hmac.compare_digest(secret, supplied)


def chinese(text):
    value = unicodedata.normalize("NFKC", text).strip()
    if not HAN.search(value) or KANA.search(value) or "hy_place" in value or "<|" in value:
        return None
    return text.strip()


class Connection:
    def __init__(self, ws):
        self.ws = ws
        self.session_id = None
        self.accepted = 0
        self.seq = 0
        self.last_end = 0
        self.active = False
        self.write_lock = asyncio.Lock()
        self.failure = asyncio.get_running_loop().create_future()

    async def send(self, type_, **data):
        if self.session_id is not None:
            data["session_id"] = self.session_id
        await self.ws.send_json({"type": type_, **data})

    async def emit(self, item):
        async with self.write_lock:
            if not self.active:
                return
            if isinstance(item, BackendError):
                if not self.failure.done():
                    self.failure.set_result(item)
                return
            if isinstance(item, Warning):
                await self.send("warning", code=item.code)
                return
            text = chinese(item.text_zh)
            if text is None:
                await self.send("warning", code="translation_invalid")
                return
            if not (0 <= item.start_sample < item.end_sample <= self.accepted and item.end_sample >= self.last_end):
                await self.send("warning", code="adapter_unaligned")
                return
            self.seq += 1
            self.last_end = item.end_sample
            await self.send("caption", seq=self.seq, start_sample=item.start_sample,
                            end_sample=item.end_sample, text_zh=text)

    async def stream(self, session):
        queue = asyncio.Queue(maxsize=QUEUE_FRAMES)
        finishing = asyncio.Event()

        async def admit(frame):
            await queue.put(frame)
            self.accepted += len(frame) // 2

        async def read():
            short_tail = False
            following = None
            try:
                while True:
                    message = await following if following else await self.ws.receive()
                    following = None
                    if message["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(message["code"])
                    if finishing.is_set():
                        raise BackendError("invalid_message")
                    frame = message.get("bytes")
                    if frame is None:
                        if control(message.get("text")) != {"type": "finish"}:
                            raise BackendError("invalid_message")
                        finishing.set()
                        if queue.empty():
                            queue.put_nowait(None)
                        continue
                    if short_tail or not 2 <= len(frame) <= FRAME_BYTES or len(frame) % 2:
                        raise BackendError("invalid_audio")
                    short_tail = len(frame) < FRAME_BYTES
                    if not queue.full():
                        await admit(frame)
                        continue
                    # Keep receiving close notifications even while admission is blocked.
                    pending = asyncio.create_task(admit(frame))
                    following = asyncio.create_task(self.ws.receive())
                    try:
                        done, _ = await asyncio.wait(
                            (pending, following), timeout=QUEUE_TIMEOUT,
                            return_when=asyncio.FIRST_COMPLETED)
                        if following in done and following.result()["type"] == "websocket.disconnect":
                            raise WebSocketDisconnect(following.result()["code"])
                        if pending not in done:
                            raise BackendError("overloaded")
                        pending.result()
                    finally:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            finally:
                if following is not None:
                    following.cancel()
                    await asyncio.gather(following, return_exceptions=True)

        async def process():
            while not (finishing.is_set() and queue.empty()):
                frame = await queue.get()
                if frame is None:
                    break
                await session.accept_pcm(frame)
            await session.finish()

        async def deadline():
            await finishing.wait()
            await asyncio.sleep(DRAIN_TIMEOUT)
            raise BackendError("drain_timeout")

        async def watch_backend():
            raise await self.failure

        tasks = [asyncio.create_task(task()) for task in (read, deadline, watch_backend, process)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                if task in done:
                    task.result()
        finally:
            self.active = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def create_app(config: Settings, token: str, backends: dict[str, SubtitleBackend]) -> FastAPI:
    state = "starting"
    leased = False
    host = f"127.0.0.1:{config.server.port}"
    origin = config.server.allowed_origin

    async def warmup():
        nonlocal state
        try:
            await backends[config.default_profile_id].warmup()
        except Exception:
            state = "unavailable"
            log.error("default_backend_warmup_failed")
        else:
            state = "ready"

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(warmup())
        try:
            yield
        finally:
            await task
            for backend in backends.values():
                await backend.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=[origin], allow_methods=["GET"],
                       allow_headers=["Authorization"])

    @app.middleware("http")
    async def restrict(request: Request, next_handler):
        if request.headers.getlist("host") != [host] or request.headers.getlist("origin") not in ([], [origin]):
            return Response(status_code=403)
        return await next_handler(request)

    @app.get("/v1/health")
    async def health():
        return JSONResponse({"status": state}, status_code=200 if state == "ready" else 503)

    @app.get("/v1/profiles")
    async def profiles(request: Request):
        if not matches(f"Bearer {token}", request.headers.get("authorization")):
            return JSONResponse({"code": "unauthorized"}, status_code=401)
        entries = []
        for key, profile in config.profiles.items():
            if profile.enabled:
                available = (state == "ready" or key != config.default_profile_id) and await backends[key].available()
                entries.append({"id": key, "label": profile.label, "available": available})
        return {"default_profile_id": config.default_profile_id, "profiles": entries}

    @app.websocket("/v1/captions")
    async def captions(ws: WebSocket):
        nonlocal leased
        if ws.headers.getlist("host") != [host] or ws.headers.getlist("origin") != [origin]:
            await ws.close(code=1008)
            return
        await ws.accept()
        connection = Connection(ws)
        owns_lease = False
        session = None
        try:
            try:
                message = await asyncio.wait_for(ws.receive(), 2)
            except TimeoutError:
                raise BackendError("invalid_message") from None
            if message["type"] == "websocket.disconnect":
                return
            start = control(message.get("text"))
            if start.keys() != {"type", "version", "profile_id", "token", "audio"} or start["type"] != "start":
                raise BackendError("invalid_message")
            if not matches(token, start["token"]):
                raise BackendError("unauthorized")
            if type(start["version"]) is not int or start["version"] != 1:
                raise BackendError("unsupported_version")
            audio = start["audio"]
            if audio != AUDIO or any(type(audio[key]) is not int for key in ("sample_rate", "channels")):
                raise BackendError("invalid_audio")
            key = start["profile_id"]
            if not isinstance(key, str) or key not in config.profiles:
                raise BackendError("unknown_profile")
            if not config.profiles[key].enabled:
                raise BackendError("profile_unavailable", "disabled")
            if state != "ready" and key == config.default_profile_id:
                raise BackendError("profile_unavailable", "not_ready")
            if leased:
                raise BackendError("busy")
            leased = owns_lease = True
            async with asyncio.timeout(18):
                session = await backends[key].open(connection.emit)
            connection.session_id = uuid4().hex
            await connection.send("ready", version=1, profile_id=key, language="zh-Hans")
            connection.active = True
            await connection.stream(session)
            if connection.failure.done() and not connection.failure.cancelled():
                raise connection.failure.result()
            await connection.send("finished", accepted_samples=connection.accepted)
            await ws.close()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            error = exc if isinstance(exc, BackendError) else BackendError()
            log.info("session_end code=%s", error.code)
            data = {"code": error.code}
            if error.reason:
                data["reason"] = error.reason
            with suppress(WebSocketDisconnect, OSError, RuntimeError):
                await connection.send("error", **data)
                await ws.close(code=CLOSE_CODES.get(error.code, 1011))
        finally:
            connection.active = False
            try:
                if session is not None:
                    await session.aclose()
            finally:
                if owns_lease:
                    leased = False

    return app
