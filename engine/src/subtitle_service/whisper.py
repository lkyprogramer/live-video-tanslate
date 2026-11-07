import asyncio
from dataclasses import dataclass
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .backend import BackendError, Caption, Warning

TIME = re.compile(r"(\d+):([0-5]\d):([0-5]\d)\.(\d\d)")


def samples(value):
    if not isinstance(value, str) or not (m := TIME.fullmatch(value)):
        raise ValueError("invalid upstream timestamp")
    h, m_, s, hundredths = map(int, m.groups())
    return ((h * 3600 + m_ * 60 + s) * 100 + hundredths) * 160


@dataclass(frozen=True)
class Row:
    start: int
    end: int
    text: str


class Snapshot:
    """Track the last aligned window and emit only text past its retained suffix."""

    def __init__(self):
        self.previous: list[Row] = []
        self.end = 0

    @staticmethod
    def parse(lines, accepted):
        rows = []
        for line in lines:
            text = line.get("translation")
            if line.get("speaker") == -2 or not text or not (text := " ".join(text.split())):
                continue
            start = min(samples(line.get("start")), accepted)
            end = min(samples(line.get("end")), accepted)
            if start < end:
                rows.append(Row(start, end, text))
        return rows

    def update(self, lines, accepted):
        try:
            current = self.parse(lines, accepted)
        except (TypeError, AttributeError, ValueError):
            return [Warning("adapter_unaligned")]
        if not current:
            return []
        text = " ".join(row.text for row in current)
        old = " ".join(row.text for row in self.previous)
        cutoff = 0
        if self.previous and any(row.start < self.end for row in current):
            if old == text or old.endswith(text):
                # Keep the confirmed watermark even if the retained window shrinks.
                return []
            limit = min(len(old), len(text))
            cutoff = next((n for n in range(limit, 0, -1) if old.endswith(text[:n])), 0)
            # Short overlaps are meaningful only when the entire old text is short.
            if cutoff < min(len(old), 4):
                return [Warning("adapter_unaligned")]
            offset = 0
            for row in current:
                offset += len(row.text) + 1
                if offset >= cutoff:
                    if row.end < self.end:
                        return [Warning("adapter_unaligned")]
                    break
        events = []
        offset = 0
        for row in current:
            if offset + len(row.text) > cutoff and row.end >= self.end:
                new = row.text[max(0, cutoff - offset):].strip()
                if new:
                    events.append(Caption(row.start, row.end, new))
                    self.end = row.end
            offset += len(row.text) + 1
        if events:
            self.previous = current
        return events


class WhisperBackend:
    def __init__(self, profile):
        self.url = profile.url
        self.health_url = "http://" + urlsplit(self.url).netloc + "/health"
        self.token_env = profile.token_env

    def healthy(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.health_url, timeout=1) as response:
                return json.load(response).get("ready") is True
        except (OSError, ValueError):
            return False

    async def available(self):
        return bool(os.environ.get(self.token_env)) and await asyncio.to_thread(self.healthy)

    async def warmup(self):
        if not await self.available():
            raise BackendError("profile_unavailable", "upstream_unreachable")

    async def open(self, emit, *, record=None):
        await self.warmup()
        ws = None
        try:
            ws = await connect(self.url + "?mode=full&language=ja&target_language=zh-cn",
                               additional_headers={"Authorization": f"Bearer {os.environ[self.token_env]}"},
                               proxy=None, open_timeout=4, close_timeout=1)
            config = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if config.get("type") != "config" or config.get("useAudioWorklet") is not True or config.get("mode") != "full":
                raise BackendError()
        except BaseException as exc:
            if ws is not None:
                await ws.close()
            if isinstance(exc, (OSError, TimeoutError, WebSocketException)):
                raise BackendError("profile_unavailable", "upstream_unreachable") from None
            raise
        return WhisperSession(ws, emit)

    async def aclose(self):
        pass


class WhisperSession:
    def __init__(self, ws, emit):
        self.ws = ws
        self.emit = emit
        self.snapshot = Snapshot()
        self.accepted = 0
        self.finishing = False
        self.eof = False
        self.reader = asyncio.create_task(self.listen())

    async def listen(self):
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                if event.get("type") == "ready_to_stop":
                    if not self.finishing:
                        raise BackendError()
                    self.eof = True
                    return
                if event.get("type") == "error" or event.get("status") == "error" or event.get("translation_error"):
                    raise BackendError()
                for caption in self.snapshot.update(event.get("lines", []), self.accepted):
                    await self.emit(caption)
        except Exception:
            pass
        await self.emit(BackendError())

    async def accept_pcm(self, frame):
        self.accepted += len(frame) // 2
        await self.ws.send(frame)

    async def finish(self):
        self.finishing = True
        await self.ws.send(b"")
        await asyncio.shield(self.reader)
        if not self.eof:
            raise BackendError()

    async def aclose(self):
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        await self.ws.close()
