"""Stage 8 T7 aggregation: paired Chrome run summarization, DOM flicker stats, and bootstrap CI."""

from __future__ import annotations

import json
from pathlib import Path
import random
from typing import Any

from p0.stage8_eval.contracts import sha256_file
from p0.stage8_eval.score import erased_chars, nearest_rank_quantile


def paired_bootstrap_ci(
    deltas: list[float],
    *,
    resamples: int = 2000,
    seed: int = 20261126,
) -> dict[str, Any]:
    """Deterministic paired bootstrap 95% CI over per-scene deltas."""
    n = len(deltas)
    if n == 0:
        return {"n": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(max(1, resamples)):
        sample_sum = sum(deltas[rng.randrange(n)] for _ in range(n))
        means.append(sample_sum / n)
    means.sort()
    lo_idx = max(0, min(len(means) - 1, int(0.025 * len(means))))
    hi_idx = max(0, min(len(means) - 1, int(0.975 * len(means))))
    return {
        "n": n,
        "mean": round(sum(deltas) / n, 6),
        "ci95_low": round(means[lo_idx], 6),
        "ci95_high": round(means[hi_idx], 6),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def _summarize_dom_events(dom_events: list[dict[str, Any]]) -> dict[str, Any]:
    streaming = 0
    final = 0
    status = 0
    empty = 0
    shrink_flickers = 0
    streaming_erased_chars = 0
    prev_stream_text = ""
    in_stream = False

    for ev in dom_events:
        is_status = bool(ev.get("is_status"))
        text = str(ev.get("text_zh") or "")
        is_streaming = bool(ev.get("is_streaming"))
        if is_status:
            status += 1
            in_stream = False
            prev_stream_text = ""
            continue
        if not text:
            empty += 1
            in_stream = False
            prev_stream_text = ""
            continue
        if is_streaming:
            streaming += 1
            if in_stream and prev_stream_text:
                er = erased_chars(prev_stream_text, text)
                streaming_erased_chars += er
                if len(text) < len(prev_stream_text) and er > 0:
                    shrink_flickers += 1
            in_stream = True
            prev_stream_text = text
        else:
            final += 1
            in_stream = False
            prev_stream_text = ""

    return {
        "total": len(dom_events),
        "streaming": streaming,
        "final": final,
        "status": status,
        "empty": empty,
        "shrink_flickers": shrink_flickers,
        "streaming_erased_chars": streaming_erased_chars,
    }


def _summarize_cdp_diagnostics(cdp_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cdp_rows:
        return None
    by_seq: dict[Any, tuple[str, str]] = {}
    preview_t: dict[Any, float] = {}
    final_t: dict[Any, float] = {}
    dom_visible = 0
    dom_update = 0
    total_erased = 0
    preview_to_fts_shrinks = 0
    kind_counts: dict[str, int] = {"preview": 0, "final_token_stream": 0, "final": 0, "unknown": 0}
    ws_preview = 0
    ws_fts = 0

    for ev in cdp_rows:
        stage = ev.get("stage")
        if stage == "ws_received" and ev.get("type") == "caption_delta":
            dk = str(ev.get("delta_kind") or "")
            if dk == "preview":
                ws_preview += 1
            elif dk == "final_token_stream":
                ws_fts += 1
            continue
        if stage not in ("dom_visible", "dom_update"):
            continue
        if stage == "dom_visible":
            dom_visible += 1
        else:
            dom_update += 1
        seq = ev.get("seq")
        kind = str(ev.get("kind") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        txt = str(ev.get("text_zh") or "")
        t_ms = float(ev.get("t", 0.0))
        if kind == "preview" and seq not in preview_t:
            preview_t[seq] = t_ms
        if kind == "final" and seq not in final_t:
            final_t[seq] = t_ms
        if seq in by_seq:
            pk, pt = by_seq[seq]
            er = erased_chars(pt, txt)
            total_erased += er
            if pk == "preview" and kind == "final_token_stream" and len(txt) < len(pt) and er > 0:
                preview_to_fts_shrinks += 1
        by_seq[seq] = (kind, txt)

    exposures = [round(final_t[s] - preview_t[s], 1) for s in sorted(preview_t) if s in final_t]
    return {
        "dom_visible": dom_visible,
        "dom_update": dom_update,
        "dom_kind_counts": kind_counts,
        "ws_delta_preview": ws_preview,
        "ws_delta_final_token_stream": ws_fts,
        "total_erased_chars": total_erased,
        "preview_to_fts_shrinks": preview_to_fts_shrinks,
        "preview_to_final_median_ms": nearest_rank_quantile(exposures, 0.50) if exposures else None,
        "preview_to_final_max_ms": max(exposures) if exposures else None,
    }


def _summarize_model_trace(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    pcm_times_by_sid: dict[str, list[tuple[int, float]]] = {}
    asr_start_by_sid: dict[str, dict[int, float]] = {}
    mt_start_by_sid: dict[str, dict[int, float]] = {}
    asr_ms: list[float] = []
    mt_ms: list[float] = []
    vad_wait_ms: list[float] = []
    seg_end_to_ws_ms: list[float] = []
    draft_sent = 0
    ws_sent = 0
    valid_finals = 0
    length_limit_count = 0
    length_limit_dropped = 0
    cut_reasons: dict[str, int] = {}

    for ev in rows:
        st = ev.get("stage")
        t = float(ev.get("t", 0.0))
        sid_key = str(ev.get("session_id") or "")
        pcm_times = pcm_times_by_sid.setdefault(sid_key, [])
        asr_start = asr_start_by_sid.setdefault(sid_key, {})
        mt_start = mt_start_by_sid.setdefault(sid_key, {})
        if st == "pcm_accepted":
            pcm_times.append((int(ev.get("end_sample", 0)), t))
        elif st == "vad_released":
            cr = str(ev.get("cut_reason") or "unknown")
            cut_reasons[cr] = cut_reasons.get(cr, 0) + 1
            en = int(ev.get("end_sample", 0))
            for s, pt in pcm_times:
                if s >= en:
                    vad_wait_ms.append(round((t - pt) * 1000.0, 2))
                    break
        elif st == "asr_start" and ev.get("segment_id") is not None:
            asr_start[int(ev["segment_id"])] = t
        elif st == "asr_end" and ev.get("segment_id") is not None:
            seg_id = int(ev["segment_id"])
            if seg_id in asr_start:
                asr_ms.append(round((t - asr_start[seg_id]) * 1000.0, 2))
        elif st == "mt_start" and ev.get("segment_id") is not None:
            mt_start[int(ev["segment_id"])] = t
        elif st == "mt_end" and ev.get("segment_id") is not None:
            seg_id = int(ev["segment_id"])
            if seg_id in mt_start:
                mt_ms.append(round((t - mt_start[seg_id]) * 1000.0, 2))
            if ev.get("stop_reason") == "length_limit":
                length_limit_count += 1
            if ev.get("dropped_reason") == "length_limit":
                length_limit_dropped += 1
            if ev.get("valid") is not False and ev.get("text_zh"):
                valid_finals += 1
        elif st == "draft_sent":
            draft_sent += 1
        elif st == "ws_sent":
            ws_sent += 1
            en = int(ev.get("end_sample", 0))
            for s, pt in pcm_times:
                if s >= en:
                    seg_end_to_ws_ms.append(round((t - pt) * 1000.0, 2))
                    break

    return {
        "asr_p50_ms": nearest_rank_quantile(asr_ms, 0.50) if asr_ms else None,
        "mt_p50_ms": nearest_rank_quantile(mt_ms, 0.50) if mt_ms else None,
        "mt_p95_ms": nearest_rank_quantile(mt_ms, 0.95) if mt_ms else None,
        "vad_wait_p50_ms": nearest_rank_quantile(vad_wait_ms, 0.50) if vad_wait_ms else None,
        "seg_end_to_ws_p50_ms": nearest_rank_quantile(seg_end_to_ws_ms, 0.50) if seg_end_to_ws_ms else None,
        "seg_end_to_ws_p95_ms": nearest_rank_quantile(seg_end_to_ws_ms, 0.95) if seg_end_to_ws_ms else None,
        "draft_sent_count": draft_sent,
        "ws_sent_count": ws_sent,
        "valid_final_count": valid_finals,
        "length_limit_count": length_limit_count,
        "length_limit_dropped_count": length_limit_dropped,
        "cut_reasons": cut_reasons,
    }


def summarize_chrome_run_dir(run_dir: Path) -> dict[str, Any]:
    """Summarize one self-contained Chrome run directory without dropping failures."""
    run_path = Path(run_dir)
    summary_path = run_path / "chrome_run_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"missing chrome_run_summary.json in {run_path}")
    chrome_doc = json.loads(summary_path.read_text(encoding="utf-8"))
    res = chrome_doc.get("result") or {}
    manifest_path = run_path / "manifest.json"
    manifest_present = manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_present else {}

    dom_events = _read_jsonl(run_path / "dom_events.jsonl")
    cdp_rows = _read_jsonl(run_path / "cdp_diagnostics.jsonl")
    model_rows = _read_jsonl(run_path / "model.jsonl")

    swap_raw = res.get("swap_growth_mib")
    abort_raw = res.get("resource_abort")
    lifecycle_raw = res.get("lifecycle")

    return {
        "run_dir": str(run_path),
        "manifest_present": manifest_present,
        "run_id": chrome_doc.get("run_id") or manifest.get("run_id"),
        "experiment_id": chrome_doc.get("experiment_id") or manifest.get("experiment_id"),
        "arm_id": chrome_doc.get("arm_id") or (manifest.get("arm") or {}).get("arm_id"),
        "baseline_role": chrome_doc.get("baseline_role") or (manifest.get("arm") or {}).get("baseline_role"),
        "baseline_sha": manifest.get("baseline_sha"),
        "actual_repo_sha": manifest.get("actual_repo_sha"),
        "media_sha256": (manifest.get("data") or {}).get("media_sha256"),
        "thresholds_sha256": (manifest.get("thresholds") or {}).get("sha256"),
        "duration_s": chrome_doc.get("duration_s"),
        "current_time_s": res.get("current_time"),
        "wall_seconds": res.get("wall_seconds"),
        "total_frames": res.get("total_frames"),
        "dropped_frames": res.get("dropped_frames"),
        "drop_rate_pct": res.get("drop_rate_pct"),
        "srv_peak_rss_mib": res.get("srv_peak_rss_mib"),
        "srv_peak_cpu_pct": res.get("srv_peak_cpu_pct"),
        "chrome_peak_rss_mib": res.get("chrome_peak_rss_mib"),
        "swap_growth_mib": float(swap_raw) if swap_raw is not None else None,
        "resource_abort": bool(abort_raw) if abort_raw is not None else None,
        "lifecycle": dict(lifecycle_raw) if isinstance(lifecycle_raw, dict) else None,
        "dom_events": _summarize_dom_events(dom_events),
        "cdp_diagnostics": _summarize_cdp_diagnostics(cdp_rows),
        "model_trace": _summarize_model_trace(model_rows),
    }


def aggregate_runs(
    run_dirs: list[Path],
    units_path: Path | None,
    judgments_path: Path | None,
    thresholds_path: Path,
) -> dict[str, Any]:
    """Aggregate paired Chrome runs, fixed units/judgments metadata, and thresholds hash."""
    t_path = Path(thresholds_path)
    thresholds = json.loads(t_path.read_text(encoding="utf-8"))
    thresholds_sha = sha256_file(t_path)

    units_sha = sha256_file(units_path) if units_path is not None and Path(units_path).is_file() else None
    units = (
        [json.loads(ln) for ln in Path(units_path).read_text(encoding="utf-8").splitlines() if ln.strip()]
        if units_sha is not None and str(units_path).endswith(".jsonl")
        else (json.loads(Path(units_path).read_text(encoding="utf-8")) if units_sha is not None else [])
    )
    judgments_sha = sha256_file(judgments_path) if judgments_path is not None and Path(judgments_path).is_file() else None
    judgments = json.loads(Path(judgments_path).read_text(encoding="utf-8")) if judgments_sha is not None else None

    runs = [summarize_chrome_run_dir(d) for d in run_dirs]
    roles: dict[str, dict[str, Any]] = {}
    for r in runs:
        role = str(r.get("baseline_role") or "")
        if role:
            if role in roles:
                raise ValueError(f"duplicate baseline_role: {role}")
            roles[role] = r

    cand = roles.get("candidate")
    dep = roles.get("deployed")
    sub_off = roles.get("subtitles_off")

    comparison: dict[str, Any] = {}
    if cand and dep:
        c_dom = cand["dom_events"]
        d_dom = dep["dom_events"]
        comparison["dom_shrink_flickers_delta"] = c_dom["shrink_flickers"] - d_dom["shrink_flickers"]
        comparison["dom_streaming_erased_chars_delta"] = (
            c_dom["streaming_erased_chars"] - d_dom["streaming_erased_chars"]
        )
        c_mt = cand.get("model_trace") or {}
        d_mt = dep.get("model_trace") or {}
        if c_mt.get("seg_end_to_ws_p95_ms") is not None and d_mt.get("seg_end_to_ws_p95_ms") is not None:
            comparison["seg_end_to_ws_p95_delta_ms"] = round(
                c_mt["seg_end_to_ws_p95_ms"] - d_mt["seg_end_to_ws_p95_ms"], 2
            )
    if cand and sub_off and cand.get("drop_rate_pct") is not None and sub_off.get("drop_rate_pct") is not None:
        comparison["drop_rate_delta_vs_subtitles_off_pp"] = round(
            float(cand["drop_rate_pct"]) - float(sub_off["drop_rate_pct"]), 3
        )
    if dep and sub_off and dep.get("drop_rate_pct") is not None and sub_off.get("drop_rate_pct") is not None:
        comparison["deployed_drop_rate_delta_vs_subtitles_off_pp"] = round(
            float(dep["drop_rate_pct"]) - float(sub_off["drop_rate_pct"]), 3
        )

    holdout_data = judgments.get("holdout") if isinstance(judgments, dict) else None
    human_review_data = judgments.get("human_review") if isinstance(judgments, dict) else None

    return {
        "schema_version": "stage8-t7-aggregate-v1",
        "thresholds_sha256": thresholds_sha,
        "thresholds_schema": thresholds.get("schema_version"),
        "units_sha256": units_sha,
        "units_count": len(units) if isinstance(units, list) else 0,
        "judgments_sha256": judgments_sha,
        "roles_present": sorted(roles.keys()),
        "runs": runs,
        "roles": roles,
        "comparison": comparison,
        "holdout": holdout_data,
        "human_review": human_review_data,
    }
