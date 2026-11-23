"""Translate Tier 2 ASR outputs with Hy-MT2 1.8B 4-bit (seed=11) and emit side-by-side 34-unit comparison."""

import argparse
import json
import sys
import time
from pathlib import Path

from subtitle_service.app import chinese
from subtitle_service.local_pipeline import HyTranslator

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "p0/evidence/tier2-asr-20261123"
D0_DIR = REPO_ROOT / "p0/evidence/n0-next"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="Model prefixes whose <model>_d0.jsonl and <model>_d1.jsonl exist in OUT_DIR",
    )
    args = parser.parse_args()

    locks_path = REPO_ROOT / "p0/evidence/d1-dialogue/models.json"
    locks = json.loads(locks_path.read_text())
    hy_path = Path({row["id"]: row["local_path"] for row in locks["models"]}["hymt-b0"])

    import mlx.core as mx
    mx.random.seed(11)
    hy = HyTranslator(hy_path)
    session = hy.new_session()

    units = load_jsonl(OUT_DIR / "d0_units.jsonl")
    base_d0 = load_jsonl(D0_DIR / "mt-reazon-m0-11.jsonl")
    base_d1 = load_jsonl(OUT_DIR / "reazon_d1.jsonl")

    # Translate baseline D1 as well if not already translated
    for row in base_d1:
        t0 = time.perf_counter()
        zh = hy.translate(session, row["text_ja"]) if row["text_ja"].strip() else ""
        row["text_zh"] = zh
        row["passes_gateway"] = chinese(zh) is not None if zh else False
        row["mt_s"] = round(time.perf_counter() - t0, 4)
    (OUT_DIR / "mt_reazon_d1.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in base_d1) + "\n", encoding="utf-8"
    )

    mt_by_model_d0: dict[str, list[dict]] = {"reazon-k2-v2": base_d0}
    mt_by_model_d1: dict[str, list[dict]] = {"reazon-k2-v2": base_d1}

    for name in args.models:
        d0_path = OUT_DIR / f"{name}_d0.jsonl"
        d1_path = OUT_DIR / f"{name}_d1.jsonl"
        d0_rows = load_jsonl(d0_path)
        for r in d0_rows:
            t0 = time.perf_counter()
            zh = hy.translate(session, r["text_ja"]) if r["text_ja"].strip() else ""
            r["text_zh"] = zh
            r["passes_gateway"] = chinese(zh) is not None if zh else False
            r["mt_s"] = round(time.perf_counter() - t0, 4)
        (OUT_DIR / f"mt_{name}_d0.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in d0_rows) + "\n", encoding="utf-8"
        )
        mt_by_model_d0[name] = d0_rows

        if d1_path.exists():
            d1_rows = load_jsonl(d1_path)
            for r in d1_rows:
                t0 = time.perf_counter()
                zh = hy.translate(session, r["text_ja"]) if r["text_ja"].strip() else ""
                r["text_zh"] = zh
                r["passes_gateway"] = chinese(zh) is not None if zh else False
                r["mt_s"] = round(time.perf_counter() - t0, 4)
            (OUT_DIR / f"mt_{name}_d1.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in d1_rows) + "\n", encoding="utf-8"
            )
            mt_by_model_d1[name] = d1_rows

    # Build 34-unit side-by-side comparison
    comparison = []
    for u in units:
        clip = u["clip"]
        key = f"{clip}/{u['group']}"
        end = round(126.04 * 16000) if key == "wmf-04/153-155" else u["end_sample"]
        entry = {
            "unit": key,
            "source_ja": u["source_ja"],
            "reference_zh": u["reference_zh"],
            "models": {},
        }
        for mname, rows in mt_by_model_d0.items():
            related = [r for r in rows if r["clip"] == clip and r["start_sample"] < end and r["end_sample"] > u["start_sample"]]
            entry["models"][mname] = {
                "ja": " / ".join(r["text_ja"] for r in related if r["text_ja"]),
                "zh": " / ".join(r["text_zh"] for r in related if r["text_zh"]),
                "passed_gateway": all(chinese(r["text_zh"]) is not None for r in related if r["text_zh"].strip()),
            }
        comparison.append(entry)

    (OUT_DIR / "d0_34unit_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Translated and compared {len(mt_by_model_d0)} models across {len(comparison)} D0 units.")


if __name__ == "__main__":
    main()
