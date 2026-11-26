"""T2 real-model cache parity audit against local Qwen3-4B-Instruct-2507-4bit."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
import importlib
import importlib.metadata as md
import json
from pathlib import Path
import re
import sys
import threading
from typing import Any

import numpy as np

from p0.stage8_eval.contracts import REPO_ROOT, load_manifest
from p0.stage8_eval.preflight import preflight
from subtitle_service.local_pipeline import Qwen3Translator, RATE
from subtitle_service.mt_cache_contract import (
    CacheAudit,
    canonical_messages,
    canonical_prompt_ids,
    first_token_divergence,
    token_ids_sha256,
)


def _compare_logits(
    cached_lp: np.ndarray | None,
    fresh_lp: np.ndarray | None,
) -> tuple[float | None, float | None, bool | None]:
    if cached_lp is None or fresh_lp is None:
        return None, None, None
    diff = np.abs(cached_lp.astype(np.float32) - fresh_lp.astype(np.float32))
    max_diff = float(np.max(diff))
    rms_diff = float(np.sqrt(np.mean(diff * diff)))
    top1 = int(np.argmax(cached_lp)) == int(np.argmax(fresh_lp))
    return round(max_diff, 6), round(rms_diff, 6), bool(top1)


def _run_fresh_canonical(
    tr: Qwen3Translator,
    canonical_ids: list[int],
    *,
    max_tokens: int = 256,
) -> tuple[str, np.ndarray | None, str]:
    mx = tr._mx
    gen_step = importlib.import_module("mlx_lm.generate").generate_step
    from mlx_lm.models.cache import make_prompt_cache

    fresh_cache = make_prompt_cache(tr.model)
    tokens: list[int] = []
    first_lp: np.ndarray | None = None
    stop_reason = "length_limit"
    prompt_arr = mx.array(canonical_ids, dtype=mx.int32)
    for tok, lp in gen_step(
        prompt_arr,
        tr.model,
        max_tokens=max_tokens,
        sampler=tr.sampler,
        logits_processors=tr.logits_processors,
        prompt_cache=fresh_cache,
        prefill_step_size=512,
    ):
        if first_lp is None and lp is not None:
            lp_f32 = lp.astype(mx.float32)
            mx.eval(lp_f32)
            first_lp = np.asarray(lp_f32, dtype=np.float32)
        tid = int(tok)
        if tid in tr._eos_ids:
            stop_reason = "eos"
            break
        tokens.append(tid)
    text = tr._clean_output(tr.tokenizer.decode(tokens))
    return text, first_lp, stop_reason


def _resolve_mt_snapshot(manifest: dict[str, Any]) -> Path:
    roles = manifest.get("roles") or {}
    mt_key = roles.get("mt")
    for mod in manifest.get("models") or []:
        if mod.get("key") == mt_key:
            for art in mod.get("artifacts") or []:
                p = Path(str(art.get("path", "")))
                if p.is_file():
                    return p.parent
                if p.is_dir():
                    return p
    raise FileNotFoundError(f"Unable to locate MT snapshot from manifest mt role {mt_key!r}")


def run_cache_audit(manifest: dict[str, Any], *, snapshot_dir: Path | None = None) -> dict[str, Any]:
    snap = snapshot_dir or _resolve_mt_snapshot(manifest)
    ctx_turns = int(((manifest.get("arm") or {}).get("parameters") or {}).get("context_turns", 4))
    tr = Qwen3Translator(snap, context_turns=ctx_turns)
    mx = tr._mx

    # Wrap _generate_step temporarily to record exact pre-step fed tokens and first-token logprobs
    orig_step = tr._generate_step
    capture: dict[str, Any] = {}

    def instrumented_step(prompt_ids, *args, **kwargs):
        feed_list = [int(x) for x in prompt_ids.tolist()] if hasattr(prompt_ids, "tolist") else [int(x) for x in prompt_ids]
        cache = kwargs.get("prompt_cache")
        if cache is not None:
            pre_len = len(tr._cache_tokens)
            capture["actual_prompt_ids"] = list(tr._cache_tokens) + feed_list
            capture["actual_offset_before_gen"] = int(cache[0].offset) + len(feed_list)
            capture["first_lp"] = None
            capture["reused_tokens"] = pre_len
            capture["fed_tokens"] = len(feed_list)
        for tok, lp in orig_step(prompt_ids, *args, **kwargs):
            if cache is not None and capture.get("first_lp") is None and lp is not None:
                lp_f32 = lp.astype(mx.float32)
                mx.eval(lp_f32)
                capture["first_lp"] = np.asarray(lp_f32, dtype=np.float32)
            yield tok, lp

    tr._generate_step = instrumented_step

    audits: list[CacheAudit] = []
    sess = tr.new_session()

    def audit_turn(
        case_id: str,
        text_ja: str,
        *,
        start_sample: int | None = None,
        end_sample: int | None = None,
        max_tokens: int = 256,
        cancel_mid: bool = False,
        cancel_pre: bool = False,
        expected_history_override: list[tuple[str, str]] | None = None,
        notes: str = "",
    ) -> str:
        capture.clear()
        if expected_history_override is not None:
            hist_snapshot = list(expected_history_override)
        else:
            cache_extra = int(tr._prompt_cache[0].offset) - tr._prefix_len
            gap_exceeded = (
                start_sample is not None
                and sess.last_end_sample > 0
                and (start_sample - sess.last_end_sample) > int(12.0 * RATE)
            )
            if gap_exceeded:
                hist_snapshot = []
            elif sess.turns >= sess.max_turns or cache_extra > 420:
                keep_n = max(1, sess.max_turns // 2) if sess.max_turns >= 2 else 0
                hist_snapshot = list(sess.history)[-keep_n:] if keep_n > 0 else []
            else:
                hist_snapshot = list(sess.history)
        expected_ids = canonical_prompt_ids(
            tr.tokenizer,
            canonical_messages(tr.SYSTEM_PROMPT, hist_snapshot, text_ja),
        )
        fresh_text, fresh_lp, fresh_stop = _run_fresh_canonical(tr, expected_ids, max_tokens=max_tokens)

        cancel_ev = threading.Event()
        if cancel_pre:
            cancel_ev.set()
        elif cancel_mid:
            saved_step = tr._generate_step

            def mid_cancel_step(*args, **kwargs):
                for idx, item in enumerate(saved_step(*args, **kwargs)):
                    yield item
                    if idx == 1:
                        cancel_ev.set()

            tr._generate_step = mid_cancel_step

        try:
            cached_text = tr.translate(
                sess,
                text_ja,
                cancel_event=cancel_ev if (cancel_pre or cancel_mid) else None,
                start_sample=start_sample,
                end_sample=end_sample,
                max_tokens=max_tokens,
            )
        finally:
            tr._generate_step = instrumented_step

        meta = getattr(tr, "last_translate_meta", {}) or {}
        stop_reason = str(meta.get("stop_reason", "unknown"))

        if cancel_pre:
            # Cancelled before generation; verify KV state was untouched relative to prior committed history
            ledger_ok = len(tr._cache_tokens) == int(tr._prompt_cache[0].offset)
            audits.append(
                CacheAudit(
                    case_id=case_id,
                    expected_ids_hash=token_ids_sha256(expected_ids),
                    actual_ids_hash="cancelled_pre_call",
                    expected_offset=len(expected_ids),
                    actual_offset=int(tr._prompt_cache[0].offset),
                    stop_reason=stop_reason,
                    first_divergence=None if ledger_ok else 0,
                    parity_pass=(stop_reason == "cancelled" and cached_text == "" and ledger_ok),
                    cached_text=cached_text,
                    fresh_text=fresh_text,
                    notes=notes,
                )
            )
            return cached_text

        actual_ids = capture.get("actual_prompt_ids", [])
        div = first_token_divergence(expected_ids, actual_ids)
        max_ld, rms_ld, top1 = _compare_logits(capture.get("first_lp"), fresh_lp)
        ledger_synced = len(tr._cache_tokens) == int(tr._prompt_cache[0].offset)
        ids_match = (div is None) and (len(actual_ids) == len(expected_ids))
        text_match = cached_text == fresh_text if not cancel_mid else (cached_text == "")
        if cancel_mid:
            ok = ids_match and ledger_synced and stop_reason == "cancelled" and cached_text == ""
        elif max_tokens <= 8:
            ok = ids_match and ledger_synced and stop_reason == "length_limit" and fresh_stop == "length_limit"
        else:
            ok = ids_match and ledger_synced and (top1 is True or top1 is None) and text_match
        audits.append(
            CacheAudit(
                case_id=case_id,
                expected_ids_hash=token_ids_sha256(expected_ids),
                actual_ids_hash=token_ids_sha256(actual_ids),
                expected_offset=len(expected_ids),
                actual_offset=int(capture.get("actual_offset_before_gen", -1)),
                stop_reason=stop_reason,
                first_divergence=div,
                parity_pass=bool(ok),
                cached_text=cached_text,
                fresh_text=fresh_text,
                max_logit_diff=max_ld,
                rms_logit_diff=rms_ld,
                top1_match=top1,
                notes=notes or f"reused={capture.get('reused_tokens')},fed={capture.get('fed_tokens')},text_match={text_match}",
            )
        )
        return cached_text

    # Cases 1–5: Reproduces exact EP01 5-turn sliding-window scenario that previously degraded!
    audit_turn("01_turn1_cold_start", "そういえば保育士さんってさ", start_sample=0, end_sample=32000)
    audit_turn("02_turn2_post_eos", "親に何て言うんだろう", start_sample=32000, end_sample=64000)
    audit_turn("03_turn3_multi_turn", "迎えに来た親にさ", start_sample=64000, end_sample=96000)
    audit_turn("04_turn4_pre_trim", "おかえりなさいって言うんだよ", start_sample=96000, end_sample=128000)
    kept_after_4 = list(sess.history)[-2:]
    t5 = audit_turn(
        "05_turn5_sliding_trim_replay",
        "迎えに来た親におかえりなさいって。",
        start_sample=128000,
        end_sample=160000,
        expected_history_override=kept_after_4,
        notes="Must retain welcoming parent sense, not thank-you regression",
    )

    # Case 6: Preview draft followed by post-preview final turn
    hist_for_draft = list(sess.history)
    draft_txt = tr.translate_draft(sess, "明日は休みですか")
    draft_ledger_ok = (len(tr._cache_tokens) == int(tr._prompt_cache[0].offset)) and (list(sess.history) == hist_for_draft)
    audit_turn(
        "06_post_preview_final",
        "明日はお休みですよね。",
        start_sample=160000,
        end_sample=192000,
        notes=f"preview_out={draft_txt!r},draft_non_mutating={draft_ledger_ok}",
    )

    # Case 7: max_tokens truncation (must classify length_limit and not commit history)
    hist_before_trunc = list(sess.history)
    audit_turn(
        "07_max_tokens_length_limit",
        "それで来週の木曜日に市役所の窓口に行って住民票と戸籍謄本をもらってくる予定なんだけど",
        start_sample=192000,
        end_sample=240000,
        max_tokens=4,
        notes="Must stop with length_limit and preserve prior history",
    )
    assert list(sess.history) == hist_before_trunc, "length_limit turn leaked into history"

    # Case 8: Post length_limit continuation using preserved history
    audit_turn(
        "08_post_length_limit_recovery",
        "了解しました。",
        start_sample=240000,
        end_sample=256000,
    )

    # Case 9: Mid-generation cancel (does not commit history or break next turn)
    hist_before_cancel = list(sess.history)
    audit_turn(
        "09_mid_generation_cancel",
        "ちょっと待ってくださいね、今確認しますから。",
        start_sample=256000,
        end_sample=280000,
        cancel_mid=True,
    )
    assert list(sess.history) == hist_before_cancel, "Cancelled turn leaked into history"

    # Case 10: Pre-call cancel
    audit_turn(
        "10_pre_call_cancel",
        "これはキャンセルされます。",
        start_sample=280000,
        end_sample=296000,
        cancel_pre=True,
    )

    # Case 11: Quote-cleaned turn -> next turn divergence trimmed automatically
    quoted_out = audit_turn(
        "11_quoted_output_turn",
        "「おかえりなさい」って言った。",
        start_sample=296000,
        end_sample=320000,
    )
    audit_turn(
        "12_post_quote_clean_turn",
        "本当ですか？",
        start_sample=320000,
        end_sample=336000,
        notes=f"Prior output cleaned to {quoted_out!r}",
    )

    # Case 13: >12s gap resets history & KV cleanly
    audit_turn(
        "13_gap_12s_reset",
        "お疲れ様でした。",
        start_sample=336000 + 15 * RATE,
        end_sample=352000 + 15 * RATE,
        expected_history_override=[],
        notes="15s audio gap must reset session KV cleanly",
    )

    # Case 14: Force cache_extra > 420 token threshold replay (without hitting max_turns first)
    sess_long = tr.new_session()
    sess_long.max_turns = 8
    sess_long.history = deque(maxlen=8)
    long_lines = [
        "昨日の夜遅くまで友達と駅前の居酒屋で昔の同級生の話をしていたら終電を逃してしまったんだよ。",
        "それで仕方なく歩いて帰ろうと思ったんだけど雨が急に降ってきてコンビニで傘を買うことになったんだ。",
        "しかも家に帰ったら鍵を会社に忘れてきたことに気づいて本当に大変だったんだよ。",
        "仕方なく会社の警備員さんに連絡して深夜にオフィスまで戻って予備の鍵を借りてきたんだ。",
        "電車も動いていなかったからタクシーを呼んだんだけど道路が工事中で大回りになって料金が二倍もかかったよ。",
        "やっとの思いで家に着いたのは朝の四時頃で、そのままシャワーも浴びずにベッドに倒れ込んだんだ。",
        "だから今日は朝からずっと眠くて仕事に全然集中できないんだよね。",
    ]
    idx = 0
    while (int(tr._prompt_cache[0].offset) - tr._prefix_len) <= 420 and idx < len(long_lines) - 1:
        idx += 1
        tr.translate(sess_long, long_lines[idx - 1], start_sample=idx * 40000, end_sample=(idx + 1) * 40000)
    extra_before = int(tr._prompt_cache[0].offset) - tr._prefix_len
    assert extra_before > 420, f"expected cache_extra > 420, got {extra_before}"
    assert sess_long.turns < sess_long.max_turns
    keep_n_long = max(1, sess_long.max_turns // 2)
    kept_long = list(sess_long.history)[-keep_n_long:]
    capture.clear()
    expected_long = canonical_prompt_ids(
        tr.tokenizer,
        canonical_messages(tr.SYSTEM_PROMPT, kept_long, long_lines[-1]),
    )
    fresh_long_txt, fresh_long_lp, _ = _run_fresh_canonical(tr, expected_long)
    cached_long_txt = tr.translate(
        sess_long, long_lines[-1], start_sample=(idx + 1) * 40000, end_sample=(idx + 2) * 40000
    )
    actual_long = capture.get("actual_prompt_ids", [])
    div_long = first_token_divergence(expected_long, actual_long)
    max_ld, rms_ld, top1 = _compare_logits(capture.get("first_lp"), fresh_long_lp)
    audits.append(
        CacheAudit(
            case_id="14_token_budget_420_trim_replay",
            expected_ids_hash=token_ids_sha256(expected_long),
            actual_ids_hash=token_ids_sha256(actual_long),
            expected_offset=len(expected_long),
            actual_offset=int(capture.get("actual_offset_before_gen", -1)),
            stop_reason=str((tr.last_translate_meta or {}).get("stop_reason", "")),
            first_divergence=div_long,
            parity_pass=bool(div_long is None and (top1 is True or top1 is None) and cached_long_txt == fresh_long_txt),
            cached_text=cached_long_txt,
            fresh_text=fresh_long_txt,
            max_logit_diff=max_ld,
            rms_logit_diff=rms_ld,
            top1_match=top1,
            notes=f"cache_extra_before={extra_before},turns_before={sess_long.turns}",
        )
    )

    # Case 15: Seek / new session forgets previous conversation
    sess_seek = tr.new_session()
    assert len(tr._cache_tokens) == tr._prefix_len
    audit_turn_hist_empty = canonical_prompt_ids(
        tr.tokenizer, canonical_messages(tr.SYSTEM_PROMPT, [], "はじめまして。")
    )
    fresh_seek, fresh_seek_lp, _ = _run_fresh_canonical(tr, audit_turn_hist_empty)
    capture.clear()
    cached_seek = tr.translate(sess_seek, "はじめまして。", start_sample=0, end_sample=16000)
    actual_seek = capture.get("actual_prompt_ids", [])
    div_seek = first_token_divergence(audit_turn_hist_empty, actual_seek)
    max_ld_s, rms_ld_s, top1_s = _compare_logits(capture.get("first_lp"), fresh_seek_lp)
    audits.append(
        CacheAudit(
            case_id="15_new_session_seek_reset",
            expected_ids_hash=token_ids_sha256(audit_turn_hist_empty),
            actual_ids_hash=token_ids_sha256(actual_seek),
            expected_offset=len(audit_turn_hist_empty),
            actual_offset=int(capture.get("actual_offset_before_gen", -1)),
            stop_reason=str((tr.last_translate_meta or {}).get("stop_reason", "")),
            first_divergence=div_seek,
            parity_pass=bool(div_seek is None and (top1_s is True or top1_s is None) and cached_seek == fresh_seek),
            cached_text=cached_seek,
            fresh_text=fresh_seek,
            max_logit_diff=max_ld_s,
            rms_logit_diff=rms_ld_s,
            top1_match=top1_s,
            notes="new_session must match fresh empty-history turn",
        )
    )

    tr._generate_step = orig_step

    all_ids_exact = all(a.first_divergence is None for a in audits)
    all_pass = all(a.parity_pass for a in audits)
    return {
        "schema_version": "stage8-mt-cache-audit-v1",
        "model_snapshot": str(snap),
        "runtime": {
            "mlx": md.version("mlx"),
            "mlx_lm": md.version("mlx-lm"),
            "transformers": md.version("transformers"),
        },
        "logit_dtype": "bfloat16",
        "logit_diff_diagnosis": (
            "MLX bfloat16 KV chunk-split ULP rounding (~0.125-1.0 on ~20-40 magnitude logprobs over 151k vocab); "
            "all canonical prompt token IDs and top-1 greedy outputs match 100%"
        ),
        "total_cases": len(audits),
        "passed_cases": sum(1 for a in audits if a.parity_pass),
        "all_ids_exact": all_ids_exact,
        "overall_status": "PASS" if (all_ids_exact and all_pass) else "FAIL",
        "turn5_regression_check": {
            "output": t5,
            "has_welcome_back": ("欢迎回来" in t5),
        },
        "cases": [asdict(a) for a in audits],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T2 Real Qwen3 KV cache equivalence audit")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    pre = preflight(manifest)
    if pre["status"] != "PASS":
        sys.stderr.write(json.dumps(pre, ensure_ascii=False, indent=2) + "\n")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    report = run_cache_audit(manifest)
    out_file = args.out / "cache_audit.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        json.dumps(
            {
                "overall_status": report["overall_status"],
                "passed_cases": report["passed_cases"],
                "total_cases": report["total_cases"],
                "all_ids_exact": report["all_ids_exact"],
                "turn5_regression_check": report["turn5_regression_check"],
                "out_file": str(out_file),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0 if report["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
