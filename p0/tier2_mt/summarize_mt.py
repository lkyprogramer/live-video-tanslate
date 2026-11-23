"""Build side-by-side Tier 2B MT comparison tables across D0 34 units and D1 45s dialogue."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ASR_DIR = REPO_ROOT / "p0/evidence/tier2-asr-20261123"
MT_DIR = REPO_ROOT / "p0/evidence/tier2b-mt-20261123"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> None:
    units = load_jsonl(ASR_DIR / "d0_units.jsonl")
    summary = json.loads((MT_DIR / "mt_summary.json").read_text(encoding="utf-8"))
    active_models = [k for k, v in summary.items() if "error" not in v]

    # 1. Track 1: Clean-JA Oracle 34 units
    oracle_cmp = []
    oracle_by_model = {
        m: {r["id"]: r for r in load_jsonl(MT_DIR / f"{m}_M0_oracle_d0.jsonl")}
        for m in active_models
        if (MT_DIR / f"{m}_M0_oracle_d0.jsonl").exists()
    }
    for u in units:
        k = f"{u['clip']}/{u['group']}"
        row = {"unit": k, "source_ja": u["source_ja"], "reference_zh": u["reference_zh"], "models": {}}
        for m, m_rows in oracle_by_model.items():
            r = m_rows.get(u["group"]) or {}
            # match both clip and id
            for cand in load_jsonl(MT_DIR / f"{m}_M0_oracle_d0.jsonl"):
                if cand["clip"] == u["clip"] and cand["id"] == u["group"]:
                    r = cand
                    break
            row["models"][m] = {"zh": r.get("text_zh", ""), "gw": r.get("passes_gateway", False), "mt_s": r.get("mt_s", 0.0)}
        oracle_cmp.append(row)
    (MT_DIR / "d0_oracle_34unit.json").write_text(json.dumps(oracle_cmp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 2. Tracks 2 & 3: NeMo-v2 & Qwen3-1.7B 34-unit alignment
    for track_key, base_mt_file in [("nemo_v2", "mt_reazonspeech-nemo-v2_d0.jsonl"), ("qwen3_1.7b", "mt_qwen3-asr-1.7b-8bit_d0.jsonl")]:
        variants: dict[str, list[dict]] = {"hy-mt2-1.8b": load_jsonl(ASR_DIR / base_mt_file)}
        for m in active_models:
            for mode in summary[m].get("modes", {}):
                p = MT_DIR / f"{m}_{mode}_{track_key}.jsonl"
                if p.exists():
                    variants[f"{m}:{mode}"] = [r for r in load_jsonl(p) if r["clip"] != "d1-ep01-45s"]

        comp = []
        for u in units:
            clip = u["clip"]
            key = f"{clip}/{u['group']}"
            end = round(126.04 * 16000) if key == "wmf-04/153-155" else u["end_sample"]
            entry = {"unit": key, "source_ja": u["source_ja"], "reference_zh": u["reference_zh"], "models": {}}
            for vname, rows in variants.items():
                rel = [r for r in rows if r["clip"] == clip and r["start_sample"] < end and r["end_sample"] > u["start_sample"]]
                entry["models"][vname] = {
                    "ja": " / ".join(r["text_ja"] for r in rel if r["text_ja"]),
                    "zh": " / ".join(r["text_zh"] for r in rel if r["text_zh"]),
                    "gw": all(r.get("passes_gateway", True) for r in rel if r.get("text_zh", "").strip()),
                }
            comp.append(entry)
        (MT_DIR / f"d0_{track_key}_34unit.json").write_text(json.dumps(comp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 3. D1 (*Brush Up Life* 45s) 3-segment comparison on NeMo-v2 & Qwen3-1.7B
    d1_out = {}
    for track_key, base_d1_file in [("nemo_v2", "mt_reazonspeech-nemo-v2_d1.jsonl"), ("qwen3_1.7b", "mt_qwen3-asr-1.7b-8bit_d1.jsonl")]:
        d1_out[track_key] = {"hy-mt2-1.8b": load_jsonl(ASR_DIR / base_d1_file)}
        for m in active_models:
            for mode in summary[m].get("modes", {}):
                p = MT_DIR / f"{m}_{mode}_{track_key}.jsonl"
                if p.exists():
                    d1_out[track_key][f"{m}:{mode}"] = [r for r in load_jsonl(p) if r["clip"] == "d1-ep01-45s"]
    (MT_DIR / "d1_drama_comparison.json").write_text(json.dumps(d1_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built comparison JSONs for {len(active_models)} active Tier 2B MT models.")


if __name__ == "__main__":
    main()
