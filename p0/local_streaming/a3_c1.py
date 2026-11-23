"""A3 C1 single-factor MT context control: empty-context template vs <=2 past JA utterances (<=256 tokens)."""
import argparse
import json
from pathlib import Path
import time

from p0.p2.freeze import dump, rows
from subtitle_service.app import chinese
from subtitle_service.local_pipeline import HyTranslator

MAX_CONTEXT_UTTERANCES = 2
MAX_CONTEXT_TOKENS = 256


def select_context(history, encode_fn, *, max_utterances=MAX_CONTEXT_UTTERANCES, max_tokens=MAX_CONTEXT_TOKENS):
    """Select up to max_utterances most recent past utterances fitting within max_tokens."""
    recent = [u for u in history[-max_utterances:] if u]
    while recent:
        joined = "\n".join(recent)
        toks = encode_fn(joined)
        if len(toks) <= max_tokens:
            return joined, len(toks)
        recent = recent[1:]
    return "", 0


def build_prompt_text(context_text, text_ja):
    ctx = context_text if context_text else "无"
    return (
        f"前文参考（仅作语境，不要翻译或重复）：\n{ctx}\n\n"
        f"请将以下日语翻译为简体中文，只输出译文：\n{text_ja}"
    )


def generate_with_prompt(session, prompt_content, seed):
    import mlx.core as mx
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_logits_processors, make_sampler
    from whisperlivekit.translation_mlx_llm_mt import _placeholder_stop_check, _strip_hy_placeholder

    model, tokenizer = session._ensure_model(session._config)
    if session._eos_token is None:
        session._eos_token = getattr(tokenizer, "eos_token", "") or ""
    eos = session._eos_token
    messages = [{"role": "user", "content": prompt_content}]
    prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
    sampler = make_sampler(
        temp=session._config.temp,
        top_p=session._config.top_p,
        top_k=session._config.top_k,
    )
    processors = make_logits_processors(repetition_penalty=session._config.repetition_penalty)
    stop_at_placeholder = _placeholder_stop_check(tokenizer)
    mx.random.seed(seed)
    t0 = time.monotonic_ns()
    out = ""
    for chunk in stream_generate(
        model, tokenizer, prompt=prompt, max_tokens=session._config.max_tokens,
        sampler=sampler, logits_processors=processors,
    ):
        out += chunk.text if hasattr(chunk, "text") else str(chunk)
        if eos and out.endswith(eos):
            break
        if stop_at_placeholder is not None and stop_at_placeholder(chunk):
            break
    elapsed_ms = (time.monotonic_ns() - t0) / 1e6
    if eos:
        out = out.replace(eos, "")
    raw = _strip_hy_placeholder(out)
    return raw, chinese(raw), len(prompt), elapsed_ms


