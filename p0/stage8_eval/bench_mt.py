"""T3 & T5 MT runner: B-corrected baseline, M1 cheap ablations, and G3 MT challenger evaluation."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any

from p0.stage8_eval.contracts import canonical_json_sha256, load_manifest, sha256_text
from p0.stage8_eval.model_adapters import load_mt_adapter
from p0.stage8_eval.score import nearest_rank_quantile
from subtitle_service.local_pipeline import RATE

_ZH_PUNCT_STRIP = re.compile(r"[\s\u3000，。！？、：；“”\"'‘’（）()\-—…·《》「」『』.,!?;:]+")


def norm_zh_legacy(text: str) -> str:
    return _ZH_PUNCT_STRIP.sub("", text).strip()


def legacy_chrf4_score(hyp: str, ref: str, *, max_n: int = 4, beta: float = 2.0) -> float:
    """Reproduce legacy Stage 6/7 chrf_score(max_n=4, beta=2, 0-1 scale)."""
    h = norm_zh_legacy(hyp)
    r = norm_zh_legacy(ref)
    if not h or not r:
        return 0.0
    total_f = 0.0
    used_n = 0
    for n in range(1, max_n + 1):
        if len(h) < n or len(r) < n:
            continue
        hc = Counter(h[i : i + n] for i in range(len(h) - n + 1))
        rc = Counter(r[i : i + n] for i in range(len(r) - n + 1))
        overlap = sum((hc & rc).values())
        if overlap > 0:
            prec = overlap / max(1, sum(hc.values()))
            rec = overlap / max(1, sum(rc.values()))
            b2 = beta * beta
            total_f += (1.0 + b2) * prec * rec / (b2 * prec + rec)
        used_n += 1
    return round(total_f / max(1, used_n), 4)


CHRF2_STANDARD_SIGNATURE = "nrefs:1|case:mixed|eff:yes|nc:6|nw:0|space:no|version:pure-py-chrf2"


def chrf2_standard_0_100(hyp: str, ref: str, *, order: int = 6, beta: float = 2.0) -> float:
    """Standard SacreBLEU character chrF2 (nc=6, nw=0, no-whitespace, 0-100 scale)."""
    h = "".join(hyp.split())
    r = "".join(ref.split())
    if not h or not r:
        return 0.0
    total_f = 0.0
    for n in range(1, order + 1):
        if len(h) < n or len(r) < n:
            continue
        hc = Counter(h[i : i + n] for i in range(len(h) - n + 1))
        rc = Counter(r[i : i + n] for i in range(len(r) - n + 1))
        overlap = sum((hc & rc).values())
        if overlap > 0:
            prec = overlap / max(1, sum(hc.values()))
            rec = overlap / max(1, sum(rc.values()))
            b2 = beta * beta
            total_f += (1.0 + b2) * prec * rec / (b2 * prec + rec)
    return round((total_f / float(order)) * 100.0, 4)


def mt_arm_identity(
    *,
    model_key: str,
    revision: str,
    system_prompt: str,
    context_turns: int,
    repetition_penalty: float,
    use_kv_cache: bool,
    enable_kana_mask: bool,
    dataset_sha256: str,
) -> str:
    return canonical_json_sha256({
        "model_key": model_key,
        "revision": revision,
        "system_prompt_sha256": sha256_text(system_prompt),
        "context_turns": context_turns,
        "repetition_penalty": repetition_penalty,
        "use_kv_cache": use_kv_cache,
        "enable_kana_mask": enable_kana_mask,
        "dataset_sha256": dataset_sha256,
    })


def run_mt_cues(
    adapter,
    cues: list[dict[str, Any]],
    gold_zh: list[dict[str, Any]],
    *,
    context_turns: int = 4,
    max_tokens: int = 256,
    gap_reset_s: float = 12.0,
    main_end_s: float = 2690.0,
    hard_cue_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Translate ordered cues serially, enforcing past-accepted-only history and retaining all failures."""
    accepted_history: list[tuple[str, str]] = []
    last_end_s: float | None = None
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    valid_count = 0
    length_limit_count = 0
    kana_leaks = 0
    peak_mlx = 0.0

    for cue in cues:
        st_s = float(cue["start_s"])
        en_s = float(cue["end_s"])
        if last_end_s is not None and (st_s - last_end_s) > gap_reset_s:
            accepted_history.clear()
        used_hist = accepted_history[-context_turns:] if context_turns > 0 else []

        res = adapter.translate_turn(
            cue["clean_text"],
            history=used_hist,
            start_sample=int(round(st_s * RATE)),
            end_sample=int(round(en_s * RATE)),
            max_tokens=max_tokens,
        )
        lat = float(res.metrics.get("latency_ms", 0.0))
        latencies.append(lat)
        peak_mlx = max(peak_mlx, float(res.metrics.get("peak_mlx_mib", 0.0)))
        if res.stop_reason == "length_limit":
            length_limit_count += 1
        if res.metrics.get("kana_leak"):
            kana_leaks += 1
        if res.valid:
            valid_count += 1
            accepted_history.append((cue["clean_text"], res.text_zh))
        last_end_s = en_s

        rows.append({
            "cue_idx": cue["cue_idx"],
            "start_s": st_s,
            "end_s": en_s,
            "jp": cue["clean_text"],
            "oracle_zh": res.text_zh,
            "stop_reason": res.stop_reason,
            "valid": res.valid,
            "metrics": res.metrics,
        })

    # Score legacy 30s blocks + full concat + standard chrF2 0-100 across sampled blocks
    # Retaining empty outputs inside sampled blocks ensures failures stay in the denominator.
    legacy_30s: list[float] = []
    std_30s: list[float] = []
    legacy_15m: dict[str, list[float]] = {"0-15m": [], "15-30m": [], "30-45m": []}
    active_hyp_parts: list[str] = []
    active_gold_parts: list[str] = []
    for b0 in range(0, int(main_end_s), 30):
        b1 = b0 + 30
        has_cue = any(b0 <= (r["start_s"] + r["end_s"]) * 0.5 < b1 for r in rows)
        if not has_cue:
            continue
        h_blk = "".join(r["oracle_zh"] for r in rows if b0 <= (r["start_s"] + r["end_s"]) * 0.5 < b1)
        g_blk = "".join(g["clean_text"] for g in gold_zh if b0 <= (g["start_s"] + g["end_s"]) * 0.5 < b1)
        if not norm_zh_legacy(g_blk):
            continue
        active_hyp_parts.append(h_blk)
        active_gold_parts.append(g_blk)
        l_sc = legacy_chrf4_score(h_blk, g_blk)
        s_sc = chrf2_standard_0_100(h_blk, g_blk)
        legacy_30s.append(l_sc)
        std_30s.append(s_sc)
        bkt = "0-15m" if b0 < 900 else ("15-30m" if b0 < 1800 else "30-45m")
        legacy_15m[bkt].append(l_sc)

    mean = lambda xs: round(sum(xs) / max(1, len(xs)), 4)
    full_hyp = "".join(active_hyp_parts) if active_hyp_parts else "".join(r["oracle_zh"] for r in rows)
    full_gold = "".join(active_gold_parts) if active_gold_parts else "".join(g["clean_text"] for g in gold_zh)

    hard_rows = []
    if hard_cue_ids:
        gold_map = {}
        for r in rows:
            if r["cue_idx"] in hard_cue_ids:
                ov_g = [
                    g["clean_text"]
                    for g in gold_zh
                    if min(r["end_s"] + 0.2, g["end_s"]) - max(r["start_s"] - 0.2, g["start_s"]) > 0.0
                ]
                gold_map[r["cue_idx"]] = " / ".join(ov_g)
                hard_rows.append({
                    "cue_idx": r["cue_idx"],
                    "start_s": r["start_s"],
                    "jp": r["jp"],
                    "zh": r["oracle_zh"],
                    "valid": r["valid"],
                    "stop_reason": r["stop_reason"],
                    "gold_zh": gold_map[r["cue_idx"]],
                })

    summary = {
        "cues": len(rows),
        "valid_count": valid_count,
        "invalid_count": len(rows) - valid_count,
        "valid_rate": round(valid_count / max(1, len(rows)), 4),
        "length_limit_count": length_limit_count,
        "kana_leaks": kana_leaks,
        "mt_p50_ms": nearest_rank_quantile(latencies, 0.50),
        "mt_p95_ms": nearest_rank_quantile(latencies, 0.95),
        "peak_mlx_mib": round(peak_mlx, 2),
        "legacy_chrf4_fmean_0_1": {
            "blocks_30s_mean": mean(legacy_30s),
            "full_concat": legacy_chrf4_score(full_hyp, full_gold),
            "by_15m": {k: mean(v) for k, v in legacy_15m.items()},
        },
        "sacrebleu_chrf2_0_100": {
            "signature": CHRF2_STANDARD_SIGNATURE,
            "blocks_30s_mean": mean(std_30s),
            "full_concat": chrf2_standard_0_100(full_hyp, full_gold),
        },
    }
    return {"summary": summary, "rows": rows, "hard_cases": hard_rows}


