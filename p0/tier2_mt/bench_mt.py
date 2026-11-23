"""Run Tier 2B 4B-8B MLX 4-bit MT + joint ASR homophone-repair blind benchmark."""

import argparse
import gc
import json
import os
import re
import resource
import sys
import time
import unicodedata
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[2] / ".model-cache"))

import mlx.core as mx
from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler

REPO_ROOT = Path(__file__).resolve().parents[2]
HAN = re.compile(r"[\u4e00-\u9fff]")
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def chinese(text: str) -> str | None:
    value = unicodedata.normalize("NFKC", text).strip()
    if not HAN.search(value) or KANA.search(value) or "hy_place" in value or "<|" in value:
        return None
    return text.strip()

ASR_DIR = REPO_ROOT / "p0/evidence/tier2-asr-20261123"
OUT_DIR = REPO_ROOT / "p0/evidence/tier2b-mt-20261123"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * p))))
    return round(xs[idx], 4)


def rss_mib() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 2)


def clean_output(text: str) -> str:
    text = text.strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[-1].strip()
    # Strip outer quotes if model wrapped single line
    if len(text) >= 2 and ((text[0] == '"' and text[-1] == '"') or (text[0] == "“" and text[-1] == "”")):
        text = text[1:-1].strip()
    return text


def load_tracks() -> dict[str, list[dict]]:
    core_clips = {"csj-reproduction", "csj-task", "wmf-04"}

    units = load_jsonl(ASR_DIR / "d0_units.jsonl")
    t1_oracle = [
        {"clip": u["clip"], "id": u["group"], "text_ja": u["source_ja"], "reference_zh": u["reference_zh"]}
        for u in units
    ]

    def load_asr_track(prefix: str) -> list[dict]:
        d0 = [r for r in load_jsonl(ASR_DIR / f"{prefix}_d0.jsonl") if r["clip"] in core_clips]
        d1 = load_jsonl(ASR_DIR / f"{prefix}_d1.jsonl")
        rows = []
        for r in d0 + d1:
            rows.append(
                {
                    "clip": r["clip"],
                    "segment_id": r["segment_id"],
                    "start_sample": r["start_sample"],
                    "end_sample": r["end_sample"],
                    "text_ja": r["text_ja"].strip(),
                }
            )
        return rows

    d1_cues = json.loads((ASR_DIR / "d1_srt_cues.json").read_text(encoding="utf-8"))
    t4_d1_srt = [{"clip": "d1-srt", "id": str(c["cue_id"]), "text_ja": c["text_ja"]} for c in d1_cues]

    return {
        "oracle_d0": t1_oracle,
        "nemo_v2": load_asr_track("reazonspeech-nemo-v2"),
        "qwen3_1.7b": load_asr_track("qwen3-asr-1.7b-8bit"),
        "d1_srt": t4_d1_srt,
    }


def build_prompt(spec_name: str, tokenizer, text_ja: str, mode: str, history_ja: list[str]) -> str:
    if spec_name == "plamo3-8b":
        return tokenizer.apply_chat_template(
            [{"role": "user", "content": text_ja}],
            source_lang="ja",
            target_lang="zh",
            add_generation_prompt=True,
            tokenize=False,
        )
    if spec_name == "seed-x-7b":
        return f"Translate the following Japanese sentence into Chinese:\n{text_ja} <zh>"
    if spec_name == "translategemma-4b":
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "source_lang_code": "ja",
                        "target_lang_code": "zh",
                        "text": text_ja,
                    }
                ],
            }
        ]
        return tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)

    # Qwen3 instruct models
    if mode == "M0":
        messages = [
            {
                "role": "system",
                "content": "你是专业日译中视频字幕翻译。只输出自然流畅的简体中文译文，不要解释，不要输出思考过程，不要保留日文假名。",
            },
            {"role": "user", "content": f"请翻译以下日语台词为简体中文：\n{text_ja}"},
        ]
    else:
        ctx_block = "\n".join(f"- {h}" for h in history_ja[-2:]) if history_ja else "（无）"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是专业日译中视频字幕翻译与语音识别纠错专家。"
                    "你会收到前几句只读语境 <context> 和当前需要翻译的日语语音识别文本 <current>。"
                    "规则：1) 结合 <context> 自动修正 <current> 中明显的日语同音错字或漏字；"
                    "2) 只翻译 <current> 为自然简洁的简体中文字幕；"
                    "3) 严禁重复、翻译或输出 <context> 中的任何内容；4) 只输出最终中文译文，不要解释，不要保留日文假名。"
                ),
            },
            {
                "role": "user",
                "content": f"<context>\n{ctx_block}\n</context>\n\n<current>\n{text_ja}\n</current>",
            },
        ]
    kwargs = {"add_generation_prompt": True, "tokenize": False}
    if spec_name == "qwen3-8b":
        kwargs["enable_thinking"] = False
    return tokenizer.apply_chat_template(messages, **kwargs)


