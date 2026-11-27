"""Tests for Stage 8 T7 aggregation and bootstrap CI."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from p0.stage8_eval.aggregate import (
    _summarize_model_trace,
    aggregate_runs,
    paired_bootstrap_ci,
    summarize_chrome_run_dir,
)


def _make_run_dir(
    root: Path,
    name: str,
    *,
    role: str,
    dom_events: list[dict],
    drop_rate_pct: float = 4.5,
    thresholds_sha: str = "",
) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "chrome_run_summary.json").write_text(
        json.dumps({
            "run_id": name,
            "experiment_id": "t7-test",
            "arm_id": name,
            "baseline_role": role,
            "duration_s": 120.0,
            "result": {
                "wall_seconds": 125.0,
                "current_time": 120.2,
                "total_frames": 3600,
                "dropped_frames": int(round(3600 * drop_rate_pct / 100.0)),
                "drop_rate_pct": drop_rate_pct,
                "srv_peak_rss_mib": 1200.0,
                "srv_peak_cpu_pct": 50.0,
                "chrome_peak_rss_mib": 3900.0,
            },
        }),
        encoding="utf-8",
    )
    (d / "manifest.json").write_text(
        json.dumps({
            "arm": {"arm_id": name, "baseline_role": role},
            "data": {"media_sha256": "media-abc"},
            "thresholds": {"sha256": thresholds_sha},
        }),
        encoding="utf-8",
    )
    (d / "dom_events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in dom_events),
        encoding="utf-8",
    )
    return d


def test_aggregate_runs_computes_metrics_and_bootstrap_ci(tmp_path):
    ci = paired_bootstrap_ci([0.3, 0.4, 0.25, 0.35], resamples=500, seed=42)
    assert ci["n"] == 4
    assert ci["ci95_low"] > 0.0
    assert ci["ci95_low"] <= ci["mean"] <= ci["ci95_high"]

    th_path = tmp_path / "thresholds.json"
    th_path.write_text(json.dumps({"schema_version": "stage8-gates-v1", "resources": {}}), encoding="utf-8")

    dep_dir = _make_run_dir(
        tmp_path,
        "deployed",
        role="deployed",
        drop_rate_pct=4.5,
        dom_events=[
            {"is_status": False, "is_streaming": True, "text_zh": "朝海，这周末你来吧。"},
            {"is_status": False, "is_streaming": True, "text_zh": "麻美"},
            {"is_status": False, "is_streaming": False, "text_zh": "麻美，这周末你来吧。"},
        ],
    )
    cand_dir = _make_run_dir(
        tmp_path,
        "candidate",
        role="candidate",
        drop_rate_pct=4.8,
        dom_events=[
            {"is_status": False, "is_streaming": True, "text_zh": "朝海，这周末你来吧。"},
            {"is_status": False, "is_streaming": True, "text_zh": "麻美，这周末你来吧！"},
            {"is_status": False, "is_streaming": False, "text_zh": "麻美，这周末你来吧！"},
        ],
    )
    off_dir = _make_run_dir(tmp_path, "subtitles_off", role="subtitles_off", drop_rate_pct=4.2, dom_events=[])

    s_dep = summarize_chrome_run_dir(dep_dir)
    assert s_dep["dom_events"]["shrink_flickers"] == 1
    s_cand = summarize_chrome_run_dir(cand_dir)
    assert s_cand["dom_events"]["shrink_flickers"] == 0

    rep = aggregate_runs([off_dir, dep_dir, cand_dir], None, None, th_path)
    assert rep["roles_present"] == ["candidate", "deployed", "subtitles_off"]
    assert rep["comparison"]["dom_shrink_flickers_delta"] == -1
    assert rep["comparison"]["drop_rate_delta_vs_subtitles_off_pp"] == 0.6


def test_v1_v5_trace_session_isolation_duplicate_role_and_missing_fields(tmp_path):
    # Cross-session trace must NOT mix session A's t=1.0 pcm_accepted with session B's t=10.2 ws_sent
    trace_rows = [
        {"t": 1.0, "stage": "pcm_accepted", "session_id": "sess-A", "end_sample": 16000},
        {"t": 10.0, "stage": "pcm_accepted", "session_id": "sess-B", "end_sample": 16000},
        {"t": 10.2, "stage": "ws_sent", "session_id": "sess-B", "end_sample": 16000, "text_zh": "你好"},
    ]
    tr_sum = _summarize_model_trace(trace_rows)
    assert tr_sum is not None
    assert tr_sum["seg_end_to_ws_p50_ms"] == 200.0
    assert tr_sum["seg_end_to_ws_p95_ms"] == 200.0

    # Missing telemetry & manifest must stay None / manifest_present=False, never fake zeros
    incomplete_dir = tmp_path / "incomplete"
    incomplete_dir.mkdir()
    (incomplete_dir / "chrome_run_summary.json").write_text(
        json.dumps({"run_id": "inc", "baseline_role": "candidate", "duration_s": 120.0, "result": {"current_time": 0.25, "total_frames": 0}}),
        encoding="utf-8",
    )
    inc_sum = summarize_chrome_run_dir(incomplete_dir)
    assert inc_sum["manifest_present"] is False
    assert inc_sum["swap_growth_mib"] is None
    assert inc_sum["resource_abort"] is None
    assert inc_sum["lifecycle"] is None

    # Duplicate baseline_role must raise ValueError instead of silently taking first
    th_path = tmp_path / "thresholds.json"
    th_path.write_text(json.dumps({"schema_version": "stage8-gates-v1", "resources": {}}), encoding="utf-8")
    d1 = _make_run_dir(tmp_path, "cand1", role="candidate", dom_events=[])
    d2 = _make_run_dir(tmp_path, "cand2", role="candidate", dom_events=[])
    with pytest.raises(ValueError, match="duplicate baseline_role"):
        aggregate_runs([d1, d2], None, None, th_path)
