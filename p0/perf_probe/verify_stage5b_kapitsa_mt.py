"""Stage 5B-1 & 5B-2 probe: Kapitsa Qwen3.5-4B (KFlash vs Plain) vs MLX Qwen3-4B MT."""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "engine/src"))
sys.path.insert(0, str(REPO_ROOT / "p0/perf_probe"))
sys.path.insert(0, "/Users/luo/Documents/github/kapitsa/sdk/python")

import mlx.core as mx  # noqa: E402
from dataset import CACHE_ROOT, QWEN3_MT_DIR, RATE, VAD_PATH, load_frozen_slices, percentile  # noqa: E402
from kapitsa import Engine  # noqa: E402
from subtitle_service.app import KANA, chinese  # noqa: E402
from subtitle_service.config import AsrConfig  # noqa: E402
from subtitle_service.local_pipeline import Qwen3Translator, SpeechCppNeMoRecognizer  # noqa: E402

ARCHIVE_PATH = REPO_ROOT / "docs/experiments/stage5b-2026-11-24/stage5b_kapitsa_mt_probe.json"

TRAP_SENTENCES = [
    {"id": "trap_passive_voice", "ja": "私、この前コンビニでキレられたんだけど。"},
    {"id": "trap_tadaima", "ja": "ただいま。"},
    {"id": "trap_hoikushi", "ja": "私今保育士として働いてるんですけど。"},
    {"id": "trap_houdearimasu", "ja": "ほうであります。"},
]


def discover_4b_model_dir(kapitsa_home: Path) -> Path:
    candidates = []
    for root in (kapitsa_home / "models", Path.home() / ".cache/nace-infer-models", Path.home() / ".cache/kapitsa/models"):
        if not root.exists():
            continue
        for p in root.rglob("config.json"):
            if "dflash" in str(p).lower():
                continue
            if "qwen3.5-4b" in str(p).lower() or p.parent.name in ("4b", "qwen3.5-4b"):
                candidates.append(p.parent)
    if not candidates:
        raise FileNotFoundError(f"Could not find Qwen3.5-4B model dir under {kapitsa_home}")
    return candidates[0]


def prepare_ja_rows() -> list[dict]:
    _, _, all_slices, _, _, _ = load_frozen_slices()
    rec = SpeechCppNeMoRecognizer(
        CACHE_ROOT / "speech-cpp/libspeech.dylib",
        CACHE_ROOT / "speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf",
        VAD_PATH,
        AsrConfig(
            type="speech_cpp_nemo",
            model_file="reazonspeech-nemo-619M-v2-Q8_0.gguf",
            threads=6,
            decoding="beam",
            device="gpu",
            pad_ms=350,
        ),
    )
    rows = []
    for s in all_slices:
        ja = rec.decode(s["audio"])
        rows.append(
            {
                "slice_idx": s["slice_idx"],
                "clip": s["clip"],
                "start_sample": s["start_sample"],
                "end_sample": s["end_sample"],
                "text_ja": ja,
            }
        )
    rec.close()
    return rows


def bench_mlx_qwen3(ja_rows: list[dict]) -> dict:
    tr = Qwen3Translator(QWEN3_MT_DIR, context_turns=2)
    sess = tr.new_session()
    prev_clip = None

    first_ms_list, total_ms_list = [], []
    outputs = []
    kana_leaks = 0
    non_zh = 0

    for row in ja_rows:
        ja = row["text_ja"]
        if row["clip"] != prev_clip:
            sess = tr.new_session()
            prev_clip = row["clip"]
        if not ja.strip():
            outputs.append("")
            continue
        t_first = None
        t0 = time.perf_counter()

        def on_delta(d: str) -> None:
            nonlocal t_first
            if t_first is None and d:
                t_first = (time.perf_counter() - t0) * 1000.0

        mx.synchronize()
        zh = tr.translate(
            sess,
            ja,
            start_sample=row["start_sample"],
            end_sample=row["end_sample"],
            on_delta=on_delta,
        )
        mx.synchronize()
        t_tot = (time.perf_counter() - t0) * 1000.0
        if t_first is None:
            t_first = t_tot
        first_ms_list.append(t_first)
        total_ms_list.append(t_tot)
        outputs.append(zh)
        if KANA.search(zh):
            kana_leaks += 1
        if not chinese(zh):
            non_zh += 1

    trap_out = {}
    for t in TRAP_SENTENCES:
        s0 = tr.new_session()
        trap_out[t["id"]] = tr.translate(s0, t["ja"])

    del tr
    gc.collect()
    mx.clear_cache()

    return {
        "first_delta_p50_ms": round(statistics.median(first_ms_list), 2),
        "first_delta_p95_ms": round(percentile(first_ms_list, 0.95), 2),
        "total_p50_ms": round(statistics.median(total_ms_list), 2),
        "total_p95_ms": round(percentile(total_ms_list, 0.95), 2),
        "kana_leaks": kana_leaks,
        "non_chinese": non_zh,
        "outputs": outputs,
        "traps": trap_out,
    }


