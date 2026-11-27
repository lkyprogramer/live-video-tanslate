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


def test_chrf2_standard_effective_order_short_strings():
    from p0.stage8_eval.bench_mt import CHRF2_STANDARD_SIGNATURE, chrf2_standard_0_100

    assert "eff:yes" in CHRF2_STANDARD_SIGNATURE
    assert chrf2_standard_0_100("我", "我") == 100.0
    assert chrf2_standard_0_100("你好", "你好") == 100.0
    assert chrf2_standard_0_100("今天天气很好", "今天天气很好") == 100.0
    # Upstream SacreBLEU effective_order=True exact parity vectors for unequal short strings
    assert chrf2_standard_0_100("我", "我们") == 55.5556
    assert chrf2_standard_0_100("我们", "我") == 83.3333
    assert chrf2_standard_0_100("", "我") == 0.0
    assert chrf2_standard_0_100("我", "") == 0.0


def test_v4_invalid_final_gets_zero_delivered_score_and_infinite_latency():
    from p0.stage8_eval.bench_mt import run_mt_cues
    from p0.stage8_eval.run_p2_index2b_screen import _score_predictions
    from subtitle_service.quality_types import TranslationResult

    class ExactTextTruncatingAdapter:
        def translate_turn(self, text_ja: str, *, history, start_sample=None, end_sample=None, max_tokens=256):
            return TranslationResult("今天天气很好", "length_limit", {"latency_ms": 20.0}, valid=False)

    cues = [{"cue_idx": 1, "start_s": 0.0, "end_s": 1.0, "clean_text": "今日はいい天気"}]
    gold = [{"cue_idx": 1, "start_s": 0.0, "end_s": 1.0, "clean_text": "今天天气很好"}]
    out = run_mt_cues(ExactTextTruncatingAdapter(), cues, gold, context_turns=0)
    s = out["summary"]
    assert s["valid_count"] == 0
    assert s["delivered"]["chrf2_full_concat"] == 0.0
    assert s["delivered"]["completion_p95_ms"] is None
    assert s["sacrebleu_chrf2_0_100"]["full_concat"] == 0.0
    assert s["mt_p95_ms"] is None
    assert s["diagnostic_raw"]["chrf2_full_concat"] == 100.0
    assert s["diagnostic_raw"]["attempt_p95_ms"] == 20.0
    assert s["valid_only"]["count"] == 0
    assert s["valid_only"]["chrf2_concat"] if "chrf2_concat" in s["valid_only"] else s["valid_only"]["chrf2_full_concat"] is None

    rows = [
        {
            "primary_tag": "E3",
            "gold_zh": "今天天气很好",
            "cand": {
                "text_zh": "今天天气很好",
                "stop_reason": "length_limit",
                "valid": False,
                "latency_ms": 20.0,
                "mem_mib": 100.0,
                "think_leak": False,
                "kana_leak": False,
            },
        }
    ]
    p2s = _score_predictions(rows, field="cand")
    assert p2s["valid_count"] == 0
    assert p2s["chrf2_concat"] == 0.0
    assert p2s["chrf2_case_mean"] == 0.0
    assert p2s["latency_p95_ms"] is None
    assert p2s["diagnostic_raw"]["chrf2_concat"] == 100.0
    assert p2s["diagnostic_raw"]["attempt_p95_ms"] == 20.0


def test_p2_eval_adapter_keeps_empty_input_in_denominator():
    from p0.stage8_eval.run_p2_index2b_screen import _eval_adapter
    from subtitle_service.quality_types import TranslationResult

    class DummyAdapter:
        def translate_turn(self, src: str, *, history, max_tokens: int):
            return TranslationResult("好的", "eos", {"latency_ms": 15.0}, valid=True)

    res = _eval_adapter(
        DummyAdapter(),
        [
            {"cue_idx": 1, "clean_jp": "はい", "live_asr_ja": ""},
            {"cue_idx": 2, "clean_jp": "ええ", "live_asr_ja": "ええ"},
        ],
        track="live_asr_ja",
    )
    assert set(res.keys()) == {1, 2}
    assert res[1]["stop_reason"] == "empty_input"
    assert res[1]["valid"] is False
    assert res[1]["latency_ms"] is None
    assert res[2]["valid"] is True


def test_v4_valid_only_aligns_gold_by_timestamp_not_cue_idx():
    from p0.stage8_eval.bench_mt import chrf2_standard_0_100, run_mt_cues
    from subtitle_service.quality_types import TranslationResult

    class SelectiveAdapter:
        def translate_turn(self, text_ja: str, *, history, start_sample=None, end_sample=None, max_tokens=256):
            if text_ja == "BAD":
                return TranslationResult("错误。", "length_limit", {"latency_ms": 50.0}, valid=False)
            return TranslationResult("正确译文。", "eos", {"latency_ms": 20.0}, valid=True)

    # JP cue_idx=2 aligns in time with ZH cue_idx=1 (simulating skipped credit/title lines in ZH SRT)
    jp_cues = [
        {"cue_idx": 1, "start_s": 0.0, "end_s": 1.0, "clean_text": "BAD"},
        {"cue_idx": 2, "start_s": 2.0, "end_s": 3.0, "clean_text": "GOOD"},
    ]
    zh_gold = [
        {"cue_idx": 1, "start_s": 2.0, "end_s": 3.0, "clean_text": "正确译文。"},
    ]
    out = run_mt_cues(SelectiveAdapter(), jp_cues, zh_gold, context_turns=0)
    assert out["summary"]["valid_only"]["count"] == 1
    assert out["summary"]["valid_only"]["chrf2_full_concat"] == chrf2_standard_0_100("正确译文。", "正确译文。")


