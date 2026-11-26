"""Token-level KV cache prefix planning and ledger reconciliation for Qwen3Translator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any


def canonical_messages(
    system_prompt: str,
    history: list[tuple[str, str]],
    text_ja: str,
    *,
    user_prefix: str = "请翻译以下日语台词为简体中文：\n",
) -> list[dict[str, str]]:
    """Build canonical ChatML messages from committed prior turns and current Japanese text."""
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for prev_ja, prev_zh in history:
        messages.append({"role": "user", "content": f"{user_prefix}{prev_ja}"})
        messages.append({"role": "assistant", "content": prev_zh})
    messages.append({"role": "user", "content": f"{user_prefix}{text_ja}"})
    return messages


def canonical_prompt_ids(tokenizer: Any, messages: list[dict[str, str]]) -> list[int]:
    """Render and tokenize canonical prompt IDs without inserting extra special tokens."""
    prompt = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=False,
    )
    ids = tokenizer.encode(prompt, add_special_tokens=False)
    return [int(x) for x in ids]


def common_prefix_len(a: list[int], b: list[int]) -> int:
    """Return length of the longest exact token-ID prefix shared by a and b."""
    limit = min(len(a), len(b))
    idx = 0
    while idx < limit and a[idx] == b[idx]:
        idx += 1
    return idx


def compute_cache_plan(
    cache_tokens: list[int],
    target_ids: list[int],
) -> tuple[int, list[int]]:
    """Compute (trim_count, feed_ids) to align cache_tokens cleanly onto target_ids."""
    if not target_ids:
        raise ValueError("target_ids must be non-empty")
    prefix = common_prefix_len(cache_tokens, target_ids)
    # mlx_lm.generate_step requires at least 1 token in feed_ids to sample the next token.
    if prefix >= len(target_ids):
        prefix = len(target_ids) - 1
    trim_n = len(cache_tokens) - prefix
    feed_ids = list(target_ids[prefix:])
    return trim_n, feed_ids


def reconcile_cache_ledger(
    pre_tokens: list[int],
    feed_ids: list[int],
    yielded_ids: list[int],
    pre_offset: int,
    post_offset: int,
) -> tuple[list[int] | None, str]:
    """Reconcile cached token IDs with actual KV offset after generate_step finishes.

    Handles both mlx-lm look-ahead (where sampled EOS is evaluated into KV before
    yielding) and non-look-ahead generators. Any unexpected delta invalidates ledger
    so callers reset KV safely rather than guessing from text slicing.
    """
    delta = int(post_offset) - int(pre_offset)
    if delta == len(feed_ids) + len(yielded_ids):
        return [*pre_tokens, *feed_ids, *yielded_ids], "exact_lookahead"
    if yielded_ids and delta == len(feed_ids) + len(yielded_ids) - 1:
        return [*pre_tokens, *feed_ids, *yielded_ids[:-1]], "exact_non_lookahead"
    return (
        None,
        f"offset_mismatch:delta={delta},feed={len(feed_ids)},yielded={len(yielded_ids)}",
    )


def first_token_divergence(expected: list[int], actual: list[int]) -> int | None:
    """Return index of first differing token ID or None when sequences match identically."""
    limit = min(len(expected), len(actual))
    for i in range(limit):
        if expected[i] != actual[i]:
            return i
    if len(expected) != len(actual):
        return limit
    return None


def token_ids_sha256(ids: list[int]) -> str:
    payload = json.dumps([int(x) for x in ids], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CacheAudit:
    """Token/offset/logit equivalence record comparing cached path vs fresh canonical prompt."""

    case_id: str
    expected_ids_hash: str
    actual_ids_hash: str
    expected_offset: int
    actual_offset: int
    stop_reason: str
    first_divergence: int | None
    parity_pass: bool
    cached_text: str = ""
    fresh_text: str = ""
    max_logit_diff: float | None = None
    rms_logit_diff: float | None = None
    top1_match: bool | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
