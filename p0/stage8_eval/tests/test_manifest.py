"""Tests for T0: Stage 8 manifest validation, experiment/run identity, AudioSpan bounds, and preflight."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from subtitle_service.quality_types import AudioSpan

MAIN_ROOT = Path(__file__).resolve().parents[3]
PLAN_DIR = MAIN_ROOT / "docs/live_video_translate_stage8_plan_20261126"
EXAMPLE_MANIFEST = PLAN_DIR / "templates/run_manifest.example.json"
THRESHOLDS_PATH = PLAN_DIR / "templates/thresholds.proposed.json"
SCHEMA_PATH = PLAN_DIR / "schemas/contracts.schema.json"


def _make_valid_manifest(tmp_path: Path) -> dict:
    m = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    pcm_file = tmp_path / "ep01.pcm"
    pcm_file.write_bytes(b"\x00\x00" * 16000)
    pcm_sha = hashlib.sha256(pcm_file.read_bytes()).hexdigest()

    media_file = tmp_path / "ep01.mkv"
    media_file.write_bytes(b"fake-mkv")
    media_sha = hashlib.sha256(media_file.read_bytes()).hexdigest()

    units_file = tmp_path / "units.jsonl"
    units_file.write_text('{"unit_id":"u1"}\n', encoding="utf-8")
    units_sha = hashlib.sha256(units_file.read_bytes()).hexdigest()

    prof_file = tmp_path / "profiles.toml"
    prof_file.write_text('schema_version = 1\ndefault_profile_id = "m1-pro-nemo"\n', encoding="utf-8")
    prof_sha = hashlib.sha256(prof_file.read_bytes()).hexdigest()

    gguf = tmp_path / "nemo.gguf"
    gguf.write_bytes(b"gguf")
    ane = tmp_path / "nemo.mlmodelc"
    ane.mkdir()
    (ane / "coremldata.bin").write_bytes(b"ane")
    qw = tmp_path / "qwen.safetensors"
    qw.write_bytes(b"safetensors")

    thresh_sha = hashlib.sha256(THRESHOLDS_PATH.read_bytes()).hexdigest()

    m["template_only"] = False
    m["run_id"] = "run-001"
    m["experiment_id"] = "exp-001"
    m["actual_repo_sha"] = "7ea13865c8c3673a1463495058a200723b153111"
    m["patch_sha256"] = "clean"
    m["bundle"] = {
        "bundle_key": "b-corrected-v1",
        "native_sha256": "a" * 64,
        "extension_sha256": "b" * 64,
        "observer_sha256": "c" * 64,
        "profile_sha256": prof_sha,
        "profile_path": str(prof_file),
    }
    m["hardware"] = {
        "chip": "Apple M1 Pro",
        "cpu_cores": 10,
        "gpu_cores": 16,
        "ram_bytes": 34359738368,
        "macos_version": "15.0",
        "chrome_version": "131.0.0.0",
    }
    m["runtime"]["python_version"] = "3.12.0"
    m["runtime"]["lock_sha256"] = "d" * 64
    m["data"] = {
        "dataset_id": "brush-up-life",
        "episode_id": "ep01",
        "split": "dev",
        "media_path": str(media_file),
        "media_sha256": media_sha,
        "audio_track": "0:a:0",
        "pcm_path": str(pcm_file),
        "pcm_sha256": pcm_sha,
        "sample_rate": 16000,
        "channels": 1,
        "resample_downmix_recipe": "ffmpeg -ac 1 -ar 16000 -f s16le",
        "semantic_units_path": str(units_file),
        "semantic_units_sha256": units_sha,
        "reference_human_verified": True,
        "scene_count": 12,
        "holdout_sealed": True,
    }
    m["models"][0]["revision"] = "v2.0-gguf-q8_0"
    m["models"][0]["precision"] = "w8a16_ane_q8_gguf"
    m["models"][0]["artifacts"] = [
        {"path": str(gguf), "sha256": hashlib.sha256(b"gguf").hexdigest()},
        {"path": str(ane), "sha256": "e" * 64},
    ]
    m["models"][0]["tokenizer_sha256"] = "f" * 64
    m["models"][0]["license_verified"] = True

    m["models"][1]["artifacts"] = [
        {"path": str(qw), "sha256": hashlib.sha256(b"safetensors").hexdigest()},
    ]
    m["models"][1]["tokenizer_sha256"] = "1" * 64
    m["models"][1]["template_sha256"] = "2" * 64
    m["models"][1]["license_verified"] = True
    m["models"][1]["stop_ids"] = [151645, 151643]

    m["arm"]["rendered_prompt_policy_sha256"] = "3" * 64
    m["thresholds"] = {"path": str(THRESHOLDS_PATH), "sha256": thresh_sha}
    m["execution"]["output_dir"] = str(tmp_path / "out-001")
    m["review"]["status"] = "PASS"
    m["review"]["reason_code"] = "READY"
    return m


def test_same_row_count_different_pcm_invalidates_key(tmp_path):
    from p0.stage8_eval.contracts import experiment_key, run_key, validate_manifest

    m1 = _make_valid_manifest(tmp_path)
    assert validate_manifest(m1, require_local_assets=True, schema_path=SCHEMA_PATH) == []

    # Same 654-row semantic units metadata, only PCM content hash changed -> experiment_key MUST change
    m2 = copy.deepcopy(m1)
    m2["data"]["pcm_sha256"] = "9" * 64
    assert experiment_key(m1) != experiment_key(m2)

    # Prompt/policy hash changed -> experiment_key MUST change
    m3 = copy.deepcopy(m1)
    m3["arm"]["rendered_prompt_policy_sha256"] = "8" * 64
    assert experiment_key(m1) != experiment_key(m3)

    # Only run_id and output_dir changed (e.g., ABBA repeat) -> experiment_key stays identical, run_key changes
    m4 = copy.deepcopy(m1)
    m4["run_id"] = "run-002"
    m4["execution"]["output_dir"] = str(tmp_path / "out-002")
    assert experiment_key(m1) == experiment_key(m4)
    assert run_key(m1) != run_key(m4)


def test_placeholder_manifest_is_blocked(tmp_path):
    from p0.stage8_eval.contracts import load_manifest, validate_manifest
    from p0.stage8_eval.preflight import preflight

    example = load_manifest(EXAMPLE_MANIFEST)
    errs = validate_manifest(example, require_local_assets=False, schema_path=SCHEMA_PATH)
    assert errs, "Template manifest with REPLACE_* and template_only=true must fail validation"

    res = preflight(example, schema_path=SCHEMA_PATH)
    assert res["status"] == "BLOCKED"
    assert res["reasons"]

    # CLI must exit 2 for invalid/placeholder manifest
    wt_root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [sys.executable, "-m", "p0.stage8_eval.preflight", "--manifest", str(EXAMPLE_MANIFEST)],
        cwd=wt_root,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_future_audio_is_rejected():
    # Valid span
    ok = AudioSpan(
        session_id="s1",
        epoch=0,
        target_start=1600,
        target_end=32000,
        input_start=0,
        input_end=32000,
        captured_end=32000,
        sample_rate=16000,
    )
    assert ok.target_end == 32000

    # input_end > captured_end must raise ValueError
    with pytest.raises(ValueError, match="captured_end"):
        AudioSpan(
            session_id="s1",
            epoch=0,
            target_start=1600,
            target_end=32000,
            input_start=0,
            input_end=33600,
            captured_end=32000,
            sample_rate=16000,
        )

    # Non-16k sample rate must raise ValueError
    with pytest.raises(ValueError, match="sample_rate"):
        AudioSpan(
            session_id="s1",
            epoch=0,
            target_start=0,
            target_end=16000,
            input_start=0,
            input_end=16000,
            captured_end=16000,
            sample_rate=48000,
        )


def test_unknown_model_revision_main_is_blocked(tmp_path):
    from p0.stage8_eval.contracts import validate_manifest

    m = _make_valid_manifest(tmp_path)
    m["models"][1]["revision"] = "main"
    errs = validate_manifest(m, require_local_assets=True, schema_path=SCHEMA_PATH)
    assert any("revision" in e for e in errs)
