"""Stage 8 dual-layer ASR, DOM flicker (LCP erased_chars), and fixed-denominator summary scoring."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


_SPEAKER_TAG = re.compile(r"（[^）]{1,16}）|\([^)]{1,16}\)")
_PUNCT_V2 = re.compile(r"[\s\u3000、。．，？！?!「」『』（）()［］\[\]・…〜～:：;；\"'’‘“”,.]+")
_PUNCT_LEGACY = re.compile(r"[\s\u3000、。．，？！?!「」『』（）()［］\[\]・…―ー〜～:：;；\"'’‘“”]+")


def lcp_len(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    idx = 0
    while idx < limit and a[idx] == b[idx]:
        idx += 1
    return idx


def erased_chars(previous: str, current: str) -> int:
    """Count characters visibly erased when updating previous -> current (0 on pure append)."""
    if not previous:
        return 0
    return len(previous) - lcp_len(previous, current)


def legacy_first_delta_edit_ratio(first_delta: str, final_text: str) -> float:
    return _edit_distance(first_delta, final_text) / max(1, len(final_text))


def normalize_ja_v2(text: str, *, strip_speakers: bool = False) -> str:
    """Stage 8 CER normalizer: NFKC, strips explicit punct/whitespace while preserving ー, digits, letters."""
    if strip_speakers:
        text = _SPEAKER_TAG.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return _PUNCT_V2.sub("", text)


def normalize_ja_legacy(text: str, *, strip_speakers: bool = False) -> str:
    """Legacy Stage 6/7 normalizer retained solely for backward compatibility (strips ー)."""
    if strip_speakers:
        text = _SPEAKER_TAG.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return _PUNCT_LEGACY.sub("", text)


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _sdi_counts(ref: str, hyp: str) -> tuple[int, int, int]:
    """Compute exact (S, D, I) via Wagner-Fischer Levenshtein backtrace (ties: sub < del < ins)."""
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    op = [[0] * (m + 1) for _ in range(n + 1)]  # 0=match/sub, 1=del, 2=ins
    for i in range(1, n + 1):
        dp[i][0] = i
        op[i][0] = 1
    for j in range(1, m + 1):
        dp[0][j] = j
        op[0][j] = 2
    for i in range(1, n + 1):
        rc = ref[i - 1]
        for j in range(1, m + 1):
            sub_cost = dp[i - 1][j - 1] + (0 if rc == hyp[j - 1] else 1)
            del_cost = dp[i - 1][j] + 1
            ins_cost = dp[i][j - 1] + 1
            best = sub_cost
            best_op = 0
            if del_cost < best:
                best = del_cost
                best_op = 1
            if ins_cost < best:
                best = ins_cost
                best_op = 2
            dp[i][j] = best
            op[i][j] = best_op

    s_cnt = d_cnt = i_cnt = 0
    i, j = n, m
    while i > 0 or j > 0:
        cur_op = op[i][j]
        if i > 0 and j > 0 and cur_op == 0:
            if ref[i - 1] != hyp[j - 1]:
                s_cnt += 1
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or cur_op == 1):
            d_cnt += 1
            i -= 1
        else:
            i_cnt += 1
            j -= 1
    return s_cnt, d_cnt, i_cnt


def score_asr(
    reference: str,
    hypothesis: str,
    normalizer: str = "cer_v2",
    *,
    strip_speakers: bool = False,
) -> dict[str, Any]:
    """Score ASR with S/D/I/N/CER using either Stage 8 cer_v2 or legacy_cer."""
    if normalizer == "cer_v2":
        norm_fn = normalize_ja_v2
    elif normalizer == "legacy_cer":
        norm_fn = normalize_ja_legacy
    else:
        raise ValueError(f"unknown normalizer: {normalizer!r}")

    ref = norm_fn(reference, strip_speakers=strip_speakers)
    hyp = norm_fn(hypothesis, strip_speakers=False)
    s_cnt, d_cnt, i_cnt = _sdi_counts(ref, hyp)
    dist = s_cnt + d_cnt + i_cnt
    n_len = len(ref)
    return {
        "normalizer": normalizer,
        "S": s_cnt,
        "D": d_cnt,
        "I": i_cnt,
        "N": n_len,
        "dist": dist,
        "hyp_len": len(hyp),
        "cer": round(dist / max(1, n_len), 6),
    }


def nearest_rank_quantile(values: list[float | None], q: float) -> float | None:
    """Nearest-rank quantile on fixed denominator where None encodes +infinity."""
    if not (0.0 < q <= 1.0):
        raise ValueError(f"q must be in (0, 1], got {q}")
    n = len(values)
    if n == 0:
        return None
    finites = sorted(float(v) for v in values if v is not None)
    inf_count = n - len(finites)
    idx = max(0, min(n - 1, math.ceil(q * n) - 1))
    if idx >= len(finites):
        return None
    _ = inf_count
    return round(finites[idx], 4)


def _distribution(values: list[float | None]) -> dict[str, Any]:
    eligible_n = len(values)
    finites = [float(v) for v in values if v is not None]
    missing_n = eligible_n - len(finites)
    return {
        "eligible_n": eligible_n,
        "missing_n": missing_n,
        "p50_ms": nearest_rank_quantile(values, 0.50),
        "p95_ms": nearest_rank_quantile(values, 0.95),
        "success_only_p50_ms": nearest_rank_quantile(finites, 0.50) if finites else None,
        "success_only_p95_ms": nearest_rank_quantile(finites, 0.95) if finites else None,
    }


def _caption_uid(ev: dict[str, Any]) -> str | None:
    key = ev.get("key")
    if isinstance(key, dict) and key.get("caption_id"):
        return f"{key.get('session_id', '')}:{key.get('epoch', 0)}:{key['caption_id']}"
    if ev.get("seq") is not None:
        return f"{ev.get('session_id', '')}:{ev.get('epoch', 0)}:{ev.get('seq')}"
    return None


def summarize_run(
    events: list[dict[str, Any]],
    units: list[dict[str, Any]],
    *,
    duration_s: float | None = None,
) -> dict[str, Any]:
    """Summarize Stage 8 events with separated ASR layers, delta kinds, DOM flicker, and missing +inf."""
    eligible_units = [u for u in units if u.get("eligible", True)]
    ref_ja_full = "".join(str(u.get("reference_ja", "")) for u in eligible_units)

    raw_asr_parts: list[str] = []
    sel_asr_parts: list[str] = []
    dom_asr_parts: list[str] = []
    visible_source_by_caption: dict[str, str] = {}

    preview_count = 0
    final_stream_count = 0
    unknown_delta_count = 0

    visible_text_by_caption: dict[str, str] = {}
    retraction_by_caption: dict[str, int] = {}
    captions_seen: set[str] = set()
    total_erased_chars = 0

    unit_final_visible: dict[str, float | None] = {u["unit_id"]: None for u in eligible_units if "unit_id" in u}
    unit_first_useful: dict[str, float | None] = {u["unit_id"]: None for u in eligible_units if "unit_id" in u}
    browser_ts_ns: list[int] = []

    for ev in events:
        etype = ev.get("event")
        kind = ev.get("kind")
        metrics = ev.get("metrics") or {}
        if ev.get("clock_domain") == "browser_performance" and isinstance(ev.get("ts_ns"), int):
            browser_ts_ns.append(ev["ts_ns"])

        if etype in ("ws_send", "ws_delta_sent", "final_token_stream"):
            if kind == "preview" or metrics.get("delta_kind") == "preview":
                preview_count += 1
            elif etype == "final_token_stream" or metrics.get("delta_kind") == "final_token_stream":
                final_stream_count += 1
            elif kind != "final":
                unknown_delta_count += 1

        if etype == "asr_end":
            raw_t = metrics.get("raw_text_ja")
            sel_t = metrics.get("selected_text_ja")
            if raw_t is None:
                raw_t = ev.get("text") or ""
            if sel_t is None:
                sel_t = ev.get("text") or ""
            if raw_t:
                raw_asr_parts.append(str(raw_t))
            if sel_t and metrics.get("mt_skipped") != "punct_only":
                sel_asr_parts.append(str(sel_t))
                cuid = _caption_uid(ev)
                if cuid is not None:
                    visible_source_by_caption[cuid] = str(sel_t)

        if etype in ("dom_visible", "dom_update", "dom_remove"):
            cuid = _caption_uid(ev) or f"anon:{len(captions_seen)}"
            txt = str(ev.get("text") or "")
            if etype == "dom_visible":
                captions_seen.add(cuid)
                visible_text_by_caption[cuid] = txt
            elif etype == "dom_update":
                captions_seen.add(cuid)
                prev_txt = visible_text_by_caption.get(cuid)
                if prev_txt is not None:
                    er = erased_chars(prev_txt, txt)
                    if er > 0:
                        total_erased_chars += er
                        retraction_by_caption[cuid] = retraction_by_caption.get(cuid, 0) + er
                visible_text_by_caption[cuid] = txt
            elif etype == "dom_remove":
                prev_txt = visible_text_by_caption.pop(cuid, None)
                if prev_txt and metrics.get("remove_reason") != "dwell_expired":
                    er = len(prev_txt)
                    total_erased_chars += er
                    retraction_by_caption[cuid] = retraction_by_caption.get(cuid, 0) + er

            uid = ev.get("unit_id")
            if uid in unit_final_visible and kind == "final" and etype in ("dom_visible", "dom_update"):
                lat = metrics.get("final_visible_latency_ms")
                if lat is not None and unit_final_visible[uid] is None:
                    unit_final_visible[uid] = float(lat)
                if cuid in visible_source_by_caption:
                    dom_asr_parts.append(visible_source_by_caption.pop(cuid))
            if uid in unit_first_useful and etype in ("dom_visible", "dom_update"):
                ulat = metrics.get("first_useful_latency_ms")
                if ulat is not None and unit_first_useful[uid] is None:
                    unit_first_useful[uid] = float(ulat)

    if duration_s is None:
        if len(browser_ts_ns) >= 2:
            duration_s = max(1e-6, (max(browser_ts_ns) - min(browser_ts_ns)) / 1_000_000_000.0)
        else:
            duration_s = 60.0
    duration_min = max(1e-6, float(duration_s) / 60.0)

    final_vals = list(unit_final_visible.values())
    useful_vals = list(unit_first_useful.values())
    visible_count = sum(1 for v in final_vals if v is not None)
    useful_count = sum(1 for v in useful_vals if v is not None)

    return {
        "deltas": {
            "preview_count": preview_count,
            "final_token_stream_count": final_stream_count,
            "unknown_delta_count": unknown_delta_count,
        },
        "flicker": {
            "total_erased_chars": total_erased_chars,
            "erased_chars_per_min": round(total_erased_chars / duration_min, 4),
            "captions_with_retraction": len(retraction_by_caption),
            "captions_total": len(captions_seen),
        },
        "asr": {
            "asr_raw": score_asr(ref_ja_full, "".join(raw_asr_parts), "cer_v2"),
            "asr_selected": score_asr(ref_ja_full, "".join(sel_asr_parts), "cer_v2"),
            "dom_backed_source": score_asr(ref_ja_full, "".join(dom_asr_parts), "cer_v2"),
        },
        "units": {
            "eligible_units": len(eligible_units),
            "final_visible_units": visible_count,
            "first_useful_units": useful_count,
            "missing_units": len(eligible_units) - visible_count,
        },
        "latency": {
            "first_useful_dom": _distribution(useful_vals),
            "unit_end_final_dom": _distribution(final_vals),
        },
    }
