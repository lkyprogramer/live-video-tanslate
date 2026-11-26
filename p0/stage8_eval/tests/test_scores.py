"""Tests for T1: LCP erased_chars, preview/final separation, cer_v2, missing denominator, and dual-layer ASR."""

from __future__ import annotations

import math
import pytest


def test_append_is_not_retraction():
    from p0.stage8_eval.score import erased_chars

    assert erased_chars("今天", "今天天气很好") == 0


def test_replacement_is_counted():
    from p0.stage8_eval.score import erased_chars

    assert erased_chars("今天", "昨天") == 2


def test_cer_keeps_long_vowel_mark():
    from p0.stage8_eval.score import score_asr

    # Normalizer cer_v2 MUST preserve Japanese long vowel mark ー (U+30FC)
    v2 = score_asr("ラーメン", "ラメン", normalizer="cer_v2")
    assert v2["D"] == 1
    assert v2["N"] == 4
    assert v2["dist"] == 1
    assert v2["cer"] == pytest.approx(0.25)

    # Legacy normalizer stripped ー and missed this deletion
    legacy = score_asr("ラーメン", "ラメン", normalizer="legacy_cer")
    assert legacy["dist"] == 0
    assert legacy["cer"] == pytest.approx(0.0)


def test_preview_not_final_stream():
    from p0.stage8_eval.score import summarize_run

    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 32000, "reference_ja": "今日は晴れ", "eligible": True},
    ]
    # 44 preview deltas + 59 final_token_stream deltas = 103 delta events, kept strictly separate!
    events = []
    t = 1000
    for i in range(44):
        events.append({
            "kind": "preview",
            "event": "ws_send",
            "clock_domain": "server_monotonic",
            "ts_ns": t + i * 10,
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": i, "request_id": f"p{i}"},
            "text": "今天",
        })
    for j in range(59):
        events.append({
            "kind": "final",
            "event": "final_token_stream",
            "clock_domain": "server_monotonic",
            "ts_ns": t + 1000 + j * 10,
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": j, "request_id": f"f{j}"},
            "text": "今天天气真不错",
        })

    summary = summarize_run(events, units)
    assert summary["deltas"]["preview_count"] == 44
    assert summary["deltas"]["final_token_stream_count"] == 59


def test_missing_output_stays_in_denominator():
    from p0.stage8_eval.score import nearest_rank_quantile, summarize_run

    # Unit u2 has no DOM output -> must remain in eligible_n=2, missing_n=1, p95_ms=None (+inf)
    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 16000, "reference_ja": "はい", "eligible": True},
        {"unit_id": "u2", "start_sample": 32000, "end_sample": 48000, "reference_ja": "ありがとう", "eligible": True},
    ]
    events = [
        {
            "kind": "final",
            "event": "dom_visible",
            "clock_domain": "browser_performance",
            "ts_ns": 1_500_000_000,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": 0, "request_id": "r1"},
            "text": "是的",
            "metrics": {"final_visible_latency_ms": 500.0, "useful": True, "first_useful_latency_ms": 500.0},
        },
        {
            "kind": "final",
            "event": "resolved_failure",
            "clock_domain": "server_monotonic",
            "ts_ns": 4_000_000_000,
            "unit_id": "u2",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap2", "revision": 0, "request_id": "r2"},
            "text": "",
            "metrics": {"outcome": "timeout"},
        },
    ]

    assert nearest_rank_quantile([500.0, None], 0.50) == pytest.approx(500.0)
    assert nearest_rank_quantile([500.0, None], 0.95) is None

    summary = summarize_run(events, units)
    assert summary["units"]["eligible_units"] == 2
    assert summary["units"]["final_visible_units"] == 1
    assert summary["units"]["missing_units"] == 1
    assert summary["latency"]["unit_end_final_dom"]["p50_ms"] == pytest.approx(500.0)
    assert summary["latency"]["unit_end_final_dom"]["p95_ms"] is None
    assert summary["latency"]["unit_end_final_dom"]["success_only_p95_ms"] == pytest.approx(500.0)


def test_asr_raw_not_filtered_by_mt():
    from p0.stage8_eval.score import summarize_run

    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 16000, "reference_ja": "おはようございます", "eligible": True},
    ]
    # Gateway rejects MT output (kana leak), yet raw ASR AND selected ASR MUST still be scored!
    events = [
        {
            "kind": "final",
            "event": "asr_end",
            "clock_domain": "server_monotonic",
            "ts_ns": 1000,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "c1", "revision": 0, "request_id": "r1"},
            "text": "おはようございます",
            "metrics": {"raw_text_ja": "おはようございます", "selected_text_ja": "おはようございます"},
        },
        {
            "kind": "final",
            "event": "resolved_failure",
            "clock_domain": "server_monotonic",
            "ts_ns": 2000,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "c1", "revision": 0, "request_id": "r1"},
            "text": "",
            "metrics": {"outcome": "rejected", "code": "translation_invalid"},
        },
    ]
    summary = summarize_run(events, units)
    assert summary["asr"]["asr_raw"]["cer"] == pytest.approx(0.0)
    assert summary["asr"]["asr_selected"]["cer"] == pytest.approx(0.0)
    assert summary["asr"]["dom_backed_source"]["hyp_len"] == 0
    assert summary["units"]["final_visible_units"] == 0
    assert summary["units"]["missing_units"] == 1