def run_c1(a0: Path, out: Path):
    import mlx.core as mx

    out.mkdir(parents=True, exist_ok=True)
    locks = {r["id"]: Path(r["local_path"]) for r in json.loads((a0 / "locks/models.json").read_text())["models"]}
    b0_asr = rows(a0 / "baseline/reazon.jsonl")
    units = rows(a0 / "inputs/units.jsonl")
    unit_map = json.loads((a0 / "inputs/unit-map.json").read_text())["required_segments"]
    verdict = json.loads((a0 / "inputs/verdict.json").read_text())["outcomes"]

    translator = HyTranslator(locks["hymt-b0"])
    session = translator.new_session()
    _, tokenizer = session._ensure_model(session._config)

    def encode_fn(text):
        try:
            return tokenizer.encode(text, add_special_tokens=False)
        except TypeError:
            return tokenizer.encode(text)

    # Sanity check against B0 seed 11 segment 1
    b0_mt11 = {(r["clip"], r["segment_id"]): r["text_zh"] for r in rows(a0 / "baseline/mt-seed11.jsonl")}
    first_seg = b0_asr[0]
    mx.random.seed(11 + first_seg["mt_seed_index"])
    sanity_got = translator.translate(session, first_seg["text_ja"])
    assert sanity_got == b0_mt11[(first_seg["clip"], first_seg["segment_id"])], (
        f"B0 sanity mismatch: {sanity_got!r} != {b0_mt11[(first_seg['clip'], first_seg['segment_id'])]!r}"
    )

    results = {}
    for seed in (11, 29):
        b0_mt = {(r["clip"], r["segment_id"]): r["text_zh"] for r in rows(a0 / f"baseline/mt-seed{seed}.jsonl")}
        for mode in ("empty", "ctx2"):
            out_rows = []
            history = []
            prev_clip = None
            for seg in b0_asr:
                clip = seg["clip"]
                if clip != prev_clip:
                    history = []
                    prev_clip = clip
                if mode == "ctx2":
                    ctx_text, ctx_toks = select_context(history, encode_fn)
                else:
                    ctx_text, ctx_toks = "", 0
                assert ctx_toks <= MAX_CONTEXT_TOKENS
                prompt_text = build_prompt_text(ctx_text, seg["text_ja"])
                raw, filtered, prompt_toks, mt_ms = generate_with_prompt(
                    session, prompt_text, seed + seg["mt_seed_index"]
                )
                out_rows.append(dict(
                    clip=clip,
                    segment_id=seg["segment_id"],
                    mt_seed_index=seg["mt_seed_index"],
                    text_ja=seg["text_ja"],
                    context_tokens=ctx_toks,
                    context_utterances=len(ctx_text.splitlines()) if ctx_text else 0,
                    prompt_tokens=prompt_toks,
                    mt_ms=round(mt_ms, 2),
                    raw_zh=raw,
                    text_zh=filtered,
                ))
                if seg["text_ja"].strip():
                    history.append(seg["text_ja"].strip())

            jsonl_path = out / f"c1-{mode}-seed{seed}.jsonl"
            with jsonl_path.open("w", encoding="utf-8") as f:
                for row in out_rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            results[(seed, mode)] = out_rows

    # Build unit-level comparison
    comparison = {"rounds": {}}
    for seed in (11, 29):
        b0_mt = {(r["clip"], r["segment_id"]): chinese(r["text_zh"]) or "" for r in rows(a0 / f"baseline/mt-seed{seed}.jsonl")}
        empty_by_key = {(r["clip"], r["segment_id"]): r for r in results[(seed, "empty")]}
        ctx2_by_key = {(r["clip"], r["segment_id"]): r for r in results[(seed, "ctx2")]}
        usable = set(verdict[f"m0-{seed}"]["usable"])

        unit_rows = []
        diff_units = []
        for u in units:
            clip, grp = u["clip"], u["group"]
            uid = f"{clip}/{grp}"
            sids = unit_map[uid]
            b0_zh = " ".join(b0_mt.get((clip, s), "") for s in sids).strip()
            e_zh = " ".join((empty_by_key[(clip, s)]["text_zh"] or "") for s in sids).strip()
            c_zh = " ".join((ctx2_by_key[(clip, s)]["text_zh"] or "") for s in sids).strip()
            entry = dict(
                unit_id=uid,
                b0_usable=uid in usable,
                reference_zh=u["reference_zh"],
                b0_zh=b0_zh,
                c1_empty_zh=e_zh,
                c1_ctx2_zh=c_zh,
                ctx2_differs_from_empty=(c_zh != e_zh),
            )
            unit_rows.append(entry)
            if c_zh != e_zh:
                diff_units.append(entry)

        e_ms = [r["mt_ms"] for r in results[(seed, "empty")]]
        c_ms = [r["mt_ms"] for r in results[(seed, "ctx2")]]
        comparison["rounds"][str(seed)] = dict(
            empty_mt_ms_mean=round(sum(e_ms) / len(e_ms), 2),
            ctx2_mt_ms_mean=round(sum(c_ms) / len(c_ms), 2),
            ctx2_max_context_tokens=max(r["context_tokens"] for r in results[(seed, "ctx2")]),
            ctx2_differs_from_empty_count=len(diff_units),
            diff_units=diff_units,
            all_units=unit_rows,
        )

    dump(out / "c1-comparison.json", comparison)
    for s in ("11", "29"):
        r = comparison["rounds"][s]
        print(json.dumps({
            "seed": s,
            "empty_ms_mean": r["empty_mt_ms_mean"],
            "ctx2_ms_mean": r["ctx2_mt_ms_mean"],
            "max_ctx_tokens": r["ctx2_max_context_tokens"],
            "diff_units_count": r["ctx2_differs_from_empty_count"],
        }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("a0", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run_c1(args.a0, args.output)
