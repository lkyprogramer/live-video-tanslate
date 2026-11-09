"""Official PLaMo MLX H0: one source, explicit IDs, no shared prompt cache."""


def prompt_h0(tokenizer, text):
    if not text.strip() or "<|plamo:" in text:
        raise ValueError("empty or reserved-token source")
    return tokenizer.apply_chat_template([dict(role="user", content=text)], source_lang="ja", target_lang="zh-hans",
                                         add_generation_prompt=True, tokenize=False)


def encode_prompt(tokenizer, prompt):
    ids = tokenizer.encode(prompt, add_special_tokens=False)
    if not ids or ids[0] != 1 or ids.count(1) != 1:
        raise ValueError("PLaMo prompt requires exactly one BOS=1")
    if len(ids) + 256 > 32768:
        raise ValueError("trained context limit exceeded")
    return ids


class Plamo:
    def __init__(self, snapshot):
        from mlx_lm import load

        self.model, self.tokenizer = load(snapshot, trust_remote_code=True,
                                          tokenizer_config={"trust_remote_code": True})
        if set(self.tokenizer.eos_token_ids) != {1, 16}:
            raise ValueError("unexpected PLaMo EOS IDs")

    def translate(self, text):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        prompt = prompt_h0(self.tokenizer, text)
        ids = encode_prompt(self.tokenizer, prompt)
        pieces, final = [], None
        # No prompt_cache argument: stream_generate constructs fresh cache per call.
        for response in stream_generate(self.model, self.tokenizer, ids, max_tokens=256,
                                        sampler=make_sampler(temp=0)):
            pieces.append(response.text)
            final = response
        if final is None or final.finish_reason not in ("stop", "length"):
            raise ValueError("missing generation termination")
        if final.finish_reason == "stop" and final.token not in {1, 16}:
            raise ValueError("invalid stop token")
        return "".join(pieces), dict(finish_reason=final.finish_reason, prompt_tokens=len(ids),
                                  generation_tokens=final.generation_tokens, terminal_token=final.token)
