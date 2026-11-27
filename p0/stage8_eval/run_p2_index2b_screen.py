"""P2 Runner: Isolated 26-case (18 E3/E4 hard + 8 OK protected) dual-track screen for Index-Translate-2B-GGUF vs Qwen3-4B-4bit."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from p0.stage8_eval.bench_mt import chrf2_standard_0_100, legacy_chrf4_score
from p0.stage8_eval.contracts import sha256_file
from p0.stage8_eval.model_adapters import LlamaServerMTAdapter, Qwen3MlxMTAdapter, resolve_model
from p0.stage8_eval.score import nearest_rank_quantile
from subtitle_service.local_pipeline import Qwen3Translator

MAIN_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = MAIN_ROOT / ".model-cache"
REGISTRY_PATH = Path(__file__).resolve().parent / "model_registry.json"
S120_PATH = MAIN_ROOT / "p0/evidence/stage6-ep01-20261126/stratified_120_audit.json"
ATTR_PATH = MAIN_ROOT / "docs/experiments/stage6-ep01-2026-11-26/stage3_accuracy_attribution.json"
B_CORRECTED_654_PATH = MAIN_ROOT / "p0/evidence/stage8-batch2-20261126/t3_mt/b_corrected_654.json"
QWEN4B_Q4_SNAPSHOT = CACHE_ROOT / "hub/models--mlx-community--Qwen3-4B-Instruct-2507-4bit/snapshots/50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b"


def build_p2_cohort(*, ok_limit: int = 8) -> list[dict[str, Any]]:
    """Build 26-case cohort: all 18 E3/E4 hard cases + 8 diverse OK protected cases."""
    s120 = {int(r["cue_idx"]): r for r in json.loads(S120_PATH.read_text(encoding="utf-8"))}
    attr = json.loads(ATTR_PATH.read_text(encoding="utf-8"))
    labels_by_cue = {int(x["cue_idx"]): x for x in attr["per_cue_labels"]}

    b654 = json.loads(B_CORRECTED_654_PATH.read_text(encoding="utf-8"))
    b_ctx4_map = {int(r["cue_idx"]): str(r["oracle_zh"]) for r in b654["rows"]}

    hard_cues = sorted(c for c, x in labels_by_cue.items() if x.get("primary_tag") in ("E3", "E4"))
    ok_all = sorted(c for c, x in labels_by_cue.items() if x.get("primary_tag") == "OK")

    # Select diverse OK cues across categories first, then fill to ok_limit
    ok_selected: list[int] = []
    seen_cats: set[str] = set()
    for cid in ok_all:
        cat = str(s120[cid]["category"])
        if cat not in seen_cats:
            seen_cats.add(cat)
            ok_selected.append(cid)
        if len(ok_selected) >= ok_limit:
            break
    if len(ok_selected) < ok_limit:
        for cid in ok_all:
            if cid not in ok_selected:
                ok_selected.append(cid)
            if len(ok_selected) >= ok_limit:
                break

    cohort: list[dict[str, Any]] = []
    for cid in hard_cues + ok_selected:
        row = s120[cid]
        lbl = labels_by_cue[cid]
        live_asr = "".join(str(it.get("asr_ja") or "") for it in (row.get("arm_a_items") or [])).strip()
        cohort.append({
            "cue_idx": cid,
            "category": str(row["category"]),
            "primary_tag": str(lbl["primary_tag"]),
            "note": str(lbl.get("note") or ""),
            "start_s": float(row["start_s"]),
            "end_s": float(row["end_s"]),
            "clean_jp": str(row["clean_jp"]),
            "live_asr_ja": live_asr,
            "gold_zh": str(row["gold_zh"]),
            "qwen4b_ctx4_zh": b_ctx4_map.get(cid, ""),
        })
    return cohort


def _score_predictions(rows: list[dict[str, Any]], *, field: str, tag_filter: set[str] | None = None) -> dict[str, Any]:
    sub = [r for r in rows if tag_filter is None or r["primary_tag"] in tag_filter]
    if not sub:
        return {"count": 0}
    raw_hyps = [str(r[field]["text_zh"]) for r in sub]
    deliv_hyps = [str(r[field]["text_zh"]) if r[field]["valid"] else "" for r in sub]
    refs = [str(r["gold_zh"]) for r in sub]
    raw_chrf2_cases = [chrf2_standard_0_100(h, ref) for h, ref in zip(raw_hyps, refs)]
    deliv_chrf2_cases = [chrf2_standard_0_100(h, ref) for h, ref in zip(deliv_hyps, refs)]
    leg_cases = [legacy_chrf4_score(h, ref) for h, ref in zip(deliv_hyps, refs)]

    attempt_lats: list[float | None] = [
        float(r[field]["latency_ms"]) if r[field].get("latency_ms") is not None else None for r in sub
    ]
    deliv_lats: list[float | None] = [
        float(r[field]["latency_ms"]) if (r[field]["valid"] and r[field].get("latency_ms") is not None) else None
        for r in sub
    ]
    valid_pairs = [(str(r[field]["text_zh"]), str(r["gold_zh"]), r[field].get("latency_ms")) for r in sub if r[field]["valid"]]
    valid_lats = [float(lat) for _, _, lat in valid_pairs if lat is not None]
    mems = [float(r[field].get("mem_mib") or 0.0) for r in sub]
    valid_count = len(valid_pairs)

    return {
        "count": len(sub),
        "valid_count": valid_count,
        "availability_rate": round(valid_count / len(sub), 4),
        "length_limit_count": sum(1 for r in sub if r[field]["stop_reason"] == "length_limit"),
        "think_leaks": sum(1 for r in sub if r[field].get("think_leak")),
        "kana_leaks": sum(1 for r in sub if r[field].get("kana_leak")),
        "latency_p50_ms": nearest_rank_quantile(deliv_lats, 0.50),
        "latency_p95_ms": nearest_rank_quantile(deliv_lats, 0.95),
        "peak_mem_mib": round(max(mems), 2) if any(mems) else 0.0,
        "chrf2_case_mean": round(sum(deliv_chrf2_cases) / len(deliv_chrf2_cases), 4),
        "chrf2_concat": chrf2_standard_0_100("".join(deliv_hyps), "".join(refs)),
        "diagnostic_raw": {
            "attempt_p50_ms": nearest_rank_quantile(attempt_lats, 0.50),
            "attempt_p95_ms": nearest_rank_quantile(attempt_lats, 0.95),
            "chrf2_case_mean": round(sum(raw_chrf2_cases) / len(raw_chrf2_cases), 4),
            "chrf2_concat": chrf2_standard_0_100("".join(raw_hyps), "".join(refs)),
        },
        "valid_only": {
            "count": valid_count,
            "success_p50_ms": nearest_rank_quantile(valid_lats, 0.50) if valid_lats else None,
            "success_p95_ms": nearest_rank_quantile(valid_lats, 0.95) if valid_lats else None,
            "chrf2_concat": (
                chrf2_standard_0_100("".join(h for h, _, _ in valid_pairs), "".join(g for _, g, _ in valid_pairs))
                if valid_pairs
                else None
            ),
        },
        "delivered": {
            "denominator": len(sub),
            "availability_rate": round(valid_count / len(sub), 4),
            "completion_p50_ms": nearest_rank_quantile(deliv_lats, 0.50),
            "completion_p95_ms": nearest_rank_quantile(deliv_lats, 0.95),
            "chrf2_case_mean": round(sum(deliv_chrf2_cases) / len(deliv_chrf2_cases), 4),
            "chrf2_concat": chrf2_standard_0_100("".join(deliv_hyps), "".join(refs)),
        },
        "legacy_chrf4_concat": legacy_chrf4_score("".join(deliv_hyps), "".join(refs)),
        "legacy_chrf4_case_mean": round(sum(leg_cases) / len(leg_cases), 4),
    }


def _eval_adapter(adapter, cohort: list[dict[str, Any]], *, track: str) -> dict[int, dict[str, Any]]:
    # Single warmup cue before timing
    _ = adapter.translate_turn("おはようございます。", history=[], max_tokens=256)
    results: dict[int, dict[str, Any]] = {}
    for item in cohort:
        src = str(item["clean_jp"] if track == "clean_jp" else item["live_asr_ja"]).strip()
        if not src:
            results[int(item["cue_idx"])] = {
                "source_ja": "",
                "text_zh": "",
                "stop_reason": "empty_input",
                "valid": False,
                "latency_ms": None,
                "mem_mib": 0.0,
                "think_leak": False,
                "kana_leak": False,
                "gen_tokens": 0,
            }
            continue
        res = adapter.translate_turn(src, history=[], max_tokens=256)
        mem = res.metrics.get("rss_mib") or res.metrics.get("peak_mlx_mib") or 0.0
        results[int(item["cue_idx"])] = {
            "source_ja": src,
            "text_zh": res.text_zh,
            "stop_reason": res.stop_reason,
            "valid": bool(res.valid),
            "latency_ms": float(res.metrics["latency_ms"]) if res.metrics.get("latency_ms") is not None else None,
            "mem_mib": float(mem),
            "think_leak": bool(res.metrics.get("think_leak", False)),
            "kana_leak": bool(res.metrics.get("kana_leak", False)),
            "gen_tokens": int(res.metrics.get("gen_tokens", 0)),
        }
    return results


def main() -> int:
    reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    idx_entry = resolve_model("index-translate-2b-q4km", reg)
    gguf_path = Path(idx_entry["artifacts"][0]["path"])

    cohort = build_p2_cohort(ok_limit=8)

    # Pass 1: Index-Translate-2B-Q4_K_M isolated via llama-server (shut down before loading MLX!)
    idx_adapter = LlamaServerMTAdapter(gguf_path, model_key="index-translate-2b-q4km", max_tokens=256)
    try:
        idx_clean = _eval_adapter(idx_adapter, cohort, track="clean_jp")
        idx_asr = _eval_adapter(idx_adapter, cohort, track="live_asr_ja")
    finally:
        idx_adapter.close()

    # Pass 2: Qwen3-4B-Instruct-2507-4bit (0-context, Stage 7 prompt, greedy)
    qwen_adapter = Qwen3MlxMTAdapter(
        QWEN4B_Q4_SNAPSHOT,
        model_key="qwen4b-q4",
        system_prompt=Qwen3Translator.SYSTEM_PROMPT,
        context_turns=0,
        repetition_penalty=1.08,
        enable_kana_mask=True,
        use_kv_cache=False,
        max_tokens=256,
    )
    try:
        qwen_clean = _eval_adapter(qwen_adapter, cohort, track="clean_jp")
        qwen_asr = _eval_adapter(qwen_adapter, cohort, track="live_asr_ja")
    finally:
        qwen_adapter.close()

    clean_rows = []
    asr_rows = []
    for item in cohort:
        cid = int(item["cue_idx"])
        clean_rows.append({
            **item,
            "index2b": idx_clean[cid],
            "qwen4b_0ctx": qwen_clean[cid],
        })
        asr_rows.append({
            **item,
            "index2b": idx_asr[cid],
            "qwen4b_0ctx": qwen_asr[cid],
        })

    summary = {
        "schema_version": "stage8-p2-index2b-screen-v1",
        "models": {
            "index2b": {
                "repository": idx_entry["repository"],
                "revision": idx_entry["revision"],
                "precision": idx_entry["precision"],
                "gguf_sha256": sha256_file(gguf_path),
            },
            "qwen4b_q4": {
                "repository": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
                "revision": "50d427756c6b1b2fe0c0a10f67fbda1fc8e82c1b",
                "precision": "4bit",
            },
        },
        "clean_jp_track": {
            "index2b_all26": _score_predictions(clean_rows, field="index2b"),
            "qwen4b_0ctx_all26": _score_predictions(clean_rows, field="qwen4b_0ctx"),
            "index2b_hard18": _score_predictions(clean_rows, field="index2b", tag_filter={"E3", "E4"}),
            "qwen4b_0ctx_hard18": _score_predictions(clean_rows, field="qwen4b_0ctx", tag_filter={"E3", "E4"}),
            "index2b_ok8": _score_predictions(clean_rows, field="index2b", tag_filter={"OK"}),
            "qwen4b_0ctx_ok8": _score_predictions(clean_rows, field="qwen4b_0ctx", tag_filter={"OK"}),
        },
        "live_asr_track": {
            "index2b_all": _score_predictions(asr_rows, field="index2b"),
            "qwen4b_0ctx_all": _score_predictions(asr_rows, field="qwen4b_0ctx"),
        },
        "clean_jp_rows": clean_rows,
        "live_asr_rows": asr_rows,
    }

    out_dir = MAIN_ROOT / "p0/evidence/stage8-batch3-20261127/p2_index2b_screen"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "p2_index2b_screen_summary.json"
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=== P2 Dual-Track Summary ===")
    for track_name, section in (("clean_jp", summary["clean_jp_track"]), ("live_asr_ja", summary["live_asr_track"])):
        print(f"-- {track_name} --")
        for k, m in section.items():
            print(
                f"  {k:20s} | n={m['count']} valid={m['valid_count']} len_lim={m['length_limit_count']} "
                f"think={m['think_leaks']} kana={m['kana_leaks']} | p50={m['latency_p50_ms']}ms p95={m['latency_p95_ms']}ms "
                f"| mem={m['peak_mem_mib']}MiB | chrf2_mean={m['chrf2_case_mean']} concat={m['chrf2_concat']}"
            )

    print("\n=== Side-by-Side: All 18 E3/E4 Hard + 8 OK Protected Cases (clean_jp) ===")
    for r in clean_rows:
        print(
            f"[{r['primary_tag']}] cue={r['cue_idx']} ({r['category']}) note={r['note']}\n"
            f"  JP        : {r['clean_jp']}\n"
            f"  GOLD      : {r['gold_zh']}\n"
            f"  QWEN4_CTX4: {r['qwen4b_ctx4_zh']}\n"
            f"  QWEN4_0CTX: {r['qwen4b_0ctx']['text_zh']}\n"
            f"  INDEX2B   : {r['index2b']['text_zh']}"
        )

    print("\n=== Live ASR Track Side-by-Side (all non-empty cases) ===")
    for r in asr_rows:
        print(
            f"[{r['primary_tag']}] cue={r['cue_idx']} ASR={r['live_asr_ja']}\n"
            f"  GOLD   : {r['gold_zh']}\n"
            f"  QWEN4  : {r['qwen4b_0ctx']['text_zh']}\n"
            f"  INDEX2B: {r['index2b']['text_zh']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
