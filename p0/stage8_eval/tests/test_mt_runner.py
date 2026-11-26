"""Tests for T3: MT adapter registry enforcement, prompt-change cache invalidation, stop/gateway validity."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_resolve_model_blocks_unapproved_or_main_revision(tmp_path):
    from p0.stage8_eval.model_adapters import resolve_model

    artifact = tmp_path / "model.safetensors"
    artifact.write_bytes(b"weights")
    registry = {
        "schema_version": "stage8-model-registry-v1",
        "models": {
            "unlicensed": {
                "key": "unlicensed",
                "repository": "org/unlicensed",
                "revision": "a" * 40,
                "backend": "mlx_lm",
                "precision": "8bit",
                "artifacts": [{"path": str(artifact), "sha256": ""}],
                "license_verified": False,
                "trust_remote_code": False,
            },
            "branch_pin": {
                "key": "branch_pin",
                "repository": "org/branch_pin",
                "revision": "main",
                "backend": "mlx_lm",
                "precision": "8bit",
                "artifacts": [{"path": str(artifact), "sha256": ""}],
                "license_verified": True,
                "trust_remote_code": False,
            },
            "remote_code": {
                "key": "remote_code",
                "repository": "org/remote_code",
                "revision": "b" * 40,
                "backend": "mlx_lm",
                "precision": "8bit",
                "artifacts": [{"path": str(artifact), "sha256": ""}],
                "license_verified": True,
                "trust_remote_code": True,
            },
            "missing_file": {
                "key": "missing_file",
                "repository": "org/missing_file",
                "revision": "c" * 40,
                "backend": "mlx_lm",
                "precision": "8bit",
                "artifacts": [{"path": str(tmp_path / "missing.safetensors"), "sha256": ""}],
                "license_verified": True,
                "trust_remote_code": False,
            },
            "ok": {
                "key": "ok",
                "repository": "org/ok",
                "revision": "d" * 40,
                "backend": "mlx_lm",
                "precision": "8bit",
                "artifacts": [{"path": str(artifact), "sha256": ""}],
                "license_verified": True,
                "trust_remote_code": False,
            },
        },
    }

    with pytest.raises(RuntimeError, match="BLOCKED"):
        resolve_model("unknown", registry)
    with pytest.raises(RuntimeError, match="BLOCKED"):
        resolve_model("unlicensed", registry)
    with pytest.raises(RuntimeError, match="BLOCKED"):
        resolve_model("branch_pin", registry)
    with pytest.raises(RuntimeError, match="BLOCKED"):
        resolve_model("remote_code", registry)
    with pytest.raises(RuntimeError, match="BLOCKED"):
        resolve_model("missing_file", registry)
    assert resolve_model("ok", registry)["key"] == "ok"


def test_prompt_change_invalidates_key_and_records_failures(tmp_path):
    from p0.stage8_eval.bench_mt import mt_arm_identity, run_mt_cues
    from subtitle_service.quality_types import TranslationResult

    k1 = mt_arm_identity(
        model_key="qwen4b-q4",
        revision="a" * 40,
        system_prompt="Prompt A",
        context_turns=4,
        repetition_penalty=1.08,
        use_kv_cache=True,
        enable_kana_mask=True,
        dataset_sha256="1" * 64,
    )
    k2 = mt_arm_identity(
        model_key="qwen4b-q4",
        revision="a" * 40,
        system_prompt="Prompt B",
        context_turns=4,
        repetition_penalty=1.08,
        use_kv_cache=True,
        enable_kana_mask=True,
        dataset_sha256="1" * 64,
    )
    assert k1 != k2

    seen_histories: list[list[tuple[str, str]]] = []

    class StubAdapter:
        def translate_turn(self, text_ja: str, *, history, start_sample=None, end_sample=None, max_tokens=256):
            seen_histories.append(list(history))
            if text_ja == "LONG":
                return TranslationResult("太长截断", "length_limit", {"latency_ms": 50.0}, valid=False)
            if text_ja == "KANA":
                return TranslationResult("まだ仮名あり", "eos", {"latency_ms": 40.0}, valid=False)
            return TranslationResult("正常中文。", "eos", {"latency_ms": 30.0}, valid=True)

    cues = [
        {"cue_idx": 1, "start_s": 0.0, "end_s": 1.0, "clean_text": "OK1"},
        {"cue_idx": 2, "start_s": 1.0, "end_s": 2.0, "clean_text": "LONG"},
        {"cue_idx": 3, "start_s": 2.0, "end_s": 3.0, "clean_text": "KANA"},
        {"cue_idx": 4, "start_s": 3.0, "end_s": 4.0, "clean_text": "OK2"},
    ]
    gold = [
        {"cue_idx": 1, "start_s": 0.0, "end_s": 1.0, "clean_text": "正常中文。"},
        {"cue_idx": 2, "start_s": 1.0, "end_s": 2.0, "clean_text": "长句。"},
        {"cue_idx": 3, "start_s": 2.0, "end_s": 3.0, "clean_text": "有假名。"},
        {"cue_idx": 4, "start_s": 3.0, "end_s": 4.0, "clean_text": "正常中文。"},
    ]
    out = run_mt_cues(StubAdapter(), cues, gold, context_turns=4)
    # Output count MUST equal input count (invalid results NOT filtered out!)
    assert len(out["rows"]) == 4
    assert out["summary"]["valid_count"] == 2
    assert out["summary"]["length_limit_count"] == 1
    assert out["summary"]["invalid_count"] == 2
    # History passed to OK2 must include ONLY accepted past turn OK1 (NOT LONG or KANA)
    assert seen_histories[3] == [("OK1", "正常中文。")]


def test_cli_rejects_manifest_mismatch(tmp_path):
    manifest_p = tmp_path / "m.json"
    manifest_p.write_text(
        json.dumps({"arm": {"arm_id": "mt-b0"}, "execution": {"output_dir": str(tmp_path / "o")}}),
        encoding="utf-8",
    )
    wt_root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "p0.stage8_eval.bench_mt",
            "--manifest",
            str(manifest_p),
            "--arm",
            "wrong-arm",
            "--out",
            str(tmp_path / "o"),
        ],
        cwd=wt_root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
