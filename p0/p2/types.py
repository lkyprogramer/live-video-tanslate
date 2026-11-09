from dataclasses import dataclass, field


@dataclass(frozen=True)
class AudioSpan:
    clip_id: str
    session_id: str
    epoch: int
    start_sample: int
    end_sample: int
    accepted_samples: int
    pcm_path: str
    pcm_sha256: str

    def __post_init__(self):
        if not 0 <= self.start_sample < self.end_sample <= self.accepted_samples:
            raise ValueError("audio span exceeds accepted real samples")


@dataclass(frozen=True)
class SourceUnit:
    source_id: str
    span: AudioSpan
    text_ja: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TranslationResult:
    source_id: str
    raw_zh: str
    text_zh: str | None
    finish_reason: str
    metadata: dict = field(default_factory=dict)
