"""Shared immutable contracts for Stage 8 streaming spans, MT requests, and telemetry events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VALID_TIME_KINDS = frozenset({"emission_approx", "aligned", "unknown"})
VALID_TRANSLATION_KINDS = frozenset({"preview", "final"})
VALID_STOP_REASONS = frozenset({"eos", "length_limit", "cancelled", "error"})


@dataclass(frozen=True)
class RequestKey:
    """Unique identity for a subtitle caption request within an epoch."""

    session_id: str
    epoch: int
    caption_id: str
    revision: int
    request_id: str

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        if self.epoch < 0 or self.revision < 0:
            raise ValueError("epoch and revision must be >= 0")
        if not self.caption_id or not self.request_id:
            raise ValueError("caption_id and request_id must be non-empty")


@dataclass(frozen=True)
class AudioSpan:
    """Left-closed right-open 16kHz audio interval [start, end) bounded by captured_end."""

    session_id: str
    epoch: int
    target_start: int
    target_end: int
    input_start: int
    input_end: int
    captured_end: int
    sample_rate: int = 16000

    def __post_init__(self) -> None:
        if self.sample_rate != 16000:
            raise ValueError(f"sample_rate must be 16000, got {self.sample_rate}")
        if self.epoch < 0:
            raise ValueError(f"epoch must be >= 0, got {self.epoch}")
        if self.target_start < 0 or self.target_end <= self.target_start:
            raise ValueError(
                f"invalid target span [{self.target_start}, {self.target_end})"
            )
        if self.input_start < 0 or self.input_start > self.target_start:
            raise ValueError(
                f"input_start ({self.input_start}) must be in [0, target_start={self.target_start}]"
            )
        if self.input_end < self.target_end:
            raise ValueError(
                f"input_end ({self.input_end}) must be >= target_end ({self.target_end})"
            )
        if self.input_end > self.captured_end:
            raise ValueError(
                f"future audio rejected: input_end ({self.input_end}) > captured_end ({self.captured_end})"
            )


@dataclass(frozen=True)
class TokenSpan:
    """Token with sample-axis timing provenance."""

    token_id: int | None
    text: str
    start_sample: int
    end_sample: int
    time_kind: str = "unknown"

    def __post_init__(self) -> None:
        if self.time_kind not in VALID_TIME_KINDS:
            raise ValueError(f"invalid time_kind: {self.time_kind}")
        if self.start_sample < 0 or self.end_sample < self.start_sample:
            raise ValueError(
                f"invalid token sample bounds [{self.start_sample}, {self.end_sample})"
            )


@dataclass(frozen=True)
class ASRHypothesis:
    """Structured ASR output tied to a causal AudioSpan and model identity."""

    span: AudioSpan
    text_ja: str
    model_key: str
    variant: str
    tokens: list[TokenSpan] | None = None
    timing: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Turn:
    """Committed prior source/target turn in the same conversation epoch."""

    source_ja: str
    target_zh: str


@dataclass(frozen=True)
class TranslationRequest:
    """MT request scoped to accepted history and server monotonic deadline."""

    key: RequestKey
    kind: str
    source_ja: str
    history: list[Turn]
    span: AudioSpan
    deadline_ns: int

    def __post_init__(self) -> None:
        if self.kind not in VALID_TRANSLATION_KINDS:
            raise ValueError(f"invalid translation kind: {self.kind}")
        if self.deadline_ns < 0:
            raise ValueError("deadline_ns must be >= 0")


@dataclass(frozen=True)
class TranslationResult:
    """MT generation result separating natural EOS from length truncation and cancellation."""

    text_zh: str
    stop_reason: str
    metrics: dict[str, Any] = field(default_factory=dict)
    valid: bool = False

    def __post_init__(self) -> None:
        if self.stop_reason not in VALID_STOP_REASONS:
            raise ValueError(f"invalid stop_reason: {self.stop_reason}")


@dataclass(frozen=True)
class Stage8Event:
    """Single domain-tagged Stage 8 telemetry event record."""

    schema_version: str
    event_id: str
    run_id: str
    experiment_id: str
    key: RequestKey | None
    parent_request_id: str | None
    kind: str
    event: str
    clock_domain: str
    ts_ns: int
    span: AudioSpan | None = None
    source_revision: int | None = None
    text: str | None = None
    text_hash: str | None = None
    model_key: str | None = None
    variant: str | None = None
    metrics: dict[str, Any] | None = None
    synthetic_fixture: bool = False