def run_mt_arm(manifest: dict[str, Any], arm_id: str, *, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    if arm_id != (manifest.get("arm") or {}).get("arm_id"):
        raise ValueError(f"arm mismatch: CLI {arm_id!r} != manifest {(manifest.get('arm') or {}).get('arm_id')!r}")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf_probe"))
    from analyze_stage6_stage3_accuracy import JP_SRT_PATH, MAIN_END_S, ZH_SRT_PATH, parse_gold_zh_cues, parse_jp_cues

    jp_dialogue, _, _ = parse_jp_cues(JP_SRT_PATH)
    gold_zh, _ = parse_gold_zh_cues(ZH_SRT_PATH)
    params = (manifest.get("arm") or {}).get("parameters") or {}
    mt_key = (manifest.get("roles") or {}).get("mt")
    adapter = load_mt_adapter(str(mt_key), manifest, registry=registry)
    try:
        return run_mt_cues(
            adapter,
            jp_dialogue,
            gold_zh,
            context_turns=int(params.get("context_turns", 4)),
            max_tokens=int(params.get("max_output_tokens", 256)),
            main_end_s=MAIN_END_S,
        )
    finally:
        adapter.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 8 MT benchmark runner")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    if args.arm != (manifest.get("arm") or {}).get("arm_id"):
        sys.stderr.write(f"arm mismatch: --arm={args.arm} != manifest arm_id\n")
        return 2
    manifest_out = (manifest.get("execution") or {}).get("output_dir")
    if manifest_out and Path(args.out).resolve() != Path(manifest_out).resolve():
        sys.stderr.write(f"out mismatch: --out={args.out} != manifest output_dir={manifest_out}\n")
        return 2

    res = run_mt_arm(manifest, args.arm)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.arm}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(res["summary"], ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