def test_t5_reuse_rejects_mismatched_experiment_key(tmp_path):
    from p0.stage8_eval.run_t5_batch2 import LOCKED_M2_CONFIG, _reuse_asr, _reuse_m2

    art = tmp_path / "model.safetensors"
    art.write_bytes(b"weights")
    reg = {
        "schema_version": "stage8-model-registry-v1",
        "models": {
            "qwen4b-q8": {
                "key": "qwen4b-q8",
                "repository": "org/qwen4b-q8",
                "revision": "a" * 40,
                "artifacts": [{"path": str(art), "sha256": ""}],
                "license_verified": True,
                "trust_remote_code": False,
            },
            "qwen3-asr-1.7b-8bit": {
                "key": "qwen3-asr-1.7b-8bit",
                "repository": "org/qwen-asr",
                "revision": "b" * 40,
                "artifacts": [{"path": str(art), "sha256": ""}],
                "license_verified": True,
                "trust_remote_code": False,
            },
        },
    }

    (tmp_path / "m2_qwen4b_q8_120.json").write_text(
        json.dumps({"experiment_key": "k_old", "summary": {"cues": 120}}),
        encoding="utf-8",
    )
    (tmp_path / "m2_qwen4b_q8_654.json").write_text(
        json.dumps({"experiment_key": "k_old", "config": LOCKED_M2_CONFIG, "summary": {"cues": 654}, "hard_cases": []}),
        encoding="utf-8",
    )
    assert _reuse_m2(reg, tmp_path, expected_experiment_key="k_new") is None
    reused_m2 = _reuse_m2(reg, tmp_path, expected_experiment_key="k_old")
    assert reused_m2 is not None
    assert reused_m2["config"] == LOCKED_M2_CONFIG
    assert reused_m2["run_status"] == "COMPLETED"
    assert reused_m2["gate_status"] == "SCREENING_ONLY"

    nemo_ref = [{"unit_id": "u1", "reference_ja": "はい", "hyp_ja": "はい", "S": 0, "D": 0, "I": 0, "N": 2}]
    asr_payload = {
        "experiment_key": "asr_old",
        "summary": {"units": 1},
        "rows": [{"unit_id": "u1", "reference_ja": "はい", "hyp_ja": "いいえ", "S": 2, "D": 0, "I": 1, "N": 2}],
    }
    (tmp_path / "qwen3_asr_1.7b_8bit_dev60.json").write_text(json.dumps(asr_payload), encoding="utf-8")
    assert _reuse_asr("qwen3-asr-1.7b-8bit", reg, nemo_ref, tmp_path, expected_experiment_key="asr_new") is None
    reused_asr = _reuse_asr("qwen3-asr-1.7b-8bit", reg, nemo_ref, tmp_path, expected_experiment_key="asr_old")
    assert reused_asr is not None
    assert reused_asr["run_status"] == "COMPLETED"
    assert reused_asr["gate_status"] == "FAIL"
    assert reused_asr["vs_nemo_oracle_hybrid"]["net_edit_delta"] == 3


def test_llama_server_mt_adapter_cmd_and_no_mmproj(tmp_path):
    from p0.stage8_eval.contracts import sha256_file
    from p0.stage8_eval.model_adapters import build_llama_server_cmd, resolve_model

    gguf = tmp_path / "Index-Translate-2B.Q4_K_M.gguf"
    gguf.write_bytes(b"GGUFdummy")
    cmd = build_llama_server_cmd(gguf, 18899)
    assert "--host" in cmd and cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert "--port" in cmd and cmd[cmd.index("--port") + 1] == "18899"
    assert "--jinja" in cmd
    assert "--chat-template-kwargs" in cmd
    assert '{"enable_thinking":false}' in cmd
    assert all("mmproj" not in tok for tok in cmd)

    reg = {
        "schema_version": "stage8-model-registry-v1",
        "models": {
            "index-translate-2b-q4km": {
                "key": "index-translate-2b-q4km",
                "repository": "IndexTeam/Index-Translate-2B-GGUF",
                "revision": "3a0eca6ce931d88d00614d427ab3ed8ff0743902",
                "backend": "llama_server_metal",
                "precision": "Q4_K_M",
                "artifacts": [{"path": str(gguf), "sha256": sha256_file(gguf)}],
                "license_verified": True,
                "trust_remote_code": False,
            }
        },
    }
    entry = resolve_model("index-translate-2b-q4km", reg)
    assert entry["precision"] == "Q4_K_M"
    assert len(entry["artifacts"]) == 1
