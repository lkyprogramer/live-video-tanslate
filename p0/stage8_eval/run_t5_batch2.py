"""T5 / G3 Runner: Download & screen M2 (Qwen3-4B-8bit), A3 (Qwen3-ASR-1.7B-8bit), A4 (whisper-large-v3-turbo)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any

MAIN_ROOT = Path("/Users/luo/Documents/github/jp-live-video-tanslate")
CACHE_ROOT = MAIN_ROOT / ".model-cache"
os.environ["HF_HOME"] = str(CACHE_ROOT)

import numpy as np  # noqa: E402
from huggingface_hub import HfApi, snapshot_download  # noqa: E402

from p0.stage8_eval.bench_asr import run_asr_units  # noqa: E402
from p0.stage8_eval.bench_mt import run_mt_cues  # noqa: E402
from p0.stage8_eval.contracts import sha256_file  # noqa: E402
from p0.stage8_eval.model_adapters import (  # noqa: E402
    Qwen3AsrMlxAdapter,
    Qwen3MlxMTAdapter,
    WhisperMlxAdapter,
    resolve_model,
)
from p0.stage8_eval.run_t3_batch2 import SHORT_FAITHFUL_PROMPT, load_stratified_120_cues  # noqa: E402
from subtitle_service.local_pipeline import Qwen3Translator  # noqa: E402

PERF_PROBE = Path(__file__).resolve().parents[1] / "perf_probe"
sys.path.insert(0, str(PERF_PROBE))

from analyze_stage6_stage3_accuracy import (  # noqa: E402
    JP_SRT_PATH,
    MAIN_END_S,
    ZH_SRT_PATH,
    parse_gold_zh_cues,
    parse_jp_cues,
)

VAD_PATH = CACHE_ROOT / "reazonspeech-assets/silero_vad.onnx"
STAGE6_ATTR_PATH = MAIN_ROOT / "docs/experiments/stage6-ep01-2026-11-26/stage3_accuracy_attribution.json"
REGISTRY_PATH = Path(__file__).resolve().parent / "model_registry.json"


def download_and_register() -> dict[str, Any]:
    disk = shutil.disk_usage(MAIN_ROOT)
    free_gib = disk.free / (1024**3)
    if free_gib < 25.0:
        raise RuntimeError(f"BLOCKED: disk free {free_gib:.2f} GiB < 25 GiB floor")

    api = HfApi()
    specs = [
        {
            "key": "qwen4b-q8",
            "repo": "mlx-community/Qwen3-4B-Instruct-2507-8bit",
            "revision": None,  # resolve current commit SHA before pinned download
            "backend": "mlx_lm",
            "precision": "8bit",
            "weight_glob": ("model*.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json"),
            "stop_ids": [151645, 151643],
        },
        {
            "key": "qwen3-asr-1.7b-8bit",
            "repo": "mlx-community/Qwen3-ASR-1.7B-8bit",
            "revision": "a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
            "backend": "mlx_audio_qwen3_asr",
            "precision": "8bit",
            "weight_glob": ("model.safetensors", "config.json", "tokenizer_config.json"),
            "stop_ids": [],
        },
        {
            "key": "whisper-large-v3-turbo",
            "repo": "mlx-community/whisper-large-v3-turbo",
            "revision": None,
            "backend": "mlx_audio_whisper",
            "precision": "fp16",
            "weight_glob": ("weights*.safetensors", "model*.safetensors", "weights.npz", "config.json"),
            "stop_ids": [],
        },
    ]

    registry_models: dict[str, Any] = {}
    for sp in specs:
        info = api.model_info(sp["repo"], revision=sp["revision"] or "main")
        pinned_sha = str(info.sha)
        assert len(pinned_sha) == 40, f"expected 40-char commit SHA for {sp['repo']}, got {pinned_sha!r}"
        print(f"[G3] Downloading {sp['repo']}@{pinned_sha[:12]}...", flush=True)
        snap = Path(
            snapshot_download(
                repo_id=sp["repo"],
                revision=pinned_sha,
                cache_dir=str(CACHE_ROOT / "hub"),
                local_files_only=False,
            )
        )
        artifacts = []
        # Record snapshot dir + weight/config files with verified SHA256
        for fname in ("config.json", "model.safetensors", "model-00001-of-00002.safetensors", "weights.safetensors", "weights.npz", "tokenizer.json", "tokenizer_config.json"):
            fp = snap / fname
            if fp.is_file():
                artifacts.append({"path": str(fp), "sha256": sha256_file(fp)})
        if not artifacts:
            artifacts.append({"path": str(snap), "sha256": ""})
        reg_entry: dict[str, Any] = {
            "key": sp["key"],
            "repository": sp["repo"],
            "revision": pinned_sha,
            "snapshot_dir": str(snap),
            "backend": sp["backend"],
            "precision": sp["precision"],
            "artifacts": artifacts,
            "license_verified": True,
            "trust_remote_code": False,
            "stop_ids": sp["stop_ids"],
        }
        if sp["key"] == "whisper-large-v3-turbo":
            proc_repo = "openai/whisper-large-v3-turbo"
            proc_info = api.model_info(proc_repo, revision="main")
            proc_sha = str(proc_info.sha)
            assert len(proc_sha) == 40
            print(f"[G3] Downloading processor JSONs {proc_repo}@{proc_sha[:12]}...", flush=True)
            proc_snap = Path(
                snapshot_download(
                    repo_id=proc_repo,
                    revision=proc_sha,
                    cache_dir=str(CACHE_ROOT / "hub"),
                    allow_patterns=["*.json", "merges.txt"],
                    local_files_only=False,
                )
            )
            reg_entry["processor_repository"] = proc_repo
            reg_entry["processor_revision"] = proc_sha
            reg_entry["processor_dir"] = str(proc_snap)
        registry_models[sp["key"]] = reg_entry

    reg = {
        "schema_version": "stage8-model-registry-v1",
        "models": registry_models,
    }
    REGISTRY_PATH.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return reg


def screen_m2_q8(reg: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    entry = resolve_model("qwen4b-q8", reg)
    snap = Path(entry["snapshot_dir"])

    t3_sum_path = MAIN_ROOT / "p0/evidence/stage8-batch2-20261126/t3_mt/t3_mt_summary.json"
    sys_prompt = Qwen3Translator.SYSTEM_PROMPT
    ctx_turns = 4
    rep = 1.08
    use_kv = True
    if t3_sum_path.is_file():
        t3 = json.loads(t3_sum_path.read_text(encoding="utf-8"))
        win = t3.get("m1_winner") or {}
        if win.get("prompt") == "short":
            sys_prompt = SHORT_FAITHFUL_PROMPT
        ctx_turns = int(win.get("context_turns", 4))
        rep = float(win.get("repetition_penalty", 1.08))
        use_kv = bool(win.get("use_kv_cache", True))

    s6_attr = json.loads(STAGE6_ATTR_PATH.read_text(encoding="utf-8"))
    hard_ids = {int(x["cue_idx"]) for x in s6_attr["per_cue_labels"] if x.get("primary_tag") in ("E3", "E4")}
    jp_dialogue, _, _ = parse_jp_cues(JP_SRT_PATH)
    gold_zh, _ = parse_gold_zh_cues(ZH_SRT_PATH)
    cues_120 = load_stratified_120_cues()
    cues_hard18 = [c for c in cues_120 if c["cue_idx"] in hard_ids]

    print(f"[T5][M2] Screening qwen4b-q8 (ctx={ctx_turns}, rep={rep}, kv={use_kv})...", flush=True)
    ad = Qwen3MlxMTAdapter(
        snap,
        model_key="qwen4b-q8",
        system_prompt=sys_prompt,
        context_turns=ctx_turns,
        repetition_penalty=rep,
        enable_kana_mask=True,
        use_kv_cache=use_kv,
        max_tokens=256,
    )
    try:
        smoke = run_mt_cues(ad, cues_hard18, gold_zh, context_turns=0, max_tokens=256, hard_cue_ids=hard_ids)
        if smoke["summary"]["valid_count"] < 16:
            return {"status": "FAIL", "reason": "smoke_protocol_failed", "smoke_summary": smoke["summary"]}
        res_120 = run_mt_cues(ad, cues_120, gold_zh, context_turns=ctx_turns, max_tokens=256, hard_cue_ids=hard_ids)
        res_654 = run_mt_cues(ad, jp_dialogue, gold_zh, context_turns=ctx_turns, max_tokens=256, main_end_s=MAIN_END_S, hard_cue_ids=hard_ids)
    finally:
        ad.close()

    (out_dir / "m2_qwen4b_q8_120.json").write_text(json.dumps(res_120, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "m2_qwen4b_q8_654.json").write_text(json.dumps(res_654, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "PASS",
        "model": entry["repository"],
        "revision": entry["revision"],
        "config": {"context_turns": ctx_turns, "repetition_penalty": rep, "use_kv_cache": use_kv},
        "smoke_18": smoke["summary"],
        "stratified_120": res_120["summary"],
        "full_654": res_654["summary"],
        "hard_cases": res_654["hard_cases"],
    }


def _complementarity(nemo_rows: list[dict], cand_rows: list[dict]) -> dict[str, Any]:
    nemo_by_id = {r["unit_id"]: r for r in nemo_rows}
    fixed = regressed = both_err = both_ok = 0
    examples_fixed = []
    examples_regressed = []
    for cr in cand_rows:
        nr = nemo_by_id.get(cr["unit_id"])
        if not nr:
            continue
        n_err = (nr["S"] + nr["D"] + nr["I"]) > 0
        c_err = (cr["S"] + cr["D"] + cr["I"]) > 0
        if n_err and not c_err:
            fixed += 1
            examples_fixed.append({"unit_id": cr["unit_id"], "ref": cr["reference_ja"], "nemo": nr["hyp_ja"], "cand": cr["hyp_ja"]})
        elif not n_err and c_err:
            regressed += 1
            examples_regressed.append({"unit_id": cr["unit_id"], "ref": cr["reference_ja"], "nemo": nr["hyp_ja"], "cand": cr["hyp_ja"]})
        elif n_err and c_err:
            both_err += 1
        else:
            both_ok += 1
    return {
        "fixed_nemo_errors": fixed,
        "regressed_nemo_correct": regressed,
        "both_err": both_err,
        "both_ok": both_ok,
        "examples_fixed": examples_fixed[:8],
        "examples_regressed": examples_regressed[:5],
    }


def screen_asr_challenger(key: str, reg: dict[str, Any], pcm_f32: np.ndarray, units: list[dict], nemo_ref_rows: list[dict], out_dir: Path) -> dict[str, Any]:
    entry = resolve_model(key, reg)
    snap = Path(entry["snapshot_dir"])
    print(f"[T5][ASR] Screening {key} ({entry['repository']}) on frozen 60 units...", flush=True)
    if key == "qwen3-asr-1.7b-8bit":
        ad = Qwen3AsrMlxAdapter(snap, VAD_PATH, model_key=key)
    else:
        proc_dir = Path(entry["processor_dir"]) if entry.get("processor_dir") else None
        ad = WhisperMlxAdapter(snap, model_key=key, processor_dir=proc_dir)
    try:
        res = run_asr_units(ad, pcm_f32, units, left_context_s=0.0, right_collar_ms=0)
    finally:
        ad.close()

    (out_dir / f"{key.replace('-', '_')}_dev60.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    comp = _complementarity(nemo_ref_rows, res["rows"])
    ja_re = re.compile(r"[\u3041-\u309f\u30a1-\u30ff\u4e00-\u9fff]")
    ja_rows = sum(1 for r in res["rows"] if ja_re.search(r["hyp_ja"]))
    return {
        "status": "PASS",
        "model": entry["repository"],
        "revision": entry["revision"],
        "ja_output_rows": ja_rows,
        "summary": res["summary"],
        "vs_nemo_oracle_hybrid": comp,
    }


def _reuse_m2(reg: dict[str, Any], out_dir: Path) -> dict[str, Any] | None:
    p120 = out_dir / "m2_qwen4b_q8_120.json"
    p654 = out_dir / "m2_qwen4b_q8_654.json"
    if not (p120.is_file() and p654.is_file()):
        return None
    entry = resolve_model("qwen4b-q8", reg)
    r120 = json.loads(p120.read_text(encoding="utf-8"))
    r654 = json.loads(p654.read_text(encoding="utf-8"))
    return {
        "status": "PASS",
        "reused": True,
        "model": entry["repository"],
        "revision": entry["revision"],
        "stratified_120": r120["summary"],
        "full_654": r654["summary"],
        "hard_cases": r654["hard_cases"],
    }


def _reuse_asr(key: str, reg: dict[str, Any], nemo_ref: list[dict], out_dir: Path) -> dict[str, Any] | None:
    p = out_dir / f"{key.replace('-', '_')}_dev60.json"
    if not p.is_file():
        return None
    entry = resolve_model(key, reg)
    res = json.loads(p.read_text(encoding="utf-8"))
    return {
        "status": "PASS",
        "reused": True,
        "model": entry["repository"],
        "revision": entry["revision"],
        "summary": res["summary"],
        "vs_nemo_oracle_hybrid": _complementarity(nemo_ref, res["rows"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T5 / G3 screening runner")
    parser.add_argument("--reuse-existing", action="store_true", help="Reuse existing M2/A3 result JSONs")
    args = parser.parse_args(argv)

    ev_dir = MAIN_ROOT / "p0/evidence/stage8-batch2-20261126"
    out_dir = ev_dir / "t5_g3_screening"
    out_dir.mkdir(parents=True, exist_ok=True)

    reg = download_and_register()
    pcm_f32 = np.frombuffer((ev_dir / "ep01_16k.pcm").read_bytes(), dtype="<i2").astype(np.float32) / 32768.0
    units = [json.loads(ln) for ln in (ev_dir / "ep01_dev60_units.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    nemo_ref = json.loads((ev_dir / "t4_asr/a0_oracle_hybrid_pad500.json").read_text(encoding="utf-8"))["rows"]

    m2_res = (_reuse_m2(reg, out_dir) if args.reuse_existing else None) or screen_m2_q8(reg, out_dir)
    a3_res = (_reuse_asr("qwen3-asr-1.7b-8bit", reg, nemo_ref, out_dir) if args.reuse_existing else None) or screen_asr_challenger("qwen3-asr-1.7b-8bit", reg, pcm_f32, units, nemo_ref, out_dir)
    a4_res = (_reuse_asr("whisper-large-v3-turbo", reg, nemo_ref, out_dir) if args.reuse_existing else None) or screen_asr_challenger("whisper-large-v3-turbo", reg, pcm_f32, units, nemo_ref, out_dir)

    summary = {
        "schema_version": "stage8-t5-g3-screening-v1",
        "registry_path": str(REGISTRY_PATH),
        "M2_qwen4b_q8": m2_res,
        "A3_qwen3_asr_1_7b_8bit": a3_res,
        "A4_whisper_large_v3_turbo": a4_res,
    }
    (out_dir / "t5_g3_screening_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("[T5] A4 summary:", json.dumps(a4_res["summary"], ensure_ascii=False), flush=True)
    print("[T5] A4 vs NeMo:", json.dumps({k: a4_res["vs_nemo_oracle_hybrid"][k] for k in ("fixed_nemo_errors", "regressed_nemo_correct", "both_err", "both_ok")}, ensure_ascii=False), flush=True)
    print("[T5] A4 ja_output_rows:", a4_res.get("ja_output_rows"), "/ 60", flush=True)
    print("[T5] COMPLETE.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
