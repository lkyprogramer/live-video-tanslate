"""Tests for T1: Stage8Event validation and cross-domain clock anchor arithmetic."""

from __future__ import annotations

import pytest


def test_clock_domains_cannot_subtract_without_anchor():
    from p0.stage8_eval.events import cross_domain_delta_ms, estimate_clock_anchor, validate_event

    server_ev = {
        "schema_version": "stage8-event-v1",
        "synthetic_fixture": True,
        "event_id": "e1",
        "run_id": "r1",
        "experiment_id": "exp1",
        "key": {
            "session_id": "s1",
            "epoch": 0,
            "caption_id": "cap1",
            "revision": 0,
            "request_id": "req1",
        },
        "parent_request_id": None,
        "kind": "final",
        "event": "ws_send",
        "clock_domain": "server_monotonic",
        "ts_ns": 1_000_000_000,
        "span": None,
        "source_revision": 0,
        "text": "你好",
        "text_hash": "h1",
        "model_key": "qwen4b-q4",
        "variant": "b0",
        "metrics": {},
    }
    dom_ev = {
        **server_ev,
        "event_id": "e2",
        "event": "dom_visible",
        "clock_domain": "browser_performance",
        "ts_ns": 5_025_000_000,  # different epoch origin (+4s offset)
    }

    assert validate_event(server_ev) == []
    assert validate_event(dom_ev) == []

    # Same-domain subtraction works directly without an anchor
    server_ev2 = {**server_ev, "event_id": "e1b", "ts_ns": 1_045_000_000}
    assert cross_domain_delta_ms(server_ev, server_ev2) == pytest.approx(45.0)

    # Cross-domain subtraction without anchor MUST fail
    with pytest.raises(ValueError, match="anchor"):
        cross_domain_delta_ms(server_ev, dom_ev)

    # High-uncertainty anchor (>20ms) MUST also fail
    bad_anchor = estimate_clock_anchor(
        b0_ns=5_000_000_000,
        s1_ns=1_000_000_000,
        s2_ns=1_002_000_000,
        b3_ns=5_060_000_000,  # RTT = 60ms -> half-RTT uncertainty ~ 29ms > 20ms
    )
    assert bad_anchor["uncertainty_ms"] > 20.0
    with pytest.raises(ValueError, match="uncertainty"):
        cross_domain_delta_ms(server_ev, dom_ev, anchor=bad_anchor)

    # Accurate anchor: server_ns - browser_ns == -4_000_000_000ns
    good_anchor = estimate_clock_anchor(
        b0_ns=5_000_000_000,
        s1_ns=1_001_000_000,
        s2_ns=1_001_000_000,
        b3_ns=5_002_000_000,
    )
    assert good_anchor["uncertainty_ms"] <= 2.0
    # dom_ev in server domain is 5_025_000_000 + (-4_000_000_000) = 1_025_000_000 -> delta = 25ms
    assert cross_domain_delta_ms(server_ev, dom_ev, anchor=good_anchor) == pytest.approx(25.0)


def test_validate_event_rejects_future_audio_span_and_invalid_clock_domain():
    from p0.stage8_eval.events import validate_event

    bad_ev = {
        "schema_version": "stage8-event-v1",
        "synthetic_fixture": True,
        "event_id": "e1",
        "run_id": "r1",
        "experiment_id": "exp1",
        "key": None,
        "parent_request_id": None,
        "kind": "final",
        "event": "asr_end",
        "clock_domain": "date_now",
        "ts_ns": 1000,
        "span": {
            "target_start": 0,
            "target_end": 16000,
            "input_start": 0,
            "input_end": 20000,
            "captured_end": 16000,
            "sample_rate": 16000,
        },
        "source_revision": 0,
        "text": "はい",
        "text_hash": "h",
        "model_key": "nemo",
        "variant": "b0",
        "metrics": {},
    }
    errs = validate_event(bad_ev)
    assert any("clock_domain" in e for e in errs)
    assert any("captured_end" in e for e in errs)
