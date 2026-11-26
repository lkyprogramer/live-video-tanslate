"""Tests for T4: causal AudioSpan validation, token ownership filtering, and collar accounting."""

from __future__ import annotations

import pytest

from subtitle_service.quality_types import AudioSpan, TokenSpan


def test_context_not_committed_twice():
    from subtitle_service.audio_span_contract import owned_tokens

    span = AudioSpan(
        session_id="s1",
        epoch=0,
        target_start=16000,
        target_end=32000,
        input_start=0,
        input_end=32000,
        captured_end=32000,
    )
    tokens = [
        TokenSpan(1, "昨日", 4000, 5280, "emission_approx"),
        TokenSpan(2, "今日", 16000, 17280, "emission_approx"),
        TokenSpan(3, "晴れ", 24000, 25280, "emission_approx"),
        TokenSpan(4, "明日", 32000, 33280, "emission_approx"),
    ]
    kept = owned_tokens(tokens, span, committed_until=16000)
    assert [t.text for t in kept] == ["今日", "晴れ"]

    # If target [16000, 20000) was already committed up to 20000, overlap must not re-commit "今日"
    kept2 = owned_tokens(tokens, span, committed_until=20000)
    assert [t.text for t in kept2] == ["晴れ"]


def test_future_pcm_rejected():
    from subtitle_service.audio_span_contract import validate_span

    with pytest.raises(ValueError, match="captured_end"):
        AudioSpan(
            session_id="s1",
            epoch=0,
            target_start=0,
            target_end=16000,
            input_start=0,
            input_end=21120,
            captured_end=16000,
        )

    bad = object.__new__(AudioSpan)
    object.__setattr__(bad, "session_id", "s1")
    object.__setattr__(bad, "epoch", 0)
    object.__setattr__(bad, "target_start", 0)
    object.__setattr__(bad, "target_end", 16000)
    object.__setattr__(bad, "input_start", 0)
    object.__setattr__(bad, "input_end", 20000)
    object.__setattr__(bad, "captured_end", 16000)
    object.__setattr__(bad, "sample_rate", 16000)
    with pytest.raises(ValueError, match="captured_end"):
        validate_span(bad)


def test_unknown_timestamps_cannot_trim_by_guess():
    from subtitle_service.audio_span_contract import owned_tokens

    span_with_ctx = AudioSpan(
        session_id="s1",
        epoch=0,
        target_start=16000,
        target_end=32000,
        input_start=0,
        input_end=32000,
        captured_end=32000,
    )
    unknown_tokens = [TokenSpan(None, "昨日今日", 0, 32000, "unknown")]
    with pytest.raises(ValueError, match="unknown"):
        owned_tokens(unknown_tokens, span_with_ctx, committed_until=16000)

    # Whole-target span without trimming (input == target and committed_until <= target_start) is allowed
    exact_target = AudioSpan(
        session_id="s1",
        epoch=0,
        target_start=16000,
        target_end=32000,
        input_start=16000,
        input_end=32000,
        captured_end=32000,
    )
    assert [t.text for t in owned_tokens(unknown_tokens, exact_target, committed_until=16000)] == ["昨日今日"]


def test_right_collar_is_charged_to_delay():
    from subtitle_service.audio_span_contract import right_collar_delay_ms

    span = AudioSpan(
        session_id="s1",
        epoch=0,
        target_start=16000,
        target_end=48000,
        input_start=0,
        input_end=48000 + 5120,  # +320ms real future collar
        captured_end=48000 + 5120,
    )
    assert right_collar_delay_ms(span) == pytest.approx(320.0)


def test_short_repeated_reply_not_deduped_across_speakers():
    from subtitle_service.audio_span_contract import owned_tokens

    span_a = AudioSpan("s1", 0, 0, 16000, 0, 16000, 16000)
    span_b = AudioSpan("s1", 0, 16000, 32000, 0, 32000, 32000)
    tok_a = owned_tokens([TokenSpan(1, "はい", 4000, 5280, "emission_approx")], span_a, committed_until=0)
    tok_b = owned_tokens(
        [
            TokenSpan(1, "はい", 4000, 5280, "emission_approx"),
            TokenSpan(1, "はい", 20000, 21280, "emission_approx"),
        ],
        span_b,
        committed_until=16000,
    )
    assert [t.text for t in tok_a] == ["はい"]
    assert [t.text for t in tok_b] == ["はい"]
