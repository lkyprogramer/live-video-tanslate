"""T4 production AudioSpan ownership and timestamp attribution helpers (no p0 imports)."""

from __future__ import annotations

from .quality_types import AudioSpan, TokenSpan

RATE = 16000
NEMO_SUBSAMPLE_STRIDE_SAMPLES = 1280  # 80ms frame stride at 16kHz


def validate_span(span: AudioSpan) -> None:
    """Validate causal left-closed right-open 16kHz AudioSpan bounds."""
    if span.sample_rate != RATE:
        raise ValueError(f"sample_rate must be {RATE}, got {span.sample_rate}")
    if span.epoch < 0:
        raise ValueError(f"epoch must be >= 0, got {span.epoch}")
    if span.target_start < 0 or span.target_end <= span.target_start:
        raise ValueError(f"invalid target span [{span.target_start}, {span.target_end})")
    if span.input_start < 0 or span.input_start > span.target_start:
        raise ValueError(
            f"input_start ({span.input_start}) must be in [0, target_start={span.target_start}]"
        )
    if span.input_end < span.target_end:
        raise ValueError(
            f"input_end ({span.input_end}) must be >= target_end ({span.target_end})"
        )
    if span.input_end > span.captured_end:
        raise ValueError(
            f"future audio rejected: input_end ({span.input_end}) > captured_end ({span.captured_end})"
        )


def nemo_frame_to_sample(input_start: int, frame: int, pad_samples: int) -> int:
    """Map NeMo FastConformer emission frame index onto session 16kHz sample axis."""
    return int(input_start) + int(frame) * NEMO_SUBSAMPLE_STRIDE_SAMPLES - int(pad_samples)


def right_collar_delay_ms(span: AudioSpan) -> float:
    """Charge any real future right-collar samples past target_end explicitly to delay."""
    validate_span(span)
    return round(max(0, span.input_end - span.target_end) / (RATE / 1000.0), 4)


def owned_tokens(
    tokens: list[TokenSpan],
    span: AudioSpan,
    committed_until: int,
) -> list[TokenSpan]:
    """Filter tokens strictly owned by [max(target_start, committed_until), target_end).

    Refuses to guess-trim tokens with time_kind == 'unknown' whenever input bounds
    exceed the uncommitted target interval.
    """
    validate_span(span)
    if committed_until < 0:
        raise ValueError(f"committed_until must be >= 0, got {committed_until}")

    effective_start = max(span.target_start, committed_until)
    needs_trim = (
        span.input_start < effective_start
        or span.input_end > span.target_end
        or committed_until > span.target_start
    )

    if any(tok.time_kind == "unknown" for tok in tokens):
        if needs_trim:
            raise ValueError(
                "cannot trim tokens with unknown timestamps across context/collar boundary"
            )
        return list(tokens)

    return [
        tok
        for tok in tokens
        if effective_start <= tok.start_sample < span.target_end
    ]
