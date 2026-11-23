"""Shared Japanese normalization, CER, alignment, and resource measurement helpers."""

import re
import resource
import unicodedata

PUNCT_AND_SPACE = re.compile(r"[\s\u3000、。．，？！?!「」『』（）()［］\[\]・…―ー〜～:：;；\"'’‘“”]+")
SPEAKER_TAG = re.compile(r"（[^）]{1,16}）|\([^)]{1,16}\)")


def normalize_ja(text: str, strip_speakers: bool = False) -> str:
    if strip_speakers:
        text = SPEAKER_TAG.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return PUNCT_AND_SPACE.sub("", text)


def edit_distance(a: str, b: str) -> int:
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
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def compute_cer(hyp: str, ref: str, strip_speakers: bool = False) -> dict:
    h = normalize_ja(hyp, strip_speakers=False)
    r = normalize_ja(ref, strip_speakers=strip_speakers)
    dist = edit_distance(h, r)
    cer = dist / max(1, len(r))
    return {"dist": dist, "ref_len": len(r), "hyp_len": len(h), "cer": round(cer, 4)}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * p))))
    return round(xs[idx], 4)


def rss_mib() -> float:
    # macOS ru_maxrss is in bytes
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 2)


def score_d0_cer(rows: list[dict], units: list[dict]) -> dict:
    """Compute clip-level and 34-unit-level Japanese CER on D0."""
    by_clip_hyp: dict[str, list[str]] = {}
    for r in rows:
        by_clip_hyp.setdefault(r["clip"], []).append(r["text_ja"])

    by_clip_ref: dict[str, list[str]] = {}
    for u in units:
        by_clip_ref.setdefault(u["clip"], []).append(u["source_ja"])

    clip_metrics = {}
    total_dist = 0
    total_ref_len = 0
    for clip, refs in sorted(by_clip_ref.items()):
        hyp_text = "".join(by_clip_hyp.get(clip, []))
        ref_text = "".join(refs)
        m = compute_cer(hyp_text, ref_text)
        clip_metrics[clip] = m
        total_dist += m["dist"]
        total_ref_len += m["ref_len"]

    # Also compute exact overlap count per unit (how many units have non-empty aligned ASR)
    non_empty_units = 0
    for u in units:
        end = round(126.04 * 16000) if f"{u['clip']}/{u['group']}" == "wmf-04/153-155" else u["end_sample"]
        related = [r for r in rows if r["clip"] == u["clip"] and r["start_sample"] < end and r["end_sample"] > u["start_sample"]]
        if any(r["text_ja"].strip() for r in related):
            non_empty_units += 1

    return {
        "overall_cer": round(total_dist / max(1, total_ref_len), 4),
        "total_dist": total_dist,
        "total_ref_len": total_ref_len,
        "non_empty_units": non_empty_units,
        "by_clip": clip_metrics,
    }