def bench_kapitsa(model_dir: Path, ja_rows: list[dict], *, spec_depth: int, label: str) -> dict:
    sys_msg = {"role": "system", "content": Qwen3Translator.SYSTEM_PROMPT}
    opts = {
        "model_dir": str(model_dir),
        "context": 4096,
        "slots": 1,
        "disk_cache": "0",
        "spec_depth": spec_depth,
    }
    if spec_depth != 0:
        opts["dflash"] = "auto"

    first_ms_list, total_ms_list = [], []
    prefill_ms_list, decode_ms_list, comp_toks_list, tok_s_list = [], [], [], []
    outputs = []
    kana_leaks = 0
    non_zh = 0
    thinking_leaks = 0

    with Engine.load(opts) as eng:
        # 3 warmup calls
        for w_ja in ("こんにちは。", "おはようございます。", "それはそうですね。"):
            eng.generate(
                {
                    "messages": [sys_msg, {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{w_ja}"}],
                    "max_tokens": 64,
                    "temperature": 0.0,
                    "template_kwargs": {"enable_thinking": False},
                }
            )

        history: list[dict] = []
        prev_clip = None
        last_end_sample = 0

        for row in ja_rows:
            ja = row["text_ja"]
            if row["clip"] != prev_clip:
                history = []
                prev_clip = row["clip"]
                last_end_sample = 0
            if not ja.strip():
                outputs.append("")
                continue
            if last_end_sample > 0 and (row["start_sample"] - last_end_sample) > int(5.0 * RATE):
                history = []
            if len(history) >= 4:  # max 2 turns (2 user + 2 assistant)
                history = []

            user_msg = {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{ja}"}
            msgs = [sys_msg, *history, user_msg]
            t_first = None
            t0 = time.perf_counter()

            def on_delta(delta: dict) -> None:
                nonlocal t_first
                if t_first is None and delta.get("content"):
                    t_first = (time.perf_counter() - t0) * 1000.0

            res = eng.generate(
                {
                    "messages": msgs,
                    "max_tokens": 96,
                    "temperature": 0.0,
                    "template_kwargs": {"enable_thinking": False},
                },
                on_delta=on_delta,
            )
            t_tot = (time.perf_counter() - t0) * 1000.0
            zh = (res.get("content") or "").strip()
            if res.get("reasoning"):
                thinking_leaks += 1
            if t_first is None:
                t_first = t_tot

            first_ms_list.append(t_first)
            total_ms_list.append(t_tot)
            prefill_ms_list.append(float(res.get("prefill_ms") or 0.0))
            decode_ms_list.append(float(res.get("decode_ms") or 0.0))
            ctoks = int(res.get("completion_tokens") or 0)
            comp_toks_list.append(ctoks)
            dms = float(res.get("decode_ms") or 0.0)
            if dms > 0 and ctoks > 0:
                tok_s_list.append(ctoks / (dms / 1000.0))

            outputs.append(zh)
            if KANA.search(zh):
                kana_leaks += 1
                history = []
            else:
                if not chinese(zh):
                    non_zh += 1
                history.extend([user_msg, {"role": "assistant", "content": zh}])
                last_end_sample = row["end_sample"]

        trap_out = {}
        for t in TRAP_SENTENCES:
            r = eng.generate(
                {
                    "messages": [sys_msg, {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{t['ja']}"}],
                    "max_tokens": 96,
                    "temperature": 0.0,
                    "template_kwargs": {"enable_thinking": False},
                }
            )
            trap_out[t["id"]] = (r.get("content") or "").strip()

    return {
        "label": label,
        "first_delta_p50_ms": round(statistics.median(first_ms_list), 2),
        "first_delta_p95_ms": round(percentile(first_ms_list, 0.95), 2),
        "total_p50_ms": round(statistics.median(total_ms_list), 2),
        "total_p95_ms": round(percentile(total_ms_list, 0.95), 2),
        "prefill_p50_ms": round(statistics.median(prefill_ms_list), 2),
        "decode_p50_ms": round(statistics.median(decode_ms_list), 2),
        "completion_tokens_p50": round(statistics.median(comp_toks_list), 1),
        "decode_tok_s_p50": round(statistics.median(tok_s_list), 1) if tok_s_list else 0.0,
        "kana_leaks": kana_leaks,
        "non_chinese": non_zh,
        "thinking_leaks": thinking_leaks,
        "outputs": outputs,
        "traps": trap_out,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kapitsa-home", default="/tmp/stage5b-kapitsa-home")
    parser.add_argument("--model-dir", default="")
    args = parser.parse_args()

    model_dir = Path(args.model_dir) if args.model_dir else discover_4b_model_dir(Path(args.kapitsa_home))
    print(f"Using Kapitsa 4B model_dir: {model_dir}")

    print("=== [1/4] Decoding 91 frozen slices via NeMo Q8_0 ===")
    ja_rows = prepare_ja_rows()

    print("=== [2/4] Baseline: MLX Qwen3-4B-Instruct-2507-4bit (context_turns=2) ===")
    res_mlx = bench_mlx_qwen3(ja_rows)

    print("=== [3/4] Candidate A: Kapitsa Qwen3.5-4B KFlash ===")
    res_kflash = bench_kapitsa(model_dir, ja_rows, spec_depth=-1, label="kapitsa_kflash")

    print("=== [4/4] Candidate B: Kapitsa Qwen3.5-4B Plain (spec_depth=0) ===")
    res_plain = bench_kapitsa(model_dir, ja_rows, spec_depth=0, label="kapitsa_plain")

    # Side-by-side comparison on D1 + 15 D0 samples + traps
    d1_compare = []
    d0_samples = []
    for i, row in enumerate(ja_rows):
        entry = {
            "slice_idx": row["slice_idx"],
            "clip": row["clip"],
            "ja": row["text_ja"],
            "mlx_qwen3_4b": res_mlx["outputs"][i],
            "kapitsa_kflash": res_kflash["outputs"][i],
            "kapitsa_plain": res_plain["outputs"][i],
        }
        if row["clip"] == "d1-ep01-45s":
            d1_compare.append(entry)
        elif len(d0_samples) < 15 and row["text_ja"].strip():
            d0_samples.append(entry)

    trap_compare = [
        {
            "id": t["id"],
            "ja": t["ja"],
            "mlx_qwen3_4b": res_mlx["traps"][t["id"]],
            "kapitsa_kflash": res_kflash["traps"][t["id"]],
            "kapitsa_plain": res_plain["traps"][t["id"]],
        }
        for t in TRAP_SENTENCES
    ]

    summary = {
        "model_dir": str(model_dir),
        "metrics": {
            "mlx_qwen3_4b": {k: v for k, v in res_mlx.items() if k not in ("outputs", "traps")},
            "kapitsa_qwen35_4b_kflash": {k: v for k, v in res_kflash.items() if k not in ("outputs", "traps")},
            "kapitsa_qwen35_4b_plain": {k: v for k, v in res_plain.items() if k not in ("outputs", "traps")},
        },
        "trap_compare": trap_compare,
        "d1_compare": d1_compare,
        "d0_samples": d0_samples,
    }

    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"metrics": summary["metrics"], "trap_compare": trap_compare}, ensure_ascii=False, indent=2))
    print(f"\nSaved {ARCHIVE_PATH}")


if __name__ == "__main__":
    main()
