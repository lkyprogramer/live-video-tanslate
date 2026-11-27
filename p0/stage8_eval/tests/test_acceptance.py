"""Tests for Stage 8 T7 acceptance gate evaluator."""

from __future__ import annotations

import json
from pathlib import Path

from p0.stage8_eval.acceptance import evaluate_gates

THRESHOLDS = json.loads(
    Path(
        "/Users/luo/Documents/github/jp-live-video-tanslate/"
        "docs/live_video_translate_stage8_plan_20261126/templates/thresholds.proposed.json"
    ).read_text(encoding="utf-8")
)


def _passing_report() -> dict:
    return {
        "thresholds_sha256": "th123",
        "roles": {
            "subtitles_off": {"media_sha256": "m1", "thresholds_sha256": "th123", "drop_rate_pct": 4.0},
            "deployed": {
                "media_sha256": "m1",
                "thresholds_sha256": "th123",
                "dom_events": {"total": 20, "streaming": 10, "final": 10},
                "availability": {"final_visible_rate": 0.99, "first_useful_rate": 0.96, "final_by_2000ms_rate": 0.97},
                "dom_latency": {"unit_end_to_final_dom_p95_ms": 900.0},
            },
            "candidate": {
                "media_sha256": "m1",
                "thresholds_sha256": "th123",
                "swap_growth_mib": 0.0,
                "resource_abort": False,
                "lifecycle": {
                    "new_disconnects": 0,
                    "new_overloads": 0,
                    "old_epoch_commits": 0,
                    "silent_final_drops": 0,
                    "out_of_order_final_accepts": 0,
                },
                "dom_events": {"total": 20, "streaming": 8, "final": 12},
                "availability": {"final_visible_rate": 0.99, "first_useful_rate": 0.97, "final_by_2000ms_rate": 0.97},
                "dom_latency": {"unit_end_to_final_dom_p95_ms": 920.0},
            },
        },
        "comparison": {"drop_rate_delta_vs_subtitles_off_pp": 0.4},
        "holdout": {
            "reuse_count": 1,
            "critical_error_relative_reduction": 0.30,
            "paired_bootstrap": {"n": 12, "mean": 0.30, "ci95_low": 0.05, "ci95_high": 0.55},
        },
        "human_review": {
            "verified": True,
            "protected_new_e4": 0,
            "protected_key_info_regressions": 0,
        },
    }


def test_passing_report_gets_pass():
    out = evaluate_gates(_passing_report(), THRESHOLDS)
    assert out["verdict"] == "PASS"
    assert out["stability_verdict"] == "PASS"


def test_missing_latency_is_infinite():
    rep = _passing_report()
    rep["roles"]["candidate"]["dom_latency"]["unit_end_to_final_dom_p95_ms"] = None
    out = evaluate_gates(rep, THRESHOLDS)
    assert out["verdict"] == "FAIL"
    assert out["gates"]["g2_availability_and_latency"]["status"] == "FAIL"


def test_no_baseline_cherry_pick():
    rep = _passing_report()
    del rep["roles"]["deployed"]
    rep["roles"]["corrected"] = dict(rep["roles"]["candidate"])
    out = evaluate_gates(rep, THRESHOLDS)
    assert out["verdict"] != "PASS"
    assert out["gates"]["g0_identity_and_dom"]["status"] == "BLOCKED"


def test_holdout_used_once():
    rep = _passing_report()
    rep["holdout"]["reuse_count"] = 2
    out = evaluate_gates(rep, THRESHOLDS)
    assert out["verdict"] == "FAIL"
    assert out["gates"]["g4_quality_and_review"]["status"] == "FAIL"


def test_candidate_resource_abort_is_fail_not_blocked():
    rep = _passing_report()
    rep["roles"]["candidate"]["resource_abort"] = True
    out = evaluate_gates(rep, THRESHOLDS)
    assert out["verdict"] == "FAIL"
    assert out["gates"]["g3_resources"]["status"] == "FAIL"


def test_missing_human_review_or_holdout_or_dom_cannot_pass():
    rep_no_review = _passing_report()
    rep_no_review["human_review"] = None
    out1 = evaluate_gates(rep_no_review, THRESHOLDS)
    assert out1["verdict"] == "INCONCLUSIVE"
    assert "QUALITY_REVIEW_MISSING" in out1["gates"]["g4_quality_and_review"]["reasons"]

    rep_no_holdout = _passing_report()
    rep_no_holdout["holdout"] = None
    out2 = evaluate_gates(rep_no_holdout, THRESHOLDS)
    assert out2["verdict"] == "BLOCKED"

    rep_no_dom = _passing_report()
    rep_no_dom["roles"]["candidate"]["dom_events"] = {"total": 0, "streaming": 0, "final": 0}
    out3 = evaluate_gates(rep_no_dom, THRESHOLDS)
    assert out3["verdict"] != "PASS"
    assert out3["gates"]["g0_identity_and_dom"]["status"] == "BLOCKED"


def test_swap_or_protected_e4_fails_gate():
    rep_swap = _passing_report()
    rep_swap["roles"]["candidate"]["swap_growth_mib"] = 300.0
    assert evaluate_gates(rep_swap, THRESHOLDS)["verdict"] == "FAIL"

    rep_e4 = _passing_report()
    rep_e4["human_review"]["protected_new_e4"] = 1
    assert evaluate_gates(rep_e4, THRESHOLDS)["verdict"] == "FAIL"


def test_all_timeouts_filtered_cannot_pass():
    rep = _passing_report()
    rep["roles"]["candidate"]["dropped_timeouts"] = 2
    out = evaluate_gates(rep, THRESHOLDS)
    assert out["verdict"] == "FAIL"
    assert out["gates"]["g0_identity_and_dom"]["status"] == "FAIL"
