"""T3 Runner: Rebuild B-corrected (654 cues) and run M1 cheap ablations (120 stratified cues)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any

from p0.stage8_eval.bench_mt import run_mt_cues
from p0.stage8_eval.model_adapters import Qwen3MlxMTAdapter
from subtitle_service.local_pipeline import Qwen3Translator

MAIN_ROOT = Path("/Users/luo/Documents/github/jp-live-video-tanslate")
PERF_PROBE = Path(__file__).resolve().parents[1] / "perf_probe"
sys.path.insert(0, str(PERF_PROBE))

from analyze_stage6_stage3_accuracy import (  # noqa: E402
    JP_SRT_PATH,
    MAIN_END_S,
    ZH_SRT_PATH,
    parse_gold_zh_cues,
    parse_jp_cues,
)

QWEN4B_Q4_SNAP = (
    MAIN_ROOT
    / ".model-cache/hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots"
    / "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"
)
STRATIFIED_120_PATH = MAIN_ROOT / "p0/evidence/stage6-ep01-20261126/stratified_120_audit.json"
STAGE6_ATTR_PATH = MAIN_ROOT / "docs/experiments/stage6-ep01-2026-11-26/stage3_accuracy_attribution.json"
STAGE7_VER_PATH = MAIN_ROOT / "docs/experiments/stage7-p0-p1-p2-2026-11-26/stage7_verification.json"

SHORT_FAITHFUL_PROMPT = (
    "你是专业日剧台词翻译。请将日语台词准确译为自然简洁的简体中文。"
    "只输出中文译文，不要解释，不要输出思考过程，不要保留任何平假名或片假名，"
    "保持人称、受益关系、否定与数字准确。"
)


def load_stratified_120_cues() -> list[dict[str, Any]]:
    raw = json.loads(STRATIFIED_120_PATH.read_text(encoding="utf-8"))
    raw_sorted = sorted(raw, key=lambda r: int(r["cue_idx"]))
    return [
        {
            "cue_idx": int(r["cue_idx"]),
            "start_s": float(r["start_s"]),
            "end_s": float(r["end_s"]),
            "clean_text": str(r["clean_jp"]),
            "category": str(r["category"]),
        }
        for r in raw_sorted
    ]


def main() -> int:
    out_dir = MAIN_ROOT / "p0/evidence/stage8-batch2-20261126/t3_mt"
    out_dir.mkdir(parents=True, exist_ok=True)

    jp_dialogue, _, _ = parse_jp_cues(JP_SRT_PATH)
    gold_zh, _ = parse_gold_zh_cues(ZH_SRT_PATH)
    cues_120 = load_stratified_120_cues()

    s6_attr = json.loads(STAGE6_ATTR_PATH.read_text(encoding="utf-8"))
    hard_ids = {int(x["cue_idx"]) for x in s6_attr["per_cue_labels"] if x.get("primary_tag") in ("E3", "E4")}
    s7_ver = json.loads(STAGE7_VER_PATH.read_text(encoding="utf-8")) if STAGE7_VER_PATH.is_file() else {}

    # 1. Full 654-cue B-corrected baseline (fixed T2 KV cache, Stage 7 SYSTEM_PROMPT, ctx=4, rep=1.08)
    print("[T3] Running B-corrected-654 on Qwen3-4B-4bit...", flush=True)
    t0 = time.perf_counter()
    adapter_b0 = Qwen3MlxMTAdapter(
        QWEN4B_Q4_SNAP,
        model_key="qwen4b-q4",
        system_prompt=Qwen3Translator.SYSTEM_PROMPT,
        context_turns=4,
        repetition_penalty=1.08,
        enable_kana_mask=True,
        use_kv_cache=True,
        max_tokens=256,
    )
    try:
        b_corr_res = run_mt_cues(
            adapter_b0,
            jp_dialogue,
            gold_zh,
            context_turns=4,
            max_tokens=256,
            main_end_s=MAIN_END_S,
            hard_cue_ids=hard_ids,
        )
    finally:
        adapter_b0.close()
    b_corr_res["wall_s"] = round(time.perf_counter() - t0, 1)
    (out_dir / "b_corrected_654.json").write_text(
        json.dumps(b_corr_res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("[T3] B-corrected-654 done:", json.dumps(b_corr_res["summary"], ensure_ascii=False), flush=True)

    # 2. M1 single-factor ablations on chronological 120 stratified cues
    ablations = [
        ("C0_stage7_prompt_ctx4_rep108_kv", Qwen3Translator.SYSTEM_PROMPT, 4, 1.08, True),
        ("C1_short_prompt_ctx4_rep108_kv", SHORT_FAITHFUL_PROMPT, 4, 1.08, True),
        ("C2_0_stage7_prompt_ctx0_rep108", Qwen3Translator.SYSTEM_PROMPT, 0, 1.08, False),
        ("C2_2_stage7_prompt_ctx2_rep108_kv", Qwen3Translator.SYSTEM_PROMPT, 2, 1.08, True),
        ("C3_stage7_prompt_ctx4_rep100_kv", Qwen3Translator.SYSTEM_PROMPT, 4, 1.0, True),
        ("C5_fresh_stage7_prompt_ctx4_rep108", Qwen3Translator.SYSTEM_PROMPT, 4, 1.08, False),
    ]

    m1_results: dict[str, Any] = {}
    for arm_name, sys_prompt, ctx_n, rep, use_kv in ablations:
        print(f"[T3][M1] Running {arm_name} (120 cues)...", flush=True)
        ts = time.perf_counter()
        ad = Qwen3MlxMTAdapter(
            QWEN4B_Q4_SNAP,
            model_key="qwen4b-q4",
            system_prompt=sys_prompt,
            context_turns=ctx_n,
            repetition_penalty=rep,
            enable_kana_mask=True,
            use_kv_cache=use_kv,
            max_tokens=256,
        )
        try:
            res = run_mt_cues(
                ad,
                cues_120,
                gold_zh,
                context_turns=ctx_n,
                max_tokens=256,
                main_end_s=MAIN_END_S,
                hard_cue_ids=hard_ids,
            )
        finally:
            ad.close()
        res["wall_s"] = round(time.perf_counter() - ts, 1)
        res["config"] = {
            "prompt": "short" if sys_prompt == SHORT_FAITHFUL_PROMPT else "stage7",
            "context_turns": ctx_n,
            "repetition_penalty": rep,
            "use_kv_cache": use_kv,
        }
        (out_dir / f"m1_{arm_name}.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        m1_results[arm_name] = {
            "config": res["config"],
            "wall_s": res["wall_s"],
            "summary": res["summary"],
            "hard_cases": res["hard_cases"],
        }
        print(f"[T3][M1] {arm_name} done:", json.dumps(res["summary"], ensure_ascii=False), flush=True)

    # Pick winning M1 arm by sacrebleu_chrf2 full_concat (tie-break: blocks_30s_mean, then mt_p95_ms)
    winner_name = max(
        m1_results,
        key=lambda k: (
            m1_results[k]["summary"]["valid_rate"],
            m1_results[k]["summary"]["sacrebleu_chrf2_0_100"]["full_concat"],
            m1_results[k]["summary"]["sacrebleu_chrf2_0_100"]["blocks_30s_mean"],
            -(m1_results[k]["summary"]["mt_p95_ms"] or 9999.0),
        ),
    )

    summary_doc = {
        "schema_version": "stage8-t3-mt-summary-v1",
        "model_snapshot": str(QWEN4B_Q4_SNAP),
        "stage6_reference": {
            "chrf_30s": s6_attr["quantitative_full_episode"]["mt_accuracy"]["chrf_30s_blocks_mean"]["arm_b_oracle_vs_gold"],
            "chrf_full": s6_attr["quantitative_full_episode"]["mt_accuracy"]["chrf_full_concat"]["arm_b_oracle_vs_gold"],
            "mt_p50_ms": s6_attr["quantitative_full_episode"]["mt_accuracy"]["arm_b_stats"]["mt_p50_ms"],
            "mt_p95_ms": s6_attr["quantitative_full_episode"]["mt_accuracy"]["arm_b_stats"]["mt_p95_ms"],
        },
        "stage7_broken_kv_reference": s7_ver.get("part_b_arm_b_oracle_asr", {}),
        "b_corrected_654": {
            "wall_s": b_corr_res["wall_s"],
            "summary": b_corr_res["summary"],
            "hard_cases": b_corr_res["hard_cases"],
        },
        "m1_ablations_120": m1_results,
        "m1_winner": {
            "arm_name": winner_name,
            **m1_results[winner_name]["config"],
            "summary": m1_results[winner_name]["summary"],
        },
    }
    (out_dir / "t3_mt_summary.json").write_text(
        json.dumps(summary_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("[T3] COMPLETE. M1 Winner:", winner_name, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
