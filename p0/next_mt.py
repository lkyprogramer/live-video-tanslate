"""Compare Hy-MT2 prompts and source text on the frozen Japanese development set."""

import argparse
from collections import defaultdict, deque
from dataclasses import replace
import json
from pathlib import Path
import random
import time

import mlx.core as mx

from monitor import mark
from next_asr import read_rows, write_rows
from review import P0
from subtitle_service.local_pipeline import HyTranslator


GLOSSARY = {
    "パラ言語情報": "副语言信息",
    "非言語情報": "非语言信息",
    "運動憲章": "运动宪章",
    "片仮名": "片假名",
    "カタカナ": "片假名",
    "オフサイド": "越位",
}
VARIANTS = ("m0", "m0-greedy", "m1-empty", "m1", "m2-empty", "m2", "m2-hit")


def translate(run, cache, sources, variants, model_snapshot=None):
    manifest = json.loads((run / "frozen.json").read_text())
    snapshot = (model_snapshot if model_snapshot is not None else
                cache / "hub/models--mlx-community--Hy-MT2-1.8B-4bit/snapshots" / manifest["hy_revision"])
    hy = HyTranslator(snapshot)
    session = hy.new_session()
    base_prompt = session._prompt
    base_config = session._config
    assert base_prompt["kind"] == "text"
    jobs = [(source, variant, seed) for source in sources for variant in variants
            for seed in manifest["offline_mt_seeds"]]
    random.Random(11).shuffle(jobs)
    scored = set(manifest["scored_clips"])
    for source, variant, seed in jobs:
        filename = {"oracle": "units", "slice-ref": "slices"}.get(source, source)
        rows = read_rows(run / f"{filename}.jsonl")
        history = defaultdict(lambda: deque(maxlen=2))
        output = run / f"mt-{source}-{variant}-{seed}.jsonl"
        if output.exists():
            existing = read_rows(output)
            expected = [row for row in rows if row["clip"] in scored]
            if [r.get("segment_id", r.get("group")) for r in existing] != [
                    r.get("segment_id", r.get("group")) for r in expected]:
                raise ValueError(f"Incomplete run: {output}")
            continue

        session._config = replace(base_config, temp=0.0) if variant == "m0-greedy" else base_config

        def translations():
            for index, row in enumerate(rows):
                clip = row["clip"]
                if clip not in scored:
                    continue
                ja = row.get("text_ja", row.get("source_ja", ""))
                prefix = ""
                if variant.startswith("m1"):
                    background = "。".join(history[clip])[-120:] if variant == "m1" else ""
                    prefix = f"以下是仅供理解的前文。只翻译当前文本。\n前文：{background}\n当前文本：\n"
                elif variant.startswith("m2"):
                    terms = "、".join(f"{ja_term}＝{zh_term}" for ja_term, zh_term in GLOSSARY.items()
                                     if ja_term in ja) if variant != "m2-empty" else ""
                    if terms or variant != "m2-hit":
                        prefix = f"以下词表仅用于当前文本中对应词语。只翻译当前文本。\n词表：{terms}\n当前文本：\n"
                session._prompt = base_prompt | {
                    "template": prefix.replace("{", "{{").replace("}", "}}") + base_prompt["template"],
                }
                mx.random.seed(seed + row.get("mt_seed_index", index))
                start = time.perf_counter()
                zh = hy.translate(session, ja) if ja else ""
                elapsed = time.perf_counter() - start
                if ja:
                    history[clip].append(ja)
                yield row | {"input_ja": ja, "text_zh": zh, "mt_s": elapsed}

        job = output.stem
        mark(job, "start")
        try:
            write_rows(output, translations())
        finally:
            mark(job, "end")
        print(output.name, flush=True)


def align(run, files):
    streams = {file.stem.removeprefix("mt-"): read_rows(file) for file in files}
    for unit in read_rows(run / "units.jsonl"):
        candidates = {}
        for name, rows in streams.items():
            candidates[name] = [{k: r[k] for k in ("start_sample", "end_sample", "input_ja", "text_zh")}
                                for r in rows if r["clip"] == unit["clip"] and (
                                    r.get("group") == unit["group"] if "group" in r else
                                    r["start_sample"] < unit["end_sample"] and r["end_sample"] > unit["start_sample"])]
        print(json.dumps(unit | {"candidates": candidates}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cache", type=Path, default=P0.parent / ".model-cache")
    commands = parser.add_subparsers(dest="command", required=True)
    mt = commands.add_parser("translate")
    mt.add_argument("--sources", nargs="+", required=True,
                    choices=("reazon", "reazon-beam", "reazon-fp32", "haas", "parakeet",
                             "qwen", "kotoba", "oracle", "slice-ref"))
    mt.add_argument("--variants", nargs="+", choices=VARIANTS, default=["m0"])
    mt.add_argument("--model-snapshot", type=Path, help="local MLX checkpoint for an isolated comparison")
    review = commands.add_parser("align")
    review.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    if args.command == "translate":
        translate(args.run, args.cache, args.sources, args.variants, args.model_snapshot)
    else:
        align(args.run, args.files)


if __name__ == "__main__":
    main()
