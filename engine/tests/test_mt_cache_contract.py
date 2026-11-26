"""Contract tests for T2: Qwen3Translator exact token ledger, double-EOS fix, cleaning, cancel, and trim."""

from __future__ import annotations

import threading
import pytest

from subtitle_service import local_pipeline as lp


EOS_ID = 151645
END_THINK_ID = 151668


class ChatTemplateFakeTokenizer:
    """Deterministic char/special-token tokenizer mimicking Qwen3 chat template tokens."""

    eos_token_ids = {EOS_ID}

    def __init__(self) -> None:
        self._tok_to_id: dict[str, int] = {
            "<|im_start|>": 151644,
            "<|im_end|>": EOS_ID,
            "</think>": END_THINK_ID,
            "\n": 198,
        }
        self._id_to_tok: dict[int, str] = {v: k for k, v in self._tok_to_id.items()}
        self._next_id = 1000

    def _intern(self, piece: str) -> int:
        if piece not in self._tok_to_id:
            tid = self._next_id
            self._next_id += 1
            self._tok_to_id[piece] = tid
            self._id_to_tok[tid] = piece
        return self._tok_to_id[piece]

    def apply_chat_template(self, messages, *, add_generation_prompt: bool = True, tokenize: bool = False) -> str:
        parts = []
        for m in messages:
            parts.append(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n")
        if add_generation_prompt:
            parts.append("<|im_start|>assistant\n")
        rendered = "".join(parts)
        return self.encode(rendered, add_special_tokens=False) if tokenize else rendered

    def encode(self, text: str, *, add_special_tokens: bool = False) -> list[int]:
        ids: list[int] = []
        i = 0
        specials = ("<|im_start|>", "<|im_end|>", "</think>")
        while i < len(text):
            matched = next((sp for sp in specials if text.startswith(sp, i)), None)
            if matched:
                ids.append(self._intern(matched))
                i += len(matched)
            else:
                ids.append(self._intern(text[i]))
                i += 1
        return ids

    def decode(self, ids, *, skip_special_tokens: bool = False) -> str:
        out = []
        for tid in ids:
            piece = self._id_to_tok.get(int(tid), "")
            if skip_special_tokens and piece in ("<|im_start|>", "<|im_end|>"):
                continue
            out.append(piece)
        return "".join(out)


class BackingCacheSlot:
    def __init__(self, backing: list[int]) -> None:
        self.backing = backing

    @property
    def offset(self) -> int:
        return len(self.backing)


def _build_fake_translator(
    *,
    context_turns: int = 4,
    outputs_by_input: dict[str, str] | None = None,
    lookahead: bool = True,
):
    import mlx.core as mx

    tok = ChatTemplateFakeTokenizer()
    backing: list[int] = []
    cache = [BackingCacheSlot(backing)]
    outputs = outputs_by_input or {}

    def _trim(c, n: int) -> None:
        if n > 0:
            del c[0].backing[-n:]

    sentinel = "<<<SPLIT_HERE>>>"
    full_tpl = tok.apply_chat_template(
        [
            {"role": "system", "content": lp.Qwen3Translator.SYSTEM_PROMPT},
            {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{sentinel}"},
        ],
        add_generation_prompt=True,
        tokenize=False,
    )
    prefix_str, suffix_tail = full_tpl.split(sentinel, 1)
    prefix_ids = tok.encode(prefix_str, add_special_tokens=False)
    backing.extend(prefix_ids)

    calls: list[dict] = []

    def fake_generate_step(
        prompt_ids,
        model,
        *,
        max_tokens: int = 256,
        prompt_cache=None,
        prompt_progress_callback=None,
        **kwargs,
    ):
        arr = [int(x) for x in prompt_ids.tolist()] if hasattr(prompt_ids, "tolist") else [int(x) for x in prompt_ids]
        pre_snapshot = list(backing)
        calls.append({"feed_ids": list(arr), "pre_offset": len(pre_snapshot)})
        if prompt_cache is None:
            local_ctx = list(arr)
            full_text = tok.decode(local_ctx)
        else:
            backing.extend(arr)
            full_text = tok.decode(backing)

        if prompt_progress_callback is not None:
            prompt_progress_callback(len(arr), len(arr))

        # Determine mocked model response without Japanese kana (so valid Chinese doesn't trigger kana repair)
        marker = "请翻译以下日语台词为简体中文：\n"
        latest_user = full_text.rsplit(marker, 1)[-1].split("<|im_end|>", 1)[0]
        reply_text = outputs.get(latest_user, f"中译{sum(ord(c) for c in latest_user) % 1000}")
        gen_ids = tok.encode(reply_text, add_special_tokens=False) + [EOS_ID]

        if max_tokens == 0:
            return

        limit = len(gen_ids) if max_tokens < 0 else min(len(gen_ids), max_tokens)
        for idx in range(limit):
            tid = gen_ids[idx]
            if prompt_cache is not None and lookahead:
                # mlx-lm 0.31.3 look-ahead writes sampled token (including EOS!) before yield
                backing.append(tid)
            yield mx.array(tid), None
            if prompt_cache is not None and not lookahead:
                backing.append(tid)

    tr = lp.Qwen3Translator.__new__(lp.Qwen3Translator)
    tr._mx = mx
    tr.tokenizer = tok
    tr.model = object()
    tr.sampler = object()
    tr.logits_processors = []
    tr._eos_ids = {EOS_ID}
    tr._context_turns = context_turns
    tr._prefix_ids = list(prefix_ids)
    tr._prefix_len = len(prefix_ids)
    tr._suffix_tail = suffix_tail
    tr._next_turn_prefix = "<|im_end|>\n<|im_start|>user\n请翻译以下日语台词为简体中文：\n"
    tr._prompt_cache = cache
    tr._cache_tokens = list(prefix_ids)
    tr._trim_prompt_cache = _trim
    tr._generate_step = fake_generate_step
    tr._generate = None
    tr.last_translate_meta = {}
    return tr, tok, backing, calls


def test_eos_cached_exactly_once():
    from subtitle_service.mt_cache_contract import canonical_messages, canonical_prompt_ids

    tr, tok, backing, calls = _build_fake_translator(
        context_turns=4,
        outputs_by_input={"おはよう": "早安。", "ありがとう": "谢谢。"},
    )
    sess = tr.new_session()

    out1 = tr.translate(sess, "おはよう", start_sample=0, end_sample=16000)
    assert out1 == "早安。"
    eos_id = EOS_ID
    # Look-ahead leaves exactly ONE eos_id at the tail of backing cache
    assert backing[-1] == eos_id and backing[-2] != eos_id

    out2 = tr.translate(sess, "ありがとう", start_sample=16000, end_sample=32000)
    assert out2 == "谢谢。"
    # Second turn feed MUST NOT start with duplicate EOS!
    assert calls[1]["feed_ids"][0] != eos_id
    # Entire KV cache content after turn 2 must equal canonical(turn1 + reply1 + turn2) + reply2 + [EOS]
    expected_turn2_prompt = canonical_prompt_ids(
        tok, canonical_messages(tr.SYSTEM_PROMPT, [("おはよう", out1)], "ありがとう")
    )
    expected_after_turn2 = expected_turn2_prompt + tok.encode(out2, add_special_tokens=False) + [eos_id]
    assert backing == expected_after_turn2
    assert tr._cache_tokens == expected_after_turn2


def test_cleaned_history_cannot_reuse_raw_prefix():
    from subtitle_service.mt_cache_contract import canonical_messages, canonical_prompt_ids

    # Turn 1 raw output is wrapped in quotes -> _clean_output strips quotes, so raw KV tail differs from cleaned history!
    tr, tok, backing, _ = _build_fake_translator(
        context_turns=4,
        outputs_by_input={"おかえりなさい": "“欢迎回家”", "次": "下一句"},
    )
    sess = tr.new_session()
    out1 = tr.translate(sess, "おかえりなさい")
    assert out1 == "欢迎回家"

    out2 = tr.translate(sess, "次")
    assert out2 == "下一句"
    expected_prompt2 = canonical_prompt_ids(
        tok, canonical_messages(tr.SYSTEM_PROMPT, [("おかえりなさい", "欢迎回家")], "次")
    )
    expected_after_2 = expected_prompt2 + tok.encode("下一句", add_special_tokens=False) + [EOS_ID]
    assert backing == expected_after_2


def test_max_tokens_not_normal_eos():
    tr, _, backing, _ = _build_fake_translator(
        context_turns=4,
        outputs_by_input={"長い台詞": "这是一个非常长并且会被截断的输出内容", "普通": "正常"},
    )
    sess = tr.new_session()
    assert tr.translate(sess, "OK1") == f"中译{sum(ord(c) for c in 'OK1') % 1000}"
    offset_before = len(backing)
    history_before = list(sess.history)

    # max_tokens=4 hits length_limit without EOS -> must NOT commit broken turn to history or corrupt cache!
    truncated = tr.translate(sess, "長い台詞", max_tokens=4)
    assert tr.last_translate_meta["stop_reason"] == "length_limit"
    assert tr.last_translate_meta["valid"] is False
    assert list(sess.history) == history_before
    assert len(backing) == offset_before

    # Subsequent valid turn seamlessly continues from prior good turn
    assert tr.translate(sess, "普通") == "正常"
    assert [src for src, _ in sess.history] == ["OK1", "普通"]

    # Even when session is already at max_turns, a truncated turn must NOT discard earlier turns
    assert tr.translate(sess, "OK2") == f"中译{sum(ord(c) for c in 'OK2') % 1000}"
    assert tr.translate(sess, "OK3") == f"中译{sum(ord(c) for c in 'OK3') % 1000}"
    assert [src for src, _ in sess.history] == ["OK1", "普通", "OK2", "OK3"]
    tr.translate(sess, "長い台詞", max_tokens=4)
    assert tr.last_translate_meta["stop_reason"] == "length_limit"
    assert [src for src, _ in sess.history] == ["OK1", "普通", "OK2", "OK3"]


def test_cancel_does_not_commit_history():
    tr, _, backing, _ = _build_fake_translator(
        context_turns=4,
        outputs_by_input={"一番目": "第一句", "キャンセル": "取消了"},
    )
    sess = tr.new_session()
    assert tr.translate(sess, "一番目") == "第一句"
    offset_before = len(backing)

    cancel = threading.Event()
    orig_step = tr._generate_step

    def cancelling_step(*args, **kwargs):
        gen = orig_step(*args, **kwargs)
        tok0, lp0 = next(gen)
        yield tok0, lp0
        cancel.set()
        yield next(gen)

    tr._generate_step = cancelling_step
    assert tr.translate(sess, "キャンセル", cancel_event=cancel) == ""
    assert tr.last_translate_meta["stop_reason"] == "cancelled"
    assert [src for src, _ in sess.history] == ["一番目"]
    assert len(backing) == offset_before


def test_preview_rollback_preserves_final_cache():
    from subtitle_service.mt_cache_contract import canonical_messages, canonical_prompt_ids

    tr, tok, backing, _ = _build_fake_translator(
        context_turns=4,
        outputs_by_input={"一句目": "第一句", "途中の草稿": "中间草稿", "二句目": "第二句"},
    )
    sess = tr.new_session()
    out1 = tr.translate(sess, "一句目")
    snapshot_before_preview = list(backing)
    turns_before = sess.turns

    draft = tr.translate_draft(sess, "途中の草稿")
    assert draft == "中间草稿"
    assert backing == snapshot_before_preview
    assert tr._cache_tokens == snapshot_before_preview
    assert sess.turns == turns_before
    assert [src for src, _ in sess.history] == ["一句目"]

    out2 = tr.translate(sess, "二句目")
    expected_p2 = canonical_prompt_ids(tok, canonical_messages(tr.SYSTEM_PROMPT, [("一句目", out1)], "二句目"))
    assert backing == expected_p2 + tok.encode(out2, add_special_tokens=False) + [EOS_ID]


def test_trim_and_replay_equals_fresh():
    from subtitle_service.mt_cache_contract import canonical_messages, canonical_prompt_ids

    tr, tok, backing, _ = _build_fake_translator(context_turns=4)
    sess = tr.new_session()
    # Turn 1..4 fills window, Turn 5 triggers max_turns=4 trim -> keeps recent 2 turns (3, 4)
    outs = [tr.translate(sess, f"台詞{i}", start_sample=i * 16000, end_sample=(i + 1) * 16000) for i in range(1, 6)]
    expected_hist = [(f"台詞{i}", outs[i - 1]) for i in (3, 4)]
    expected_p5 = canonical_prompt_ids(tok, canonical_messages(tr.SYSTEM_PROMPT, expected_hist, "台詞5"))
    expected_after_5 = expected_p5 + tok.encode(outs[4], add_special_tokens=False) + [EOS_ID]
    assert backing == expected_after_5
    assert [src for src, _ in sess.history] == ["台詞3", "台詞4", "台詞5"]


def test_new_epoch_forgets_old_history():
    from subtitle_service.mt_cache_contract import canonical_messages, canonical_prompt_ids

    tr, tok, backing, _ = _build_fake_translator(context_turns=4)
    sess1 = tr.new_session()
    _ = tr.translate(sess1, "旧シーン")
    assert len(backing) > tr._prefix_len

    # Seek / new session = new epoch -> must reset cache and forget old history
    sess2 = tr.new_session()
    assert len(backing) == tr._prefix_len
    assert list(sess2.history) == []
    out = tr.translate(sess2, "新シーン")
    expected = canonical_prompt_ids(tok, canonical_messages(tr.SYSTEM_PROMPT, [], "新シーン")) + tok.encode(out, add_special_tokens=False) + [EOS_ID]
    assert backing == expected


def test_gap_12s_clears_history_and_cache():
    tr, tok, backing, _ = _build_fake_translator(context_turns=4)
    sess = tr.new_session()
    _ = tr.translate(sess, "前", start_sample=0, end_sample=16000)
    out = tr.translate(sess, "十二秒後", start_sample=15 * lp.RATE, end_sample=16 * lp.RATE)
    assert [src for src, _ in sess.history] == ["十二秒後"]
    assert sess.turns == 1


def test_non_lookahead_generator_ledger_reconciliation():
    from subtitle_service.mt_cache_contract import canonical_messages, canonical_prompt_ids

    tr, tok, backing, _ = _build_fake_translator(context_turns=4, lookahead=False)
    sess = tr.new_session()
    out1 = tr.translate(sess, "A")
    # Non-lookahead does NOT cache EOS, so pre_tokens ends cleanly before EOS and turn 2 feeds EOS
    out2 = tr.translate(sess, "B")
    expected_p2 = canonical_prompt_ids(tok, canonical_messages(tr.SYSTEM_PROMPT, [("A", out1)], "B"))
    assert backing == expected_p2 + tok.encode(out2, add_special_tokens=False)