def run_model(spec_name: str, repo_id: str, modes: list[str], tracks: dict[str, list[dict]]) -> dict:
    mx.reset_peak_memory()
    t_load = time.perf_counter()
    if spec_name == "plamo3-8b":
        model, tokenizer = load(repo_id, tokenizer_config={"trust_remote_code": True})
    else:
        model, tokenizer = load(repo_id)
    load_s = round(time.perf_counter() - t_load, 2)
    sampler = make_sampler(temp=0.0)

    # Warmup
    warm_prompt = build_prompt(spec_name, tokenizer, "こんにちは", "M0", [])
    _ = generate(model, tokenizer, prompt=warm_prompt, max_tokens=32, sampler=sampler, verbose=False)

    model_summary = {"repo_id": repo_id, "load_s": load_s, "modes": {}}

    for mode in modes:
        latencies = []
        gateway_pass = 0
        total_non_empty = 0
        track_stats = {}

        target_tracks = ["oracle_d0", "nemo_v2", "qwen3_1.7b", "d1_srt"] if mode == "M0" else ["nemo_v2", "qwen3_1.7b"]
        for tname in target_tracks:
            rows = tracks[tname]
            out_rows = []
            history_by_clip: dict[str, list[str]] = {}
            t_pass = 0
            t_non_empty = 0
            t_lats = []

            for r in rows:
                ja = r["text_ja"].strip()
                clip = r["clip"]
                hist = history_by_clip.setdefault(clip, [])
                if not ja:
                    out_rows.append(r | {"text_zh": "", "passes_gateway": False, "mt_s": 0.0})
                    continue

                prompt = build_prompt(spec_name, tokenizer, ja, mode, hist)
                t0 = time.perf_counter()
                raw = generate(model, tokenizer, prompt=prompt, max_tokens=256, sampler=sampler, verbose=False)
                dt = time.perf_counter() - t0
                zh = clean_output(raw)
                ok = chinese(zh) is not None if zh else False

                latencies.append(dt)
                t_lats.append(dt)
                total_non_empty += 1
                t_non_empty += 1
                if ok:
                    gateway_pass += 1
                    t_pass += 1

                out_rows.append(r | {"text_zh": zh, "passes_gateway": ok, "mt_s": round(dt, 4)})
                hist.append(ja)

            out_path = OUT_DIR / f"{spec_name}_{mode}_{tname}.jsonl"
            out_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out_rows) + "\n", encoding="utf-8")
            track_stats[tname] = {
                "rows": len(out_rows),
                "non_empty": t_non_empty,
                "gateway_pass": t_pass,
                "p50_s": percentile(t_lats, 0.50),
                "p95_s": percentile(t_lats, 0.95),
            }

        model_summary["modes"][mode] = {
            "non_empty": total_non_empty,
            "gateway_pass": gateway_pass,
            "p50_s": percentile(latencies, 0.50),
            "p95_s": percentile(latencies, 0.95),
            "tracks": track_stats,
        }

    model_summary["gpu_peak_mib"] = round(mx.get_peak_memory() / (1024 * 1024), 2)
    model_summary["rss_mib"] = rss_mib()

    del model, tokenizer
    gc.collect()
    mx.clear_cache()
    return model_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        nargs="+",
        default=["translategemma-4b", "seed-x-7b", "plamo3-8b", "qwen3-4b", "qwen3-8b"],
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tracks = load_tracks()

    specs = {
        "translategemma-4b": ("mlx-community/translategemma-4b-it-4bit", ["M0"]),
        "seed-x-7b": ("dong-99/Seed-X-PPO-7B-mlx-4Bit", ["M0"]),
        "plamo3-8b": ("pfnet/plamo-3-translate-8b-MLX-4bit", ["M0"]),
        "qwen3-4b": ("mlx-community/Qwen3-4B-Instruct-2507-4bit", ["M0", "M_CTX"]),
        "qwen3-8b": ("mlx-community/Qwen3-8B-4bit", ["M0", "M_CTX"]),
    }

    summary_path = OUT_DIR / "mt_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}

    for name in args.models:
        repo_id, modes = specs[name]
        print(f"=== Running {name} ({repo_id}) modes={modes} ===", flush=True)
        try:
            res = run_model(name, repo_id, modes, tracks)
            summary[name] = res
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({name: {"gpu_peak_mib": res["gpu_peak_mib"], "modes": res["modes"]}}, ensure_ascii=False), flush=True)
        except Exception as e:
            summary[name] = {"repo_id": repo_id, "error": str(e)}
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"ERROR {name}: {e}", flush=True)


if __name__ == "__main__":
    main()
