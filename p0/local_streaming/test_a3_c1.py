"""Unit tests for A3 C1 context selection and bounds."""
from .a3_c1 import MAX_CONTEXT_TOKENS, select_context


def test_select_context_caps_at_2_utterances_and_256_tokens():
    encode = lambda s: list(s)  # 1 char = 1 token for deterministic unit test
    hist = ["first", "second", "third"]
    ctx, n = select_context(hist, encode)
    assert ctx == "second\nthird"
    assert n == len("second\nthird")

    long_old = "a" * 250
    short_new = "b" * 20
    ctx2, n2 = select_context([long_old, short_new], encode)
    assert ctx2 == short_new
    assert n2 <= MAX_CONTEXT_TOKENS

    too_long = "c" * 300
    ctx3, n3 = select_context([too_long], encode)
    assert ctx3 == ""
    assert n3 == 0
