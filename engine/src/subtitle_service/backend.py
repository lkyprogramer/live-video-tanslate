from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Caption:
    start_sample: int
    end_sample: int
    text_zh: str


@dataclass(frozen=True)
class Warning:
    code: str


class BackendError(Exception):
    def __init__(self, code="backend_error", reason=None):
        self.code = code
        self.reason = reason
        super().__init__(code)


CaptionSink = Callable[[Caption | Warning | BackendError], Awaitable[None]]


class SubtitleSession(Protocol):
    async def accept_pcm(self, frame: bytes) -> None: ...
    async def finish(self) -> None: ...
    async def aclose(self) -> None: ...


class SubtitleBackend(Protocol):
    async def warmup(self) -> None: ...
    async def available(self) -> bool: ...
    async def open(self, emit: CaptionSink) -> SubtitleSession: ...
    async def aclose(self) -> None: ...
