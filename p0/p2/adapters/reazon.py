"""Decode-only zeros; caller retains the original source span and VAD boundary."""


def pad_for_decode(samples, sample_rate, head_ms, tail_ms):
    import numpy as np

    if samples.ndim != 1 or sample_rate <= 0 or min(head_ms, tail_ms) < 0:
        raise ValueError("invalid decode padding")
    return np.pad(samples, (round(head_ms * sample_rate / 1000), round(tail_ms * sample_rate / 1000)))
