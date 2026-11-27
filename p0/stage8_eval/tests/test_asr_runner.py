"""Tests for T4: same-span ASR runner failure retention, collar accounting, and CLI guards."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from subtitle_service.quality_types import ASRHypothesis, AudioSpan, TokenSpan


def test_asr_runner_retains_empty_and_accounts_collar(tmp_path):
    from p0.stage8_eval.bench_asr import run_asr_units

    class StubAsr:
        def transcribe(self, pcm_f32, span: AudioSpan, options: dict) -> ASRHypothesis:
            if span.target_start == 32000:
                return ASRHypothesis(span, "", "stub-asr", "v1", [], {"total_ms": 12.0}, [])
            return ASRHypothesis(
                span,
                "はい。",
                "stub-asr",
                "v1",
                [TokenSpan(1, "はい。", span.target_start, span.target_end, "aligned")],
                {"total_ms": 20.0},
                [],
            )

    pcm = np.zeros(64000, dtype=np.float32)
    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 16000, "reference_ja": "はい。"},
        {"unit_id": "u2", "start_sample": 32000, "end_sample": 48000, "reference_ja": "うん。"},
    ]
    res = run_asr_units(StubAsr(), pcm, units, left_context_s=0.0, right_collar_ms=160)
    assert len(res["rows"]) == 2
    assert res["summary"]["empty_count"] == 1
    assert res["summary"]["empty_unit_count"] == 1
    assert res["summary"]["extra_collar_delay_ms"] == 160.0
    assert res["summary"]["cer_v2"]["N"] == 4  # both "はい" and "うん" kept!


def test_asr_runner_reports_per_unit_and_concat_cer_separately():
    from p0.stage8_eval.bench_asr import run_asr_units

    class SplitAsr:
        def transcribe(self, pcm_f32, span: AudioSpan, options: dict) -> ASRHypothesis:
            txt = "猫犬" if span.target_start == 0 else ""
            return ASRHypothesis(span, txt, "stub-asr", "v1", [], {"total_ms": 15.0}, [])

    pcm = np.zeros(64000, dtype=np.float32)
    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 16000, "reference_ja": "猫"},
        {"unit_id": "u2", "start_sample": 32000, "end_sample": 48000, "reference_ja": "犬"},
    ]
    res = run_asr_units(SplitAsr(), pcm, units)
    assert res["summary"]["cer_v2_concat"]["cer"] == 0.0
    assert res["summary"]["cer_v2_per_unit"]["cer"] == 1.0
    assert res["summary"]["empty_unit_count"] == 1


def test_asr_runner_separates_ane_error_from_ok_latency():
    from p0.stage8_eval.bench_asr import run_asr_units

    class ErrAsr:
        def transcribe(self, pcm_f32, span: AudioSpan, options: dict) -> ASRHypothesis:
            if span.target_start == 0:
                return ASRHypothesis(span, "", "stub-asr", "v1", [], {"total_ms": 1.0}, ["ane_error_2"])
            tot = 50.0 if span.target_start == 16000 else 60.0
            return ASRHypothesis(span, "はい。", "stub-asr", "v1", [], {"total_ms": tot}, [])

    pcm = np.zeros(64000, dtype=np.float32)
    units = [
        {"unit_id": "u1", "start_sample": 0, "end_sample": 16000, "reference_ja": "はい。"},
        {"unit_id": "u2", "start_sample": 16000, "end_sample": 32000, "reference_ja": "はい。"},
        {"unit_id": "u3", "start_sample": 32000, "end_sample": 48000, "reference_ja": "はい。"},
    ]
    res = run_asr_units(ErrAsr(), pcm, units)
    assert res["summary"]["failed_unit_count"] == 1
    assert res["summary"]["error_flags_count"] == {"ane_error_2": 1}
    assert res["summary"]["latency_ok_p50_ms"] == 50.0
    assert res["summary"]["latency_ok_p95_ms"] == 60.0


def test_p1b_scene_grouping_unique_assignment_and_overlap_detector():
    from p0.stage8_eval.run_p1b_vad_2x2 import (
        assign_segments_to_scenes,
        detect_unprotected_overlaps,
        group_contiguous_scenes,
    )

    cues = [
        {"cue_idx": 1, "start_s": 1.0, "end_s": 2.0, "clean_text": "あ"},
        {"cue_idx": 2, "start_s": 2.5, "end_s": 3.5, "clean_text": "い"},
        {"cue_idx": 3, "start_s": 6.0, "end_s": 7.0, "clean_text": "う"},
    ]
    scenes = group_contiguous_scenes(cues, gap_s=2.0)
    assert len(scenes) == 2
    assert scenes[0]["reference_ja"] == "あい"
    assert scenes[1]["reference_ja"] == "う"

    segs = [
        {"start_sample": 16000, "end_sample": 56000, "text_ja": "あい"},
        {"start_sample": 96000, "end_sample": 112000, "text_ja": "う"},
        {"start_sample": 200000, "end_sample": 216000, "text_ja": "孤立"},
    ]
    by_sc, orphans = assign_segments_to_scenes(segs, scenes, margin_s=0.5)
    assert len(by_sc["sc-001"]) == 1
    assert len(by_sc["sc-002"]) == 1
    assert len(orphans) == 1 and orphans[0]["text_ja"] == "孤立"

    overlaps = detect_unprotected_overlaps([
        {"start_sample": 0, "end_sample": 40000, "dedup_before_sample": 0, "cut_reason": "energy_valley"},
        {"start_sample": 34400, "end_sample": 64000, "dedup_before_sample": 40000, "cut_reason": "vad_tail"},
        {"start_sample": 50000, "end_sample": 80000, "dedup_before_sample": 0, "cut_reason": "vad_natural"},
    ])
    assert len(overlaps) == 1
    assert overlaps[0]["chunk_idx"] == 2
    assert overlaps[0]["prev_end_sample"] == 64000
    assert overlaps[0]["cut_reason"] == "vad_natural"


def test_asr_cli_rejects_manifest_mismatch(tmp_path):
    manifest_p = tmp_path / "m.json"
    manifest_p.write_text(
        json.dumps({"arm": {"arm_id": "asr-b0"}, "execution": {"output_dir": str(tmp_path / "o")}}),
        encoding="utf-8",
    )
    wt_root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "p0.stage8_eval.bench_asr",
            "--manifest",
            str(manifest_p),
            "--arm",
            "wrong",
            "--out",
            str(tmp_path / "o"),
        ],
        cwd=wt_root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