def test_short_reply_has_own_unit():
    from p0.stage8_eval.score import summarize_run

    # <= 4 chars short replies ("はい", "うん") MUST stay in unit denominator
    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 8000, "reference_ja": "はい", "eligible": True},
        {"unit_id": "u2", "start_sample": 10000, "end_sample": 18000, "reference_ja": "うん", "eligible": True},
    ]
    events = [
        {
            "kind": "final",
            "event": "asr_end",
            "clock_domain": "server_monotonic",
            "ts_ns": 500,
            "unit_id": "u1",
            "text": "はい",
            "metrics": {"raw_text_ja": "はい", "selected_text_ja": "はい"},
        },
    ]
    summary = summarize_run(events, units)
    assert summary["units"]["eligible_units"] == 2
    assert summary["asr"]["asr_raw"]["N"] == 4
    assert summary["asr"]["asr_raw"]["D"] == 2


def test_synthetic_replay_hand_calculated_metrics():
    from p0.stage8_eval.score import summarize_run

    # Hand-calculated fixture:
    # cap1 (0..60s window = 1.0 min):
    #   "今天" -> "今天天气" (append: erased=0)
    #   "今天天气" -> "昨天天气" (retraction: LCP="今天天气"/"昨天天气"=0? Wait LCP is 0 -> len("今天天气")-0 = 4!)
    #   "昨天天气" -> final "昨天天气很好" (append: erased=0)
    #   dwell_expired remove -> MUST NOT add erasure!
    # cap2: early replace removed early (remove_reason="replaced") with visible text "错了" -> erased += 2
    # Total erased = 4 + 2 = 6 over 60s = 6.0 erased_chars_per_min!
    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 16000, "reference_ja": "昨日は晴れ", "eligible": True},
        {"unit_id": "u2", "start_sample": 20000, "end_sample": 36000, "reference_ja": "はい", "eligible": True},
    ]
    events = [
        {
            "kind": "preview",
            "event": "dom_visible",
            "clock_domain": "browser_performance",
            "ts_ns": 0,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": 0, "request_id": "r1"},
            "text": "今天",
            "metrics": {},
        },
        {
            "kind": "preview",
            "event": "dom_update",
            "clock_domain": "browser_performance",
            "ts_ns": 10_000_000_000,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": 1, "request_id": "r2"},
            "text": "今天天气",
            "metrics": {},
        },
        {
            "kind": "preview",
            "event": "dom_update",
            "clock_domain": "browser_performance",
            "ts_ns": 20_000_000_000,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": 2, "request_id": "r3"},
            "text": "昨天天气",
            "metrics": {},
        },
        {
            "kind": "final",
            "event": "dom_update",
            "clock_domain": "browser_performance",
            "ts_ns": 30_000_000_000,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": 3, "request_id": "r4"},
            "text": "昨天天气很好",
            "metrics": {"final_visible_latency_ms": 800.0},
        },
        {
            "kind": "final",
            "event": "dom_remove",
            "clock_domain": "browser_performance",
            "ts_ns": 60_000_000_000,
            "unit_id": "u1",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap1", "revision": 4, "request_id": "r4"},
            "text": "",
            "metrics": {"remove_reason": "dwell_expired"},
        },
        {
            "kind": "final",
            "event": "dom_visible",
            "clock_domain": "browser_performance",
            "ts_ns": 15_000_000_000,
            "unit_id": "u2",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap2", "revision": 0, "request_id": "r5"},
            "text": "错了",
            "metrics": {"final_visible_latency_ms": 400.0},
        },
        {
            "kind": "final",
            "event": "dom_remove",
            "clock_domain": "browser_performance",
            "ts_ns": 16_000_000_000,
            "unit_id": "u2",
            "key": {"session_id": "s1", "epoch": 0, "caption_id": "cap2", "revision": 1, "request_id": "r5"},
            "text": "",
            "metrics": {"remove_reason": "replaced"},
        },
    ]

    summary = summarize_run(events, units, duration_s=60.0)
    assert summary["flicker"]["total_erased_chars"] == 6
    assert summary["flicker"]["erased_chars_per_min"] == pytest.approx(6.0)
    assert summary["flicker"]["captions_with_retraction"] == 2
    assert summary["flicker"]["captions_total"] == 2
